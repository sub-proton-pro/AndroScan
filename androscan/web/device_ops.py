"""Pure-function helpers for the "Bring device online" wizard.

These wrap the small set of external commands the wizard needs:

* :func:`find_emulator_binary` — locate the ``emulator`` CLI (it lives under
  ``$ANDROID_HOME/emulator/`` and is *not* on PATH on most installations,
  unlike ``adb``).
* :func:`list_avds` — ``emulator -list-avds`` parsed into a list.
* :func:`spawn_emulator_detached` — fire-and-forget launch of an AVD.
* :func:`adb_install_apk` — ``adb install -r <apk>``.
* :func:`adb_launch_package` — resolve LAUNCHER activity via
  ``cmd package resolve-activity``, then ``am start -W -n <pkg>/<act>``,
  then verify via ``pidof``. Falls back to the legacy ``monkey``-based
  launcher only when activity resolution itself fails (older Android
  builds without ``cmd package``).

All command-running helpers are async, timeboxed, and **never raise** —
they always return a structured ``(ok, info)`` so the route handlers can
turn them straight into JSON without try/except gymnastics.

Kept separate from :mod:`androscan.web.app` so they're trivially unit-
testable (no FastAPI / event-loop scaffolding) and reusable from skills.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional


# Wall-clock caps. The emulator spawn returns immediately (detached); the
# wizard polls /api/device/status separately for boot completion.
ADB_INSTALL_TIMEOUT_SEC = 120.0
ADB_LAUNCH_TIMEOUT_SEC = 15.0
ADB_PM_PATH_TIMEOUT_SEC = 5.0
ADB_RESOLVE_ACTIVITY_TIMEOUT_SEC = 5.0
ADB_PIDOF_TIMEOUT_SEC = 3.0
EMULATOR_LIST_TIMEOUT_SEC = 5.0

# How long after ``am start -W`` returns to keep polling ``pidof`` before
# concluding the app didn't actually start. ``am start -W`` already waits
# for the activity's first frame, so the process *should* be alive by the
# time we get here — the extra polls are belt-and-braces for slow cold
# boots where init has reported BOOT_COMPLETED but the app's process
# hasn't been forked yet (rare; mostly happens on first launch after a
# brand-new install).
_PIDOF_VERIFY_RETRIES = 5
_PIDOF_VERIFY_INTERVAL_SEC = 0.5


# ---------------------------------------------------------------------------
# emulator binary discovery


def _candidate_android_homes() -> list[Path]:
    """Return likely roots for an Android SDK install, in order of preference."""
    out: list[Path] = []
    for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        v = os.environ.get(env_var)
        if v:
            out.append(Path(v))
    # Common defaults on macOS / Linux.
    home = Path.home()
    out.extend(
        [
            home / "Library" / "Android" / "sdk",   # macOS Android Studio default
            home / "Android" / "Sdk",               # Linux Android Studio default
            home / "android-sdk",                   # manual installs
        ]
    )
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        s = str(p)
        if s in seen:
            continue
        seen.add(s)
        uniq.append(p)
    return uniq


def find_emulator_binary() -> Optional[str]:
    """Best-effort locate the ``emulator`` binary path.

    Returns ``None`` if not found. Order:

    1. ``shutil.which("emulator")`` — covers users who put it on PATH.
    2. ``$ANDROID_HOME/emulator/emulator`` (and ``$ANDROID_SDK_ROOT/...``).
    3. Common SDK install locations on macOS / Linux.
    """
    on_path = shutil.which("emulator")
    if on_path:
        return on_path
    for root in _candidate_android_homes():
        candidate = root / "emulator" / "emulator"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


# ---------------------------------------------------------------------------
# AVD inventory


async def list_avds(timeout: float = EMULATOR_LIST_TIMEOUT_SEC) -> dict[str, Any]:
    """Run ``emulator -list-avds`` and parse the lines.

    Returns ``{ok, emulator_path, avds, error}``. ``ok`` is True when at
    least one AVD is reported (an empty list with the binary present is
    still ``ok=False`` because the wizard has nothing to start).
    """
    binary = find_emulator_binary()
    if not binary:
        return {
            "ok": False,
            "emulator_path": None,
            "avds": [],
            "error": (
                "Android 'emulator' binary not found. Set ANDROID_HOME or "
                "install Android Studio so $ANDROID_HOME/emulator/emulator exists."
            ),
        }
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "-list-avds",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return {"ok": False, "emulator_path": binary, "avds": [],
                "error": f"spawn error: {e}"}
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return {"ok": False, "emulator_path": binary, "avds": [],
                "error": f"`emulator -list-avds` timed out after {timeout:.0f}s"}

    if proc.returncode != 0:
        return {
            "ok": False,
            "emulator_path": binary,
            "avds": [],
            "error": (err_b or out_b or b"").decode(errors="replace").strip()[:400] or "non-zero exit",
        }
    avds = [
        ln.strip()
        for ln in (out_b or b"").decode(errors="replace").splitlines()
        if ln.strip() and not ln.startswith("INFO ")
    ]
    return {
        "ok": bool(avds),
        "emulator_path": binary,
        "avds": avds,
        "error": None if avds else "No AVDs found. Create one in Android Studio's Device Manager.",
    }


# ---------------------------------------------------------------------------
# emulator launch (detached)


async def spawn_emulator_detached(avd: str) -> dict[str, Any]:
    """Spawn ``emulator -avd <avd>`` in the background and return immediately.

    The emulator can take 30-90 s to boot; the caller is expected to poll
    ``/api/device/status`` until ``state == "device"``. We deliberately do
    **not** ``await communicate`` — that would block the request until the
    emulator window closes.

    Returns ``{ok, emulator_path, avd, pid, error}``.
    """
    if not avd or not avd.strip():
        return {"ok": False, "emulator_path": None, "avd": "", "pid": None,
                "error": "avd name is required"}
    binary = find_emulator_binary()
    if not binary:
        return {"ok": False, "emulator_path": None, "avd": avd, "pid": None,
                "error": "emulator binary not found (set ANDROID_HOME)"}
    try:
        # ``start_new_session=True`` detaches the child from this process's
        # session/group so the emulator survives uvicorn restarts and we
        # don't carry its zombies. stdout/stderr go to DEVNULL — we don't
        # want to fill our log buffer with hundreds of MB of QEMU chatter.
        proc = await asyncio.create_subprocess_exec(
            binary, "-avd", avd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        return {"ok": False, "emulator_path": binary, "avd": avd, "pid": None,
                "error": f"spawn error: {e}"}
    return {
        "ok": True,
        "emulator_path": binary,
        "avd": avd,
        "pid": proc.pid,
        "error": None,
    }


# ---------------------------------------------------------------------------
# adb helpers (install / launch / pm path)


async def adb_pm_path(package: str, timeout: float = ADB_PM_PATH_TIMEOUT_SEC) -> dict[str, Any]:
    """``adb shell pm path <pkg>`` — returns ``{installed, apk_path_on_device, error}``."""
    if not package:
        return {"installed": False, "apk_path_on_device": None, "error": "package required"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "shell", "pm", "path", package,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"installed": False, "apk_path_on_device": None, "error": "adb not on PATH"}
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return {"installed": False, "apk_path_on_device": None,
                "error": f"timeout after {timeout:.1f}s"}
    text = (out_b or b"").decode(errors="replace").strip()
    err = (err_b or b"").decode(errors="replace").strip()
    if text.lower().startswith("package:"):
        return {"installed": True, "apk_path_on_device": text.split(":", 1)[1].strip(),
                "error": None}
    return {"installed": False, "apk_path_on_device": None,
            "error": err[:300] or None if err else None}


async def adb_install_apk(apk_path: str, timeout: float = ADB_INSTALL_TIMEOUT_SEC) -> dict[str, Any]:
    """``adb install -r <apk>`` — returns ``{ok, exit_code, stdout, stderr, error}``."""
    if not apk_path:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "error": "apk_path required"}
    p = Path(apk_path)
    if not p.is_file():
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "error": f"APK not found at {apk_path}"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "install", "-r", str(p),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "error": "adb not on PATH"}
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "error": f"adb install timed out after {timeout:.0f}s"}
    out = (out_b or b"").decode(errors="replace").strip()
    err = (err_b or b"").decode(errors="replace").strip()
    # ``adb install`` writes "Success" on stdout for success even with rc=0,
    # but rare combinations (busy device) print "Success" with non-zero rc;
    # treat both signals as authoritative.
    success = (proc.returncode == 0) and ("success" in out.lower() or not out)
    return {
        "ok": success,
        "exit_code": proc.returncode,
        "stdout": out[:2000],
        "stderr": err[:2000],
        "error": None if success else (err[:300] or out[:300] or "install failed"),
    }


# ---------------------------------------------------------------------------
# Launcher activity resolution + sidecar cache
#
# Background: the previous launcher used ``adb shell monkey -p <pkg> -c
# LAUNCHER 1``, which works but has a nasty failure mode — on some
# Android builds (Pixel 8 / API 34 system images) ``monkey`` defaults
# its verbose flag to ≥ 2, dumping ``args: [...] arg: "-p" ...`` on
# stdout for *every* invocation, success or failure. There's no
# reliable string heuristic to disambiguate that from the AM-not-ready
# failure mode (which can also write similar lines), so the wizard
# would render a red ✕ on a launch that actually worked.
#
# AOSP's intended launcher API is ``am start -W -n <pkg>/<activity>``,
# which prints structured output (``Status: ok``, ``LaunchState: COLD``,
# ``TotalTime: NNN``) and waits for the activity's first frame. The
# trade-off is that we need to know the launcher activity component
# name; we resolve it via ``cmd package resolve-activity --brief`` and
# cache the answer per-app to keep steady-state launches at one adb
# roundtrip.


_LAUNCHER_CACHE_FILENAME = ".launcher_cache.json"


def _launcher_cache_path(app_dir: Path) -> Path:
    return Path(app_dir) / _LAUNCHER_CACHE_FILENAME


def read_launcher_cache(app_dir: Path | None, package: str) -> Optional[str]:
    """Return the cached launcher activity for ``package`` or ``None``.

    The cache is invalidated implicitly: a mismatched package name means
    the file is stale (the operator changed app or the analysis
    repointed the dossier), so we treat it as a miss. Callers that
    successfully install a new APK should call
    :func:`invalidate_launcher_cache` to drop the file outright — the
    launcher activity may have changed in the new version.
    """
    if app_dir is None or not package:
        return None
    path = _launcher_cache_path(app_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("package") != package:
        return None
    activity = data.get("launcher_activity")
    return activity if isinstance(activity, str) and "/" in activity else None


def write_launcher_cache(
    app_dir: Path | None, package: str, activity: str,
) -> None:
    """Persist ``activity`` for ``package`` in the per-app sidecar cache.

    Best-effort: write failures (read-only filesystem, full disk) are
    swallowed because the cache is only an optimisation — a missing
    cache just means the next launch resolves again.
    """
    if app_dir is None or not package or not activity:
        return
    if "/" not in activity:
        return
    path = _launcher_cache_path(app_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"package": package, "launcher_activity": activity}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def invalidate_launcher_cache(app_dir: Path | None) -> None:
    """Drop the launcher activity cache. Call after ``adb install`` succeeds.

    A reinstall could change the launcher activity (e.g. the developer
    renamed ``MainActivity`` between builds). Resolving fresh on the
    next launch is cheap and safer than serving a stale entry that
    points at an activity the new APK doesn't expose.
    """
    if app_dir is None:
        return
    path = _launcher_cache_path(app_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # Best-effort — if we couldn't unlink, the next launch will
        # serve the stale entry and re-resolution will catch the miss
        # via the mismatch path above (which is also our normal "stale"
        # signal). Better than crashing the install flow.
        pass


# Matches the *component* line from ``cmd package resolve-activity --brief``
# output. The header lines look like ``priority=0 preferredOrder=0 ...``,
# the component line looks like ``com.example.weakbank.low/.MainActivity``
# or ``com.example.weakbank.low/com.example.weakbank.MainActivity``.
_RESOLVE_ACTIVITY_COMPONENT_RE = re.compile(
    r"^\s*([a-zA-Z][\w.]*\/[a-zA-Z._][\w.$]*)\s*$",
    re.MULTILINE,
)


async def _resolve_launcher_activity(
    package: str,
    timeout: float = ADB_RESOLVE_ACTIVITY_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Run ``adb shell cmd package resolve-activity --brief <pkg>``.

    Returns ``{ok, activity, error}``. ``activity`` is the fully-qualified
    component (``com.foo/.Bar``) on success, ``None`` otherwise.

    On Android 7+ (the AndroScan emulator floor) ``cmd package`` is
    always available. On the rare miss (older custom build, missing
    Google services framework, or PackageManager mid-rebuild after a
    factory reset) the caller falls back to the legacy monkey-based
    launcher.
    """
    if not package:
        return {"ok": False, "activity": None, "error": "package required"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "shell", "cmd", "package", "resolve-activity",
            "--brief", package,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"ok": False, "activity": None, "error": "adb not on PATH"}
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return {"ok": False, "activity": None,
                "error": f"resolve-activity timed out after {timeout:.0f}s"}
    out = (out_b or b"").decode(errors="replace")
    err = (err_b or b"").decode(errors="replace").strip()
    if proc.returncode != 0:
        return {"ok": False, "activity": None,
                "error": err[:300] or f"resolve-activity exited {proc.returncode}"}
    # ``--brief`` output (Android 9+):
    #   priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true
    #   com.example.weakbank.low/.MainActivity
    # Older Android sometimes only prints the component (no header). We
    # take the *last* matching line to avoid accidentally picking up a
    # header that happens to contain a "/".
    matches = _RESOLVE_ACTIVITY_COMPONENT_RE.findall(out)
    if not matches:
        return {"ok": False, "activity": None,
                "error": f"no LAUNCHER activity in output: {out.strip()[:200]!r}"}
    activity = matches[-1].strip()
    return {"ok": True, "activity": activity, "error": None}


