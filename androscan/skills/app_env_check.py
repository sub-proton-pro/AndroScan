"""Exploit-verification skill: check device(s), emulator, and app installed via adb."""

import shutil
import subprocess
from typing import Any

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="app_env_check",
    description="Check that an emulator/device is available, is an emulator (ro.kernel.qemu=1), and the given package is installed. Use device_serial if multiple devices; otherwise returns device list for user to choose.",
    params_schema={
        "package": "Android package name (e.g. com.example.app)",
        "device_serial": "Optional. ADB device serial (e.g. emulator-5554). Required when multiple devices are attached.",
    },
    tier="exploit",
)


def _run_adb(serial: str | None, *args: str, timeout: int = 10) -> subprocess.CompletedProcess:
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _list_devices() -> list[dict[str, str]]:
    """Return list of {serial, state} for devices in 'device' state."""
    proc = _run_adb(None, "devices", "-l")
    if proc.returncode != 0:
        return []
    devices = []
    for line in (proc.stdout or "").strip().splitlines():
        if not line.strip() or line.startswith("List of"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            serial, state = parts[0], parts[1]
            if state == "device":
                devices.append({"serial": serial, "state": state})
    return devices


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

    devices = _list_devices()
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

    proc = _run_adb(serial, "shell", "getprop", "ro.kernel.qemu")
    qemu_out = (proc.stdout or "").strip() if proc.returncode == 0 else ""
    is_emulator = qemu_out == "1"

    proc = _run_adb(serial, "shell", "pm", "path", package)
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
    if not app_installed:
        return SkillResult(
            success=False,
            data=data,
            text=f"[app_env_check] Package {package!r} is not installed on {serial}. Install the APK first.",
        )

    return SkillResult(
        success=True,
        data=data,
        text=f"[app_env_check] OK: {serial} (emulator), {package} installed.",
    )
