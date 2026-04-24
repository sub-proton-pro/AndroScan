"""Tests for Phase 6 RE Workbench (FastAPI)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from androscan.config import Config
from androscan.web.app import create_app


@pytest.fixture
def cfg() -> Config:
    return Config.default()


def test_api_health(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "apps_root" in body


def test_api_llm_info(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/llm/info")
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == cfg.ollama_model
    assert body["base_url"] == cfg.ollama_base_url
    assert body["provider"] == "ollama"


def test_api_projects_empty(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert r.json() == {"projects": []}


def test_api_projects_lists_dirs(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps" / "com_example_app").mkdir(parents=True)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert r.json() == {"projects": [{"app_id": "com_example_app"}]}


def test_api_runs(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "apps" / "myapp" / "01-jan-26_12-00-00"
    run_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text("{}", encoding="utf-8")
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/projects/myapp/runs")
    assert r.status_code == 200
    assert r.json()["runs"] == [{"run_timestamp": "01-jan-26_12-00-00"}]


def test_api_findings(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "apps" / "myapp" / "01-jan-26_12-00-00"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(json.dumps({"summary": "x", "hypotheses": []}), encoding="utf-8")
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/findings/myapp/01-jan-26_12-00-00")
    assert r.status_code == 200
    assert r.json()["report"]["summary"] == "x"


def test_api_dossier(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    app_root = tmp_path / "apps" / "myapp"
    app_root.mkdir(parents=True)
    (app_root / "app_meta.json").write_text(
        json.dumps({"apk_sha256": "abc", "dossier": {"apk_info": {"package": "com.example"}}}),
        encoding="utf-8",
    )
    run_dir = app_root / "01-jan-26_12-00-00"
    run_dir.mkdir()
    (run_dir / "run_meta.json").write_text("{}", encoding="utf-8")
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/dossier/myapp/01-jan-26_12-00-00")
    assert r.status_code == 200
    assert r.json()["dossier"]["apk_info"]["package"] == "com.example"


def test_api_runs_unknown_app(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/projects/does_not_exist/runs")
    assert r.status_code == 404


def test_api_triage_get_empty(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    run = tmp_path / "apps" / "myapp" / "01-jan-26_12-00-00"
    run.mkdir(parents=True)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/triage/myapp/01-jan-26_12-00-00")
    assert r.status_code == 200
    assert r.json()["triage"] == {}


def test_api_triage_post_then_get_round_trip(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    run = tmp_path / "apps" / "myapp" / "01-jan-26_12-00-00"
    run.mkdir(parents=True)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    p = client.post(
        "/api/triage/myapp/01-jan-26_12-00-00/finding-001",
        json={"status": "false_positive", "note": "internal-only id"},
    )
    assert p.status_code == 200, p.text
    assert p.json()["entry"]["status"] == "false_positive"

    g = client.get("/api/triage/myapp/01-jan-26_12-00-00")
    body = g.json()
    assert body["triage"]["finding-001"]["status"] == "false_positive"
    assert body["triage"]["finding-001"]["note"] == "internal-only id"


def test_api_triage_rejects_bad_status(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    run = tmp_path / "apps" / "myapp" / "01-jan-26_12-00-00"
    run.mkdir(parents=True)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/triage/myapp/01-jan-26_12-00-00/finding-001",
        json={"status": "lol"},
    )
    assert r.status_code == 400


def test_api_triage_post_without_finding_id_returns_400(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: empty finding ids used to redirect 307 -> POST -> GET-only
    route -> 405. We now respond with an explicit 400 so the bug is loud."""
    monkeypatch.chdir(tmp_path)
    run = tmp_path / "apps" / "myapp" / "01-jan-26_12-00-00"
    run.mkdir(parents=True)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/triage/myapp/01-jan-26_12-00-00",
        json={"status": "confirmed"},
    )
    assert r.status_code == 400, r.text
    assert "finding_id" in r.json()["detail"]