# Parses ``am start -W`` output. The format is stable across Android 7+:
#   Starting: Intent { ... }
#   Status: ok                       <-- success marker
#   LaunchState: COLD                <-- COLD / WARM / HOT / RELAUNCH
#   Activity: com.example/.MainActivity
#   TotalTime: 678                   <-- ms; useful for cold-start diag
#   WaitTime: 712
#   Complete
# Failure variants:
#   Status: error
#   Error type 3
#   Error: Activity class {com.example/.MainActivity} does not exist.
_AM_START_STATUS_RE = re.compile(r"^Status:\s*(.+?)\s*$", re.MULTILINE)
_AM_START_LAUNCH_STATE_RE = re.compile(r"^LaunchState:\s*(\S+)\s*$", re.MULTILINE)
_AM_START_TOTAL_TIME_RE = re.compile(r"^TotalTime:\s*(\d+)\s*$", re.MULTILINE)
_AM_START_ERROR_RE = re.compile(r"^Error:\s*(.+?)\s*$", re.MULTILINE)


def _parse_am_start_output(stdout: str) -> dict[str, Any]:
    """Extract ``status``, ``launch_state``, ``total_time_ms``, ``error``.

    ``status`` is the literal ``Status:`` value (``"ok"`` for success,
    anything else is failure). When ``Status:`` is absent we fall back
    to the ``Error:`` line if present, otherwise return ``status=None``
    so the caller can decide based on exit code alone.
    """
    status_match = _AM_START_STATUS_RE.search(stdout)
    status = status_match.group(1).strip() if status_match else None
    state_match = _AM_START_LAUNCH_STATE_RE.search(stdout)
    launch_state = state_match.group(1).strip() if state_match else None
    time_match = _AM_START_TOTAL_TIME_RE.search(stdout)
    total_time_ms: Optional[int] = None
    if time_match:
        try:
            total_time_ms = int(time_match.group(1))
        except ValueError:
            total_time_ms = None
    error_match = _AM_START_ERROR_RE.search(stdout)
    error_msg = error_match.group(1).strip() if error_match else None
    return {
        "status": status,
        "launch_state": launch_state,
        "total_time_ms": total_time_ms,
        "error": error_msg,
    }


