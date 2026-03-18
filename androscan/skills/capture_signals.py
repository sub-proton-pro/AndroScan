"""Exploit-verification skill: capture volatile signals then non-volatile (adb/logcat/dumpsys/screenshot).

Reads signal types and volatile ordering from vuln_module_skills_signals.json (profile).
Stub signal types (e.g. network_capture) return placeholder content. Real captures use adb.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from androscan.internal.vuln_signals_config import get_module_profiles, get_signal_type_metadata
from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="capture_signals",
    description="Capture signals (logcat, dumpsys, screenshot, etc.) for a vuln module profile. Volatile signals first, then non-volatile. Uses device_serial and package; stub types (e.g. network_capture) return placeholder.",
    params_schema={
        "device_serial": "ADB device serial (e.g. emulator-5554)",
        "package": "Android package name (for logcat filtering if needed)",
        "vuln_module": "Vuln module name (e.g. exported_components)",
        "profile": "Profile name (e.g. exported_activity) to get signal_types and volatile list",
        "file_prefix": "Optional. Prefix for captured files (e.g. before, after). Default: capture",
    },
    tier="exploit",
)

STUB_MESSAGE = "[stub] Signal type not implemented; placeholder for verification workflow."


def _run_adb(serial: str, *args: str, timeout: int = 15, binary: bool = False) -> subprocess.CompletedProcess:
    cmd = ["adb", "-s", serial] + list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=not binary,
        timeout=timeout,
    )


def _capture_logcat(serial: str, _package: str, _context: SkillContext) -> str:
    proc = _run_adb(serial, "shell", "logcat", "-d", "-t", "300")
    if proc.returncode != 0:
        return f"[logcat failed: exit {proc.returncode}]"
    return (proc.stdout or "")[-50000:]  # limit size


def _capture_dumpsys_activity(serial: str, _package: str, _context: SkillContext) -> str:
    proc = _run_adb(serial, "shell", "dumpsys", "activity")
    if proc.returncode != 0:
        return f"[dumpsys activity failed: exit {proc.returncode}]"
    return (proc.stdout or "")[-100000:]


def _capture_dumpsys_window(serial: str, _package: str, _context: SkillContext) -> str:
    proc = _run_adb(serial, "shell", "dumpsys", "window")
    if proc.returncode != 0:
        return f"[dumpsys window failed: exit {proc.returncode}]"
    return (proc.stdout or "")[-50000:]


def _capture_screenshot(serial: str, _package: str, context: SkillContext, file_prefix: str) -> str:
    run_folder = Path(context.run_folder)
    run_folder.mkdir(parents=True, exist_ok=True)
    out_path = run_folder / f"{file_prefix}_screenshot.png"
    proc = _run_adb(serial, "exec-out", "screencap", "-p", timeout=10, binary=True)
    if proc.returncode != 0:
        return f"[screencap failed: exit {proc.returncode}]"
    try:
        out_path.write_bytes(proc.stdout or b"")
        return str(out_path)
    except OSError as e:
        return f"[screenshot write failed: {e}]"


def _capture_signal(
    signal_type: str,
    serial: str,
    package: str,
    context: SkillContext,
    file_prefix: str,
    metadata: dict[str, dict[str, Any]],
) -> str:
    if metadata.get(signal_type, {}).get("stub"):
        return STUB_MESSAGE
    if signal_type == "logcat":
        return _capture_logcat(serial, package, context)
    if signal_type in ("dumpsys_activity",):
        return _capture_dumpsys_activity(serial, package, context)
    if signal_type in ("dumpsys_window", "window_stack"):
        return _capture_dumpsys_window(serial, package, context)
    if signal_type == "screenshot":
        return _capture_screenshot(serial, package, context, file_prefix)
    return STUB_MESSAGE


def execute(params: dict[str, Any], context: SkillContext) -> SkillResult:
    """Capture signals for the given profile: volatile first, then non-volatile."""
    if not shutil.which("adb"):
        return SkillResult(
            success=False,
            data=None,
            text="[capture_signals] adb not found. Install Android SDK platform-tools and ensure adb is on PATH.",
        )
    device_serial = (params.get("device_serial") or "").strip()
    if not device_serial:
        return SkillResult(
            success=False,
            data=None,
            text="[capture_signals] device_serial is required.",
        )
    package = (params.get("package") or "").strip()
    if not package:
        return SkillResult(
            success=False,
            data=None,
            text="[capture_signals] package is required.",
        )
    vuln_module = (params.get("vuln_module") or "").strip()
    if not vuln_module:
        return SkillResult(
            success=False,
            data=None,
            text="[capture_signals] vuln_module is required (e.g. exported_components).",
        )
    profile = (params.get("profile") or "").strip()
    if not profile:
        return SkillResult(
            success=False,
            data=None,
            text="[capture_signals] profile is required (e.g. exported_activity).",
        )
    file_prefix = (params.get("file_prefix") or "capture").strip() or "capture"

    profiles = get_module_profiles(vuln_module)
    profile_config = profiles.get(profile)
    if not profile_config:
        return SkillResult(
            success=False,
            data=None,
            text=f"[capture_signals] Unknown profile {profile!r} for module {vuln_module!r}.",
        )
    signal_types = list(profile_config.get("signal_types") or [])
    volatile_list = list(profile_config.get("volatile") or [])
    metadata = get_signal_type_metadata()

    volatile_order = [t for t in signal_types if t in volatile_list]
    non_volatile_order = [t for t in signal_types if t not in volatile_list]
    ordered_types = volatile_order + non_volatile_order

    signals: dict[str, str] = {}
    for signal_type in ordered_types:
        signals[signal_type] = _capture_signal(
            signal_type, device_serial, package, context, file_prefix, metadata
        )

    n = len(signals)
    types_str = ", ".join(ordered_types) if ordered_types else "none"
    log_summary = f"Captured {n} signals: {types_str}"
    spinner_text = "Capturing signals..."
    return SkillResult(
        success=True,
        data={
            "signals": signals,
            "volatile_captured": volatile_order,
            "non_volatile_captured": non_volatile_order,
        },
        text=f"[capture_signals] {log_summary}",
        log_summary=log_summary,
        spinner_text=spinner_text,
    )
