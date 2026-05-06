"""Pure-function health probes used by the Settings tab status aggregators.

Each probe is small, async, and timeboxed. Probes never raise — they always
return a dict with ``ok: bool`` plus probe-specific keys, and stash any
unexpected error string under ``error``. This lets the status aggregator
:mod:`androscan.web.status_routes` build a full status payload with
``asyncio.gather(..., return_exceptions=False)`` and trust every entry to
be JSON-serialisable.

The probes are pure so they're trivial to unit-test without standing up the
FastAPI app. The aggregator is the only place where probe results are
combined into UI-shaped payloads.

Categories
----------
* External tools (``probe_tool_version``): adb, jadx, apktool, frida.
* Device reachability (``probe_adb_device``) — gates the per-app device
  probes so we don't fan out 5 doomed adb-shell calls when no emulator is
  attached.
* Device CPU ABI (``probe_device_cpu_abi``) — surfaces the
  ``ro.product.cpu.abi`` value plus its mapping to the Frida release
  filename arch suffix so the Settings tab can render an ABI-aware
  ``frida-server`` install playbook.
* Device root status (``probe_device_root_status``) — predicts
  whether ``adb root`` will succeed (``ro.build.type`` +
  ``ro.debuggable`` + current shell uid) so the install playbook can
  warn the operator before they paste a step that can't possibly
  work on a production / Google Play AVD image.
* Network services (``probe_ollama_tags`` / ``probe_ollama_embed_model``).
* Embed provider (``probe_fastembed_available`` / ``probe_embed_provider``).
* Filesystem (``probe_disk`` / ``probe_path_writable``).
* Per-app on-device probes (``probe_pkg_installed`` / ``probe_pkg_running``
  / ``probe_pkg_uid`` / ``probe_foreground_activity``).
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional


# Per-probe wall-clock caps. These are deliberately tight — the Settings tab
# polls every 15s and a stuck probe would freeze the whole status card.
DEFAULT_TIMEOUT_SEC = 2.0
SHORT_TIMEOUT_SEC = 1.0
HTTP_TIMEOUT_SEC = 1.5

# Heuristic disk-space warning threshold.
LOW_DISK_GB_WARNING = 2.0


# ---------------------------------------------------------------------------
# Subprocess helpers


async def _run(
    *argv: str,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    stdin: Optional[bytes] = None,
) -> tuple[int, str, str]:
    """Run ``argv`` and return ``(rc, stdout, stderr)``.

    Returns ``(-1, "", "<err>")`` on missing binary or timeout — never raises.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        return -1, "", f"binary not found: {argv[0] if argv else '?'} ({e})"
    except OSError as e:
        return -1, "", f"spawn error: {e}"

    try:
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(input=stdin), timeout=timeout
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return -1, "", f"timeout after {timeout:.1f}s"

    return (
        proc.returncode if proc.returncode is not None else -1,
        (out_b or b"").decode(errors="replace"),
        (err_b or b"").decode(errors="replace"),
    )


# ---------------------------------------------------------------------------
# External tool probes


def _which(cmd: str) -> Optional[str]:
    """``shutil.which`` returning ``None`` for empty / missing input."""
    if not cmd:
        return None
    return shutil.which(cmd)


async def probe_tool_version(
    cmd: str,
    *args: str,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    parse_first_token: bool = False,
) -> dict[str, Any]:
    """Probe an external tool: presence on PATH plus a version string.

    ``cmd`` is the configured binary name (so users can override e.g.
    ``jadx_cmd: /opt/jadx/bin/jadx`` in YAML). ``args`` are the version-
    eliciting flags (``"--version"`` for most things, ``"version"`` for
    apktool's older releases, etc.).

    ``parse_first_token=True`` extracts the first whitespace-delimited
    token of stdout's first non-empty line, useful for outputs like
    ``"adb version 1.0.41\\n..."`` where we want just ``"1.0.41"``.
    """
    path = _which(cmd)
    if not path:
        return {"ok": False, "found": False, "cmd": cmd, "path": None, "version": None,
                "error": f"{cmd!r} not on PATH"}
    rc, out, err = await _run(path, *args, timeout=timeout)
    raw = (out or err or "").strip()
    if not raw:
        return {"ok": False, "found": True, "cmd": cmd, "path": path, "version": None,
                "error": f"no version output (rc={rc})"}

    # Take the first non-empty line; some tools dump multi-line banners.
    first_line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), raw)
    version: Optional[str] = first_line
    if parse_first_token:
        for tok in first_line.split():
            if any(c.isdigit() for c in tok):
                version = tok.strip(",;()")
                break
    return {
        "ok": rc == 0 or rc is None,
        "found": True,
        "cmd": cmd,
        "path": path,
        "version": version,
        "error": None if rc == 0 else f"rc={rc}",
    }


async def probe_adb_version(adb_cmd: str = "adb") -> dict[str, Any]:
    """Convenience wrapper. ``adb version`` (no dashes) prints "Android Debug Bridge version X.Y.Z"."""
    return await probe_tool_version(adb_cmd, "version", parse_first_token=False)


async def probe_jadx_version(jadx_cmd: str = "jadx") -> dict[str, Any]:
    return await probe_tool_version(jadx_cmd, "--version", parse_first_token=True)


async def probe_apktool_version(apktool_cmd: str = "apktool") -> dict[str, Any]:
    # Apktool uses a `version` subcommand (not `--version`); the GNU-style
    # flag prints the banner but exits non-zero, which would mark the card
    # red even on a healthy install.
    return await probe_tool_version(apktool_cmd, "version", parse_first_token=True)


async def probe_adb_device(timeout: float = SHORT_TIMEOUT_SEC) -> dict[str, Any]:
    """Is anything reachable over adb? Uses ``adb get-state``.

    ``adb get-state`` is a fast, package-independent check that exits 0 with
    stdout ``device`` when a device or emulator is fully booted and ready.
    For unauthorised / offline / booting devices it prints a different state
    (``unauthorized``, ``offline``, ``bootloader``, ``recovery``, ...). When
    nothing is attached at all it exits non-zero with stderr like
    ``adb: no devices/emulators found``.

    The card is ``ok`` only when state is exactly ``device``. The serial of
    the active device (when there's exactly one) is read separately via
    ``adb get-serialno`` so the UI can show *which* device is attached.
    """
    rc, out, err = await _run("adb", "get-state", timeout=timeout)
    state = (out or "").strip() or None
    if rc != 0 or not state:
        return {
            "ok": False,
            "connected": False,
            "state": state,
            "serial": None,
            "error": (err or out or "no device").strip()[:300] or "no device",
        }
    serial: Optional[str] = None
    if state == "device":
        sn_rc, sn_out, _ = await _run("adb", "get-serialno", timeout=timeout)
        if sn_rc == 0:
            tok = (sn_out or "").strip()
            if tok and tok.lower() != "unknown":
                serial = tok
    return {
        "ok": state == "device",
        "connected": True,
        "state": state,
        "serial": serial,
        "error": None if state == "device" else f"device state={state!r}",
    }


