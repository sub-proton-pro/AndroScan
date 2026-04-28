"""SQLite round-trip + lifecycle tests for :mod:`androscan.analysis.call_graph`.

Fixture strategy: we copy the pre-extracted smali trees from
``tests/fixtures/call_graph_smali/`` into a tmpdir laid out exactly like
a real decompile cache (``.decompiled/<sha>/smali_out/smali[,_classes2]``).
That lets us exercise ``build_index`` without ever invoking apktool —
the code path short-circuits when ``find_smali_roots`` returns non-empty.

Covers:

* Build → ``ready``, populated ``classes`` / ``nodes`` / ``edges``, meta
  fields.
* External nodes are materialised (``Landroid/util/Log;`` etc. get
  ``is_external=1`` rows) so graph queries don't stall at in-app
  boundaries.
* Virtual dispatch rows visible via ``list_graph``.
* ``neighbors`` returns both callers and callees with full edge info.
* ``paths`` finds at least one path that matches expected hops.
* ``invalidate`` removes the DB and a subsequent rebuild produces the
  same counts (idempotency).
* ``get_status`` orphan-pending recovery: stale "pending" with no live
  worker flips to ``failed`` after the grace window.
* ``is_build_running`` is false when no thread is registered.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from androscan.analysis import call_graph


FIXTURES = Path(__file__).parent / "fixtures" / "call_graph_smali"
SHA = "deadbeef" * 5  # 40 chars — shape matches real sha1


def _seed_cache(tmp_path: Path) -> Path:
    """Lay out a fake decompile-cache directory pre-populated with the
    fixture smali trees so ``build_index`` skips apktool decoding.

    Returns the cache directory (equivalent to
    ``apps/<app_id>/.decompiled/<sha>/`` in production).
    """
    cache = tmp_path / "app" / ".decompiled" / SHA
    smali_out = cache / call_graph.APKTOOL_OUT_SUBDIR
    smali_out.mkdir(parents=True)
    shutil.copytree(FIXTURES / "smali", smali_out / "smali")
    shutil.copytree(FIXTURES / "smali_classes2", smali_out / "smali_classes2")
    return cache


def test_missing_status_before_build(tmp_path: Path) -> None:
    cache = tmp_path / "never-built"
    cache.mkdir()
    st = call_graph.get_status(cache)
    assert st.status == "missing"
    assert st.node_count is None


def test_build_index_populates_schema_and_meta(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    st = call_graph.build_index(cache, apk_path=Path("/nonexistent.apk"), sha=SHA)
    assert st.status == "ready", st.error
    assert st.sha == SHA
    assert st.fidelity_level == call_graph.FIDELITY
    assert st.parser_version == call_graph.PARSER_VERSION
    # 7 in-app classes in the fixture (Animal, Dog, Cat, Greeter, HelloGreeter, App, Helper).
    assert st.class_count == 7
    # External classes: Object, Log, Class, reflect.Method (at minimum).
    assert (st.external_class_count or 0) >= 3
    assert (st.node_count or 0) > 0
    assert (st.edge_count or 0) > 0


def test_in_app_and_external_node_counts(tmp_path: Path) -> None:
    """Sanity-check the external-node materialisation design choice."""
    import sqlite3
    cache = _seed_cache(tmp_path)
    call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    db = call_graph.call_graph_db_path(cache)
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        in_app = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE is_external = 0"
        ).fetchone()[0]
        external = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE is_external = 1"
        ).fetchone()[0]
        assert in_app > 0
        assert external > 0
        # Log.d / Class.forName / Class.getMethod must be external nodes.
        log_node = conn.execute(
            "SELECT n.is_external FROM nodes n"
            " JOIN classes c ON c.id = n.class_id"
            " WHERE c.smali_class = 'Landroid/util/Log;' AND n.method_name = 'd'"
        ).fetchone()
        assert log_node is not None
        assert log_node[0] == 1


def test_reflection_flag_on_app_reflect_method(tmp_path: Path) -> None:
    import sqlite3
    cache = _seed_cache(tmp_path)
    call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    db = call_graph.call_graph_db_path(cache)
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT may_have_unresolved_reflection FROM nodes"
            " WHERE smali_id = 'Lcom/example/App;->reflect()V'"
        ).fetchone()
        assert row is not None and row[0] == 1


def test_virtual_dispatch_edges_from_app_main(tmp_path: Path) -> None:
    import sqlite3
    cache = _seed_cache(tmp_path)
    call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    db = call_graph.call_graph_db_path(cache)
    with sqlite3.connect(str(db)) as conn:
        # Find the 'main' node id, then count virtual_dispatch edges out.
        main_id = conn.execute(
            "SELECT id FROM nodes WHERE smali_id = 'Lcom/example/App;->main()V'"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT n.smali_id FROM edges e JOIN nodes n ON n.id = e.dst_id"
            " WHERE e.src_id = ? AND e.kind = 'virtual_dispatch'",
            (main_id,),
        ).fetchall()
        dsts = {r[0] for r in rows}
    assert "Lcom/example/Animal;->speak()V" in dsts
    assert "Lcom/example/Dog;->speak()V" in dsts
    assert "Lcom/example/Cat;->speak()V" in dsts


def test_list_graph_honours_package_filter_and_excludes_external(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    payload = call_graph.list_graph(cache, package_prefix="com.example", limit=500)
    assert payload["total_nodes"] > 0
    # Default excludes external.
    assert all(n["is_external"] is False for n in payload["nodes"])
    class_names = {c["class_name"] for c in payload["classes"]}
    assert "com.example.App" in class_names


def test_list_graph_include_external_surfaces_log(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    payload = call_graph.list_graph(cache, include_external=True, limit=2000)
    node_ids = {n["smali_id"] for n in payload["nodes"]}
    assert any(sig.startswith("Landroid/util/Log;") for sig in node_ids)


def test_neighbors_returns_callers_and_callees(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    dog_speak = call_graph.neighbors(cache, "Lcom/example/Dog;->speak()V")
    assert dog_speak is not None
    caller_sigs = {c["node"]["smali_id"] for c in dog_speak["callers"]}
    assert "Lcom/example/App;->main()V" in caller_sigs
    # Dog.speak is a leaf — no outgoing in-app edges (body is just return-void).
    assert dog_speak["callees"] == []


def test_paths_finds_route_from_main_to_dog_speak(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    res = call_graph.paths(
        cache,
        "Lcom/example/App;->main()V",
        "Lcom/example/Dog;->speak()V",
        max_hops=3,
        max_paths=5,
    )
    assert res["paths"], "expected at least one path"
    # Path IDs are integers; we verify it ends at the dog-speak node.
    import sqlite3
    db = call_graph.call_graph_db_path(cache)
    with sqlite3.connect(str(db)) as conn:
        dog_id = conn.execute(
            "SELECT id FROM nodes WHERE smali_id = 'Lcom/example/Dog;->speak()V'"
        ).fetchone()[0]
    assert all(p[-1] == dog_id for p in res["paths"])


def test_invalidate_then_rebuild_is_idempotent(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    first = call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    assert first.status == "ready"
    assert call_graph.invalidate(cache) is True
    assert call_graph.get_status(cache).status == "missing"
    second = call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    assert second.status == "ready"
    assert second.node_count == first.node_count
    assert second.edge_count == first.edge_count


def test_fast_path_when_sha_matches(tmp_path: Path) -> None:
    """Second call with the same sha must not rewrite the DB (mtime trick)."""
    cache = _seed_cache(tmp_path)
    call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    db = call_graph.call_graph_db_path(cache)
    mtime_before = db.stat().st_mtime
    # Sleep briefly so a rewrite would bump mtime visibly on all FSes.
    time.sleep(0.05)
    call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    assert db.stat().st_mtime == pytest.approx(mtime_before, abs=0.02)


def test_orphan_pending_flips_to_failed(tmp_path: Path) -> None:
    import sqlite3
    cache = _seed_cache(tmp_path)
    db = call_graph.call_graph_db_path(cache)
    db.parent.mkdir(parents=True, exist_ok=True)
    # Hand-craft a pending DB with built_at far in the past and no running job.
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        now = time.time() - (call_graph.PENDING_GRACE_SEC * 2)
        for k, v in [
            ("schema_version", call_graph.SCHEMA_VERSION),
            ("status", "pending"),
            ("sha", SHA),
            ("built_at", str(now)),
            ("fidelity_level", call_graph.FIDELITY),
            ("parser_version", call_graph.PARSER_VERSION),
        ]:
            conn.execute("INSERT INTO meta(key, value) VALUES (?, ?)", (k, v))
    st = call_graph.get_status(cache)
    assert st.status == "failed"
    assert "interrupted" in (st.error or "").lower()


def test_is_build_running_false_without_registration(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    # No build has been started — registry empty.
    assert call_graph.is_build_running(cache, SHA) is False


def test_schema_mismatch_marks_failed(tmp_path: Path) -> None:
    import sqlite3
    cache = _seed_cache(tmp_path)
    db = call_graph.call_graph_db_path(cache)
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', '0')"
        )
    st = call_graph.get_status(cache)
    assert st.status == "failed"
    assert "schema_version" in (st.error or "")


def test_dump_meta_returns_all_keys(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    call_graph.build_index(cache, Path("/n/a.apk"), SHA)
    meta = call_graph.dump_meta(cache)
    assert meta.get("status") == "ready"
    assert meta.get("sha") == SHA
    assert meta.get("fidelity_level") == call_graph.FIDELITY
    assert int(meta.get("edge_count", "0")) > 0


# ---------------------------------------------------------------------------
# _format_build_error: turns the opaque ``OperationalError: disk I/O error``
# into something operators can act on (SQLITE_IOERR_FSYNC vs LOCK vs
# SHMOPEN vs CANTOPEN). Without the suffix every flavour of SQLITE_IOERR_*
# renders identically in the Settings → Status card.


def test_format_build_error_includes_sqlite_errorname(tmp_path: Path) -> None:
    """Real ``sqlite3.OperationalError`` instances carry
    ``sqlite_errorname`` on Python 3.11+; the helper must surface it."""
    import sqlite3
    try:
        sqlite3.connect(str(tmp_path / "no" / "such" / "dir.sqlite"))
    except sqlite3.OperationalError as e:
        msg = call_graph._format_build_error(e)
        assert msg.startswith("OperationalError: ")
        assert "unable to open database file" in msg
        # Python 3.11+ exposes the extended result code; older runtimes
        # gracefully omit the suffix (the helper's getattr fallback).
        if getattr(e, "sqlite_errorname", None):
            assert "[SQLITE_CANTOPEN]" in msg
    else:  # pragma: no cover - defensive: connect should always raise
        pytest.fail("expected sqlite3.OperationalError")


def test_format_build_error_handles_non_sqlite_exception() -> None:
    """Non-SQLite errors (apktool subprocess crash, parser bug, etc.) must
    not break the helper — they just lack the bracketed suffix."""
    err = ValueError("smali parser blew up")
    msg = call_graph._format_build_error(err)
    assert msg == "ValueError: smali parser blew up"
    assert "[" not in msg


def test_format_build_error_truncates_huge_messages() -> None:
    """Cap matches the previous 2000-char ceiling so long stack-style
    messages don't blow out the meta row."""
    err = RuntimeError("x" * 5000)
    msg = call_graph._format_build_error(err)
    assert len(msg) == 2000
    assert msg.startswith("RuntimeError: ")