async def _adb_am_start(
    package: str, activity: str, timeout: float = ADB_LAUNCH_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Run ``adb shell am start -W -n <activity>``.

    Returns ``{ok, exit_code, stdout, stderr, status, launch_state,
    total_time_ms, error}``. Success requires both ``Status: ok`` *and*
    a clean exit code so a malformed/missing parser hit doesn't claim
    success on a failed launch.
    """
    if not package or not activity:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "status": None, "launch_state": None, "total_time_ms": None,
                "error": "package and activity required"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "shell", "am", "start", "-W",
            "-a", "android.intent.action.MAIN",
            "-c", "android.intent.category.LAUNCHER",
            "-n", activity,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "status": None, "launch_state": None, "total_time_ms": None,
                "error": "adb not on PATH"}
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "status": None, "launch_state": None, "total_time_ms": None,
                "error": f"am start timed out after {timeout:.0f}s"}
    out = (out_b or b"").decode(errors="replace").strip()
    err = (err_b or b"").decode(errors="replace").strip()
    parsed = _parse_am_start_output(out)
    status_ok = (parsed["status"] or "").lower() == "ok"
    success = proc.returncode == 0 and status_ok
    return {
        "ok": success,
        "exit_code": proc.returncode,
        "stdout": out[:2000],
        "stderr": err[:2000],
        "status": parsed["status"],
        "launch_state": parsed["launch_state"],
        "total_time_ms": parsed["total_time_ms"],
        "error": (
            None if success
            else (parsed["error"] or err[:300] or out[:300] or "am start failed")
        ),
    }


async def _is_package_running(
    package: str, timeout: float = ADB_PIDOF_TIMEOUT_SEC,
) -> Optional[int]:
    """Return the PID of ``package`` if running, else ``None``.

    Single-shot ``adb shell pidof -s <pkg>``. Used as the ground-truth
    verifier for :func:`adb_launch_package` — regardless of what
    ``am start`` or ``monkey`` printed, if the process is alive the
    launch succeeded. Conversely, if the process is *not* alive after
    a "successful" am start, something killed it on startup (crash,
    SELinux denial, signature mismatch on reinstall) and the wizard
    should report that instead of a green ✓.
    """
    if not package:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "shell", "pidof", "-s", package,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return None
    try:
        out_b, _err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return None
    if proc.returncode != 0:
        return None
    token = (out_b or b"").decode(errors="replace").strip().split()
    if not token:
        return None
    try:
        return int(token[0])
    except ValueError:
        return None


async def _verify_running_with_retries(package: str) -> Optional[int]:
    """Poll :func:`_is_package_running` a handful of times.

    ``am start -W`` already waits for the activity's first frame, so the
    process *should* be alive by the time we get here — but on a slow
    cold start (fresh install, encrypted /data, low-RAM device) the
    fork can lag the visible activity by half a second or so. Five
    retries × 0.5 s = 2.5 s ceiling, which is shorter than any failure
    path the user would notice.
    """
    for _ in range(_PIDOF_VERIFY_RETRIES):
        pid = await _is_package_running(package)
        if pid is not None:
            return pid
        await asyncio.sleep(_PIDOF_VERIFY_INTERVAL_SEC)
    return None


# ---------------------------------------------------------------------------
# Legacy ``monkey``-based launcher — fallback only
#
# Kept for the rare case where ``cmd package resolve-activity`` doesn't
# return a usable component (older Android, PackageManager mid-rebuild,
# unusual app with multiple equal-priority LAUNCHERs that resolve to a
# disambiguation chooser). The previous AM-not-ready retry hack was
# removed: with ``sys.boot_completed=1`` already gating the wizard's
# install/launch steps, the binder race is effectively impossible, and
# the heuristic was a source of false-positive failures on builds where
# ``monkey`` defaults to verbose mode.


async def _adb_monkey_launch(
    package: str, timeout: float = ADB_LAUNCH_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Single ``monkey`` invocation; success = ``Events injected:`` token.

    Positive-signal detection only: we look for the canonical success
    marker (``Events injected: N``) rather than trying to enumerate the
    failure markers. Any output without the success token + a clean
    exit code is treated as failure (the caller still verifies via
    ``pidof`` so a truly successful launch with weird output isn't lost).
    """
    if not package:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "error": "package required"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "shell", "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER", "1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "error": "adb not on PATH"}
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "error": f"monkey timed out after {timeout:.0f}s"}
    out = (out_b or b"").decode(errors="replace").strip()
    err = (err_b or b"").decode(errors="replace").strip()
    blob = (out + "\n" + err).lower()
    success_marker = re.search(r"events injected:\s*\d+", blob)
    explicit_failure = (
        "** monkey aborted" in blob
        or "no activities found to run" in blob
    )
    success = (
        proc.returncode == 0
        and success_marker is not None
        and not explicit_failure
    )
    return {
        "ok": success,
        "exit_code": proc.returncode,
        "stdout": out[:2000],
        "stderr": err[:2000],
        "error": None if success else (err[:300] or out[:300] or "monkey failed"),
    }


