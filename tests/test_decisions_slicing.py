"""Tests for :mod:`androscan.analysis.slicing` and the
:class:`PredicateOrigin` extensions in
:mod:`androscan.analysis.trace_types` — Phase 10 sub-step 10.2.

Run purely against the fixture smali under
``tests/fixtures/trace_smali/`` (Slices.smali in particular) and the
existing 10.1 fixtures — no apktool, no SQLite, no LLM.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from androscan.analysis import decisions, slicing, smali_parser
from androscan.analysis.trace_types import (
    BehaviorAnchor,
    Branch,
    CallSite,
    CompositeOrigin,
    ConstOrigin,
    DecisionKind,
    DecisionPoint,
    FieldReadOrigin,
    MethodCallOrigin,
    MethodRef,
    ParamOrigin,
)


FIXTURES = Path(__file__).parent / "fixtures" / "trace_smali"


def _roots() -> list[Path]:
    return [FIXTURES / "smali", FIXTURES / "smali_classes2"]


def _parse_and_slice() -> dict[str, decisions.MethodDecisions]:
    """Common harness: pass-1 (classes) → pass-3 (decisions) → 10.2 slicer.

    Returns a dict keyed by smali method signature so tests can pull
    one method at a time without re-parsing.
    """
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes)
    sliced = {md.method_signature: slicing.slice_predicate_origins(md) for md in mds}
    return sliced


def _only_decision(md: decisions.MethodDecisions) -> DecisionPoint:
    """Helper: assert the method has exactly one decision and return it.
    Used by tests that pin a single-decision fixture method."""
    assert len(md.decision_points) == 1, (
        f"expected one decision in {md.method_signature}, got {len(md.decision_points)}"
    )
    return md.decision_points[0]


# ---------------------------------------------------------------------------
# Data-model wiring: defaults + parser instruction stream


def test_decision_point_predicate_origin_defaults_to_none() -> None:
    """Pre-slice DecisionPoints (10.1's output, before 10.2's slicer
    runs) must carry ``predicate_origin=None`` so the post-slice value
    is unambiguous."""
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes)
    for md in mds:
        for dp in md.decision_points:
            assert dp.predicate_origin is None


def test_method_decisions_carries_raw_instruction_stream() -> None:
    """10.2 needs ``MethodDecisions.instructions`` populated by 10.1's
    parser pass; the slicer joins each ``DecisionPoint.instruction_index``
    against this list. Spot-check that the if-* instruction is at the
    expected index, and that every if-* the parser found has a
    matching entry in instructions."""
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes)
    by_method = {md.method_signature: md for md in mds}
    md = by_method["Lcom/trace/Slices;->sliceConstInt()V"]
    # The const/4 + if-eqz pair are the only "real" instructions before
    # the cond branch; const at index 0, if at index 1.
    assert md.instructions[0].startswith("const/4")
    assert md.instructions[1].startswith("if-eqz")
    # Every decision's instruction_index points at an if-/switch- line.
    for md in mds:
        for dp in md.decision_points:
            line = md.instructions[dp.instruction_index]
            assert line.startswith("if-") or line.endswith("-switch") \
                or "-switch " in line, f"instruction at index {dp.instruction_index} is {line!r}"


# ---------------------------------------------------------------------------
# Each origin variant


def test_method_call_origin_from_invoke_move_result() -> None:
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceMethodCall()V"]
    dp = _only_decision(md)
    origin = dp.predicate_origin
    assert isinstance(origin, MethodCallOrigin)
    assert origin.invoke_kind == "static"
    assert origin.method.smali_signature == "Lcom/trace/Slices;->getFlag()Z"
    assert origin.kind == "method_call"


def test_field_read_origin_from_iget() -> None:
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceIgetField()V"]
    origin = _only_decision(md).predicate_origin
    assert isinstance(origin, FieldReadOrigin)
    assert origin.is_static is False
    assert origin.field.smali_signature == "Lcom/trace/Slices;->mFlag:Z"


def test_field_read_origin_from_sget() -> None:
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceSgetField()V"]
    origin = _only_decision(md).predicate_origin
    assert isinstance(origin, FieldReadOrigin)
    assert origin.is_static is True
    assert origin.field.smali_signature == "Lcom/trace/Slices;->sFlag:Z"


def test_const_origin_from_const_int() -> None:
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceConstInt()V"]
    origin = _only_decision(md).predicate_origin
    assert isinstance(origin, ConstOrigin)
    assert origin.smali_op == "const/4"
    assert origin.value == "0x1"


def test_const_origin_from_const_string() -> None:
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceConstString()V"]
    origin = _only_decision(md).predicate_origin
    assert isinstance(origin, ConstOrigin)
    assert origin.smali_op == "const-string"
    # Quoted string literal preserved verbatim from the source.
    assert origin.value == '"premium"'


def test_param_origin_when_predicate_is_unmodified_p_register() -> None:
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceParam(Z)V"]
    origin = _only_decision(md).predicate_origin
    assert isinstance(origin, ParamOrigin)
    assert origin.register == "p1"


def test_composite_origin_from_arithmetic() -> None:
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceArithmetic(II)V"]
    origin = _only_decision(md).predicate_origin
    assert isinstance(origin, CompositeOrigin)
    assert origin.reason == "add-int"


def test_composite_origin_from_instance_of() -> None:
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceInstanceOf(Ljava/lang/Object;)V"]
    origin = _only_decision(md).predicate_origin
    assert isinstance(origin, CompositeOrigin)
    assert origin.reason == "instance-of"


def test_composite_origin_from_move_exception() -> None:
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceMoveException()V"]
    origin = _only_decision(md).predicate_origin
    assert isinstance(origin, CompositeOrigin)
    assert origin.reason == "move-exception"


# ---------------------------------------------------------------------------
# Move chain


def test_move_chain_resolves_to_underlying_field_read() -> None:
    """``iget v1 ; move v0, v1 ; if-eqz v0`` should resolve to the
    underlying ``iget`` — the slicer must follow ``move`` aliases
    rather than reporting the move itself as a definition."""
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceMoveChain()V"]
    origin = _only_decision(md).predicate_origin
    assert isinstance(origin, FieldReadOrigin)
    assert origin.field.smali_signature == "Lcom/trace/Slices;->mFlag:Z"
    assert origin.is_static is False


# ---------------------------------------------------------------------------
# Two-register comparison combination


def test_two_register_const_vs_field_returns_field_read() -> None:
    """``if-ge v0, v1`` where v0 is iget and v1 is const → the
    more-actionable side (FieldRead) wins per the priority rule."""
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceTwoRegConstAndField()V"]
    origin = _only_decision(md).predicate_origin
    assert isinstance(origin, FieldReadOrigin)
    assert origin.field.smali_signature == "Lcom/trace/Slices;->mLevel:I"


def test_two_register_both_method_calls_returns_lhs_method_call() -> None:
    """When both operands are MethodCall, ties break to the LHS
    (left-hand side of the smali if-* instruction) so the result is
    stable in source order."""
    sliced = _parse_and_slice()
    md = sliced["Lcom/trace/Slices;->sliceTwoRegBothMethodCalls()V"]
    origin = _only_decision(md).predicate_origin
    assert isinstance(origin, MethodCallOrigin)
    assert origin.method.method_name == "getA"


# ---------------------------------------------------------------------------
# Slice failure


def test_slice_failure_returns_none_when_walk_exhausted() -> None:
    """Force ``max_walk=2`` against a method whose predicate is defined
    further than 2 instructions back. The slicer must surface
    ``predicate_origin=None`` honestly per DEC-024 rather than
    fabricating an origin."""
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes)
    by_method = {md.method_signature: md for md in mds}
    md = by_method["Lcom/trace/Slices;->sliceWalkExhausted()V"]
    sliced = slicing.slice_predicate_origins(md, max_walk=2)
    assert _only_decision(sliced).predicate_origin is None
    # Sanity: with a generous walk, the same method does resolve to a Const.
    sliced_full = slicing.slice_predicate_origins(md, max_walk=64)
    origin = _only_decision(sliced_full).predicate_origin
    assert isinstance(origin, ConstOrigin)
    assert origin.value == "0x1"


# ---------------------------------------------------------------------------
# Wire-format: every origin variant + None round-trips through asdict + json


def test_predicate_origin_round_trips_through_json_for_all_variants() -> None:
    """Every variant carries a ``kind`` discriminator field so JSON
    round-trip preserves the union variant unambiguously. Verify that
    contract for the full enriched DecisionPoint payload — this is
    what 10.5's trace.sqlite cache + 10.6's wire format both rely on."""
    sliced = _parse_and_slice()
    fixture_methods = [
        ("Lcom/trace/Slices;->sliceMethodCall()V", "method_call"),
        ("Lcom/trace/Slices;->sliceIgetField()V", "field_read"),
        ("Lcom/trace/Slices;->sliceConstInt()V", "const"),
        ("Lcom/trace/Slices;->sliceParam(Z)V", "param"),
        ("Lcom/trace/Slices;->sliceArithmetic(II)V", "composite"),
    ]
    for sig, expected_kind in fixture_methods:
        dp = _only_decision(sliced[sig])
        payload = json.dumps(dataclasses.asdict(dp), default=str)
        decoded = json.loads(payload)
        assert decoded["predicate_origin"] is not None, sig
        assert decoded["predicate_origin"]["kind"] == expected_kind, sig


# ===========================================================================
# Phase 11 sub-step 11.4 — bounded inter-procedural descent
# ===========================================================================
#
# Descent harness: parse classes + decisions WITH branchless bodies
# (so pure helper methods are reachable via ``decisions_by_method_sig``),
# build the side-indices, and slice with descent enabled.


def _parse_and_slice_with_descent(
    *,
    reflective: frozenset[str] = frozenset(),
    max_depth: int = slicing.MAX_SLICE_DEPTH,
) -> dict[str, decisions.MethodDecisions]:
    """Variant of :func:`_parse_and_slice` that enables Phase 11.4
    bounded inter-procedural descent. Returns a dict keyed by smali
    signature so each test can pin one fixture method independently.
    """
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes, include_branchless=True)
    classes_by_smali = {c.class_desc: c for c in classes}
    decisions_by_sig = {md.method_signature: md for md in mds}
    out: dict[str, decisions.MethodDecisions] = {}
    for md in mds:
        if not md.decision_points:
            # Branchless methods don't get sliced; they're only used
            # as descent targets (consumed via ``decisions_by_sig``).
            continue
        sliced = slicing.slice_predicate_origins(
            md,
            classes_by_smali=classes_by_smali,
            decisions_by_method_sig=decisions_by_sig,
            reflective_method_sigs=reflective,
            descent_budget=slicing._DescentBudget.fresh(max_depth=max_depth),
        )
        out[md.method_signature] = sliced
    return out


# --- Descent positive paths ------------------------------------------------


def test_descent_one_hop_resolves_through_pure_getter() -> None:
    """``gateOneHopGetter`` slices to ``MethodCallOrigin(pureGetFlag)``;
    depth-1 descent re-slices ``pureGetFlag`` and finds a const-string,
    so the operator-visible terminal is ``ConstOrigin("premium")``."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateOneHopGetter()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, ConstOrigin), f"expected ConstOrigin, got {type(origin).__name__}"
    assert origin.value == '"premium"'
    assert origin.smali_op == "const-string"