async def probe_frida_version(frida_cmd: str = "frida") -> dict[str, Any]:
    """Frida CLI on the host. Hook Lab needs this once it lands."""
    return await probe_tool_version(frida_cmd, "--version", parse_first_token=True)


async def probe_frida_server(
    adb_cmd: str = "adb",
    timeout: float = SHORT_TIMEOUT_SEC,
    *,
    frida_ps_cmd: str = "frida-ps",
) -> dict[str, Any]:
    """Is ``frida-server`` reachable on the connected device?

    Three-layer strategy, cheapest first; later layers only fire when
    earlier ones come up empty:

    1. ``adb shell pidof frida-server`` — exact-name match. Hits when
       the operator installed via the AndroScan playbook (binary at
       ``/data/local/tmp/frida-server``) or any other path where the
       file is named exactly ``frida-server``. Single adb call → fast
       steady-state cost for the canonical install.
    2. ``adb shell ps -A`` with a ``comm`` prefix match. Catches the
       common "ran the GitHub release binary as-is" case, e.g.
       ``/data/local/tmp/frida-server-16.7.19-android-arm64``. The
       kernel ``comm`` field is capped at 15 chars
       (``TASK_COMM_LEN``) but always starts with ``frida-server``,
       so the prefix match holds.
    3. **Host-side** ``frida-ps -U``. Authoritative — uses the same
       wire protocol as the operator's manual ``frida-ps -U`` check.
       Fires when (a) the on-device binary was deliberately renamed
       to evade ``/proc/*/comm`` greps (a common stealth tactic when
       testing apps that detect frida by name), or (b) the operator
       is running ``frida-gadget`` injected into the target app —
       there's no separate ``frida-server`` process at all in that
       mode. Returns ``running=True, pid=None`` because no on-device
       PID is observable; the version-skew probe handles the
       missing pid by falling through to its known-paths candidate
       list.

    Returns ``{ok, running, pid, uid, helper_running, detection, error}``:

    * ``ok`` mirrors ``running``.
    * ``pid`` is the on-device process id when layers 1 or 2 hit;
      ``None`` when only layer 3 confirms reachability.
    * ``uid`` is the device-side username the server runs as (``"root"``,
      ``"shell"``, ``"u0_a123"``, ``...``); ``None`` when we couldn't
      determine it (layer 3 only, or ``ps -A`` failed). The Settings
      card surfaces a warning when this isn't ``"root"`` because
      attaching into other-uid app processes requires CAP_SYS_PTRACE
      ambient capabilities, which only root has on stock Android — a
      shell-uid frida-server can list processes (``frida-ps`` works)
      but every ``device.attach(<pid>)`` against an app fails with
      ``unable to connect to remote frida-server: closed`` once the
      per-process helper hits the ptrace barrier.
    * ``helper_running`` is ``True`` when ``re.frida.helper`` (the
      per-attach ptrace shim frida-server forks) is observable in
      ``ps -A``. Useful for diagnosing the "server is up but helper
      crashes mid-handshake" failure mode (typically a SELinux denial
      or a version-skew handshake error). The helper is short-lived
      between attaches — a ``False`` here just means "no active
      attach right now", not "broken".
    * ``detection`` is ``"pidof" | "ps" | "frida-ps" | None`` so
      callers can tell which layer succeeded (the Settings card uses
      it to label the host-confirmed case as "running (host-confirmed)"
      rather than the misleading "pid ?").

    Probe never raises; falls back to ``ok=False`` when every layer
    fails so the Settings card can show the same red dot for "no
    device" and "no frida-server" without the aggregator needing
    special-case logic.

    The Hook Lab adapter (:mod:`androscan.adapters.frida_client`) talks
    to ``frida-server`` over the standard host-to-device USB transport;
    if this probe is red there is no point hammering the device with
    ``frida.attach`` calls.
    """
    detection: Optional[str] = None

    # ----- Layer 1: pidof (exact-name match) -----
    rc, out, err = await _run(adb_cmd, "shell", "pidof", "frida-server", timeout=timeout)
    tok = (out or "").strip().split()
    pid: Optional[int] = None
    if tok:
        try:
            pid = int(tok[0])
            detection = "pidof"
        except ValueError:
            pid = None

    # ----- Layer 2: ps -A (always; enriches uid + helper, also serves
    # as fallback PID source for renamed/versioned binaries) -----
    #
    # Previously this only ran when layer 1 missed; we now always run
    # it because the uid + helper signals it provides are first-class
    # diagnostics (the Settings card warns when uid != "root", and the
    # mid-handshake "closed" failure mode is most reliably detected by
    # noticing that the helper isn't present). Cost is one extra adb
    # round-trip per poll (~50ms on an emulator); the value is being
    # able to tell "running but unprivileged" apart from "running
    # correctly" without any extra operator action.
    fallback_err = ""
    uid: Optional[str] = None
    helper_running = False
    rc2, out2, err2 = await _run(
        adb_cmd, "shell", "ps", "-A", timeout=timeout,
    )
    fallback_err = (err2 or "").strip()
    if rc2 == 0:
        for line in (out2 or "").splitlines():
            parts = line.split()
            # Typical toybox `ps -A` row: USER PID PPID VSZ RSS WCHAN ADDR S NAME.
            # Last column is the (truncated) command name; column 0 is
            # USER, column 1 is PID. Header row is skipped naturally
            # because parts[-1] is the literal "NAME" (no startswith
            # match) and `int(parts[1])` would raise on "PID" anyway.
            if len(parts) < 2:
                continue
            comm = parts[-1]
            # frida-server row(s) — extract pid (when layer 1 missed)
            # and uid. Skips zombies whose `ps` rendering wraps the
            # name in brackets ("[frida-server]") because the
            # ``startswith`` check intentionally requires the bare
            # name. We always overwrite uid with the live row's value
            # so a transient zombie ahead of the live row in the
            # listing doesn't poison the read.
            if comm.startswith("frida-server"):
                try:
                    row_pid = int(parts[1])
                except ValueError:
                    continue
                if pid is None:
                    pid = row_pid
                    detection = "ps"
                if row_pid == pid:
                    uid = parts[0] or None
                continue
            # re.frida.helper / re.frida.helper-32 / re.frida.helper-64
            # — frida-server forks one of these per attach to do the
            # ptrace work in a separate process (so a crashed helper
            # doesn't take down the whole server). Presence here just
            # means at least one attach is in flight; absence is the
            # quiescent steady state, NOT an error signal by itself.
            if comm.startswith("re.frida.helper"):
                helper_running = True

    # ----- Layer 3: host-side frida-ps -U (authoritative) -----
    # Fires only when the device-side checks miss — covers stealth-renamed
    # binaries and frida-gadget setups. Uses a generous timeout because
    # `frida-ps` enumerates every process on the device (can take a
    # few seconds on a busy emulator); cap at max(timeout * 4, 4.0)
    # so a sluggish enumeration doesn't block the aggregator forever
    # while still being cheap enough for the Settings card's 15s poll.
    host_confirmed = False
    host_err = ""
    if pid is None:
        host_timeout = max(timeout * 4.0, 4.0)
        rc3, out3, err3 = await _run(
            frida_ps_cmd, "-U", timeout=host_timeout,
        )
        # rc3 == 0 with non-empty stdout means the USB transport is
        # alive AND at least one process was enumerated.
        if rc3 == 0 and (out3 or "").strip():
            host_confirmed = True
            detection = "frida-ps"
        elif rc3 != -1:
            # Real frida-ps failure (transport unreachable, no USB
            # device, etc.) — propagate its stderr. Skip rc3 == -1
            # which means ``frida-ps`` isn't installed on the host:
            # that's not actionable for "is frida-server running on
            # the device" and would otherwise mask the canonical
            # "frida-server not running on device" fallback message.
            host_err = (err3 or "").strip()

    running = pid is not None or host_confirmed
    # Error precedence when nothing succeeded: pidof stderr first (most
    # informative when adb itself is broken — "no devices/emulators
    # found"), then ps stderr, then frida-ps stderr, then a generic
    # fallback string.
    err_msg = (err or "").strip() or fallback_err or host_err
    return {
        "ok": running,
        "running": running,
        "pid": pid,
        "uid": uid,
        "helper_running": helper_running,
        "detection": detection,
        "error": None if running else (err_msg[:300] or "frida-server not running on device"),
    }


