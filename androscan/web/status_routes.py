"""Aggregator endpoints for the Settings tab status panels.

Two endpoints:

* ``GET /api/status/global`` — host / tools / LLM / embed / disk / process.
* ``GET /api/status/apps/{app_id}`` — package on device, decompile, RAG,
  per-app config drift, etc.

Both fan out to :mod:`androscan.web.health_probes` via ``asyncio.gather``
so a slow probe (e.g. an unreachable Ollama daemon) only adds *its own*
timeout to the response, not the sum of all probe timeouts.

The response shape is intentionally flat-ish so the React layer can render
it as a grid of "status cards" without each card needing custom plumbing.
Every card has at minimum: ``ok: bool``, ``label: str``, optional ``hint``,
``error``, and probe-specific extras.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from androscan.config import Config
from androscan.config.loader import (
    discover_config_path,
    effective_sources,
    env_overrides,
)
from androscan.internal.app_meta import load_app_meta
from androscan.web.decompile_cache import (
    cache_root_for as decompile_cache_root,
    get_status as decompile_status,
)
from androscan.web.health_probes import (
    probe_adb_device,
    probe_adb_version,
    probe_apk_sha_drift,
    probe_apktool_version,
    probe_disk,
    probe_embed_provider,
    probe_foreground_activity,
    probe_device_cpu_abi,
    probe_device_root_status,
    probe_frida_server,
    probe_frida_version,
    probe_frida_version_skew,
    probe_jadx_version,
    probe_llamacpp,
    probe_ollama_tags,
    probe_path_writable,
    probe_pkg_installed,
    probe_pkg_running,
    probe_pkg_uid,
    probe_python_env,
    probe_uiautomator_dump,
    llamacpp_model_present,
    model_present,
)
from androscan.web.per_app_settings import (
    apk_overrides_summary,
    load_app_settings,
)

logger = logging.getLogger(__name__)


# Server-side cache to avoid hammering adb / Ollama if the React UI mounts
# multiple status cards at once. Keyed by endpoint + app_id.
_STATUS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_STATUS_CACHE_TTL_SEC = 3.0


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _STATUS_CACHE.get(key)
    if entry is None:
        return None
    ts, payload = entry
    if (time.time() - ts) > _STATUS_CACHE_TTL_SEC:
        _STATUS_CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    _STATUS_CACHE[key] = (time.time(), payload)


def invalidate_status_cache() -> None:
    """Called by the settings_routes after a config save / reload."""
    _STATUS_CACHE.clear()


# ---------------------------------------------------------------------------
# Global aggregator


async def _gather_global(config: Config, apps_root: Path) -> dict[str, Any]:
    """Run all global probes in parallel and shape the response."""
    started = time.perf_counter()

    adb_p = probe_adb_version("adb")
    jadx_p = probe_jadx_version(getattr(config, "jadx_cmd", "jadx"))
    apktool_p = probe_apktool_version(getattr(config, "apktool_cmd", "apktool"))
    frida_p = probe_frida_version("frida")
    frida_server_p = probe_frida_server("adb")
    # Always probe the device ABI in parallel — used by the Settings
    # tab's frida-server card to synthesise an ABI-aware install
    # playbook when the on-device server isn't running. Cheap when no
    # device is attached (fails fast with the same SHORT_TIMEOUT_SEC as
    # the other adb probes), so we don't bother gating it on
    # ``probe_adb_device``.
    abi_p = probe_device_cpu_abi("adb")
    # Root-status probe — same deal: tells the install playbook
    # whether ``adb root`` is going to work on this AVD, so it can
    # warn before the operator pastes step 4. Pure read (``getprop``
    # + ``id``); no side effects on adbd.
    root_p = probe_device_root_status("adb")
    device_p = probe_adb_device()
    # LCP.3 / DEC-027 — exactly one local-LLM probe runs per request,
    # selected by ``Config.provider_kind()``. Mutually exclusive: when
    # the operator runs llama.cpp we don't waste a syscall pinging an
    # Ollama daemon that isn't up (and vice-versa). Cloud providers
    # keep using the Ollama probe in v1 for backwards-compat — the
    # card shows up red but doesn't block analysis (cloud LLM card is
    # a future ship).
    provider_kind = config.provider_kind()
    if provider_kind == "local-openai-compat":
        from androscan.config.loader import LLM_PROVIDERS
        llamacpp_default = LLM_PROVIDERS["local"]["llamacpp"]["base_url_default"]
        llm_url = (
            getattr(config, "llamacpp_base_url", None)
            or llamacpp_default
        )
        llm_p = probe_llamacpp(llm_url)
    else:
        # local-ollama (default) and cloud both use the Ollama probe.
        llm_p = probe_ollama_tags(getattr(config, "ollama_base_url", "http://localhost:11434"))

    embed_p = probe_embed_provider(
        getattr(config, "rag_embed_provider", "fastembed"),
        getattr(config, "rag_embed_model", ""),
        getattr(config, "ollama_base_url", "http://localhost:11434"),
    )

    (adb_v, jadx_v, apktool_v, frida_v, frida_server_v, device_v, llm_t, embed_s, abi_v, root_v) = await asyncio.gather(
        adb_p, jadx_p, apktool_p, frida_p, frida_server_p, device_p, llm_p, embed_p, abi_p, root_p,
        return_exceptions=False,
    )

    # Version-skew probe is sequenced after the host CLI + device probes
    # (it consumes ``frida_v`` and only meaningfully runs when the device
    # half is up). Cheap when frida-server is not running — the probe
    # short-circuits on the ``adb shell frida-server --version`` failure
    # so we don't pay an extra ~1s on the common "no device" case.
    #
    # We forward the pid we just learned from ``probe_frida_server`` so
    # the skew probe can read ``/proc/<pid>/exe`` and invoke the *exact*
    # binary that's running, rather than relying on the device shell's
    # ``$PATH`` (which doesn't include ``/data/local/tmp/`` — the
    # canonical Frida install location our own Settings playbook
    # recommends). Without this the card showed red on every install
    # the playbook itself produces.
    if frida_server_v.get("running"):
        skew_v = await probe_frida_version_skew(
            frida_v, "adb", pid=frida_server_v.get("pid"),
        )
    else:
        skew_v = {
            "ok": False,
            "host_version": frida_v.get("version"),
            "device_version": None,
            "severity": None,
            "error": frida_server_v.get("error") or "frida-server not running on device",
        }

    # LLM card shape is identical across providers (so the React
    # ``StatusCardView`` doesn't need a per-provider variant); only
    # the ``label`` / ``model`` / ``provider`` discriminators differ.
    # The new ``provider`` field lets the frontend render a small
    # "via llama.cpp" / "via Ollama" extras line without re-deriving
    # it from ``llm_provider`` (which is operator-typed and can drift).
    if provider_kind == "local-openai-compat":
        chat_model = (
            getattr(config, "llamacpp_model", None)
            or ""  # LCP.4 will plumb a dedicated llamacpp_model field
        )
        chat_present = llamacpp_model_present(llm_t, chat_model)
        llm_card = {
            "ok": bool(llm_t.get("ok")) and chat_present,
            "label": "LLM (llama.cpp)",
            "provider": "llamacpp",
            "model": chat_model or (llm_t.get("models") or [""])[0],
            "base_url": llm_t.get("url"),
            "ping_ms": llm_t.get("ping_ms"),
            "models_available": llm_t.get("models", []),
            "model_present": chat_present,
            "error": llm_t.get("error") or (
                None if chat_present
                else f"configured llamacpp_model {chat_model!r} not loaded "
                     f"(loaded: {llm_t.get('models') or []!r}); restart "
                     "llama-server with the right -m <gguf-path>"
            ),
        }
    else:
        chat_model = getattr(config, "ollama_model", "")
        chat_present = model_present(llm_t, chat_model)
        llm_card = {
            "ok": bool(llm_t.get("ok")) and chat_present,
            "label": "LLM (Ollama)",
            "provider": "ollama",
            "model": chat_model,
            "base_url": llm_t.get("url"),
            "ping_ms": llm_t.get("ping_ms"),
            "models_available": llm_t.get("models", []),
            "model_present": chat_present,
            "error": llm_t.get("error") or (
                None if chat_present else f"chat model {chat_model!r} not pulled (run: ollama pull {chat_model})"
            ),
        }

    cfg_path = discover_config_path()
    sources = effective_sources(cfg_path)
    env_locks = env_overrides()

    return {
        "took_ms": int((time.perf_counter() - started) * 1000),
        "ts": time.time(),
        "process": {
            "ok": True,
            "label": "Web app process",
            "pid": os.getpid(),
            "host": getattr(config, "web_host", "127.0.0.1"),
            "port": getattr(config, "web_port", 8420),
            "cwd": str(Path.cwd()),
            "config_path": str(cfg_path),
            "config_path_exists": cfg_path.is_file(),
            "env_locked_keys": sorted(env_locks.keys()),
            "python": probe_python_env(),
        },
        "tools": {
            "adb":     {**adb_v,     "label": "adb"},
            "jadx":    {**jadx_v,    "label": "jadx"},
            "apktool": {**apktool_v, "label": "apktool"},
            "frida":   {**frida_v,   "label": "frida (host CLI)"},
            # Hook Lab device-side readiness (Phase 6 step 4 / DEC-023):
            # combines the ``pidof frida-server`` reachability probe with
            # the host↔device version-skew comparison. The card is green
            # only when the device half is running *and* the wire-protocol
            # is compatible (same major). Closes DEC-021's "Hook Lab
            # readiness rollup probe" follow-up.
            "frida_server": {
                "ok": bool(frida_server_v.get("ok")) and bool(skew_v.get("ok")),
                "label": "frida-server (device)",
                "running": bool(frida_server_v.get("running")),
                "pid": frida_server_v.get("pid"),
                # Which probe layer confirmed reachability:
                # ``"pidof" | "ps" | "frida-ps" | None``. Surfaced so the
                # Settings card can label host-confirmed reachability
                # ("frida-ps") differently from the on-device pidof / ps
                # paths — operators using a renamed binary or
                # frida-gadget see "running (host-confirmed)" instead of
                # the misleading "pid ?".
                "detection": frida_server_v.get("detection"),
                # Device-side identity of the running server. ``"root"``
                # is required for app attaches to succeed (ptrace barrier
                # against other-uid app processes); ``"shell"`` is the
                # common bug state when the operator forgot ``adb root``
                # or ran the binary directly without ``su 0``. The
                # frontend uses this to surface a yellow warning + the
                # Start-as-root action button on the card.
                "uid": frida_server_v.get("uid"),
                # Whether ``re.frida.helper`` is currently observable in
                # ``ps -A`` — present during an active attach, absent
                # in steady state. Surfaced for diagnostics; the frontend
                # doesn't wire any UX off of it directly today, but it's
                # the cheapest place to capture the signal so it's
                # available for the live Trace panel later.
                "helper_running": bool(frida_server_v.get("helper_running")),
                "host_version": skew_v.get("host_version") or frida_v.get("version"),
                "device_version": skew_v.get("device_version"),
                "version_skew": skew_v.get("severity"),
                "error": frida_server_v.get("error") or skew_v.get("error"),
                # Device ABI + Frida release-arch suffix so the Settings
                # card can synthesise an install playbook when the
                # server isn't running. Both are ``null`` when no device
                # is attached or the ABI isn't in our mapping table.
                "device_abi": abi_v.get("abi"),
                "frida_arch": abi_v.get("frida_arch"),
                # Device root-status: does the install playbook need to
                # warn the operator that ``adb root`` will fail? All
                # four fields are ``null`` when no device is attached.
                # ``can_adb_root`` rolls up build-type + debuggable +
                # current uid into the single boolean the UI cares
                # about; the raw ``device_build_type`` and
                # ``device_rooted`` are surfaced too so the warning
                # message can be specific.
                "device_rooted": root_v.get("rooted"),
                "can_adb_root": root_v.get("can_adb_root"),
                "device_build_type": root_v.get("build_type"),
            },
        },
        "device": {
            **device_v,
            "label": "Android device / emulator",
            "hint": (
                None
                if device_v.get("ok")
                else "start an emulator (or connect a device) — per-app device probes are skipped until then"
            ),
        },
        "llm": llm_card,
        "rag_provider": {**embed_s, "label": "RAG embed provider"},
        "filesystem": {
            "apps_root": {
                **probe_path_writable(apps_root),
                **{k: v for k, v in probe_disk(apps_root).items() if k != "ok"},
                "label": "apps/ root",
            },
        },
        "config_sources": sources,
    }


# ---------------------------------------------------------------------------
# Per-app aggregator


def _decompile_card(app_dir: Path) -> dict[str, Any]:
    """Wrap :func:`decompile_status` into a status-card shape."""
    raw = decompile_status(app_dir)
    status = raw.get("status", "unknown")
    return {
        "ok": status == "ready",
        "label": "Decompile cache",
        "status": status,
        "sha": raw.get("sha"),
        "file_count": raw.get("file_count"),
        "started_ts": raw.get("started_ts"),
        "finished_ts": raw.get("finished_ts"),
        "tree_available": raw.get("tree_available", False),
        "error": raw.get("error"),
        "hint": {
            "missing":  "POST /api/decompile/{app_id} to populate",
            "pending":  "jadx is running…",
            "failed":   "see error; check jadx is on PATH",
            "ready":    None,
            "unknown":  "no app_meta.json (run analysis first)",
        }.get(status, None),
    }


def _rag_card(app_dir: Path, decompile: dict[str, Any]) -> dict[str, Any]:
    """Wrap RAG status into a card; safe even if decompile isn't ready."""
    sha = decompile.get("sha")
    if not sha or decompile.get("status") != "ready":
        return {
            "ok": False,
            "label": "RAG index",
            "status": "missing",
            "hint": "build decompile cache first",
            "error": None,
        }
    cache_dir = decompile_cache_root(app_dir, sha)
    # Lazy import keeps RAG deps optional at import time.
    from androscan.rag.index import get_status as rag_status
    rs = rag_status(cache_dir)
    d = rs.to_dict()
    return {
        "ok": d.get("status") == "ready",
        "label": "RAG index",
        **d,
        "hint": {
            "missing":  "auto-build kicks in after decompile completes; or POST /api/rag/{app_id}/rebuild",
            "pending":  "embedding worker is running…",
            "failed":   "see error; check embed provider availability",
            "ready":    None,
        }.get(d.get("status"), None),
    }


