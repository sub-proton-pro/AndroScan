"""Per-app SQLite static call graph over apktool's Smali output.

Layout (co-located with the decompile cache so cache lifetime matches)::

    apps/<app_id>/.decompiled/<sha>/
        sources/                 (jadx output, owned by decompile_cache)
        rag.sqlite               (RAG index, owned by androscan.rag.index)
        smali_out/               (apktool d output, owned by THIS module)
            smali/, smali_classes2/, ..., AndroidManifest.xml
        call_graph.sqlite        (THIS module)

Schema (``schema_version = "1"``) — see DEC-023 and the 4.1 plan. In
short: INTEGER-id ``classes`` and ``nodes``, a ``class_interfaces``
join table, ``edges(src_id, dst_id, kind, invoke_op, src_line)``, a
``meta`` table mirroring ``androscan.rag.index`` for build/lifecycle
state.

Edge kinds (locked): ``direct | static | super | virtual_dispatch |
interface_dispatch | external``. Externality of the *destination* wins
over the bytecode opcode: a call to ``Landroid/util/Log;->d`` is always
``external`` regardless of whether the opcode was ``invoke-virtual`` or
``invoke-static``.

External method nodes are materialised as ``nodes.is_external=1`` rows
(design choice #1 in the plan) so neighbour queries are uniform.

Per-call-site edge rows (design choice #2) preserve the exact line where
each invoke appears; the PK ``(src_id, dst_id, kind, src_line)`` allows
an in-app method that calls ``Log.d`` 20 times to produce 20 edge rows.

Class hierarchy is persisted (design choice #3) even though only the
build pass strictly needs it; cheap to store and unlocks future skills
(e.g. "all subclasses of X") without re-running the parser.

Mirrors the conventions from :mod:`androscan.rag.index` so callers and
reviewers see the same idioms (WAL + autocommit, ``meta`` table,
in-process ``_RUNNING`` registry with orphan-pending recovery, async
wrapper).
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from androscan.analysis import apktool_runner, dispatch, smali_parser
from androscan.analysis.smali_types import (
    class_desc_to_java,
    compute_access_flags,
    descriptor_to_java,
    method_descriptor,
    params_to_java,
    split_class_name,
)

logger = logging.getLogger(__name__)

INDEX_FILENAME = "call_graph.sqlite"
APKTOOL_OUT_SUBDIR = "smali_out"
SCHEMA_VERSION = "1"
FIDELITY = "v2"
PARSER_VERSION = "1"

# In-process registry of running build jobs, keyed by (cache_dir, sha).
_RUNNING: dict[tuple[str, str], "_BuildJob"] = {}
_RUNNING_LOCK = threading.Lock()

# Same grace logic as androscan.rag.index — see PENDING_GRACE_SEC there.
PENDING_GRACE_SEC = 30.0


@dataclass
class _BuildJob:
    sha: str
    cache_dir: Path
    thread: threading.Thread


@dataclass
class IndexStatus:
    """Status envelope parallel to :class:`androscan.rag.index.IndexStatus`
    so the Settings tab's status fan-out can render both with the same
    React component."""
    status: str  # missing | pending | ready | failed
    sha: Optional[str] = None
    fidelity_level: Optional[str] = None
    parser_version: Optional[str] = None
    built_at: Optional[float] = None
    finished_at: Optional[float] = None
    class_count: Optional[int] = None
    external_class_count: Optional[int] = None
    node_count: Optional[int] = None
    edge_count: Optional[int] = None
    error: Optional[str] = None
    db_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sha": self.sha,
            "fidelity_level": self.fidelity_level,
            "parser_version": self.parser_version,
            "built_at": self.built_at,
            "finished_at": self.finished_at,
            "class_count": self.class_count,
            "external_class_count": self.external_class_count,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "error": self.error,
            "db_path": self.db_path,
        }


# ---------------------------------------------------------------------------
# Path helpers


def call_graph_db_path(decompile_cache_dir: Path) -> Path:
    """SQLite path inside an existing decompile cache directory."""
    return Path(decompile_cache_dir) / INDEX_FILENAME


def apktool_out_dir(decompile_cache_dir: Path) -> Path:
    """Apktool output root inside the cache (``smali_out/``)."""
    return Path(decompile_cache_dir) / APKTOOL_OUT_SUBDIR


# ---------------------------------------------------------------------------
# Connection / schema


@contextmanager
def _connect(db_path: Path, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        timeout=10.0,
        isolation_level=None,  # autocommit; we wrap writes explicitly
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        # Logical FKs only — skipping enforcement keeps batch inserts cheap
        # and mirrors the RAG index's choice.
        conn.execute("PRAGMA foreign_keys=OFF")
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


_SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS meta ("
    " key   TEXT PRIMARY KEY,"
    " value TEXT NOT NULL"
    ")",
    "CREATE TABLE IF NOT EXISTS classes ("
    " id           INTEGER PRIMARY KEY,"
    " smali_class  TEXT UNIQUE NOT NULL,"
    " class_name   TEXT NOT NULL,"
    " package      TEXT NOT NULL,"
    " simple_name  TEXT NOT NULL,"
    " super_class  TEXT,"
    " is_external  INTEGER NOT NULL DEFAULT 0,"
    " is_abstract  INTEGER NOT NULL DEFAULT 0,"
    " is_interface INTEGER NOT NULL DEFAULT 0,"
    " smali_file   TEXT,"
    " jadx_file    TEXT"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_classes_package ON classes(package)",
    "CREATE INDEX IF NOT EXISTS idx_classes_super   ON classes(super_class)",
    "CREATE TABLE IF NOT EXISTS class_interfaces ("
    " class_id       INTEGER NOT NULL,"
    " interface_name TEXT NOT NULL,"
    " PRIMARY KEY (class_id, interface_name)"
    ")",
    "CREATE TABLE IF NOT EXISTS nodes ("
    " id                              INTEGER PRIMARY KEY,"
    " smali_id                        TEXT UNIQUE NOT NULL,"
    " class_id                        INTEGER NOT NULL,"
    " method_name                     TEXT NOT NULL,"
    " descriptor                      TEXT NOT NULL,"
    " return_type                     TEXT NOT NULL,"
    " param_types_json                TEXT NOT NULL,"
    " access_flags                    INTEGER NOT NULL DEFAULT 0,"
    " is_static                       INTEGER NOT NULL DEFAULT 0,"
    " is_abstract                     INTEGER NOT NULL DEFAULT 0,"
    " is_native                       INTEGER NOT NULL DEFAULT 0,"
    " is_synthetic                    INTEGER NOT NULL DEFAULT 0,"
    " is_constructor                  INTEGER NOT NULL DEFAULT 0,"
    " is_external                     INTEGER NOT NULL DEFAULT 0,"
    " smali_start_line                INTEGER,"
    " smali_end_line                  INTEGER,"
    " may_have_unresolved_reflection  INTEGER NOT NULL DEFAULT 0"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_nodes_class    ON nodes(class_id)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_method   ON nodes(method_name)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_external ON nodes(is_external)",
    "CREATE TABLE IF NOT EXISTS edges ("
    " src_id    INTEGER NOT NULL,"
    " dst_id    INTEGER NOT NULL,"
    " kind      TEXT NOT NULL,"
    " invoke_op TEXT NOT NULL,"
    " src_line  INTEGER NOT NULL,"
    " PRIMARY KEY (src_id, dst_id, kind, src_line)"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_edges_src  ON edges(src_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_dst  ON edges(dst_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind)",
)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the schema. Safe inside a transaction."""
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


