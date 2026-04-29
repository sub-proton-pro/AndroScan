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


# ---------------------------------------------------------------------------
# Launcher-activity resolution + parser unit tests
#
# These exercise the small pure-ish helpers that ``adb_launch_package``
# composes — keeping them under their own coverage means a parser
# regression points at one assertion instead of an integration mystery.


# Canonical ``cmd package resolve-activity --brief`` output on Android 9+.
_RESOLVE_ACTIVITY_BRIEF = (
    b"priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n"
    b"com.example.weakbank.low/.MainActivity\n"
)

# Canonical ``am start -W`` success output. The ``Status: ok`` line is
# the structured success marker that replaced our brittle monkey-output
# heuristics.
_AM_START_SUCCESS = (
    b"Starting: Intent { act=android.intent.action.MAIN "
    b"cat=[android.intent.category.LAUNCHER] "
    b"cmp=com.example.weakbank.low/.MainActivity }\n"
    b"Status: ok\n"
    b"LaunchState: COLD\n"
    b"Activity: com.example.weakbank.low/.MainActivity\n"
    b"TotalTime: 678\n"
    b"WaitTime: 712\n"
    b"Complete\n"
)

# ``am start -W`` output when the cached activity component no longer
# exists (e.g. the developer renamed MainActivity in a reinstall). This
# is the trigger for the cache-invalidation + re-resolve fallback.
_AM_START_DOES_NOT_EXIST = (
    b"Starting: Intent { ... }\n"
    b"Error type 3\n"
    b"Error: Activity class {com.example.weakbank.low/.MainActivity} does not exist.\n"
)


def test_resolve_launcher_activity_parses_brief_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--brief`` output has a header line + the component on the next.
    The parser must pick the component, not the header."""
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _make_exec_stub({
            ("adb", "shell", "cmd", "package", "resolve-activity",
             "--brief", "com.example.weakbank.low"):
                _StubProc(0, _RESOLVE_ACTIVITY_BRIEF),
        }),
    )
    res = asyncio.run(
        device_ops._resolve_launcher_activity("com.example.weakbank.low")
    )
    assert res["ok"] is True
    assert res["activity"] == "com.example.weakbank.low/.MainActivity"


def test_resolve_launcher_activity_handles_missing_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the brief output has no component line (apps with no LAUNCHER
    intent — services, IMEs) the helper reports an error so the caller
    can fall back to monkey rather than passing garbage to am start."""
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _make_exec_stub({
            ("adb", "shell", "cmd", "package", "resolve-activity"):
                _StubProc(0, b"No activity found\n"),
        }),
    )
    res = asyncio.run(device_ops._resolve_launcher_activity("com.no.launcher"))
    assert res["ok"] is False
    assert res["activity"] is None


def test_parse_am_start_output_success() -> None:
    """Structured parse of the canonical ``am start -W`` success output —
    ``Status: ok``, ``LaunchState: COLD``, ``TotalTime: 678``."""
    parsed = device_ops._parse_am_start_output(_AM_START_SUCCESS.decode())
    assert parsed["status"] == "ok"
    assert parsed["launch_state"] == "COLD"
    assert parsed["total_time_ms"] == 678
    assert parsed["error"] is None


def test_parse_am_start_output_error() -> None:
    """When ``am start -W`` reports an error there's no ``Status: ok`` line
    but there *is* an ``Error: ...`` line we surface for the wizard."""
    parsed = device_ops._parse_am_start_output(_AM_START_DOES_NOT_EXIST.decode())
    assert parsed["status"] is None
    assert parsed["launch_state"] is None
    assert parsed["total_time_ms"] is None
    assert parsed["error"] is not None
    assert "does not exist" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# Sidecar launcher cache (read / write / invalidate)


def test_launcher_cache_round_trip(tmp_path: Path) -> None:
    """Write then read the same package returns the cached activity;
    a different package name is treated as a miss (the cache is keyed
    on package and gets implicitly invalidated when the operator
    repoints the app at a different APK)."""
    device_ops.write_launcher_cache(
        tmp_path, "com.example.app", "com.example.app/.MainActivity",
    )
    assert (
        device_ops.read_launcher_cache(tmp_path, "com.example.app")
        == "com.example.app/.MainActivity"
    )
    # Different package → miss (defensive behaviour, not just key lookup).
    assert device_ops.read_launcher_cache(tmp_path, "com.other.app") is None