# ---------------------------------------------------------------------------
# Public launcher API


async def adb_launch_package(
    package: str,
    timeout: float = ADB_LAUNCH_TIMEOUT_SEC,
    *,
    app_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Launch ``package`` on the connected device.

    Three-stage pipeline:

    1. **Resolve LAUNCHER activity.** Cached in
       ``apps/<app_id>/.launcher_cache.json`` (sidecar JSON), resolved
       on miss via ``cmd package resolve-activity --brief``. Pass
       ``app_dir`` to enable caching; ``None`` means no caching.
    2. **Start the activity** via ``am start -W -n <pkg>/<act>``. The
       ``-W`` flag waits for the first frame and prints structured
       output (``Status: ok``, ``TotalTime: NNN``) we can parse
       deterministically — no string heuristics. If activity resolution
       failed in step 1 we fall back to the legacy ``monkey`` launcher
       (also a single attempt; the boot-completion gate elsewhere
       eliminates the binder race the old retry hack covered).
    3. **Verify** with ``pidof <package>``. This is the ground truth:
       regardless of what ``am start`` or ``monkey`` printed, if the
       process is alive the launch worked, and if it isn't the launch
       failed (and the wizard surfaces that even when the upstream
       command claimed success — covers crash-on-startup cases).

    Returns ``{ok, activity, launch_state, total_time_ms, pid,
    used_monkey_fallback, exit_code, stdout, stderr, error}``.
    """
    if not package:
        return {
            "ok": False, "activity": None, "launch_state": None,
            "total_time_ms": None, "pid": None,
            "used_monkey_fallback": False, "exit_code": None,
            "stdout": "", "stderr": "", "error": "package required",
        }

    # --- 1. Resolve launcher activity (cached) ---
    activity = read_launcher_cache(app_dir, package)
    cache_hit = activity is not None
    resolve_err: Optional[str] = None
    if not activity:
        resolved = await _resolve_launcher_activity(package)
        if resolved["ok"]:
            activity = resolved["activity"]
            write_launcher_cache(app_dir, package, activity)
        else:
            resolve_err = resolved["error"]

    # --- 2. Launch via am start (fallback to monkey if no activity) ---
    if activity:
        am_result = await _adb_am_start(package, activity, timeout=timeout)
        used_monkey_fallback = False
        launch_ok = am_result["ok"]
        launch_state = am_result["launch_state"]
        total_time_ms = am_result["total_time_ms"]
        exit_code = am_result["exit_code"]
        stdout = am_result["stdout"]
        stderr = am_result["stderr"]
        launch_err = am_result["error"]
        # If am start failed because the cached activity is stale (e.g.
        # the new APK renamed MainActivity), invalidate and re-resolve
        # *once* — covers the "you reinstalled the app and forgot to
        # tell us" case without a permanent broken state.
        if (
            not launch_ok
            and cache_hit
            and launch_err
            and "does not exist" in launch_err.lower()
        ):
            invalidate_launcher_cache(app_dir)
            re_resolved = await _resolve_launcher_activity(package)
            if re_resolved["ok"] and re_resolved["activity"] != activity:
                activity = re_resolved["activity"]
                write_launcher_cache(app_dir, package, activity)
                am_result = await _adb_am_start(package, activity, timeout=timeout)
                launch_ok = am_result["ok"]
                launch_state = am_result["launch_state"]
                total_time_ms = am_result["total_time_ms"]
                exit_code = am_result["exit_code"]
                stdout = am_result["stdout"]
                stderr = am_result["stderr"]
                launch_err = am_result["error"]
    else:
        monkey_result = await _adb_monkey_launch(package, timeout=timeout)
        used_monkey_fallback = True
        launch_ok = monkey_result["ok"]
        launch_state = None
        total_time_ms = None
        exit_code = monkey_result["exit_code"]
        stdout = monkey_result["stdout"]
        stderr = monkey_result["stderr"]
        launch_err = monkey_result["error"]
        if not launch_ok and resolve_err:
            launch_err = f"{launch_err or 'launch failed'} (resolve: {resolve_err})"

    # --- 3. Ground-truth verification via pidof ---
    pid = await _verify_running_with_retries(package)
    verified_running = pid is not None

    # Final verdict: pidof wins. If the process is alive the launch
    # worked even if the upstream command was confused; if the process
    # is dead the launch failed even if am start said "Status: ok"
    # (e.g. instant crash on startup).
    if verified_running:
        success = True
        error: Optional[str] = None
    else:
        success = False
        if launch_ok:
            error = "am start reported success but the app is not running"
        else:
            error = launch_err or "launch failed"

    return {
        "ok": success,
        "activity": activity,
        "launch_state": launch_state,
        "total_time_ms": total_time_ms,
        "pid": pid,
        "verified_running": verified_running,
        "used_monkey_fallback": used_monkey_fallback,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "error": error,
    }
