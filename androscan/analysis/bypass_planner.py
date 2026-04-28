"""Template-bound bypass plan synthesiser — Phase 10 sub-step 10.4.

Given a single :class:`androscan.analysis.trace_types.DecisionPoint`
that's been enriched by the slicer (10.2's ``predicate_origin``) and
the heuristic classifier (10.3's ``branch_outcome``), this module
emits zero or more :class:`BypassPlan` instances — concrete, deterministic
strategies for circumventing the gate via the existing Frida hook
templates shipped in :mod:`androscan.adapters.frida_hooks`.

Per DEC-024, every plan is **template-bound** — there is no free-form
JS in v1. The LLM in 10.5 picks among the deterministic plans this
module emitted (and may reject them in favour of authoring the
rationale prose), but it cannot author the JS itself; that path lives
in the operator-driven ``generate_frida_hook`` skill (DEC-022 consent
gate) and stays separate.

Plan emission rules (locked v1)
-------------------------------

For each ``(decision, outcome)`` pair where the outcome carries at
least one ``"deny"`` verdict *and* at least one ``"allow"`` verdict
(no clear flip target → no plans), and where ``outcome.confidence > 0``
(no signals → no plans), the planner runs three independent rules
that may each contribute one plan:

* **Plan A — flip the predicate value (Layer A — precise)**

  *Applies when* ``predicate_origin`` is :class:`MethodCallOrigin`
  *and* the source method is *not* ``String.equals`` /
  ``String.equalsIgnoreCase`` (those route to Plan C below) *and* the
  source method has a return type the planner can synthesise a
  literal for (``Z`` / ``I`` / ``J`` / ``B`` / ``S`` / ``C`` / ``F`` /
  ``D``, or ``L...;`` *only when* the desired side is "zero" → ``null``).

  Template: :data:`force_return_value`. Target: the predicate's
  source method (e.g. ``isPremiumUser``), *not* the enclosing gate
  method. The forced literal is derived from
  ``(decision.kind, allow-branch direction, source method's return
  descriptor)`` — see :func:`_zero_for_descriptor` /
  :func:`_nonzero_for_descriptor`. **Risk: LOW.**

* **Plan B — force-skip the gate method (Layer B — broad)**

  *Applies when* ``decision.method.return_descriptor == "V"`` (void).
  Template: :data:`force_method_skip`. Target: the enclosing gate
  method. Useful for ``void enforceLicense()`` style gates that throw
  on deny — skipping the method short-circuits the throw. **Risk:
  MEDIUM** (skips legitimate side effects too).

* **Plan C — string-literal bypass**

  *Applies when* ``predicate_origin`` is :class:`MethodCallOrigin`
  *and* the source method is ``Ljava/lang/String;->equals(...)`` or
  ``Ljava/lang/String;->equalsIgnoreCase(...)`` *and* a recoverable
  ``const-string`` literal exists in either branch's basic block
  (the canonical "if (input.equals(SECRET))" license-check shape).
  Template: :data:`force_string_compare_equal`. Target: the pseudo-
  method ``Ljava/lang/String;->equals(Ljava/lang/Object;)Z`` (the
  hook is app-wide but literal-gated; see the template module for
  the trade-off discussion). **Risk: MEDIUM.**

Switch decisions (``decision.is_switch``) are **skipped entirely**
for v1 — Plan A's per-case logic is non-trivial (the operator wants
a different forced value per case, which the template doesn't model
directly), and 10.5's LLM can recommend the operator hand-craft via
Manual Hooks mode for these.

Risk-threshold filtering
------------------------

After all rules emit, :func:`partition_by_risk` splits the result
into ``(default_plans, advanced_plans)`` according to the operator-
configured ``trace.bypass_risk_max`` threshold (default ``"medium"``).
10.5 persists both tuples on the :class:`BehaviorAnchor`; 10.7's UI
renders ``advanced_plans`` behind an "Advanced" expander rather than
mixing them with the defaults. The risk taxonomy is a strict total
order: ``low < medium < high``; an unknown threshold falls soft to
``medium``.

Non-goals for v1 (per DEC-024)
------------------------------

* No FieldRead → ``patch_field`` template — Java reflection-based
  field patching is messy in Frida (instance-vs-static, getter
  patterns, the right time to patch). Plan B + the LLM-driven
  ``generate_frida_hook`` operator path cover the use cases v1 needs.
* No two-register predicate flip — the v1 slicer returns a single
  ``PredicateOrigin`` for ``if-eq`` / ``if-ne`` / etc. via the
  priority rule; the *other* operand is unknown to the planner, so
  it can't know which side to flip. The LLM in 10.5 sees the
  decision unmodified and may suggest a hand-rolled hook.
* No object-side non-null synthesis — ``Plan A`` for an
  ``L...;``-returning method handles the "force null" direction (when
  that's the allow-branch direction) but punts the "force non-null"
  direction; the operator has to author the instance via Manual
  Hooks. The ``CompositeOrigin`` case is also ambiguous-by-design.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from androscan.analysis.trace_types import (
    BranchOutcome,
    BypassPlan,
    DecisionKind,
    DecisionPoint,
    MethodCallOrigin,
    MethodRef,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Risk taxonomy — locked v1 enum + total-order comparison.

VALID_RISKS: tuple[str, ...] = ("low", "medium", "high")
_RISK_ORDER: dict[str, int] = {risk: i for i, risk in enumerate(VALID_RISKS)}
DEFAULT_RISK_THRESHOLD = "medium"


def _normalise_risk(value: Optional[str], *, fallback: str = DEFAULT_RISK_THRESHOLD) -> str:
    """Return ``value.lower()`` if it's in :data:`VALID_RISKS`, else ``fallback``.

    Used by :func:`partition_by_risk` to coerce the operator's
    ``trace.bypass_risk_max`` config value into the closed set the
    comparator understands. Mirrors the loader's permissive posture
    (any string accepted at config-load time) with a fail-soft
    fallback at consumption time.
    """
    if value is None:
        return fallback
    candidate = value.strip().lower()
    if candidate in _RISK_ORDER:
        return candidate
    logger.warning("bypass_planner: unknown risk %r (using %r)", value, fallback)
    return fallback


def risk_at_or_below(plan_risk: str, threshold: str) -> bool:
    """``True`` iff ``plan_risk <= threshold`` in the locked total order
    ``low < medium < high``. Unknown values on either side fall soft to
    ``"medium"``."""
    return _RISK_ORDER[_normalise_risk(plan_risk)] <= _RISK_ORDER[_normalise_risk(threshold)]


# ---------------------------------------------------------------------------
# Predicate-flip direction analysis
#
# For each conditional kind, work out which Smali-level relationship
# between the predicate register(s) takes which branch. Then, given
# the operator's identified ALLOW branch label, return the desired
# value the predicate register must hold so that branch is taken.
#
# v1 only handles single-register predicates (``if-*z`` and switches —
# although switches are filtered upstream). Two-register predicates
# (``if-eq v0, v1`` etc.) get None back from :func:`_desired_zero_or_nonzero`
# since the planner only knows one of the two operands' origins; the
# LLM in 10.5 sees these unchanged.


# Single-register kinds: maps decision.kind → (true_branch_takes_when_zero,
# false_branch_takes_when_zero). For ``if-eqz``, the true branch fires
# when the register IS zero; for ``if-nez``, the true branch fires when
# the register is NON-zero; for ``if-ltz`` etc., the true branch fires
# on a sign relationship.
#
# v1 collapses signed comparisons (``if-ltz`` / ``if-lez`` / ``if-gtz``
# / ``if-gez``) onto the binary "zero vs non-zero" axis: ``ltz`` /
# ``lez`` / ``gtz`` / ``gez`` only ever flip on zero crossings so the
# binary axis is sufficient for the literal we'll force back. The LLM
# in 10.5 can refine if the gate semantics actually require a specific
# negative / positive value.

_SINGLE_REG_TRUE_TAKES_ZERO: dict[DecisionKind, bool] = {
    DecisionKind.IF_EQZ: True,    # if reg == 0 → true branch
    DecisionKind.IF_NEZ: False,   # if reg != 0 → true branch
    DecisionKind.IF_LTZ: False,   # signed < 0; "true takes when reg < 0" → non-zero side
    DecisionKind.IF_LEZ: True,    # signed <= 0; treat the zero point as the "true" side
    DecisionKind.IF_GTZ: False,   # signed > 0; non-zero side
    DecisionKind.IF_GEZ: True,    # signed >= 0; zero side qualifies
}


def _desired_zero_or_nonzero(decision: DecisionPoint, allow_branch_label: str) -> Optional[bool]:
    """Return ``True`` if the predicate register must be **zero** for
    the operator's allow branch to fire, ``False`` if it must be
    **non-zero**, or ``None`` if the kind is unsupported (two-register
    or switch — the LLM in 10.5 owns those).
    """
    if decision.kind not in _SINGLE_REG_TRUE_TAKES_ZERO:
        return None
    true_takes_zero = _SINGLE_REG_TRUE_TAKES_ZERO[decision.kind]
    # The "allow branch" is identified by its label (``"true"`` /
    # ``"false"`` per :class:`Branch`). Map that to "did the allow
    # branch take the zero side?" using the kind table above.
    if allow_branch_label == "true":
        return true_takes_zero
    if allow_branch_label == "false":
        return not true_takes_zero
    # Switch case labels reach here when a 2-branch if has somehow
    # acquired a non-true/false label — defensive; the upstream
    # filter (``decision.is_switch``) keeps switches out.
    return None


# ---------------------------------------------------------------------------
# Return-descriptor literal synthesis
#
# Given a JNI return descriptor (``"Z"`` / ``"I"`` / ``"L...;"`` /
# etc.), return the JS literal text that represents the descriptor's
# zero or non-zero value. Used by :func:`_plan_force_return_value` to
# fill ``return_value_expr``.
#
# References are special-cased: the zero side is ``null`` (always
# valid); the non-zero side requires synthesising an instance of the
# return type, which the planner can't reliably do (constructor
# discovery is application-specific). v1 returns ``None`` for the
# non-null direction so the planner can skip that plan honestly.


_PRIMITIVE_DESCRIPTORS = frozenset({"Z", "B", "S", "C", "I", "J"})
_FLOATING_DESCRIPTORS = frozenset({"F", "D"})


def _zero_for_descriptor(desc: str) -> Optional[str]:
    """JS literal for the descriptor's zero value, or ``None`` if void
    (which can't have a return value)."""
    if desc == "V":
        return None
    if desc == "Z":
        return "false"
    if desc in _PRIMITIVE_DESCRIPTORS:
        return "0"
    if desc in _FLOATING_DESCRIPTORS:
        return "0.0"
    # References (``L...;``) and arrays (``[...``) — null is always valid.
    return "null"


def _nonzero_for_descriptor(desc: str) -> Optional[str]:
    """JS literal for the descriptor's non-zero value, or ``None`` if
    we can't synthesise one (void; reference / array — the operator
    must hand-roll the instance)."""
    if desc == "V":
        return None
    if desc == "Z":
        return "true"
    if desc in _PRIMITIVE_DESCRIPTORS:
        return "1"
    if desc in _FLOATING_DESCRIPTORS:
        return "1.0"
    # References and arrays — can't synthesise a non-null instance
    # without knowing the type's constructors. Operator path via
    # Manual Hooks instead.
    return None


# ---------------------------------------------------------------------------
# String-equals detection (for Plan C)


_STRING_EQUALS_SIG = "Ljava/lang/String;->equals(Ljava/lang/Object;)Z"
_STRING_EQUALS_IGNORECASE_SIG = "Ljava/lang/String;->equalsIgnoreCase(Ljava/lang/String;)Z"

_STRING_EQUALS_SIGS = frozenset({_STRING_EQUALS_SIG, _STRING_EQUALS_IGNORECASE_SIG})

# Synthetic MethodRef for the force_string_compare_equal hook — the
# hook intercepts the canonical String.equals(Object) overload (the
# template hooks both equals and equalsIgnoreCase from one entry point
# but the operator-facing target_method is the most-canonical one).
_STRING_EQUALS_TARGET = MethodRef(
    class_name="java.lang.String",
    method_name="equals",
    param_descriptors=("Ljava/lang/Object;",),
    return_descriptor="Z",
)


_RE_CONST_STRING = re.compile(
    r"^const-string(?:/jumbo)?\s+[vp]\d+\s*,\s*\"(?P<value>(?:[^\"\\]|\\.)*)\""
)


def _find_const_string_in_block(
    target_label: Optional[str],
    decision_instruction_index: int,
    instructions: tuple[str, ...],
    label_index: dict[str, int],
) -> Optional[str]:
    """Walk one branch's basic block (forward from ``target_label`` /
    fall-through) and return the first ``const-string`` literal text
    encountered, or ``None`` if the block has none. Mirrors 10.3's
    basic-block walker contract — terminator semantics (``return*`` /
    ``throw`` / ``goto*`` / ``if-*`` / ``*-switch``) are identical so
    the planner sees the same block boundaries the classifier did."""
    if target_label is None:
        start = decision_instruction_index + 1
    else:
        start = label_index.get(target_label)
        if start is None:
            return None
    i = start
    n = len(instructions)
    while i < n:
        line = instructions[i]
        m = _RE_CONST_STRING.match(line)
        if m:
            return m.group("value")
        # Same terminator set as branch_classifier._is_terminator — kept
        # private here so the two modules don't import each other; the
        # invariant is that adding a terminator to the classifier
        # requires updating it here too. Documented in DEC-024 v1.
        if (
            line.startswith("return")
            or line.startswith("throw")
            or line.startswith("goto")
            or line.startswith("if-")
        ):
            return None
        head = line.split(None, 1)[0] if line else ""
        if head.endswith("-switch"):
            return None
        i += 1
    return None


# ---------------------------------------------------------------------------
# Public API


def plan_bypasses(
    decision: DecisionPoint,
    instructions: tuple[str, ...] = (),
    label_index: Optional[dict[str, int]] = None,
) -> tuple[BypassPlan, ...]:
    """Emit zero or more :class:`BypassPlan` instances for one decision.

    ``instructions`` and ``label_index`` are the per-method context
    needed for Plan C's const-string scan. Both default to empty so
    callers that only want Plans A + B (the common case for tests +
    LLM consumers that don't have the raw instruction stream handy)
    can omit them — Plan C silently skips when context is missing.

    Skipped (returns empty tuple) when:

    * ``decision.is_switch`` (v1 punts switches)
    * ``decision.branch_outcome is None`` (classifier hasn't run)
    * ``decision.branch_outcome.confidence == 0.0`` (no signals)
    * No clear flip target (need at least one DENY *and* one ALLOW
      verdict to know which direction to push)

    The returned plans are in deterministic order: Plan A → Plan B →
    Plan C. Tests join by ``template_id`` rather than by index where
    they want to be insulated from the rule order, but the order is
    locked v1 to keep the UI rendering stable.
    """
    if decision.is_switch:
        return ()
    outcome: Optional[BranchOutcome] = decision.branch_outcome
    if outcome is None or outcome.confidence == 0.0:
        return ()

    # Identify the "allow target" branch — we want to flip toward it.
    allow_branch_labels = [v.branch_label for v in outcome.verdicts if v.verdict == "allow"]
    deny_branch_labels = [v.branch_label for v in outcome.verdicts if v.verdict == "deny"]
    if not allow_branch_labels or not deny_branch_labels:
        # No clear flip direction — either both branches deny, both
        # allow, or all neutral. The LLM in 10.5 can still propose
        # something here, but the deterministic planner has nothing
        # actionable to say.
        return ()
    # If multiple allow targets (multi-branch ifs don't exist in
    # Smali but defensive), prefer the first in source order.
    allow_branch_label = allow_branch_labels[0]

    plans: list[BypassPlan] = []

    plan_a = _plan_force_return_value_on_predicate(decision, allow_branch_label)
    if plan_a is not None:
        plans.append(plan_a)

    plan_b = _plan_force_method_skip_on_void_gate(decision)
    if plan_b is not None:
        plans.append(plan_b)

    plan_c = _plan_force_string_compare_equal(
        decision,
        allow_branch_label,
        instructions=instructions,
        label_index=label_index or {},
    )
    if plan_c is not None:
        plans.append(plan_c)

    return tuple(plans)


def partition_by_risk(
    plans: tuple[BypassPlan, ...],
    threshold: str,
) -> tuple[tuple[BypassPlan, ...], tuple[BypassPlan, ...]]:
    """Split ``plans`` into ``(default, advanced)`` by ``threshold``.

    ``default`` keeps plans whose risk is at or below ``threshold``;
    ``advanced`` collects the rest. 10.7's UI renders the latter
    behind an "Advanced" expander rather than mixing them with the
    defaults. Order within each tuple is preserved from the input.
    Unknown risk values fall soft to ``"medium"`` on both sides of
    the comparison.
    """
    default_plans: list[BypassPlan] = []
    advanced_plans: list[BypassPlan] = []
    normalised_threshold = _normalise_risk(threshold)
    for plan in plans:
        if risk_at_or_below(plan.risk, normalised_threshold):
            default_plans.append(plan)
        else:
            advanced_plans.append(plan)
    return tuple(default_plans), tuple(advanced_plans)


# ---------------------------------------------------------------------------
# Per-rule plan synthesis


def _plan_force_return_value_on_predicate(
    decision: DecisionPoint,
    allow_branch_label: str,
) -> Optional[BypassPlan]:
    """Plan A — see module docstring. Returns ``None`` if the rule
    doesn't apply (non-MethodCall predicate origin, String.equals
    source method, void source method, reference return on the
    non-null direction, or unsupported decision kind)."""
    origin = decision.predicate_origin
    if not isinstance(origin, MethodCallOrigin):
        return None
    source_sig = origin.method.smali_signature
    # Plan C handles the String.equals case more precisely (literal-
    # gated rather than blanket force-true); avoid emitting both.
    if source_sig in _STRING_EQUALS_SIGS:
        return None
    desired_zero = _desired_zero_or_nonzero(decision, allow_branch_label)
    if desired_zero is None:
        # Two-register or switch — we filter switches upstream, so
        # this is a defensive guard against new kinds being added
        # without updating ``_SINGLE_REG_TRUE_TAKES_ZERO``.
        return None
    descriptor = origin.method.return_descriptor
    if desired_zero:
        literal = _zero_for_descriptor(descriptor)
    else:
        literal = _nonzero_for_descriptor(descriptor)
    if literal is None:
        # Void source method (can't force a return), or reference
        # return where we want non-null (no auto-synthesis). Honest
        # skip — the LLM may suggest an operator-driven Manual Hooks
        # path in 10.5.
        return None
    rationale = (
        f"Predicate value comes from {source_sig}; "
        f"forcing its return to {literal} steers {decision.method.smali_signature} "
        f"toward the {allow_branch_label!r} branch (operator-classified ALLOW)."
    )
    return BypassPlan(
        template_id="force_return_value",
        params={
            "class_name": origin.method.class_name,
            "method_name": origin.method.method_name,
            "return_value_expr": literal,
            "event_label": _event_label_for(decision, "frv"),
        },
        rationale=rationale,
        risk="low",
        risks=(
            "Hijacks one named method app-wide; original method body is not executed.",
        ),
        target_method=origin.method,
        source_decision_method=decision.method,
        source_decision_instruction_index=decision.instruction_index,
    )


def _plan_force_method_skip_on_void_gate(decision: DecisionPoint) -> Optional[BypassPlan]:
    """Plan B — see module docstring. Returns ``None`` if the gate
    method's return descriptor isn't ``"V"``."""
    if decision.method.return_descriptor != "V":
        return None
    rationale = (
        f"Gate method {decision.method.smali_signature} returns void — "
        "skipping its body short-circuits any deny-side throws / process-exits "
        "while letting execution continue past the call site."
    )
    return BypassPlan(
        template_id="force_method_skip",
        params={
            "class_name": decision.method.class_name,
            "method_name": decision.method.method_name,
            "return_descriptor": decision.method.return_descriptor,
            "event_label": _event_label_for(decision, "fms"),
        },
        rationale=rationale,
        risk="medium",
        risks=(
            "Skips the gate method's body wholesale; legitimate side effects "
            "(logging, state mutations) are also suppressed.",
        ),
        target_method=decision.method,
        source_decision_method=decision.method,
        source_decision_instruction_index=decision.instruction_index,
    )


def _plan_force_string_compare_equal(
    decision: DecisionPoint,
    allow_branch_label: str,
    *,
    instructions: tuple[str, ...],
    label_index: dict[str, int],
) -> Optional[BypassPlan]:
    """Plan C — see module docstring. Returns ``None`` unless the
    predicate origin is a ``String.equals`` / ``equalsIgnoreCase``
    call AND a recoverable ``const-string`` literal exists in either
    branch's basic block."""
    origin = decision.predicate_origin
    if not isinstance(origin, MethodCallOrigin):
        return None
    source_sig = origin.method.smali_signature
    if source_sig not in _STRING_EQUALS_SIGS:
        return None
    if not instructions:
        # No raw instructions → can't scan for the literal. Honest
        # skip: 10.5's LLM may still emit a Manual Hooks suggestion.
        return None
    # Scan both branches' basic blocks for the first const-string —
    # gates frequently load the secret right above the if-* in the
    # gate method body itself. The classifier's basic-block walk is
    # forward-from-target; we additionally scan backward from the
    # decision through the gate method's instruction stream because
    # the secret is often loaded into the predicate register *before*
    # the call to ``String.equals`` rather than inside the branch.
    literal: Optional[str] = None
    for branch in decision.branches:
        candidate = _find_const_string_in_block(
            branch.target_label,
            decision.instruction_index,
            instructions,
            label_index,
        )
        if candidate:
            literal = candidate
            break
    if literal is None:
        # Backward scan over the entire method body up to the
        # decision point — captures the "load secret then compare"
        # shape that the forward branch scan misses.
        for k in range(decision.instruction_index - 1, -1, -1):
            m = _RE_CONST_STRING.match(instructions[k])
            if m:
                literal = m.group("value")
                break
    if literal is None:
        return None
    rationale = (
        f"Predicate value comes from {source_sig} comparing against the literal "
        f"{literal!r}; force-true on every comparison involving that literal "
        f"steers {decision.method.smali_signature} toward the {allow_branch_label!r} "
        "branch (operator-classified ALLOW)."
    )
    return BypassPlan(
        template_id="force_string_compare_equal",
        params={
            "target_literal": literal,
            "event_label": _event_label_for(decision, "fsce"),
        },
        rationale=rationale,
        risk="medium",
        risks=(
            "App-wide String.equals interception — only fires when the literal "
            "appears in either side, but other gates comparing against the same "
            "literal will also be affected.",
        ),
        target_method=_STRING_EQUALS_TARGET,
        source_decision_method=decision.method,
        source_decision_instruction_index=decision.instruction_index,
    )


# ---------------------------------------------------------------------------
# Helpers


def _event_label_for(decision: DecisionPoint, prefix: str) -> str:
    """Deterministic event label keyed off the decision's identity.

    Operators see this in the Lab session pane filters; the per-prefix
    variant (``frv`` / ``fms`` / ``fsce``) keeps Plan A / B / C
    streams distinguishable when an operator stages multiple plans
    for the same gate. Format kept short to fit comfortably in the
    UI's filter chip width.
    """
    return f"trace-{prefix}-{decision.method.method_name}-{decision.instruction_index}"