def test_launcher_cache_invalidate_drops_file(tmp_path: Path) -> None:
    """``invalidate_launcher_cache`` removes the sidecar file so the
    next launch re-resolves. The route calls this after a successful
    install so a renamed MainActivity in the new APK is picked up."""
    device_ops.write_launcher_cache(
        tmp_path, "com.example.app", "com.example.app/.OldActivity",
    )
    cache_file = tmp_path / device_ops._LAUNCHER_CACHE_FILENAME
    assert cache_file.is_file()
    device_ops.invalidate_launcher_cache(tmp_path)
    assert not cache_file.exists()
    # Idempotent — invalidating an already-empty cache is a no-op.
    device_ops.invalidate_launcher_cache(tmp_path)


def test_launcher_cache_no_app_dir_is_noop(tmp_path: Path) -> None:
    """All cache helpers tolerate ``app_dir=None`` so callers (tests,
    skills) can opt out of caching without special-casing."""
    assert device_ops.read_launcher_cache(None, "com.example") is None
    device_ops.write_launcher_cache(None, "com.example", "com.example/.X")
    device_ops.invalidate_launcher_cache(None)
    # Nothing should have leaked into tmp_path because we passed None.
    assert not list(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# adb_launch_package — full pipeline (resolve → am start → pidof verify)


def _launch_pipeline_stub(
    *,
    activity_out: bytes = _RESOLVE_ACTIVITY_BRIEF,
    am_out: bytes = _AM_START_SUCCESS,
    am_rc: int = 0,
    pidof_out: bytes = b"12345\n",
    pidof_rc: int = 0,
    monkey_out: bytes = b"Events injected: 1\n",
    monkey_rc: int = 0,
):
    """Stub assembling the four-call sequence ``adb_launch_package`` may
    issue. Tests pass overrides for whichever leg they're exercising."""
    return _make_exec_stub({
        ("adb", "shell", "cmd", "package", "resolve-activity"):
            _StubProc(0 if activity_out else 1, activity_out),
        ("adb", "shell", "am", "start"): _StubProc(am_rc, am_out),
        ("adb", "shell", "pidof"): _StubProc(pidof_rc, pidof_out),
        ("adb", "shell", "monkey"): _StubProc(monkey_rc, monkey_out),
    })


def test_adb_launch_package_am_start_happy_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Resolve → am start ok → pidof returns PID. The wizard sees the
    full diagnostic surface (activity, COLD/WARM, TotalTime, pid) so
    the launch row reads "COLD start in 678 ms (com.example/.MainActivity)"
    instead of a bare green check."""
    monkeypatch.setattr(
        device_ops.asyncio, "create_subprocess_exec", _launch_pipeline_stub(),
    )

    async def no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(device_ops.asyncio, "sleep", no_sleep)

    res = asyncio.run(
        device_ops.adb_launch_package("com.example.weakbank.low", app_dir=tmp_path)
    )
    assert res["ok"] is True
    assert res["activity"] == "com.example.weakbank.low/.MainActivity"
    assert res["launch_state"] == "COLD"
    assert res["total_time_ms"] == 678
    assert res["pid"] == 12345
    assert res["verified_running"] is True
    assert res["used_monkey_fallback"] is False
    assert res["error"] is None
    # And the side effect: the sidecar cache now contains the activity
    # so the next launch skips the resolve roundtrip.
    assert (
        device_ops.read_launcher_cache(tmp_path, "com.example.weakbank.low")
        == "com.example.weakbank.low/.MainActivity"
    )


def test_adb_launch_package_pidof_overrides_am_start_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``am start -W`` prints ``Status: ok`` but the app crashes on
    startup (e.g. signature mismatch on reinstall) so ``pidof`` returns
    nothing across all retries. ``pidof`` is the ground truth — the
    wizard MUST report failure, not a green check."""
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _launch_pipeline_stub(pidof_out=b"", pidof_rc=1),
    )

    async def no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(device_ops.asyncio, "sleep", no_sleep)

    res = asyncio.run(
        device_ops.adb_launch_package("com.example.app", app_dir=tmp_path)
    )
    assert res["ok"] is False
    assert res["verified_running"] is False
    assert res["pid"] is None
    # Diagnostics still get through so the operator sees what am start
    # claimed before pidof shot it down.
    assert res["activity"] == "com.example.weakbank.low/.MainActivity"
    assert "not running" in (res["error"] or "")


