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

import difflib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

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


class NormaliseEntryRequest(BaseModel):
    """Phase 11 v2.1 sub-step v2.1.2 request body for
    ``POST /{app_id}/normalise-entry``.

    JSON body (rather than query param) because the operator's typed
    input may contain ``(``, ``)``, ``;``, ``/`` and other characters
    that need URL-encoding; passing through the body keeps the
    ``Content-Type: application/json`` contract clean and matches how
    every other ``POST`` body in the workbench is shaped (cf.
    :class:`androscan.web.graph_routes.PathsQuery` for the same
    pattern).
    """

    entry: str = Field(..., max_length=_MAX_ENTRY_LEN)


class SuggestSimilarClassesRequest(BaseModel):
    """Phase 11 v2.1 sub-step v2.1.3 request body for
    ``POST /{app_id}/suggest-similar-classes`` — the Tier-1 "Find
    similar classes" suggestion path that grows off the v2.1.2 ⚠
    "class not found in call graph" validation pill.

    Same body shape as :class:`NormaliseEntryRequest` so the frontend
    can pass the *exact* same operator-typed input through both
    endpoints without massaging the payload — the suggestion
    endpoint runs its own ``_coalesce_entry`` pass to extract the
    class portion + then fuzzy-matches against the call graph's
    class list.
    """

    entry: str = Field(..., max_length=_MAX_ENTRY_LEN)


# ---------------------------------------------------------------------------
# v2.1.3 — fuzzy-match Tier-1 constants
#
# Tunables for the ``_suggest_similar_classes`` helper. Kept module-
# scope (rather than inside the route closure) so tests can patch them
# if needed and so the values are documented alongside the rest of the
# route module's hard caps.
#
# v2.1.5 will add an LLM fallback path on top of this helper — the
# constants below stay pure-Python / no-LLM (hot path, sub-100ms
# target) and the LLM path will sit behind a separate budget knob.

_SIMILAR_CLASSES_LIMIT = 5
"""Hard cap on suggestion-candidate count returned. Five is a
deliberate operator-cognitive limit — past 5 the operator is
better off scrolling Browse than scanning a long suggestion list,
and past 5 the lower-similarity tail tends to be noise rather than
signal."""

_SIMILAR_CLASSES_CUTOFF = 0.6
"""Minimum :func:`difflib.SequenceMatcher.ratio` for a candidate to
be returned. Matches :func:`difflib.get_close_matches`'s default —
empirically catches single-character typos (``MainActvity`` →
``MainActivity``, ratio ≈ 0.92) without surfacing wholly-unrelated
classes that happen to share a prefix (``MainActivity`` →
``MainAdapter``, ratio ≈ 0.5)."""


def _looks_like_java_identifier(s: str) -> bool:
    """Lightweight check used by :func:`_coalesce_entry`'s dotted-Java
    path. A Java identifier is ``[A-Za-z_$][A-Za-z0-9_$]*``; we admit a
    digit-leading first char too so dex-merger output (``$lambda$0``,
    ``Foo$0``) parses cleanly.
    """
    if not s:
        return False
    return all(c.isalnum() or c in ("_", "$") for c in s)


