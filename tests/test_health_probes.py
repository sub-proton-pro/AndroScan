"""Unit tests for the pure-function health probes.

We monkeypatch ``shutil.which`` and ``asyncio.create_subprocess_exec`` to
keep the tests hermetic — no real adb / jadx / ollama needs to be on the
test runner.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from androscan.web import health_probes as hp


# ---------------------------------------------------------------------------
# Subprocess plumbing


class _FakeProc:
    def __init__(self, rc: int, stdout: bytes, stderr: bytes) -> None:
        self.returncode = rc
        self._out = stdout
        self._err = stderr

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        return self._out, self._err


def _make_subprocess_factory(plan: dict[str, _FakeProc]):
    """Return a coroutine that mimics ``asyncio.create_subprocess_exec``.

    ``plan`` keys are the first argv element (the binary). Anything not in
    the plan raises ``FileNotFoundError`` to mirror real "missing binary"
    behaviour.
    """
    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        if not argv:
            raise FileNotFoundError("no binary")
        cmd = argv[0]
        if cmd not in plan:
            raise FileNotFoundError(cmd)
        return plan[cmd]
    return factory


# ---------------------------------------------------------------------------
# probe_tool_version


def test_probe_tool_version_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hp, "_which", lambda c: None)
    out = asyncio.run(hp.probe_tool_version("nope"))
    assert out["ok"] is False
    assert out["found"] is False
    assert out["error"]


def test_probe_tool_version_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hp, "_which", lambda c: f"/fake/bin/{c}")
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"/fake/bin/jadx": _FakeProc(0, b"jadx 1.5.0\n", b"")}),
    )
    out = asyncio.run(hp.probe_tool_version("jadx", "--version", parse_first_token=True))
    assert out["ok"] is True
    assert out["found"] is True
    assert out["version"] == "1.5.0"


def test_probe_tool_version_no_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hp, "_which", lambda c: f"/fake/bin/{c}")
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"/fake/bin/adb": _FakeProc(0, b"", b"")}),
    )
    out = asyncio.run(hp.probe_tool_version("adb"))
    assert out["ok"] is False
    assert "no version output" in out["error"]


def test_probe_apktool_version_uses_version_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Apktool 2.x exits non-zero for `--version`; the probe must call the
    # `version` subcommand instead. Record argv to prove the wiring.
    seen_argv: list[tuple[str, ...]] = []

    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        seen_argv.append(argv)
        return _FakeProc(0, b"2.12.0\n", b"")

    monkeypatch.setattr(hp, "_which", lambda c: f"/fake/bin/{c}")
    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_apktool_version("apktool"))
    assert out["ok"] is True
    assert out["version"] == "2.12.0"
    assert seen_argv == [("/fake/bin/apktool", "version")]


# ---------------------------------------------------------------------------
# probe_adb_device


def test_probe_adb_device_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Healthy emulator: get-state returns `device`, get-serialno returns serial."""
    seen_argv: list[tuple[str, ...]] = []

    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        seen_argv.append(argv)
        if argv[1] == "get-state":
            return _FakeProc(0, b"device\n", b"")
        if argv[1] == "get-serialno":
            return _FakeProc(0, b"emulator-5554\n", b"")
        return _FakeProc(1, b"", b"unexpected")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_adb_device())
    assert out["ok"] is True
    assert out["connected"] is True
    assert out["state"] == "device"
    assert out["serial"] == "emulator-5554"
    assert out["error"] is None
    # Both helper calls happened, in order.
    assert [a[1] for a in seen_argv] == ["get-state", "get-serialno"]


def test_probe_adb_device_no_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """No emulator attached: get-state exits non-zero with adb stderr."""
    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        # Real adb prints to stderr in this case.
        return _FakeProc(1, b"", b"adb: no devices/emulators found\n")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_adb_device())
    assert out["ok"] is False
    assert out["connected"] is False
    assert out["state"] is None
    assert out["serial"] is None
    assert "no devices" in out["error"]


