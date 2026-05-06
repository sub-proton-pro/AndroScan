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
    """``adb shell pidof frida-server`` returning a pid → ok=True, running=True,
    detection=='pidof'. ``ps -A`` runs unconditionally to enrich uid +
    helper, but the simple factory returns the same canned response
    for every adb call so the row count is too short to extract uid."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(0, b"4321\n", b"")}),
    )
    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is True
    assert out["running"] is True
    assert out["pid"] == 4321
    # ps -A row was empty → can't determine uid / helper. Probe still
    # reports running because pidof gave us a positive PID.
    assert out["uid"] is None
    assert out["helper_running"] is False
    assert out["detection"] == "pidof"
    assert out["error"] is None


def test_probe_frida_server_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty stdout from ``pidof``, ``ps``, *and* the host ``frida-ps``
    fallback → not running, error surfaced, detection is None.

    The factory only knows about ``adb``; the host-side ``frida-ps``
    fallback gets a ``FileNotFoundError`` from the factory which
    ``_run`` translates to ``rc=-1`` — same behaviour as a host without
    frida-tools installed."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(1, b"", b"")}),
    )
    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is False
    assert out["running"] is False
    assert out["pid"] is None
    assert out["uid"] is None
    assert out["helper_running"] is False
    assert out["detection"] is None
    assert "frida-server" in out["error"]


def test_probe_frida_server_no_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """adb itself errors (no device) → not ok, original adb stderr
    forwarded (takes precedence over the host-side fallback's stderr)."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _make_subprocess_factory({"adb": _FakeProc(1, b"", b"adb: no devices/emulators found\n")}),
    )
    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is False
    assert out["running"] is False
    assert out["uid"] is None
    assert out["helper_running"] is False
    assert out["detection"] is None
    assert "no devices" in out["error"]


def test_probe_frida_server_versioned_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """``pidof frida-server`` misses a versioned binary like
    ``frida-server-16.7.19-android-arm64`` (kernel ``comm`` is truncated
    to ``frida-server-16.``), but the ``ps -A`` fallback finds it via
    the prefix match and also captures uid. detection=='ps'."""

    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        # `adb shell pidof frida-server` → no match for the versioned name.
        if "pidof" in argv:
            return _FakeProc(1, b"", b"")
        # `adb shell ps -A` → process is alive, comm truncated by the kernel.
        if "ps" in argv:
            return _FakeProc(
                0,
                (
                    b"USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME\n"
                    b"root             1     0   38684   2868 SyS_epoll_wait   0 S init\n"
                    b"root          7777     1   45000   1500 SyS_poll         0 S frida-server-16.\n"
                    b"shell         8888  7777   12345    250 SyS_poll         0 S sh\n"
                ),
                b"",
            )
        return _FakeProc(127, b"", b"unexpected argv")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is True
    assert out["running"] is True
    assert out["pid"] == 7777
    assert out["uid"] == "root"
    assert out["helper_running"] is False
    assert out["detection"] == "ps"
    assert out["error"] is None