def test_descent_two_hop_chain_resolves_to_terminal_const() -> None:
    """Two-hop chain: ``gateTwoHopChain → pureGetA → pureGetB → const/4``.
    With ``MAX_SLICE_DEPTH=2`` (and the chain's actual depth=2) the
    descent walks both helpers and the operator sees the terminal
    ``const/4 0x1`` as ``ConstOrigin("0x1")``."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateTwoHopChain()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, ConstOrigin)
    assert origin.value == "0x1"


def test_descent_depth_cap_stops_at_max_slice_depth() -> None:
    """Three-hop chain (``gateThreeHopChainCapped → pureChainHopOne →
    pureChainHopTwo → pureChainHopThree → const/4``) exceeds the v1
    ``MAX_SLICE_DEPTH=2`` cap. The descent stops at the depth-2
    helper; the operator-visible terminal is
    ``MethodCallOrigin(pureChainHopThree)`` — the helper at the
    boundary, NOT the original surface call (per the spec: "operator
    sees the new terminal, not the chain that produced it")."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateThreeHopChainCapped()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, MethodCallOrigin)
    assert origin.method.method_name == "pureChainHopThree"


def test_descent_max_depth_zero_disables_descent_entirely() -> None:
    """Setting ``max_depth=0`` on the budget should preserve every
    v1 ``MethodCallOrigin`` terminal regardless of callee statelessness
    — useful as a kill-switch + sanity check that descent is the only
    mechanism producing the new terminals."""
    sliced = _parse_and_slice_with_descent(max_depth=0)
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateOneHopGetter()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, MethodCallOrigin)
    assert origin.method.method_name == "pureGetFlag"


def test_descent_cross_class_resolves_through_classes_by_smali() -> None:
    """``gateCrossClassDescent`` calls ``Lcom/trace/Slices;->getFlag()Z``
    — a method on a sibling class. The descent should find ``Slices``
    via ``classes_by_smali``, find the body via
    ``decisions_by_method_sig``, classify it stateless, walk its
    ``return v0`` source register and find the ``const/4 0x1``.
    Terminal becomes ``ConstOrigin("0x1")``."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateCrossClassDescent()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, ConstOrigin), f"expected ConstOrigin, got {type(origin).__name__}"
    assert origin.value == "0x1"


# --- Descent negative paths (descent blocked) ------------------------------


def test_descent_blocked_when_callee_writes_field() -> None:
    """``gateStatefulFieldWriteCallee`` calls ``statefulIputCallee``
    which contains ``iput-boolean``. ``is_stateless`` returns False;
    descent is skipped; v1 ``MethodCallOrigin`` terminal preserved."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateStatefulFieldWriteCallee()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, MethodCallOrigin)
    assert origin.method.method_name == "statefulIputCallee"


def test_descent_blocked_when_callee_is_external() -> None:
    """``gateExternalAndroidCallee`` calls ``Landroid/util/Log;->d(...)``
    — an Android framework class with no Smali in our corpus. The
    callee is NOT in ``classes_by_smali``; descent is skipped; v1
    terminal preserved."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(
        sliced["Lcom/trace/Helpers;->gateExternalAndroidCallee(Ljava/lang/String;)V"]
    )
    origin = dp.predicate_origin
    assert isinstance(origin, MethodCallOrigin)
    assert origin.method.class_name == "android.util.Log"
    assert origin.method.method_name == "d"


def test_descent_falls_back_to_v1_when_descent_kwargs_omitted() -> None:
    """Public API contract: when ``classes_by_smali`` and
    ``decisions_by_method_sig`` are both omitted, the slicer behaves
    exactly like v1 — every ``MethodCallOrigin`` terminal is surfaced
    unchanged. This is the backwards-compat path for tests + ad-hoc
    callers that don't care about descent."""
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes, include_branchless=True)
    by_sig = {md.method_signature: md for md in mds}
    md = by_sig["Lcom/trace/Helpers;->gateOneHopGetter()V"]
    sliced = slicing.slice_predicate_origins(md)  # no descent kwargs
    dp = _only_decision(sliced)
    origin = dp.predicate_origin
    assert isinstance(origin, MethodCallOrigin)
    assert origin.method.method_name == "pureGetFlag"


# --- ``is_stateless`` analyzer — direct unit tests -------------------------


def _is_stateless_harness() -> tuple[
    dict[str, "smali_parser.ClassDecl"],
    dict[str, decisions.MethodDecisions],
]:
    """Shared fixture loader for ``is_stateless`` unit tests."""
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes, include_branchless=True)
    return (
        {c.class_desc: c for c in classes},
        {md.method_signature: md for md in mds},
    )


def test_is_stateless_returns_true_for_pure_const_return() -> None:
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->pureGetFlag()Ljava/lang/String;",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is True


def test_is_stateless_returns_false_for_iput_field_write() -> None:
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->statefulIputCallee()I",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is False


def test_is_stateless_returns_false_for_sput_field_write() -> None:
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->statefulSputCallee()I",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is False


def test_is_stateless_returns_false_for_aput_array_write() -> None:
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->statefulAputCallee([I)I",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is False


def test_is_stateless_returns_false_for_monitor_enter() -> None:
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->statefulMonitorCallee()I",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is False


def test_is_stateless_returns_false_for_throw() -> None:
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->statefulThrowCallee()I",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is False


def test_is_stateless_returns_true_for_pure_arithmetic_only() -> None:
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->pureArithmeticOnly(II)I",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is True


# --- ``_STATELESS_LIB_DENYLIST`` deny-list short-circuit -------------------


def test_denylist_string_length_treated_as_stateless() -> None:
    """``String.length`` is in the per-method allowlist on the
    String class entry → analyzer returns True for any caller body
    that only invokes allowlisted String methods."""
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->pureStringLength(Ljava/lang/String;)I",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is True


def test_denylist_math_abs_treated_as_stateless() -> None:
    """``Math.abs`` falls under the whole-class ``Math`` deny-list
    entry → caller body that only invokes Math methods is stateless."""
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->pureMathAbs(I)I",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is True


def test_denylist_object_hashcode_treated_as_stateless() -> None:
    """``Object.hashCode`` is the planning-checkpoint addition on top
    of the spec's seed — verify it actually short-circuits to True."""
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->pureObjectHashCode(Ljava/lang/Object;)I",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is True


def test_denylist_kotlin_intrinsics_areequal_treated_as_stateless() -> None:
    """``Kotlin.Intrinsics`` is whole-class deny-listed; ``areEqual``
    is the hot Kotlin codegen call site for ``==`` comparisons."""
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->pureKotlinAreEqual(Ljava/lang/Object;Ljava/lang/Object;)Z",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is True


def test_denylist_string_concat_NOT_treated_as_stateless() -> None:
    """``String.concat`` is NOT in the per-method allowlist (it
    allocates) → defensive False. This pins the per-method allowlist
    enforcement (without it, the whole-class fallback would
    spuriously claim concat is pure)."""
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->statefulStringConcat(Ljava/lang/String;)Ljava/lang/String;",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is False


# --- Reflection deny-list --------------------------------------------------


def test_is_stateless_returns_false_when_callee_is_reflective() -> None:
    """A method flagged ``may_have_unresolved_reflection`` is treated
    as stateful regardless of its body shape — reflection results
    can have arbitrary side effects we can't analyze statically."""
    cls, dec = _is_stateless_harness()
    sig = "Lcom/trace/Helpers;->pureGetFlag()Ljava/lang/String;"
    assert slicing.is_stateless(
        sig, classes_by_smali=cls, decisions_by_method_sig=dec,
    ) is True
    # Same method, but flagged reflective via the side-set:
    assert slicing.is_stateless(
        sig,
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
        reflective_method_sigs=frozenset({sig}),
    ) is False


def test_descent_blocked_when_helper_is_reflective() -> None:
    """End-to-end: the descent layer respects
    ``reflective_method_sigs`` — descent is skipped when the callee
    is in the reflective set, even if its body would otherwise pass
    the statelessness check."""
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes, include_branchless=True)
    classes_by_smali = {c.class_desc: c for c in classes}
    decisions_by_sig = {md.method_signature: md for md in mds}
    md = decisions_by_sig["Lcom/trace/Helpers;->gateOneHopGetter()V"]
    sliced = slicing.slice_predicate_origins(
        md,
        classes_by_smali=classes_by_smali,
        decisions_by_method_sig=decisions_by_sig,
        reflective_method_sigs=frozenset({
            "Lcom/trace/Helpers;->pureGetFlag()Ljava/lang/String;",
        }),
    )
    dp = _only_decision(sliced)
    origin = dp.predicate_origin
    assert isinstance(origin, MethodCallOrigin)  # descent skipped
    assert origin.method.method_name == "pureGetFlag"


# --- Cycle termination -----------------------------------------------------


def test_is_stateless_terminates_on_mutual_recursion_cycle() -> None:
    """``cycleA <-> cycleB`` mutual recursion. The visited-set cycle
    breaker triggers; per the v2 defensive default ('cycle = stateful
    without proof') both methods classify as stateful (False)."""
    cls, dec = _is_stateless_harness()
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->cycleA()I",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is False
    assert slicing.is_stateless(
        "Lcom/trace/Helpers;->cycleB()I",
        classes_by_smali=cls,
        decisions_by_method_sig=dec,
    ) is False


# --- ``_DescentBudget`` shape + closed-economy semantics -------------------


def test_descent_budget_visited_set_prevents_redundant_redescent() -> None:
    """The same callee invoked from two top-level decisions in a
    closure should be descended into AT MOST ONCE per shared budget
    instance. We simulate this by sharing a single ``_DescentBudget``
    across two slice calls and asserting the second call surfaces
    the v1 ``MethodCallOrigin`` (descent skipped because the helper
    is already in ``budget.visited``)."""
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes, include_branchless=True)
    classes_by_smali = {c.class_desc: c for c in classes}
    decisions_by_sig = {md.method_signature: md for md in mds}
    shared_budget = slicing._DescentBudget.fresh()

    md_one = decisions_by_sig["Lcom/trace/Helpers;->gateOneHopGetter()V"]
    sliced_one = slicing.slice_predicate_origins(
        md_one,
        classes_by_smali=classes_by_smali,
        decisions_by_method_sig=decisions_by_sig,
        descent_budget=shared_budget,
    )
    dp_one = _only_decision(sliced_one)
    assert isinstance(dp_one.predicate_origin, ConstOrigin)  # first descent succeeds
    # Second slice of the same fixture method shares the budget —
    # the visited set already contains pureGetFlag's cycle key, so
    # the second descent should bail and surface the v1 terminal.
    sliced_two = slicing.slice_predicate_origins(
        md_one,
        classes_by_smali=classes_by_smali,
        decisions_by_method_sig=decisions_by_sig,
        descent_budget=shared_budget,
    )
    dp_two = _only_decision(sliced_two)
    assert isinstance(dp_two.predicate_origin, MethodCallOrigin)
    assert dp_two.predicate_origin.method.method_name == "pureGetFlag"