def test_probe_adb_device_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Device is plugged in but not authorised → not ok, but connected=True."""
    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        if argv[1] == "get-state":
            return _FakeProc(0, b"unauthorized\n", b"")
        return _FakeProc(1, b"", b"")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_adb_device())
    assert out["ok"] is False
    assert out["connected"] is True
    assert out["state"] == "unauthorized"
    # No serial lookup attempted for non-`device` states.
    assert out["serial"] is None
    assert "unauthorized" in out["error"]


# ---------------------------------------------------------------------------
# probe_pkg_running (regression: ok must reflect actual running state)


def test_probe_pkg_running_not_running_when_no_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No device → adb shell fails → card is not ok and surfaces stderr.

    Regression: previously this returned ``ok=True, error=None`` even when
    adb couldn't reach a device, which made the "Running on device" card
    misleadingly green.
    """
    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        return _FakeProc(1, b"", b"adb: no devices/emulators found\n")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_pkg_running("com.example"))
    assert out["ok"] is False
    assert out["running"] is False
    assert out["pid"] is None
    assert "no devices" in (out["error"] or "")


def test_probe_pkg_running_actually_running(monkeypatch: pytest.MonkeyPatch) -> None:
    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        return _FakeProc(0, b"4242\n", b"")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_pkg_running("com.example"))
    assert out["ok"] is True
    assert out["running"] is True
    assert out["pid"] == 4242
    assert out["error"] is None


# ---------------------------------------------------------------------------
# probe_frida_server + probe_frida_version_skew (Hook Lab readiness)


def test_probe_frida_server_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """``adb shell pidof frida-server`` returning a pid → ok=True, running=True."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"4321\n", b"")}),
    )
    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is True
    assert out["running"] is True
    assert out["pid"] == 4321
    assert out["error"] is None


def test_probe_frida_server_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty stdout from ``pidof`` → not running, error surfaced."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(1, b"", b"")}),
    )
    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is False
    assert out["running"] is False
    assert out["pid"] is None
    assert "frida-server" in out["error"]


def test_probe_frida_server_no_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """adb itself errors (no device) → not ok, stderr forwarded."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(1, b"", b"adb: no devices/emulators found\n")}),
    )
    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is False
    assert out["running"] is False
    assert "no devices" in out["error"]


def test_probe_frida_version_skew_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same major+minor on host and device → ok, no severity."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"16.4.10\n", b"")}),
    )
    out = asyncio.run(hp.probe_frida_version_skew(
        {"version": "16.4.10"}, "adb",
    ))
    assert out["ok"] is True
    assert out["severity"] is None
    assert out["host_version"] == "16.4.10"
    assert out["device_version"] == "16.4.10"
    assert out["error"] is None


def test_probe_frida_version_skew_minor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same major, different minor → ok=True but severity='minor'."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"16.5.0\n", b"")}),
    )
    out = asyncio.run(hp.probe_frida_version_skew(
        {"version": "16.4.10"}, "adb",
    ))
    assert out["ok"] is True
    assert out["severity"] == "minor"
    assert "minor version skew" in out["error"]


def test_probe_frida_version_skew_major(monkeypatch: pytest.MonkeyPatch) -> None:
    """Major mismatch → ok=False, severity='major', error explains why."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"15.2.0\n", b"")}),
    )
    out = asyncio.run(hp.probe_frida_version_skew(
        {"version": "16.4.10"}, "adb",
    ))
    assert out["ok"] is False
    assert out["severity"] == "major"
    assert "incompatible" in out["error"].lower() or "wire protocol" in out["error"].lower()


def test_probe_frida_version_skew_device_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """``adb shell frida-server --version`` errors → ok=False, no skew opinion."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(127, b"", b"frida-server: not found\n")}),
    )
    out = asyncio.run(hp.probe_frida_version_skew(
        {"version": "16.4.10"}, "adb",
    ))
    assert out["ok"] is False
    assert out["severity"] is None
    assert out["device_version"] is None


