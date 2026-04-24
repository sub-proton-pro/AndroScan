"""Per-app SQLite RAG index over decompiled sources.

Layout (co-located with the decompile cache so cache lifetime matches):

    apps/<app_id>/.decompiled/<sha>/
        sources/      (jadx output)
        rag.sqlite    (this index)

Schema (versioned via ``meta.schema_version``):

    meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)
        keys: schema_version, sha, provider_name, provider_model, dim,
              built_at, finished_at, status, error,
              file_count, chunk_count, sources_root

    chunks(id TEXT PRIMARY KEY, file, package, class_name, method_name,
           kind, start_line, end_line, content, vector BLOB)

``vector`` is a packed ``float32`` numpy array (little-endian) so search can
``np.frombuffer`` it without any decoding shenanigans. The schema is
intentionally provider-agnostic — swapping providers requires a rebuild
(enforced via ``provider_name``/``provider_model`` mismatch detection in
:func:`get_status`), but does not require schema changes.

The brute-force cosine search lives in ``search.py``. When we eventually
swap to ``sqlite-vec``, only ``index.py``/``search.py`` change; callers
(endpoints, skills, chat) remain untouched.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from androscan.rag.chunking import Chunk, chunk_sources
from androscan.rag.embed import EmbedProvider, EmbedProviderError

logger = logging.getLogger(__name__)

INDEX_FILENAME = "rag.sqlite"
SCHEMA_VERSION = "1"

# Embedding batch size: small enough to keep memory bounded on big apps,
# large enough to amortize provider overhead. Tuned for fastembed.
DEFAULT_BATCH_SIZE = 32

# In-process registry of running build jobs, keyed by (app_dir, sha).
# Mirrors the pattern in decompile_cache so multiple HTTP callers share work.
_RUNNING: dict[tuple[str, str], "_BuildJob"] = {}
_RUNNING_LOCK = threading.Lock()

# How long (seconds) a ``status=pending`` row is allowed to exist without
# a live worker thread before :func:`get_status` reclassifies it as
# ``failed`` (orphaned by a server restart or worker crash). The grace
# period covers the small window between :func:`start_build_async`
# returning and :func:`build_index` registering itself in ``_RUNNING``.
PENDING_GRACE_SEC = 30.0


@dataclass
class _BuildJob:
    sha: str
    cache_dir: Path
    thread: threading.Thread


@dataclass
class IndexStatus:
    status: str  # missing | pending | ready | failed
    sha: Optional[str] = None
    provider_name: Optional[str] = None
    provider_model: Optional[str] = None
    dim: Optional[int] = None
    built_at: Optional[float] = None
    finished_at: Optional[float] = None
    file_count: Optional[int] = None
    chunk_count: Optional[int] = None
    error: Optional[str] = None
    db_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sha": self.sha,
            "provider_name": self.provider_name,
            "provider_model": self.provider_model,
            "dim": self.dim,
            "built_at": self.built_at,
            "finished_at": self.finished_at,
            "file_count": self.file_count,
            "chunk_count": self.chunk_count,
            "error": self.error,
            "db_path": self.db_path,
        }


# ---------------------------------------------------------------------------
# Path helpers


def rag_db_path(decompile_cache_dir: Path) -> Path:
    """Return the SQLite path inside an existing decompile cache directory.

    ``decompile_cache_dir`` is the ``apps/<app>/.decompiled/<sha>/`` folder
    produced by :mod:`androscan.web.decompile_cache`.
    """
    return Path(decompile_cache_dir) / INDEX_FILENAME


# ---------------------------------------------------------------------------
# Vector codec (numpy is required by ``search`` but not by index writes)


def _encode_vector(v: list[float]) -> bytes:
    """Pack a python list of floats as little-endian float32 bytes."""
    return struct.pack(f"<{len(v)}f", *v)


# ---------------------------------------------------------------------------
# Connection / schema


@contextmanager
def _connect(db_path: Path, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        timeout=10.0,
        isolation_level=None,  # autocommit; we manage txns explicitly when writing
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        if write:
            conn.execute("BEGIN")
        yield conn
        if write:
            conn.execute("COMMIT")
    except Exception:
        if write:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise
    finally:
        conn.close()


_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS meta ("
    " key   TEXT PRIMARY KEY,"
    " value TEXT NOT NULL"
    ")",
    "CREATE TABLE IF NOT EXISTS chunks ("
    " id           TEXT PRIMARY KEY,"
    " file         TEXT NOT NULL,"
    " package      TEXT NOT NULL,"
    " class_name   TEXT NOT NULL,"
    " method_name  TEXT,"
    " kind         TEXT NOT NULL,"
    " start_line   INTEGER NOT NULL,"
    " end_line     INTEGER NOT NULL,"
    " content      TEXT NOT NULL,"
    " vector       BLOB NOT NULL"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_chunks_file    ON chunks(file)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_class   ON chunks(class_name)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_package ON chunks(package)",
)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the schema. Safe inside a transaction.

    We avoid ``executescript`` here because it implicitly issues ``COMMIT``
    before running the script, which breaks the explicit transaction we
    open in :func:`_connect` when ``write=True``.
    """
    for stmt in _SCHEMA_STATEMENTS:
        conn.execute(stmt)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (SCHEMA_VERSION,),
    )