def _format_build_error(e: BaseException) -> str:
    """Render an exception into the ``meta.error`` string the Settings →
    Status card displays.

    On Python 3.11+ ``sqlite3.Error`` instances expose ``sqlite_errorname``
    / ``sqlite_errorcode`` carrying SQLite's extended result code (e.g.
    ``SQLITE_IOERR_FSYNC`` vs the plain ``disk I/O error`` text). Surfacing
    that suffix turns an opaque generic IO failure into an actionable
    diagnosis (fsync vs lock vs shm-open vs cantopen).
    Falls back gracefully on older Pythons and on non-SQLite exceptions
    where these attributes are absent.
    """
    code = getattr(e, "sqlite_errorname", None) or getattr(e, "sqlite_errorcode", None)
    suffix = f" [{code}]" if code is not None else ""
    return f"{type(e).__name__}: {e}{suffix}"[:2000]


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
# Public read API


def is_build_running(decompile_cache_dir: Path, sha: str) -> bool:
    """True if a live worker for ``(decompile_cache_dir, sha)`` is registered."""
    key = (str(decompile_cache_dir), sha)
    with _RUNNING_LOCK:
        job = _RUNNING.get(key)
    return bool(job and job.thread.is_alive())


def get_status(decompile_cache_dir: Path) -> IndexStatus:
    """Current build status. ``pending`` rows older than
    :data:`PENDING_GRACE_SEC` with no live worker are reclassified as
    ``failed`` (same orphan-recovery logic as the RAG index)."""
    db = call_graph_db_path(decompile_cache_dir)
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
                fidelity_level=_meta_get(conn, "fidelity_level"),
                parser_version=_meta_get(conn, "parser_version"),
                built_at=built_at,
                finished_at=_safe_float(_meta_get(conn, "finished_at")),
                class_count=_safe_int(_meta_get(conn, "class_count")),
                external_class_count=_safe_int(_meta_get(conn, "external_class_count")),
                node_count=_safe_int(_meta_get(conn, "node_count")),
                edge_count=_safe_int(_meta_get(conn, "edge_count")),
                error=error,
                db_path=str(db),
            )
    except sqlite3.Error as e:
        return IndexStatus(status="failed", db_path=str(db), error=f"sqlite: {e}")