# ---------------------------------------------------------------------------
# probe_device_cpu_abi (drives the Settings tab's frida-server install hint)


def test_probe_device_cpu_abi_arm64(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standard 64-bit ARM emulator/device → maps to ``android-arm64``."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"arm64-v8a\n", b"")}),
    )
    out = asyncio.run(hp.probe_device_cpu_abi("adb"))
    assert out["ok"] is True
    assert out["abi"] == "arm64-v8a"
    assert out["frida_arch"] == "android-arm64"
    assert out["error"] is None


def test_probe_device_cpu_abi_x86_64(monkeypatch: pytest.MonkeyPatch) -> None:
    """Intel HAXM emulator → ``android-x86_64``."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"x86_64\n", b"")}),
    )
    out = asyncio.run(hp.probe_device_cpu_abi("adb"))
    assert out["ok"] is True
    assert out["abi"] == "x86_64"
    assert out["frida_arch"] == "android-x86_64"


def test_probe_device_cpu_abi_armv7(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy 32-bit ARM device → ``android-arm`` (Frida only ships one 32-bit ARM build)."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"armeabi-v7a\n", b"")}),
    )
    out = asyncio.run(hp.probe_device_cpu_abi("adb"))
    assert out["ok"] is True
    assert out["abi"] == "armeabi-v7a"
    assert out["frida_arch"] == "android-arm"


def test_probe_device_cpu_abi_unknown_abi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrecognised ABI → ABI surfaced but ``frida_arch`` is None and ``ok`` is False."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"riscv64\n", b"")}),
    )
    out = asyncio.run(hp.probe_device_cpu_abi("adb"))
    assert out["ok"] is False
    assert out["abi"] == "riscv64"
    assert out["frida_arch"] is None
    assert out["error"] is not None
    assert "riscv64" in out["error"]


def test_probe_device_cpu_abi_no_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """adb errors (no device) → ok=False, abi=None, error forwarded."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(1, b"", b"adb: no devices/emulators found\n")}),
    )
    out = asyncio.run(hp.probe_device_cpu_abi("adb"))
    assert out["ok"] is False
    assert out["abi"] is None
    assert out["frida_arch"] is None
    assert out["error"] is not None


# ---------------------------------------------------------------------------
# probe_device_root_status (drives the install-playbook root warning)


def _root_probe_stdout(build_type: str, debuggable: str, id_line: str) -> bytes:
    """Build the exact ``getprop ; echo --- ; getprop ; echo --- ; id`` shape
    the probe joins server-side. Keeps the parser-boundary contract in one
    place so a refactor of the probe's command string fails fast here.
    """
    return f"{build_type}\n---\n{debuggable}\n---\n{id_line}\n".encode()


def test_probe_device_root_status_user_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production (Google Play) AVD: build=user, uid=2000 → can_adb_root=False."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, _root_probe_stdout(
            "user", "0", "uid=2000(shell) gid=2000(shell) groups=2000(shell)",
        ), b"")}),
    )
    out = asyncio.run(hp.probe_device_root_status("adb"))
    assert out["ok"] is True
    assert out["rooted"] is False
    assert out["can_adb_root"] is False
    assert out["current_uid"] == 2000
    assert out["build_type"] == "user"
    assert out["debuggable"] is False
    assert out["error"] is None


