"""HTTP routes for the per-app Behavior Trace cache (Phase 10 sub-step 10.6).

Sibling to :mod:`androscan.web.graph_routes` in spirit: the actual
SQLite backend lives in :mod:`androscan.internal.trace_cache`; this
module only does HTTP plumbing, argument validation, per-app-dir
look-up, and skill invocation for the build path.

Endpoints (prefix ``/api/trace``):

* ``GET    /{app_id}/status``            — cache state + decompile /
                                           call-graph readiness fan-out
                                           (mirrors the graph_routes
                                           status surface so the
                                           Settings tab can render both
                                           with the same component)
* ``GET    /{app_id}/anchors``           — list cached anchors
                                           (``[{entry_smali_id, hops,
                                           created_at}, ...]``)
* ``GET    /{app_id}/anchor``            — pure cache read for one
                                           anchor
                                           (``?entry=<smali_id>&hops=<n>``);
                                           404 when not cached
* ``POST   /{app_id}/anchor``            — invoke ``trace_behavior``
                                           skill, build/refresh the
                                           anchor, persist, return the
                                           populated payload;
                                           ``?force=true`` bypasses the
                                           cache and re-traces from
                                           scratch
* ``DELETE /{app_id}/anchor``            — drop one cached row
* ``GET    /{app_id}/anchored-methods``  — Phase 11 sub-step 11.3
                                           overlay feed: dedupe every
                                           ``(class_smali, method_name)``
                                           pair touched by any cached
                                           anchor's ``decisions`` list
                                           with the most-recent
                                           ``(hops, created_at)`` per
                                           method; consumed by
                                           ``CallGraphView``'s
                                           ``BehaviorAnchor``-aware
                                           overlay layer in Manual
                                           Hooks mode

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
import sqlite3
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

    # ----------------------------------------------------------------------
    # GET /{app_id}/anchored-methods — Phase 11 sub-step 11.3 overlay feed
    #
    # Walks every cached anchor, decodes the ``BehaviorAnchor`` payload,
    # enumerates ``decisions[*].method``, dedupes on
    # ``(class_smali, method_name)`` keeping the most-recent
    # ``(hops, created_at)`` per method (most-recent = larger
    # ``created_at``; ties broken by larger ``hops`` since a deeper
    # trace is more thorough). Cheap because the cached-anchor count
    # is bounded (typically ≤ 50 per app) and the per-row JSON unpack
    # is the same encoder/decoder used by every other cache read.
    #
    # Why a dedicated endpoint (vs. inferring from ``/anchors`` + N
    # follow-up ``/anchor`` calls): the consumer is the call-graph
    # overlay in Manual Hooks mode, which needs the full set in one
    # round-trip to render the ⚓ glyphs in a single Cytoscape
    # restyle pass. N+1 fetches against the per-anchor endpoint
    # would either flicker the overlay (each response triggers a
    # restyle) or require a coordinator on the frontend that
    # re-implements server-side dedupe.
    #
    # Schema invariance: the per-row payload unpack uses
    # ``trace_cache.anchor_from_json``, which already handles both
    # v1 and v2 ``BehaviorAnchor`` payload shapes via additive
    # field defaults — so this endpoint stays correct under 11.6's
    # ``SCHEMA_VERSION`` bump (``"1"`` → ``"2"``) without code
    # changes here.

    @router.get("/{app_id}/anchored-methods")
    def trace_list_anchored_methods(app_id: str) -> dict[str, Any]:
        """List every ``(class_smali, method_name)`` pair touched by
        any cached anchor's decision closure, with the most-recent
        ``(hops, created_at)`` per method.

        Response shape::

            {
                "app_id": "<id>",
                "sha":    "<decompile_sha>",
                "methods": [
                    {
                        "class_smali": "Lcom/example/Foo;",
                        "method_name": "bar",
                        "hops":        3,
                        "created_at":  1714500000.0,
                    },
                    ...
                ],
                "total":  N,
                "error":  null | "<one-line decode-failure summary>",
            }

        Status codes:

        * **404** when no ``trace.sqlite`` exists yet (per the 11.3
          contract — operators see "no traces have ever been built"
          as a different empty state than "all built traces have
          been deleted").
        * **200 + empty methods** when ``trace.sqlite`` exists but
          the ``anchors`` table is empty.
        * **200 + ``error`` field set** when at least one cached
          anchor's payload couldn't be decoded — partial results
          still returned (decoded rows are kept) so the operator's
          overlay still renders the methods we *can* read.
        """
        # ``_cache_dir_for`` raises 409 when decompile is not ready
        # (same precondition every other route in this module uses).
        # Unknown ``app_id`` → 404 via ``app_dir_resolver``.
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
        cache_dir = decompile_cache_root(app_dir, sha)

        # The "unbuilt trace cache" 404 case — distinguishes "operator
        # has never built a trace for this app" (no SQLite file) from
        # "operator has built and then deleted everything" (SQLite
        # file exists, anchors table empty → 200 + empty list).
        # Frontend's overlay code can treat both as "no glyphs" but
        # the operator-facing empty state in Trace mode uses this
        # distinction to nudge "click Build to start".
        db_path = trace_cache.trace_cache_db_path(cache_dir)
        if not db_path.is_file():
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No trace cache built yet for app {app_id!r}. "
                    "POST /api/trace/{app_id}/anchor first."
                ),
            )

        rows = trace_cache.list_anchors(cache_dir)
        # Per-method aggregator. Key = (class_smali, method_name);
        # value = the row dict we'll emit. We update on every visit
        # iff the new row's (created_at, hops) sorts *strictly later*
        # — keeps the most-recent + most-thorough trace's metadata
        # for the operator's tooltip.
        agg: dict[tuple[str, str], dict[str, Any]] = {}
        decode_errors: list[str] = []

        for row in rows:
            entry = row.get("entry_smali_id")
            hops = row.get("hops")
            created_at = row.get("created_at")
            if not isinstance(entry, str) or not isinstance(hops, int):
                continue  # Defensive — list_anchors rows are well-typed.
            try:
                anchor = trace_cache.read_anchor(cache_dir, entry, int(hops))
            except (sqlite3.DatabaseError, ValueError) as exc:  # pragma: no cover - read_anchor swallows
                decode_errors.append(f"{entry}#{hops}: {exc}")
                continue
            if anchor is None:
                # Either gone-since-list-anchors or payload decode
                # failed inside ``read_anchor`` (which fail-soft
                # logs + returns None). Surface the latter as a
                # decode error so the operator sees "X anchors
                # couldn't be decoded" rather than silent partial
                # results.
                decode_errors.append(f"{entry}#{hops}: payload unreadable")
                continue
            for decision in anchor.decisions:
                method = decision.method
                # ``MethodRef.class_name`` is the Java form
                # (``com.example.Foo``); the call-graph store keys on
                # the Smali descriptor (``Lcom/example/Foo;``), and
                # the frontend's ``hitKey`` joins on that — so we
                # convert here so the overlay's join is direct.
                class_smali = f"L{method.class_name.replace('.', '/')};"
                key = (class_smali, method.method_name)
                prior = agg.get(key)
                # Most-recent wins: larger created_at first; if equal,
                # larger hops (more thorough trace) wins. Initial
                # insert always wins regardless of values.
                if prior is None or (
                    created_at,
                    hops,
                ) > (
                    prior["created_at"],
                    prior["hops"],
                ):
                    agg[key] = {
                        "class_smali": class_smali,
                        "method_name": method.method_name,
                        "hops": int(hops),
                        "created_at": float(created_at) if created_at is not None else 0.0,
                    }

        methods = sorted(
            agg.values(),
            key=lambda m: (m["class_smali"], m["method_name"]),
        )
        # Single-line error summary keeps the response shape stable
        # — the frontend just renders the field as-is in a small
        # operator-readable banner if non-null.
        error = (
            f"{len(decode_errors)} anchor(s) failed to decode: "
            + "; ".join(decode_errors[:3])
            + ("..." if len(decode_errors) > 3 else "")
        ) if decode_errors else None
        return {
            "app_id": app_id,
            "sha": sha,
            "methods": methods,
            "total": len(methods),
            "error": error,
        }

    return router