# ---------------------------------------------------------------------------
# Invalidation


def invalidate(decompile_cache_dir: Path, *, drop_apktool: bool = False) -> bool:
    """Delete the SQLite index. ``drop_apktool=True`` also removes the
    apktool output so the next build re-decodes from scratch (useful
    after switching apktool versions)."""
    db = call_graph_db_path(decompile_cache_dir)
    removed = False
    if db.is_file():
        try:
            db.unlink()
            removed = True
        except OSError as e:
            logger.warning("call_graph.invalidate: failed to remove %s: %s", db, e)
    if drop_apktool:
        out = apktool_out_dir(decompile_cache_dir)
        if out.is_dir():
            shutil.rmtree(out, ignore_errors=True)
    return removed


# ---------------------------------------------------------------------------
# Persist helpers — build {class,node} id tables then executemany inserts.


def _build_class_rows(
    classes: list[smali_parser.ClassDecl],
    external_class_descs: set[str],
    sources_root: Optional[Path],
) -> tuple[
    dict[str, int],                # desc -> id
    list[tuple[Any, ...]],         # classes rows
    list[tuple[int, str]],         # (class_id, interface_name) rows
]:
    """Assemble ``classes`` + ``class_interfaces`` rows.

    In-app classes are allocated first (ids 1..N), external classes
    after. ``jadx_file`` is looked up in ``sources_root`` when provided:
    we prefer ``.java`` and fall back to ``.kt``. Missing source files
    yield NULL so the UI can still render the node.
    """
    desc_to_id: dict[str, int] = {}
    rows: list[tuple[Any, ...]] = []
    iface_rows: list[tuple[int, str]] = []

    next_id = 1
    for c in classes:
        class_name = class_desc_to_java(c.class_desc)
        package, simple = split_class_name(class_name)
        super_name = class_desc_to_java(c.super_desc) if c.super_desc else None
        jadx_file = _resolve_jadx_file(class_name, sources_root)
        rows.append((
            next_id, c.class_desc, class_name, package, simple,
            super_name,
            0,
            1 if c.is_abstract else 0,
            1 if c.is_interface else 0,
            c.file, jadx_file,
        ))
        desc_to_id[c.class_desc] = next_id
        for iface in c.interfaces:
            iface_rows.append((next_id, class_desc_to_java(iface)))
        next_id += 1

    for ext_desc in sorted(external_class_descs):
        if ext_desc in desc_to_id:
            continue
        class_name = class_desc_to_java(ext_desc)
        package, simple = split_class_name(class_name)
        rows.append((
            next_id, ext_desc, class_name, package, simple,
            None, 1, 0, 0, None, None,
        ))
        desc_to_id[ext_desc] = next_id
        next_id += 1
    return desc_to_id, rows, iface_rows


def _resolve_jadx_file(java_class_name: str, sources_root: Optional[Path]) -> Optional[str]:
    """Return ``com/example/Foo.java`` (or ``.kt``) relative to ``sources_root``
    if it exists, else ``None``. Best-effort: one ``is_file`` check per extension.
    """
    if not sources_root or not sources_root.is_dir():
        return None
    base = java_class_name.split("$", 1)[0].replace(".", "/")
    for ext in (".java", ".kt"):
        rel = base + ext
        if (sources_root / rel).is_file():
            return rel
    return None


def _node_flags(method: smali_parser.MethodDecl) -> dict[str, int]:
    """Derive the boolean + access-flag columns for a node row."""
    return {
        "access_flags": compute_access_flags(method.flags),
        "is_static": 1 if "static" in method.flags else 0,
        "is_abstract": 1 if method.is_abstract else 0,
        "is_native": 1 if "native" in method.flags else 0,
        "is_synthetic": 1 if (
            "synthetic" in method.flags or "bridge" in method.flags
        ) else 0,
        "is_constructor": 1 if method.is_constructor else 0,
    }


def _build_node_rows(
    classes: list[smali_parser.ClassDecl],
    external_targets: set[str],
    desc_to_id: dict[str, int],
    reflective_sigs: set[str],
) -> tuple[dict[str, int], list[tuple[Any, ...]]]:
    """Assemble ``nodes`` rows (in-app declared + external referenced).

    Returns ``(sig_to_id, rows)`` so the edge builder can resolve
    ``src_method_sig`` / ``dst_method_sig`` to INTEGER FKs.
    """
    sig_to_id: dict[str, int] = {}
    rows: list[tuple[Any, ...]] = []
    next_id = 1

    for c in classes:
        class_id = desc_to_id[c.class_desc]
        for m in c.methods:
            flags = _node_flags(m)
            rows.append((
                next_id, m.signature, class_id,
                m.name, method_descriptor(m.params, m.ret),
                descriptor_to_java(m.ret),
                json.dumps(params_to_java(m.params)),
                flags["access_flags"], flags["is_static"], flags["is_abstract"],
                flags["is_native"], flags["is_synthetic"], flags["is_constructor"],
                0,  # is_external
                m.line_start, m.line_end,
                1 if m.signature in reflective_sigs else 0,
            ))
            sig_to_id[m.signature] = next_id
            next_id += 1

    for ext_sig in sorted(external_targets):
        if ext_sig in sig_to_id:
            continue
        owner, name, params, ret = _split_signature(ext_sig)
        class_id = desc_to_id.get(owner)
        if class_id is None:
            # Owner wasn't materialised — shouldn't happen because
            # external_class_descs includes all owners, but guard anyway.
            continue
        is_ctor = 1 if name in ("<init>", "<clinit>") else 0
        rows.append((
            next_id, ext_sig, class_id,
            name, method_descriptor(params, ret),
            descriptor_to_java(ret),
            json.dumps(params_to_java(params)),
            0, 0, 0, 0, 0, is_ctor,
            1,  # is_external
            None, None,
            0,
        ))
        sig_to_id[ext_sig] = next_id
        next_id += 1

    return sig_to_id, rows