def test_adb_launch_package_am_start_failure_with_pid_alive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The inverse: ``am start -W`` claims failure (corrupt output, weird
    Android quirk) but the process IS alive. ``pidof`` is still ground
    truth — surface success so we don't false-negative on noisy builds.
    This is exactly the ``args:``/``data=`` failure mode the user
    reported with the old monkey heuristic."""
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _launch_pipeline_stub(am_out=b"garbled output\n", am_rc=1),
    )

    async def no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(device_ops.asyncio, "sleep", no_sleep)

    res = asyncio.run(
        device_ops.adb_launch_package("com.example.app", app_dir=tmp_path)
    )
    assert res["ok"] is True
    assert res["pid"] == 12345
    assert res["verified_running"] is True
    # Timing diagnostics are missing because the parser couldn't latch
    # onto a Status: ok line — that's fine, the wizard's launchDetail
    # falls back to "running (pid …)".
    assert res["total_time_ms"] is None


def test_adb_launch_package_falls_back_to_monkey_when_resolve_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If ``cmd package resolve-activity`` returns no usable component
    (older Android, mid-rebuild PackageManager) we drop down to the
    legacy monkey launcher. ``used_monkey_fallback`` flips so the
    wizard can surface the unusual environment."""
    monkeypatch.setattr(
        device_ops.asyncio,
        "create_subprocess_exec",
        _launch_pipeline_stub(activity_out=b"No activity found\n"),
    )

    async def no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(device_ops.asyncio, "sleep", no_sleep)

    res = asyncio.run(
        device_ops.adb_launch_package("com.example.app", app_dir=tmp_path)
    )
    assert res["ok"] is True   # pidof says it's running
    assert res["used_monkey_fallback"] is True
    assert res["activity"] is None  # nothing to cache when fallback used
    # And we did NOT cache an empty activity — would poison subsequent runs.
    assert device_ops.read_launcher_cache(tmp_path, "com.example.app") is None


def test_adb_launch_package_uses_cached_activity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Cache hit short-circuits the resolve roundtrip — we should see
    ``am start`` and ``pidof`` calls but no ``cmd package`` call."""
    device_ops.write_launcher_cache(
        tmp_path, "com.example.app", "com.example.app/.MainActivity",
    )
    seen: list[tuple[str, ...]] = []

    async def fake_exec(*args: object, **_kw: object) -> _StubProc:
        argv = tuple(str(a) for a in args)
        seen.append(argv)
        if argv[:5] == ("adb", "shell", "am", "start", "-W"):
            return _StubProc(0, _AM_START_SUCCESS)
        if argv[:3] == ("adb", "shell", "pidof"):
            return _StubProc(0, b"99999\n")
        return _StubProc(0)

    async def no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(device_ops.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(device_ops.asyncio, "sleep", no_sleep)

    res = asyncio.run(
        device_ops.adb_launch_package("com.example.app", app_dir=tmp_path)
    )
    assert res["ok"] is True
    assert res["pid"] == 99999
    # The crucial assertion: no resolve-activity call was made because
    # the cache hit served the activity name directly.
    assert not any(
        argv[:5] == ("adb", "shell", "cmd", "package", "resolve-activity")
        for argv in seen
    )


def test_adb_launch_package_invalidates_stale_cache_on_does_not_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The "you reinstalled the app and the activity got renamed" case.
    First am start fails with 'does not exist'; we invalidate the cache,
    re-resolve to the new activity, and try once more — that one
    succeeds and the new activity replaces the cached entry."""
    device_ops.write_launcher_cache(
        tmp_path, "com.example.app", "com.example.app/.OldActivity",
    )
    am_calls: list[tuple[str, ...]] = []

    async def fake_exec(*args: object, **_kw: object) -> _StubProc:
        argv = tuple(str(a) for a in args)
        if argv[:5] == ("adb", "shell", "am", "start", "-W"):
            am_calls.append(argv)
            # First am start uses the cached (stale) activity; second
            # uses the freshly resolved one. We discriminate by the
            # ``-n <activity>`` token at the end.
            if "com.example.app/.OldActivity" in argv:
                return _StubProc(1, _AM_START_DOES_NOT_EXIST)
            return _StubProc(0, _AM_START_SUCCESS)
        if argv[:5] == ("adb", "shell", "cmd", "package", "resolve-activity"):
            return _StubProc(
                0,
                b"priority=0 preferredOrder=0\ncom.example.app/.NewActivity\n",
            )
        if argv[:3] == ("adb", "shell", "pidof"):
            return _StubProc(0, b"4321\n")
        return _StubProc(0)

    async def no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(device_ops.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(device_ops.asyncio, "sleep", no_sleep)

    res = asyncio.run(
        device_ops.adb_launch_package("com.example.app", app_dir=tmp_path)
    )
    assert res["ok"] is True
    # Two am start calls: first with the stale activity, second with
    # the freshly resolved one.
    assert len(am_calls) == 2
    assert "com.example.app/.OldActivity" in am_calls[0]
    assert "com.example.app/.NewActivity" in am_calls[1]
    # Cache replaced with the new activity name.
    assert (
        device_ops.read_launcher_cache(tmp_path, "com.example.app")
        == "com.example.app/.NewActivity"
    )


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
