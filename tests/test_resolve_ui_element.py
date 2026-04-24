"""Tests for the resolve_ui_element skill (deterministic fuser + RAG enrichment)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from androscan.config import Config
from androscan.rag.embed import HashProvider
from androscan.rag.index import build_index
from androscan.skills import discover, execute as skill_execute
from androscan.skills.base import SkillContext
from androscan.skills.resolve_ui_element import (
    _build_rag_query,
    _early_line_bonus,
    _file_simple_name,
    _foreground_simple_name,
    resolve,
)
from androscan.web import decompile_cache as dc


# ---------------------------------------------------------------------------
# Pure helpers


def test_foreground_simple_name_strips_dot_and_package() -> None:
    assert _foreground_simple_name("com.example.app/.MainActivity") == "MainActivity"
    assert _foreground_simple_name("com.example.app/com.example.app.LoginActivity") == "LoginActivity"
    assert _foreground_simple_name(None) is None
    assert _foreground_simple_name("") is None


def test_file_simple_name_strips_extension() -> None:
    assert _file_simple_name("com/example/app/MainActivity.java") == "MainActivity"
    assert _file_simple_name("foo/Bar.kt") == "Bar"
    assert _file_simple_name("README") == "README"


def test_early_line_bonus_decays() -> None:
    assert _early_line_bonus(1) > _early_line_bonus(50) > _early_line_bonus(199) >= 0
    assert _early_line_bonus(200) == 0.0
    assert _early_line_bonus(0) == 0.0


def test_build_rag_query_prefers_visible_text() -> None:
    q = _build_rag_query({"text": "Login", "content_desc": "submit", "resource_id": "app:id/btn_login"})
    assert q is not None
    assert "Login" in q
    assert "btn_login" in q
    # Empty element yields no query (so RAG isn't asked random nonsense).
    assert _build_rag_query(None) is None
    assert _build_rag_query({}) is None
    assert _build_rag_query({"resource_id": "", "text": "", "content_desc": ""}) is None


# ---------------------------------------------------------------------------
# Pure-function resolve() — no RAG path


def _candidate(file: str, line: int, kind: str, snippet: str = "x") -> dict:
    return {"file": file, "line": line, "kind": kind, "snippet": snippet}


def test_resolve_returns_none_with_empty_inputs() -> None:
    out = resolve(element=None, foreground_activity=None, candidates=[])
    assert out["best"] is None
    assert out["alternatives"] == []
    assert "No deterministic" in out["text"]


def test_findViewById_beats_reference() -> None:
    out = resolve(
        element={"resource_id": "app:id/btn_login"},
        foreground_activity=None,
        candidates=[
            _candidate("Foo.java", 5, "reference"),
            _candidate("Bar.java", 12, "findViewById"),
        ],
    )
    assert out["best"]["file"] == "Bar.java"
    assert out["best"]["kind"] == "findViewById"
    assert out["alternatives"][0]["file"] == "Foo.java"


def test_foreground_activity_boost_overrides_kind() -> None:
    """A 'reference' inside the foreground activity should beat a 'findViewById'
    in some random other file because the +0.50 foreground bonus exceeds the
    findViewById vs reference base gap (1.00 - 0.20 = 0.80? — no, kind wins
    here). Use 'onClick_near' (0.80) so the foreground bonus tips it."""
    out = resolve(
        element={"resource_id": "app:id/x"},
        foreground_activity="com.example.app/.LoginActivity",
        candidates=[
            _candidate("LoginActivity.java", 100, "onClick_near"),
            _candidate("OtherClass.java", 5, "findViewById"),
        ],
    )
    # LoginActivity (onClick_near 0.80 + foreground 0.50 + activity-named 0.10)
    # = 1.40 > OtherClass (findViewById 1.00 + early-line bonus ~0.10) = 1.10.
    assert out["best"]["file"] == "LoginActivity.java"
    assert any("foreground match" in r for r in out["best"]["reasons"])


def test_early_line_bonus_breaks_kind_tie() -> None:
    """Two findViewById candidates: the earlier line wins via the early-line bonus."""
    out = resolve(
        element={"resource_id": "app:id/x"},
        foreground_activity=None,
        candidates=[
            _candidate("A.java", 150, "findViewById"),
            _candidate("B.java", 5, "findViewById"),
        ],
    )
    assert out["best"]["file"] == "B.java"


def test_resolve_handles_non_dict_candidates_gracefully() -> None:
    out = resolve(
        element=None,
        foreground_activity=None,
        candidates=["garbage", None, _candidate("Ok.java", 1, "reference")],  # type: ignore[list-item]
    )
    assert out["best"]["file"] == "Ok.java"


def test_alternatives_capped_at_five() -> None:
    cands = [_candidate(f"C{i}.java", 1, "reference") for i in range(20)]
    out = resolve(element=None, foreground_activity=None, candidates=cands)
    assert out["best"] is not None
    assert len(out["alternatives"]) == 5


# ---------------------------------------------------------------------------
# RAG enrichment — uses the real Lane-1 index with HashProvider for hermeticity


def _seed_app_with_rag(tmp_path: Path) -> Path:
    app_dir = tmp_path / "apps" / "myapp"
    app_dir.mkdir(parents=True)
    sha = "a" * 64
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK")
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": sha, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )
    src = dc.sources_dir(app_dir, sha)
    (src / "com" / "example").mkdir(parents=True)
    (src / "com" / "example" / "LoginActivity.java").write_text(
        "package com.example;\n"
        "public class LoginActivity {\n"
        "  public void onCreate() {}\n"
        "  public boolean checkPassword(String s) { return s.equals(\"hunter2\"); }\n"
        "}\n",
        encoding="utf-8",
    )
    dc._write_index(app_dir, sha, {"status": "ready", "apk_path": str(apk), "file_count": 1})
    cache_dir = dc.cache_root_for(app_dir, sha)
    build_index(cache_dir, src, sha, provider=HashProvider())
    return app_dir


def test_resolve_with_rag_enrichment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANDROSCAN_RAG_PROVIDER", "hash")
    monkeypatch.setenv("ANDROSCAN_RAG_ALLOW_HASH", "1")
    app_dir = _seed_app_with_rag(tmp_path)

    out = resolve(
        element={"resource_id": "app:id/btn_login", "text": "Login"},
        foreground_activity="com.example/.LoginActivity",
        candidates=[],
        config=Config.default(),
        app_dir=app_dir,
    )
    # No deterministic candidates, but RAG should pull at least one chunk.
    assert out["rag_query"] is not None
    assert out["rag_hits"], out["text"]
    assert out["best"] is not None
    assert out["best"]["source"] == "rag"


def test_resolve_failsoft_when_no_rag_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No app dir / no decompile cache → still returns a clean structure."""
    monkeypatch.setenv("ANDROSCAN_RAG_PROVIDER", "hash")
    monkeypatch.setenv("ANDROSCAN_RAG_ALLOW_HASH", "1")
    out = resolve(
        element={"resource_id": "app:id/btn", "text": "Click"},
        foreground_activity=None,
        candidates=[],
        config=Config.default(),
        app_dir=None,
    )
    # rag_error should explain why no hits, and we should still return a
    # well-formed dict with no best.
    assert out["best"] is None
    assert out["rag_hits"] == []
    assert out["rag_error"]


