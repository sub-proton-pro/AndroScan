"""Tests for the Lane-1 RAG indexer (chunking + index + search)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from androscan.config import Config
from androscan.rag import chunking, index, search
from androscan.rag.embed import (
    EmbedProviderError,
    HashProvider,
    OllamaEmbedProvider,
    get_provider,
)


# ---------------------------------------------------------------------------
# Helpers


def _java(pkg: str, cls: str, methods: list[tuple[str, str]]) -> str:
    """Render a tiny Java class with named methods (name, body)."""
    body = "\n".join(
        f"    public void {n}() {{\n        {b}\n    }}\n"
        for n, b in methods
    )
    return f"package {pkg};\n\npublic class {cls} {{\n{body}\n}}\n"


def _write_sources(root: Path) -> None:
    """Build a small synthetic decompiled tree under ``root``."""
    a = root / "com" / "example" / "weakbank"
    a.mkdir(parents=True)
    (a / "LoginActivity.java").write_text(
        _java(
            "com.example.weakbank",
            "LoginActivity",
            [
                ("onCreate", 'String pwd = etPassword.getText().toString();'),
                ("checkPassword", 'return pwd.equals("hunter2hunter2");'),
                ("onLogout", 'session.invalidate();'),
            ],
        ),
        encoding="utf-8",
    )
    (a / "Crypto.java").write_text(
        _java(
            "com.example.weakbank",
            "Crypto",
            [
                ("encryptAes", 'cipher.init(Cipher.ENCRYPT_MODE, key);'),
                ("deriveKey", 'return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA1");'),
            ],
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Chunking


def test_chunk_file_emits_class_header_and_one_chunk_per_method(tmp_path: Path) -> None:
    src = _java(
        "com.example.app",
        "Foo",
        [("a", "int x = 1;"), ("b", "int y = 2;")],
    )
    chunks = chunking.chunk_file("Foo.java", src)
    kinds = [c.kind for c in chunks]
    assert "class_header" in kinds
    method_chunks = [c for c in chunks if c.kind == "method"]
    method_names = sorted(c.method_name for c in method_chunks)
    assert method_names == ["a", "b"]


def test_chunk_file_blacklists_keywords() -> None:
    src = (
        "package p;\n"
        "class C {\n"
        "  void real() { if (true) { } for (int i=0;i<1;i++) { } }\n"
        "}\n"
    )
    chunks = chunking.chunk_file("C.java", src)
    method_chunks = [c for c in chunks if c.kind == "method"]
    assert [c.method_name for c in method_chunks] == ["real"]


def test_chunk_file_handles_strings_with_braces() -> None:
    """Brace-balancer must ignore { } that live inside strings/comments."""
    src = (
        "package p;\n"
        "class S {\n"
        '  String s() { String x = "}"; /* { */ return "{"; }\n'
        "}\n"
    )
    chunks = chunking.chunk_file("S.java", src)
    method_chunks = [c for c in chunks if c.kind == "method"]
    assert len(method_chunks) == 1
    assert "return" in method_chunks[0].content


def test_chunk_sources_walks_recursively(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    chunks, stats = chunking.chunk_sources(tmp_path)
    assert stats.files_scanned == 2
    files = {c.file for c in chunks}
    assert any(f.endswith("LoginActivity.java") for f in files)
    assert any(f.endswith("Crypto.java") for f in files)


def test_chunk_id_is_stable() -> None:
    src = _java("p", "F", [("m", "int x = 1;")])
    a = chunking.chunk_file("F.java", src)
    b = chunking.chunk_file("F.java", src)
    assert [c.chunk_id() for c in a] == [c.chunk_id() for c in b]


# ---------------------------------------------------------------------------
# Embedding providers


def test_hash_provider_is_normalized_and_deterministic() -> None:
    p = HashProvider()
    v1 = p.embed(["the quick brown fox"])[0]
    v2 = p.embed(["the quick brown fox"])[0]
    assert v1 == v2
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_get_provider_requires_explicit_opt_in_for_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANDROSCAN_RAG_PROVIDER", "hash")
    monkeypatch.delenv("ANDROSCAN_RAG_ALLOW_HASH", raising=False)
    cfg = Config.default()
    with pytest.raises(EmbedProviderError):
        get_provider(cfg)


def test_get_provider_hash_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANDROSCAN_RAG_PROVIDER", "hash")
    monkeypatch.setenv("ANDROSCAN_RAG_ALLOW_HASH", "1")
    p = get_provider(Config.default())
    assert p.name == "hash"
    assert p.dim > 0


def test_ollama_provider_surfaces_clear_error_when_unreachable() -> None:
    """We don't run Ollama in CI; the probe must fail with EmbedProviderError."""
    with pytest.raises(EmbedProviderError):
        OllamaEmbedProvider(base_url="http://127.0.0.1:1", model="nomic-embed-text", timeout=1)


# ---------------------------------------------------------------------------
# Index + search roundtrip (with HashProvider so tests stay hermetic)


