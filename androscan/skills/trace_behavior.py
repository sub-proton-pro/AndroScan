"""LLM-tier ``trace_behavior`` skill — Phase 10 sub-step 10.5.

The capstone of Phase 10's static layer. Walks the call-graph forward
closure from an entry method, runs the full
``parse_decisions → slice_predicate_origins → classify_branch_outcomes
→ plan_bypasses`` pipeline over every method in the closure, builds a
populated :class:`BehaviorAnchor`, and feeds it to the LLM **once per
anchor** (per-decision LLM calls explicitly rejected per DEC-024) for:

(a) re-classification of gates whose heuristic ``confidence < 0.6``,
(b) author rationale prose for the operator,
(c) propose template-bound bypass plans for decisions where the
    deterministic planner returned empty.

Persists the populated anchor to
``apps/<app_id>/.decompiled/<sha>/trace.sqlite`` (per DEC-024) keyed
by ``(entry_method.smali_signature, hops)``. Subsequent calls hit
the cache directly unless the operator passes ``force=True``.

Read-only, ``requires_confirmation=False`` per DEC-022 / DEC-024 —
no device touching, no APK mutation, no Frida injection. The
operator-driven Hook Lab Stage→Inject path remains the only way for
any of this skill's output to actually run on a device; this skill
is pure prep.

Fail-soft posture (mirrors :mod:`query_call_graph` /
:mod:`search_decompiled_sources` per DEC-024):

* Missing app context, unbuilt decompile cache, missing call graph,
  or unresolved entry method → ``success=True`` with a clear ``text``
  explanation and ``data=None``. The LLM (or the route layer in
  10.6) reads the empty result and either picks a different tool or
  surfaces a helpful empty state to the operator.
* LLM call failure (network down, JSON parse error, all proposed
  plans invalid) → returns the deterministic-only payload with a
  ``[llm-skipped: <reason>]`` marker in ``text``. The static layer
  shipped in 10.1–10.4 is enough for an operator to make progress
  without LLM augmentation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import deque
from pathlib import Path
from typing import Any, Optional

from androscan.analysis.bypass_planner import partition_by_risk, plan_bypasses
from androscan.analysis.trace_types import (
    BehaviorAnchor,
    BranchOutcome,
    BranchVerdict,
    BypassPlan,
    DecisionPoint,
    MethodRef,
)
from androscan.skills.base import SkillContext, SkillMeta, SkillResult


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Locked v1 contract constants (justified inline so 10.6 routes / 10.7 UI /
# tests can pin against the same values without re-deriving them).

#: Hard cap on methods visited during the closure walk. Higher counts blow up
#: the per-anchor LLM token budget + the ``trace.sqlite`` payload size; lower
#: counts miss too many gates in real apps.
MAX_TRACE_METHODS = 30

#: The heuristic confidence threshold below which the LLM is asked to
#: re-classify (re-exported from :mod:`androscan.analysis.branch_classifier`
#: for the route layer's convenience — same value, single source of truth).
LLM_RECLASSIFY_THRESHOLD = 0.6

#: Confidence floor we attach to LLM-refined verdicts. Picked as
#: ``LLM_RECLASSIFY_THRESHOLD + 0.15`` so the UI can render an "LLM-refined"
#: badge without colliding with the heuristic moderate tier (0.85). Lower
#: than 1.00 so operators are nudged to spot-check LLM output.
LLM_RECLASSIFY_CONFIDENCE = 0.75

#: Hard wall-clock budget for the LLM call. Skill returns the deterministic
#: payload if the LLM exceeds this — caller-side timeouts on the request
#: layer do the actual enforcement; this is documentation.
_LLM_BUDGET_SEC = 60


SKILL_META = SkillMeta(
    name="trace_behavior",
    description=(
        "Behavior-trace from a Java entry method: walks the call-graph "
        "forward closure (≤ trace.max_hops_default hops, ≤ 30 methods), "
        "runs decision extraction + backward slicing + heuristic branch "
        "classification + template-bound bypass planning over each method, "
        "then asks the LLM to re-classify low-confidence gates and propose "
        "additional bypass plans. Persists the populated BehaviorAnchor to "
        "apps/<app_id>/.decompiled/<sha>/trace.sqlite for subsequent "
        "look-up. Read-only; safe to call without confirmation."
    ),
    params_schema={
        "entry_method": (
            "smali_id of the entry method, e.g. "
            "'Lcom/example/MainActivity;->onClick(Landroid/view/View;)V'. Required."
        ),
        "app_id": (
            "app_id (apps/<app_id>/) to trace. Optional; defaults to the "
            "current run's app_id derived from the skill context."
        ),
        "hops": (
            "optional; closure depth (default trace.max_hops_default = 3, "
            "clamped to trace.max_hops_hard_cap = 6)."
        ),
        "force": (
            "optional bool; if true, bypass the trace.sqlite cache and "
            "re-trace from scratch (default false)."
        ),
    },
    tier="llm",
    requires_confirmation=False,
)


# ---------------------------------------------------------------------------
# Public entry point


def execute(params: dict, context: SkillContext) -> SkillResult:
    entry_smali_id = (params.get("entry_method") or "").strip()
    if not entry_smali_id:
        return SkillResult(
            success=False, data=None,
            text="[trace_behavior] 'entry_method' is required (smali_id).",
        )

    app_id = (params.get("app_id") or "").strip() or None
    force = _coerce_bool(params.get("force"), default=False)

    # Resolve config knobs early so explicit / clamped hops are correct
    # even on the fail-open paths (the empty-result text mentions which
    # hops the call would have used).
    config = context.config
    hops = _coerce_int(
        params.get("hops"),
        default=int(getattr(config, "trace_max_hops_default", 3)),
        lo=1,
        hi=int(getattr(config, "trace_max_hops_hard_cap", 6)),
    )

    # Stage 1: locate the app dir + cache dir (mirrors query_call_graph).
    app_dir = _resolve_app_dir(context, app_id)
    if app_dir is None or not app_dir.is_dir():
        return _empty_result(
            entry_smali_id, hops,
            f"[trace_behavior] No app directory available for app_id={app_id!r}.",
        )
    cache_dir = _resolve_cache_dir(app_dir)
    if cache_dir is None:
        return _empty_result(
            entry_smali_id, hops,
            "[trace_behavior] Decompile cache not ready. Run jadx via the workbench first.",
        )

    # Stage 2: cache lookup (skipped on force=True).
    from androscan.internal import trace_cache

    if not force:
        cached = trace_cache.read_anchor(cache_dir, entry_smali_id, hops)
        if cached is not None:
            return _success_result(cached, prefix="[cached] ")

    # Stage 3: call-graph closure walk + per-method static layer.
    try:
        from androscan.analysis import call_graph
        from androscan.analysis import decisions as decisions_mod
        from androscan.analysis import branch_classifier, slicing, smali_parser
    except Exception as exc:  # pragma: no cover - defensive
        return _empty_result(
            entry_smali_id, hops,
            f"[trace_behavior] analysis layer unavailable: {exc}",
        )

    cg_status = call_graph.get_status(cache_dir)
    if cg_status.status != "ready":
        return _empty_result(
            entry_smali_id, hops,
            f"[trace_behavior] Call graph not ready (status={cg_status.status}).",
        )

    closure = _walk_closure(cache_dir, entry_smali_id, hops)
    if not closure.methods:
        return _empty_result(
            entry_smali_id, hops,
            f"[trace_behavior] Entry method {entry_smali_id!r} not found in call graph.",
        )

    # Stage 4: parse Smali once + filter to the closure methods.
    apktool_root = call_graph.apktool_out_dir(cache_dir)
    smali_roots = _smali_root_dirs(apktool_root)
    if not smali_roots:
        return _empty_result(
            entry_smali_id, hops,
            "[trace_behavior] Apktool output directory missing or empty.",
        )

    classes, _ = smali_parser.parse_classes(smali_roots)
    # Phase 11 sub-step 11.4 — pass ``include_branchless=True`` so the
    # slicer's bounded inter-procedural descent can reach helper-method
    # bodies that have no decisions of their own (pure getters,
    # arithmetic-only computations, deny-list-friendly stdlib wrappers).
    # The 10.x consumers below still ``continue`` on branchless methods
    # in the per-closure loop (line ~225 below) so the pipeline output
    # is byte-identical to v1 for inputs that don't benefit from
    # descent.
    method_decisions, _ = decisions_mod.parse_decisions(
        smali_roots, classes, include_branchless=True,
    )
    by_signature = {md.method_signature: md for md in method_decisions}
    classes_by_smali = {c.class_desc: c for c in classes}
    # Reflective-method set — used by ``is_stateless`` to refuse
    # descent into methods the call-graph indexer flagged with
    # ``may_have_unresolved_reflection``. Cached once per skill
    # invocation; the ``frozenset`` is hashable + cheap to pass
    # through to every per-decision slice call.
    reflective_method_sigs = call_graph.list_reflective_method_sigs(cache_dir)
    # Shared "closed economy" descent budget per the 11.4 spec:
    # depth + visited set are both consumed cumulatively across every
    # decision in the closure (so a hub-helper visited via decision A
    # isn't redundantly re-descended via decision B). 11.5's
    # field-write-site walking will draw from the same instance.
    descent_budget = slicing._DescentBudget.fresh()

    aggregated_decisions: list[DecisionPoint] = []
    aggregated_plans: list[BypassPlan] = []
    incomplete = False
    for sig in closure.methods:
        md = by_signature.get(sig)
        if md is None:
            # Closure node has no Smali body in the apktool tree
            # (compiled-away helper). Legitimate and just contributes
            # zero decisions.
            continue
        if not md.decision_points:
            # Branchless method (now reachable via ``include_branchless=True``
            # but contributes no decisions to the aggregation —
            # behaviour matches v1 for the per-closure loop).
            continue
        sliced = slicing.slice_predicate_origins(
            md,
            classes_by_smali=classes_by_smali,
            decisions_by_method_sig=by_signature,
            reflective_method_sigs=reflective_method_sigs,
            descent_budget=descent_budget,
        )
        classified = branch_classifier.classify_branch_outcomes(sliced)
        for dp in classified.decision_points:
            aggregated_decisions.append(dp)
            if dp.predicate_origin is None:
                incomplete = True
            for plan in plan_bypasses(
                dp,
                instructions=classified.instructions,
                label_index=dict(classified.label_index),
            ):
                aggregated_plans.append(plan)

    # Stage 5: identify LLM workload + invoke (fail-soft).
    low_conf_indices = tuple(
        dp.instruction_index
        for dp in aggregated_decisions
        if dp.branch_outcome is not None
        and dp.branch_outcome.confidence < LLM_RECLASSIFY_THRESHOLD
    )
    planless_keys = _planless_decision_keys(aggregated_decisions, aggregated_plans)
    rationale = ""
    llm_skip_reason: Optional[str] = None
    if low_conf_indices or planless_keys:
        llm_outcome = _invoke_llm(
            config=config,
            entry_smali_id=entry_smali_id,
            decisions=aggregated_decisions,
            low_confidence_indices=low_conf_indices,
            planless_keys=planless_keys,
        )
        if llm_outcome.error is None:
            aggregated_decisions = _apply_reclassifications(
                aggregated_decisions, llm_outcome.reclassifications
            )
            aggregated_plans.extend(
                _validate_proposed_plans(
                    llm_outcome.proposed_plans,
                    decisions_by_key={
                        (dp.method.smali_signature, dp.instruction_index): dp
                        for dp in aggregated_decisions
                    },
                )
            )
            rationale = llm_outcome.rationale
        else:
            llm_skip_reason = llm_outcome.error

    # Stage 6: risk-partition + truncation flag + entry MethodRef + persist.
    risk_threshold = str(getattr(config, "trace_bypass_risk_max", "medium"))
    default_plans, advanced_plans = partition_by_risk(
        tuple(aggregated_plans), risk_threshold
    )

    entry_method = _entry_method_ref(closure, entry_smali_id)
    anchor = BehaviorAnchor(
        entry_method=entry_method,
        hops=hops,
        truncated=closure.truncated,
        incomplete=incomplete,
        decisions=tuple(aggregated_decisions),
        plans=default_plans,
        advanced_plans=advanced_plans,
        rationale=rationale,
        low_confidence_decision_indices=low_conf_indices,
    )

    try:
        trace_cache.write_anchor(cache_dir, anchor)
    except (sqlite3.DatabaseError, OSError) as exc:
        # Persist failure should never fail the call — the operator
        # can re-run; the in-memory payload is still useful for this
        # turn's response.
        logger.warning("trace_behavior: failed to persist anchor: %s", exc)

    return _success_result(anchor, llm_skip_reason=llm_skip_reason)


# ---------------------------------------------------------------------------
# Closure walk (call-graph forward BFS).


class _Closure:
    """Lightweight namespace for the closure walk's outputs.

    A real dataclass would also work but this stays internal to the
    skill; keeping it nested keeps ``trace_types`` focused on the
    cross-module data model.
    """
    __slots__ = ("methods", "truncated", "entry_class_name")

    def __init__(self) -> None:
        self.methods: list[str] = []
        self.truncated: bool = False
        self.entry_class_name: Optional[str] = None


def _walk_closure(cache_dir: Path, entry_smali_id: str, hops: int) -> _Closure:
    """BFS forward from ``entry_smali_id``, collecting up to
    :data:`MAX_TRACE_METHODS` internal (non-external) callee
    smali_ids reached within ``hops`` levels."""
    from androscan.analysis import call_graph

    out = _Closure()

    # Up-front existence check on the entry node. The call_graph
    # neighbors() helper returns None for unknown smali_ids; an
    # unknown entry must surface as an empty closure so the skill
    # can fail open with a helpful "not found" message rather than
    # silently emit a zero-decision anchor for the ghost method.
    root_nb = call_graph.neighbors(cache_dir, entry_smali_id)
    if root_nb is None:
        return out

    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque()
    queue.append((entry_smali_id, 0))
    visited.add(entry_smali_id)

    while queue:
        sig, depth = queue.popleft()
        out.methods.append(sig)
        if len(out.methods) >= MAX_TRACE_METHODS:
            out.truncated = True
            break
        if depth >= hops:
            continue
        # Reuse the root probe for depth-0 to avoid an extra SQL hit;
        # subsequent depths walk the graph normally.
        nb = root_nb if (depth == 0 and sig == entry_smali_id) else call_graph.neighbors(cache_dir, sig)
        if nb is None:
            # Subsequent BFS hops won't usually reach this branch
            # since they came from edges that already validated
            # their endpoints, but defensive against schema drift.
            continue
        if depth == 0 and out.entry_class_name is None:
            node = nb.get("node") or {}
            classes = nb.get("classes") or []
            cls = next(
                (c for c in classes if c.get("id") == node.get("class_id")), None
            )
            if cls and cls.get("class_name"):
                out.entry_class_name = str(cls["class_name"])
        for callee in nb.get("callees") or []:
            cn = callee.get("node") or {}
            if cn.get("is_external"):
                continue
            child_sig = cn.get("smali_id")
            if not child_sig or child_sig in visited:
                continue
            visited.add(child_sig)
            queue.append((child_sig, depth + 1))
            if len(visited) >= MAX_TRACE_METHODS:
                # Pre-emptive truncation — the BFS may keep popping
                # depth-bound nodes after this point but we won't add
                # any more.
                pass
    return out


def _entry_method_ref(closure: _Closure, entry_smali_id: str) -> MethodRef:
    """Reverse-engineer a :class:`MethodRef` from the entry smali_id.
    Falls through to ``MethodRef.from_smali_signature`` for the parse;
    only thing we *can* infer beyond that is the human-readable class
    name (call_graph.classes carries the dot-form), but the smali_id
    parse already gives us the slash-form which is what
    ``smali_signature`` round-trips."""
    return MethodRef.from_smali_signature(entry_smali_id)


def _smali_root_dirs(apktool_root: Path) -> list[Path]:
    """Return the ``smali`` / ``smali_classes2`` / ... directories
    inside an apktool output tree, mirroring how the existing
    ``smali_parser`` test fixtures are laid out (and how a real
    apktool unpack lays them out)."""
    if not apktool_root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(apktool_root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name == "smali" or name.startswith("smali_classes"):
            out.append(child)
    return out


# ---------------------------------------------------------------------------
# LLM round-trip


class _LLMOutcome:
    """Container for the LLM call's parsed results — keeps the call
    site readable without proliferating top-level dataclasses."""
    __slots__ = ("rationale", "reclassifications", "proposed_plans", "error")

    def __init__(
        self,
        *,
        rationale: str = "",
        reclassifications: Optional[list[dict[str, Any]]] = None,
        proposed_plans: Optional[list[dict[str, Any]]] = None,
        error: Optional[str] = None,
    ) -> None:
        self.rationale = rationale
        self.reclassifications = reclassifications or []
        self.proposed_plans = proposed_plans or []
        self.error = error


def _invoke_llm(
    *,
    config: Any,
    entry_smali_id: str,
    decisions: list[DecisionPoint],
    low_confidence_indices: tuple[int, ...],
    planless_keys: tuple[tuple[str, int], ...],
) -> _LLMOutcome:
    """Call the LLM once per anchor with a structured JSON-mode prompt;
    parse the response into :class:`_LLMOutcome`. Any failure
    (transport error, JSON parse) yields ``error=<reason>`` and the
    caller falls back to the deterministic payload."""
    try:
        from androscan.llm.client import complete
    except Exception as exc:  # pragma: no cover - defensive
        return _LLMOutcome(error=f"llm-import: {exc}")

    system_content, user_prompt = build_trace_behavior_prompt(
        entry_smali_id=entry_smali_id,
        decisions=decisions,
        low_confidence_indices=low_confidence_indices,
        planless_keys=planless_keys,
    )
    try:
        result = complete(
            user_prompt,
            config=config,
            system_content=system_content,
            stream=False,
            response_format="json",
        )
    except Exception as exc:
        return _LLMOutcome(error=f"transport: {type(exc).__name__}: {exc}")

    raw_text = getattr(result, "content", None) or getattr(result, "text", None) or ""
    if not raw_text:
        return _LLMOutcome(error="empty-llm-response")
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return _LLMOutcome(error=f"json-parse: {exc.msg}")
    if not isinstance(parsed, dict):
        return _LLMOutcome(error="llm-response-not-object")

    rationale = str(parsed.get("rationale") or "").strip()
    reclassifications = parsed.get("reclassifications")
    proposed_plans = parsed.get("proposed_plans")
    return _LLMOutcome(
        rationale=rationale,
        reclassifications=list(reclassifications) if isinstance(reclassifications, list) else [],
        proposed_plans=list(proposed_plans) if isinstance(proposed_plans, list) else [],
    )


def build_trace_behavior_prompt(
    *,
    entry_smali_id: str,
    decisions: list[DecisionPoint],
    low_confidence_indices: tuple[int, ...],
    planless_keys: tuple[tuple[str, int], ...],
) -> tuple[str, str]:
    """Construct ``(system_content, user_prompt)`` for the LLM call.

    The system content pins the LLM to a strict JSON schema so the
    parser side has a stable contract. The user prompt enumerates
    the gates that need attention with enough context for the LLM
    to reason about each one (verdict, score, reasons, plus the
    enclosing method's signature) but stops short of dumping the
    full Smali — that's what the operator's manual review loop is
    for.

    Exposed at module level so tests can pin the prompt shape
    deterministically.
    """
    system_content = (
        "You are a security-analysis assistant helping an experienced "
        "Android pentester triage gate decisions in a single Java method "
        "and propose template-bound Frida bypass plans. The static layer "
        "has already enumerated the gates (DecisionPoints), classified "
        "each branch (deny / allow / neutral), and proposed deterministic "
        "bypass plans where it could.\n\n"
        "Your job is to (a) re-classify gates the heuristics flagged as "
        "low-confidence, (b) author a short rationale for the operator, "
        "(c) propose additional template-bound bypass plans for gates "
        "where the deterministic planner emitted nothing.\n\n"
        "Reply STRICTLY in JSON with this shape:\n"
        "{\n"
        "  \"rationale\": <string — 1-3 sentences for the operator>,\n"
        "  \"reclassifications\": [\n"
        "    {\"method\": <smali_signature>, \"instruction_index\": <int>,\n"
        "     \"branch_label\": <string>, \"verdict\": <\"deny\"|\"allow\"|\"neutral\">,\n"
        "     \"reason\": <string>}\n"
        "  ],\n"
        "  \"proposed_plans\": [\n"
        "    {\"method\": <smali_signature>, \"instruction_index\": <int>,\n"
        "     \"template_id\": <one of: force_return_value, force_method_skip, "
        "force_string_compare_equal>,\n"
        "     \"params\": {<template-specific>}, \"rationale\": <string>,\n"
        "     \"risk\": <\"low\"|\"medium\"|\"high\">}\n"
        "  ]\n"
        "}\n\n"
        "Rules: only propose plans for the requested decisions. Use only "
        "the three template_ids listed above. Never emit free-form JS — "
        "templates are the only source of hook code in v1. Keep the "
        "rationale focused on the operator's bypass strategy, not on "
        "describing what the static analysis already shows."
    )

    decisions_by_key = {
        (dp.method.smali_signature, dp.instruction_index): dp for dp in decisions
    }
    low_conf_decisions = [
        dp for dp in decisions if dp.instruction_index in low_confidence_indices
    ]
    planless_decisions = [
        decisions_by_key[k] for k in planless_keys if k in decisions_by_key
    ]

    parts: list[str] = [
        f"Entry method: {entry_smali_id}",
        f"Total decisions in closure: {len(decisions)}",
    ]
    if low_conf_decisions:
        parts.append("\n## Gates needing re-classification (heuristic confidence < 0.6)")
        for dp in low_conf_decisions:
            parts.append(_render_decision_for_prompt(dp))
    if planless_decisions:
        parts.append("\n## Gates with no deterministic bypass plan")
        for dp in planless_decisions:
            parts.append(_render_decision_for_prompt(dp))
    if not low_conf_decisions and not planless_decisions:
        # Defensive — caller checks before invoking, but keep the
        # prompt well-formed if invoked directly from tests.
        parts.append("\n(No gates flagged for LLM attention.)")

    return system_content, "\n".join(parts)


def _render_decision_for_prompt(dp: DecisionPoint) -> str:
    """Compact, deterministic rendering of one decision for the LLM
    prompt. Contains everything the LLM needs to re-classify or
    propose a plan, nothing more."""
    lines = [
        f"\n- Method: {dp.method.smali_signature}",
        f"  instruction_index: {dp.instruction_index}",
        f"  kind: {dp.kind.value}  registers: {list(dp.predicate_registers)}",
    ]
    if dp.predicate_origin is not None:
        lines.append(f"  predicate_origin: {dp.predicate_origin.kind}")
    if dp.branches:
        labels = [
            f"{b.label}{'→' + b.target_label if b.target_label else '(fall-through)'}"
            for b in dp.branches
        ]
        lines.append(f"  branches: {labels}")
    if dp.branch_outcome is not None:
        lines.append(
            "  outcome: confidence={:.2f}".format(dp.branch_outcome.confidence)
        )
        for v in dp.branch_outcome.verdicts:
            reason_str = "; ".join(v.reasons) if v.reasons else "(no signals)"
            lines.append(
                f"    {v.branch_label}: {v.verdict} (score={v.score:+.2f}) — {reason_str}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM output application


def _planless_decision_keys(
    decisions: list[DecisionPoint],
    plans: list[BypassPlan],
) -> tuple[tuple[str, int], ...]:
    """Return the ``(method_signature, instruction_index)`` of every
    decision that has no deterministic plan — the LLM is asked to
    propose one."""
    planned_keys: set[tuple[str, int]] = set()
    for plan in plans:
        if (
            plan.source_decision_method is not None
            and plan.source_decision_instruction_index is not None
        ):
            planned_keys.add(
                (
                    plan.source_decision_method.smali_signature,
                    int(plan.source_decision_instruction_index),
                )
            )
    out: list[tuple[str, int]] = []
    for dp in decisions:
        # Only flag decisions the heuristic classifier scored above
        # neutral — completely-neutral decisions weren't going to
        # produce plans anyway and asking the LLM to invent one would
        # generate noise.
        if dp.branch_outcome is None or dp.branch_outcome.confidence == 0.0:
            continue
        key = (dp.method.smali_signature, dp.instruction_index)
        if key not in planned_keys:
            out.append(key)
    return tuple(out)


def _apply_reclassifications(
    decisions: list[DecisionPoint],
    reclassifications: list[dict[str, Any]],
) -> list[DecisionPoint]:
    """Replace the ``branch_outcome.verdicts`` for each ``(method,
    instruction_index)`` mentioned in ``reclassifications``. Defensive
    against malformed entries — invalid rows are silently dropped
    (logged at WARN) so a typo in the LLM's output doesn't void the
    whole anchor."""
    if not reclassifications:
        return decisions
    by_key: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for entry in reclassifications:
        if not isinstance(entry, dict):
            continue
        sig = entry.get("method")
        idx = entry.get("instruction_index")
        branch_label = entry.get("branch_label")
        verdict = entry.get("verdict")
        if not isinstance(sig, str) or not isinstance(idx, int):
            continue
        if not isinstance(branch_label, str) or verdict not in ("deny", "allow", "neutral"):
            continue
        bucket = by_key.setdefault((sig, int(idx)), {})
        bucket[branch_label] = {
            "verdict": str(verdict),
            "reason": str(entry.get("reason") or ""),
        }
    if not by_key:
        return decisions

    out: list[DecisionPoint] = []
    for dp in decisions:
        key = (dp.method.smali_signature, dp.instruction_index)
        updates = by_key.get(key)
        if updates is None or dp.branch_outcome is None:
            out.append(dp)
            continue
        new_verdicts: list[BranchVerdict] = []
        any_changed = False
        for v in dp.branch_outcome.verdicts:
            update = updates.get(v.branch_label)
            if update is None:
                new_verdicts.append(v)
                continue
            any_changed = True
            new_verdicts.append(
                BranchVerdict(
                    branch_label=v.branch_label,
                    verdict=update["verdict"],
                    score=v.score,  # heuristic score preserved for audit
                    reasons=v.reasons + (
                        f"llm-reclassified: {update['reason']}"
                        if update["reason"]
                        else "llm-reclassified",
                    ),
                )
            )
        if not any_changed:
            out.append(dp)
            continue
        new_outcome = BranchOutcome(
            verdicts=tuple(new_verdicts),
            confidence=LLM_RECLASSIFY_CONFIDENCE,
            reasons=dp.branch_outcome.reasons + ("llm re-classification applied",),
        )
        # frozen dataclass — rebuild via the canonical constructor.
        out.append(
            DecisionPoint(
                method=dp.method,
                instruction_index=dp.instruction_index,
                source_line=dp.source_line,
                kind=dp.kind,
                predicate_registers=dp.predicate_registers,
                branches=dp.branches,
                predicate_origin=dp.predicate_origin,
                branch_outcome=new_outcome,
            )
        )
    return out


def _validate_proposed_plans(
    proposed: list[dict[str, Any]],
    *,
    decisions_by_key: dict[tuple[str, int], DecisionPoint],
) -> list[BypassPlan]:
    """Validate each proposed plan against (a) the registered Frida
    template id catalogue, (b) the template's declared parameter
    schema. Drops invalid entries silently with a WARN log so the LLM
    can self-correct on subsequent calls."""
    if not proposed:
        return []
    try:
        from androscan.adapters.frida_hooks import (
            HookParamError,
            HookTemplateNotFound,
            render_by_id,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("trace_behavior: hook library unavailable: %s", exc)
        return []
    out: list[BypassPlan] = []
    for entry in proposed:
        if not isinstance(entry, dict):
            continue
        sig = entry.get("method")
        idx = entry.get("instruction_index")
        template_id = entry.get("template_id")
        params = entry.get("params")
        risk = entry.get("risk")
        rationale = entry.get("rationale")
        if not isinstance(sig, str) or not isinstance(idx, int):
            continue
        if not isinstance(template_id, str) or not isinstance(params, dict):
            continue
        if risk not in ("low", "medium", "high"):
            continue
        # Probe the template by attempting to render it — same shape
        # as ``generate_frida_hook``'s validation. We discard the
        # rendered output (the renderer is consulted at injection
        # time too); this is just a fail-fast on schema drift.
        try:
            render_by_id(template_id, dict(params))
        except (HookTemplateNotFound, HookParamError) as exc:
            logger.warning(
                "trace_behavior: dropping LLM-proposed plan (template=%r): %s",
                template_id, exc,
            )
            continue
        decision = decisions_by_key.get((sig, int(idx)))
        target_method = (
            decision.method if decision is not None else MethodRef.from_smali_signature(sig)
        )
        out.append(
            BypassPlan(
                template_id=template_id,
                params={str(k): str(v) for k, v in dict(params).items()},
                rationale=str(rationale or ""),
                risk=risk,
                risks=("LLM-proposed plan; review the rendered JS before injecting.",),
                target_method=target_method,
                source_decision_method=(decision.method if decision else target_method),
                source_decision_instruction_index=int(idx),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Result envelopes


def _empty_result(entry_smali_id: str, hops: int, message: str) -> SkillResult:
    """Fail-open SkillResult: ``success=True`` so the LLM can read the
    text and pivot to a different tool, but ``data=None`` so 10.6's
    route layer can distinguish from a real anchor."""
    return SkillResult(
        success=True,
        data=None,
        text=f"{message} (entry={entry_smali_id!r}, hops={hops})",
    )


def _success_result(
    anchor: BehaviorAnchor,
    *,
    prefix: str = "",
    llm_skip_reason: Optional[str] = None,
) -> SkillResult:
    """Pack a populated :class:`BehaviorAnchor` into a SkillResult.
    The text summary is what the LLM / chat dock sees; the structured
    ``data`` is what 10.6's REST routes pass through."""
    decisions = anchor.decisions
    plans = anchor.plans
    advanced = anchor.advanced_plans
    verdict_counts = {"deny": 0, "allow": 0, "neutral": 0}
    for dp in decisions:
        if dp.branch_outcome is None:
            continue
        for v in dp.branch_outcome.verdicts:
            verdict_counts[v.verdict] = verdict_counts.get(v.verdict, 0) + 1
    text_lines = [
        f"{prefix}[trace_behavior] entry={anchor.entry_method.smali_signature} "
        f"hops={anchor.hops}",
        f"  decisions: {len(decisions)} "
        f"(deny={verdict_counts['deny']}, allow={verdict_counts['allow']}, "
        f"neutral={verdict_counts['neutral']})",
        f"  plans: {len(plans)} default + {len(advanced)} advanced",
        f"  truncated={anchor.truncated} incomplete={anchor.incomplete}",
    ]
    if anchor.rationale:
        text_lines.append(f"  rationale: {anchor.rationale}")
    if llm_skip_reason:
        text_lines.append(f"  [llm-skipped: {llm_skip_reason}]")

    # Use the cache layer's encoder so consumers can rely on the same
    # JSON shape across (a) the SkillResult.data dict, (b) the
    # trace.sqlite payload, (c) the 10.6 REST response.
    from androscan.internal.trace_cache import anchor_to_json

    return SkillResult(
        success=True,
        data=json.loads(anchor_to_json(anchor)),
        text="\n".join(text_lines),
    )


# ---------------------------------------------------------------------------
# Helpers


def _resolve_app_dir(context: SkillContext, app_id: Optional[str]) -> Optional[Path]:
    """Mirror ``query_call_graph._resolve_app_dir`` — explicit app_id
    wins, otherwise fall back to ``run_folder.parent``."""
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


def _resolve_cache_dir(app_dir: Path) -> Optional[Path]:
    """Translate ``app_dir`` → ``apps/<app_id>/.decompiled/<sha>/``
    via the existing decompile-cache helper. Returns ``None`` when
    the cache hasn't been built yet."""
    try:
        from androscan.web.decompile_cache import (
            cache_root_for as decompile_cache_root,
            get_status as decompile_status,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("trace_behavior: decompile_cache unavailable: %s", exc)
        return None
    ds = decompile_status(app_dir)
    sha = ds.get("sha")
    if ds.get("status") != "ready" or not sha:
        return None
    return decompile_cache_root(app_dir, sha)


def _coerce_int(
    value: Any, default: int, *, lo: int = 1, hi: Optional[int] = None,
) -> int:
    try:
        out = int(value) if value is not None else default
    except (TypeError, ValueError):
        out = default
    out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default