def _meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------------------
# Public read API


def is_build_running(decompile_cache_dir: Path, sha: str) -> bool:
    """True if a live worker for ``(decompile_cache_dir, sha)`` is registered.

    Process-local check: a ``True`` answer means *this* uvicorn process has
    a thread actively building this index. After a server restart the
    in-memory registry is empty, so any ``status=pending`` row in the DB is
    by definition orphaned (caught by :func:`get_status`).
    """
    key = (str(decompile_cache_dir), sha)
    with _RUNNING_LOCK:
        job = _RUNNING.get(key)
    return bool(job and job.thread.is_alive())


def get_status(decompile_cache_dir: Path) -> IndexStatus:
    """Return the current build status for the index in ``decompile_cache_dir``.

    A ``status=pending`` row is reclassified as ``failed`` when no live worker
    is registered for ``(cache_dir, sha)`` and the row is older than
    :data:`PENDING_GRACE_SEC` — this catches the common case where the uvicorn
    server (or the build worker) was killed mid-build, leaving the DB stuck in
    ``pending`` forever.
    """
    db = rag_db_path(decompile_cache_dir)
    if not db.is_file():
        return IndexStatus(status="missing")
    try:
        with _connect(db) as conn:
            sv = _meta_get(conn, "schema_version")
            if sv != SCHEMA_VERSION:
                return IndexStatus(
                    status="failed",
                    db_path=str(db),
                    error=f"schema_version mismatch (have {sv!r}, want {SCHEMA_VERSION!r})",
                )
            raw_status = _meta_get(conn, "status") or "missing"
            sha = _meta_get(conn, "sha")
            built_at = _safe_float(_meta_get(conn, "built_at"))
            error = _meta_get(conn, "error") or None

            if (
                raw_status == "pending"
                and sha
                and built_at is not None
                and (time.time() - built_at) > PENDING_GRACE_SEC
                and not is_build_running(decompile_cache_dir, sha)
            ):
                raw_status = "failed"
                error = (
                    error
                    or "build was interrupted (server restart or worker crash); "
                    "no live worker for this index. Click Rebuild to retry."
                )

            return IndexStatus(
                status=raw_status,
                sha=sha,
                provider_name=_meta_get(conn, "provider_name"),
                provider_model=_meta_get(conn, "provider_model"),
                dim=_safe_int(_meta_get(conn, "dim")),
                built_at=built_at,
                finished_at=_safe_float(_meta_get(conn, "finished_at")),
                file_count=_safe_int(_meta_get(conn, "file_count")),
                chunk_count=_safe_int(_meta_get(conn, "chunk_count")),
                error=error,
                db_path=str(db),
            )
    except sqlite3.Error as e:
        return IndexStatus(status="failed", db_path=str(db), error=f"sqlite: {e}")


def _safe_int(s: Optional[str]) -> Optional[int]:
    if s is None or s == "":
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _safe_float(s: Optional[str]) -> Optional[float]:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Build