def _split_signature(sig: str) -> tuple[str, str, str, str]:
    """Split ``Lcom/example/Foo;->bar(I[B)V`` into
    ``("Lcom/example/Foo;", "bar", "I[B", "V")``.
    """
    sep = sig.find(";->")
    if sep < 0:
        return sig, "", "", ""
    owner = sig[: sep + 1]
    rest = sig[sep + 3:]
    paren = rest.find("(")
    close = rest.find(")", paren + 1)
    if paren < 0 or close < 0:
        return owner, rest, "", ""
    return owner, rest[:paren], rest[paren + 1:close], rest[close + 1:]


def _build_edge_rows(
    edges: list[dispatch.ResolvedEdge],
    sig_to_id: dict[str, int],
    node_row_by_id: dict[int, tuple[Any, ...]],
) -> list[tuple[int, int, str, str, int]]:
    """Convert dispatch edges to ``edges`` rows (INTEGER FKs).

    When an invoke had no observed ``.line`` directive we fall back to
    the src node's ``smali_start_line`` so the NOT NULL constraint on
    ``src_line`` always holds. Edges whose source isn't in ``sig_to_id``
    (shouldn't happen — src methods are always declared) are silently
    dropped to avoid a partial-build crash.

    De-dup happens implicitly via the PK ``(src_id, dst_id, kind, src_line)``
    on INSERT OR IGNORE.
    """
    out: list[tuple[int, int, str, str, int]] = []
    for e in edges:
        src_id = sig_to_id.get(e.src_method_sig)
        dst_id = sig_to_id.get(e.dst_method_sig)
        if src_id is None or dst_id is None:
            continue
        line = e.src_line
        if line is None:
            # Fallback: method-start line. node rows are indexed 10 →
            # start_line, 11 → end_line from the INSERT shape above.
            row = node_row_by_id.get(src_id)
            if row is not None:
                line = row[14]  # smali_start_line index in the INSERT tuple
        if line is None:
            line = 0  # last-resort: never NULL
        out.append((src_id, dst_id, e.kind, e.invoke_op, int(line)))
    return out