def test_probe_device_root_status_userdebug_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """AOSP / Google APIs AVD: build=userdebug, debuggable=1 → can_adb_root=True."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, _root_probe_stdout(
            "userdebug", "1", "uid=2000(shell) gid=2000(shell) groups=2000(shell)",
        ), b"")}),
    )
    out = asyncio.run(hp.probe_device_root_status("adb"))
    assert out["ok"] is True
    assert out["rooted"] is False  # not yet root, but adb root will succeed
    assert out["can_adb_root"] is True
    assert out["current_uid"] == 2000
    assert out["build_type"] == "userdebug"
    assert out["debuggable"] is True


def test_probe_device_root_status_already_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Magisk / eng device where adbd already runs as root: rooted=True wins."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, _root_probe_stdout(
            "user", "0", "uid=0(root) gid=0(root) groups=0(root)",
        ), b"")}),
    )
    out = asyncio.run(hp.probe_device_root_status("adb"))
    assert out["ok"] is True
    assert out["rooted"] is True
    # Even though build_type is ``user``, an already-root shell makes the
    # adb-root step a no-op rather than a failure — the UI gate is
    # ``can_adb_root``, so it must roll the rooted-shell case in.
    assert out["can_adb_root"] is True
    assert out["current_uid"] == 0


def test_probe_device_root_status_eng_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Engineering build: same allowance as userdebug."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, _root_probe_stdout(
            "eng", "1", "uid=2000(shell) gid=2000(shell) groups=2000(shell)",
        ), b"")}),
    )
    out = asyncio.run(hp.probe_device_root_status("adb"))
    assert out["can_adb_root"] is True
    assert out["build_type"] == "eng"


def test_probe_device_root_status_userdebug_but_not_debuggable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``userdebug`` build with ``ro.debuggable=0`` (rare hardened image):
    we refuse to claim ``can_adb_root=True`` since adbd will refuse the
    upgrade. Closes a footgun where a sideways-rooted device looks
    rootable on build_type alone but isn't.
    """
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, _root_probe_stdout(
            "userdebug", "0", "uid=2000(shell) gid=2000(shell) groups=2000(shell)",
        ), b"")}),
    )
    out = asyncio.run(hp.probe_device_root_status("adb"))
    assert out["can_adb_root"] is False
    assert out["debuggable"] is False


def test_probe_device_root_status_no_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """adb errors (no device) → ok=False, all four data fields None."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(1, b"", b"adb: no devices/emulators found\n")}),
    )
    out = asyncio.run(hp.probe_device_root_status("adb"))
    assert out["ok"] is False
    assert out["rooted"] is None
    assert out["can_adb_root"] is None
    assert out["current_uid"] is None
    assert out["build_type"] is None
    assert out["debuggable"] is None
    assert out["error"]


def test_probe_device_root_status_unparsable_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garbled getprop response (missing ``---`` boundaries) → ok=False, no claims."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"???\n", b"")}),
    )
    out = asyncio.run(hp.probe_device_root_status("adb"))
    assert out["ok"] is False
    assert out["can_adb_root"] is None
    assert out["error"]


# ---------------------------------------------------------------------------
# Ollama probes


def test_probe_ollama_tags_no_curl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the urllib fallback path."""
    import androscan.web.health_probes as mod

    def fake_which(name: str) -> str | None:
        return None  # no curl

    monkeypatch.setattr(mod.shutil, "which", fake_which)

    def fake_sync_thread(fn):
        async def runner():
            return fn()
        return runner()

    # Patch asyncio.to_thread to call our function inline.
    async def to_thread(fn, *args, **kw):
        return fn(*args, **kw)

    monkeypatch.setattr("asyncio.to_thread", to_thread)

    # Make urllib.urlopen succeed with a known JSON.
    import json as _json

    class _FakeResp:
        def read(self) -> bytes:
            return _json.dumps({"models": [{"name": "qwen3.5:35b"}, {"name": "nomic-embed-text"}]}).encode()

        def __enter__(self):  # noqa: D401
            return self

        def __exit__(self, *a) -> None:  # noqa: D401
            pass

    def fake_urlopen(req, timeout=0):
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    out = asyncio.run(hp.probe_ollama_tags("http://localhost:11434"))
    assert out["ok"] is True
    assert "qwen3.5:35b" in out["models"]
    assert hp.model_present(out, "qwen3.5:35b")
    assert hp.model_present(out, "qwen3.5") is True  # bare match
    assert hp.model_present(out, "missing") is False


# ---------------------------------------------------------------------------
# Embed provider


