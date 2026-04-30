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
    CompositeOrigin,
    ConstOrigin,
    DecisionPoint,
    FieldReadOrigin,
    MethodCallOrigin,
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