def test_descent_budget_fresh_clamps_max_depth_to_hard_cap() -> None:
    """``_DescentBudget.fresh(max_depth=999)`` should clamp to the
    :data:`HARD_CAP_DEPTH` constant — operator misconfig of the
    11.6 ``trace.max_slice_depth`` knob can't blow the per-anchor
    budget."""
    budget = slicing._DescentBudget.fresh(max_depth=999)
    assert budget.remaining_depth == slicing.HARD_CAP_DEPTH


def test_descent_budget_fresh_clamps_negative_to_zero() -> None:
    """Negative ``max_depth`` (defensive against operator typos in
    the 11.6 config knob) clamps to zero — descent disabled."""
    budget = slicing._DescentBudget.fresh(max_depth=-5)
    assert budget.remaining_depth == 0


def test_max_slice_depth_module_constant_is_two() -> None:
    """11.4 ships with ``MAX_SLICE_DEPTH=2``; 11.6 promotes this to
    a config knob. Pin the v1 default so promotion is a noticeable
    change (this test will need updating when 11.6 lands, by design)."""
    assert slicing.MAX_SLICE_DEPTH == 2
    assert slicing.HARD_CAP_DEPTH == 4


# ===========================================================================
# Phase 11 sub-step 11.5 — same-class field-write-site walking
# ===========================================================================
#
# These tests exercise the ``_maybe_descend_field_read`` /
# ``_walk_field_write_sites`` paths on the new fixture methods in
# ``Helpers.smali``'s 11.5 section.


def test_field_write_descent_resolves_through_init_iput() -> None:
    """``gateInstanceFieldRead`` reads ``this.mPremiumFlag``; the
    field is initialised in ``<init>`` via
    ``const/4 v0, 0x1; iput-boolean v0, p0, ...``. Field-write-site
    descent finds the constructor write, slices the source register
    (v0), and resolves to ``ConstOrigin "0x1"``."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateInstanceFieldRead()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, ConstOrigin), f"expected ConstOrigin, got {type(origin).__name__}"
    assert origin.value == "0x1"


def test_field_write_descent_resolves_through_clinit_sput() -> None:
    """``gateStaticFieldRead`` reads ``Helpers.sFeatureEnabled``;
    the field is initialised in ``<clinit>`` via
    ``const/4 v0, 0x1; sput-boolean v0, ...``. Field-write-site
    descent finds the static-init write and resolves to
    ``ConstOrigin "0x1"``."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateStaticFieldRead()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, ConstOrigin)
    assert origin.value == "0x1"


def test_field_write_descent_constructor_priority_wins_over_setter() -> None:
    """``gateMultiWriteFieldRead`` reads ``mMultiWriteFlag`` which is
    written in BOTH ``<init>`` (const/4 0x1) AND a setter
    ``setMultiWriteFlag`` (a different value via param) AND a helper
    ``initMultiWriteFlag`` (const/4 0x0). Per Q1 (A) the constructor
    write wins → ``ConstOrigin "0x1"`` (NOT 0x0 from the helper)."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateMultiWriteFieldRead()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, ConstOrigin)
    assert origin.value == "0x1", (
        f"constructor-priority rule should pick the <init> write (0x1), "
        f"not initMultiWriteFlag's 0x0; got {origin.value}"
    )


def test_field_write_descent_blocked_on_cross_class_field() -> None:
    """``gateCrossClassFieldRead`` reads ``Lcom/trace/Slices;->sFlag:Z``
    — a field on a sibling class. Q2 (A) strict same-class match
    blocks the descent; v1 ``FieldReadOrigin`` terminal preserved."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateCrossClassFieldRead()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, FieldReadOrigin)
    assert origin.field.class_name == "com.trace.Slices"
    assert origin.field.field_name == "sFlag"


def test_field_write_descent_falls_back_when_no_write_site_exists() -> None:
    """``gateUnwrittenFieldRead`` reads ``mNeverWritten`` which is
    declared but never written anywhere in the class. Field-write
    descent finds no candidate site; v1 ``FieldReadOrigin`` terminal
    preserved (graceful failure, not crash)."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateUnwrittenFieldRead()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, FieldReadOrigin)
    assert origin.field.field_name == "mNeverWritten"


def test_closed_economy_budget_field_then_method_descent_chains() -> None:
    """``gateFieldWriteFromMethodCall``: the field's constructor
    write is sourced from ``move-result`` of ``pureGetA()``. So the
    descent chain is: gate (iget) → 11.5 field-write descent (1 hop;
    budget=2→1) → write site re-slices to MethodCallOrigin(pureGetA)
    → 11.4 method descent (1 hop; budget=1→0) → re-slice into
    pureGetA's body which itself calls pureGetB → would need budget=
    -1 to descend further, so the inner _maybe_descend stops at
    MethodCallOrigin(pureGetB). This pins the closed-economy
    semantics: 1 hop field + 1 hop method exhausts the v1 default
    MAX_SLICE_DEPTH=2 budget."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateFieldWriteFromMethodCall()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, MethodCallOrigin), (
        f"expected MethodCallOrigin terminal at depth-2 cap, got {type(origin).__name__}"
    )
    # The chain went: gate → field-write descent (depth 2→1) → invoke
    # pureGetA → method descent (depth 1→0) → re-slice pureGetA's body
    # which itself invokes pureGetB → would need depth 0→-1 to
    # descend, so we stop. The terminal is the deepest method we
    # reached: pureGetB (the method whose return v0 was the last
    # MethodCallOrigin we sliced before the budget hit zero).
    assert origin.method.method_name == "pureGetB"


def test_closed_economy_budget_exhaustion_mid_chain() -> None:
    """``gateFieldWriteFromDeepChain``: the field's constructor write
    sources from ``pureChainHopOne`` which itself chains 3 method
    hops (One → Two → Three → const). With v1 default budget = 2,
    the 1 hop field-write descent + 1 hop method descent exhausts
    it after reaching ``pureChainHopTwo`` — operator sees the
    "stopped at depth 2" terminal."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateFieldWriteFromDeepChain()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, MethodCallOrigin)
    assert origin.method.method_name == "pureChainHopTwo", (
        f"expected pureChainHopTwo at depth-2 cap, got {origin.method.method_name}"
    )


def test_field_write_descent_v1_path_when_descent_kwargs_omitted() -> None:
    """Public API contract: when ``classes_by_smali`` and
    ``decisions_by_method_sig`` are both omitted, the slicer behaves
    exactly like v1 — every ``FieldReadOrigin`` terminal is surfaced
    unchanged (no field-write descent without the descent index)."""
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes, include_branchless=True)
    by_sig = {md.method_signature: md for md in mds}
    md = by_sig["Lcom/trace/Helpers;->gateInstanceFieldRead()V"]
    sliced = slicing.slice_predicate_origins(md)  # no descent kwargs
    dp = _only_decision(sliced)
    assert isinstance(dp.predicate_origin, FieldReadOrigin)
    assert dp.predicate_origin.field.field_name == "mPremiumFlag"


# ===========================================================================
# Phase 11 sub-step 11.6 / DEC-025 — descent_depth field on cap-stop
# terminals + v1-vs-v2 corpus measurement (ISSUE-013 close-out criterion)
# ===========================================================================


def test_descent_depth_present_on_method_call_cap_stop() -> None:
    """``gateThreeHopChainCapped`` chains 3 pure helpers (depth-3
    const), ``MAX_SLICE_DEPTH=2`` caps at hop 3 → terminal is
    ``MethodCallOrigin(pureChainHopThree, descent_depth=2)``. This
    pins the 11.6 wire shape: cap-stop terminals carry the
    descent-stack depth at which the cap fired, so the frontend's
    depth pill can render "via 2 helper methods"."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateThreeHopChainCapped()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, MethodCallOrigin)
    assert origin.descent_depth == 2, (
        f"expected depth-2 cap-stop tag, got descent_depth={origin.descent_depth}"
    )


