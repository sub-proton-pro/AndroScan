"""LLM-requestable skill: suggest trace-entry-method candidates from a
natural-language description.

Phase 11 v2.1 sub-step v2.1.5 — Tier-3 of the v2.1 entry-method
discoverability scope (DEC-025 v2.1 closing-note Q7 / Q8 / Q11). The
operator describes what they want to trace in plain English (e.g.
"the password verification flow", "where the app talks to the bank
backend"); this skill runs RAG over decompiled sources, gathers
call-graph context for the matched classes, and asks the LLM to rank
the top-3 candidate Smali entry methods with a one-line rationale +
confidence per candidate.

The result is surfaced via the v2.1.5 chat-widget pattern:
``SkillResult.widgets`` carries a tuple of
``TraceEntryCandidateWidget`` dataclasses that the chat agentic loop
forwards through ``widget`` SSE events to the chat dock, where they
render as clickable cards with a "Trace this" button. On click, the
frontend writes ``pendingTraceEntry`` (re-uses the 10.8 / 11.2
plumbing) and flips the workbench to Lab → Trace mode (Q8 (a) —
auto-fire on landing if the candidate's smali_id is a complete
return-descriptor signature).

Fail-open contract — mirrors :mod:`search_decompiled_sources` and
:mod:`query_call_graph`: every unavailability mode (missing app
context, decompile cache not ready, RAG not built, call graph not
built, LLM transport error, JSON parse error) returns
``success=True`` with an empty ``widgets`` tuple and a clear
``[suggest_trace_entry] …`` text so the chat agentic loop can read
"nothing here, pivot" rather than loop on a hard failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from androscan.skills.base import (
    SkillContext,
    SkillMeta,
    SkillResult,
    TraceEntryCandidateWidget,
)

SKILL_META = SkillMeta(
    name="suggest_trace_entry",
    description=(
        "Suggest Smali trace-entry-method candidates from a natural-language "
        "description of the operator's intent. Combines RAG semantic search "
        "over decompiled sources + call-graph context + an LLM ranking pass "
        "to surface up to 3 ranked candidates with per-candidate rationale "
        "and confidence. Use when the operator asks 'where does X happen?' "
        "or 'what method handles Y?' — the candidates land in the chat dock "
        "as clickable widgets that auto-fire a behaviour trace on click."
    ),
    params_schema={
        "description": (
            "natural-language description of what the operator wants to "
            "trace (e.g. 'where the app validates passwords', 'the deep "
            "link handler for vsop://')"
        ),
        "app_id": (
            "optional explicit app_id; defaults to ``run_folder.parent.name`` "
            "(matches the same fallback ladder as :mod:`query_call_graph` / "
            ":mod:`resolve_ui_element`)"
        ),
    },
    tier="llm",
)

# ---------------------------------------------------------------------------
# Tunables
#
# DEC-025 v2.1 closing-note risk note: "the per-skill output budget of
# ~6 KB (DEC-022) might get tight on apps with very long class names +
# verbose rationale". The caps below keep the widget payload bounded
# while still leaving room for substantive rationale prose.

_RAG_TOP_K = 8
"""How many decompiled-source chunks to fetch from the RAG index. 8
matches the default ``search_decompiled_sources`` top_k — enough
breadth to surface candidate classes across packages, narrow enough
that the LLM ranking prompt stays under ~4 KB."""

_MAX_CANDIDATE_POOL = 12
"""Hard cap on the candidate pool size we hand to the LLM ranker.
Each RAG hit may surface multiple overloads via
``list_methods_on_class``; this cap stops a class with 50+
overloads from blowing through DEC-022's per-skill input budget."""

_MAX_CANDIDATES_RETURNED = 3
"""Per Q11 / Q12 of the v2.1.0 ratification — the chat widget
candidate-list caps at 3. Past 3 the operator is better served by
running a Browse-tree pivot than scanning a long widget list, and
past 3 the lower-confidence tail tends to be noise rather than
signal (mirrors v2.1.3's same cap on the fuzzy-match suggestion
list)."""

