"""HTTP routes for the per-app Behavior Trace cache (Phase 10 sub-step 10.6).

Sibling to :mod:`androscan.web.graph_routes` in spirit: the actual
SQLite backend lives in :mod:`androscan.internal.trace_cache`; this
module only does HTTP plumbing, argument validation, per-app-dir
look-up, and skill invocation for the build path.

Endpoints (prefix ``/api/trace``):

* ``GET    /{app_id}/status``   — cache state + decompile / call-graph
                                  readiness fan-out (mirrors the
                                  graph_routes status surface so the
                                  Settings tab can render both with the
                                  same component)
* ``GET    /{app_id}/anchors``  — list cached anchors
                                  (``[{entry_smali_id, hops, created_at}, ...]``)
* ``GET    /{app_id}/anchor``   — pure cache read for one anchor
                                  (``?entry=<smali_id>&hops=<n>``);
                                  404 when not cached
* ``POST   /{app_id}/anchor``   — invoke ``trace_behavior`` skill,
                                  build/refresh the anchor, persist,
                                  return the populated payload;
                                  ``?force=true`` bypasses the cache
                                  and re-traces from scratch
* ``DELETE /{app_id}/anchor``   — drop one cached row

Wire shape contract (locked in 10.6, depended on by 10.7's
``BehaviorAnchorCard`` / ``DecisionTimeline`` / ``BypassPlanCard``):
the JSON returned by GET / POST is the canonical
:func:`androscan.internal.trace_cache.anchor_to_json` output —
``dataclasses.asdict(anchor)`` with stable key ordering. No per-route
mapping layer; any field-level change to :class:`BehaviorAnchor` ripples
through both the skill's ``SkillResult.data`` and these routes
identically. 10.7's frontend treats the response as the structural
truth.

Build vs read separation (operator mental model)
-------------------------------------------------

GET is a **pure cache read** — it never invokes the skill / never
runs the LLM / never writes to ``trace.sqlite``. POST is the
**only** entry point that triggers the
``parse → slice → classify → plan → LLM → persist`` pipeline. This
matches how operators describe the workflow ("look up vs compute")
and is the same separation the graph routes use (``GET /`` reads;
``POST /rebuild`` builds).

The skill is invoked **synchronously** (no background job queue in
v1) because per-anchor cost is bounded — the closure walk caps at
30 methods + one LLM call total. Async will land in v2 once the
Lab tab's UI starts firing builds in the background while the
operator works in another mode (10.7 / 10.8).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from androscan.analysis import call_graph
from androscan.config import Config
from androscan.internal import trace_cache
from androscan.skills import SkillContext, execute as execute_skill
from androscan.web.decompile_cache import (
    cache_root_for as decompile_cache_root,
    get_status as decompile_status,
)


logger = logging.getLogger(__name__)


# Hard caps — keep route inputs bounded even if the UI forgets its own limits.
# Values mirror the skill's clamps (``trace.max_hops_hard_cap``) so a request
# that exceeds them surfaces as 422 rather than silently clamping (operators
# benefit from the explicit signal that they hit the ceiling).
_MAX_HOPS = 6
_MAX_ENTRY_LEN = 500
# Synthetic run-folder leaf — the trace_behavior skill resolves
# ``app_dir = run_folder.parent``, so we just need *any* path under
# ``app_dir`` that doesn't shadow a real run folder. ``trace-build`` is
# distinctive enough to spot in logs without colliding with the
# timestamp-shaped run folder names produced by ``create_run_folder``.
_SYNTHETIC_RUN_LEAF = "trace-build"


AppDirResolver = Callable[[str], Path]
ConfigProvider = Callable[[], Config]


def build_trace_router(
    config_provider: ConfigProvider,
    app_dir_resolver: AppDirResolver,
) -> APIRouter:
    """Create the ``/api/trace`` router, wired to the live ``Config``
    + the same ``app_dir_resolver`` callable as the existing routers.

    ``app_dir_resolver(app_id) -> Path`` must raise
    :class:`HTTPException(404)` for unknown ids (same contract as
    ``rag_router`` / ``graph_router``); we don't ship our own.
    """
    router = APIRouter(prefix="/api/trace", tags=["trace"])

    # ----------------------------------------------------------------------
    # Helpers

    def _cache_dir_for(app_id: str) -> Path:
        """Resolve ``apps/<app_id>/.decompiled/<sha>/`` or 409 with a
        decompile-not-ready message. Mirrors the same precondition the
        graph router enforces — the trace cache is *inside* the
        decompile cache, so we can't even open the SQLite without
        knowing the sha."""
        app_dir: Path = app_dir_resolver(app_id)
        ds = decompile_status(app_dir)
        if ds.get("status") != "ready" or not ds.get("sha"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Decompile cache is not ready. "
                    "POST /api/decompile/{app_id} first."
                ),
            )
        sha = str(ds["sha"])
        return decompile_cache_root(app_dir, sha)

    def _validate_entry(entry: str) -> str:
        """Bound the entry-method param; reject empty / oversized inputs.
        Doesn't validate the smali_id grammar — the skill / cache layer
        treats unknown ids as cache misses, which is the correct
        operator UX (the LLM frequently passes the wrong descriptor on
        its first attempt + corrects on the next turn)."""
        s = (entry or "").strip()
        if not s:
            raise HTTPException(
                status_code=422,
                detail="'entry' query param is required (smali signature).",
            )
        if len(s) > _MAX_ENTRY_LEN:
            raise HTTPException(
                status_code=422,
                detail=f"'entry' exceeds the {_MAX_ENTRY_LEN}-char ceiling.",
            )
        return s

    def _invoke_trace_skill(
        app_dir: Path,
        entry: str,
        hops: int,
        force: bool,
    ) -> dict[str, Any]:
        """Run ``trace_behavior`` synchronously and return the parsed
        ``BehaviorAnchor`` JSON dict. Raises 404 for the skill's
        fail-open paths (the skill returns ``data=None`` for
        decompile-not-ready / call-graph-not-ready / unresolved
        entry — all three are operator-facing 404s when triggered
        via the route)."""
        ctx = SkillContext(
            config=config_provider(),
            run_folder=app_dir / _SYNTHETIC_RUN_LEAF,
            dossier_dict={},
            apk_path=None,
        )
        result = execute_skill(
            "trace_behavior",
            {"entry_method": entry, "hops": hops, "force": force},
            ctx,
        )
        if result.data is None:
            # Skill's fail-open envelope — surface as 404 so the
            # frontend renders the empty state with the skill's
            # explanation text rather than silently 200ing.
            raise HTTPException(status_code=404, detail=result.text or "trace not found")
        if not isinstance(result.data, dict):
            # Defensive — should never happen given the skill's
            # contract, but keep the route's response invariant
            # ("either an anchor dict or a 404") explicit.
            logger.warning(
                "trace_behavior returned non-dict data type=%s; treating as 404",
                type(result.data).__name__,
            )
            raise HTTPException(status_code=404, detail="trace data unavailable")
        return result.data

    # ----------------------------------------------------------------------
    # GET /{app_id}/status — cache + decompile + call-graph fan-out

    @router.get("/{app_id}/status")
    def trace_get_status(app_id: str) -> dict[str, Any]:
        """Surface the current build state. Mirrors the graph_router's
        status payload so the Settings tab + the LabTraceMode
        placeholder can render both with the same component shape."""
        app_dir: Path = app_dir_resolver(app_id)
        ds = decompile_status(app_dir)
        sha = ds.get("sha")
        if not sha:
            return {
                "app_id": app_id,
                "decompile_status": ds.get("status", "unknown"),
                "call_graph": {"status": "missing"},
                "trace_cache": {"status": "missing"},
            }
        cache_dir = decompile_cache_root(app_dir, sha)
        cg = call_graph.get_status(cache_dir).to_dict()
        tc_status = trace_cache.get_status(cache_dir)
        return {
            "app_id": app_id,
            "decompile_status": ds.get("status"),
            "call_graph": cg,
            "trace_cache": {
                "status": tc_status.status,
                "schema_version": tc_status.schema_version,
                "anchor_count": tc_status.anchor_count,
                "db_path": tc_status.db_path,
                "error": tc_status.error,
            },
        }

    # ----------------------------------------------------------------------
    # GET /{app_id}/anchors — list cached rows

    @router.get("/{app_id}/anchors")
    def trace_list_anchors(app_id: str) -> dict[str, Any]:
        """List every cached ``(entry_smali_id, hops, created_at)``
        triple, ordered by ``created_at DESC`` (most recent first —
        matches the operator's mental model of "what did I just trace?")."""
        cache_dir = _cache_dir_for(app_id)
        rows = trace_cache.list_anchors(cache_dir)
        return {"app_id": app_id, "anchors": rows}

    # ----------------------------------------------------------------------
    # GET /{app_id}/anchor?entry=...&hops=N — pure cache read

    @router.get("/{app_id}/anchor")
    def trace_get_anchor(
        app_id: str,
        entry: str = Query(..., max_length=_MAX_ENTRY_LEN),
        hops: int = Query(default=3, ge=1, le=_MAX_HOPS),
    ) -> dict[str, Any]:
        """Pure cache read — never invokes the skill. Returns the
        canonical ``BehaviorAnchor`` JSON shape (matches what POST
        returns + what the skill's ``SkillResult.data`` contains).
        404 when not cached so the frontend can flip into the "no
        anchor yet, click Build" empty state without parsing a 200
        with empty-tuple decisions."""
        e = _validate_entry(entry)
        cache_dir = _cache_dir_for(app_id)
        anchor = trace_cache.read_anchor(cache_dir, e, hops)
        if anchor is None:
            raise HTTPException(
                status_code=404,
                detail=f"No cached anchor for entry={e!r} hops={hops}.",
            )
        # Round-trip through the canonical encoder so the response is
        # byte-identical to what the skill returns + what 10.7 will
        # serialise back into (it's the same JSON either way; we go
        # through the encoder to keep a single source of truth for the
        # wire shape).
        return json.loads(trace_cache.anchor_to_json(anchor))

    # ----------------------------------------------------------------------
    # POST /{app_id}/anchor?entry=...&hops=N&force=B — build via skill

    @router.post("/{app_id}/anchor")
    def trace_build_anchor(
        app_id: str,
        entry: str = Query(..., max_length=_MAX_ENTRY_LEN),
        hops: int = Query(default=3, ge=1, le=_MAX_HOPS),
        force: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Invoke ``trace_behavior`` synchronously; persist the result;
        return the populated anchor JSON. ``force=true`` bypasses the
        ``trace.sqlite`` cache and re-runs the full pipeline (the
        skill writes the new payload back so subsequent GETs see it).

        Synchronous on purpose — per-anchor cost is bounded
        (≤ 30 methods, one LLM call) and operators expect the call
        to block. Async build queue is a v2 concern (see module
        docstring)."""
        e = _validate_entry(entry)
        app_dir: Path = app_dir_resolver(app_id)
        return _invoke_trace_skill(app_dir, e, hops, force)

    # ----------------------------------------------------------------------
    # DELETE /{app_id}/anchor?entry=...&hops=N — single-row eviction

    @router.delete("/{app_id}/anchor", status_code=204)
    def trace_delete_anchor(
        app_id: str,
        entry: str = Query(..., max_length=_MAX_ENTRY_LEN),
        hops: int = Query(default=3, ge=1, le=_MAX_HOPS),
    ) -> Response:
        """Drop one cached anchor. 204 on success; 404 when no
        matching row existed (so the frontend can distinguish
        "I deleted it" from "it was already gone")."""
        e = _validate_entry(entry)
        cache_dir = _cache_dir_for(app_id)
        deleted = trace_cache.delete_anchor(cache_dir, e, hops)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"No cached anchor for entry={e!r} hops={hops}.",
            )
        return Response(status_code=204)

    return router