def _coalesce_entry(
    entry: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Translate the operator's typed input into a canonical Smali
    method-prefix (or bare class descriptor) + the underlying Smali
    class descriptor. Phase 11 v2.1 sub-step v2.1.2 (Q5: A — class +
    method only; descriptors / overload resolution are
    ``MethodPicker``'s job).

    Returns ``(normalised_entry, smali_class, error)``:

    * On success — ``normalised_entry`` and ``smali_class`` are both
      populated; ``error`` is ``None``.
    * On parse failure — both string fields are ``None`` and ``error``
      carries an operator-readable one-liner (rendered inline as the
      ✗ pill in Trace mode).

    Recognised input shapes:

    * Already-canonical Smali ``Lcom/example/Foo;->onClick(Landroid/view/View;)V``
      → passes through unchanged (``smali_class`` extracted via
      :func:`call_graph._normalise_smali_class`).
    * Smali class+separator ``Lcom/example/Foo;->`` → passes through
      (operator's intent is "show me methods on this class"; the
      MethodPicker activates downstream).
    * Smali class+method-prefix ``Lcom/example/Foo;->onClick(`` →
      passes through (Inspect → Trace seed shape from 10.8).
    * Bare Smali class descriptor ``Lcom/example/Foo;`` → returned
      unchanged (operator can extend with ``->method`` or pick from
      the MethodPicker).
    * Dotted Java method ``com.example.Foo.onClick`` →
      ``Lcom/example/Foo;->onClick(``.
    * Dotted Java class ``com.example.Foo`` → ``Lcom/example/Foo;``.
    * Stack-trace line ``com.example.Foo.onClick(Foo.java:42)`` →
      ``Lcom/example/Foo;->onClick(`` (the ``(...)`` tail is dropped
      — it's a source location, not part of the method signature).
    * Java-method-arglist line ``com.example.Foo.onClick(int, String)``
      → ``Lcom/example/Foo;->onClick(`` (same heuristic — the tail is
      dropped because the descriptor list isn't in Smali form anyway).
    * Inner classes ``com.example.Foo$Inner.onClick`` →
      ``Lcom/example/Foo$Inner;->onClick(`` (the ``$`` is preserved as
      a class-name character, matching how dex / smali represent
      inner classes).
    * Default-package class ``MainActivity`` → ``LMainActivity;``.

    Rejected (returns parse error):

    * Empty / whitespace-only input.
    * Inputs whose dotted form has no UpperCamelCase segment in the
      class portion (``com.example.foo`` — operator probably typed
      a method name without its class).
    * Inputs containing characters outside
      ``[A-Za-z0-9_$./;()->]`` after the obvious-cleanup pass.
    * Smali-shaped inputs whose class portion fails
      :func:`call_graph._normalise_smali_class`'s validity check
      (``Lcom.bad name;`` etc.).

    Pure / no I/O — call-graph existence validation lives in the
    route layer (one ``list_methods_on_class`` query against the
    SQLite call graph).
    """
    s = (entry or "").strip()
    if not s:
        return None, None, "entry is empty"

    # ----- Branch 1: already-Smali class+method form ---------------------
    # The ``;->`` substring is the canonical class-vs-method separator
    # and is present in every shape we want to pass through unchanged.
    if s.startswith("L") and ";->" in s:
        klass_part, _, _method_part = s.partition(";->")
        smali_class = call_graph._normalise_smali_class(klass_part + ";")
        if not smali_class:
            return (
                None,
                None,
                f"couldn't parse Smali class {klass_part + ';'!r}",
            )
        # Pass the input through unchanged — the operator already typed
        # canonical Smali, and any partial / full descriptor tail
        # carries information the picker / trace skill consumes
        # downstream (Inspect → Trace seed shape, full sig submit, etc.).
        return s, smali_class, None

    # ----- Branch 2: bare Smali class descriptor (no method) -------------
    if s.startswith("L") and s.endswith(";"):
        smali_class = call_graph._normalise_smali_class(s)
        if not smali_class:
            return None, None, f"couldn't parse Smali class {s!r}"
        return smali_class, smali_class, None

    # ----- Branch 3: Java forms (dotted, possibly with stack-trace
    #                location or method-arglist trailing) ------------------
    # Drop everything from the first ``(`` — that's either a
    # stack-trace location ``(Foo.java:42)`` or a Java method-arglist
    # ``(int, java.lang.String)``; either way we only need class +
    # method per Q5 (A) — the descriptors / overload resolution is
    # MethodPicker's job.
    paren_idx = s.find("(")
    if paren_idx >= 0:
        s = s[:paren_idx].rstrip()
        if not s:
            return None, None, "couldn't parse entry — empty before '('"

    parts = s.split(".") if "." in s else [s]
    for p in parts:
        if not _looks_like_java_identifier(p):
            return (
                None,
                None,
                f"couldn't parse {entry!r} — invalid identifier {p!r}",
            )

    # Method-name detection: the last segment that starts with a
    # lowercase letter is the method (Java naming convention — methods
    # start lowercase, classes start UpperCamelCase). If every segment
    # starts uppercase, the input is a bare class name.
    method_name: Optional[str] = None
    if len(parts) >= 2 and parts[-1][:1].islower():
        method_name = parts[-1]
        class_parts = parts[:-1]
    else:
        class_parts = parts

    # The class parts must include at least one UpperCamelCase segment
    # — otherwise the input is a bare package prefix (``com.example``)
    # or a method name without its class (``com.example.foo``).
    if not any(p[:1].isupper() for p in class_parts):
        return None, None, (
            f"couldn't parse {entry!r} — no class name found "
            "(expected an UpperCamelCase class segment)"
        )

    java_class = ".".join(class_parts)
    smali_class = call_graph._normalise_smali_class(java_class)
    if not smali_class:
        return None, None, f"couldn't parse {entry!r} — bad class form"

    if method_name:
        return f"{smali_class}->{method_name}(", smali_class, None
    return smali_class, smali_class, None


def _smali_class_simple_name(smali_class: str) -> str:
    """Extract the simple-name (last class segment, including any
    inner-class ``$`` suffix) from a canonical Smali class
    descriptor. Used by :func:`_suggest_similar_classes` to fuzzy-
    match against the call graph's ``classes.simple_name`` column.

    Examples::

        "Lcom/example/MainActivity;"          → "MainActivity"
        "Lcom/example/Foo$Inner;"             → "Foo$Inner"
        "LMainActivity;"                      → "MainActivity"
        ""                                    → ""
    """
    if not smali_class.startswith("L") or not smali_class.endswith(";"):
        return ""
    inner = smali_class[1:-1]  # strip leading L and trailing ;
    last_slash = inner.rfind("/")
    return inner[last_slash + 1:] if last_slash >= 0 else inner


def _suggest_similar_classes(
    typed_simple_name: str,
    candidate_classes: list[dict[str, str]],
    *,
    limit: int = _SIMILAR_CLASSES_LIMIT,
    cutoff: float = _SIMILAR_CLASSES_CUTOFF,
) -> list[dict[str, Any]]:
    """Fuzzy-match ``typed_simple_name`` against the simple-names of
    every class in ``candidate_classes`` (the materialised list from
    :func:`call_graph.list_class_names`). Returns up to ``limit``
    candidates with ratio >= ``cutoff``, sorted by descending
    similarity then by Smali class descriptor (deterministic ties
    so the test surface is stable).

    Each returned candidate is a dict::

        {
            "smali_class": "Lcom/example/MainActivity;",
            "simple_name": "MainActivity",
            "package":     "com.example",
            "rationale":   "fuzzy match on simple class name (similarity 0.92)",
            "confidence":  0.92,
        }

    Pure / no I/O — the SQLite read happens once in the route layer
    via :func:`call_graph.list_class_names`. Hot path: target
    sub-100ms even on a 50k-class app (one ``SequenceMatcher.ratio()``
    call per class, each O(N*M) on tiny strings).

    Phase 11 v2.1 sub-step v2.1.3 ships the fuzzy-only path. v2.1.5's
    ``suggest_trace_entry`` skill will wire an LLM-backed semantic-
    search fallback here when the fuzzy ratio falls below ``cutoff``
    AND the operator's input has at least 3 word-segments — that's
    a separate code-path on top of this helper, not a replacement.
    """
    if not typed_simple_name:
        return []
    target = typed_simple_name
    scored: list[tuple[float, dict[str, str]]] = []
    for c in candidate_classes:
        ratio = difflib.SequenceMatcher(None, target, c["simple_name"]).ratio()
        if ratio >= cutoff:
            scored.append((ratio, c))
    # Sort by descending similarity; tie-break on smali_class for
    # deterministic ordering (tests + operator UX).
    scored.sort(key=lambda t: (-t[0], t[1]["smali_class"]))
    out: list[dict[str, Any]] = []
    for ratio, c in scored[:limit]:
        out.append(
            {
                "smali_class": c["smali_class"],
                "simple_name": c["simple_name"],
                "package": c["package"],
                "rationale": (
                    f"fuzzy match on simple class name (similarity {ratio:.2f})"
                ),
                "confidence": round(float(ratio), 4),
            }
        )
    return out


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

    # ----------------------------------------------------------------------
    # POST /{app_id}/normalise-entry — Phase 11 v2.1 sub-step v2.1.2
    #                                  coalescer + call-graph validation
    #
    # Translates the operator's typed input (dotted Java, partial
    # Smali, or stack-trace line) into a canonical Smali method-prefix
    # (``Lcom/example/Foo;->onClick(``) and validates the underlying
    # class against the call graph. Powers Trace mode's debounced
    # inline spinner + ✓ / ⚠ validation pill — gives operators a fast
    # honest signal that the entry they're typing actually exists in
    # the app's call graph before they fire the synchronous (LLM-cost-
    # bearing) ``trace_behavior`` skill.
    #
    # Q5 (A): translate class + method name only; descriptor /
    # overload resolution is MethodPicker's job.
    # Q6 (B): backend (not frontend) so call-graph validation happens
    # in the same round-trip as the parse — frontend-only translation
    # would lack the validation signal that makes the spinner / pill
    # operator-meaningful.
    #
    # Status codes:
    # * 404 — unknown ``app_id`` (via ``app_dir_resolver``).
    # * 409 — call graph not ready (``_cache_dir_for`` precondition).
    # * 422 — un-parseable input (``_coalesce_entry`` returned an
    #         ``error``); the body's ``detail`` carries the operator-
    #         readable explanation that the frontend renders inline.
    # * 200 — parseable input (regardless of whether the class exists
    #         in the call graph; the response's ``class_exists_in_graph``
    #         + ``method_count`` carry the validation signal so the
    #         operator can distinguish "couldn't parse" from "parsed
    #         but the class isn't in the graph" — the latter is the
    #         entry point for v2.1.3's "Find similar classes"
    #         suggestions).

    @router.post("/{app_id}/normalise-entry")
    def trace_normalise_entry(
        app_id: str,
        body: NormaliseEntryRequest,
    ) -> dict[str, Any]:
        """Translate + validate. See module docstring above for the
        full behaviour contract.

        Response shape::

            {
                "normalised_entry":      "Lcom/example/Foo;->onClick(",
                "smali_class":           "Lcom/example/Foo;",
                "class_exists_in_graph": true,
                "method_count":          3,
                "error":                 null
            }

        ``method_count`` is the count of non-external method nodes on
        the class — same set the MethodPicker would surface — so the
        ✓ pill copy "valid class with N methods" matches what the
        operator would see if they expanded the picker.
        """
        cache_dir = _cache_dir_for(app_id)

        normalised_entry, smali_class, parse_error = _coalesce_entry(body.entry)
        if parse_error is not None:
            # 422 with the operator-readable detail. Frontend renders
            # this inline as the ✗ validation pill.
            raise HTTPException(status_code=422, detail=parse_error)

        # ``smali_class`` is guaranteed non-None on the success branch
        # (the helper's invariant), but mypy / ruff don't know that —
        # assert so the type narrowing is explicit.
        assert smali_class is not None
        assert normalised_entry is not None

        # Single SQLite query against the call-graph store. Cheap —
        # one ``COUNT(*)`` over a 2-table join — so we don't need to
        # cache the result. ``include_external=False`` so we only
        # surface methods the operator can actually trace; matches
        # the MethodPicker's default visibility.
        methods_payload = call_graph.list_methods_on_class(
            cache_dir,
            smali_class,
            limit=1,  # We only need ``total``; row payload is wasted.
            include_external=False,
        )
        method_count = int(methods_payload.get("total", 0))
        class_exists_in_graph = method_count > 0
        return {
            "normalised_entry": normalised_entry,
            "smali_class": smali_class,
            "class_exists_in_graph": class_exists_in_graph,
            "method_count": method_count,
            "error": None,
        }

    # ----------------------------------------------------------------------
    # POST /{app_id}/suggest-similar-classes — Phase 11 v2.1 sub-step v2.1.3
    #                                          Tier-1 "Find similar classes"
    #
    # Grows off v2.1.2's ⚠ "class not found in call graph" validation
    # pill: when the operator's typed input parses cleanly but the
    # class isn't in the call graph (a typo, a wrong package, an old
    # class name from a stale crash report), the frontend grows a
    # "Find similar classes" button next to the pill — clicking it
    # POSTs the same input here and renders the returned candidates as
    # clickable suggestion pills.
    #
    # v2.1.3 ships the fuzzy-match-only path (no LLM, sub-100ms);
    # v2.1.5 will wire an LLM-backed semantic-search fallback into
    # this same endpoint when the fuzzy match yields no candidates
    # AND the input has at least 3 word-segments. The endpoint shape
    # is forward-compatible with that extension — same response,
    # different ``rationale`` strings + slightly higher latency.
    #
    # Status codes:
    # * 404 — unknown ``app_id`` (via ``app_dir_resolver``).
    # * 409 — call graph not ready (``_cache_dir_for`` precondition).
    # * 422 — un-parseable input (``_coalesce_entry`` returned an
    #         ``error``); the body's ``detail`` carries the reason —
    #         the frontend hides the suggestion list in this case
    #         (the v2.1.2 ✗ pill is the relevant signal, not a sibling
    #         empty suggestion list).
    # * 200 — parseable input; ``candidates`` carries 0..N matches.
    #         An empty list is the "no fuzzy candidates" path —
    #         frontend renders "no similar classes found" copy
    #         beneath the suggestion-list region.

    @router.post("/{app_id}/suggest-similar-classes")
    def trace_suggest_similar_classes(
        app_id: str,
        body: SuggestSimilarClassesRequest,
    ) -> dict[str, Any]:
        """Fuzzy-match the operator's typed input against the call
        graph's class list. See module-level comment block above for
        the full behaviour contract.

        Response shape::

            {
                "candidates": [
                    {
                        "smali_class": "Lcom/example/MainActivity;",
                        "simple_name": "MainActivity",
                        "package":     "com.example",
                        "rationale":   "fuzzy match on simple class name (similarity 0.92)",
                        "confidence":  0.92,
                    },
                    ...
                ],
                "total": 3,
                "source": "fuzzy",
                "error": null
            }

        ``source`` is currently always ``"fuzzy"``; v2.1.5 will add
        ``"llm_fallback"`` as a second source value when the
        ``suggest_trace_entry`` skill backstops a no-fuzzy-match
        case. ``error`` is reserved for future non-blocking warnings
        (currently always ``null`` on a 200).
        """
        cache_dir = _cache_dir_for(app_id)

        # Re-use the v2.1.2 coalescer to extract the bare class form
        # from any of the input shapes the operator might paste
        # (dotted Java, partial Smali, stack-trace line). We only need
        # the ``smali_class`` portion here — the normalised method-
        # prefix isn't useful for fuzzy class matching.
        _normalised_entry, smali_class, parse_error = _coalesce_entry(body.entry)
        if parse_error is not None:
            raise HTTPException(status_code=422, detail=parse_error)
        assert smali_class is not None  # guaranteed on the success branch

        typed_simple = _smali_class_simple_name(smali_class)
        all_classes = call_graph.list_class_names(
            cache_dir, include_external=False
        )
        candidates = _suggest_similar_classes(
            typed_simple,
            all_classes,
        )
        return {
            "candidates": candidates,
            "total": len(candidates),
            "source": "fuzzy",
            "error": None,
        }

    return router