_MAX_RATIONALE_CHARS = 200
"""Per-candidate rationale cap. DEC-025 v2.1 closing-note explicitly
calls out 200 chars as the operator-readable target — long enough to
cite the specific evidence (file + line range), short enough to keep
the chat-widget card compact."""

_MAX_PREVIEW_CHARS = 240
"""Per-candidate decompiled-source preview length we feed to the LLM
ranker. Tighter than ``search_decompiled_sources``'s 320 — we're
ranking many candidates here, not previewing one method, so we trade
preview depth for breadth across the candidate pool."""

_OVERLOADS_PER_CLASS_CAP = 3
"""For each unique class in the RAG hits, how many method overloads
we surface to the LLM ranker. 3 covers the common "method + 2
overloads" Java pattern without blowing the candidate pool."""


def _resolve_app_dir(context: SkillContext, app_id: Optional[str]) -> Optional[Path]:
    """Locate ``apps/<app_id>/`` from the explicit arg + the run folder.

    Same fallback ladder as :mod:`query_call_graph` / :mod:`resolve_ui_element`
    / :mod:`trace_behavior` — the explicit ``app_id`` wins when supplied;
    otherwise we fall back to ``run_folder.parent`` which is exactly
    ``apps/<app_id>/`` for the active run.
    """
    rf = getattr(context, "run_folder", None)
    if rf is None:
        return None
    rf_path = Path(rf)
    apps_root = rf_path.parent.parent if rf_path.parent.parent.exists() else None
    if app_id and apps_root and (apps_root / app_id).is_dir():
        return apps_root / app_id
    if rf_path.parent.exists():
        return rf_path.parent
    return None


def _java_class_to_smali(java_class: str) -> str:
    """Convert dotted Java class name to canonical Smali type descriptor.

    ``com.example.Foo`` → ``Lcom/example/Foo;`` (preserves inner-class
    ``$`` separators verbatim). Pure / no I/O — used to bridge from
    the RAG hits' Java class names (the source-form name) to the call
    graph's Smali class column (the bytecode-form name).
    """
    return f"L{java_class.replace('.', '/')};" if java_class else ""


