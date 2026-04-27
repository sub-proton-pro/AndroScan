"""Tests for the LLM-tier ``query_call_graph`` skill (sub-step 4.7 / DEC-023).

We seed a real ``apps/<app_id>/.decompiled/<sha>/`` cache with the same
fixture smali used by :mod:`test_call_graph_index` / :mod:`test_graph_routes`,
build the call-graph SQLite synchronously, and exercise each ``mode``
(``overview`` / ``neighbors`` / ``paths``) through the skill registry.
The skill's app-dir resolver expects ``run_folder.parent`` to be the app
directory, so the fixture mirrors the production layout.

Fail-open paths (no app context, no decompile, no call graph) and the
input-validation paths (unknown mode, missing ``node_ref`` / ``source``
/ ``target``) are covered too — these are the branches the LLM will
actually exercise on its first few turns.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from androscan.analysis import call_graph
from androscan.config import Config
from androscan.skills import (
    SkillContext,
    execute,
    list_llm_skills,
    list_skills_by_tier,
)
from androscan.web import decompile_cache as dc

FIXTURES = Path(__file__).parent / "fixtures" / "call_graph_smali"


def _seed_apps_with_graph(tmp_path: Path, app_id: str = "myapp") -> Path:
    """Mirror tests/test_graph_routes._seed_app_with_graph but return the
    app dir directly so tests can construct a SkillContext."""
    apps_root = tmp_path / "apps"
    app_dir = apps_root / app_id
    app_dir.mkdir(parents=True)
    sha = "f" * 40
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK")
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": sha, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )
    dc.sources_dir(app_dir, sha).mkdir(parents=True)
    dc._write_index(
        app_dir, sha,
        {"status": "ready", "apk_path": str(apk), "apk_sha256": sha, "file_count": 0},
    )
    cache = dc.cache_root_for(app_dir, sha)
    smali_out = cache / call_graph.APKTOOL_OUT_SUBDIR
    smali_out.mkdir(parents=True)
    shutil.copytree(FIXTURES / "smali", smali_out / "smali")
    shutil.copytree(FIXTURES / "smali_classes2", smali_out / "smali_classes2")
    st = call_graph.build_index(cache, apk_path=apk, sha=sha)
    assert st.status == "ready", st.error
    return app_dir


def _ctx_for(app_dir: Path) -> SkillContext:
    """Build a SkillContext whose ``run_folder.parent`` is ``app_dir``."""
    run_folder = app_dir / "run-1"
    run_folder.mkdir(parents=True, exist_ok=True)
    return SkillContext(
        config=Config.default(),
        run_folder=run_folder,
        dossier_dict={},
        apk_path=str(app_dir / "fake.apk"),
    )


# ---------------------------------------------------------------------------
# Registration / catalog


def test_query_call_graph_in_llm_catalog():
    """The skill is advertised to the LLM with tier=llm and not as a consent-class skill."""
    metas = list_llm_skills()
    by_name = {m.name: m for m in metas}
    assert "query_call_graph" in by_name
    assert by_name["query_call_graph"].tier == "llm"
    assert by_name["query_call_graph"].requires_confirmation is False


def test_query_call_graph_not_in_exploit_catalog():
    """Read-only skills must never accidentally surface as exploit-tier."""
    names = {m.name for m in list_skills_by_tier("exploit")}
    assert "query_call_graph" not in names


# ---------------------------------------------------------------------------
# Input validation


def test_unknown_mode_returns_failure(tmp_path):
    ctx = _ctx_for(tmp_path / "apps" / "no-such-app")
    r = execute("query_call_graph", {"mode": "bogus"}, ctx)
    assert r.success is False
    assert "[query_call_graph]" in r.text
    assert "mode" in r.text.lower()


def test_missing_mode_returns_failure(tmp_path):
    ctx = _ctx_for(tmp_path / "apps" / "no-such-app")
    r = execute("query_call_graph", {}, ctx)
    assert r.success is False
    assert "mode" in r.text.lower()


def test_neighbors_requires_node_ref(tmp_path):
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute("query_call_graph", {"mode": "neighbors"}, _ctx_for(app_dir))
    assert r.success is False
    assert "node_ref" in r.text


def test_paths_requires_source_and_target(tmp_path):
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute("query_call_graph", {"mode": "paths"}, _ctx_for(app_dir))
    assert r.success is False
    assert "source" in r.text and "target" in r.text


# ---------------------------------------------------------------------------
# Fail-open paths (LLM should be able to read the empty result + retry)


def test_no_app_context_returns_fail_open(tmp_path):
    """Run folder whose resolved app dir has no decompile cache → success=True,
    empty data, clear text. Covers the broad "no usable graph here" path the
    LLM is most likely to hit on its first call against a fresh project."""
    bare = tmp_path / "isolated"
    bare.mkdir()
    ctx = SkillContext(
        config=Config.default(),
        run_folder=bare,
        dossier_dict={},
        apk_path=None,
    )
    r = execute("query_call_graph", {"mode": "overview"}, ctx)
    assert r.success is True
    assert r.data is None
    assert "[query_call_graph]" in r.text


def test_explicit_unknown_app_id_is_fail_open(tmp_path):
    """An ``app_id`` that doesn't exist on disk must NOT raise — fail-open
    with the dedicated 'no app directory' line so the LLM can pivot."""
    apps_root = tmp_path / "apps"
    apps_root.mkdir()
    real_app = apps_root / "real"
    real_app.mkdir()
    run_folder = real_app / "run-1"
    run_folder.mkdir()
    ctx = SkillContext(
        config=Config.default(),
        run_folder=run_folder,
        dossier_dict={},
        apk_path=None,
    )
    r = execute(
        "query_call_graph",
        {"mode": "overview", "app_id": "ghost"},
        ctx,
    )
    assert r.success is True
    assert r.data is None
    assert "[query_call_graph]" in r.text


def test_decompile_not_ready_is_fail_open(tmp_path):
    """An app dir with no decompile cache should fail open, not raise."""
    app_dir = tmp_path / "apps" / "fresh"
    app_dir.mkdir(parents=True)
    r = execute("query_call_graph", {"mode": "overview"}, _ctx_for(app_dir))
    assert r.success is True
    assert r.data is None
    assert "decompile" in r.text.lower()


def test_call_graph_not_ready_is_fail_open(tmp_path):
    """Decompile=ready but call graph never built → fail open."""
    app_dir = tmp_path / "apps" / "halfbuilt"
    app_dir.mkdir(parents=True)
    sha = "0" * 40
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK")
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": sha, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )
    dc.sources_dir(app_dir, sha).mkdir(parents=True)
    dc._write_index(
        app_dir, sha,
        {"status": "ready", "apk_path": str(apk), "apk_sha256": sha, "file_count": 0},
    )
    r = execute("query_call_graph", {"mode": "overview"}, _ctx_for(app_dir))
    assert r.success is True
    assert r.data is None
    assert "call graph" in r.text.lower()


# ---------------------------------------------------------------------------
# Mode dispatch — round-trips against the fixture call graph


def test_overview_returns_nodes_and_classes(tmp_path):
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute("query_call_graph", {"mode": "overview", "limit": 50}, _ctx_for(app_dir))
    assert r.success is True
    assert r.data is not None
    assert r.data["total_nodes"] > 0
    assert all(n["is_external"] is False for n in r.data["nodes"])
    assert "[query_call_graph][overview]" in r.text


def test_overview_with_package_prefix(tmp_path):
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "query_call_graph",
        {"mode": "overview", "package_prefix": "com.example", "limit": 500},
        _ctx_for(app_dir),
    )
    assert r.success is True
    names = {c["class_name"] for c in r.data["classes"]}
    assert "com.example.App" in names


def test_overview_clamps_limit_to_hard_cap(tmp_path):
    """An LLM that asks for ``limit=999999`` shouldn't blow up; clamp instead."""
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "query_call_graph",
        {"mode": "overview", "limit": 999_999},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert len(r.data["nodes"]) <= 5000