def test_descent_depth_zero_when_descent_blocked_by_stateful_callee() -> None:
    """``gateStatefulFieldWriteCallee`` calls a stateful helper —
    ``is_stateless`` returns False at the top-level
    ``_maybe_descend_method_call``, before any descent fires. The
    cap-stop early-exit returns the v1 terminal at
    ``current_descent_depth=0`` → no spurious depth tag.
    Operator-facing: no pill on the UI for the stateful-block case
    (matches the v1 wire shape exactly)."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateStatefulFieldWriteCallee()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, MethodCallOrigin)
    assert origin.descent_depth == 0


def test_descent_depth_zero_on_v1_terminal_with_descent_disabled() -> None:
    """When the slicer is called without descent kwargs (v1 path),
    every ``MethodCallOrigin`` / ``FieldReadOrigin`` carries
    ``descent_depth=0`` (the default). Pins the v1-wire-shape
    backwards-compat guarantee: 11.6 doesn't force callers that
    only want v1 semantics to see any new field on origin."""
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes)
    by_sig = {md.method_signature: md for md in mds}
    md = by_sig["Lcom/trace/Plans;->gateBoolPredicate()V"]
    sliced = slicing.slice_predicate_origins(md)  # no descent kwargs
    dp = _only_decision(sliced)
    origin = dp.predicate_origin
    # ``gateBoolPredicate`` calls the external ``isPremium`` (not in
    # the in-app classes index) → MethodCallOrigin terminal in both
    # v1 and v2; v1 path explicitly sets descent_depth=0.
    assert isinstance(origin, MethodCallOrigin)
    assert origin.descent_depth == 0


def test_descent_depth_does_not_appear_on_const_param_composite_variants() -> None:
    """Q1 (A) literal-spec rule: ``ConstOrigin`` / ``ParamOrigin`` /
    ``CompositeOrigin`` stay v1-shaped — no ``descent_depth`` field
    even when descent successfully resolved through them. Pins the
    schema commitment that only the two non-terminal-in-v1 variants
    carry the depth signal. (When operators care about depth on a
    successfully-resolved Const, they read it indirectly via the
    pill on the *prior* call site — but the success-path Const
    terminal itself stays v1-shaped to keep the schema small.)
    """
    sliced = _parse_and_slice_with_descent()
    # ``gateOneHopGetter`` resolves to ConstOrigin via 1-hop descent.
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateOneHopGetter()V"])
    origin = dp.predicate_origin
    assert isinstance(origin, ConstOrigin)
    # ConstOrigin must NOT have a descent_depth attribute.
    assert not hasattr(origin, "descent_depth"), (
        f"ConstOrigin unexpectedly carries descent_depth={getattr(origin, 'descent_depth', None)!r}; "
        f"Q1 (A) spec: only MethodCallOrigin / FieldReadOrigin tagged"
    )


def test_descent_depth_round_trips_through_dataclasses_asdict() -> None:
    """Wire-format check: ``dataclasses.asdict`` of a tagged
    ``MethodCallOrigin`` includes ``descent_depth`` so the cache
    SQLite + the frontend wire payload both see the field. Critical
    for the new ``api/trace.ts`` ``descent_depth?: number`` typing —
    if the field weren't on the dict, the frontend would never
    render the pill."""
    sliced = _parse_and_slice_with_descent()
    dp = _only_decision(sliced["Lcom/trace/Helpers;->gateThreeHopChainCapped()V"])
    payload = json.dumps(dataclasses.asdict(dp), default=str)
    decoded = json.loads(payload)
    assert decoded["predicate_origin"]["kind"] == "method_call"
    assert decoded["predicate_origin"]["descent_depth"] == 2


def test_v1_vs_v2_corpus_measurement_v2_resolves_strictly_more_terminals() -> None:
    """**ISSUE-013 close-out criterion** (Q4 (A) of the 11.6 planning
    checkpoint). Run both v1 (no descent kwargs) and v2 (with descent
    kwargs) over the entire ``trace_smali`` fixture corpus. Count
    decision points whose ``predicate_origin`` is a v1-terminal
    variant (``MethodCallOrigin`` / ``FieldReadOrigin``) — those are
    the cases where v1 left the operator with "method call /
    field read; trace yourself in the decompiler". Assert that:

    1. v2 resolves *strictly more* terminals to a non-Method/non-Field
       variant than v1 did. The 11.4 + 11.5 fixtures were built with
       this comparison in mind: every fixture method that exercises
       descent has a v1-terminal-shape under no-descent and a deeper
       terminal (often ``ConstOrigin``) under descent.
    2. v2 produces at least one tagged terminal (``descent_depth >= 1``)
       — confirms the new pipeline actually emits the depth signal
       for the cap-stop / cycle-blocked / external-blocked cases.
    3. v2 produces zero ``predicate_origin: None`` cases on this
       corpus (no slice failures; confirms descent never breaks the
       baseline slicer behaviour).

    This test is the regression floor that locks in 11.4 + 11.5's
    measurable improvement; the >50% production-dogfood threshold
    from DEC-025's spec text still requires real-app verification,
    but the corpus floor is locked here so 11.x or 12.x changes
    can't silently regress the v2 resolution rate.
    """
    v1 = {}
    v2 = _parse_and_slice_with_descent()

    # Build v1 baseline using the same harness shape as v2 (parse +
    # slice over every method with decisions) but WITHOUT descent
    # kwargs — apples-to-apples on the same fixture set.
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes, include_branchless=True)
    for md in mds:
        if not md.decision_points:
            continue
        v1[md.method_signature] = slicing.slice_predicate_origins(md)

    # Same set of methods on both sides — sanity check the harnesses.
    assert set(v1.keys()) == set(v2.keys()), (
        "v1 / v2 harnesses produced different method sets — fixture / "
        "include_branchless mismatch?"
    )

    def _count_unresolved(corpus: dict) -> int:
        n = 0
        for md in corpus.values():
            for dp in md.decision_points:
                origin = dp.predicate_origin
                if isinstance(origin, (MethodCallOrigin, FieldReadOrigin)):
                    n += 1
        return n

    def _count_tagged(corpus: dict) -> int:
        n = 0
        for md in corpus.values():
            for dp in md.decision_points:
                origin = dp.predicate_origin
                if isinstance(origin, (MethodCallOrigin, FieldReadOrigin)) and origin.descent_depth >= 1:
                    n += 1
        return n

    def _count_none(corpus: dict) -> int:
        n = 0
        for md in corpus.values():
            for dp in md.decision_points:
                if dp.predicate_origin is None:
                    n += 1
        return n

    v1_unresolved = _count_unresolved(v1)
    v2_unresolved = _count_unresolved(v2)
    v2_tagged = _count_tagged(v2)
    v2_none = _count_none(v2)

    # (1) Strict reduction in unresolved terminals (descent did real
    # work). On this fixture corpus the gain is large — 11.4 + 11.5
    # were specifically built to exercise descent paths.
    assert v2_unresolved < v1_unresolved, (
        f"v2 must resolve strictly fewer terminals than v1; "
        f"got v1={v1_unresolved}, v2={v2_unresolved} — "
        f"if this test starts failing, descent has regressed (or the "
        f"fixture's exercise-paths got removed). Check 11.4 / 11.5 "
        f"fixtures (Helpers.smali) and the slicer's descent logic."
    )

    # (2) At least one tagged origin — the cap-stop / cycle-blocked
    # / external-blocked terminal lives somewhere in the corpus.
    # ``gateThreeHopChainCapped`` alone provides this (depth-2 cap
    # → tagged), but the assertion is corpus-wide so the test
    # doesn't pin to one fixture.
    assert v2_tagged >= 1, (
        f"v2 produced zero tagged origins (descent_depth >= 1) — "
        f"the depth-tagging code path on _maybe_descend_method_call / "
        f"_maybe_descend_field_read may have regressed."
    )

    # (3) v2 doesn't introduce any new slice failures.
    v1_none = _count_none(v1)
    assert v2_none <= v1_none, (
        f"v2 produced more slice failures than v1; got "
        f"v1={v1_none}, v2={v2_none}. Descent must never make slicing"
        f" *worse* — only equal or better."
    )


# ---------------------------------------------------------------------------
# Phase 13 v3.X-next.1 / DEC-031 — CallSite + method_invocations
#
# Tests for ``slicing.extract_call_sites``, ``slicing._compute_branch_dominance``,
# the ``FRAMEWORK_CLASS_PREFIXES`` denylist, the ``CallSite`` dataclass
# shape, and the additive ``BehaviorAnchor.method_invocations`` field.
#
# All-synthetic — we hand-build :class:`MethodDecisions` so each test
# pins one specific slicer behaviour without relying on the fixture
# smali corpus (which would couple v3.X-next.1's regression posture to
# any future fixture refactor).


_CALLER_SIG = "Lcom/app/Caller;->run()V"
_CALLER_CLASS_SMALI = "Lcom/app/Caller;"


def _caller_ref() -> MethodRef:
    """The canonical caller MethodRef used by the synthetic fixtures
    below. Matches :data:`_CALLER_SIG` round-tripped through
    :meth:`MethodRef.from_smali_signature`."""
    return MethodRef.from_smali_signature(_CALLER_SIG)


def _md(
    *,
    instructions: tuple[str, ...],
    decision_points: tuple[DecisionPoint, ...] = (),
    label_index: tuple[tuple[str, int], ...] = (),
    method_signature: str = _CALLER_SIG,
) -> decisions.MethodDecisions:
    """Build a synthetic :class:`MethodDecisions` for the v3.X-next.1
    test class. The signature defaults to :data:`_CALLER_SIG` so each
    test reads as "this is what the caller's body looks like"; tests
    that need a different caller (nested-locals scenario) override
    explicitly."""
    return decisions.MethodDecisions(
        method_signature=method_signature,
        src_file="com/app/Caller.smali",
        decision_points=decision_points,
        label_index=label_index,
        instructions=instructions,
    )


def _ifeqz_at(
    *,
    instruction_index: int,
    target_label: str,
    method_signature: str = _CALLER_SIG,
) -> DecisionPoint:
    """Build a synthetic ``if-eqz`` DecisionPoint pointing at *target_label*.

    Mirrors how :mod:`androscan.analysis.decisions` shapes the
    two-branch fan-out (``"true"`` branch jumps to the target;
    ``"false"`` branch falls through to the next instruction)."""
    return DecisionPoint(
        method=MethodRef.from_smali_signature(method_signature),
        instruction_index=instruction_index,
        source_line=None,
        kind=DecisionKind.IF_EQZ,
        predicate_registers=("v0",),
        branches=(
            Branch(label="true", target_label=target_label),
            Branch(label="false", target_label=None),
        ),
    )


def _app_classes(*class_descs: str) -> dict[str, smali_parser.ClassDecl]:
    """Build a ``classes_by_smali`` map populated with sentinel entries
    for the given class descriptors. The slicer's primary resolution
    path only checks for *membership*, never inspects the
    :class:`ClassDecl` body, so we can use ``None`` sentinels — typed
    ``Any`` here to satisfy the static checker."""
    return {desc: None for desc in class_descs}  # type: ignore[misc]


class TestPhase13V3XNext1_MethodInvocations:
    """Phase 13 v3.X-next.1 (DEC-031) — slicer extension.

    Locked Q&As under test:

    * Q1=(a) — :class:`CallSite` lives in
      :mod:`androscan.analysis.trace_types` (import path test).
    * Q2=(a) — ``method_invocations`` keyed by overload-signature shape.
    * Q3=(c) — hybrid invoke-target resolution (same-module direct;
      cross-module via optional resolver).
    * Q4=(a) — single ``in_branch_of`` dominator + ``branch_label``
      ride-along; innermost wins.
    * Q5=(c) — slicer applies ``FRAMEWORK_CLASS_PREFIXES`` denylist.
    """

    # -----------------------------------------------------------------
    # Linear scenarios (no decisions) — 4 tests

    def test_linear_three_invokes_no_branches(self) -> None:
        """3 sequential invokes, no decisions → 3 CallSites, all
        un-dominated (``in_branch_of=None, branch_label=None``)."""
        md = _md(instructions=(
            "invoke-static {p0}, Lcom/app/A;->aa()V",
            "invoke-static {p0}, Lcom/app/B;->bb()V",
            "invoke-static {p0}, Lcom/app/C;->cc()V",
            "return-void",
        ))
        classes = _app_classes("Lcom/app/Caller;", "Lcom/app/A;", "Lcom/app/B;", "Lcom/app/C;")
        sites = slicing.extract_call_sites(md, classes_by_smali=classes)
        assert len(sites) == 3
        for cs in sites:
            assert cs.in_branch_of is None
            assert cs.branch_label is None
        assert [cs.callee.class_name for cs in sites] == ["com.app.A", "com.app.B", "com.app.C"]

    def test_linear_invokes_preserve_source_order(self) -> None:
        """``extract_call_sites`` returns CallSites ordered by
        ``instruction_index`` — same as source order under sequential
        iteration, but the explicit sort is the contract."""
        md = _md(instructions=(
            "invoke-static {}, Lcom/app/A;->aa()V",
            "invoke-static {}, Lcom/app/B;->bb()V",
            "invoke-static {}, Lcom/app/C;->cc()V",
        ))
        classes = _app_classes("Lcom/app/Caller;", "Lcom/app/A;", "Lcom/app/B;", "Lcom/app/C;")
        sites = slicing.extract_call_sites(md, classes_by_smali=classes)
        assert [cs.instruction_index for cs in sites] == [0, 1, 2]

    def test_linear_zero_invokes_returns_empty_tuple(self) -> None:
        """Method with no invoke instructions → empty tuple. The
        caller-side wire-up in ``trace_behavior`` drops empty tuples
        from the ``method_invocations`` dict to keep it tight."""
        md = _md(instructions=(
            "const/4 v0, 0x0",
            "return-void",
        ))
        classes = _app_classes("Lcom/app/Caller;")
        sites = slicing.extract_call_sites(md, classes_by_smali=classes)
        assert sites == ()

    def test_caller_methodref_matches_method_signature(self) -> None:
        """Every CallSite's ``caller`` round-trips to
        ``method_signature`` — the caller anchor that the v3.X-next.2
        emitter joins against."""
        md = _md(instructions=(
            "invoke-static {}, Lcom/app/A;->aa()V",
        ))
        classes = _app_classes("Lcom/app/Caller;", "Lcom/app/A;")
        sites = slicing.extract_call_sites(md, classes_by_smali=classes)
        assert len(sites) == 1
        assert sites[0].caller.smali_signature == _CALLER_SIG

    # -----------------------------------------------------------------
    # Branchy scenarios (one if-eqz) — 4 tests

    def test_branchy_pre_branch_callsite_undominated(self) -> None:
        """A CallSite at an instruction-index *before* the if-eqz
        decision must have ``in_branch_of=None, branch_label=None``."""
        md = _md(
            instructions=(
                "invoke-static {p0}, Lcom/app/Pre;->pre()V",   # idx 0 — pre-branch
                "if-eqz v0, :cond_0",                            # idx 1
                "invoke-static {p0}, Lcom/app/False;->fa()V",   # idx 2 — false arm
                "goto :end_0",                                    # idx 3
                "invoke-static {p0}, Lcom/app/True;->ta()V",    # idx 4 — true arm
                "return-void",                                    # idx 5
            ),
            decision_points=(_ifeqz_at(instruction_index=1, target_label=":cond_0"),),
            label_index=((":cond_0", 4), (":end_0", 5)),
        )
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/Pre;", "Lcom/app/False;", "Lcom/app/True;")
        sites = {cs.instruction_index: cs for cs in slicing.extract_call_sites(md, classes_by_smali=classes)}
        assert sites[0].in_branch_of is None
        assert sites[0].branch_label is None

    def test_branchy_true_arm_dominated(self) -> None:
        """CallSite in the true arm has ``in_branch_of = decision_idx``
        and ``branch_label = "true"`` (matches :class:`Branch.label`
        verbatim)."""
        md = _md(
            instructions=(
                "invoke-static {p0}, Lcom/app/Pre;->pre()V",
                "if-eqz v0, :cond_0",
                "invoke-static {p0}, Lcom/app/False;->fa()V",
                "goto :end_0",
                "invoke-static {p0}, Lcom/app/True;->ta()V",
                "return-void",
            ),
            decision_points=(_ifeqz_at(instruction_index=1, target_label=":cond_0"),),
            label_index=((":cond_0", 4), (":end_0", 5)),
        )
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/Pre;", "Lcom/app/False;", "Lcom/app/True;")
        sites = {cs.instruction_index: cs for cs in slicing.extract_call_sites(md, classes_by_smali=classes)}
        assert sites[4].in_branch_of == 1
        assert sites[4].branch_label == "true"

    def test_branchy_false_arm_dominated(self) -> None:
        """CallSite in the false (fall-through) arm has
        ``branch_label = "false"`` and points at the same decision."""
        md = _md(
            instructions=(
                "invoke-static {p0}, Lcom/app/Pre;->pre()V",
                "if-eqz v0, :cond_0",
                "invoke-static {p0}, Lcom/app/False;->fa()V",
                "goto :end_0",
                "invoke-static {p0}, Lcom/app/True;->ta()V",
                "return-void",
            ),
            decision_points=(_ifeqz_at(instruction_index=1, target_label=":cond_0"),),
            label_index=((":cond_0", 4), (":end_0", 5)),
        )
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/Pre;", "Lcom/app/False;", "Lcom/app/True;")
        sites = {cs.instruction_index: cs for cs in slicing.extract_call_sites(md, classes_by_smali=classes)}
        assert sites[2].in_branch_of == 1
        assert sites[2].branch_label == "false"

    def test_branchy_two_register_predicate_no_special_case(self) -> None:
        """``if-eq`` (two-register variant) dominance behaves identically
        to ``if-eqz`` — the slicer treats every Branch label uniformly."""
        decision = DecisionPoint(
            method=_caller_ref(),
            instruction_index=0,
            source_line=None,
            kind=DecisionKind.IF_EQ,
            predicate_registers=("v0", "v1"),
            branches=(
                Branch(label="true", target_label=":cond_0"),
                Branch(label="false", target_label=None),
            ),
        )
        md = _md(
            instructions=(
                "if-eq v0, v1, :cond_0",
                "invoke-static {}, Lcom/app/F;->fa()V",
                "goto :end_0",
                "invoke-static {}, Lcom/app/T;->ta()V",
                "return-void",
            ),
            decision_points=(decision,),
            label_index=((":cond_0", 3), (":end_0", 4)),
        )
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/F;", "Lcom/app/T;")
        sites = {cs.instruction_index: cs for cs in slicing.extract_call_sites(md, classes_by_smali=classes)}
        assert sites[1].branch_label == "false"
        assert sites[3].branch_label == "true"

    # -----------------------------------------------------------------
    # Nested-locals scenarios — 3 tests

    def test_nested_outer_method_invocations_keyed_by_signature(self) -> None:
        """Outer method's CallSites flow into the
        ``method_invocations`` dict under the outer's
        ``method_signature``; the inner method's CallSites flow under
        the inner's signature. Keys are the overload-signature shape
        (Q2=(a)) — matches :attr:`MethodRef.smali_signature`."""
        outer_sig = "Lcom/app/Login;->login(Ljava/lang/String;)Z"
        inner_sig = "Lcom/app/Login;->check_active_session()Z"
        outer_md = _md(
            method_signature=outer_sig,
            instructions=(
                "invoke-virtual {p0}, Lcom/app/Login;->check_active_session()Z",
                "invoke-virtual {p0, p1}, Lcom/app/Login;->check_input(Ljava/lang/String;)Z",
                "invoke-virtual {p0, p1}, Lcom/app/Login;->validate_pin(Ljava/lang/String;)Z",
                "return v0",
            ),
        )
        classes = _app_classes("Lcom/app/Login;")
        sites = slicing.extract_call_sites(outer_md, classes_by_smali=classes)
        assert len(sites) == 3
        method_names = [cs.callee.method_name for cs in sites]
        assert method_names == ["check_active_session", "check_input", "validate_pin"]
        assert all(cs.caller.smali_signature == outer_sig for cs in sites)
        assert outer_sig != inner_sig  # paranoia — Q2 key shape distinct

    def test_nested_innermost_wins_dominance(self) -> None:
        """CallSite inside an inner if-eqz's true arm picks the
        *inner* decision as ``in_branch_of`` (not the outer's),
        per Q4=(a) — innermost dominator wins."""
        outer = _ifeqz_at(instruction_index=0, target_label=":outer_true")
        inner = _ifeqz_at(instruction_index=3, target_label=":inner_true")
        md = _md(
            instructions=(
                "if-eqz v0, :outer_true",                          # 0 — outer decision
                "invoke-static {}, Lcom/app/OuterFalse;->of()V",  # 1 — outer false arm
                "goto :end_outer",                                  # 2
                "if-eqz v1, :inner_true",                           # 3 — inner decision (in outer true arm)
                "invoke-static {}, Lcom/app/InnerFalse;->if_()V", # 4 — inner false arm
                "goto :end_inner",                                   # 5
                "invoke-static {}, Lcom/app/InnerTrue;->it()V",   # 6 — inner true arm
                "return-void",                                       # 7
            ),
            decision_points=(outer, inner),
            label_index=(
                (":outer_true", 3),
                (":inner_true", 6),
                (":end_outer", 7),
                (":end_inner", 7),
            ),
        )
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/OuterFalse;", "Lcom/app/InnerFalse;", "Lcom/app/InnerTrue;")
        sites = {cs.instruction_index: cs for cs in slicing.extract_call_sites(md, classes_by_smali=classes)}
        assert sites[6].in_branch_of == 3
        assert sites[6].branch_label == "true"
        assert sites[4].in_branch_of == 3
        assert sites[4].branch_label == "false"

    def test_nested_outer_arm_only_uses_outer_dominator(self) -> None:
        """A CallSite in the outer-arm region that is NOT covered by
        any inner arm gets the outer decision as its dominator."""
        outer = _ifeqz_at(instruction_index=0, target_label=":outer_true")
        md = _md(
            instructions=(
                "if-eqz v0, :outer_true",                          # 0 — outer decision
                "invoke-static {}, Lcom/app/OuterFalse;->of()V",  # 1 — outer false arm
                "return-void",                                       # 2
            ),
            decision_points=(outer,),
            label_index=((":outer_true", 2),),
        )
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/OuterFalse;")
        sites = {cs.instruction_index: cs for cs in slicing.extract_call_sites(md, classes_by_smali=classes)}
        assert sites[1].in_branch_of == 0
        assert sites[1].branch_label == "false"

    # -----------------------------------------------------------------
    # Shape / contract tests — 4 tests

    def test_callsite_is_frozen(self) -> None:
        """:class:`CallSite` is ``frozen=True`` — attempting to mutate a
        field raises :class:`dataclasses.FrozenInstanceError`."""
        ref = _caller_ref()
        cs = CallSite(caller=ref, instruction_index=0, callee=ref, in_branch_of=None, branch_label=None)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cs.instruction_index = 99  # type: ignore[misc]

    def test_callsite_is_hashable(self) -> None:
        """Frozen dataclass → hashable. Tests can stuff CallSites in
        sets / dict keys for de-duplication."""
        ref = _caller_ref()
        cs1 = CallSite(caller=ref, instruction_index=0, callee=ref, in_branch_of=None, branch_label=None)
        cs2 = CallSite(caller=ref, instruction_index=0, callee=ref, in_branch_of=None, branch_label=None)
        assert hash(cs1) == hash(cs2)
        assert {cs1, cs2} == {cs1}

    def test_callsite_roundtrips_asdict_json(self) -> None:
        """``dataclasses.asdict`` + ``json.dumps`` + ``json.loads``
        round-trip preserves every field. Belt-and-braces guard for
        the trace cache layer's encode/decode contract."""
        ref = MethodRef.from_smali_signature("Lcom/app/Caller;->run()V")
        cs = CallSite(
            caller=ref,
            instruction_index=7,
            callee=MethodRef.from_smali_signature("Lcom/app/X;->y()V"),
            in_branch_of=4,
            branch_label="case 2",
        )
        encoded = json.dumps(dataclasses.asdict(cs), sort_keys=True)
        raw = json.loads(encoded)
        assert raw["instruction_index"] == 7
        assert raw["in_branch_of"] == 4
        assert raw["branch_label"] == "case 2"
        assert raw["caller"]["class_name"] == "com.app.Caller"
        assert raw["callee"]["method_name"] == "y"

    def test_behavior_anchor_method_invocations_defaults_empty(self) -> None:
        """Constructing :class:`BehaviorAnchor` without
        ``method_invocations`` defaults to ``{}`` — backwards-compat
        with v2 / v2.0.1 / v3.0 / v3.1 cached anchors that were
        serialised before the field existed."""
        anchor = BehaviorAnchor(entry_method=_caller_ref(), hops=1)
        assert anchor.method_invocations == {}

    def test_behavior_anchor_method_invocations_can_be_populated(self) -> None:
        """Explicit ``method_invocations`` flows through the
        constructor — sanity check that the additive field is wired
        up correctly on :class:`BehaviorAnchor`."""
        ref = _caller_ref()
        cs = CallSite(caller=ref, instruction_index=0, callee=ref, in_branch_of=None, branch_label=None)
        anchor = BehaviorAnchor(
            entry_method=ref, hops=1,
            method_invocations={_CALLER_SIG: (cs,)},
        )
        assert anchor.method_invocations[_CALLER_SIG] == (cs,)

    # -----------------------------------------------------------------
    # Framework filter (Q5=(c)) — 4 tests

    @pytest.mark.parametrize("prefix", list(slicing.FRAMEWORK_CLASS_PREFIXES))
    def test_framework_filter_drops_all_ten_prefixes(self, prefix: str) -> None:
        """Each of the 10 ``FRAMEWORK_CLASS_PREFIXES`` entries must
        cause the slicer to drop matching invoke targets — keeps the
        denominator consistent with the v3.1 emitter's denylist."""
        target_class = prefix + "Synthetic;"
        md = _md(instructions=(
            f"invoke-static {{p0}}, {target_class}->doit()V",
            "return-void",
        ))
        classes = _app_classes(_CALLER_CLASS_SMALI)
        sites = slicing.extract_call_sites(md, classes_by_smali=classes)
        assert sites == ()

    def test_framework_filter_drops_kotlin_intrinsics(self) -> None:
        """The canonical noise case — ``Kotlin.Intrinsics.checkNotNull``
        — must drop. This is the rank-1 sibling the v3.1 emitter's
        denylist was originally built to suppress."""
        md = _md(instructions=(
            "invoke-static {p0}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V",
            "return-void",
        ))
        classes = _app_classes(_CALLER_CLASS_SMALI)
        assert slicing.extract_call_sites(md, classes_by_smali=classes) == ()

    def test_framework_filter_drops_android_view(self) -> None:
        """``Landroid/`` prefix catches platform classes the operator
        never wants in the flowchart (View / Bundle / Activity
        framework methods)."""
        md = _md(instructions=(
            "invoke-virtual {p0}, Landroid/view/View;->getId()I",
            "return-void",
        ))
        classes = _app_classes(_CALLER_CLASS_SMALI)
        assert slicing.extract_call_sites(md, classes_by_smali=classes) == ()

    def test_app_code_callee_not_filtered_by_framework_list(self) -> None:
        """``Lcom/app/`` (or any non-framework prefix) target passes
        through the filter unchanged."""
        md = _md(instructions=(
            "invoke-static {p0}, Lcom/app/Service;->call()V",
            "return-void",
        ))
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/Service;")
        sites = slicing.extract_call_sites(md, classes_by_smali=classes)
        assert len(sites) == 1
        assert sites[0].callee.class_name == "com.app.Service"

    # -----------------------------------------------------------------
    # Resolution policy (Q3=(c)) — 5 tests

    def test_primary_path_resolves_in_module_callee(self) -> None:
        """Target class IS in ``classes_by_smali`` → primary path
        constructs the callee MethodRef directly off the smali line
        (no resolver invoked)."""
        md = _md(instructions=(
            "invoke-static {p0}, Lcom/app/In;->aa()V",
            "return-void",
        ))
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/In;")
        sites = slicing.extract_call_sites(md, classes_by_smali=classes)
        assert len(sites) == 1
        assert sites[0].callee.smali_signature == "Lcom/app/In;->aa()V"

    def test_resolver_none_drops_cross_module_callee(self) -> None:
        """Target class NOT in ``classes_by_smali`` AND no resolver
        provided → CallSite dropped silently (Q3=(c) fallback path
        unavailable)."""
        md = _md(instructions=(
            "invoke-static {p0}, Lcom/external/X;->y()V",
            "return-void",
        ))
        classes = _app_classes(_CALLER_CLASS_SMALI)
        sites = slicing.extract_call_sites(md, classes_by_smali=classes)
        assert sites == ()

    def test_resolver_returning_methodref_emits_callsite(self) -> None:
        """Resolver returns a :class:`MethodRef` → the CallSite is
        emitted with the resolver's MethodRef as the callee (Q3=(c)
        cross-module fallback success path)."""
        external_ref = MethodRef.from_smali_signature("Lcom/external/X;->y()V")

        def resolver(sig: str) -> MethodRef:
            assert sig == "Lcom/external/X;->y()V"
            return external_ref

        md = _md(instructions=(
            "invoke-static {p0}, Lcom/external/X;->y()V",
            "return-void",
        ))
        classes = _app_classes(_CALLER_CLASS_SMALI)
        sites = slicing.extract_call_sites(
            md, classes_by_smali=classes, call_graph_resolver=resolver,
        )
        assert len(sites) == 1
        assert sites[0].callee is external_ref

    def test_resolver_returning_none_drops_callsite(self) -> None:
        """Resolver returns ``None`` (call-graph doesn't know the
        target) → CallSite dropped silently."""
        def resolver(sig: str) -> None:
            return None

        md = _md(instructions=(
            "invoke-static {p0}, Lcom/external/X;->y()V",
            "return-void",
        ))
        classes = _app_classes(_CALLER_CLASS_SMALI)
        sites = slicing.extract_call_sites(
            md, classes_by_smali=classes, call_graph_resolver=resolver,
        )
        assert sites == ()

    def test_resolver_not_called_for_in_module_target(self) -> None:
        """When the primary path resolves the target (class is in
        ``classes_by_smali``), the resolver MUST NOT be invoked — its
        only role is the cross-module fallback."""
        resolver_calls: list[str] = []

        def resolver(sig: str) -> MethodRef:
            resolver_calls.append(sig)
            return MethodRef.from_smali_signature(sig)

        md = _md(instructions=(
            "invoke-static {p0}, Lcom/app/In;->aa()V",
            "return-void",
        ))
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/In;")
        sites = slicing.extract_call_sites(
            md, classes_by_smali=classes, call_graph_resolver=resolver,
        )
        assert len(sites) == 1
        assert resolver_calls == []

    # -----------------------------------------------------------------
    # Dominance edge cases — 5 tests

    def test_packed_switch_per_case_branch_label(self) -> None:
        """``packed-switch`` with 4 cases — each per-case CallSite gets
        its arm's ``Branch.label`` (``"case 0"`` / ``"case 1"`` / ... /
        ``"default"``) verbatim."""
        switch = DecisionPoint(
            method=_caller_ref(),
            instruction_index=0,
            source_line=None,
            kind=DecisionKind.PACKED_SWITCH,
            predicate_registers=("v0",),
            branches=(
                Branch(label="case 0", target_label=":case_0"),
                Branch(label="case 1", target_label=":case_1"),
                Branch(label="case 2", target_label=":case_2"),
                Branch(label="default", target_label=None),
            ),
        )
        md = _md(
            instructions=(
                "packed-switch v0, :switch_data",   # 0
                "invoke-static {}, Lcom/app/D;->d()V",   # 1 — default arm (fall-through)
                "goto :end",                         # 2
                "invoke-static {}, Lcom/app/A;->a()V",   # 3 — case 0
                "goto :end",                         # 4
                "invoke-static {}, Lcom/app/B;->b()V",   # 5 — case 1
                "goto :end",                         # 6
                "invoke-static {}, Lcom/app/C;->c()V",   # 7 — case 2
                "return-void",                        # 8
            ),
            decision_points=(switch,),
            label_index=(
                (":case_0", 3),
                (":case_1", 5),
                (":case_2", 7),
                (":end", 8),
            ),
        )
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/A;", "Lcom/app/B;", "Lcom/app/C;", "Lcom/app/D;")
        sites = {cs.instruction_index: cs for cs in slicing.extract_call_sites(md, classes_by_smali=classes)}
        assert sites[1].branch_label == "default"
        assert sites[3].branch_label == "case 0"
        assert sites[5].branch_label == "case 1"
        assert sites[7].branch_label == "case 2"

    def test_sparse_switch_default_label(self) -> None:
        """``sparse-switch`` with a ``default`` arm — fall-through
        CallSites pick up the ``"default"`` branch_label (matches
        :class:`Branch.label` from ``decisions.py``)."""
        switch = DecisionPoint(
            method=_caller_ref(),
            instruction_index=0,
            source_line=None,
            kind=DecisionKind.SPARSE_SWITCH,
            predicate_registers=("v0",),
            branches=(
                Branch(label="case 100", target_label=":case_100"),
                Branch(label="default", target_label=None),
            ),
        )
        md = _md(
            instructions=(
                "sparse-switch v0, :switch_data",   # 0
                "invoke-static {}, Lcom/app/D;->d()V",   # 1 — default
                "goto :end",                         # 2
                "invoke-static {}, Lcom/app/H;->h()V",   # 3 — case 100
                "return-void",                        # 4
            ),
            decision_points=(switch,),
            label_index=((":case_100", 3), (":end", 4)),
        )
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/D;", "Lcom/app/H;")
        sites = {cs.instruction_index: cs for cs in slicing.extract_call_sites(md, classes_by_smali=classes)}
        assert sites[1].branch_label == "default"
        assert sites[3].branch_label == "case 100"

    def test_dominance_no_decisions_returns_empty(self) -> None:
        """``_compute_branch_dominance`` on a decision-free method
        body returns an empty dict — every instruction is
        un-dominated."""
        md = _md(instructions=(
            "invoke-static {}, Lcom/app/A;->a()V",
            "return-void",
        ))
        dom = slicing._compute_branch_dominance(md)
        assert dom == {}

    def test_dominance_unresolvable_target_label_skipped(self) -> None:
        """Defensive: a Branch with ``target_label`` not in
        ``label_index`` is silently skipped (shouldn't happen on
        well-formed parser output but the dominance helper stays
        defensive)."""
        broken_dp = DecisionPoint(
            method=_caller_ref(),
            instruction_index=0,
            source_line=None,
            kind=DecisionKind.IF_EQZ,
            predicate_registers=("v0",),
            branches=(
                Branch(label="true", target_label=":does_not_exist"),
                Branch(label="false", target_label=None),
            ),
        )
        md = _md(
            instructions=("if-eqz v0, :does_not_exist", "return-void"),
            decision_points=(broken_dp,),
            label_index=(),
        )
        dom = slicing._compute_branch_dominance(md)
        assert all(label != "true" for (_, label) in dom.values())

    def test_extract_call_sites_explicit_sort_by_instruction_index(self) -> None:
        """The returned tuple is sorted by ``instruction_index`` — the
        sort is the documented contract even when the natural
        iteration order already matches."""
        md = _md(instructions=(
            "invoke-static {}, Lcom/app/A;->a()V",
            "invoke-static {}, Lcom/app/B;->b()V",
            "invoke-static {}, Lcom/app/C;->c()V",
            "return-void",
        ))
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/A;", "Lcom/app/B;", "Lcom/app/C;")
        sites = slicing.extract_call_sites(md, classes_by_smali=classes)
        indices = [cs.instruction_index for cs in sites]
        assert indices == sorted(indices)

    # -----------------------------------------------------------------
    # Import-path / Q1 lock — 1 test

    def test_callsite_importable_from_trace_types(self) -> None:
        """Q1=(a) — :class:`CallSite` must live in
        :mod:`androscan.analysis.trace_types` (alongside every other
        anchor-payload primitive)."""
        from androscan.analysis import trace_types
        assert trace_types.CallSite is CallSite


# ---------------------------------------------------------------------------
# Phase 13 v3.X-next.3 / DEC-031 — invoke-gap recovery via shared resolver
#
# Tests for the v3.X-next.3 Q5=(a) extension to ``slicing.extract_call_sites``
# (same-module ``target_sig in cls.method_sigs`` presence check + defer to
# resolver on miss) + the new public ``call_graph.lookup_method_ref`` wrapper
# over the existing private ``_lookup_node``.
#
# Five locked implementation-detail Q&As at v3.X-next.3.0 top (per
# DEC-031 v3.X-next.3.0 follow-up note + the TASKS.md v3.X-next.3 row):
#   Q1=(e) combined (a) inherited-from-app-superclass + (b) cross-module via
#          shared resolver path.
#   Q2=(a) new public ``call_graph.lookup_method_ref(decompile_cache_dir,
#          smali_id) -> Optional[MethodRef]``.
#   Q3=(a) per-trace dict-memoization in a closure captured by
#          ``trace_behavior._walk_closure``.
#   Q4=(a) INFO-log resolver stats; no ``BehaviorAnchor`` payload changes.
#   Q5=(a) same-module branch checks ``target_sig in cls.method_sigs``;
#          on miss, defer to resolver (treats inherited-from-app-
#          superclass identically to cross-module).
#
# The pre-v3.X-next.3 cross-module fallback tests (Q3=(c) from v3.X-next.1)
# already live in TestPhase13V3XNext1_MethodInvocations above and stay
# unchanged — recovered CallSites for the cross-module branch flow through
# the same resolver hook + same drop-on-None semantics now used by the
# new inherited-from-app-superclass branch.


def _app_class_with_methods(
    class_desc: str, *method_sigs: str,
) -> smali_parser.ClassDecl:
    """Build a real :class:`ClassDecl` carrying the given
    method-signatures (each in full smali shape
    ``Lcom/Foo;->bar(I)V``) — used by the v3.X-next.3 tests below to
    exercise the new ``target_sig in cls.method_sigs`` presence check.

    Parses each ``method_sig`` into a :class:`MethodDecl` whose own
    ``signature`` property round-trips back to the same string (so the
    slicer's frozenset lookup hits). Other ``MethodDecl`` / ``ClassDecl``
    fields are set to inert defaults — the slicer only inspects
    ``cls.methods[*].signature``, no other field on this path.
    """
    methods: list[smali_parser.MethodDecl] = []
    for sig in method_sigs:
        sep = sig.find(";->")
        if sep < 0:
            raise ValueError(f"malformed test method sig: {sig!r}")
        owner = sig[: sep + 1]
        rest = sig[sep + 3:]
        paren = rest.find("(")
        close = rest.find(")", paren + 1)
        if paren < 0 or close < 0:
            raise ValueError(f"malformed test method sig: {sig!r}")
        methods.append(
            smali_parser.MethodDecl(
                class_desc=owner,
                name=rest[:paren],
                params=rest[paren + 1:close],
                ret=rest[close + 1:],
                flags=(),
                file=f"{owner[1:-1]}.smali",
                line_start=0,
                line_end=0,
            )
        )
    return smali_parser.ClassDecl(
        class_desc=class_desc,
        super_desc=None,
        interfaces=(),
        file=f"{class_desc[1:-1]}.smali",
        methods=tuple(methods),
    )


class TestPhase13V3XNext3_InvokeGapRecovery:
    """Phase 13 v3.X-next.3 (DEC-031) — invoke-gap recovery via shared
    ``call_graph_resolver`` path on ``slicing.extract_call_sites``.

    Covers the Q5=(a) extension that unifies (a) inherited-from-app-
    superclass + (b) cross-module unresolvable under one resolver hook,
    plus the v3.X-next.1 same-module / framework-filter / Q3=(c)
    cross-module-fallback paths that v3.X-next.3 leaves byte-equal-behaviour.
    Tests here focus on the new ``target_sig in cls.method_sigs`` presence
    check; the pre-existing test_resolver_* family in
    TestPhase13V3XNext1_MethodInvocations above already covers the
    cross-module branch.
    """

    # -----------------------------------------------------------------
    # Inherited-from-app-superclass scenarios (Q5=(a) extension) — 5 tests

    def test_inherited_method_defers_to_resolver(self) -> None:
        """Same-module owner BUT ``target_sig`` not in
        ``cls.method_sigs`` (method is inherited from an app-code
        superclass) → resolver is invoked; on hit, the resolver's
        ``MethodRef`` becomes the CallSite's callee. Demonstrates the
        Q5=(a) unification — inherited targets flow through the same
        resolver hook the cross-module branch uses."""
        # MyActivity has only ``onCreate``; ``helper`` is inherited from
        # an app-code superclass (e.g. BaseActivity) and not declared
        # on MyActivity itself. The smali invoke line still references
        # ``LMyActivity;->helper()V`` (some compilers emit the
        # subclass reference rather than the declaring class).
        declaring_ref = MethodRef.from_smali_signature("Lcom/app/BaseActivity;->helper()V")

        resolver_calls: list[str] = []

        def resolver(sig: str) -> MethodRef:
            resolver_calls.append(sig)
            assert sig == "Lcom/app/MyActivity;->helper()V"
            return declaring_ref

        md = _md(instructions=(
            "invoke-virtual {p0}, Lcom/app/MyActivity;->helper()V",
            "return-void",
        ))
        classes = {
            _CALLER_CLASS_SMALI: None,
            "Lcom/app/MyActivity;": _app_class_with_methods(
                "Lcom/app/MyActivity;",
                "Lcom/app/MyActivity;->onCreate()V",
            ),
        }
        sites = slicing.extract_call_sites(
            md, classes_by_smali=classes, call_graph_resolver=resolver,
        )
        assert len(sites) == 1
        assert sites[0].callee is declaring_ref
        # CFG metadata stays byte-equal-shape to a today's-resolved CallSite —
        # local enumerate + dominance computation aren't gated on the
        # resolver path per the v3.X-next.3.0 visual-safety guarantee.
        assert sites[0].instruction_index == 0
        assert sites[0].in_branch_of is None
        assert sites[0].branch_label is None
        assert resolver_calls == ["Lcom/app/MyActivity;->helper()V"]

    def test_inherited_method_resolver_returns_none_drops_callsite(self) -> None:
        """Same-module owner + missing method + resolver returns None
        (target not in call_graph either — graceful degradation) →
        CallSite dropped silently. Matches the Q3=(c) cross-module
        drop-on-None contract."""
        def resolver(sig: str) -> None:
            return None

        md = _md(instructions=(
            "invoke-virtual {p0}, Lcom/app/MyActivity;->helper()V",
            "return-void",
        ))
        classes = {
            _CALLER_CLASS_SMALI: None,
            "Lcom/app/MyActivity;": _app_class_with_methods(
                "Lcom/app/MyActivity;",
                "Lcom/app/MyActivity;->onCreate()V",
            ),
        }
        sites = slicing.extract_call_sites(
            md, classes_by_smali=classes, call_graph_resolver=resolver,
        )
        assert sites == ()

    def test_inherited_method_no_resolver_drops_callsite(self) -> None:
        """Same-module owner + missing method + NO resolver wired →
        CallSite dropped silently. Pre-v3.X-next.3, this case would
        have emitted a phantom CallSite pointing at a non-existent
        body — v3.X-next.3 closes that gap by dropping when no
        recovery hook is available (graceful degradation that matches
        the Q3=(c) cross-module branch's no-resolver behaviour)."""
        md = _md(instructions=(
            "invoke-virtual {p0}, Lcom/app/MyActivity;->helper()V",
            "return-void",
        ))
        classes = {
            _CALLER_CLASS_SMALI: None,
            "Lcom/app/MyActivity;": _app_class_with_methods(
                "Lcom/app/MyActivity;",
                "Lcom/app/MyActivity;->onCreate()V",
            ),
        }
        sites = slicing.extract_call_sites(md, classes_by_smali=classes)
        assert sites == ()

    def test_resolver_not_called_when_method_present_on_class(self) -> None:
        """Same-module owner + method IS in ``cls.method_sigs`` →
        resolver NOT invoked (Q5=(a) lock: the resolver only fires
        when the slicer has POSITIVE evidence the method isn't
        declared on the owner). Preserves byte-equal behaviour with
        the pre-v3.X-next.3 happy path — this is the dominant case in
        production (most invokes ARE same-module-direct, not
        inherited)."""
        resolver_calls: list[str] = []

        def resolver(sig: str) -> MethodRef:
            resolver_calls.append(sig)
            return MethodRef.from_smali_signature(sig)

        md = _md(instructions=(
            "invoke-virtual {p0}, Lcom/app/MyActivity;->onCreate()V",
            "return-void",
        ))
        classes = {
            _CALLER_CLASS_SMALI: None,
            "Lcom/app/MyActivity;": _app_class_with_methods(
                "Lcom/app/MyActivity;",
                "Lcom/app/MyActivity;->onCreate()V",
            ),
        }
        sites = slicing.extract_call_sites(
            md, classes_by_smali=classes, call_graph_resolver=resolver,
        )
        assert len(sites) == 1
        assert sites[0].callee.class_name == "com.app.MyActivity"
        assert sites[0].callee.method_name == "onCreate"
        assert resolver_calls == []

    def test_sentinel_classdecl_preserves_pre_v3xnext3_behaviour(self) -> None:
        """When ``classes_by_smali`` has ``None`` sentinel values (test-
        fixture mode), the v3.X-next.3 presence check falls back to
        "trust the owner check, assume method is present" — preserves
        byte-equal behaviour with the pre-v3.X-next.3 slicer for the
        ~30 existing tests in TestPhase13V3XNext1_MethodInvocations
        above that use ``_app_classes(...)`` sentinel-mode fixtures."""
        resolver_calls: list[str] = []

        def resolver(sig: str) -> MethodRef:
            resolver_calls.append(sig)
            return MethodRef.from_smali_signature(sig)

        md = _md(instructions=(
            "invoke-virtual {p0}, Lcom/app/MyActivity;->anyMethod()V",
            "return-void",
        ))
        # Sentinel mode — ``Lcom/app/MyActivity;`` maps to ``None``,
        # so we can't tell whether ``anyMethod`` is actually on the
        # class. Pre-v3.X-next.3 behaviour: emit CallSite anyway.
        classes = _app_classes(_CALLER_CLASS_SMALI, "Lcom/app/MyActivity;")
        sites = slicing.extract_call_sites(
            md, classes_by_smali=classes, call_graph_resolver=resolver,
        )
        assert len(sites) == 1
        assert sites[0].callee.method_name == "anyMethod"
        assert resolver_calls == [], (
            "resolver must not fire in sentinel mode — the pre-v3.X-next.3 "
            "behaviour is preserved verbatim"
        )

    # -----------------------------------------------------------------
    # Framework-filter still wins over resolver (Q5=(a) outer-gate
    # invariant) — 1 test

    def test_framework_target_resolver_not_called(self) -> None:
        """``FRAMEWORK_CLASS_PREFIXES`` filter at line 1660 stays the
        outer gate — framework targets are dropped BEFORE any
        resolution work runs, so the resolver never sees them. This
        invariant matters because the FE-side ``isFrameworkClass``
        check + the BE-side ``FRAMEWORK_CLASS_PREFIXES`` denylist
        must agree on what's framework noise; if the resolver could
        recover a framework target, the BE would emit edges the FE
        would refuse to render, breaking the v3.X-next.2 emitter's
        denominator contract."""
        resolver_calls: list[str] = []

        def resolver(sig: str) -> MethodRef:
            resolver_calls.append(sig)
            return MethodRef.from_smali_signature(sig)

        md = _md(instructions=(
            "invoke-virtual {p0}, Landroid/app/Activity;->getResources()Landroid/content/res/Resources;",
            "invoke-static {p0}, Ljava/lang/String;->valueOf(I)Ljava/lang/String;",
            "return-void",
        ))
        classes = _app_classes(_CALLER_CLASS_SMALI)
        sites = slicing.extract_call_sites(
            md, classes_by_smali=classes, call_graph_resolver=resolver,
        )
        assert sites == ()
        assert resolver_calls == []

    # -----------------------------------------------------------------
    # Real-class + present-method round-trip (callee carries app-code
    # MethodRef, not the inherited one) — 1 test

    def test_present_method_callee_uses_target_sig_directly(self) -> None:
        """When ``target_sig`` IS in ``cls.method_sigs``, the CallSite's
        callee is parsed straight off the smali invoke line — NOT routed
        through the resolver. Preserves the v3.X-next.1 Q3=(c) hybrid
        contract for the same-module direct path."""
        md = _md(instructions=(
            "invoke-virtual {p0, p1}, Lcom/app/Login;->validate_pin(Ljava/lang/String;)Z",
            "return v0",
        ))
        classes = {
            _CALLER_CLASS_SMALI: None,
            "Lcom/app/Login;": _app_class_with_methods(
                "Lcom/app/Login;",
                "Lcom/app/Login;->validate_pin(Ljava/lang/String;)Z",
                "Lcom/app/Login;->check_input(Ljava/lang/String;)Z",
            ),
        }
        sites = slicing.extract_call_sites(md, classes_by_smali=classes)
        assert len(sites) == 1
        assert sites[0].callee.class_name == "com.app.Login"
        assert sites[0].callee.method_name == "validate_pin"
        assert sites[0].callee.param_descriptors == ("Ljava/lang/String;",)
        assert sites[0].callee.return_descriptor == "Z"


class TestPhase13V3XNext3_LookupMethodRef:
    """Phase 13 v3.X-next.3 (DEC-031) — public
    :func:`call_graph.lookup_method_ref` wrapper over private
    ``_lookup_node``.

    Verifies the Q2=(a) public-API contract: thin wrapper with per-call
    connection; returns ``None`` on miss, missing DB, malformed
    smali_id, or any :class:`sqlite3.Error`. The wrapper is stateless;
    memoization lives in :mod:`androscan.skills.trace_behavior`'s
    ``_walk_closure`` per Q3=(a).
    """

    def _build_minimal_call_graph(
        self,
        tmp_path: Path,
        *node_sigs: str,
    ) -> Path:
        """Build a minimal ``call_graph.sqlite`` under *tmp_path*
        carrying one ``nodes`` row per ``node_sig`` (full smali
        signature shape ``Lcom/Foo;->bar(I)V``). One synthetic
        ``classes`` row + meta init via the module's own
        ``_ensure_schema``. Mirrors what ``_build_node_rows`` would
        produce, just hand-built for the unit test surface."""
        import sqlite3
        from androscan.analysis import call_graph

        cache_dir = tmp_path / ".decompiled" / "deadbeef"
        cache_dir.mkdir(parents=True, exist_ok=True)
        db_path = call_graph.call_graph_db_path(cache_dir)

        conn = sqlite3.connect(str(db_path), isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            call_graph._ensure_schema(conn)
            # One synthetic class row (id=1) — every node FKs to a
            # class_id; we just need any valid one.
            conn.execute(
                "INSERT OR REPLACE INTO classes"
                " (id, smali_class, class_name, package, simple_name)"
                " VALUES (1, 'Lcom/dummy/C;', 'com.dummy.C', 'com.dummy', 'C')"
            )
            for i, sig in enumerate(node_sigs, start=1):
                # Parse owner / name / params / ret off the sig — same
                # shape ``MethodRef.from_smali_signature`` accepts.
                sep = sig.find(";->")
                rest = sig[sep + 3:]
                paren = rest.find("(")
                close = rest.find(")", paren + 1)
                name = rest[:paren]
                params = rest[paren + 1:close]
                ret = rest[close + 1:]
                descriptor = f"({params}){ret}"
                conn.execute(
                    "INSERT OR REPLACE INTO nodes"
                    " (id, smali_id, class_id, method_name, descriptor,"
                    "  return_type, param_types_json)"
                    " VALUES (?, ?, 1, ?, ?, ?, ?)",
                    (i, sig, name, descriptor, ret, "[]"),
                )
        finally:
            conn.close()
        return cache_dir

    def test_lookup_method_ref_hit(self, tmp_path: Path) -> None:
        """Known smali_id in the ``nodes`` table → returns a
        :class:`MethodRef` that round-trips back to the same
        ``smali_signature``."""
        from androscan.analysis import call_graph

        sig = "Lcom/app/Login;->validate_pin(Ljava/lang/String;)Z"
        cache_dir = self._build_minimal_call_graph(tmp_path, sig)

        ref = call_graph.lookup_method_ref(cache_dir, sig)
        assert ref is not None
        assert ref.smali_signature == sig
        assert ref.class_name == "com.app.Login"
        assert ref.method_name == "validate_pin"
        assert ref.param_descriptors == ("Ljava/lang/String;",)
        assert ref.return_descriptor == "Z"

    def test_lookup_method_ref_miss(self, tmp_path: Path) -> None:
        """Smali_id NOT in the ``nodes`` table → ``None`` (caller
        treats this as "drop the CallSite")."""
        from androscan.analysis import call_graph

        cache_dir = self._build_minimal_call_graph(
            tmp_path, "Lcom/app/Login;->validate_pin(Ljava/lang/String;)Z",
        )

        ref = call_graph.lookup_method_ref(
            cache_dir, "Lcom/app/Unknown;->missing()V",
        )
        assert ref is None

    def test_lookup_method_ref_missing_db(self, tmp_path: Path) -> None:
        """No ``call_graph.sqlite`` file under the cache dir → ``None``
        (no exception). The slicer's resolver hook will treat this as
        "drop the CallSite" + the trace_behavior INFO log will record
        a miss."""
        from androscan.analysis import call_graph

        cache_dir = tmp_path / ".decompiled" / "no-such-sha"
        cache_dir.mkdir(parents=True, exist_ok=True)

        ref = call_graph.lookup_method_ref(
            cache_dir, "Lcom/app/Anything;->anything()V",
        )
        assert ref is None

    def test_lookup_method_ref_empty_input(self, tmp_path: Path) -> None:
        """Empty / whitespace smali_id → ``None`` (no DB hit, no
        exception). Defensive guard so a malformed caller doesn't
        propagate an exception up through the slicer resolver hook."""
        from androscan.analysis import call_graph

        cache_dir = self._build_minimal_call_graph(
            tmp_path, "Lcom/app/Login;->validate_pin(Ljava/lang/String;)Z",
        )

        assert call_graph.lookup_method_ref(cache_dir, "") is None
        assert call_graph.lookup_method_ref(cache_dir, "   ") is None

    def test_lookup_method_ref_malformed_smali_id_in_nodes(
        self, tmp_path: Path,
    ) -> None:
        """If ``nodes.smali_id`` were ever malformed (shouldn't happen
        in practice — call_graph builder normalises everything — but
        the parser raises ``ValueError`` and we want None propagation,
        not a stack trace) → ``None``. Defensive contract for the
        slicer's resolver hook."""
        from androscan.analysis import call_graph

        cache_dir = tmp_path / ".decompiled" / "deadbeef"
        cache_dir.mkdir(parents=True, exist_ok=True)
        db_path = call_graph.call_graph_db_path(cache_dir)

        import sqlite3
        conn = sqlite3.connect(str(db_path), isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            call_graph._ensure_schema(conn)
            conn.execute(
                "INSERT OR REPLACE INTO classes"
                " (id, smali_class, class_name, package, simple_name)"
                " VALUES (1, 'Lcom/dummy/C;', 'com.dummy.C', 'com.dummy', 'C')"
            )
            # Deliberately malformed smali_id (missing the ;-> separator).
            conn.execute(
                "INSERT OR REPLACE INTO nodes"
                " (id, smali_id, class_id, method_name, descriptor,"
                "  return_type, param_types_json)"
                " VALUES (1, 'not_a_valid_smali_signature', 1, 'x', '()V', 'V', '[]')",
            )
        finally:
            conn.close()

        ref = call_graph.lookup_method_ref(cache_dir, "not_a_valid_smali_signature")
        assert ref is None

    def test_lookup_method_ref_multiple_hits_via_memoization_friendly_shape(
        self, tmp_path: Path,
    ) -> None:
        """``lookup_method_ref`` is stateless (per Q2=(a) lock —
        memoization lives one layer up in ``trace_behavior``); repeated
        calls open + close their own connection each time. This test
        pins the contract — three back-to-back calls on the same key
        each return an equivalent ``MethodRef`` (frozen dataclass
        equality)."""
        from androscan.analysis import call_graph

        sig = "Lcom/app/Helper;->doStuff(II)V"
        cache_dir = self._build_minimal_call_graph(tmp_path, sig)

        ref1 = call_graph.lookup_method_ref(cache_dir, sig)
        ref2 = call_graph.lookup_method_ref(cache_dir, sig)
        ref3 = call_graph.lookup_method_ref(cache_dir, sig)
        assert ref1 is not None
        assert ref2 is not None
        assert ref3 is not None
        # Frozen dataclass equality (== ) — NOT object identity (`is`),
        # since each call opens a fresh DB connection + builds a fresh
        # MethodRef. The trace_behavior-side cache (Q3=(a)) is what
        # avoids the repeated SQL hits in production.
        assert ref1 == ref2 == ref3