def invalidate(decompile_cache_dir: Path) -> bool:
    """Delete the SQLite index. Returns True if a file was removed."""
    db = rag_db_path(decompile_cache_dir)
    if not db.is_file():
        return False
    try:
        db.unlink()
    except OSError as e:
        logger.warning("rag.invalidate: failed to remove %s: %s", db, e)
        return False
    return True


def _is_provider_compatible(
    conn: sqlite3.Connection, sha: str, provider: EmbedProvider
) -> bool:
    """True if existing rows match the provider+sha+dim."""
    if _meta_get(conn, "sha") != sha:
        return False
    if _meta_get(conn, "provider_name") != provider.name:
        return False
    if _meta_get(conn, "provider_model") != provider.model:
        return False
    if _safe_int(_meta_get(conn, "dim")) != provider.dim:
        return False
    return True


def _embed_in_batches(
    provider: EmbedProvider,
    chunks: list[Chunk],
    batch_size: int,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Iterator[tuple[Chunk, list[float]]]:
    total = len(chunks)
    done = 0
    for i in range(0, total, batch_size):
        batch = chunks[i: i + batch_size]
        vectors = provider.embed([c.content for c in batch])
        if len(vectors) != len(batch):
            raise EmbedProviderError(
                f"provider returned {len(vectors)} vectors for {len(batch)} inputs"
            )
        for ch, v in zip(batch, vectors):
            yield ch, v
        done += len(batch)
        if on_progress:
            on_progress(done, total)


def build_index(
    decompile_cache_dir: Path,
    sources_root: Path,
    sha: str,
    provider: EmbedProvider,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> IndexStatus:
    """Chunk ``sources_root``, embed every chunk, and persist into SQLite.

    Always writes a fresh, transactional snapshot:
      * If the existing index already matches ``(sha, provider, model, dim)``
        and reports ``status=ready``, this is a no-op (returns the status).
      * Otherwise the previous rows are dropped and re-inserted under one
        transaction so callers never observe a half-built index.

    Concurrency: callers should hold no locks; the function uses an in-process
    registry to serialize concurrent builds for the same ``(cache_dir, sha)``
    so two HTTP requests don't fight over one SQLite file.
    """
    db = rag_db_path(decompile_cache_dir)
    key = (str(decompile_cache_dir), sha)

    with _RUNNING_LOCK:
        if key in _RUNNING:
            # Another thread is already building; report pending.
            return IndexStatus(status="pending", sha=sha, db_path=str(db))
        _RUNNING[key] = _BuildJob(
            sha=sha, cache_dir=decompile_cache_dir, thread=threading.current_thread()
        )

    started = time.time()
    try:
        # Fast path: already-good index.
        if db.is_file():
            with _connect(db) as conn:
                _ensure_schema(conn)
                if (
                    _is_provider_compatible(conn, sha, provider)
                    and _meta_get(conn, "status") == "ready"
                ):
                    return get_status(decompile_cache_dir)

        # Mark pending in a separate write transaction so other readers can
        # observe the in-progress state.
        with _connect(db, write=True) as conn:
            _ensure_schema(conn)
            _meta_set(conn, "status", "pending")
            _meta_set(conn, "sha", sha)
            _meta_set(conn, "provider_name", provider.name)
            _meta_set(conn, "provider_model", provider.model)
            _meta_set(conn, "dim", str(provider.dim))
            _meta_set(conn, "built_at", str(started))
            _meta_set(conn, "sources_root", str(sources_root))
            _meta_set(conn, "error", "")
            # Wipe any stale rows from a previous (incompatible) build.
            conn.execute("DELETE FROM chunks")

        chunks, stats = chunk_sources(sources_root)
        if not chunks:
            with _connect(db, write=True) as conn:
                _meta_set(conn, "status", "ready")
                _meta_set(conn, "finished_at", str(time.time()))
                _meta_set(conn, "file_count", str(stats.files_scanned))
                _meta_set(conn, "chunk_count", "0")
            return get_status(decompile_cache_dir)

        # Embed + insert in one transaction. Embedding may take seconds-to-minutes;
        # SQLite holds no locks during that time because we open the write txn
        # only when we have a batch to flush.
        rows: list[tuple[Any, ...]] = []
        try:
            for ch, vec in _embed_in_batches(provider, chunks, batch_size, on_progress):
                rows.append((
                    ch.chunk_id(),
                    ch.file,
                    ch.package,
                    ch.class_name,
                    ch.method_name,
                    ch.kind,
                    ch.start_line,
                    ch.end_line,
                    ch.content,
                    _encode_vector(vec),
                ))
        except EmbedProviderError as e:
            with _connect(db, write=True) as conn:
                _meta_set(conn, "status", "failed")
                _meta_set(conn, "finished_at", str(time.time()))
                _meta_set(conn, "error", str(e)[:2000])
            return get_status(decompile_cache_dir)

        with _connect(db, write=True) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO chunks "
                "(id, file, package, class_name, method_name, kind, "
                " start_line, end_line, content, vector) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            _meta_set(conn, "status", "ready")
            _meta_set(conn, "finished_at", str(time.time()))
            _meta_set(conn, "file_count", str(stats.files_scanned))
            _meta_set(conn, "chunk_count", str(len(rows)))

        return get_status(decompile_cache_dir)
    except Exception as e:
        # Best-effort: record the error in the index so the UI can surface it.
        try:
            with _connect(db, write=True) as conn:
                _ensure_schema(conn)
                _meta_set(conn, "status", "failed")
                _meta_set(conn, "finished_at", str(time.time()))
                _meta_set(conn, "error", f"{type(e).__name__}: {e}"[:2000])
        except sqlite3.Error:
            pass
        raise
    finally:
        with _RUNNING_LOCK:
            _RUNNING.pop(key, None)


# ---------------------------------------------------------------------------
# Async wrapper used by the web layer / decompile callback


def start_build_async(
    decompile_cache_dir: Path,
    sources_root: Path,
    sha: str,
    provider_factory: Callable[[], EmbedProvider],
    *,
    on_done: Optional[Callable[[IndexStatus], None]] = None,
) -> dict[str, Any]:
    """Kick off ``build_index`` in a daemon thread. Returns immediate status.

    ``provider_factory`` is a 0-arg callable so we can defer heavy provider
    construction (model load, network probe) into the worker thread instead
    of blocking the calling HTTP handler.
    """
    db = rag_db_path(decompile_cache_dir)
    key = (str(decompile_cache_dir), sha)
    with _RUNNING_LOCK:
        if key in _RUNNING:
            return get_status(decompile_cache_dir).to_dict()

    def _worker() -> None:
        try:
            try:
                provider = provider_factory()
            except EmbedProviderError as e:
                logger.warning("rag.build: provider unavailable: %s", e)
                with _connect(db, write=True) as conn:
                    _ensure_schema(conn)
                    _meta_set(conn, "status", "failed")
                    _meta_set(conn, "error", str(e)[:2000])
                    _meta_set(conn, "finished_at", str(time.time()))
                if on_done:
                    on_done(get_status(decompile_cache_dir))
                return
            status = build_index(decompile_cache_dir, sources_root, sha, provider)
            if on_done:
                on_done(status)
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("rag.build worker crashed: %s", e)
            try:
                with _connect(db, write=True) as conn:
                    _ensure_schema(conn)
                    _meta_set(conn, "status", "failed")
                    _meta_set(conn, "error", f"{type(e).__name__}: {e}"[:2000])
                    _meta_set(conn, "finished_at", str(time.time()))
            except sqlite3.Error:
                pass

    t = threading.Thread(target=_worker, daemon=True, name=f"rag-build-{sha[:10]}")
    t.start()
    return {"status": "pending", "sha": sha, "db_path": str(db)}


# ---------------------------------------------------------------------------
# Diagnostic dump (used by ``/api/rag/{app_id}/status?verbose=1``)


def dump_meta(decompile_cache_dir: Path) -> dict[str, str]:
    db = rag_db_path(decompile_cache_dir)
    if not db.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        with _connect(db) as conn:
            for row in conn.execute("SELECT key, value FROM meta ORDER BY key"):
                out[row["key"]] = row["value"]
    except sqlite3.Error as e:
        out["__error__"] = json.dumps({"sqlite": str(e)})
    return out