def _call_graph_card(app_dir: Path, decompile: dict[str, Any]) -> dict[str, Any]:
    """Wrap the static-call-graph status into a card.

    Parallels :func:`_rag_card` — same "wait for decompile, then surface
    the sub-index's status" contract. Kept as a separate card rather than
    folded into ``rag`` because the two indexes have independent lifecycles
    (one can be ready while the other is still pending / failed).
    """
    sha = decompile.get("sha")
    if not sha or decompile.get("status") != "ready":
        return {
            "ok": False,
            "label": "Call graph",
            "status": "missing",
            "hint": "build decompile cache first",
            "error": None,
        }
    cache_dir = decompile_cache_root(app_dir, sha)
    from androscan.analysis.call_graph import get_status as cg_status
    st = cg_status(cache_dir).to_dict()
    return {
        "ok": st.get("status") == "ready",
        "label": "Call graph",
        **st,
        "hint": {
            "missing":  "auto-build kicks in after decompile completes; or POST /api/graph/{app_id}/rebuild",
            "pending":  "apktool/parser worker is running…",
            "failed":   "see error; check apktool is on PATH",
            "ready":    None,
        }.get(st.get("status"), None),
    }


def _meta_card(app_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(meta_dict, card_dict)`` derived from ``app_meta.json``."""
    meta = load_app_meta(app_dir) or {}
    pkg = (meta.get("dossier") or {}).get("apk_info", {}).get("package") or meta.get("package") or ""
    apk_path = meta.get("apk_path")
    apk_sha = meta.get("apk_sha256")
    drift = probe_apk_sha_drift(apk_path, apk_sha)
    card = {
        "ok": drift.get("ok", False),
        "label": "App metadata",
        "package": pkg,
        "apk_path": apk_path,
        "apk_sha256": apk_sha,
        **{k: v for k, v in drift.items() if k not in ("ok",)},
    }
    return meta, card


def _skipped_device_card(label: str, reason: str, **extras: Any) -> dict[str, Any]:
    """Uniform shape for a device-side card that wasn't run because no device is attached.

    ``ok`` is False so the dot isn't green, but ``skipped`` lets the UI
    render it muted instead of as an error. Probe-specific keys (e.g.
    ``installed``, ``running``, ``pid``) are passed via ``**extras`` so
    each card retains its expected schema for downstream consumers.
    """
    return {
        "ok": False,
        "skipped": True,
        "label": label,
        "error": reason,
        "hint": "no device attached — start an emulator or connect a device",
        **extras,
    }


async def _gather_per_app(app_dir: Path, app_id: str) -> dict[str, Any]:
    """Per-app status card bundle. Mirrors :func:`_gather_global`'s shape.

    Device-side probes (``pm path``, ``pidof``, UID resolver, ``dumpsys
    activity top``, ``uiautomator dump``) are gated on a single
    :func:`probe_adb_device` call. When no device is attached we skip the
    five adb-shell roundtrips entirely and return uniform "skipped" cards
    — much faster (one adb call instead of six) and the UI gets a coherent
    "no device" story instead of five independently red cards each leaking
    "no devices/emulators found" into their error line.
    """
    started = time.perf_counter()

    meta, meta_card = _meta_card(app_dir)
    pkg: str = meta_card.get("package") or ""

    decompile_card = _decompile_card(app_dir)
    rag_card = _rag_card(app_dir, decompile_card)
    call_graph_card = _call_graph_card(app_dir, decompile_card)

    device = await probe_adb_device()
    device_reason = device.get("error") or "no device attached"

    if device.get("ok"):
        pkg_install_p = probe_pkg_installed(pkg)
        pkg_run_p = probe_pkg_running(pkg)
        pkg_uid_p = probe_pkg_uid(pkg)
        fg_p = probe_foreground_activity()
        ui_p = probe_uiautomator_dump()

        (pkg_install, pkg_run, pkg_uid, fg, ui) = await asyncio.gather(
            pkg_install_p, pkg_run_p, pkg_uid_p, fg_p, ui_p,
            return_exceptions=False,
        )

        fg_pkg_match = bool(fg.get("package")) and bool(pkg) and fg.get("package") == pkg
        fg_card = {
            **fg,
            "label": "Foreground activity",
            "matches_app": fg_pkg_match,
            "hint": (
                None if fg_pkg_match
                else f"top activity is {fg.get('package')!r}; switch the device to {pkg!r} for click-to-code to be relevant"
            ),
        }

        device_block = {
            "connected": True,
            "state": device.get("state"),
            "serial": device.get("serial"),
            "package_installed": {**pkg_install, "label": "Installed on device"},
            "package_running":   {**pkg_run,     "label": "Running on device"},
            "package_uid":       {**pkg_uid,     "label": "Stable Linux UID (logcat key)"},
            "foreground":        fg_card,
            "uiautomator_dump":  {**ui,          "label": "uiautomator dump (for click-to-code)"},
        }
    else:
        device_block = {
            "connected": bool(device.get("connected")),
            "state": device.get("state"),
            "serial": device.get("serial"),
            "package_installed": _skipped_device_card(
                "Installed on device", device_reason, installed=False, package=pkg, apk_path_on_device=None,
            ),
            "package_running": _skipped_device_card(
                "Running on device", device_reason, running=False, package=pkg, pid=None,
            ),
            "package_uid": _skipped_device_card(
                "Stable Linux UID (logcat key)", device_reason, resolved=False, uid=None, method=None,
            ),
            "foreground": _skipped_device_card(
                "Foreground activity", device_reason, activity=None, package=None, matches_app=False,
            ),
            "uiautomator_dump": _skipped_device_card(
                "uiautomator dump (for click-to-code)", device_reason, rc=-1, has_xml=False,
            ),
        }

    per_app = load_app_settings(app_dir)
    overrides = apk_overrides_summary(per_app)

    return {
        "took_ms": int((time.perf_counter() - started) * 1000),
        "ts": time.time(),
        "app_id": app_id,
        "app_dir": str(app_dir),
        "meta": meta_card,
        "decompile": decompile_card,
        "rag": rag_card,
        "call_graph": call_graph_card,
        "device": device_block,
        "overrides": {
            "ok": True,
            "label": "Per-app overrides",
            "active_count": len(overrides),
            "lines": overrides,
            "tags": per_app.get("tags", []),
        },
    }


# ---------------------------------------------------------------------------
# Router factory


def build_status_router(
    config_provider: Callable[[], Config],
    apps_root_provider: Callable[[], Path],
    app_dir_resolver: Callable[[str], Path],
) -> APIRouter:
    """Return a router exposing ``/api/status/*``.

    All three providers are callables (rather than captured values) so that
    config / apps_root reload semantics can be implemented once in
    ``app.py`` (via ``app.state.config``) without each router holding a
    stale snapshot.
    """
    router = APIRouter(prefix="/api/status", tags=["status"])

    @router.get("/global")
    async def global_status() -> dict[str, Any]:
        cached = _cache_get("global")
        if cached is not None:
            return cached
        try:
            payload = await _gather_global(config_provider(), apps_root_provider())
        except Exception as e:
            logger.exception("global status aggregation failed: %s", e)
            raise HTTPException(status_code=500, detail=f"status aggregation failed: {e}")
        _cache_put("global", payload)
        return payload

    @router.get("/apps/{app_id}")
    async def per_app_status(app_id: str) -> dict[str, Any]:
        key = f"app:{app_id}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        app_dir = app_dir_resolver(app_id)
        if not app_dir.is_dir():
            raise HTTPException(status_code=404, detail="Unknown app_id")
        try:
            payload = await _gather_per_app(app_dir, app_id)
        except Exception as e:
            logger.exception("per-app status aggregation failed: %s", e)
            raise HTTPException(status_code=500, detail=f"status aggregation failed: {e}")
        _cache_put(key, payload)
        return payload

    return router