def test_neighbors_round_trip(tmp_path):
    app_dir = _seed_apps_with_graph(tmp_path)
    sig = "Lcom/example/App;->main()V"
    r = execute(
        "query_call_graph",
        {"mode": "neighbors", "node_ref": sig},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.data["node"]["smali_id"] == sig
    callee_sigs = {c["node"]["smali_id"] for c in r.data["callees"]}
    assert "Lcom/example/Dog;->speak()V" in callee_sigs
    assert "[query_call_graph][neighbors]" in r.text


def test_neighbors_unknown_node_returns_empty(tmp_path):
    """An unknown node_ref must NOT raise — empty payload + clear text."""
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "query_call_graph",
        {"mode": "neighbors", "node_ref": "Lcom/example/Nope;->x()V"},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.data["node"] is None
    assert r.data["callers"] == []
    assert r.data["callees"] == []


def test_paths_returns_at_least_one_path(tmp_path):
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "query_call_graph",
        {
            "mode": "paths",
            "source": "Lcom/example/App;->main()V",
            "target": "Lcom/example/Cat;->speak()V",
            "max_hops": 3,
            "max_paths": 5,
        },
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.data["paths"], "expected at least one path"
    assert "[query_call_graph][paths]" in r.text


def test_paths_clamps_max_hops(tmp_path):
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "query_call_graph",
        {
            "mode": "paths",
            "source": "Lcom/example/App;->main()V",
            "target": "Lcom/example/Dog;->speak()V",
            "max_hops": 999,
            "max_paths": 999,
        },
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.data["max_hops"] <= 12
    assert r.data["max_paths"] <= 50


def test_explicit_app_id_resolves_through_apps_root(tmp_path):
    """When the LLM passes an explicit app_id, the skill should use it
    instead of falling back to ``run_folder.parent``."""
    app_dir = _seed_apps_with_graph(tmp_path, app_id="targetapp")
    # Construct a context whose run_folder is in a *different* app.
    other_app = tmp_path / "apps" / "otherapp"
    other_app.mkdir(parents=True)
    other_run = other_app / "run-1"
    other_run.mkdir()
    ctx = SkillContext(
        config=Config.default(),
        run_folder=other_run,
        dossier_dict={},
        apk_path=None,
    )
    r = execute(
        "query_call_graph",
        {"mode": "overview", "app_id": "targetapp", "limit": 50},
        ctx,
    )
    assert r.success is True
    assert r.data is not None
    assert r.data["total_nodes"] > 0
