"""Tests for the "Bring device online" wizard backend.

Covers:

* :func:`androscan.web.device_ops.list_avds` (binary missing / parse).
* :func:`androscan.web.device_ops.spawn_emulator_detached` (happy path).
* :func:`androscan.web.device_ops.adb_install_apk` (success + non-zero rc).
* :func:`androscan.web.device_ops.adb_launch_package` (monkey aborted).
* The three new FastAPI routes wired in :mod:`androscan.web.app`.

Subprocesses are always mocked — these tests must run on machines without
``adb`` / ``emulator`` installed (CI).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from androscan.config import Config
from androscan.web import device_ops
from androscan.web.app import create_app


# ---------------------------------------------------------------------------
# helpers


class _StubProc:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.pid = 4242

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        pass


def _make_exec_stub(scenarios: dict[tuple[str, ...], _StubProc]):
    """Build an async stub for ``asyncio.create_subprocess_exec``.

    Looks up ``scenarios`` by the leading argv tokens; the first matching
    prefix wins (so ``("adb", "install")`` matches both ``("adb", "install",
    "-r", path)`` regardless of the APK path). Falls back to a generic
    ``rc=0`` proc so unrelated calls (e.g. status probes) don't blow up.
    """

    async def fake_exec(*args: object, **_kw: object) -> _StubProc:
        argv = tuple(str(a) for a in args)
        for prefix, proc in scenarios.items():
            if argv[: len(prefix)] == prefix:
                return proc
        return _StubProc(0)

    return fake_exec


# ---------------------------------------------------------------------------
# device_ops.list_avds


def test_list_avds_no_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_ops, "find_emulator_binary", lambda: None)
    res = asyncio.run(device_ops.list_avds())
    assert res["ok"] is False
    assert res["avds"] == []
    assert "ANDROID_HOME" in (res["error"] or "")


def test_list_avds_parses_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_ops, "find_emulator_binary", lambda: "/fake/emulator")
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _make_exec_stub({("/fake/emulator",): _StubProc(0, b"Pixel_7_API_34\nMedium_Tablet\n")}),
    )
    res = asyncio.run(device_ops.list_avds())
    assert res["ok"] is True
    assert res["avds"] == ["Pixel_7_API_34", "Medium_Tablet"]
    assert res["emulator_path"] == "/fake/emulator"


def test_list_avds_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_ops, "find_emulator_binary", lambda: "/fake/emulator")
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _make_exec_stub({("/fake/emulator",): _StubProc(0, b"")}),
    )
    res = asyncio.run(device_ops.list_avds())
    assert res["ok"] is False
    assert res["avds"] == []
    assert "No AVDs" in (res["error"] or "")


# ---------------------------------------------------------------------------
# device_ops.spawn_emulator_detached


def test_spawn_emulator_requires_avd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_ops, "find_emulator_binary", lambda: "/fake/emulator")
    res = asyncio.run(device_ops.spawn_emulator_detached(""))
    assert res["ok"] is False
    assert "avd name" in (res["error"] or "").lower()


def test_spawn_emulator_returns_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_ops, "find_emulator_binary", lambda: "/fake/emulator")
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _make_exec_stub({("/fake/emulator", "-avd", "Pixel_7_API_34"): _StubProc(0)}),
    )
    res = asyncio.run(
        device_ops.spawn_emulator_detached("Pixel_7_API_34")
    )
    assert res["ok"] is True
    assert res["pid"] == 4242
    assert res["avd"] == "Pixel_7_API_34"


# ---------------------------------------------------------------------------
# device_ops.adb_install_apk + adb_launch_package


def test_adb_install_apk_missing_file(tmp_path: Path) -> None:
    res = asyncio.run(
        device_ops.adb_install_apk(str(tmp_path / "nope.apk"))
    )
    assert res["ok"] is False
    assert "not found" in (res["error"] or "").lower()


def test_adb_install_apk_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"PK\x03\x04dummy")
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _make_exec_stub({("adb", "install"): _StubProc(0, b"Success\n")}),
    )
    res = asyncio.run(device_ops.adb_install_apk(str(apk)))
    assert res["ok"] is True
    assert res["exit_code"] == 0


def test_adb_install_apk_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"PK\x03\x04dummy")
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _make_exec_stub({
            ("adb", "install"): _StubProc(1, b"", b"Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]"),
        }),
    )
    res = asyncio.run(device_ops.adb_install_apk(str(apk)))
    assert res["ok"] is False
    assert "INSUFFICIENT_STORAGE" in (res["error"] or "")


def test_adb_launch_package_aborted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _make_exec_stub({
            ("adb", "shell", "monkey"): _StubProc(0, b"** Monkey aborted due to error.\n"),
        }),
    )
    res = asyncio.run(device_ops.adb_launch_package("com.example"))
    assert res["ok"] is False
    assert "aborted" in (res["error"] or "").lower()


def test_adb_launch_package_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _make_exec_stub({
            ("adb", "shell", "monkey"): _StubProc(0, b"Events injected: 1\n"),
        }),
    )
    res = asyncio.run(device_ops.adb_launch_package("com.example"))
    assert res["ok"] is True


# ---------------------------------------------------------------------------
# Route-level tests


@pytest.fixture
def cfg() -> Config:
    return Config.default()


def _write_meta(app_root: Path, *, package: str, apk_path: str | None) -> None:
    payload: dict[str, Any] = {
        "apk_sha256": "deadbeef",
        "dossier": {"apk_info": {"package": package}},
    }
    if apk_path is not None:
        payload["apk_path"] = apk_path
    (app_root / "app_meta.json").write_text(json.dumps(payload), encoding="utf-8")


def test_route_avds_returns_inventory(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()

    async def fake_list_avds() -> dict[str, Any]:
        return {"ok": True, "emulator_path": "/x/emulator", "avds": ["A", "B"], "error": None}

    monkeypatch.setattr("androscan.web.app.list_avds", fake_list_avds)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.get("/api/device/avds")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["avds"] == ["A", "B"]


def test_route_emulator_start_uses_first_avd_when_omitted(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()

    async def fake_list_avds() -> dict[str, Any]:
        return {"ok": True, "emulator_path": "/x/emulator", "avds": ["Default"], "error": None}

    captured: dict[str, str] = {}

    async def fake_spawn(avd: str) -> dict[str, Any]:
        captured["avd"] = avd
        return {"ok": True, "emulator_path": "/x/emulator", "avd": avd, "pid": 1, "error": None}

    monkeypatch.setattr("androscan.web.app.list_avds", fake_list_avds)
    monkeypatch.setattr("androscan.web.app.spawn_emulator_detached", fake_spawn)

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post("/api/device/emulator/start", json={})
    assert r.status_code == 200, r.text
    assert captured["avd"] == "Default"
    assert r.json()["avd"] == "Default"


def test_route_emulator_start_503_when_no_avds(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()

    async def fake_list_avds() -> dict[str, Any]:
        return {"ok": False, "emulator_path": None, "avds": [], "error": "no AVDs"}

    monkeypatch.setattr("androscan.web.app.list_avds", fake_list_avds)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post("/api/device/emulator/start", json={})
    assert r.status_code == 503
    assert "no AVDs" in r.json()["detail"]


def test_route_install_and_launch_already_installed(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path with the app already installed: skip install, do launch."""
    monkeypatch.chdir(tmp_path)
    app_root = tmp_path / "apps" / "com_example_app"
    app_root.mkdir(parents=True)
    _write_meta(app_root, package="com.example.app", apk_path=str(tmp_path / "app.apk"))

    async def fake_pm_path(pkg: str, **_kw: Any) -> dict[str, Any]:
        return {"installed": True, "apk_path_on_device": "/data/app/x.apk", "error": None}

    async def fake_install(*_a: Any, **_kw: Any) -> dict[str, Any]:  # pragma: no cover - shouldn't run
        raise AssertionError("install should be skipped when already installed")

    async def fake_launch(pkg: str, **_kw: Any) -> dict[str, Any]:
        return {"ok": True, "exit_code": 0, "stdout": "Events injected: 1", "stderr": "", "error": None}

    monkeypatch.setattr("androscan.web.app.adb_pm_path", fake_pm_path)
    monkeypatch.setattr("androscan.web.app.adb_install_apk", fake_install)
    monkeypatch.setattr("androscan.web.app.adb_launch_package", fake_launch)

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post("/api/device/install_and_launch", json={"app_id": "com_example_app"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["package"] == "com.example.app"
    keys = [s["key"] for s in body["steps"]]
    assert keys == ["check_installed", "install", "launch"]
    assert body["steps"][1].get("skipped") is True


def test_route_install_and_launch_installs_then_launches(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: not installed → install succeeds → launch succeeds."""
    monkeypatch.chdir(tmp_path)
    app_root = tmp_path / "apps" / "com_example_app"
    app_root.mkdir(parents=True)
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"PK\x03\x04")
    _write_meta(app_root, package="com.example.app", apk_path=str(apk))

    async def fake_pm_path(pkg: str, **_kw: Any) -> dict[str, Any]:
        return {"installed": False, "apk_path_on_device": None, "error": None}

    async def fake_install(path: str, **_kw: Any) -> dict[str, Any]:
        assert path == str(apk)
        return {"ok": True, "exit_code": 0, "stdout": "Success", "stderr": "", "error": None}

    async def fake_launch(pkg: str, **_kw: Any) -> dict[str, Any]:
        return {"ok": True, "exit_code": 0, "stdout": "Events injected: 1", "stderr": "", "error": None}

    monkeypatch.setattr("androscan.web.app.adb_pm_path", fake_pm_path)
    monkeypatch.setattr("androscan.web.app.adb_install_apk", fake_install)
    monkeypatch.setattr("androscan.web.app.adb_launch_package", fake_launch)

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post("/api/device/install_and_launch", json={"app_id": "com_example_app"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    steps = {s["key"]: s for s in body["steps"]}
    assert steps["install"]["ok"] is True
    assert steps["install"].get("skipped") is not True
    assert steps["launch"]["ok"] is True


def test_route_install_and_launch_missing_apk_path(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not installed + no apk_path = install step fails fast with a clear error."""
    monkeypatch.chdir(tmp_path)
    app_root = tmp_path / "apps" / "com_example_app"
    app_root.mkdir(parents=True)
    _write_meta(app_root, package="com.example.app", apk_path=None)

    async def fake_pm_path(pkg: str, **_kw: Any) -> dict[str, Any]:
        return {"installed": False, "apk_path_on_device": None, "error": None}

    monkeypatch.setattr("androscan.web.app.adb_pm_path", fake_pm_path)
    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post("/api/device/install_and_launch", json={"app_id": "com_example_app"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    install_step = next(s for s in body["steps"] if s["key"] == "install")
    assert install_step["ok"] is False
    assert "apk_path" in (install_step["error"] or "")


def test_route_install_and_launch_409_without_package(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app_root = tmp_path / "apps" / "com_example_app"
    app_root.mkdir(parents=True)
    # app_meta.json with no package at all.
    (app_root / "app_meta.json").write_text(json.dumps({"apk_sha256": "x", "dossier": {}}), encoding="utf-8")

    app = create_app(cfg, cwd=tmp_path)
    client = TestClient(app)
    r = client.post("/api/device/install_and_launch", json={"app_id": "com_example_app"})
    assert r.status_code == 409
    assert "package" in r.json()["detail"].lower()