def test_api_chat_uses_mocked_llm(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    run = tmp_path / "apps" / "myapp" / "01-jan-26_12-00-00"
    run.mkdir(parents=True)

    class FakeResult:
        def __init__(self, content: str) -> None:
            self.content = content

    def fake_complete(**kwargs: object) -> FakeResult:
        # Confirm it was called as prose chat, not JSON-format analysis.
        assert kwargs.get("response_format") is None
        msgs = kwargs.get("messages") or []
        assert isinstance(msgs, list) and msgs and msgs[0]["role"] == "system"  # type: ignore[index]
        return FakeResult("hello from fake model")

    monkeypatch.setattr("androscan.llm.client.complete", fake_complete)

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/chat",
        json={
            "tab": "reports",
            "prompt": "summarise",
            "history": [],
            "attachments": [{"kind": "finding", "name": "finding-001", "text": "title=Foo"}],
            "app_id": "myapp",
            "run_ts": "01-jan-26_12-00-00",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["reply"] == "hello from fake model"
    transcript = run / "chat" / "reports.jsonl"
    assert transcript.is_file()


def test_api_chat_validates_tab(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post("/api/chat", json={"tab": "elsewhere", "prompt": "x"})
    assert r.status_code == 400
    assert "tab" in r.json()["error"]


def test_api_decompile_status_unknown_without_app_meta(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps" / "myapp").mkdir(parents=True)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/decompile/myapp")
    assert r.status_code == 200
    assert r.json()["status"] == "unknown"


def test_api_code_endpoints_after_blocking_decompile(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-populate the cache via blocking decompile, then hit /api/code/*."""
    from androscan.web import decompile_cache as dc

    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "myapp"
    app_dir.mkdir(parents=True)
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK")
    sha = "a" * 64
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": sha, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )

    def fake_run(cmd: str, p: Path, out: Path, timeout: int = 0) -> tuple[bool, str]:
        out.mkdir(parents=True, exist_ok=True)
        (out / "Foo.java").write_text(
            "package p;\npublic class Foo { public void onCreate() {} }\n",
            encoding="utf-8",
        )
        return True, ""

    monkeypatch.setattr(dc, "_run_jadx_bulk", fake_run)
    monkeypatch.setattr(dc.shutil, "which", lambda c: "/usr/bin/" + c)
    res = dc.start_decompile(app_dir, blocking=True)
    assert res["status"] == "ready", res

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)

    s = client.get("/api/decompile/myapp")
    assert s.status_code == 200
    assert s.json()["status"] == "ready"

    tree = client.get("/api/code/myapp/tree")
    assert tree.status_code == 200, tree.text
    assert any(c["name"] == "Foo" for p in tree.json()["tree"]["packages"] for c in p["classes"])

    f = client.get("/api/code/myapp/file", params={"path": "Foo.java"})
    assert f.status_code == 200
    assert "class Foo" in f.json()["text"]

    bad = client.get("/api/code/myapp/file", params={"path": "../../etc/passwd"})
    assert bad.status_code == 404


def test_api_code_tree_409_when_not_built(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps" / "myapp").mkdir(parents=True)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/code/myapp/tree")
    assert r.status_code == 409


def test_api_inspect_map_returns_element_and_candidates(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end click-to-map with adb mocked + decompile cache pre-populated."""
    from androscan.web import decompile_cache as dc
    from androscan.web import inspect_map as im

    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "myapp"
    app_dir.mkdir(parents=True)
    sha = "b" * 64
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK")
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": sha, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )
    src = dc.sources_dir(app_dir, sha)
    (src / "com" / "example" / "app").mkdir(parents=True)
    (src / "com" / "example" / "app" / "Main.java").write_text(
        "package com.example.app;\nclass Main {\n  void onCreate() { findViewById(R.id.btn_login); }\n}\n",
        encoding="utf-8",
    )
    dc._write_index(app_dir, sha, {"status": "ready", "apk_path": str(apk), "file_count": 1})

    ui_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
<hierarchy>
  <node bounds="[0,0][1080,1920]" class="L" package="com.example.app" clickable="false" enabled="true" resource-id="" text="" content-desc="">
    <node bounds="[600,250][900,350]" class="B" package="com.example.app" clickable="true" enabled="true" resource-id="com.example.app:id/btn_login" text="" content-desc=""/>
  </node>
</hierarchy>
"""

    async def fake_run(*args):
        if args[0] == "shell":
            return 0, b"ACTIVITY com.example.app/.Main 1\n", b""
        return 0, ui_xml.encode(), b""

    monkeypatch.setattr(im, "_run_adb", fake_run)

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post("/api/inspect/map", json={"app_id": "myapp", "x": 700, "y": 300})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["short_resource_id"] == "btn_login"
    assert any(c["kind"] == "findViewById" for c in body["candidates"])
    assert body["decompile_status"] == "ready"
    # The fused resolver block lifts the deterministic findViewById hit into
    # a single ``best`` answer with reasoning.
    assert "resolution" in body
    assert body["resolution"]["best"] is not None
    assert body["resolution"]["best"]["kind"] == "findViewById"
    assert body["resolution"]["best"]["file"].endswith("Main.java")
    assert isinstance(body["resolution"]["alternatives"], list)


def test_api_device_status_offline_when_adb_missing(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()

    async def boom(*a, **kw):
        raise FileNotFoundError("no adb")

    monkeypatch.setattr("androscan.web.app.asyncio.create_subprocess_exec", boom)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/device/status")
    assert r.status_code == 200
    body = r.json()
    assert body["online"] is False
    assert body["state"] == "no_adb"


def test_api_device_status_online(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()

    class Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"device\n", b""

        def kill(self) -> None:
            pass

    async def fake_exec(*_a, **_kw):
        return Proc()

    monkeypatch.setattr("androscan.web.app.asyncio.create_subprocess_exec", fake_exec)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/device/status")
    assert r.status_code == 200
    body = r.json()
    assert body["online"] is True
    assert body["state"] == "device"


def test_ws_mirror_first_frame(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror loop uses adb; mock subprocess to return a minimal PNG."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40

    async def fake_exec(*_a: object, **_kw: object):
        class Proc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return png, b""

        return Proc()

    monkeypatch.setattr("androscan.web.app.asyncio.create_subprocess_exec", fake_exec)
    monkeypatch.setattr("androscan.web.app.asyncio.sleep", asyncio.sleep)

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    with client.websocket_connect("/ws/mirror") as ws:
        data = ws.receive_bytes()
        assert data.startswith(b"\x89PNG")


def test_ws_logcat_connect_disconnect(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Logcat stream: mock process with one line then EOF."""

    class FakeStdout:
        def __init__(self) -> None:
            self._n = 0

        async def readline(self) -> bytes:
            self._n += 1
            if self._n == 1:
                return b"01-01 12:00:00.000  I test: hello\n"
            return b""

    class LogcatProc:
        returncode: int | None = None

        def __init__(self) -> None:
            self.stdout = FakeStdout()

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            if self.returncode is None:
                self.returncode = 0
            return int(self.returncode)

    class EmptyProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_exec_logcat(*args: object, **_kw: object) -> object:
        if "logcat" in args:
            return LogcatProc()
        return EmptyProc()

    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    monkeypatch.setattr("androscan.web.app.asyncio.create_subprocess_exec", fake_exec_logcat)

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    with client.websocket_connect("/ws/logcat") as ws:
        msg = ws.receive_text()
        assert "hello" in msg


def test_api_adb_shell_runs_argv(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: argv is parsed and stdout/exit_code returned."""

    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"package:com.foo\npackage:com.bar\n", b""

    async def fake_exec(*args: object, **_kw: object) -> object:
        captured["args"] = args
        return FakeProc()

    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    monkeypatch.setattr("androscan.web.app.asyncio.create_subprocess_exec", fake_exec)

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post("/api/adb/shell", json={"command": "pm list packages -3"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exit_code"] == 0
    assert "com.foo" in body["stdout"]
    assert body["argv"] == ["pm", "list", "packages", "-3"]
    args = captured.get("args")
    assert isinstance(args, tuple)
    # First three positional args must be ('adb', 'shell', 'pm', ...).
    assert args[:3] == ("adb", "shell", "pm")


def test_api_adb_shell_blocks_irreversible_commands(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reboot / wipe / fastboot etc. are denied with HTTP 400 before exec."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()

    def explode(*_a: object, **_kw: object) -> None:
        raise AssertionError("subprocess must not be spawned for denied commands")

    monkeypatch.setattr("androscan.web.app.asyncio.create_subprocess_exec", explode)

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    for cmd in ("reboot", "su -c reboot recovery", "wipe -p data", "fastboot devices"):
        r = client.post("/api/adb/shell", json={"command": cmd})
        assert r.status_code == 400, (cmd, r.text)
        assert "blocked" in r.json()["detail"].lower()


def test_api_adb_shell_rejects_empty_command(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post("/api/adb/shell", json={"command": "   "})
    assert r.status_code == 400
    assert r.json()["detail"] == "empty command"


# ---------------------------------------------------------------------------
# RAG endpoints (Lane-1)
#
# Tests configure ANDROSCAN_RAG_PROVIDER=hash so we exercise the full
# build/query path without any optional ML deps. The hash provider is
# only enabled when ANDROSCAN_RAG_ALLOW_HASH=1 — this gate is what
# keeps it out of production by default.


def _seed_decompile_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Pre-populate apps/myapp with a finished decompile cache. Returns sha."""
    from androscan.web import decompile_cache as dc

    app_dir = tmp_path / "apps" / "myapp"
    app_dir.mkdir(parents=True)
    sha = "c" * 64
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
        "  public void onCreate() {\n"
        "    String pwd = etPassword.getText().toString();\n"
        "  }\n"
        "  public boolean checkPassword(String s) {\n"
        '    return s.equals("hunter2hunter2");\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    dc._write_index(app_dir, sha, {"status": "ready", "apk_path": str(apk), "file_count": 1})
    return sha


def test_api_rag_status_missing_when_no_decompile(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps" / "myapp").mkdir(parents=True)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/rag/myapp/status")
    assert r.status_code == 200
    body = r.json()
    assert body["rag"]["status"] == "missing"


def test_api_rag_query_409_when_decompile_not_ready(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps" / "myapp").mkdir(parents=True)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post("/api/rag/myapp/query", json={"text": "anything"})
    assert r.status_code == 409


def test_api_rag_rebuild_then_query(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANDROSCAN_RAG_PROVIDER", "hash")
    monkeypatch.setenv("ANDROSCAN_RAG_ALLOW_HASH", "1")
    monkeypatch.chdir(tmp_path)
    sha = _seed_decompile_cache(tmp_path, monkeypatch)

    # Force synchronous build so the test doesn't depend on daemon-thread
    # scheduling under pytest. Production still uses the async path.
    from androscan.rag import index as rag_index

    def sync_build_async(cache_dir, sources_root, sha, provider_factory, **_kw):
        provider = provider_factory()
        rag_index.build_index(cache_dir, sources_root, sha, provider)
        return {"status": "ready", "sha": sha}

    monkeypatch.setattr("androscan.rag.index.start_build_async", sync_build_async)

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)

    r = client.post("/api/rag/myapp/rebuild")
    assert r.status_code == 200, r.text
    assert r.json()["sha"] == sha

    s = client.get("/api/rag/myapp/status").json()
    assert s["rag"]["status"] == "ready", s

    q = client.post("/api/rag/myapp/query", json={"text": "password equals hunter2", "top_k": 3})
    assert q.status_code == 200, q.text
    body = q.json()
    assert body["provider"]["name"] == "hash"
    assert body["hits"]
    assert any("LoginActivity" in h["file"] for h in body["hits"])
