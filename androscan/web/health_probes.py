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
) -> dict[str, Any]:
    """Is ``frida-server`` running on the connected device?

    Strategy: ``adb shell pidof frida-server`` (cheap, no root needed for
    ``pidof`` on standard emulator images). Returns ``{ok, running, pid,
    error}``. Probe never raises; falls back to ``ok=False`` when adb
    itself is unreachable so the Settings card can show the same red dot
    for "no device" and "no frida-server" without the aggregator
    needing special-case logic.

    The Hook Lab adapter (:mod:`androscan.adapters.frida_client`) talks
    to ``frida-server`` over the standard host-to-device USB transport;
    if this probe is red there is no point hammering the device with
    ``frida.attach`` calls.
    """
    rc, out, err = await _run(adb_cmd, "shell", "pidof", "frida-server", timeout=timeout)
    tok = (out or "").strip().split()
    pid: Optional[int] = None
    if tok:
        try:
            pid = int(tok[0])
        except ValueError:
            pid = None
    running = pid is not None and rc == 0
    return {
        "ok": running,
        "running": running,
        "pid": pid,
        "error": None if running else ((err or "").strip()[:300] or "frida-server not running on device"),
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