def _persist_results(
    db: Path,
    sha: str,
    started: float,
    classes: list[smali_parser.ClassDecl],
    edges: list[dispatch.ResolvedEdge],
    external_targets: set[str],
    reflection: list[smali_parser.ReflectionHit],
    parser_summary: smali_parser.ParseSummary,
    sources_root: Optional[Path],
) -> None:
    """Single-transaction snapshot write — readers never see a partial graph."""
    external_class_descs = {
        ext_sig[:ext_sig.find(";->") + 1]
        for ext_sig in external_targets
        if ";->" in ext_sig
    }
    # Interfaces / supers of in-app classes may point at external classes
    # that aren't invoke targets; materialise those too so the hierarchy
    # is queryable.
    in_app_descs = {c.class_desc for c in classes}
    for c in classes:
        if c.super_desc and c.super_desc not in in_app_descs:
            external_class_descs.add(c.super_desc)
        for iface in c.interfaces:
            if iface not in in_app_descs:
                external_class_descs.add(iface)

    desc_to_id, class_rows, iface_rows = _build_class_rows(
        classes, external_class_descs, sources_root,
    )
    reflective_sigs = {r.src_method_sig for r in reflection}
    sig_to_id, node_rows = _build_node_rows(
        classes, external_targets, desc_to_id, reflective_sigs,
    )
    node_row_by_id = {r[0]: r for r in node_rows}
    edge_rows = _build_edge_rows(edges, sig_to_id, node_row_by_id)

    in_app_count = sum(1 for c in class_rows if c[6] == 0)   # is_external col
    external_count = sum(1 for c in class_rows if c[6] == 1)

    with _connect(db, write=True) as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM classes")
        conn.execute("DELETE FROM class_interfaces")
        conn.execute("DELETE FROM nodes")
        conn.execute("DELETE FROM edges")

        conn.executemany(
            "INSERT INTO classes("
            " id, smali_class, class_name, package, simple_name,"
            " super_class, is_external, is_abstract, is_interface,"
            " smali_file, jadx_file"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            class_rows,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO class_interfaces(class_id, interface_name) "
            "VALUES (?, ?)",
            iface_rows,
        )
        conn.executemany(
            "INSERT INTO nodes("
            " id, smali_id, class_id, method_name, descriptor,"
            " return_type, param_types_json,"
            " access_flags, is_static, is_abstract,"
            " is_native, is_synthetic, is_constructor, is_external,"
            " smali_start_line, smali_end_line,"
            " may_have_unresolved_reflection"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            node_rows,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO edges("
            " src_id, dst_id, kind, invoke_op, src_line"
            ") VALUES (?, ?, ?, ?, ?)",
            edge_rows,
        )

        _meta_set(conn, "status", "ready")
        _meta_set(conn, "sha", sha)
        _meta_set(conn, "built_at", str(started))
        _meta_set(conn, "finished_at", str(time.time()))
        _meta_set(conn, "fidelity_level", FIDELITY)
        _meta_set(conn, "parser_version", PARSER_VERSION)
        _meta_set(conn, "class_count", str(in_app_count))
        _meta_set(conn, "external_class_count", str(external_count))
        _meta_set(conn, "node_count", str(len(node_rows)))
        _meta_set(conn, "edge_count", str(len(edge_rows)))
        _meta_set(conn, "smali_files", str(parser_summary.smali_files))
        _meta_set(conn, "parser_skipped", str(parser_summary.skipped_files))
        _meta_set(conn, "reflection_hits", str(len(reflection)))
        _meta_set(conn, "apktool_smali_root", APKTOOL_OUT_SUBDIR)
        if parser_summary.parse_errors:
            _meta_set(conn, "parser_errors_json",
                      json.dumps(parser_summary.parse_errors[:50]))
        else:
            _meta_set(conn, "parser_errors_json", "[]")
        _meta_set(conn, "error", "")


# ---------------------------------------------------------------------------
# Build


def build_index(
    decompile_cache_dir: Path,
    apk_path: Path,
    sha: str,
    *,
    apktool_cmd: str = "apktool",
    sources_root: Optional[Path] = None,
) -> IndexStatus:
    """Decode the APK with apktool, parse Smali, resolve dispatch, persist.

    Always writes a fresh snapshot when the existing index doesn't match
    ``sha``; otherwise no-ops. ``sources_root`` is the jadx output dir
    (used only to populate ``classes.jadx_file``) — if omitted the column
    stays NULL and the 4.2 UI falls back to the smali_file for code jumps.

    Concurrency: an in-process ``_RUNNING`` registry serialises builds for
    the same ``(cache_dir, sha)`` so two HTTP requests can't fight over
    the SQLite file.
    """
    db = call_graph_db_path(decompile_cache_dir)
    key = (str(decompile_cache_dir), sha)

    with _RUNNING_LOCK:
        if key in _RUNNING:
            return IndexStatus(status="pending", sha=sha, db_path=str(db))
        _RUNNING[key] = _BuildJob(
            sha=sha, cache_dir=decompile_cache_dir, thread=threading.current_thread()
        )

    started = time.time()
    try:
        # Fast path: already-good index for this sha. Read-only peek so
        # a repeated build on the same sha doesn't bump the DB mtime.
        if db.is_file():
            with _connect(db) as conn:
                try:
                    sv = _meta_get(conn, "schema_version")
                    cur_sha = _meta_get(conn, "sha")
                    cur_status = _meta_get(conn, "status")
                    cur_fidelity = _meta_get(conn, "fidelity_level")
                    cur_parser = _meta_get(conn, "parser_version")
                except sqlite3.OperationalError:
                    sv = cur_sha = cur_status = cur_fidelity = cur_parser = None
                if (
                    sv == SCHEMA_VERSION
                    and cur_sha == sha
                    and cur_status == "ready"
                    and cur_fidelity == FIDELITY
                    and cur_parser == PARSER_VERSION
                ):
                    return get_status(decompile_cache_dir)

        # Mark pending in its own write txn so other readers see in-progress.
        with _connect(db, write=True) as conn:
            _ensure_schema(conn)
            _meta_set(conn, "status", "pending")
            _meta_set(conn, "sha", sha)
            _meta_set(conn, "fidelity_level", FIDELITY)
            _meta_set(conn, "parser_version", PARSER_VERSION)
            _meta_set(conn, "built_at", str(started))
            _meta_set(conn, "error", "")

        out_dir = apktool_out_dir(decompile_cache_dir)
        smali_roots = apktool_runner.find_smali_roots(out_dir)
        if not smali_roots:
            # Only demand apktool when we actually need to decode — rebuilds
            # against already-extracted smali (tests, dev setups without
            # apktool on PATH) can still proceed.
            if not apktool_runner.is_available(apktool_cmd):
                with _connect(db, write=True) as conn:
                    _meta_set(conn, "status", "failed")
                    _meta_set(conn, "finished_at", str(time.time()))
                    _meta_set(
                        conn, "error",
                        f"apktool not on PATH (looked for {apktool_cmd!r}); "
                        "install apktool to enable the call graph",
                    )
                return get_status(decompile_cache_dir)
            ok, err = apktool_runner.run_apktool_decode(apktool_cmd, apk_path, out_dir)
            if not ok:
                with _connect(db, write=True) as conn:
                    _meta_set(conn, "status", "failed")
                    _meta_set(conn, "finished_at", str(time.time()))
                    _meta_set(conn, "error", err[:2000])
                return get_status(decompile_cache_dir)
            smali_roots = apktool_runner.find_smali_roots(out_dir)
            if not smali_roots:
                with _connect(db, write=True) as conn:
                    _meta_set(conn, "status", "failed")
                    _meta_set(conn, "finished_at", str(time.time()))
                    _meta_set(conn, "error", "apktool produced no smali/ trees")
                return get_status(decompile_cache_dir)

        classes, parse_summary = smali_parser.parse_classes(smali_roots)
        invokes, reflection, invokes_summary = smali_parser.parse_invokes(
            smali_roots, classes
        )
        edges, _hier, external_targets = dispatch.resolve_invokes(classes, invokes)
        parse_summary.invokes = invokes_summary.invokes
        parse_summary.reflection_hits = invokes_summary.reflection_hits
        parse_summary.parse_errors.extend(invokes_summary.parse_errors)

        _persist_results(
            db, sha, started,
            classes, edges, external_targets, reflection, parse_summary,
            sources_root,
        )
        return get_status(decompile_cache_dir)
    except Exception as e:
        try:
            with _connect(db, write=True) as conn:
                _ensure_schema(conn)
                _meta_set(conn, "status", "failed")
                _meta_set(conn, "finished_at", str(time.time()))
                _meta_set(conn, "error", _format_build_error(e))
        except sqlite3.Error:
            pass
        raise
    finally:
        with _RUNNING_LOCK:
            _RUNNING.pop(key, None)


def start_build_async(
    decompile_cache_dir: Path,
    apk_path: Path,
    sha: str,
    *,
    apktool_cmd: str = "apktool",
    sources_root: Optional[Path] = None,
    on_done: Optional[Callable[[IndexStatus], None]] = None,
) -> dict[str, Any]:
    """Kick off :func:`build_index` in a daemon thread. Returns immediate status.

    Mirrors :func:`androscan.rag.index.start_build_async` so call sites can
    treat both the same way.
    """
    db = call_graph_db_path(decompile_cache_dir)
    key = (str(decompile_cache_dir), sha)
    with _RUNNING_LOCK:
        if key in _RUNNING:
            return get_status(decompile_cache_dir).to_dict()

    def _worker() -> None:
        try:
            status = build_index(
                decompile_cache_dir, apk_path, sha,
                apktool_cmd=apktool_cmd, sources_root=sources_root,
            )
            if on_done:
                try:
                    on_done(status)
                except Exception:  # pragma: no cover - defensive
                    logger.exception("call_graph on_done callback raised")
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("call_graph.build worker crashed: %s", e)
            try:
                with _connect(db, write=True) as conn:
                    _ensure_schema(conn)
                    _meta_set(conn, "status", "failed")
                    _meta_set(conn, "error", _format_build_error(e))
                    _meta_set(conn, "finished_at", str(time.time()))
            except sqlite3.Error:
                pass

    t = threading.Thread(
        target=_worker, daemon=True, name=f"call-graph-{sha[:10]}"
    )
    t.start()
    return {"status": "pending", "sha": sha, "db_path": str(db)}


# ---------------------------------------------------------------------------
# Diagnostic dump


def dump_meta(decompile_cache_dir: Path) -> dict[str, str]:
    db = call_graph_db_path(decompile_cache_dir)
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


# ---------------------------------------------------------------------------
# Read API used by graph_routes (and 4.2+ consumers).
#
# Returns plain dicts rather than dataclasses so the FastAPI serializer
# can emit them directly without any adapter layer.


def _node_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    try:
        param_types = json.loads(r["param_types_json"])
    except (TypeError, ValueError):
        param_types = []
    return {
        "id": r["id"],
        "smali_id": r["smali_id"],
        "class_id": r["class_id"],
        "method_name": r["method_name"],
        "descriptor": r["descriptor"],
        "return_type": r["return_type"],
        "param_types": param_types,
        "access_flags": r["access_flags"],
        "is_static": bool(r["is_static"]),
        "is_abstract": bool(r["is_abstract"]),
        "is_native": bool(r["is_native"]),
        "is_synthetic": bool(r["is_synthetic"]),
        "is_constructor": bool(r["is_constructor"]),
        "is_external": bool(r["is_external"]),
        "smali_start_line": r["smali_start_line"],
        "smali_end_line": r["smali_end_line"],
        "may_have_unresolved_reflection": bool(r["may_have_unresolved_reflection"]),
    }


def _class_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "smali_class": r["smali_class"],
        "class_name": r["class_name"],
        "package": r["package"],
        "simple_name": r["simple_name"],
        "super_class": r["super_class"],
        "is_external": bool(r["is_external"]),
        "is_abstract": bool(r["is_abstract"]),
        "is_interface": bool(r["is_interface"]),
        "smali_file": r["smali_file"],
        "jadx_file": r["jadx_file"],
    }