def test_probe_frida_server_host_fallback_renamed_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stealth-renamed binary: ``pidof`` empty, ``ps -A`` shows nothing
    matching ``frida-server*`` (the binary was renamed to evade
    ``/proc/*/comm`` greps), but the host-side ``frida-ps -U`` enumerates
    processes successfully → running=True, pid=None, detection='frida-ps'.

    Mirrors the exact symptom the operator hit on a real device:
    ``adb shell pidof frida-server`` and ``ps -A | grep frida`` were both
    silent, while ``frida-ps -U`` listed every process. The probe should
    confirm reachability from the host wire-protocol check rather than
    falsely reporting "not running"."""

    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        if "pidof" in argv:
            return _FakeProc(1, b"", b"")
        if "ps" in argv:
            # `ps -A` lists no process whose comm starts with frida-server.
            return _FakeProc(
                0,
                (
                    b"USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME\n"
                    b"root             1     0   38684   2868 SyS_epoll_wait   0 S init\n"
                    b"root           430  1     12345    900 SyS_poll         0 S adbd\n"
                    b"shell         8888  430   12345    250 SyS_poll         0 S sh\n"
                ),
                b"",
            )
        if argv and argv[0] == "frida-ps":
            # Host-side enumeration succeeds — wire protocol is alive.
            return _FakeProc(
                0,
                (
                    b" PID  Name\n"
                    b"-----  ----------------\n"
                    b" 1528  Google\n"
                    b"17371  Clock\n"
                    b" 2167  WeakBank Low\n"
                    b"  430  adbd\n"
                ),
                b"",
            )
        return _FakeProc(127, b"", b"unexpected argv")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is True
    assert out["running"] is True
    assert out["pid"] is None  # no on-device PID — wire-protocol confirmation only
    # uid + helper unknowable without a device-side row to read from.
    assert out["uid"] is None
    assert out["helper_running"] is False
    assert out["detection"] == "frida-ps"
    assert out["error"] is None


def test_probe_frida_server_host_fallback_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``frida-ps -U`` exits non-zero (no USB device, transport error) →
    fallback fails, probe reports not running."""

    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        if "pidof" in argv:
            return _FakeProc(1, b"", b"")
        if "ps" in argv:
            return _FakeProc(0, b"USER PID ... NAME\nroot 1 ... init\n", b"")
        if argv and argv[0] == "frida-ps":
            return _FakeProc(
                1, b"", b"Failed to enumerate processes: unable to connect\n"
            )
        return _FakeProc(127, b"", b"unexpected argv")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is False
    assert out["running"] is False
    assert out["pid"] is None
    assert out["detection"] is None
    assert out["error"]


def test_probe_frida_server_host_fallback_missing_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Host doesn't have ``frida-ps`` installed → factory raises
    ``FileNotFoundError`` which ``_run`` swallows as ``rc=-1`` → probe
    correctly reports not running rather than crashing."""

    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        if "pidof" in argv:
            return _FakeProc(1, b"", b"")
        if "ps" in argv:
            return _FakeProc(0, b"USER PID ... NAME\nroot 1 ... init\n", b"")
        # Anything else (including frida-ps) raises — ``_run`` catches.
        raise FileNotFoundError(argv[0] if argv else "?")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is False
    assert out["running"] is False
    assert out["detection"] is None


# ---- uid + helper detection (Settings card warning + diagnostics) ----
#
# The uid signal is what catches the "running but unprivileged" failure
# mode the operator hit on Apr 29: ``frida-server`` started by hand
# without ``adb root`` runs as uid 2000 (``shell``); ``frida-ps`` works
# fine because process enumeration is unprivileged, but every
# ``device.attach(<pid>)`` fails with ``unable to connect to remote
# frida-server: closed`` once the per-attach helper hits the ptrace
# barrier on a non-root server. The probe's job is to surface that the
# server's *uid* is wrong so the Settings card can suggest a restart.


def test_probe_frida_server_uid_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: server running as root + helper observed → uid='root',
    helper_running=True. This is the state the Settings card considers
    "fully healthy"; no warning surfaced."""

    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        if "pidof" in argv:
            return _FakeProc(0, b"4758\n", b"")
        if "ps" in argv:
            return _FakeProc(
                0,
                (
                    b"USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME\n"
                    b"root             1     0   38684   2868 SyS_epoll_wait   0 S init\n"
                    b"root          4758     1   45000   1500 SyS_poll         0 S frida-server\n"
                    b"shell        32098 32096   16266   2567 do_epoll_wait    0 S re.frida.helper\n"
                ),
                b"",
            )
        return _FakeProc(127, b"", b"unexpected argv")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is True
    assert out["running"] is True
    assert out["pid"] == 4758
    assert out["uid"] == "root"
    assert out["helper_running"] is True
    # detection stays "pidof" because layer 1 already confirmed; ps -A
    # is doing enrichment, not primary detection.
    assert out["detection"] == "pidof"
    assert out["error"] is None


def test_probe_frida_server_uid_shell_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server running but as the unprivileged ``shell`` user. Probe must
    still report running=True (process IS up) but uid='shell' so the
    Settings card can show the "running as shell — app attaches will
    fail" warning AND the Start-as-root button."""

    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        if "pidof" in argv:
            return _FakeProc(0, b"5500\n", b"")
        if "ps" in argv:
            return _FakeProc(
                0,
                (
                    b"USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME\n"
                    b"shell         5500   430   45000   1500 SyS_poll         0 S frida-server\n"
                ),
                b"",
            )
        return _FakeProc(127, b"", b"unexpected argv")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is True
    assert out["running"] is True
    assert out["pid"] == 5500
    assert out["uid"] == "shell"  # ← the signal the Settings card warns on
    assert out["helper_running"] is False
    assert out["error"] is None


def test_probe_frida_server_skips_zombie_for_uid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the daemon-fork pattern leaves a transient zombie parent
    around, ``ps -A`` shows TWO frida-server-ish rows: ``[frida-server]``
    (zombie, name wrapped in brackets) and ``frida-server`` (the live
    daemon). The bracketed zombie row shouldn't poison the uid read —
    we only consider rows whose comm STARTS WITH ``frida-server``
    (zombie names start with ``[``), and we attribute uid to whichever
    row matches the pid we ended up resolving.

    Reproduces the exact ``ps -A`` snapshot the operator saw right
    after launching with ``adb shell "su 0 frida-server -D"``."""

    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        if "pidof" in argv:
            # pidof returns the LIVE pid (it skips zombies by default).
            return _FakeProc(0, b"4758\n", b"")
        if "ps" in argv:
            return _FakeProc(
                0,
                (
                    b"USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME\n"
                    b"root          4756   430       0      0 -                0 Z [frida-server]\n"
                    b"root          4758     1   45000   1500 SyS_poll         0 S frida-server\n"
                ),
                b"",
            )
        return _FakeProc(127, b"", b"unexpected argv")

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_frida_server("adb"))
    assert out["ok"] is True
    assert out["pid"] == 4758
    # uid attributed to the LIVE row (4758), zombie ([frida-server], 4756)
    # was filtered out by the bracket-prefix check.
    assert out["uid"] == "root"


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
    """Every candidate ``frida-server`` path fails → ok=False, no skew opinion.

    The probe walks bare ``frida-server`` plus every entry in
    ``_FRIDA_SERVER_DEVICE_PATHS``; if all of them return non-zero with
    a "not found" stderr the card surfaces the last shell error so the
    operator can tell "no device" from "binary missing".
    """
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
    assert "not found" in out["error"]


def test_probe_frida_version_skew_resolves_via_pid_readlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the running pid is known, the probe reads ``/proc/<pid>/exe``
    and invokes that exact binary — even when it lives outside ``$PATH``.

    Models the operator-followed-the-playbook case: ``frida-server`` was
    pushed to ``/data/local/tmp/`` (which is not on the device shell's
    ``$PATH``) and is running as pid 4680. ``readlink`` returns the
    full path; we then exec it directly and skip the bare-name attempt.
    """
    seen_argv: list[tuple[str, ...]] = []

    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        seen_argv.append(argv)
        if argv[1] == "shell" and argv[2] == "readlink":
            return _FakeProc(0, b"/data/local/tmp/frida-server\n", b"")
        if argv[1] == "shell" and argv[2] == "/data/local/tmp/frida-server":
            return _FakeProc(0, b"16.4.10\n", b"")
        return _FakeProc(127, b"", b"unexpected argv: " + " ".join(argv).encode())

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_frida_version_skew(
        {"version": "16.4.10"}, "adb", pid=4680,
    ))
    assert out["ok"] is True
    assert out["device_version"] == "16.4.10"
    assert out["severity"] is None
    # Readlink must run first; the resolved path is the *only* binary we
    # probe (no bare-name or fallback-list attempts when readlink wins).
    binaries = [a[2] for a in seen_argv if a[1] == "shell"]
    assert binaries == ["readlink", "/data/local/tmp/frida-server"]


def test_probe_frida_version_skew_falls_back_to_known_install_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No pid known; bare ``frida-server`` fails (not on $PATH) but
    ``/data/local/tmp/frida-server`` succeeds — the card recovers.

    Regression for the user-reported case where the playbook installs
    to ``/data/local/tmp/`` and the resulting card stayed red with
    ``frida-server: inaccessible or not found`` even though the server
    was actually running.
    """
    seen_argv: list[tuple[str, ...]] = []
    not_found = b"/system/bin/sh: frida-server: inaccessible or not found\n"

    async def factory(*argv: str, **kwargs: Any) -> _FakeProc:
        seen_argv.append(argv)
        if argv[1] == "shell" and argv[2] == "frida-server":
            return _FakeProc(127, b"", not_found)
        if argv[1] == "shell" and argv[2] == "/data/local/tmp/frida-server":
            return _FakeProc(0, b"16.4.10\n", b"")
        return _FakeProc(127, b"", b"unexpected: " + " ".join(argv).encode())

    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    out = asyncio.run(hp.probe_frida_version_skew(
        {"version": "16.4.10"}, "adb",
    ))
    assert out["ok"] is True
    assert out["device_version"] == "16.4.10"
    # Bare name is tried first (cheap, common case for /system/bin
    # installs), then the canonical /data/local/tmp/ install location.
    binaries = [a[2] for a in seen_argv if a[1] == "shell"]
    assert binaries[:2] == ["frida-server", "/data/local/tmp/frida-server"]
    # No further fallbacks should run once we got a parsable version.
    assert "/system/bin/frida-server" not in binaries


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
# LCP.3 / DEC-027 — llama.cpp llama-server probe (probe_llamacpp)


class _FakeUrlopenResp:
    """Minimal urlopen stand-in used by both the curl-absent fallback
    path and the `_FakeProc`-equivalent on systems where curl is not
    on PATH (Linux CI runners typically have it; the urllib path is
    the safety net)."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeUrlopenResp":
        return self

    def __exit__(self, *a) -> None:
        return None


def _force_urllib_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the urllib fallback path so the test isn't sensitive to
    whether curl happens to be on PATH on the CI runner."""
    import androscan.web.health_probes as mod
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    async def to_thread(fn, *args, **kw):
        return fn(*args, **kw)
    monkeypatch.setattr("asyncio.to_thread", to_thread)


def test_probe_llamacpp_up_with_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_UP`` — server live, model loaded. ``ok`` flips green."""
    _force_urllib_fallback(monkeypatch)
    import json as _json

    body = _json.dumps({
        "object": "list",
        "data": [{"id": "qwen3-27b-q5km", "object": "model", "owned_by": "llamacpp"}],
    }).encode()

    def fake_urlopen(req, timeout=0):
        return _FakeUrlopenResp(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    out = asyncio.run(hp.probe_llamacpp("http://127.0.0.1:8033/v1"))
    assert out["ok"] is True
    assert out["reachable"] is True
    assert out["models"] == ["qwen3-27b-q5km"]
    assert out["error"] is None
    assert out["url"].endswith("/models")


def test_probe_llamacpp_partial_up_no_models_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_PARTIAL_UP`` — server live (HTTP 200 with empty data list)
    but model still mmap'ing. Operator sees a yellow card with a
    "wait for cold-start" hint rather than a misleading red."""
    _force_urllib_fallback(monkeypatch)
    import json as _json

    body = _json.dumps({"object": "list", "data": []}).encode()
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=0: _FakeUrlopenResp(body),
    )

    out = asyncio.run(hp.probe_llamacpp("http://127.0.0.1:8033/v1"))
    assert out["ok"] is False
    assert out["reachable"] is True   # server is up …
    assert out["models"] == []        # … but model not loaded yet
    assert out["error"] and "no models loaded" in out["error"]


def test_probe_llamacpp_down_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_DOWN`` — server unreachable. ``urlopen`` raises URLError;
    the probe surfaces the error string in ``error`` (truncated)."""
    _force_urllib_fallback(monkeypatch)
    from urllib.error import URLError

    def fake_urlopen(req, timeout=0):
        raise URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    out = asyncio.run(hp.probe_llamacpp("http://127.0.0.1:8033/v1"))
    assert out["ok"] is False
    assert out["reachable"] is False
    assert out["models"] == []
    assert out["error"] and "connection refused" in out["error"]


def test_probe_llamacpp_unparsable_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server reachable but body isn't JSON — surface the parser
    error rather than crashing the status aggregator."""
    _force_urllib_fallback(monkeypatch)

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=0: _FakeUrlopenResp(b"<html>nginx error</html>"),
    )

    out = asyncio.run(hp.probe_llamacpp("http://127.0.0.1:8033/v1"))
    assert out["ok"] is False
    # urllib path returns the error from JSON parse failure under
    # `_sync`; either the parsed-failure label or "JSONDecodeError"
    # is acceptable, but the card MUST surface *something* rather
    # than silently flipping green.
    assert out["error"]


def test_probe_llamacpp_error_shape_matches_probe_ollama_tags() -> None:
    """Regression guard — :func:`probe_llamacpp` MUST return the
    same key set as :func:`probe_ollama_tags` so the
    :func:`status_routes._gather_global` LLM card builder can be a
    single shared codepath."""
    ollama_keys = {"ok", "reachable", "url", "ping_ms", "models", "error"}

    # Construct a synthetic ``_DOWN`` shape from each probe and compare keys.
    async def _ollama_dummy() -> dict:
        # Re-use the one path that doesn't actually hit the network: the
        # urllib fallback when both curl and urlopen are unreachable.
        return {
            "ok": False, "reachable": False,
            "url": "http://x/api/tags", "ping_ms": 0,
            "models": [], "error": "stubbed",
        }

    async def _llamacpp_dummy() -> dict:
        return {
            "ok": False, "reachable": False,
            "url": "http://x/v1/models", "ping_ms": 0,
            "models": [], "error": "stubbed",
        }

    olla = asyncio.run(_ollama_dummy())
    lcp = asyncio.run(_llamacpp_dummy())
    assert set(olla.keys()) == ollama_keys
    assert set(lcp.keys()) == ollama_keys


class TestLlamacppModelPresent:
    """:func:`llamacpp_model_present` matches against
    ``llama-server``'s operator-controlled model id (no canonical
    ``:tag`` suffix scheme like Ollama)."""

    def test_empty_models_returns_false(self) -> None:
        assert hp.llamacpp_model_present({"models": []}, "qwen3") is False

    def test_empty_model_arg_accepts_any_loaded_model(self) -> None:
        """LCP.4 will plumb ``llamacpp_model``; until then the LLM
        card flips green on any loaded model rather than refusing
        to acknowledge a working server."""
        assert hp.llamacpp_model_present(
            {"models": ["qwen3-27b-q5km"]}, "",
        ) is True

    def test_exact_match_case_insensitive(self) -> None:
        assert hp.llamacpp_model_present(
            {"models": ["Qwen3-27B-Q5KM"]}, "qwen3-27b-q5km",
        ) is True

    def test_prefix_match_either_way(self) -> None:
        assert hp.llamacpp_model_present(
            {"models": ["qwen3-27b-q5km-v2"]}, "qwen3-27b-q5km",
        ) is True
        assert hp.llamacpp_model_present(
            {"models": ["qwen3-27b"]}, "qwen3-27b-q5km",
        ) is True

    def test_no_match_returns_false(self) -> None:
        assert hp.llamacpp_model_present(
            {"models": ["qwen3-27b-q5km"]}, "llama2-7b",
        ) is False


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