def test_probe_fastembed_available_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    out = hp.probe_fastembed_available()
    assert out["ok"] is False
    assert out["installed"] is False


def test_probe_fastembed_model_cache_missing_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FASTEMBED_CACHE", str(tmp_path / "does_not_exist"))
    out = hp.probe_fastembed_model_cache("BAAI/bge-small-en-v1.5")
    assert out["ok"] is False
    assert out["cached"] is False


def test_probe_fastembed_model_cache_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "fec"
    (cache / "fast-bge-small-en-v1.5").mkdir(parents=True)
    monkeypatch.setenv("FASTEMBED_CACHE", str(cache))
    out = hp.probe_fastembed_model_cache("BAAI/bge-small-en-v1.5")
    assert out["cached"] is True


def test_probe_embed_provider_hash_always_ok() -> None:
    out = asyncio.run(hp.probe_embed_provider("hash", "", ""))
    assert out["ok"] is True
    assert out["provider"] == "hash"


# ---------------------------------------------------------------------------
# Disk / path / dir-size


def test_probe_disk(tmp_path: Path) -> None:
    out = hp.probe_disk(tmp_path)
    assert "free_gb" in out
    assert isinstance(out["low_space"], bool)


def test_probe_path_writable_existing(tmp_path: Path) -> None:
    out = hp.probe_path_writable(tmp_path)
    assert out["ok"] is True
    assert out["writable"] is True


def test_probe_path_writable_missing(tmp_path: Path) -> None:
    out = hp.probe_path_writable(tmp_path / "nope")
    assert out["ok"] is False


def test_probe_dir_size(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_bytes(b"y" * 200)
    out = hp.probe_dir_size(tmp_path)
    assert out["ok"] is True
    assert out["size_bytes"] >= 300
    assert out["entries"] >= 3


# ---------------------------------------------------------------------------
# APK sha drift


def test_probe_apk_sha_drift_missing_inputs() -> None:
    out = hp.probe_apk_sha_drift(None, None)
    assert out["ok"] is False
    assert "no apk_path" in out["error"]


def test_probe_apk_sha_drift_match(tmp_path: Path) -> None:
    apk = tmp_path / "x.apk"
    apk.write_bytes(b"abc")
    from androscan.internal.app_meta import compute_apk_sha256
    sha = compute_apk_sha256(apk)
    out = hp.probe_apk_sha_drift(str(apk), sha)
    assert out["ok"] is True
    assert out["drift"] is False


def test_probe_apk_sha_drift_mismatch(tmp_path: Path) -> None:
    apk = tmp_path / "x.apk"
    apk.write_bytes(b"abc")
    out = hp.probe_apk_sha_drift(str(apk), "deadbeef" * 8)
    assert out["ok"] is False
    assert out["drift"] is True


# ---------------------------------------------------------------------------
# Per-app on-device probes (all use the FakeProc plumbing)


def test_probe_pkg_installed_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"package:/data/app/foo/base.apk\n", b"")}),
    )
    out = asyncio.run(hp.probe_pkg_installed("com.example"))
    assert out["installed"] is True
    assert out["apk_path_on_device"]


def test_probe_pkg_installed_no(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"", b"")}),
    )
    out = asyncio.run(hp.probe_pkg_installed("com.example"))
    assert out["installed"] is False


def test_probe_pkg_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"12345\n", b"")}),
    )
    out = asyncio.run(hp.probe_pkg_running("com.example"))
    assert out["running"] is True
    assert out["pid"] == 12345


def test_probe_foreground_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"  ACTIVITY com.example/.MainActivity\n", b"")}),
    )
    out = asyncio.run(hp.probe_foreground_activity())
    assert out["ok"] is True
    assert out["activity"] == "com.example/.MainActivity"
    assert out["package"] == "com.example"


def test_probe_python_env_shape() -> None:
    out = hp.probe_python_env()
    assert "python_version" in out
    assert "modules" in out