# ---------------------------------------------------------------------------
# Skill registry path (LLM-tier execution)


def test_skill_execute_via_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The skill is discoverable + advertised in the LLM catalog."""
    monkeypatch.setenv("ANDROSCAN_RAG_PROVIDER", "hash")
    monkeypatch.setenv("ANDROSCAN_RAG_ALLOW_HASH", "1")
    monkeypatch.chdir(tmp_path)
    app_dir = _seed_app_with_rag(tmp_path)
    run_dir = app_dir / "01-jan-26_12-00-00"
    run_dir.mkdir()
    (run_dir / "run_meta.json").write_text("{}", encoding="utf-8")

    discover()
    from androscan.skills import list_llm_skills

    assert any(m.name == "resolve_ui_element" for m in list_llm_skills())

    ctx = SkillContext(config=Config.default(), run_folder=run_dir, dossier_dict={})
    res = skill_execute(
        "resolve_ui_element",
        {
            "element": {"resource_id": "app:id/btn_login", "text": "Login"},
            "foreground_activity": "com.example/.LoginActivity",
            "candidates": [
                {"file": "com/example/LoginActivity.java", "line": 3, "kind": "findViewById", "snippet": "..."}
            ],
            "app_id": "myapp",
        },
        ctx,
    )
    assert res.success, res.text
    assert res.data["best"] is not None
    assert res.data["best"]["file"].endswith("LoginActivity.java")
    assert "Best handler" in res.text