def test_build_and_query_roundtrip(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_sources(sources)

    provider = HashProvider()
    status = index.build_index(cache, sources, sha="abc", provider=provider)
    assert status.status == "ready"
    assert status.chunk_count is not None and status.chunk_count > 0
    assert status.dim == provider.dim

    hits = search.query(cache, "password equals hunter2", provider, top_k=3)
    assert hits, "expected at least one hit"
    # The LoginActivity file contains the password literal so the hash provider
    # — even though it's a hashing trick — should rank LoginActivity above Crypto.
    files = [h.file for h in hits]
    assert any("LoginActivity" in f for f in files)


def test_query_filters_by_file_substring(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_sources(sources)
    provider = HashProvider()
    index.build_index(cache, sources, sha="abc", provider=provider)

    hits = search.query(
        cache, "encrypt key cipher", provider, top_k=5, file_substr="Crypto"
    )
    assert hits
    assert all("Crypto" in h.file for h in hits)


def test_rebuild_invalidates_old_provider(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_sources(sources)

    p1 = HashProvider(model="m1", dim=64)
    p2 = HashProvider(model="m2", dim=128)

    s1 = index.build_index(cache, sources, sha="abc", provider=p1)
    assert s1.status == "ready" and s1.dim == 64

    # Different model+dim must trigger a rebuild rather than mixing vectors.
    s2 = index.build_index(cache, sources, sha="abc", provider=p2)
    assert s2.status == "ready"
    assert s2.dim == 128
    assert s2.provider_model == "m2"

    # Searching with the original provider now must error out cleanly.
    with pytest.raises(EmbedProviderError):
        search.query(cache, "anything", p1, top_k=1)


def test_invalidate_removes_db(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_sources(sources)
    provider = HashProvider()
    index.build_index(cache, sources, sha="abc", provider=provider)
    assert index.rag_db_path(cache).is_file()
    assert index.invalidate(cache) is True
    assert not index.rag_db_path(cache).is_file()


def test_query_on_empty_text_returns_empty(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_sources(sources)
    provider = HashProvider()
    index.build_index(cache, sources, sha="abc", provider=provider)
    assert search.query(cache, "", provider, top_k=3) == []
    assert search.query(cache, "   ", provider, top_k=3) == []


def test_status_missing_when_no_db(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    s = index.get_status(cache)
    assert s.status == "missing"


def test_status_pending_within_grace_stays_pending(tmp_path: Path) -> None:
    """A freshly-kicked build should be reported as pending, not orphaned.

    We simulate the very short window between ``start_build_async`` returning
    and the worker registering itself in ``_RUNNING`` — ``built_at`` is
    "just now" and the grace period hasn't elapsed.
    """
    import time as _time
    from androscan.rag.index import _connect, _ensure_schema, _meta_set, rag_db_path

    cache = tmp_path / "cache"
    cache.mkdir()
    db = rag_db_path(cache)
    with _connect(db, write=True) as conn:
        _ensure_schema(conn)
        _meta_set(conn, "status", "pending")
        _meta_set(conn, "sha", "abc")
        _meta_set(conn, "built_at", str(_time.time()))

    s = index.get_status(cache)
    assert s.status == "pending"
    assert s.error is None


def test_status_pending_orphaned_after_grace_becomes_failed(tmp_path: Path) -> None:
    """A pending row older than the grace period with no live worker is reclassified."""
    import time as _time
    from androscan.rag.index import _connect, _ensure_schema, _meta_set, rag_db_path

    cache = tmp_path / "cache"
    cache.mkdir()
    db = rag_db_path(cache)
    long_ago = _time.time() - 3600  # 1 hour ago, well past PENDING_GRACE_SEC
    with _connect(db, write=True) as conn:
        _ensure_schema(conn)
        _meta_set(conn, "status", "pending")
        _meta_set(conn, "sha", "abc")
        _meta_set(conn, "built_at", str(long_ago))

    s = index.get_status(cache)
    assert s.status == "failed"
    assert s.error is not None and "interrupted" in s.error.lower()
    # built_at is preserved so the UI can show "started ~1h ago".
    assert s.built_at is not None and abs(s.built_at - long_ago) < 0.001


def test_status_pending_with_live_worker_stays_pending(tmp_path: Path) -> None:
    """An orphan-age row should *not* be reclassified if a live worker is registered."""
    import threading as _t
    import time as _time
    from androscan.rag.index import (
        _BuildJob, _RUNNING, _RUNNING_LOCK, _connect, _ensure_schema, _meta_set,
        rag_db_path,
    )

    cache = tmp_path / "cache"
    cache.mkdir()
    db = rag_db_path(cache)
    long_ago = _time.time() - 3600
    with _connect(db, write=True) as conn:
        _ensure_schema(conn)
        _meta_set(conn, "status", "pending")
        _meta_set(conn, "sha", "abc")
        _meta_set(conn, "built_at", str(long_ago))

    # Pretend a long-running worker is still alive.
    stop = _t.Event()
    worker = _t.Thread(target=stop.wait, daemon=True)
    worker.start()
    key = (str(cache), "abc")
    with _RUNNING_LOCK:
        _RUNNING[key] = _BuildJob(sha="abc", cache_dir=cache, thread=worker)
    try:
        s = index.get_status(cache)
        assert s.status == "pending"
    finally:
        with _RUNNING_LOCK:
            _RUNNING.pop(key, None)
        stop.set()
        worker.join(timeout=2.0)
