"""Integration tests: chat inspect tab pulls RAG chunks; skill returns hits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from androscan.config import Config
from androscan.rag.embed import HashProvider
from androscan.rag.index import build_index
from androscan.skills import discover, execute as skill_execute
from androscan.skills.base import SkillContext
from androscan.web import chat as chat_module
from androscan.web import decompile_cache as dc


def _seed_app(tmp_path: Path) -> tuple[Path, str]:
    """Mirror of test_web._seed_decompile_cache for re-use here."""
    app_dir = tmp_path / "apps" / "myapp"
    app_dir.mkdir(parents=True)
    sha = "d" * 64
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK")
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": sha, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )
    src = dc.sources_dir(app_dir, sha)
    (src / "com" / "example" / "weakbank").mkdir(parents=True)
    (src / "com" / "example" / "weakbank" / "LoginActivity.java").write_text(
        "package com.example.weakbank;\n"
        "public class LoginActivity {\n"
        "  public void onCreate() { String pwd = etPassword.getText().toString(); }\n"
        "  public boolean checkPassword(String s) { return s.equals(\"hunter2hunter2\"); }\n"
        "}\n",
        encoding="utf-8",
    )
    dc._write_index(app_dir, sha, {"status": "ready", "apk_path": str(apk), "file_count": 1})
    # Build index synchronously with HashProvider so the test is hermetic.
    cache_dir = dc.cache_root_for(app_dir, sha)
    build_index(cache_dir, src, sha, provider=HashProvider())
    return app_dir, sha


def test_inspect_chat_appends_rag_attachments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANDROSCAN_RAG_PROVIDER", "hash")
    monkeypatch.setenv("ANDROSCAN_RAG_ALLOW_HASH", "1")
    monkeypatch.chdir(tmp_path)
    _seed_app(tmp_path)

    captured: dict[str, list] = {}

    class FakeResult:
        def __init__(self, content: str) -> None:
            self.content = content

    def fake_complete(**kwargs):
        captured["messages"] = kwargs.get("messages") or []
        return FakeResult("ok")

    monkeypatch.setattr("androscan.llm.client.complete", fake_complete)

    body = {
        "tab": "inspect",
        "prompt": "where is the password verified?",
        "history": [],
        "attachments": [{"kind": "default", "name": "selection", "text": "app_id: myapp"}],
        "app_id": "myapp",
    }
    status, resp = chat_module.handle_chat_request(body, Config.default(), tmp_path / "apps")
    assert status == 200, resp
    msgs = captured["messages"]
    assert msgs and msgs[-1]["role"] == "user"
    user_text = msgs[-1]["content"]
    # RAG enrichment should have inlined a code chunk from LoginActivity.
    assert "LoginActivity" in user_text


def test_inspect_chat_enrichment_is_failsoft_without_rag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No decompile cache -> chat must still succeed without RAG attachments."""
    monkeypatch.setenv("ANDROSCAN_RAG_PROVIDER", "hash")
    monkeypatch.setenv("ANDROSCAN_RAG_ALLOW_HASH", "1")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps" / "myapp").mkdir(parents=True)

    class FakeResult:
        content = "ok"

    monkeypatch.setattr("androscan.llm.client.complete", lambda **k: FakeResult())

    body = {
        "tab": "inspect",
        "prompt": "anything",
        "history": [],
        "attachments": [],
        "app_id": "myapp",
    }
    status, resp = chat_module.handle_chat_request(body, Config.default(), tmp_path / "apps")
    assert status == 200, resp


def test_search_decompiled_sources_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANDROSCAN_RAG_PROVIDER", "hash")
    monkeypatch.setenv("ANDROSCAN_RAG_ALLOW_HASH", "1")
    monkeypatch.chdir(tmp_path)
    app_dir, sha = _seed_app(tmp_path)
    run_dir = app_dir / "01-jan-26_12-00-00"
    run_dir.mkdir()
    (run_dir / "run_meta.json").write_text("{}", encoding="utf-8")

    discover()
    ctx = SkillContext(config=Config.default(), run_folder=run_dir, dossier_dict={})
    res = skill_execute(
        "search_decompiled_sources",
        {"query": "password verification", "top_k": 3},
        ctx,
    )
    assert res.success, res.text
    assert res.data, res.text
    assert any("LoginActivity" in d["file"] for d in res.data), res.text


def test_search_decompiled_sources_requires_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    discover()
    ctx = SkillContext(config=Config.default(), run_folder=tmp_path)
    res = skill_execute("search_decompiled_sources", {}, ctx)
    assert not res.success
    assert "required" in res.text.lower()
