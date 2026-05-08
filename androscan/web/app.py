"""FastAPI RE Workbench: REST for runs + WebSockets for mirror and logcat."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from androscan.config import Config
from androscan.internal.app_meta import load_app_meta
from androscan.web.chat import handle_chat_request, stream_chat_request
from androscan.web.device_ops import (
    adb_install_apk,
    adb_launch_package,
    adb_pm_path,
    invalidate_launcher_cache,
    list_avds,
    spawn_emulator_detached,
)
from androscan.web.decompile_cache import (
    get_status as decompile_status,
    load_tree,
    read_source_file,
    sources_dir as decompile_sources_dir,
    start_decompile,
)
from androscan.skills.resolve_ui_element import resolve as resolve_ui_element
from androscan.web.inspect_map import map_tap_to_code
from androscan.web.paths import apps_root, read_json, safe_child
from androscan.web.frida_routes import build_frida_router
from androscan.web.graph_routes import (
    build_graph_router,
    schedule_call_graph_build_after_decompile,
)
from androscan.web.rag_routes import build_rag_router, schedule_rag_build_after_decompile
from androscan.web.settings_routes import build_settings_router
from androscan.web.status_routes import build_status_router
from androscan.web.trace_routes import build_trace_router
from androscan.web.triage import load_triage, upsert_triage

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class TapBody(BaseModel):
    x: int = Field(..., ge=0, description="Screen X in device pixels")
    y: int = Field(..., ge=0, description="Screen Y in device pixels")


class TriageBody(BaseModel):
    status: Optional[str] = Field(default=None, description="confirmed | false_positive | suppressed | needs_review")
    severity_override: Optional[str] = Field(default=None, description="critical|high|medium|low|informational or null")
    note: Optional[str] = Field(default=None, max_length=4000)
    actor: Optional[str] = Field(default="user", max_length=64)


class ChatAttachment(BaseModel):
    kind: str = Field(default="default", max_length=32)
    name: str = Field(default="", max_length=120)
    text: str = Field(default="", max_length=64_000)


class ChatBody(BaseModel):
    tab: str = Field(..., description="reports | inspect | hook")
    prompt: str = Field(..., max_length=16_000)
    history: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[ChatAttachment] = Field(default_factory=list)
    app_id: Optional[str] = Field(default=None, max_length=200)
    run_ts: Optional[str] = Field(default=None, max_length=120)


class InspectMapBody(BaseModel):
    app_id: str = Field(..., max_length=200)
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)


class AdbShellBody(BaseModel):
    command: str = Field(..., max_length=2000, description="adb shell argv (whitespace separated)")


class EmulatorStartBody(BaseModel):
    avd: Optional[str] = Field(
        default=None, max_length=200,
        description="AVD name to launch. If omitted, picks the first from `emulator -list-avds`.",
    )


class InstallLaunchBody(BaseModel):
    app_id: str = Field(..., max_length=200,
                        description="AndroScan app_id under apps/. Used to resolve APK + package.")
    install: bool = Field(default=True, description="Install (or reinstall) the APK if not present.")
    launch: bool = Field(default=True, description="Launch the package after install.")


# How long we wait between PID re-resolutions when the target app is not yet
# running (or has just died) on the package-scoped logcat stream.
_LOGCAT_PID_RESOLVE_INTERVAL_SEC = 1.5

# Hard cap on how long any one ad-hoc ``adb shell`` invocation may run.
_ADB_SHELL_TIMEOUT_SEC = 20.0
# Hard cap on captured output size to keep the UI responsive.
_ADB_SHELL_MAX_OUTPUT = 200_000

# These adb sub-commands are blocked because (a) they would brick the
# attached emulator, or (b) they reach outside ``adb shell`` entirely
# (allow-list discipline: only ``adb shell`` argv is permitted).
_ADB_SHELL_DENYLIST_TOKENS = (
    "reboot",
    "wipe",
    "format",
    "factory_reset",
    "factoryreset",
    "remount",
    "fastboot",
)

# Per-call wall-clock cap for the AVD/model name lookups behind
# ``/api/device/status``. Both calls are best-effort and the polling
# interval on the frontend is 3s, so we keep these tight to avoid
# stretching the status response when adb is briefly slow.
_DEVICE_NAME_TIMEOUT_SEC = 1.5

# Per-call cap for the ``getprop sys.boot_completed`` probe behind
# ``/api/device/status``. ``getprop`` is normally instantaneous (the
# property store is in-memory) but adb itself can stall for a few
# seconds during the first moments of a fresh emulator attach, so we
# leave a little headroom while still capping each status call.
_BOOT_COMPLETED_TIMEOUT_SEC = 2.0


async def _resolve_device_name() -> Optional[str]:
    """Best-effort human-readable name for the currently attached device.

    Used by ``/api/device/status`` so the Mirror View status pill reads
    e.g. ``device: online (Pixel_2_API_28)`` instead of the literal adb
    state ``device: online (device)``. Resolution order:

    1. ``adb emu avd name`` — Android emulator console command. Returns
       the AVD name on its own line followed by ``OK`` (and is silent
       on physical devices). This is the strongest signal: the operator
       chose this AVD via the boot wizard, so seeing it back in the
       status pill closes the loop.
    2. ``adb shell getprop ro.product.model`` — physical-device
       fallback. Returns e.g. ``Pixel 6`` or ``sdk_gphone64_arm64``
       (the AOSP emulator's model string when ``adb emu`` is suppressed
       e.g. on multi-device-attached errors).

    Returns ``None`` when neither path resolves to a non-empty string.
    Never raises — every error path is swallowed because the status
    endpoint must remain a fast happy-path GET even when adb is wedged.
    """
    # Try AVD console first — emulator-only, so failure here is benign
    # for physical devices.
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "emu", "avd", "name",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, _err_b = await asyncio.wait_for(
                proc.communicate(), timeout=_DEVICE_NAME_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            out_b = b""
    except (FileNotFoundError, OSError):
        out_b = b""
    # ``adb emu avd name`` output:
    #     Pixel_2_API_28
    #     OK
    # We take the first non-empty, non-``OK`` line. On physical devices
    # adb writes ``error: closed`` to stderr and stdout is empty.
    for line in (out_b or b"").decode(errors="replace").splitlines():
        token = line.strip()
        if token and token.upper() != "OK" and not token.lower().startswith("error"):
            return token

    # Fall back to product model for physical devices (and for emulators
    # where ``adb emu`` was rejected, e.g. multi-device attach).
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "shell", "getprop", "ro.product.model",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, _err_b = await asyncio.wait_for(
                proc.communicate(), timeout=_DEVICE_NAME_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return None
        if proc.returncode != 0:
            return None
    except (FileNotFoundError, OSError):
        return None
    model = (out_b or b"").decode(errors="replace").strip()
    return model or None


async def _resolve_boot_completed() -> bool:
    """Return ``True`` once Android's ``sys.boot_completed`` flips to ``1``.

    ``adb get-state`` returns ``device`` as soon as the on-device
    ``adbd`` socket is up — that's typically 10-20 s into a cold boot,
    long before ``system_server`` / ``ActivityManager`` / ``PackageManager``
    are actually serving requests. Firing ``adb install`` or ``monkey -p``
    in that window deterministically fails (``monkey`` dumps its argv as
    its only "error message" — the literal symptom the user reported).

    AOSP's canonical "system is up" signal is ``getprop sys.boot_completed``
    flipping to ``1`` (set by ``init`` once the BOOT_COMPLETED broadcast
    has fired). Every reliable CI / test harness gates on this property.

    Best-effort: any failure (adb missing, getprop timeout, non-zero exit,
    unexpected output) is treated as ``False`` so the status endpoint stays
    a fast happy-path GET. Caller is responsible for retrying on the next
    poll tick.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "shell", "getprop", "sys.boot_completed",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return False
    try:
        out_b, _err_b = await asyncio.wait_for(
            proc.communicate(), timeout=_BOOT_COMPLETED_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return False
    if proc.returncode != 0:
        return False
    # ``getprop`` prints just ``1`` (with a trailing newline) once boot is
    # complete; an empty line / ``0`` / anything else means "not yet".
    return (out_b or b"").decode(errors="replace").strip() == "1"


# ---------------------------------------------------------------------------
# Phase 13 sub-step 13.4 — production wiring for the ``summarise_method``
# skill behind sub-step 13.3's :data:`SummaryCallable` seam.
#
# Built as a module-level factory (rather than inline inside
# :func:`create_app`) so tests + the docstrings can reference the
# exact adapter shape — and so a future skill-tier evolution
# (richer source enrichment, multi-shot prompts, etc.) lands here
# without touching the route layer at all.


def _build_summarise_method_callable(
    config_provider: Callable[[], Config],
    app_dir_resolver: Callable[[str], Path],
):
    """Return the production :data:`SummaryCallable` that wraps the
    registered ``summarise_method`` skill (sub-step 13.4).

    The returned async callable accepts the four-arg
    :data:`SummaryCallable` signature locked at sub-step 13.4 —
    ``(app_id, class_smali, method_name, descriptor) -> str`` —
    and bounces the sync :func:`androscan.skills.execute` call into
    the default executor so the WS event loop stays unblocked
    during the LLM round-trip. The route layer's
    :func:`asyncio.wait_for` against :data:`SUMMARY_TIMEOUT_S` is
    the canonical timeout enforcement; this adapter doesn't add a
    second one (avoids the double-timeout race the
    ``trace_summary.default_summary_callable`` originally guarded
    against — under 13.4 the sync skill IS the LLM call, so a
    single layer of timeout suffices).

    Failure handling: a skill ``SkillResult`` with ``success=False``
    surfaces as a :exc:`RuntimeError` (the WS handler then emits
    ``summary_failed`` with the operator-facing reason). A skill
    that returns ``success=True`` with a fail-open text
    (e.g. "Source unavailable for summary") is treated as a
    successful summary — the operator sees the explanation in the
    Inspector pane rather than a hard failure pill.
    """

    import asyncio
    from androscan.skills import SkillContext, execute as execute_skill

    async def _call(
        app_id: str,
        class_smali: str,
        method_name: str,
        descriptor: str,
    ) -> str:
        cfg = config_provider()
        # Resolve the per-app run-folder synthetically — the
        # skill's :func:`_resolve_app_dir` walks
        # ``run_folder.parent.parent`` to find the ``apps/`` root,
        # so any run-folder-shaped path inside ``apps/<app_id>/``
        # works. We use a synthetic ``apps/<app_id>/__ws_trace__/``
        # path: the skill never reads the run folder itself (only
        # uses it to derive ``apps_root`` + ``app_id``), so the
        # synthetic path is enough to drive the resolution
        # without creating a real run folder per WS connection.
        try:
            app_dir = app_dir_resolver(app_id)
        except Exception:
            # Fall back to a path-shape that lets the skill's
            # resolver still extract ``apps/`` from the parent
            # ladder (resolves to ``apps/<app_id>/__ws_trace__/``
            # → ``apps/<app_id>/`` → ``apps/`` root).
            apps_dir = Path(cfg.run_folder_root or "apps")
            app_dir = apps_dir / app_id
        synthetic_run_folder = app_dir / "__ws_trace__"

        skill_ctx = SkillContext(
            config=cfg,
            run_folder=synthetic_run_folder,
            dossier_dict=None,
            apk_path=None,
        )
        params = {
            "class_smali": class_smali,
            "method_name": method_name,
            "descriptor": descriptor,
            "app_id": app_id,
        }

        loop = asyncio.get_running_loop()

        def _do_call() -> str:
            result = execute_skill("summarise_method", params, skill_ctx)
            if not result.success:
                # Surface as exception so the WS handler's
                # ``summary_failed`` path emits the operator-facing
                # error message verbatim.
                raise RuntimeError(result.text or "summarise_method failed")
            return (result.text or "").strip()

        return await loop.run_in_executor(None, _do_call)

    return _call


def create_app(config: Config, *, cwd: Optional[Path] = None) -> FastAPI:
    """Create FastAPI app bound to ``config`` and current working directory."""
    root = apps_root(config, cwd=cwd)

    app = FastAPI(title="AndroScan RE Workbench", version="0.1.0")

    # ``app.state.config`` is the source of truth for live-reloadable settings.
    # The Settings tab swaps it via ``set_config`` after a YAML save / reset.
    # Existing closures in this module still capture ``config`` directly — they
    # will keep using the boot-time snapshot until uvicorn restarts, which the
    # Settings UI surfaces via its ``restart_required`` indicator. New code
    # added after this point should read from ``app.state.config`` instead.
    app.state.config = config

    def _current_config() -> Config:
        return getattr(app.state, "config", config)

    def _set_config(new_config: Config) -> None:
        app.state.config = new_config

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            f"http://{config.web_host}:{config.web_port}",
            f"http://127.0.0.1:{config.web_port}",
            f"http://localhost:{config.web_port}",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "apps_root": str(root)}

    @app.get("/api/llm/info")
    def llm_info() -> dict[str, Any]:
        """Expose the current LLM model + base URL for UI tooltips.

        Read-only; no secrets. Used by the chat dock's info icon.
        """
        return {
            "model": getattr(config, "ollama_model", None) or "qwen3.5:35b",
            "base_url": getattr(config, "ollama_base_url", None) or "http://localhost:11434",
            "provider": "ollama",
        }

    @app.get("/api/projects")
    def list_projects() -> dict[str, Any]:
        if not root.is_dir():
            return {"projects": []}
        projects = []
        for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
            if p.is_dir() and not p.name.startswith("."):
                projects.append({"app_id": p.name})
        return {"projects": projects}

    @app.get("/api/projects/{app_id}/runs")
    def list_runs(app_id: str) -> dict[str, Any]:
        app_dir = safe_child(root, app_id)
        if app_dir is None or not app_dir.is_dir():
            raise HTTPException(status_code=404, detail="Unknown app_id")
        runs = []
        for p in sorted(app_dir.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_dir() or p.name in ("extracted_apk",) or p.name.startswith("."):
                continue
            if (p / "run_meta.json").exists() or (p / "report.json").exists():
                runs.append({"run_timestamp": p.name})
        return {"app_id": app_id, "runs": runs}

    @app.get("/api/dossier/{app_id}/{run_ts}")
    def get_dossier(app_id: str, run_ts: str) -> dict[str, Any]:
        """Return dossier from ``app_meta.json`` if run folder exists (dossier is app-scoped)."""
        run_dir = safe_child(root, app_id, run_ts)
        if run_dir is None or not run_dir.is_dir():
            raise HTTPException(status_code=404, detail="Unknown run")
        app_dir = safe_child(root, app_id)
        assert app_dir is not None
        meta = load_app_meta(app_dir)
        if not meta or "dossier" not in meta:
            raise HTTPException(status_code=404, detail="No dossier in app_meta.json for this app")
        return {"app_id": app_id, "run_timestamp": run_ts, "dossier": meta["dossier"]}

    @app.get("/api/findings/{app_id}/{run_ts}")
    def get_findings(app_id: str, run_ts: str) -> dict[str, Any]:
        report_path = safe_child(root, app_id, run_ts, "report.json")
        if report_path is None or not report_path.is_file():
            raise HTTPException(status_code=404, detail="report.json not found")
        data = read_json(report_path)
        if not isinstance(data, dict):
            raise HTTPException(status_code=500, detail="Invalid report.json")
        return {"app_id": app_id, "run_timestamp": run_ts, "report": data}

    @app.get("/api/triage/{app_id}/{run_ts}")
    def get_triage(app_id: str, run_ts: str) -> dict[str, Any]:
        run_dir = safe_child(root, app_id, run_ts)
        if run_dir is None or not run_dir.is_dir():
            raise HTTPException(status_code=404, detail="Unknown run")
        return {"app_id": app_id, "run_timestamp": run_ts, "triage": load_triage(root, app_id, run_ts)}

    @app.post("/api/triage/{app_id}/{run_ts}")
    def post_triage_missing_id(app_id: str, run_ts: str) -> dict[str, Any]:
        """Explicit 400 instead of FastAPI's confusing 307→405 chain when the
        UI accidentally builds the URL without a finding id (was the symptom
        of a frontend bug where empty string ids slipped through ``??``).
        """
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing finding_id path segment; "
                "expected POST /api/triage/{app_id}/{run_ts}/{finding_id}"
            ),
        )

    @app.post("/api/triage/{app_id}/{run_ts}/{finding_id}")
    def post_triage(app_id: str, run_ts: str, finding_id: str, body: TriageBody) -> dict[str, Any]:
        if not finding_id or not finding_id.strip():
            raise HTTPException(status_code=400, detail="finding_id must be non-empty")
        ok, err, entry = upsert_triage(
            root,
            app_id,
            run_ts,
            finding_id,
            status=body.status,
            severity_override=body.severity_override,
            note=body.note,
            actor=(body.actor or "user"),
        )
        if not ok:
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True, "entry": entry}

    @app.post("/api/chat")
    def post_chat(body: ChatBody) -> JSONResponse:
        payload = body.model_dump()
        # ChatAttachment was already validated by Pydantic; pass dicts to the handler.
        payload["attachments"] = [a.model_dump() for a in body.attachments]
        status_code, resp = handle_chat_request(payload, config, root)
        return JSONResponse(status_code=status_code, content=resp)

    @app.post("/api/chat/stream")
    async def post_chat_stream(body: ChatBody) -> StreamingResponse:
        """SSE variant of ``/api/chat`` — yields ``thinking`` and ``content``
        deltas as the model generates them, then a terminal ``done`` (or
        ``error``) event.

        The response always opens with HTTP 200 because SSE clients can't
        usefully react to non-2xx status mid-stream; validation/rate-limit
        failures are surfaced as an immediate ``event: error`` frame.
        """
        payload = body.model_dump()
        payload["attachments"] = [a.model_dump() for a in body.attachments]
        gen = stream_chat_request(payload, config, root)
        # Disable proxy/browser buffering so each SSE frame flushes promptly.
        headers = {
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
        return StreamingResponse(gen, media_type="text/event-stream", headers=headers)

    # ----- Inspect tab: persistent decompile cache + click-to-map -----

    def _app_dir(app_id: str) -> Path:
        d = safe_child(root, app_id)
        if d is None or not d.is_dir():
            raise HTTPException(status_code=404, detail="Unknown app_id")
        return d

    @app.get("/api/decompile/{app_id}")
    def decompile_get(app_id: str) -> dict[str, Any]:
        return decompile_status(_app_dir(app_id))

    @app.post("/api/decompile/{app_id}")
    def decompile_post(app_id: str) -> dict[str, Any]:
        jadx_cmd = getattr(config, "jadx_cmd", "jadx") or "jadx"
        app_dir = _app_dir(app_id)

        # Chain RAG build + static call-graph build after jadx success.
        # Both auto-builders are best-effort — failures log but never
        # bubble up to the HTTP caller.
        def _on_decompile_done(success: bool) -> None:
            if not success:
                return
            try:
                schedule_rag_build_after_decompile(app_dir, config)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("RAG auto-build hookup failed: %s", e)
            try:
                schedule_call_graph_build_after_decompile(app_dir, config)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("call-graph auto-build hookup failed: %s", e)

        result = start_decompile(app_dir, jadx_cmd=jadx_cmd, on_done=_on_decompile_done)
        # If the cache was already ready from a previous run, kick the
        # downstream indexers now too.
        if result.get("status") == "ready":
            try:
                schedule_rag_build_after_decompile(app_dir, config)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("RAG auto-build (cached decompile) failed: %s", e)
            try:
                schedule_call_graph_build_after_decompile(app_dir, config)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("call-graph auto-build (cached decompile) failed: %s", e)
        return result

    @app.get("/api/code/{app_id}/tree")
    def code_tree(app_id: str) -> dict[str, Any]:
        tree = load_tree(_app_dir(app_id))
        if tree is None:
            raise HTTPException(
                status_code=409,
                detail="Decompile cache not ready. POST /api/decompile/{app_id} first.",
            )
        return {"app_id": app_id, "tree": tree}

    @app.get("/api/code/{app_id}/file")
    def code_file(app_id: str, path: str = Query(..., max_length=1000)) -> dict[str, Any]:
        text = read_source_file(_app_dir(app_id), path)
        if text is None:
            raise HTTPException(status_code=404, detail="File not found in decompile cache")
        return {"app_id": app_id, "path": path, "text": text}

    @app.get("/api/device/status")
    async def device_status() -> dict[str, Any]:
        """Quick adb online/offline + boot-completion probe.

        Returns ``{online, state, detail, device_name, boot_completed}``:

        * ``online`` / ``state`` come from ``adb get-state``. ``state ==
          "device"`` means ``adbd`` is reachable — but that fires *very*
          early in the Android boot sequence, well before ``system_server``
          (the host of ``ActivityManager`` and ``PackageManager``) is up.
        * ``boot_completed`` reflects ``getprop sys.boot_completed`` — the
          AOSP-canonical "the OS has finished booting" signal. The Boot
          wizard gates the install/launch steps on this *and* ``online``
          so ``adb install`` / ``monkey -p`` aren't fired into a half-up
          system (which is exactly the failure mode the user reported:
          ``monkey`` printing its argv as its only output because its
          ``IActivityManager`` proxy hadn't been wired yet).
        * ``device_name`` is best-effort human-readable (AVD name, then
          ``ro.product.model``); ``None`` when neither path resolves.

        Every probe is individually timeboxed and never raises — the
        endpoint must remain a fast happy-path GET even when adb is
        wedged, because the frontend polls it every 3 s.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "adb", "get-state",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
                return {
                    "online": False, "state": "timeout", "detail": "",
                    "device_name": None, "boot_completed": False,
                }
        except FileNotFoundError:
            return {
                "online": False, "state": "no_adb", "detail": "adb not on PATH",
                "device_name": None, "boot_completed": False,
            }
        state = (stdout or b"").decode(errors="replace").strip()
        if proc.returncode == 0 and state == "device":
            # Run the two best-effort follow-ups concurrently — neither
            # depends on the other, and both add 200-500 ms each on a
            # cold-attached emulator. ``return_exceptions=True`` is a
            # belt-and-braces because both helpers already swallow.
            device_name, boot_completed = await asyncio.gather(
                _resolve_device_name(),
                _resolve_boot_completed(),
                return_exceptions=False,
            )
            return {
                "online": True,
                "state": state,
                "detail": "" if boot_completed else "system still booting",
                "device_name": device_name,
                "boot_completed": bool(boot_completed),
            }
        return {
            "online": False,
            "state": state or "unknown",
            "detail": (stderr or b"").decode(errors="replace").strip()[:300],
            "device_name": None,
            "boot_completed": False,
        }

    @app.post("/api/adb/shell")
    async def adb_shell(body: AdbShellBody) -> dict[str, Any]:
        """Run a single ``adb shell`` command and return its captured output.

        Layered guarantees:
          * Argv is parsed with ``shlex.split`` (no real shell), so
            backticks / pipes / redirects are inert.
          * A small denylist of irreversible adb sub-commands (``reboot``,
            ``wipe``, ``remount``, ``fastboot`` …) returns 400 immediately.
          * Wall-clock timeout (``_ADB_SHELL_TIMEOUT_SEC``) and stdout
            cap (``_ADB_SHELL_MAX_OUTPUT``) keep the UI responsive.
        """
        import shlex

        cmd = (body.command or "").strip()
        if not cmd:
            raise HTTPException(status_code=400, detail="empty command")
        try:
            argv = shlex.split(cmd, posix=True)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"parse error: {e}")
        if not argv:
            raise HTTPException(status_code=400, detail="empty command")

        joined = " ".join(argv).lower()
        for tok in _ADB_SHELL_DENYLIST_TOKENS:
            if tok in joined:
                raise HTTPException(
                    status_code=400,
                    detail=f"command contains blocked token '{tok}'",
                )

        try:
            proc = await asyncio.create_subprocess_exec(
                "adb", "shell", *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail="adb not on PATH")

        try:
            out_b, err_b = await asyncio.wait_for(
                proc.communicate(), timeout=_ADB_SHELL_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(
                status_code=504,
                detail=f"command exceeded {_ADB_SHELL_TIMEOUT_SEC:.0f}s timeout",
            )

        out = (out_b or b"").decode(errors="replace")
        err = (err_b or b"").decode(errors="replace")
        truncated = False
        if len(out) > _ADB_SHELL_MAX_OUTPUT:
            out = out[:_ADB_SHELL_MAX_OUTPUT]
            truncated = True
        if len(err) > _ADB_SHELL_MAX_OUTPUT:
            err = err[:_ADB_SHELL_MAX_OUTPUT]
            truncated = True

        return {
            "argv": argv,
            "exit_code": proc.returncode,
            "stdout": out,
            "stderr": err,
            "truncated": truncated,
        }

    # ----- "Bring device online" wizard endpoints -----
    #
    # The Inspect tab's MirrorView shows a multi-step wizard when adb has no
    # device attached. Each step here is a small endpoint so the frontend
    # can stream progress (a step at a time) and render its shimmer state.

    @app.get("/api/device/avds")
    async def device_avds() -> dict[str, Any]:
        """List installed AVDs via ``emulator -list-avds``.

        Returns 200 always (with ``ok: false`` + ``error`` when the binary
        isn't found or there are no AVDs) so the UI can render a helpful
        message instead of a generic HTTP error.
        """
        return await list_avds()

    @app.post("/api/device/emulator/start")
    async def device_emulator_start(body: EmulatorStartBody) -> dict[str, Any]:
        """Spawn ``emulator -avd <avd>`` *detached* and return immediately.

        The emulator can take 30-90 s to boot; the wizard is expected to
        poll ``/api/device/status`` until ``state == "device"``. We pick
        the first AVD if none is supplied so the common single-AVD setup
        works with one click.
        """
        avd = (body.avd or "").strip()
        if not avd:
            inv = await list_avds()
            if not inv.get("ok"):
                raise HTTPException(status_code=503, detail=inv.get("error") or "no AVDs available")
            avd = inv["avds"][0]
        result = await spawn_emulator_detached(avd)
        if not result.get("ok"):
            raise HTTPException(status_code=503, detail=result.get("error") or "emulator spawn failed")
        return result

    @app.post("/api/device/install_and_launch")
    async def device_install_and_launch(body: InstallLaunchBody) -> dict[str, Any]:
        """Check installed → optionally install → optionally launch the app.

        Resolves the package + APK path from the app's ``app_meta.json``.
        Each substep is reported in the ``steps`` list so the wizard can
        light up rows individually even though this is a single request.
        """
        app_dir = _app_dir(body.app_id)
        meta = load_app_meta(app_dir) or {}
        package = (
            (meta.get("dossier") or {}).get("apk_info", {}).get("package")
            or meta.get("package")
            or ""
        ).strip()
        apk_path = (meta.get("apk_path") or "").strip() or None
        if not package:
            raise HTTPException(
                status_code=409,
                detail="No package in app_meta.json (run analysis first to populate it).",
            )

        steps: list[dict[str, Any]] = []

        # Step 1: pm path — is the app installed?
        pm = await adb_pm_path(package)
        steps.append({
            "key": "check_installed",
            "ok": pm.get("error") is None,
            "installed": pm["installed"],
            "apk_path_on_device": pm.get("apk_path_on_device"),
            "error": pm.get("error"),
        })

        # Step 2: install if requested and not present (or always reinstall
        # is currently not surfaced by the wizard — keep it simple).
        installed_now = pm["installed"]
        if body.install and not installed_now:
            if not apk_path:
                steps.append({
                    "key": "install",
                    "ok": False,
                    "installed": False,
                    "error": "App is not installed and no apk_path is recorded in app_meta.json.",
                })
                return {"package": package, "apk_path": apk_path, "steps": steps, "ok": False}
            inst = await adb_install_apk(apk_path)
            installed_now = inst["ok"]
            # A fresh install can change the LAUNCHER activity (developer
            # renamed MainActivity between builds, for example). Drop the
            # sidecar cache so the next launch re-resolves and we don't
            # serve a stale component name to ``am start``.
            if inst["ok"]:
                invalidate_launcher_cache(app_dir)
            steps.append({
                "key": "install",
                "ok": inst["ok"],
                "installed": inst["ok"],
                "exit_code": inst.get("exit_code"),
                "error": inst.get("error"),
            })
            if not inst["ok"]:
                return {"package": package, "apk_path": apk_path, "steps": steps, "ok": False}
        else:
            steps.append({
                "key": "install",
                "ok": True,
                "skipped": True,
                "installed": installed_now,
                "reason": "already installed" if installed_now else "install=false",
            })

        # Step 3: launch — uses ``am start -W`` with the resolved LAUNCHER
        # activity (cached per-app via ``app_dir``), then verifies the
        # process is actually alive via ``pidof``. Surfaces the parsed
        # ``LaunchState`` (COLD/WARM/HOT) and ``TotalTime`` so the wizard
        # can show "started in 678 ms" instead of just a green check.
        if body.launch and installed_now:
            launch = await adb_launch_package(package, app_dir=app_dir)
            steps.append({
                "key": "launch",
                "ok": launch["ok"],
                "exit_code": launch.get("exit_code"),
                "activity": launch.get("activity"),
                "launch_state": launch.get("launch_state"),
                "total_time_ms": launch.get("total_time_ms"),
                "pid": launch.get("pid"),
                "verified_running": launch.get("verified_running"),
                "used_monkey_fallback": launch.get("used_monkey_fallback"),
                "error": launch.get("error"),
            })
            return {
                "package": package,
                "apk_path": apk_path,
                "steps": steps,
                "ok": launch["ok"],
            }
        steps.append({"key": "launch", "ok": True, "skipped": True,
                      "reason": "launch=false" if not body.launch else "not installed"})
        return {"package": package, "apk_path": apk_path, "steps": steps, "ok": installed_now}

    @app.post("/api/inspect/map")
    async def inspect_map(body: InspectMapBody) -> dict[str, Any]:
        app_dir = _app_dir(body.app_id)
        status = decompile_status(app_dir)
        # We can still return the element info even if no sources are cached,
        # so the UI gets something useful while decompile is in flight.
        srcs = decompile_sources_dir(app_dir, status.get("sha") or "") if status.get("sha") else app_dir
        result = await map_tap_to_code(
            app_dir=app_dir,
            sha=status.get("sha"),
            sources_dir=srcs,
            x=body.x,
            y=body.y,
        )
        result["decompile_status"] = status.get("status")

        # Fuse the deterministic candidates (and, when available, RAG hits)
        # into a single ``best`` answer with reasoning so the UI doesn't have
        # to re-implement the picking heuristics. Identical logic is exposed
        # to the LLM agent via the resolve_ui_element skill.
        try:
            resolution = resolve_ui_element(
                element=result.get("element"),
                foreground_activity=result.get("foreground_activity"),
                candidates=result.get("candidates") or [],
                config=config,
                app_dir=app_dir,
            )
            result["resolution"] = {
                k: v for k, v in resolution.items() if k != "text"
            }
            result["resolution"]["summary"] = resolution.get("text", "")
        except Exception as e:  # pragma: no cover - defensive; never break /map
            logger.warning("resolve_ui_element fuser failed: %s", e)
            result["resolution"] = {"best": None, "alternatives": [], "error": str(e)}

        return result

    @app.post("/api/input/tap")
    async def input_tap(body: TapBody) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            "adb",
            "shell",
            "input",
            "tap",
            str(body.x),
            str(body.y),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        ok = proc.returncode == 0
        return {
            "ok": ok,
            "returncode": proc.returncode,
            "stderr": stderr.decode(errors="replace")[:2000] if stderr else "",
            "stdout": stdout.decode(errors="replace")[:500] if stdout else "",
        }

    @app.websocket("/ws/mirror")
    async def ws_mirror(websocket: WebSocket) -> None:
        """Stream PNG frames from ``adb exec-out screencap -p`` (polling)."""
        await websocket.accept()
        interval = max(0.05, config.web_screencap_interval_ms / 1000.0)
        try:
            while True:
                proc = await asyncio.create_subprocess_exec(
                    "adb",
                    "exec-out",
                    "screencap",
                    "-p",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if stdout and len(stdout) > 8 and stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                    await websocket.send_bytes(stdout)
                else:
                    err = (stderr or b"").decode(errors="replace")[:500]
                    await websocket.send_json(
                        {"type": "error", "message": "screencap failed (is adb connected?)", "detail": err}
                    )
                await asyncio.sleep(interval)
        except WebSocketDisconnect:
            logger.debug("mirror websocket disconnected")
        except Exception as e:
            logger.exception("mirror websocket error: %s", e)
            try:
                await websocket.close(code=1011)
            except Exception:
                pass

    async def _resolve_pid(package: str) -> Optional[int]:
        """Best-effort ``pidof <package>``; returns first PID or None."""
        proc = await asyncio.create_subprocess_exec(
            "adb", "shell", "pidof", "-s", package,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        token = (out or b"").decode(errors="replace").strip().split()
        if not token:
            return None
        try:
            return int(token[0])
        except ValueError:
            return None

    async def _resolve_uid(package: str) -> Optional[int]:
        """Resolve the stable Linux UID assigned to ``package``.

        Tries ``stat -c %u /data/data/<pkg>`` first (works on most modern
        devices, including non-rooted emulators because the directory is
        readable as part of the package's own world). Falls back to
        ``dumpsys package <pkg> | grep userId=`` which is also stable
        across app restarts.
        """
        proc = await asyncio.create_subprocess_exec(
            "adb", "shell", "stat", "-c", "%u", f"/data/data/{package}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        if proc.returncode == 0:
            tok = (out or b"").decode(errors="replace").strip()
            if tok.isdigit():
                return int(tok)

        proc = await asyncio.create_subprocess_exec(
            "adb", "shell", "dumpsys", "package", package,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        for line in (out or b"").decode(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("userId="):
                tail = line.split("=", 1)[1].split()[0]
                if tail.isdigit():
                    return int(tail)
        return None

    @app.websocket("/ws/logcat")
    async def ws_logcat(websocket: WebSocket, package: Optional[str] = None) -> None:
        """Stream logcat. Without ``?package=`` it tails the whole device.

        With ``?package=<pkg>`` we use the package's stable Linux UID
        (``adb logcat --uid=<uid>``) so the stream is keyed off the
        package identifier and survives PID changes (app restarts,
        force-stop / re-launch). We fall back to per-PID filtering on
        older devices that don't support ``--uid``.
        """
        await websocket.accept()
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            if not package:
                proc = await asyncio.create_subprocess_exec(
                    "adb", "logcat", "-v", "time", "-T", "50",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    await websocket.send_text(line.decode(errors="replace").rstrip("\n"))
                return

            # Package-scoped: try UID-based filtering once (stable across restarts).
            await websocket.send_text(f"# androscan: scoped logcat for {package}")
            uid = await _resolve_uid(package)
            if uid is not None:
                await websocket.send_text(
                    f"# androscan: attaching by package uid={uid} ({package})"
                )
                proc = await asyncio.create_subprocess_exec(
                    "adb", "logcat", "-v", "time", "-T", "50", f"--uid={uid}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    await websocket.send_text(line.decode(errors="replace").rstrip("\n"))
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                proc = None
                await websocket.send_text(
                    f"# androscan: uid stream ended for {package}; falling back to PID resolver"
                )

            # Fallback: per-PID re-resolution (older devices / restricted shells).
            while True:
                pid = await _resolve_pid(package)
                if pid is None:
                    await websocket.send_text(f"# androscan: {package} not running, waiting…")
                    await asyncio.sleep(_LOGCAT_PID_RESOLVE_INTERVAL_SEC)
                    continue
                await websocket.send_text(f"# androscan: attaching to {package} pid={pid}")
                proc = await asyncio.create_subprocess_exec(
                    "adb", "logcat", "-v", "time", "-T", "50", f"--pid={pid}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    await websocket.send_text(line.decode(errors="replace").rstrip("\n"))
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                proc = None
                await websocket.send_text(f"# androscan: {package} pid {pid} ended; re-resolving…")
                await asyncio.sleep(_LOGCAT_PID_RESOLVE_INTERVAL_SEC)
        except WebSocketDisconnect:
            logger.debug("logcat websocket disconnected")
        except Exception as e:
            logger.exception("logcat websocket error: %s", e)
        finally:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

    @app.get("/", response_model=None)
    async def spa_index() -> FileResponse | JSONResponse:
        index = STATIC_DIR / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            status_code=503,
            content={
                "message": "Workbench UI not built. Run: cd androscan/web/frontend && npm install && npm run build",
                "api": "/api/health",
            },
        )

    app.include_router(build_rag_router(config, _app_dir))
    app.include_router(build_graph_router(config, _app_dir))

    # Lazy Frida client provider — defined here (above the trace
    # router) so both the trace router (Phase 13 sub-step 13.2's
    # dynamic-trace endpoint) and the Hook Lab router below can
    # share the same provider closure. The provider construction
    # is cheap and only talks to ``frida-server`` on the first
    # ``client.is_available()`` / ``client.attach`` call, so wiring
    # it eagerly at app-startup time is safe even when ``[frida]``
    # isn't installed (the import is wrapped inside the function).
    def _frida_provider() -> Any:
        from androscan.adapters.frida_client import get_frida_client as _gfc
        return _gfc(app, _current_config())

    # Phase 10 sub-step 10.6: per-app Behavior Trace cache routes.
    # Pure-SQLite reads + a synchronous skill-invocation POST; the route
    # surface is intentionally narrow so 10.7's frontend can build
    # against it without further iteration. Phase 13 sub-step 13.2
    # also adds ``POST /{app_id}/dynamic`` + ``DELETE /{app_id}/
    # dynamic/{session_id}`` on this same REST router (they need
    # ``_frida_provider`` for the Frida session lifecycle); Phase 13
    # sub-step 13.3 adds the ``WS /ws/trace/{app_id}/{session_id}``
    # multiplexed-events surface as a separate router (mirrors the
    # ``build_frida_router`` (rest, ws) split since the WS prefix
    # differs from the REST prefix).
    #
    # Phase 13 sub-step 13.4 — wire the registered ``summarise_method``
    # skill behind 13.3's :data:`SummaryCallable` seam. The adapter
    # bounces the sync :func:`execute` into the default executor
    # (mirrors :func:`androscan.web.trace_summary.default_summary_callable`)
    # so the WS event loop stays unblocked while the LLM round-trip is
    # in flight; the timeout enforcement still happens at the WS-handler
    # layer via :func:`asyncio.wait_for` against
    # :data:`SUMMARY_TIMEOUT_S` from :mod:`trace_summary`. Cache writes
    # land in the same ``skill_results_cache.json`` slot 13.3 was
    # populating (skill_id ``"summarise_method"`` + the same 4-tuple
    # cache key), so any cached summaries an operator built up under
    # 13.3's inline-LLM default callable carry forward verbatim.
    _summary_callable = _build_summarise_method_callable(
        _current_config, _app_dir
    )
    _trace_rest, _trace_ws = build_trace_router(
        _current_config, _app_dir, _frida_provider, _summary_callable
    )
    app.include_router(_trace_rest)
    app.include_router(_trace_ws)
    app.include_router(
        build_status_router(
            config_provider=_current_config,
            apps_root_provider=lambda: root,
            app_dir_resolver=_app_dir,
        )
    )
    app.include_router(
        build_settings_router(
            config_provider=_current_config,
            set_config=_set_config,
            apps_root_provider=lambda: root,
            app_dir_resolver=_app_dir,
        )
    )

    # Hook Lab routes (DEC-023, sub-step 4.5). The router factory
    # returns ``(rest_router, ws_router)``; we mount both. The Frida
    # client is created lazily inside ``_frida_provider`` (defined
    # just above the trace router) so a missing ``[frida]`` extra
    # still lets the *templates* + *render* routes work (they're
    # pure Python).
    _frida_rest, _frida_ws = build_frida_router(
        config_provider=_current_config,
        apps_root_provider=lambda: root,
        app_dir_resolver=_app_dir,
        frida_client_provider=_frida_provider,
    )
    app.include_router(_frida_rest)
    app.include_router(_frida_ws)

    # Hook Lab Frida adapter (DEC-023): detach any live sessions when uvicorn
    # tears the app down, so the next workbench start doesn't trip over a
    # stale frida.core.Session referencing a process that no longer exists.
    # Guarded so the import is a no-op when ``[frida]`` isn't installed.
    @app.on_event("shutdown")
    async def _detach_frida_sessions() -> None:  # pragma: no cover - shutdown path
        try:
            from androscan.adapters.frida_client import FridaClient
        except Exception:
            return
        client = getattr(app.state, "frida_client", None)
        if isinstance(client, FridaClient):
            try:
                client.detach_all()
            except Exception as e:
                logger.debug("frida detach_all on shutdown raised: %s", e)

    # Vite places hashed bundles under /assets (do not mount "/" — would shadow /api).
    from starlette.staticfiles import StaticFiles

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    return app