# Mapping from Android primary ABI (``ro.product.cpu.abi``) to the
# architecture suffix used by Frida release filenames
# (``frida-server-X.Y.Z-android-<ARCH>.xz`` on
# https://github.com/frida/frida/releases). Used by the Settings tab to
# synthesise an ABI-aware install playbook when ``frida-server`` isn't
# running on the device. ``armeabi`` (legacy 32-bit ARM v5/v6) maps to
# the same Frida arch as ``armeabi-v7a`` because Frida only ships an
# ``android-arm`` binary for 32-bit ARM.
_ABI_TO_FRIDA_ARCH: dict[str, str] = {
    "arm64-v8a": "android-arm64",
    "armeabi-v7a": "android-arm",
    "armeabi": "android-arm",
    "x86_64": "android-x86_64",
    "x86": "android-x86",
}


async def probe_device_cpu_abi(
    adb_cmd: str = "adb",
    timeout: float = SHORT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Read the device's primary ABI via ``getprop ro.product.cpu.abi``.

    Used by the Settings tab's ``frida-server`` card to synthesise an
    ABI-aware install playbook (download URL + push / install / verify
    commands) when the on-device server isn't running. We deliberately
    keep this as its own probe rather than folding it into
    :func:`probe_frida_server` so the aggregator can still know the
    device ABI even on a green card — useful for diagnostics and for a
    future "the device ABI changed since the last run" warning.

    Returns ``{ok, abi, frida_arch, error}``. ``frida_arch`` is the
    suffix used in Frida release filenames (e.g. ``android-arm64`` for
    ``arm64-v8a``); it is ``None`` when the ABI is not in our mapping
    table (in which case ``error`` carries the unmapped ABI string so
    the UI can degrade to "see https://github.com/frida/frida/releases"
    instead of a synthesised download link). Probe never raises;
    ``ok=False`` whenever we couldn't read the ABI off the device for
    any reason — the UI's hint render checks ``device_abi`` directly
    rather than relying on ``ok``.
    """
    rc, out, err = await _run(
        adb_cmd, "shell", "getprop", "ro.product.cpu.abi", timeout=timeout
    )
    abi = (out or "").strip() or None
    if rc != 0 or not abi:
        return {
            "ok": False,
            "abi": None,
            "frida_arch": None,
            "error": (err or out or "could not read ro.product.cpu.abi").strip()[:300] or "no device",
        }
    frida_arch = _ABI_TO_FRIDA_ARCH.get(abi)
    return {
        "ok": frida_arch is not None,
        "abi": abi,
        "frida_arch": frida_arch,
        "error": None if frida_arch is not None
                 else f"unknown ABI {abi!r} (no Frida arch mapping)",
    }


# Build types that allow ``adb root`` to succeed. ``user`` (production
# / Google Play AVD images) refuses with "adbd cannot run as root in
# production builds"; ``userdebug`` (the AOSP / Google APIs AVD
# variants) and ``eng`` (engineering builds) flip adbd to uid 0 on
# request.
_ROOTABLE_BUILD_TYPES: frozenset[str] = frozenset({"userdebug", "eng"})


async def probe_device_root_status(
    adb_cmd: str = "adb",
    timeout: float = SHORT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Predict whether the connected device can be rooted via ``adb root``.

    Frida requires the on-device server to run as root, which in turn
    means either (a) the default adb shell user is *already* root
    (uid 0 — rare on stock images, common on Magisk-rooted phones or
    custom ``eng`` ROMs) or (b) ``adb root`` will succeed, which only
    works on ``userdebug`` / ``eng`` build types with
    ``ro.debuggable=1``. Production (``user``) builds — including the
    standard Google Play AVD images — refuse ``adb root`` with
    *"adbd cannot run as root in production builds"*, so the
    Settings-tab install playbook needs to warn the operator before
    they paste step 4.

    We deliberately do **not** invoke ``adb root`` itself here — it
    has the side effect of restarting adbd, which would tear down
    in-flight WebSocket streams (``/ws/mirror`` / ``/ws/logcat`` /
    Frida trace WS). Instead we read ``ro.build.type`` +
    ``ro.debuggable`` + the current adb-shell uid via ``id``; together
    these are sufficient to predict whether ``adb root`` *would*
    succeed without paying the side effect.

    Returns ``{ok, rooted, can_adb_root, current_uid, build_type,
    debuggable, error}``:

    * ``rooted`` — True when the default adb shell already runs as
      uid 0 (the strongest "no further setup needed" signal).
    * ``can_adb_root`` — True when ``adb root`` is expected to
      succeed (build is ``userdebug`` or ``eng`` AND
      ``ro.debuggable=1``) **or** the shell is already root.
    * ``current_uid`` — int uid the default adb shell runs as
      (typically 2000 = ``shell`` on locked-down images).
    * ``build_type`` — ``"user"`` (production), ``"userdebug"``,
      or ``"eng"`` (engineering).

    All four data fields are ``None`` when the probe couldn't reach
    the device at all (no emulator attached / adb wedged).
    """
    # Single shell roundtrip — 3 sub-commands joined with ``;`` so we
    # don't pay 3 separate ``adb shell`` startups (~100 ms each on a
    # busy emulator). ``echo ---`` between blocks is the parser
    # boundary; it's safe because none of the values we read can
    # contain that string.
    rc, out, err = await _run(
        adb_cmd, "shell",
        "getprop ro.build.type; echo ---; getprop ro.debuggable; echo ---; id",
        timeout=timeout,
    )
    if rc != 0:
        return {
            "ok": False,
            "rooted": None,
            "can_adb_root": None,
            "current_uid": None,
            "build_type": None,
            "debuggable": None,
            "error": (err or out or "could not query device root status").strip()[:300] or "no device",
        }

    parts = (out or "").split("---")
    if len(parts) < 3:
        return {
            "ok": False,
            "rooted": None,
            "can_adb_root": None,
            "current_uid": None,
            "build_type": None,
            "debuggable": None,
            "error": f"unexpected getprop output: {out!r:.300}",
        }

    build_type = parts[0].strip() or None
    debuggable_raw = parts[1].strip()
    debuggable: Optional[bool] = (
        debuggable_raw == "1" if debuggable_raw in ("0", "1") else None
    )
    id_line = parts[2].strip()

    current_uid: Optional[int] = None
    # ``id`` outputs e.g. ``uid=2000(shell) gid=2000(shell) groups=...``.
    m = re.search(r"uid=(\d+)", id_line)
    if m:
        try:
            current_uid = int(m.group(1))
        except ValueError:
            current_uid = None

    rooted = current_uid == 0
    can_adb_root = bool(rooted) or (
        build_type in _ROOTABLE_BUILD_TYPES and debuggable is True
    )

    return {
        "ok": True,
        "rooted": rooted,
        "can_adb_root": can_adb_root,
        "current_uid": current_uid,
        "build_type": build_type,
        "debuggable": debuggable,
        "error": None,
    }


def _normalize_frida_version(raw: Optional[str]) -> Optional[str]:
    """Strip the ``"frida X.Y.Z"`` banner down to the bare ``"X.Y.Z"`` token.

    The host CLI prints just ``"16.4.10"`` for ``frida --version``; the
    on-device server prints ``"frida-server 16.4.10\\n"``. We normalise
    both so :func:`_compare_major_minor` doesn't have to care.
    """
    if not raw:
        return None
    for tok in str(raw).split():
        if any(c.isdigit() for c in tok):
            return tok.strip(",;()")
    return None


def _major_minor(v: Optional[str]) -> Optional[tuple[int, int]]:
    """Best-effort ``(major, minor)`` parse; returns ``None`` for nonsense input.

    Frida version strings are dotted ints (e.g. ``"16.4.10"``); we ignore
    everything past minor because the wire-protocol contract Frida
    advertises is "compatible across same major" (with very rare
    exceptions during pre-release minors, which we surface as a warning).
    """
    if not v:
        return None
    parts = v.split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


# Well-known install locations for ``frida-server`` on Android. These
# are tried in order when neither (a) the bare command name resolves
# via the device shell ``$PATH`` nor (b) the running pid's
# ``/proc/<pid>/exe`` symlink could be read. The first entry matches
# what AndroScan's own Settings-tab install playbook (see
# ``frontend/src/tabs/SettingsTab.tsx``) instructs operators to push,
# which is also the canonical location in the official Frida Android
# docs (https://frida.re/docs/android/). The remaining entries cover
# system installs from custom Magisk modules / vendor ROMs; we keep
# the list short so the worst-case cost is a handful of cheap
# ``adb shell`` round-trips on the 15s Settings-tab poll cadence.
_FRIDA_SERVER_DEVICE_PATHS: tuple[str, ...] = (
    "/data/local/tmp/frida-server",
    "/system/bin/frida-server",
    "/system/xbin/frida-server",
)


async def _resolve_frida_server_exe_via_pid(
    adb_cmd: str,
    pid: Optional[int],
    timeout: float,
) -> Optional[str]:
    """Look up the on-device ``frida-server`` binary path from ``/proc/<pid>/exe``.

    Returns ``None`` when ``pid`` is missing, when the readlink call
    fails (process gone, /proc unreadable for the calling shell uid,
    SELinux denial), or when the link target is empty. Never raises.
    """
    if pid is None:
        return None
    rc, out, _err = await _run(
        adb_cmd, "shell", "readlink", f"/proc/{int(pid)}/exe", timeout=timeout,
    )
    if rc != 0:
        return None
    target = (out or "").strip()
    return target or None


async def probe_frida_version_skew(
    host: dict[str, Any],
    adb_cmd: str = "adb",
    timeout: float = SHORT_TIMEOUT_SEC,
    *,
    pid: Optional[int] = None,
) -> dict[str, Any]:
    """Compare host ``frida`` CLI version with device ``frida-server`` version.

    ``host`` is the dict returned by :func:`probe_frida_version` — we
    read ``host["version"]`` and compare to ``adb shell <bin>
    --version`` where ``<bin>`` is resolved as follows:

    1. If ``pid`` is supplied (the running ``frida-server`` pid from
       :func:`probe_frida_server`), try ``readlink /proc/<pid>/exe``
       to learn the *exact* binary that's running. This is the most
       accurate source — there can never be version skew between what
       we probe and what the host CLI talks to.
    2. Try the bare command name ``frida-server`` (works when the
       binary is on the device shell's ``$PATH``, e.g. installs to
       ``/system/bin``).
    3. Fall through to ``_FRIDA_SERVER_DEVICE_PATHS`` in order, which
       starts with ``/data/local/tmp/frida-server`` — the path
       AndroScan's own install playbook recommends and the canonical
       Frida Android install location. Without this fallback the
       common "I followed the playbook" case showed a red card with
       ``frida-server: inaccessible or not found`` even though
       :func:`probe_frida_server` had already found the running pid
       (``pidof`` matches process names regardless of ``$PATH``).

    A major-version mismatch is fatal (Frida's wire protocol is not
    stable across majors); a minor mismatch is a warning.

    Returns ``{ok, host_version, device_version, severity, error}``
    where ``severity`` ∈ ``{None, "minor", "major"}``. ``ok`` is
    ``False`` when severity is ``"major"`` or when *every* candidate
    binary path failed — the SettingsTab card uses ``ok`` to choose
    the dot colour.
    """
    host_version = _normalize_frida_version(host.get("version") if isinstance(host, dict) else None)

    # Build the candidate list: pid-resolved exe (if any), then bare
    # command name, then the well-known install paths. ``dict.fromkeys``
    # preserves insertion order while deduping — so a pid that resolves
    # to ``/data/local/tmp/frida-server`` doesn't make us probe that
    # path twice.
    candidates: list[str] = []
    resolved = await _resolve_frida_server_exe_via_pid(adb_cmd, pid, timeout)
    if resolved:
        candidates.append(resolved)
    candidates.append("frida-server")
    candidates.extend(_FRIDA_SERVER_DEVICE_PATHS)
    candidates = list(dict.fromkeys(candidates))

    last_rc: int = -1
    last_out: str = ""
    last_err: str = ""
    device_version: Optional[str] = None
    for binary in candidates:
        rc, out, err = await _run(
            adb_cmd, "shell", binary, "--version", timeout=timeout
        )
        last_rc, last_out, last_err = rc, (out or ""), (err or "")
        raw_dev = (out or err or "").strip()
        parsed = _normalize_frida_version(raw_dev)
        if rc == 0 and parsed:
            device_version = parsed
            break

    if device_version is None:
        # Surface the *last* shell error (the broadest fallback path
        # we tried), prefixed with the candidate so the operator can
        # tell "no frida-server anywhere" from "this specific path
        # was unreadable".
        msg = (last_err or last_out or "could not read frida-server --version on device").strip()
        if not msg:
            msg = f"could not read frida-server --version on device (rc={last_rc})"
        return {
            "ok": False,
            "host_version": host_version,
            "device_version": None,
            "severity": None,
            "error": msg[:300],
        }

    h = _major_minor(host_version)
    d = _major_minor(device_version)
    if h is None or d is None:
        # Couldn't parse one side; report what we got but don't claim ok.
        return {
            "ok": False,
            "host_version": host_version,
            "device_version": device_version,
            "severity": None,
            "error": "unparsable version string",
        }
    if h[0] != d[0]:
        return {
            "ok": False,
            "host_version": host_version,
            "device_version": device_version,
            "severity": "major",
            "error": (
                f"major version mismatch (host {host_version}, device {device_version}); "
                "Frida wire protocol is incompatible — upgrade one side"
            ),
        }
    if h[1] != d[1]:
        return {
            "ok": True,  # functional, but flag in the card
            "host_version": host_version,
            "device_version": device_version,
            "severity": "minor",
            "error": (
                f"minor version skew (host {host_version}, device {device_version}); "
                "instrumentation should still work but consider matching"
            ),
        }
    return {
        "ok": True,
        "host_version": host_version,
        "device_version": device_version,
        "severity": None,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Ollama / LLM probes


async def probe_ollama_tags(
    base_url: str,
    timeout: float = HTTP_TIMEOUT_SEC,
) -> dict[str, Any]:
    """``GET {base_url}/api/tags`` to verify the daemon is reachable.

    We avoid ``requests`` (which would be sync and block the event loop) and
    instead spawn a tiny ``curl`` if available; otherwise fall back to a
    sync probe in a thread.
    """
    base = base_url.rstrip("/")
    url = f"{base}/api/tags"
    started = time.perf_counter()
    # Prefer curl for non-blocking subprocess; falls back to urllib in a thread.
    if shutil.which("curl"):
        rc, out, err = await _run(
            "curl", "-fsS", "-m", str(int(max(1, timeout))), url, timeout=timeout + 0.5
        )
        ms = int((time.perf_counter() - started) * 1000)
        if rc != 0:
            return {"ok": False, "reachable": False, "url": url, "ping_ms": ms,
                    "models": [], "error": (err or out or f"curl rc={rc}").strip()[:300]}
        try:
            import json as _json
            data = _json.loads(out)
            models = [m.get("name") for m in (data.get("models") or []) if m.get("name")]
            return {"ok": True, "reachable": True, "url": url, "ping_ms": ms,
                    "models": models, "error": None}
        except Exception as e:
            return {"ok": False, "reachable": True, "url": url, "ping_ms": ms,
                    "models": [], "error": f"unparsable response: {e}"}

    # Fallback path (no curl): use urllib in a worker thread.
    def _sync() -> tuple[bool, list[str], Optional[str]]:
        import json as _json
        from urllib.error import URLError
        from urllib.request import Request, urlopen
        try:
            with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as resp:
                body = resp.read().decode(errors="replace")
            data = _json.loads(body)
            models = [m.get("name") for m in (data.get("models") or []) if m.get("name")]
            return True, models, None
        except URLError as e:
            return False, [], f"urlopen: {e}"
        except Exception as e:
            return False, [], f"{type(e).__name__}: {e}"

    ok, models, err = await asyncio.to_thread(_sync)
    ms = int((time.perf_counter() - started) * 1000)
    return {"ok": ok, "reachable": ok, "url": url, "ping_ms": ms,
            "models": models, "error": err}


def model_present(tags: dict[str, Any], model: str) -> bool:
    """Return True if ``model`` appears in an ``/api/tags`` response.

    Ollama tags include a ``:tag`` suffix (e.g. ``qwen3.5:35b``); the user
    might write the bare model name. We accept both exact match and a
    prefix match up to the first colon to be forgiving.
    """
    if not tags.get("models") or not model:
        return False
    model_norm = model.strip().lower()
    bare = model_norm.split(":", 1)[0]
    for name in tags["models"]:
        n = (name or "").strip().lower()
        if n == model_norm or n == bare or n.split(":", 1)[0] == bare:
            return True
    return False


# ---------------------------------------------------------------------------
# llama.cpp / llama-server health probe (LCP.3 / DEC-027)


async def probe_llamacpp(
    base_url: str,
    timeout: float = HTTP_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Liveness + model-list probe for ``llama-server`` (LCP.3, DEC-028).

    Two-step probe so the Settings-tab status card can distinguish
    ``_DOWN`` / ``_PARTIAL_UP`` / ``_UP`` cleanly:

    1. **Liveness.** ``GET {base_origin}/health`` — purpose-built
       liveness endpoint that returns ``{"status": "ok"}`` when the
       server is fully up, ``{"status": "loading model"}`` (HTTP 503)
       during the cold-start mmap window, or fails entirely when the
       process isn't listening. DEC-028 (LCP.6 follow-up) switched
       from probing ``/v1/models`` for liveness because ``/health`` is
       smaller, semantically correct, and surfaces the "loading"
       transient state explicitly rather than leaving operators to
       infer it from an empty ``data: []`` list.
    2. **Model list.** ``GET {base_url}/models`` — only fired when the
       liveness step succeeded, populates the ``models`` field used
       by :func:`llamacpp_model_present` to verify the operator's
       configured ``llamacpp_model`` label matches the GGUF actually
       loaded at server startup.

    The ``base_url`` already includes the OpenAI-compat ``/v1`` suffix
    (e.g. ``http://127.0.0.1:8033/v1``) so ``/models`` is appended
    directly, while ``/health`` is a server-root endpoint at
    ``http://127.0.0.1:8033/health`` (one level above ``/v1``).

    Returns a dict structurally parallel to :func:`probe_ollama_tags`
    so the Settings tab's status-card renderer stays generic:

        {
            "ok":         bool,   # server fully up AND >=1 model loaded
            "reachable":  bool,   # server reachable (any /health response)
            "url":        str,    # /v1/models URL (preserved for back-compat
                                  # with status-card "open in browser" links)
            "ping_ms":    int,    # total elapsed across both steps
            "models":     list[str],   # model ids from /v1/models
            "error":      str | None,
        }

    Three-state classification matters for the operator UX:

    * ``_DOWN``       — ``ok=False, reachable=False`` — server isn't
      running. Operator's first move is the recommended startup line
      from ``global_config.yaml`` / ``README.md``.
    * ``_PARTIAL_UP`` — ``ok=False, reachable=True, models=[]`` —
      common during model-load lag on cold starts (large GGUFs can
      take 10-30s to mmap into RAM). Operator just needs to wait;
      the next probe (Settings polls every 15s) flips to ``_UP``.
    * ``_UP``         — ``ok=True, reachable=True, models=[<id>]`` —
      ready to serve requests.
    """
    base = (base_url or "").strip().rstrip("/")
    # /v1/models is the configured-suffix path; /health lives at the
    # server root one level above /v1. Strip a trailing /v1 (if any)
    # to derive the health URL — operators sometimes paste base_url
    # without the /v1, in which case base IS the root and we use it.
    health_root = base[:-3] if base.endswith("/v1") else base
    health_url = f"{health_root}/health"
    models_url = f"{base}/models"
    started = time.perf_counter()

    async def _http_get(url: str) -> tuple[bool, str, Optional[str]]:
        """``GET url`` returning ``(ok, body_text, error_or_None)``.

        Mirrors :func:`probe_ollama_tags`'s curl-first / urllib-fallback
        pattern so we never block the event loop on a stuck socket.
        Both /health (small JSON) and /models (small JSON) are loopback
        calls; total wall-clock budget across both steps stays under
        the single ``timeout`` ceiling because the urllib socket-level
        timeout is enforced per-call.
        """
        if shutil.which("curl"):
            rc, out, err = await _run(
                "curl", "-fsS", "-m", str(int(max(1, timeout))), url,
                timeout=timeout + 0.5,
            )
            if rc != 0:
                return False, "", (err or out or f"curl rc={rc}").strip()[:300]
            return True, out, None

        def _sync() -> tuple[bool, str, Optional[str]]:
            from urllib.error import URLError
            from urllib.request import Request, urlopen
            try:
                with urlopen(
                    Request(url, headers={"Accept": "application/json"}),
                    timeout=timeout,
                ) as resp:
                    body = resp.read().decode(errors="replace")
                return True, body, None
            except URLError as e:
                return False, "", f"urlopen: {e}"
            except Exception as e:
                return False, "", f"{type(e).__name__}: {e}"

        return await asyncio.to_thread(_sync)

    # Step 1 — liveness via /health.
    ok, health_body, err = await _http_get(health_url)
    if not ok:
        ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False, "reachable": False, "url": models_url, "ping_ms": ms,
            "models": [], "error": err,
        }

    # Parse /health body (small, pure-string-search is fine on the
    # body's "status" field — but JSON is the contract so prefer it).
    health_status: Optional[str] = None
    try:
        import json as _json
        parsed_health = _json.loads(health_body)
        if isinstance(parsed_health, dict):
            health_status = str(parsed_health.get("status") or "").strip().lower()
    except Exception:
        # Some llama-server builds return non-JSON for /health (rare).
        # Treat presence of "ok" in the body as a soft signal.
        if "ok" in health_body.lower():
            health_status = "ok"

    if health_status and health_status != "ok":
        # _PARTIAL_UP — server is up but model is still mmapping (or
        # the slot pool is exhausted). Skip the /v1/models fetch — its
        # ``data`` list will be empty during model load anyway, and we
        # already know enough to render the yellow card.
        ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False, "reachable": True, "url": models_url, "ping_ms": ms,
            "models": [],
            "error": f"llama-server reachable but {health_status!r} "
                     "(cold-start mmap can take 10-30s for large GGUFs)",
        }

    # Step 2 — model list via /v1/models. Only meaningful when /health
    # said "ok"; an empty data list at this point is the unusual case
    # of a server that booted without ``-hf`` / ``-m`` (operator typo)
    # and it should still surface as _PARTIAL_UP rather than green.
    ok2, models_body, err2 = await _http_get(models_url)
    ms = int((time.perf_counter() - started) * 1000)
    if not ok2:
        # /health said ok but /v1/models is unreachable — odd but real
        # (e.g. operator pointed base_url at the wrong port). Surface
        # the /v1/models error to make the misconfiguration obvious.
        return {
            "ok": False, "reachable": True, "url": models_url, "ping_ms": ms,
            "models": [], "error": err2,
        }

    try:
        import json as _json
        parsed = _json.loads(models_body)
    except Exception as e:
        return {
            "ok": False, "reachable": True, "url": models_url, "ping_ms": ms,
            "models": [], "error": f"unparsable response: {e}",
        }

    data = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(data, list):
        return {
            "ok": False, "reachable": True, "url": models_url, "ping_ms": ms,
            "models": [],
            "error": "unexpected /v1/models shape (no 'data' list)",
        }
    models = [
        str(m.get("id"))
        for m in data
        if isinstance(m, dict) and m.get("id")
    ]
    if not models:
        # _PARTIAL_UP — /health said ok but no model labels exposed.
        # Rare but possible (server booted without a model arg).
        return {
            "ok": False, "reachable": True, "url": models_url, "ping_ms": ms,
            "models": [],
            "error": "llama-server reachable but no models loaded yet "
                     "(cold-start mmap can take 10-30s for large GGUFs)",
        }
    return {
        "ok": True, "reachable": True, "url": models_url, "ping_ms": ms,
        "models": models, "error": None,
    }


def llamacpp_model_present(tags: dict[str, Any], model: str) -> bool:
    """Return True if ``model`` matches an entry in a ``probe_llamacpp`` result.

    ``llama-server`` returns whatever model-id was loaded at server
    startup (operator-controlled string, e.g. ``qwen3-27b-q5km``).
    Unlike Ollama there's no canonical ``:tag`` suffix scheme, so this
    helper does a case-insensitive exact match plus a prefix match
    (operator may write a shortened label in Settings).

    When ``model`` is empty (LCP.4 hasn't yet added the
    ``llamacpp_model`` Config field) the helper accepts any loaded
    model — the existence of *any* entry in the ``models`` list is
    enough for the LLM status card to flip green.
    """
    models = tags.get("models") or []
    if not models:
        return False
    if not model:
        return True
    model_norm = model.strip().lower()
    for name in models:
        n = (name or "").strip().lower()
        if n == model_norm or n.startswith(model_norm) or model_norm.startswith(n):
            return True
    return False


# ---------------------------------------------------------------------------
# Embed-provider probes


def probe_fastembed_available() -> dict[str, Any]:
    """Is the ``fastembed`` package importable in this Python env?

    Pure import check — does not download or load a model (which is what
    ``FastEmbedProvider.__init__`` does, and is far too slow for a status
    probe). Combined with :func:`probe_fastembed_model_cache` the user gets
    "installed yes/no" + "default model already pulled yes/no".
    """
    spec = importlib.util.find_spec("fastembed")
    if spec is None:
        return {
            "ok": False,
            "installed": False,
            "error": "fastembed not installed (pip install 'fastembed>=0.3' or pip install -e '.[rag]')",
        }
    return {"ok": True, "installed": True, "module_path": getattr(spec, "origin", None), "error": None}


def probe_fastembed_model_cache(model: str = "BAAI/bge-small-en-v1.5") -> dict[str, Any]:
    """Heuristic: is ``model`` already in the fastembed on-disk cache?

    fastembed stores ONNX downloads under ``~/.cache/fastembed`` (or
    ``$FASTEMBED_CACHE``). We just check for *any* directory matching the
    model name's tail; we don't try to validate the contents.
    """
    cache_root = Path(os.environ.get("FASTEMBED_CACHE") or Path.home() / ".cache" / "fastembed")
    if not cache_root.is_dir():
        return {"ok": False, "cached": False, "cache_root": str(cache_root), "error": None}
    tail = model.rsplit("/", 1)[-1].lower()
    found = False
    try:
        for entry in cache_root.iterdir():
            if tail in entry.name.lower():
                found = True
                break
    except OSError as e:
        return {"ok": False, "cached": False, "cache_root": str(cache_root), "error": str(e)}
    return {"ok": found, "cached": found, "cache_root": str(cache_root), "error": None}


async def probe_embed_provider(provider_name: str, model: str, ollama_base_url: str) -> dict[str, Any]:
    """High-level embed-provider availability probe.

    For ``fastembed``: import + model cache check (no model load).
    For ``ollama``: re-uses :func:`probe_ollama_tags` and looks for the
    model.
    For ``hash``: always available (test fallback).
    """
    name = (provider_name or "").lower()
    if name == "fastembed":
        avail = probe_fastembed_available()
        cache = probe_fastembed_model_cache(model or "BAAI/bge-small-en-v1.5")
        return {
            "ok": bool(avail.get("ok")),
            "provider": "fastembed",
            "model": model or "BAAI/bge-small-en-v1.5",
            "installed": bool(avail.get("installed")),
            "cached": bool(cache.get("cached")),
            "cache_root": cache.get("cache_root"),
            "error": avail.get("error") or cache.get("error"),
        }
    if name == "ollama":
        tags = await probe_ollama_tags(ollama_base_url)
        wanted = model or "nomic-embed-text"
        present = model_present(tags, wanted)
        return {
            "ok": bool(tags.get("ok")) and present,
            "provider": "ollama",
            "model": wanted,
            "reachable": bool(tags.get("reachable")),
            "model_present": present,
            "error": tags.get("error") or (None if present else f"model {wanted!r} not in /api/tags"),
        }
    if name == "hash":
        return {
            "ok": True,
            "provider": "hash",
            "model": "(none)",
            "note": "deterministic test-only provider; vector quality is poor",
            "error": None,
        }
    return {"ok": False, "provider": name, "error": f"unknown provider {name!r}"}


# ---------------------------------------------------------------------------
# Filesystem probes


def probe_disk(path: Path) -> dict[str, Any]:
    """``shutil.disk_usage`` with a low-space warning flag."""
    try:
        usage = shutil.disk_usage(str(path))
    except (FileNotFoundError, OSError) as e:
        return {"ok": False, "path": str(path), "error": str(e)}
    free_gb = usage.free / (1024 ** 3)
    return {
        "ok": free_gb >= LOW_DISK_GB_WARNING,
        "path": str(path),
        "total_gb": round(usage.total / (1024 ** 3), 2),
        "used_gb": round(usage.used / (1024 ** 3), 2),
        "free_gb": round(free_gb, 2),
        "low_space": free_gb < LOW_DISK_GB_WARNING,
        "low_space_threshold_gb": LOW_DISK_GB_WARNING,
        "error": None,
    }


def probe_path_writable(path: Path) -> dict[str, Any]:
    """``os.access(path, W_OK)`` plus existence flag."""
    p = Path(path)
    exists = p.exists()
    writable = exists and os.access(str(p), os.W_OK)
    return {
        "ok": writable,
        "path": str(p),
        "exists": exists,
        "writable": writable,
        "error": None if writable else (None if exists else "does not exist"),
    }


def probe_dir_size(path: Path, *, max_entries: int = 50_000) -> dict[str, Any]:
    """Best-effort recursive directory size in bytes.

    Bails out early after ``max_entries`` direntries to keep the probe cheap
    on huge trees (the ``apps/`` folder can grow several GB across many
    runs). The returned ``truncated`` flag tells the UI when to mark the
    number as "≥".
    """
    p = Path(path)
    if not p.exists():
        return {"ok": False, "path": str(p), "size_bytes": 0, "entries": 0,
                "truncated": False, "error": "does not exist"}
    total = 0
    count = 0
    truncated = False
    try:
        for entry in p.rglob("*"):
            count += 1
            if count > max_entries:
                truncated = True
                break
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError as e:
        return {"ok": False, "path": str(p), "size_bytes": total, "entries": count,
                "truncated": truncated, "error": str(e)}
    return {"ok": True, "path": str(p), "size_bytes": total,
            "size_mb": round(total / (1024 ** 2), 2), "entries": count,
            "truncated": truncated, "error": None}


# ---------------------------------------------------------------------------
# Per-app on-device probes (all use ``adb shell`` and are short-timeout).


async def probe_pkg_installed(package: str, timeout: float = SHORT_TIMEOUT_SEC) -> dict[str, Any]:
    """``adb shell pm path <pkg>`` — empty stdout means not installed."""
    if not package:
        return {"ok": False, "installed": False, "package": "", "error": "no package name"}
    rc, out, err = await _run("adb", "shell", "pm", "path", package, timeout=timeout)
    if rc < 0:
        return {"ok": False, "installed": False, "package": package, "error": err}
    text = (out or "").strip()
    installed = bool(text and text.lower().startswith("package:"))
    return {
        "ok": installed,
        "installed": installed,
        "package": package,
        "apk_path_on_device": text.split(":", 1)[1].strip() if installed else None,
        "error": None if installed else (err or "package not found").strip()[:300] or None,
    }


async def probe_pkg_running(package: str, timeout: float = SHORT_TIMEOUT_SEC) -> dict[str, Any]:
    """``adb shell pidof -s <pkg>`` — first PID, or None if not running.

    ``ok`` reflects whether the package is actually running. We also surface
    adb stderr (e.g. "no devices/emulators found") so the card can tell
    "not running" apart from "couldn't even ask the device".

    Note: ``pidof`` exits 1 when the process isn't found *and* when the adb
    transport itself fails. We only treat empty stdout + clean stderr as a
    benign "not running"; anything in stderr is propagated to ``error``.
    """
    if not package:
        return {"ok": False, "running": False, "package": "", "pid": None, "error": "no package name"}
    rc, out, err = await _run("adb", "shell", "pidof", "-s", package, timeout=timeout)
    tok = (out or "").strip().split()
    pid: Optional[int] = None
    if tok:
        try:
            pid = int(tok[0])
        except ValueError:
            pid = None
    running = pid is not None and rc == 0
    err_txt = (err or "").strip()
    return {
        "ok": running,
        "running": running,
        "package": package,
        "pid": pid,
        "error": None if running else (err_txt[:300] or "package not running"),
    }


async def probe_pkg_uid(package: str, timeout: float = SHORT_TIMEOUT_SEC) -> dict[str, Any]:
    """Resolve the package's stable Linux UID (via ``stat -c %u /data/data/<pkg>``).

    Mirrors the production resolver in :mod:`androscan.web.app` but is
    side-effect-free and timeboxed. Falls back to ``dumpsys package`` like
    the production path does.
    """
    if not package:
        return {"ok": False, "resolved": False, "uid": None, "method": None, "error": "no package name"}
    rc, out, _ = await _run(
        "adb", "shell", "stat", "-c", "%u", f"/data/data/{package}", timeout=timeout
    )
    if rc == 0:
        tok = (out or "").strip()
        if tok.isdigit():
            return {"ok": True, "resolved": True, "uid": int(tok), "method": "stat", "error": None}

    rc, out, _ = await _run(
        "adb", "shell", "dumpsys", "package", package, timeout=timeout + 1.0
    )
    if rc == 0:
        for line in (out or "").splitlines():
            line = line.strip()
            if line.startswith("userId="):
                tail = line.split("=", 1)[1].split()[0]
                if tail.isdigit():
                    return {"ok": True, "resolved": True, "uid": int(tail),
                            "method": "dumpsys", "error": None}
    return {"ok": False, "resolved": False, "uid": None, "method": None,
            "error": "uid not resolvable (logcat will fall back to per-PID filtering)"}


async def probe_foreground_activity(timeout: float = SHORT_TIMEOUT_SEC) -> dict[str, Any]:
    """``adb shell dumpsys activity top`` — extract the topmost activity.

    Used by the per-app status card to confirm the package under test is
    actually in the foreground (otherwise click-to-code mapping will pick
    up handlers from the wrong activity).
    """
    rc, out, err = await _run(
        "adb", "shell", "dumpsys", "activity", "top", timeout=timeout + 0.5
    )
    if rc != 0:
        return {"ok": False, "activity": None, "package": None,
                "error": (err or "dumpsys failed").strip()[:300] or "dumpsys failed"}
    activity: Optional[str] = None
    for line in (out or "").splitlines():
        line = line.strip()
        # Looking for: "ACTIVITY com.foo/.MainActivity ..." (newer) or
        # "    mResumedActivity: ActivityRecord{... com.foo/.MainActivity ...}".
        if line.startswith("ACTIVITY "):
            tok = line.split()
            if len(tok) >= 2 and "/" in tok[1]:
                activity = tok[1]
                break
        if "ResumedActivity" in line and "/" in line:
            for tok in line.replace("{", " ").replace("}", " ").split():
                if "/" in tok and "." in tok:
                    activity = tok
                    break
            if activity:
                break
    pkg = activity.split("/", 1)[0] if activity and "/" in activity else None
    return {"ok": bool(activity), "activity": activity, "package": pkg, "error": None}


async def probe_uiautomator_dump(timeout: float = DEFAULT_TIMEOUT_SEC) -> dict[str, Any]:
    """``adb shell uiautomator dump /dev/tty`` — does the hierarchy dump?

    Required for click-to-code mapping. Returns ok=True if we get any XML
    output back (full XML parsing is the responsibility of inspect_map).
    """
    rc, out, err = await _run(
        "adb", "exec-out", "uiautomator", "dump", "/dev/tty", timeout=timeout + 1.0
    )
    has_xml = "<hierarchy" in (out or "")
    return {
        "ok": has_xml,
        "rc": rc,
        "has_xml": has_xml,
        "error": None if has_xml else (err or out or "no <hierarchy> in output").strip()[:300],
    }


# ---------------------------------------------------------------------------
# Misc small probes


def probe_python_env() -> dict[str, Any]:
    """Snapshot of the interpreter + the optional extras we care about."""
    extras_ok: dict[str, bool] = {}
    for mod in ("fastembed", "numpy", "yaml", "fastapi", "uvicorn"):
        extras_ok[mod] = importlib.util.find_spec(mod) is not None
    return {
        "ok": True,
        "python_version": sys.version.split()[0],
        "executable": sys.executable,
        "platform": sys.platform,
        "modules": extras_ok,
    }


def probe_apk_sha_drift(apk_path: Optional[str], stored_sha: Optional[str]) -> dict[str, Any]:
    """Re-hash the APK on disk and compare to the sha stored in ``app_meta.json``.

    A mismatch typically means the APK was rebuilt or replaced since
    analysis ran; the user should re-run analysis to refresh the dossier
    and decompile cache.
    """
    if not apk_path:
        return {"ok": False, "drift": None, "stored_sha": stored_sha, "current_sha": None,
                "error": "no apk_path in app_meta.json"}
    p = Path(apk_path)
    if not p.is_file():
        return {"ok": False, "drift": None, "stored_sha": stored_sha, "current_sha": None,
                "error": f"APK not found at {apk_path}"}
    if not stored_sha:
        return {"ok": False, "drift": None, "stored_sha": None, "current_sha": None,
                "error": "no apk_sha256 in app_meta.json"}
    # Lazy import to avoid pulling app_meta into hot import paths.
    from androscan.internal.app_meta import compute_apk_sha256
    try:
        current = compute_apk_sha256(p)
    except OSError as e:
        return {"ok": False, "drift": None, "stored_sha": stored_sha, "current_sha": None,
                "error": f"hash failed: {e}"}
    drift = current != stored_sha
    return {
        "ok": not drift,
        "drift": drift,
        "stored_sha": stored_sha,
        "current_sha": current,
        "apk_size_bytes": p.stat().st_size,
        "error": "APK on disk no longer matches stored sha; re-run analysis" if drift else None,
    }