def _gather_candidates(
    cache_dir: Path,
    rag_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Materialise a flat candidate pool from RAG hits.

    Each RAG hit gives us a ``class_name`` + a chunk preview. We query
    the call graph for method overloads on each unique class, surface
    up to ``_OVERLOADS_PER_CLASS_CAP`` per class, and stop once the
    pool reaches ``_MAX_CANDIDATE_POOL``. The returned candidates each
    carry::

        {
            "smali_id": "Lcom/example/Foo;->onClick(Landroid/view/View;)V",
            "java_class": "com.example.Foo",
            "method_name": "onClick",
            "preview": "<truncated source>",
            "evidence": "<file>:<start_line>-<end_line>",
        }

    Lazy import for ``call_graph`` keeps skill discovery cheap on
    machines without a built decompile cache.
    """
    from androscan.analysis import call_graph

    pool: list[dict[str, Any]] = []
    seen_smali_ids: set[str] = set()
    seen_classes: set[str] = set()

    for hit in rag_hits:
        if len(pool) >= _MAX_CANDIDATE_POOL:
            break
        java_class = (hit.get("class_name") or "").strip()
        if not java_class or java_class in seen_classes:
            continue
        seen_classes.add(java_class)
        smali_class = _java_class_to_smali(java_class)
        if not smali_class:
            continue
        try:
            methods_payload = call_graph.list_methods_on_class(
                cache_dir, smali_class, limit=_OVERLOADS_PER_CLASS_CAP * 2,
            )
        except Exception:
            # Pathological row in the call graph store — skip the
            # class entirely rather than surface a partial candidate
            # list. Operator's other RAG hits still feed candidates.
            continue
        methods = methods_payload.get("methods") or []
        # Prefer methods whose name matches the RAG hit's method_name
        # (when the chunk is method-sized) — those are the most
        # likely candidates. Fallback: take the first N overloads.
        hit_method_name = (hit.get("method_name") or "").strip() or None
        ranked_methods = sorted(
            methods,
            key=lambda m: (
                0 if hit_method_name and m.get("method_name") == hit_method_name else 1,
                m.get("method_name") or "",
            ),
        )[: _OVERLOADS_PER_CLASS_CAP]

        preview = (hit.get("content") or "").strip()
        if len(preview) > _MAX_PREVIEW_CHARS:
            preview = preview[: _MAX_PREVIEW_CHARS - 1] + "…"

        evidence_pieces: list[str] = []
        f = hit.get("file")
        sl = hit.get("start_line")
        el = hit.get("end_line")
        if f and sl and el:
            evidence_pieces.append(f"{f}:{sl}-{el}")
        evidence = " | ".join(evidence_pieces) if evidence_pieces else "(no evidence)"

        for m in ranked_methods:
            sid = (m.get("smali_id") or "").strip()
            if not sid or sid in seen_smali_ids:
                continue
            seen_smali_ids.add(sid)
            pool.append(
                {
                    "smali_id": sid,
                    "java_class": java_class,
                    "method_name": m.get("method_name") or "",
                    "preview": preview,
                    "evidence": evidence,
                }
            )
            if len(pool) >= _MAX_CANDIDATE_POOL:
                break
    return pool


def _build_ranking_prompt(
    description: str, candidate_pool: list[dict[str, Any]],
) -> tuple[str, str]:
    """Build the (system, user) prompt for the LLM ranking pass.

    The system prompt locks the LLM into a strict JSON output shape
    with explicit caps (max 3 candidates, rationale ≤ 200 chars,
    confidence ∈ [0.0, 1.0]). The user prompt carries the operator's
    description + the candidate pool with previews + the explicit
    "rank by relevance" instruction.

    Pure / no I/O — used by ``execute`` and the test suite (which
    mocks the LLM client; the prompt body is one of the things the
    test suite pins to catch a future prompt-shape regression).
    """
    system = (
        "You are an Android reverse-engineering assistant. The operator has "
        "described what they want to trace in plain English. You have a "
        "candidate pool of Smali entry-method signatures with decompiled-"
        "source previews. Rank the top-3 candidates by relevance and emit "
        "JSON with the EXACT shape:\n\n"
        '{"candidates": [\n'
        '  {"smali_id": "<exact Smali id from the pool>", '
        '"rationale": "<≤200 char operator-facing explanation>", '
        '"confidence": <float in [0.0, 1.0]>}\n'
        ", ...]}\n\n"
        "Rules:\n"
        "- Return AT MOST 3 candidates. Fewer is fine if the pool only has "
        "weak matches; an empty list is acceptable for a fully unrelated pool.\n"
        "- Each smali_id MUST appear verbatim in the candidate pool — do "
        "NOT invent signatures.\n"
        "- Confidence reflects YOUR judgement on relevance; 1.0 = clearly "
        "matches, 0.5 = plausibly relevant, 0.2 = stretch.\n"
        "- The rationale should cite the specific evidence (file path / "
        "line range / method name) from the preview when possible.\n"
        "- Output JSON only — no prose preamble, no markdown fences."
    )
    pool_lines: list[str] = []
    for i, c in enumerate(candidate_pool, 1):
        pool_lines.append(
            f"{i}. {c['smali_id']}\n"
            f"   class: {c['java_class']}, method: {c['method_name']}\n"
            f"   evidence: {c['evidence']}\n"
            f"   preview: {c['preview']}"
        )
    user = (
        f'Operator description: "{description}"\n\n'
        f"Candidate pool ({len(candidate_pool)} entries):\n\n"
        + "\n\n".join(pool_lines)
        + "\n\nRank the top-3 candidates by relevance and emit JSON."
    )
    return system, user


def _parse_ranking_response(
    raw: str, valid_smali_ids: set[str],
) -> list[TraceEntryCandidateWidget]:
    """Parse the LLM's JSON response into validated widget objects.

    Strict validation — fails open (returns empty list) on malformed
    JSON, missing fields, hallucinated ``smali_id``s (not in the
    pool), or out-of-range confidence. The LLM is asked to return at
    most 3 candidates; we cap at that anyway as defence in depth.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return []
    out: list[TraceEntryCandidateWidget] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        sid = str(c.get("smali_id") or "").strip()
        # Hallucination guard: the LLM occasionally paraphrases a Smali
        # signature ("Foo.onClick" instead of the full id). Reject
        # anything not in the pool — the chat-widget renderer's
        # auto-fire on click would seed garbage into ``pendingTraceEntry``
        # and the trace_behavior skill would blow up downstream.
        if sid not in valid_smali_ids:
            continue
        rationale = str(c.get("rationale") or "").strip()
        if len(rationale) > _MAX_RATIONALE_CHARS:
            rationale = rationale[: _MAX_RATIONALE_CHARS - 1] + "…"
        try:
            conf = float(c.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        # Clamp to [0.0, 1.0] — the LLM occasionally returns >1 or <0.
        conf = max(0.0, min(1.0, conf))
        out.append(
            TraceEntryCandidateWidget(
                kind="trace_entry_candidate",
                smali_id=sid,
                rationale=rationale,
                confidence=round(conf, 4),
            )
        )
        if len(out) >= _MAX_CANDIDATES_RETURNED:
            break
    return out


def _format_summary_text(
    description: str,
    widgets: tuple[TraceEntryCandidateWidget, ...],
    pool_size: int,
) -> str:
    """Build the natural-language summary that lands in
    ``SkillResult.text``. The chat agentic loop feeds this back to the
    LLM as part of the next turn's context — keep it grounded so the
    LLM doesn't hallucinate candidates that didn't make the widget
    list.
    """
    if not widgets:
        return (
            f"[suggest_trace_entry] No confident matches for {description!r} "
            f"(searched a pool of {pool_size} candidate methods). The "
            "operator can refine the description or use the Browse panel."
        )
    lines = [
        f"[suggest_trace_entry] Surfaced {len(widgets)} candidate(s) for "
        f"{description!r} (from a pool of {pool_size}):"
    ]
    for i, w in enumerate(widgets, 1):
        lines.append(f"  {i}. {w.smali_id}  [confidence={w.confidence:.2f}]")
        if w.rationale:
            lines.append(f"     rationale: {w.rationale}")
    return "\n".join(lines)


def execute(params: dict, context: SkillContext) -> SkillResult:
    description = (params.get("description") or "").strip()
    if not description:
        return SkillResult(
            success=False,
            data=None,
            text="[suggest_trace_entry] 'description' is required.",
        )

    app_id = (params.get("app_id") or "").strip() or None
    app_dir = _resolve_app_dir(context, app_id)
    if app_dir is None or not app_dir.is_dir():
        return SkillResult(
            success=True,
            data=None,
            text=(
                f"[suggest_trace_entry] No app directory available for "
                f"app_id={app_id!r}; cannot surface candidates."
            ),
        )

    # Lazy imports — keeps skill discovery cheap on machines without
    # the [rag] extra installed and without a built decompile cache.
    try:
        from androscan.analysis import call_graph
        from androscan.rag.embed import EmbedProviderError, get_provider
        from androscan.rag.index import get_status as rag_status
        from androscan.rag.search import query as rag_query
        from androscan.web.decompile_cache import (
            cache_root_for as decompile_cache_root,
            get_status as decompile_status,
        )
    except Exception as e:
        return SkillResult(
            success=True,
            data=None,
            text=f"[suggest_trace_entry] analysis layer unavailable: {e}",
        )

    ds = decompile_status(app_dir)
    sha = ds.get("sha")
    if ds.get("status") != "ready" or not sha:
        return SkillResult(
            success=True,
            data=None,
            text=(
                f"[suggest_trace_entry] Decompile cache not ready "
                f"(status={ds.get('status')}). Run jadx via the workbench first."
            ),
        )

    cache_dir = decompile_cache_root(app_dir, sha)

    cg_status = call_graph.get_status(cache_dir)
    if cg_status.status != "ready":
        return SkillResult(
            success=True,
            data=None,
            text=(
                f"[suggest_trace_entry] Call graph not ready "
                f"(status={cg_status.status}, error={cg_status.error or '(none)'}). "
                "Click Rebuild on the Settings → Status card."
            ),
        )

    rs = rag_status(cache_dir)
    if rs.status != "ready":
        return SkillResult(
            success=True,
            data=None,
            text=(
                f"[suggest_trace_entry] RAG index not ready "
                f"(status={rs.status}, error={rs.error or '(none)'})."
            ),
        )

    try:
        provider = get_provider(context.config)
    except EmbedProviderError as e:
        return SkillResult(
            success=True,
            data=None,
            text=f"[suggest_trace_entry] Embed provider unavailable: {e}",
        )

    try:
        hits = rag_query(cache_dir, description, provider, top_k=_RAG_TOP_K)
    except EmbedProviderError as e:
        return SkillResult(
            success=True,
            data=None,
            text=f"[suggest_trace_entry] RAG query failed: {e}",
        )

    if not hits:
        return SkillResult(
            success=True,
            data=None,
            text=(
                f"[suggest_trace_entry] RAG returned no hits for "
                f"{description!r}. The decompiled sources may not cover "
                "this functionality, or the description is too generic."
            ),
        )

    hit_dicts = [h.to_dict() for h in hits]
    candidate_pool = _gather_candidates(cache_dir, hit_dicts)
    if not candidate_pool:
        return SkillResult(
            success=True,
            data=None,
            text=(
                f"[suggest_trace_entry] RAG matched {len(hits)} chunk(s) but "
                "no method-level entries surfaced from the call graph "
                "(possible call-graph build error). Try Browse-tree pivot."
            ),
        )

    # LLM ranking pass — last step, lazy-import the client so test
    # suites can monkeypatch it without importing the heavy llm
    # transport on collection.
    try:
        from androscan.llm.client import complete
    except Exception as e:
        return SkillResult(
            success=True,
            data=None,
            text=f"[suggest_trace_entry] LLM client unavailable: {e}",
        )

    system, user = _build_ranking_prompt(description, candidate_pool)
    try:
        result = complete(
            user,
            config=context.config,
            system_content=system,
            stream=False,
            response_format="json",
        )
    except Exception as e:
        return SkillResult(
            success=True,
            data=None,
            text=(
                f"[suggest_trace_entry] LLM ranking call failed: "
                f"{type(e).__name__}: {e}"
            ),
        )

    raw_text = getattr(result, "content", None) or getattr(result, "text", None) or ""
    if not raw_text:
        return SkillResult(
            success=True,
            data=None,
            text="[suggest_trace_entry] LLM returned an empty response.",
        )

    valid_smali_ids = {c["smali_id"] for c in candidate_pool}
    widgets = tuple(_parse_ranking_response(raw_text, valid_smali_ids))

    summary = _format_summary_text(description, widgets, pool_size=len(candidate_pool))
    return SkillResult(
        success=True,
        data={
            "candidate_pool_size": len(candidate_pool),
            "rag_hits": len(hits),
            "candidates": [
                {
                    "smali_id": w.smali_id,
                    "rationale": w.rationale,
                    "confidence": w.confidence,
                }
                for w in widgets
            ],
        },
        text=summary,
        widgets=widgets,
    )
