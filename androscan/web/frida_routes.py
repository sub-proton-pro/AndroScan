"""HTTP + WebSocket surface for the Hook Lab (DEC-023, sub-step 4.5).

Endpoints
---------
* ``GET    /api/frida/templates``                       — list of v1 templates.
* ``GET    /api/frida/templates/{template_id}``         — single template.
* ``POST   /api/frida/render``                          — render template + ``pyjsparser`` validate.
* ``POST   /api/frida/sessions``                        — render + allowlist + attach + load_script.
* ``GET    /api/frida/sessions``                        — list active sessions.
* ``GET    /api/frida/sessions/{session_id}``           — single session detail.
* ``DELETE /api/frida/sessions/{session_id}``           — detach + flush JSONL.
* ``GET    /api/frida/sessions/{session_id}/events``    — ring-buffer snapshot.
* ``GET    /api/frida/sessions/{session_id}/export``    — JSONL trace download.
* ``WS     /ws/frida/sessions/{session_id}/trace``      — replay-then-stream live events.

Design
------
* **Allowlist enforcement.** ``POST /sessions`` reads the per-app
  ``hook.hook_target_package_prefix`` (defaulting to the app's own
  manifest package id) and rejects with **403** if ``package`` doesn't
  start with that prefix. This is the security boundary DEC-023 calls
  out — no JS gets loaded into a sibling app or system process by
  accident.
* **Render is server-side.** The frontend's Monaco preview displays
  the response of ``POST /render``; the *same* render runs again
  inside ``POST /sessions`` (intentional: clients can't smuggle
  modified JS by tampering between preview and inject).
* **WebSocket replay-then-stream.** On connect, we drain the ring
  buffer once (catch-up for late joiners) and *then* register the
  session's ``on_event`` hook to forward new events. This is the
  contract the frontend's ``useFridaTrace`` hook relies on; tests
  exercise the boundary by populating the ring before the WS
  connect.
* **JSONL persistence.** Sessions allocate
  ``apps/<app_id>/<run_ts>/frida/<session_id>.jsonl`` via
  :func:`androscan.internal.run_folder.create_run_folder` and wire it
  into :meth:`FridaSession.set_persistence_path`. The export endpoint
  is a thin ``StreamingResponse`` over the persisted file — the wire
  format on the WebSocket and the on-disk format are identical (see
  ``_event_to_jsonable``).
* **Optional [frida] extra.** All session routes return **503
  frida_unavailable** if :func:`FridaClient.is_available` is false;
  template + render routes work without the extra (they're pure
  Python).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from androscan.adapters.frida_client import (
    FridaClient,
    FridaSession,
    FridaUnavailableError,
    _event_to_jsonable,
    get_frida_client,
)
from androscan.adapters.frida_hooks import (
    HookParamError,
    HookTemplate,
    HookTemplateNotFound,
    list_templates,
    get_template,
    render as render_template,
)
from androscan.adapters.frida_hooks._jsparse import parse_frida_js
from androscan.internal.app_meta import load_app_meta
from androscan.internal.run_folder import create_run_folder
from androscan.web.per_app_settings import load_app_settings


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request bodies


class RenderBody(BaseModel):
    """Body for ``POST /api/frida/render``."""

    template_id: str = Field(..., min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)


class CreateSessionBody(BaseModel):
    """Body for ``POST /api/frida/sessions``.

    ``spawn`` mirrors the ``frida -f`` flag; we leave it off by default
    because operators usually want to attach to a running process the
    UI is already inspecting. ``persist`` lets the operator opt out of
    JSONL persistence (useful for noisy throwaway hooks); when omitted
    or true, persistence runs.
    """

    app_id: str = Field(..., min_length=1, max_length=128)
    package: str = Field(..., min_length=1, max_length=255)
    template_id: str = Field(..., min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)
    spawn: bool = False
    persist: bool = True


# ---------------------------------------------------------------------------
# Response shapes (returned as plain dicts to keep the OpenAPI surface tight)


def _template_to_payload(t: HookTemplate) -> dict[str, Any]:
    """Public shape — drops the raw JS / summary templates.

    The frontend never needs the raw template strings; it always goes
    through ``POST /render`` which returns the *rendered* output.
    Keeping the raw bodies private also means a future shift to
    server-side template signing doesn't break the wire schema.
    """
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "params": [
            {
                "name": p.name,
                "description": p.description,
                "required": p.required,
                "default": p.default,
            }
            for p in t.params
        ],
        "sensitive_apis": list(t.sensitive_apis),
    }


def _session_to_payload(session: FridaSession) -> dict[str, Any]:
    """Wire shape for session list / detail responses."""
    s = session.stats()
    return {
        "session_id": s["session_id"],
        "app_id": s.get("app_id"),
        "template_id": s.get("template_id"),
        "package": s["package"],
        "pid": s["pid"],
        "started_at": s.get("started_at"),
        "buffered": s["buffered"],
        "ring_capacity": s["ring_capacity"],
        "total_events": s["total_events"],
        "dropped": s["dropped"],
        "by_kind": s["by_kind"],
        "last_ts": s.get("last_ts"),
        "detached": s.get("detached", False),
        "persist_path": s.get("persist_path"),
        "persist_dropped": s.get("persist_dropped", 0),
    }


# ---------------------------------------------------------------------------
# Router factory


def build_frida_router(
    *,
    config_provider: Callable[[], Any],
    apps_root_provider: Callable[[], Path],
    app_dir_resolver: Callable[[str], Path],
    frida_client_provider: Callable[[], FridaClient],
) -> APIRouter:
    """Return the ``/api/frida`` + ``/ws/frida`` router.

    Dependency-injection seams mirror the rest of the web layer:

    * ``config_provider`` / ``apps_root_provider`` / ``app_dir_resolver``
      reuse the same callables ``status_routes`` / ``settings_routes``
      take so live ``Config`` reload + the test apps fixture flow
      through unchanged.
    * ``frida_client_provider`` returns the cached
      :class:`FridaClient`; tests pass a stub-backed instance, prod
      passes :func:`androscan.adapters.frida_client.get_frida_client`
      bound to the current ``app``.
    """

    router = APIRouter(prefix="/api/frida", tags=["frida"])
    ws_router = APIRouter(tags=["frida"])

    # -- templates --------------------------------------------------------

    @router.get("/templates")
    def get_templates() -> dict[str, Any]:
        return {"templates": [_template_to_payload(t) for t in list_templates()]}

    @router.get("/templates/{template_id}")
    def get_one_template(template_id: str) -> dict[str, Any]:
        try:
            t = get_template(template_id)
        except HookTemplateNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))
        return _template_to_payload(t)

    # -- render -----------------------------------------------------------

    @router.post("/render")
    def post_render(body: RenderBody) -> dict[str, Any]:
        try:
            t = get_template(body.template_id)
        except HookTemplateNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))
        try:
            rendered = render_template(t, body.params)
        except HookParamError as e:
            # 400 — operator-correctable: a required param is missing
            # or an unknown one was passed. Surfaced verbatim so the UI
            # can place the message under the offending input.
            raise HTTPException(status_code=400, detail=str(e))
        parse = parse_frida_js(rendered.js)
        return {
            "rendered": {
                "template_id": rendered.template_id,
                "js": rendered.js,
                "summary": rendered.summary,
                "params_used": rendered.params_used,
            },
            "parse": {
                "ok": parse.ok,
                "error": parse.error,
                "line": parse.line,
                "column": parse.column,
                "available": parse.available,
            },
        }

    # -- sessions ---------------------------------------------------------

    @router.post("/sessions")
    def create_session(body: CreateSessionBody) -> dict[str, Any]:
        # ---- App + per-app settings ------------------------------------
        app_dir = app_dir_resolver(body.app_id)
        if not app_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"unknown app_id: {body.app_id}")
        per_app = load_app_settings(app_dir)
        prefix = _resolve_target_prefix(per_app, app_dir)
        if not prefix:
            # No prefix configured *and* no manifest package id — the
            # operator must set ``hook.hook_target_package_prefix``
            # explicitly before any Inject can run. We fail closed
            # rather than allowing arbitrary packages.
            raise HTTPException(
                status_code=403,
                detail=(
                    "hook_blocked: no hook_target_package_prefix configured for this app. "
                    "Set it in Settings → per-app → Hook before retrying."
                ),
            )
        if not body.package.startswith(prefix):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"hook_blocked: package {body.package!r} does not start with the "
                    f"configured prefix {prefix!r}. Widen the prefix in Settings → "
                    "per-app → Hook to allow this package."
                ),
            )

        # ---- Render + JS pre-validate ----------------------------------
        try:
            t = get_template(body.template_id)
        except HookTemplateNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))
        try:
            rendered = render_template(t, body.params)
        except HookParamError as e:
            raise HTTPException(status_code=400, detail=str(e))
        parse = parse_frida_js(rendered.js)
        if not parse.ok and parse.available:
            # Parser available + JS broken = hard 400. We deliberately
            # *don't* block on ``available=False`` (the [frida] extra
            # might not include pyjsparser); the route layer surfaces
            # that as a soft warning in the response so the UI can
            # decide whether to soften the Inject button.
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "render_parse_error",
                    "message": parse.error or "JS failed to parse",
                    "line": parse.line,
                    "column": parse.column,
                },
            )

        # ---- Frida attach ----------------------------------------------
        client = frida_client_provider()
        if not client.is_available():
            raise HTTPException(
                status_code=503,
                detail=(
                    "frida_unavailable: install with `pip install -e '.[frida]'` "
                    "and ensure frida-server is running on the device."
                ),
            )
        try:
            session = client.attach(body.package, spawn=body.spawn)
        except FridaUnavailableError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            # Anything else (e.g. PermissionError from the device, a
            # gone-wrong handshake) is a 502 — the upstream service
            # said no, the workbench itself is healthy.
            logger.warning("frida.attach failed for %s: %s", body.package, e)
            raise HTTPException(status_code=502, detail=f"frida attach failed: {e}")

        session.app_id = body.app_id
        session.template_id = body.template_id

        # ---- JSONL persistence -----------------------------------------
        persist_path: Optional[Path] = None
        if body.persist:
            try:
                run_folder = create_run_folder(body.app_id, config=config_provider())
                frida_dir = run_folder / "frida"
                frida_dir.mkdir(parents=True, exist_ok=True)
                persist_path = frida_dir / f"{session.session_id}.jsonl"
                session.set_persistence_path(persist_path)
            except Exception as e:
                # Persistence is best-effort: log + continue without
                # tearing down the session. Operator sees ``persist_path=None``
                # in the response and can decide whether to abort.
                logger.warning(
                    "frida session %s: persistence setup failed: %s",
                    session.session_id, e,
                )
                persist_path = None

        # ---- Load the rendered script ----------------------------------
        try:
            session.load_script(rendered.js, name=f"androscan:{body.template_id}")
        except Exception as e:
            # Detach so we don't leak the half-initialised attach.
            try:
                session.detach()
            except Exception:  # pragma: no cover - secondary failure
                pass
            logger.warning(
                "frida session %s: load_script failed: %s",
                session.session_id, e,
            )
            raise HTTPException(status_code=502, detail=f"frida load_script failed: {e}")

        stats = session.stats()
        return {
            "session_id": session.session_id,
            "app_id": body.app_id,
            "template_id": body.template_id,
            "package": session.package,
            "pid": session.pid,
            "started_at": session.started_at,
            "ring_capacity": stats.get("ring_capacity"),
            "ws_url": f"/ws/frida/sessions/{session.session_id}/trace",
            "persist_path": str(persist_path) if persist_path else None,
            "summary": rendered.summary,
            "parse": {
                "ok": parse.ok,
                "error": parse.error,
                "line": parse.line,
                "column": parse.column,
                "available": parse.available,
            },
        }

    @router.get("/sessions")
    def list_sessions_route() -> dict[str, Any]:
        client = frida_client_provider()
        return {"sessions": [_session_to_payload(s) for s in client.list_sessions()]}

    @router.get("/sessions/{session_id}")
    def get_session_detail(session_id: str) -> dict[str, Any]:
        session = _require_session(frida_client_provider(), session_id)
        return _session_to_payload(session)

    @router.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, Any]:
        client = frida_client_provider()
        session = client.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session_id: {session_id}")
        try:
            session.detach()
        except Exception as e:
            logger.warning("frida session %s detach raised: %s", session_id, e)
        return {"ok": True, "session_id": session_id}

    @router.get("/sessions/{session_id}/events")
    def get_session_events(session_id: str, limit: int = Query(default=200, ge=1, le=10000)) -> dict[str, Any]:
        session = _require_session(frida_client_provider(), session_id)
        events = session.events(limit=limit)
        return {
            "session_id": session_id,
            "events": [_event_to_jsonable(e) for e in events],
        }

    @router.get("/sessions/{session_id}/export")
    def export_session_jsonl(session_id: str) -> StreamingResponse:
        session = _require_session(frida_client_provider(), session_id)
        stats = session.stats()
        path_str = stats.get("persist_path")
        if not path_str:
            raise HTTPException(
                status_code=404,
                detail=f"session {session_id} has no persistence path (created with persist=false)",
            )
        path = Path(path_str)
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"persistence file missing on disk: {path}",
            )

        def _stream() -> Any:
            with path.open("rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        return
                    yield chunk

        filename = path.name
        return StreamingResponse(
            _stream(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # -- WebSocket: replay-then-stream -----------------------------------

    @ws_router.websocket("/ws/frida/sessions/{session_id}/trace")
    async def ws_trace(websocket: WebSocket, session_id: str) -> None:
        """Replay the ring buffer once, then forward live events.

        Connection lifecycle:

        1. Accept the socket. If the session is unknown, send a
           structured error message and close with policy code 1008
           (matches FastAPI's convention for app-level rejections).
        2. Drain the current ring under the session lock (snapshot)
           and send each event as JSON.
        3. Register a non-async ``on_event`` hook that pushes
           subsequent events into an :class:`asyncio.Queue` via
           ``loop.call_soon_threadsafe``. The Frida message thread
           never blocks on the socket — congestion just deepens the
           queue, which is bounded.
        4. Run a forever-loop pumping the queue to the socket; on
           ``WebSocketDisconnect``, unregister the hook and return.

        The bounded queue (``maxsize=2000``) means a runaway producer
        eventually drops *queue* items rather than starving the event
        loop. Drops here are separate from the ring's ``dropped`` /
        the JSONL ``persist_dropped`` counters — they're WebSocket-
        backpressure drops and we send a single ``{type: 'drop',
        count: N}`` notice to the client when they happen.
        """

        await websocket.accept()
        client = frida_client_provider()
        session = client.get_session(session_id)
        if session is None:
            await websocket.send_json(
                {"type": "error", "error": "unknown_session", "session_id": session_id}
            )
            await websocket.close(code=1008)
            return

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=2000)

        # Step 2 — replay
        for event in session.events():
            try:
                await websocket.send_json(_event_to_jsonable(event))
            except Exception:
                # Client closed mid-replay. Bail without registering.
                return

        # Step 3 — register live hook
        previous_hook = session.on_event

        def _push(event: Any) -> None:
            # Runs on the Frida message thread. Never await here; we
            # bounce to the event loop via ``call_soon_threadsafe``.
            try:
                loop.call_soon_threadsafe(_offer, event)
            except RuntimeError:
                # Loop closed (server shutting down) — drop quietly.
                return

        def _offer(event: Any) -> None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Coalesce adjacent overflows into one drop counter
                # rather than spamming the client.
                try:
                    _ = queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - race
                    pass
                queue.put_nowait({"__drop__": True})

        session.on_event = _push

        try:
            while True:
                event = await queue.get()
                if isinstance(event, dict) and event.get("__drop__"):
                    await websocket.send_json({"type": "drop", "session_id": session_id})
                    continue
                await websocket.send_json(_event_to_jsonable(event))
        except WebSocketDisconnect:
            logger.debug("frida trace ws %s disconnected", session_id)
        except Exception as e:
            logger.warning("frida trace ws %s error: %s", session_id, e)
            try:
                await websocket.close(code=1011)
            except Exception:  # pragma: no cover - secondary close failure
                pass
        finally:
            # Restore the previous hook; do NOT detach — multiple WS
            # clients (e.g. the operator's two browser tabs) could be
            # observing the same session, and we don't want close-one
            # to kill the trace.
            if session.on_event is _push:
                session.on_event = previous_hook

    return router, ws_router


# ---------------------------------------------------------------------------
# Helpers shared between routes


def _require_session(client: FridaClient, session_id: str) -> FridaSession:
    """Look up ``session_id`` or raise ``404``."""
    session = client.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"unknown session_id: {session_id}")
    return session


def _resolve_target_prefix(per_app: dict[str, Any], app_dir: Path) -> Optional[str]:
    """Return the effective ``hook_target_package_prefix`` for this app.

    Mirrors the merge logic of :func:`effective_settings` for the hook
    section: per-app override wins, otherwise fall back to the raw
    manifest package id. Returning ``None`` means "no prefix
    configured" — the route layer treats that as a hard 403 so an
    operator can't accidentally Inject into an arbitrary package on a
    half-set-up app.
    """
    hook = per_app.get("hook") if isinstance(per_app, dict) else None
    override = hook.get("hook_target_package_prefix") if isinstance(hook, dict) else None
    if isinstance(override, str) and override.strip():
        return override.strip()
    meta = load_app_meta(app_dir)
    if not isinstance(meta, dict):
        return None
    dossier = meta.get("dossier") or {}
    apk_info = dossier.get("apk_info") if isinstance(dossier, dict) else None
    if not isinstance(apk_info, dict):
        return None
    pkg = apk_info.get("package")
    if isinstance(pkg, str) and pkg.strip():
        return pkg.strip()
    return None


__all__ = ["build_frida_router"]
