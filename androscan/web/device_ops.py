"""Pure-function helpers for the "Bring device online" wizard.

These wrap the small set of external commands the wizard needs:

* :func:`find_emulator_binary` — locate the ``emulator`` CLI (it lives under
  ``$ANDROID_HOME/emulator/`` and is *not* on PATH on most installations,
  unlike ``adb``).
* :func:`list_avds` — ``emulator -list-avds`` parsed into a list.
* :func:`spawn_emulator_detached` — fire-and-forget launch of an AVD.
* :func:`adb_install_apk` — ``adb install -r <apk>``.
* :func:`adb_launch_package` — ``adb shell monkey -p <pkg> -c LAUNCHER 1``.

All command-running helpers are async, timeboxed, and **never raise** —
they always return a structured ``(ok, info)`` so the route handlers can
turn them straight into JSON without try/except gymnastics.

Kept separate from :mod:`androscan.web.app` so they're trivially unit-
testable (no FastAPI / event-loop scaffolding) and reusable from skills.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any, Optional


# Wall-clock caps. The emulator spawn returns immediately (detached); the
# wizard polls /api/device/status separately for boot completion.
ADB_INSTALL_TIMEOUT_SEC = 120.0
ADB_LAUNCH_TIMEOUT_SEC = 15.0
ADB_PM_PATH_TIMEOUT_SEC = 5.0
EMULATOR_LIST_TIMEOUT_SEC = 5.0


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


async def adb_launch_package(package: str, timeout: float = ADB_LAUNCH_TIMEOUT_SEC) -> dict[str, Any]:
    """``adb shell monkey -p <pkg> -c android.intent.category.LAUNCHER 1``."""
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
                "error": f"launch timed out after {timeout:.0f}s"}
    out = (out_b or b"").decode(errors="replace").strip()
    err = (err_b or b"").decode(errors="replace").strip()
    # monkey prints "Events injected: 1" on success and a "monkey aborted"
    # error on failure (e.g. no LAUNCHER intent). rc==0 + no aborted line.
    aborted = "aborted" in (out + err).lower() or "no activities found" in (out + err).lower()
    success = proc.returncode == 0 and not aborted
    return {
        "ok": success,
        "exit_code": proc.returncode,
        "stdout": out[:2000],
        "stderr": err[:2000],
        "error": None if success else (err[:300] or out[:300] or "launch failed"),
    }
