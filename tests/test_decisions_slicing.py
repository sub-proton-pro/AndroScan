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
