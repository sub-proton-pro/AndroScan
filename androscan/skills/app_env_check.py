"""Exploit-verification skill: check device(s), emulator, and app installed via adb."""

import shutil
import subprocess
from typing import Any

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="app_env_check",
    description="Check that an emulator/device is available, is an emulator (ro.kernel.qemu=1), and the given package is installed. Optionally checks if app is running and in foreground, and brings it to foreground. Use device_serial if multiple devices; pass run_logger for run.log and spinner.",
    params_schema={
        "package": "Android package name (e.g. com.example.app)",
        "device_serial": "Optional. ADB device serial (e.g. emulator-5554). Required when multiple devices are attached.",
        "run_logger": "Optional. RunLogger for run.log and spinner (task_update with \\r for overwrite).",
    },
    tier="exploit",
)


def _run_adb(serial: str | None, *args: str, timeout: int = 10) -> subprocess.CompletedProcess:
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise subprocess.TimeoutExpired(cmd, timeout) from None


def _list_devices() -> tuple[list[dict[str, str]], str]:
    """Return (devices, error_detail). error_detail is non-empty if adb failed."""
    try:
        proc = _run_adb(None, "devices", "-l")
    except subprocess.TimeoutExpired:
        return [], "adb devices timed out"
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:200]
        return [], f"adb devices failed (exit {proc.returncode}): {stderr}"
    devices = []
    for line in (proc.stdout or "").strip().splitlines():
        if not line.strip() or line.startswith("List of"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            serial, state = parts[0], parts[1]
            if state == "device":
                devices.append({"serial": serial, "state": state})
    return devices, ""


def execute(params: dict[str, Any], context: SkillContext) -> SkillResult:
    """Check adb, list devices, optionally verify emulator and app installed."""
    if not shutil.which("adb"):
        return SkillResult(
            success=False,
            data=None,
            text="[app_env_check] adb not found. Install Android SDK platform-tools and ensure adb is on PATH.",
        )
    package = (params.get("package") or "").strip()
    if not package:
        return SkillResult(
            success=False,
            data=None,
            text="[app_env_check] package is required.",
        )
    device_serial = (params.get("device_serial") or "").strip() or None

    devices, adb_error = _list_devices()
    if not devices and adb_error:
        return SkillResult(
            success=False,
            data={"devices": [], "reason": "adb_failed", "detail": adb_error},
            text=f"[app_env_check] {adb_error}",
        )
    if not devices:
        return SkillResult(
            success=False,
            data={"devices": [], "reason": "no_devices"},
            text="[app_env_check] No devices attached. Run 'adb devices -l' and connect an emulator or device.",
        )
    if len(devices) > 1 and not device_serial:
        return SkillResult(
            success=False,
            data={
                "devices": [d["serial"] for d in devices],
                "reason": "multiple_devices_choose_one",
                "message": "Multiple devices attached. Pass device_serial (e.g. emulator-5554) to select one.",
            },
            text="[app_env_check] Multiple devices attached. Pass device_serial in params to choose one.",
        )
    serial = device_serial if device_serial else devices[0]["serial"]
    if device_serial and not any(d["serial"] == device_serial for d in devices):
        return SkillResult(
            success=False,
            data={"devices": [d["serial"] for d in devices], "requested": device_serial},
            text=f"[app_env_check] Device {device_serial!r} not in attached list.",
        )

    try:
        proc = _run_adb(serial, "shell", "getprop", "ro.kernel.qemu")
    except subprocess.TimeoutExpired:
        return SkillResult(success=False, data=None, text="[app_env_check] Timed out checking if device is an emulator (getprop).")
    qemu_out = (proc.stdout or "").strip() if proc.returncode == 0 else ""
    is_emulator = qemu_out == "1"

    try:
        proc = _run_adb(serial, "shell", "pm", "path", package)
    except subprocess.TimeoutExpired:
        return SkillResult(success=False, data=None, text=f"[app_env_check] Timed out checking if {package!r} is installed (pm path).")
    pm_out = (proc.stdout or "").strip() if proc.returncode == 0 else ""
    app_installed = "package:" in pm_out
    app_path = pm_out.replace("package:", "").strip() if app_installed else None

    data: dict[str, Any] = {
        "device_serial": serial,
        "emulator": is_emulator,
        "app_installed": app_installed,
        "package": package,
    }
    if app_path:
        data["app_path"] = app_path

    if not is_emulator:
        return SkillResult(
            success=False,
            data=data,
            text="[app_env_check] Device is not an emulator (ro.kernel.qemu != 1). Use an emulator for exploit verification.",
        )

    run_logger = params.get("run_logger")
    if run_logger:
        run_logger.task_update(f"Emulated device found: {serial}")
        run_logger.exploit_stage(f"Emulated device found: {serial}")

    if not app_installed:
        return SkillResult(
            success=False,
            data=data,
            text=f"[app_env_check] Package {package!r} is not installed on {serial}. Install the APK first.",
        )

    # Check if app is running (pidof <package>)
    def _log_spinner(text: str) -> None:
        if run_logger:
            run_logger.task_update("\r" + text)

    try:
        proc = _run_adb(serial, "shell", "pidof", package)
    except subprocess.TimeoutExpired:
        proc = None
    pid_out = (proc.stdout or "").strip() if proc and proc.returncode == 0 else ""
    app_running = bool(pid_out)
    data["app_running"] = app_running
    if run_logger:
        run_logger.exploit_stage(f"Checking if app is running (pidof {package}): {'running (pid(s) ' + pid_out + ')' if app_running else 'not running'}.")
        _log_spinner("App running: yes." if app_running else "App running: no.")

    # Check if app is in foreground (dumpsys activity activities: mResumedActivity or mFocusedApp)
    in_foreground = False
    if app_running:
        try:
            proc = _run_adb(serial, "shell", "dumpsys", "activity", "activities", timeout=15)
        except subprocess.TimeoutExpired:
            proc = None
        dumpsys_out = (proc.stdout or "") if proc and proc.returncode == 0 else ""
        for line in dumpsys_out.splitlines():
            if ("mResumedActivity" in line or "mFocusedApp" in line) and package in line:
                in_foreground = True
                break
    data["app_in_foreground"] = in_foreground
    if run_logger:
        run_logger.exploit_stage(f"Checking if app is in foreground (dumpsys activity): {'yes' if in_foreground else 'no'}.")
        _log_spinner("App in foreground: yes." if in_foreground else "App in foreground: no.")

    # If not in foreground, bring to foreground (monkey -p package -c LAUNCHER 1)
    brought_to_foreground = False
    if app_running and not in_foreground:
        if run_logger:
            run_logger.exploit_stage("Bringing app to foreground (monkey -c LAUNCHER 1)...")
            _log_spinner("Bringing app to foreground...")
        try:
            proc = _run_adb(serial, "shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1", timeout=10)
        except subprocess.TimeoutExpired:
            proc = None
        brought_to_foreground = proc is not None and proc.returncode == 0
        data["brought_to_foreground"] = brought_to_foreground
        if run_logger:
            run_logger.exploit_stage(f"Bring to foreground: {'success' if brought_to_foreground else 'failed (exit ' + str(proc.returncode) + ')'}.")
            _log_spinner("Foreground: done." if brought_to_foreground else "Foreground: failed.")
    else:
        data["brought_to_foreground"] = False

    if run_logger:
        _log_spinner("App env check OK.")

    return SkillResult(
        success=True,
        data=data,
        text=f"[app_env_check] OK: {serial} (emulator), {package} installed."
        + (f" App running: {app_running}, in foreground: {in_foreground}, brought_to_foreground: {brought_to_foreground}" if run_logger else ""),
    )