def _edge_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "src_id": r["src_id"],
        "dst_id": r["dst_id"],
        "kind": r["kind"],
        "invoke_op": r["invoke_op"],
        "src_line": r["src_line"],
    }


def list_graph(
    decompile_cache_dir: Path,
    *,
    package_prefix: Optional[str] = None,
    kind: Optional[str] = None,
    include_external: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated node + edge listing for the 4.2 Cytoscape initial view.

    Hard limit of 5000 per request to keep the JSON payload bounded.
    ``kind`` filters edges (one of the six enum values); ``package_prefix``
    filters nodes by their class's ``package``. Edges connecting to
    filtered-out nodes are also dropped so the returned subgraph is
    self-consistent.
    """
    db = call_graph_db_path(decompile_cache_dir)
    if not db.is_file():
        return {"nodes": [], "edges": [], "classes": [],
                "total_nodes": 0, "total_edges": 0, "total_classes": 0}
    limit = max(1, min(int(limit), 5000))
    offset = max(0, int(offset))

    node_where: list[str] = []
    node_args: list[Any] = []
    if not include_external:
        node_where.append("n.is_external = 0")
    if package_prefix:
        node_where.append("c.package LIKE ?")
        node_args.append(f"{package_prefix}%")

    try:
        with _connect(db) as conn:
            base_nodes = (
                "SELECT n.* FROM nodes n JOIN classes c ON c.id = n.class_id"
            )
            total_nodes_q = (
                "SELECT COUNT(*) FROM nodes n JOIN classes c ON c.id = n.class_id"
            )
            if node_where:
                base_nodes += " WHERE " + " AND ".join(node_where)
                total_nodes_q += " WHERE " + " AND ".join(node_where)
            total_nodes = conn.execute(total_nodes_q, node_args).fetchone()[0]
            node_rows = conn.execute(
                base_nodes + " ORDER BY n.id LIMIT ? OFFSET ?",
                [*node_args, limit, offset],
            ).fetchall()
            node_ids = [r["id"] for r in node_rows]

            classes_rows: list[sqlite3.Row] = []
            if node_ids:
                class_ids = sorted({r["class_id"] for r in node_rows})
                placeholders = ",".join("?" * len(class_ids))
                classes_rows = conn.execute(
                    f"SELECT * FROM classes WHERE id IN ({placeholders})",
                    class_ids,
                ).fetchall()

            # Edges where both endpoints are inside the returned node set.
            edge_rows: list[sqlite3.Row] = []
            total_edges = 0
            if node_ids:
                placeholders = ",".join("?" * len(node_ids))
                edge_sql = (
                    f"SELECT * FROM edges WHERE src_id IN ({placeholders})"
                    f" AND dst_id IN ({placeholders})"
                )
                edge_args: list[Any] = [*node_ids, *node_ids]
                if kind:
                    edge_sql += " AND kind = ?"
                    edge_args.append(kind)
                edge_rows = conn.execute(edge_sql, edge_args).fetchall()
                total_edges = len(edge_rows)
    except sqlite3.Error as e:
        logger.warning("list_graph sqlite error: %s", e)
        return {"nodes": [], "edges": [], "classes": [],
                "total_nodes": 0, "total_edges": 0, "total_classes": 0,
                "error": str(e)}

    return {
        "nodes": [_node_to_dict(r) for r in node_rows],
        "edges": [_edge_to_dict(r) for r in edge_rows],
        "classes": [_class_to_dict(r) for r in classes_rows],
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "total_classes": len(classes_rows),
    }


def neighbors(
    decompile_cache_dir: Path,
    node_ref: str,
    *,
    limit_each: int = 200,
) -> Optional[dict[str, Any]]:
    """Callers + callees of a node.

    ``node_ref`` is either a numeric ``id`` or the full smali id
    (``Lcom/example/Foo;->bar(I)V``); ``graph_routes`` decodes the URL
    segment before passing it in. Hard cap of 200 each side keeps
    runaway hubs like ``Log.d`` bounded.
    """
    db = call_graph_db_path(decompile_cache_dir)
    if not db.is_file():
        return None
    limit_each = max(1, min(int(limit_each), 2000))
    try:
        with _connect(db) as conn:
            node_row = _lookup_node(conn, node_ref)
            if node_row is None:
                return None
            node_id = node_row["id"]

            callers = conn.execute(
                "SELECT n.*, e.kind AS e_kind, e.invoke_op AS e_invoke_op,"
                " e.src_line AS e_src_line, e.src_id AS e_src_id,"
                " e.dst_id AS e_dst_id"
                " FROM edges e JOIN nodes n ON n.id = e.src_id"
                " WHERE e.dst_id = ?"
                " ORDER BY e.src_line, n.id LIMIT ?",
                (node_id, limit_each),
            ).fetchall()
            callees = conn.execute(
                "SELECT n.*, e.kind AS e_kind, e.invoke_op AS e_invoke_op,"
                " e.src_line AS e_src_line, e.src_id AS e_src_id,"
                " e.dst_id AS e_dst_id"
                " FROM edges e JOIN nodes n ON n.id = e.dst_id"
                " WHERE e.src_id = ?"
                " ORDER BY e.src_line, n.id LIMIT ?",
                (node_id, limit_each),
            ).fetchall()

            relevant_class_ids = sorted(
                {node_row["class_id"]}
                | {r["class_id"] for r in callers}
                | {r["class_id"] for r in callees}
            )
            if relevant_class_ids:
                placeholders = ",".join("?" * len(relevant_class_ids))
                class_rows = conn.execute(
                    f"SELECT * FROM classes WHERE id IN ({placeholders})",
                    relevant_class_ids,
                ).fetchall()
            else:
                class_rows = []

            return {
                "node": _node_to_dict(node_row),
                "callers": [
                    {"node": _node_to_dict(r),
                     "edge": {
                         "src_id": r["e_src_id"], "dst_id": r["e_dst_id"],
                         "kind": r["e_kind"], "invoke_op": r["e_invoke_op"],
                         "src_line": r["e_src_line"],
                     }}
                    for r in callers
                ],
                "callees": [
                    {"node": _node_to_dict(r),
                     "edge": {
                         "src_id": r["e_src_id"], "dst_id": r["e_dst_id"],
                         "kind": r["e_kind"], "invoke_op": r["e_invoke_op"],
                         "src_line": r["e_src_line"],
                     }}
                    for r in callees
                ],
                "classes": [_class_to_dict(r) for r in class_rows],
            }
    except sqlite3.Error as e:
        logger.warning("neighbors sqlite error: %s", e)
        return None


def _lookup_node(conn: sqlite3.Connection, node_ref: str) -> Optional[sqlite3.Row]:
    """Accept either an integer id (as a string) or a smali_id."""
    ref = (node_ref or "").strip()
    if not ref:
        return None
    if ref.isdigit():
        return conn.execute(
            "SELECT * FROM nodes WHERE id = ?", (int(ref),)
        ).fetchone()
    return conn.execute(
        "SELECT * FROM nodes WHERE smali_id = ?", (ref,)
    ).fetchone()


def paths(
    decompile_cache_dir: Path,
    from_ref: str,
    to_ref: str,
    *,
    max_hops: int = 8,
    max_paths: int = 10,
    include_external: bool = False,
) -> dict[str, Any]:
    """Bounded BFS returning up to ``max_paths`` distinct paths.

    Paths are lists of node ids starting with ``from_ref`` and ending
    with ``to_ref``. ``max_hops`` is server-capped at 12 by the route
    so the BFS never runs unbounded even on pathological inputs.
    Simple cycles are skipped (no node repeats within a path).
    """
    db = call_graph_db_path(decompile_cache_dir)
    if not db.is_file():
        return {"paths": []}
    max_hops = max(1, min(int(max_hops), 12))
    max_paths = max(1, min(int(max_paths), 50))
    try:
        with _connect(db) as conn:
            src_row = _lookup_node(conn, from_ref)
            dst_row = _lookup_node(conn, to_ref)
            if src_row is None or dst_row is None:
                return {"paths": []}
            src_id = src_row["id"]
            dst_id = dst_row["id"]

            queue: deque[list[int]] = deque([[src_id]])
            found: list[list[int]] = []
            while queue and len(found) < max_paths:
                path = queue.popleft()
                if len(path) - 1 >= max_hops:
                    continue
                cur = path[-1]
                sql = "SELECT dst_id, kind FROM edges WHERE src_id = ?"
                args: list[Any] = [cur]
                if not include_external:
                    sql += " AND kind != 'external'"
                rows = conn.execute(sql, args).fetchall()
                seen_in_path = set(path)
                for r in rows:
                    nxt = r[0]
                    if nxt in seen_in_path:
                        continue
                    new_path = path + [nxt]
                    if nxt == dst_id:
                        found.append(new_path)
                        if len(found) >= max_paths:
                            break
                    else:
                        queue.append(new_path)
            return {
                "paths": found,
                "from": src_id,
                "to": dst_id,
                "max_hops": max_hops,
                "max_paths": max_paths,
                "include_external": include_external,
            }
    except sqlite3.Error as e:
        logger.warning("paths sqlite error: %s", e)
        return {"paths": [], "error": str(e)}
