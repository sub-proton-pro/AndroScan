"""Tests for :mod:`androscan.analysis.decisions` and the platform-neutral
:mod:`androscan.analysis.trace_types` data model — Phase 10 sub-step 10.1.

Run purely against the fixture smali under
``tests/fixtures/trace_smali/`` — no apktool, no SQLite, no LLM. Same
pattern as ``tests/test_call_graph_parser.py``: keep the static layer
honest at this level so 10.2 / 10.3 / 10.5 build on stable ground.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from androscan.analysis import decisions, smali_parser
from androscan.analysis.trace_types import (
    Branch,
    DecisionKind,
    DecisionPoint,
    FieldRef,
    MethodRef,
)


FIXTURES = Path(__file__).parent / "fixtures" / "trace_smali"


def _roots() -> list[Path]:
    return [FIXTURES / "smali", FIXTURES / "smali_classes2"]


def _parse_all() -> tuple[list[decisions.MethodDecisions], decisions.DecisionsParseSummary]:
    """Common harness: pass-1 (classes) feeds pass-3 (decisions)."""
    classes, _ = smali_parser.parse_classes(_roots())
    return decisions.parse_decisions(_roots(), classes)


def _by_method(
    mds: list[decisions.MethodDecisions],
) -> dict[str, decisions.MethodDecisions]:
    return {md.method_signature: md for md in mds}


# ---------------------------------------------------------------------------
# trace_types: MethodRef / FieldRef round-trips


def test_method_ref_round_trips_through_smali_signature() -> None:
    sig = "Lcom/trace/Gates;->openPremium(Z)V"
    ref = MethodRef.from_smali_signature(sig)
    assert ref.class_name == "com.trace.Gates"
    assert ref.method_name == "openPremium"
    assert ref.param_descriptors == ("Z",)
    assert ref.return_descriptor == "V"
    assert ref.smali_signature == sig

    multi = "Lcom/example/Foo;->bar(ILjava/lang/String;[B)Ljava/lang/Object;"
    multi_ref = MethodRef.from_smali_signature(multi)
    assert multi_ref.param_descriptors == ("I", "Ljava/lang/String;", "[B")
    assert multi_ref.return_descriptor == "Ljava/lang/Object;"
    assert multi_ref.smali_signature == multi

    with pytest.raises(ValueError):
        MethodRef.from_smali_signature("not a signature")


def test_field_ref_round_trips_through_smali_signature() -> None:
    sig = "Lcom/trace/Gates;->mIsPremium:Z"
    ref = FieldRef.from_smali_signature(sig)
    assert ref.class_name == "com.trace.Gates"
    assert ref.field_name == "mIsPremium"
    assert ref.type_descriptor == "Z"
    assert ref.smali_signature == sig

    obj = FieldRef.from_smali_signature("Lcom/x/Y;->name:Ljava/lang/String;")
    assert obj.type_descriptor == "Ljava/lang/String;"

    with pytest.raises(ValueError):
        FieldRef.from_smali_signature("Lcom/x/Y;->bad")


# ---------------------------------------------------------------------------
# Pass-3: linear methods are excluded; only methods with branches surface


def test_linear_method_yields_no_decision_record() -> None:
    """``Gates.greet()V`` has no conditional branches — it must not
    appear in the parse output at all (10.3 / 10.5 join against the
    method declaration list when they need full enumeration)."""
    mds, _ = _parse_all()
    sigs = {md.method_signature for md in mds}
    assert "Lcom/trace/Gates;->greet()V" not in sigs
    # Constructor is similarly linear and must not appear.
    assert "Lcom/trace/Gates;-><init>()V" not in sigs


def test_summary_counters_match_emitted_decisions() -> None:
    mds, summary = _parse_all()
    total_decisions = sum(len(md.decision_points) for md in mds)
    assert summary.decisions == total_decisions
    assert summary.methods_with_decisions == len(mds)
    assert summary.smali_files >= 3  # 3 fixture .smali files at minimum
    # At least 2 switches (packed + sparse from SwitchCases.smali);
    # later fixtures may add more (e.g. 10.3's Outcomes.smali contains
    # an additional packed-switch). Lower bound keeps the assertion
    # robust against fixture growth without losing coverage of the
    # switch-counting path itself.
    assert summary.switches >= 2
    # Pass is fail-soft; a clean fixture set must produce zero parse errors.
    assert summary.parse_errors == []


# ---------------------------------------------------------------------------
# All twelve if-* opcodes are recognised with the right kind +
# predicate-register arity


def test_if_eqz_emits_two_branches_with_correct_register_and_target() -> None:
    """The realistic ``checkRoot`` gate exercises the full if-eqz path:
    one register, ``"true"`` branch jumps to ``:cond_safe``, ``"false"``
    branch falls through (target_label=None)."""
    mds = _by_method(_parse_all()[0])
    md = mds["Lcom/trace/Gates;->checkRoot(Z)V"]
    assert len(md.decision_points) == 1
    dp = md.decision_points[0]
    assert dp.kind == DecisionKind.IF_EQZ
    assert dp.predicate_registers == ("p1",)
    assert dp.is_two_register_predicate is False
    assert dp.is_switch is False
    assert dp.branches == (
        Branch(label="true", target_label="cond_safe"),
        Branch(label="false", target_label=None),
    )
    # Most-recent .line directive at parse time was line 10.
    assert dp.source_line == 10
    # Method ref round-trips back to the smali key.
    assert dp.method.smali_signature == md.method_signature


def test_all_six_zero_kinds_covered_in_one_method() -> None:
    """``coverZero`` walks if-nez / if-ltz / if-lez / if-gtz / if-gez
    in declared order; verify every kind shows up exactly once with a
    single-register predicate."""
    md = _by_method(_parse_all()[0])["Lcom/trace/Gates;->coverZero(I)V"]
    kinds = [dp.kind for dp in md.decision_points]
    assert kinds == [
        DecisionKind.IF_NEZ,
        DecisionKind.IF_LTZ,
        DecisionKind.IF_LEZ,
        DecisionKind.IF_GTZ,
        DecisionKind.IF_GEZ,
    ]
    for dp in md.decision_points:
        assert len(dp.predicate_registers) == 1
        assert dp.predicate_registers[0] == "p1"


def test_all_six_two_register_kinds_covered_in_one_method() -> None:
    """``coverTwoReg`` walks if-eq / if-ne / if-lt / if-le / if-gt /
    if-ge in declared order; verify every kind shows up exactly once
    with a two-register predicate (p1, p2)."""
    md = _by_method(_parse_all()[0])["Lcom/trace/Gates;->coverTwoReg(II)V"]
    kinds = [dp.kind for dp in md.decision_points]
    assert kinds == [
        DecisionKind.IF_EQ,
        DecisionKind.IF_NE,
        DecisionKind.IF_LT,
        DecisionKind.IF_LE,
        DecisionKind.IF_GT,
        DecisionKind.IF_GE,
    ]
    for dp in md.decision_points:
        assert dp.predicate_registers == ("p1", "p2")
        assert dp.is_two_register_predicate is True


# ---------------------------------------------------------------------------
# Source-line attachment: the most recent .line directive wins


def test_source_lines_track_most_recent_line_directive() -> None:
    """``openPremium`` has two decisions separated by a .line bump.
    The first decision is preceded by ``.line 40``; the implicit
    fall-through region after that branch is followed by another
    ``.line 41`` before any next branch — which there isn't, since
    there's only one branch in the method. So the assertion is just:
    the single decision's source_line is 40."""
    md = _by_method(_parse_all()[0])["Lcom/trace/Gates;->openPremium(Z)V"]
    assert len(md.decision_points) == 1
    assert md.decision_points[0].kind == DecisionKind.IF_NEZ
    assert md.decision_points[0].source_line == 40


# ---------------------------------------------------------------------------
# Label index: every label appears, points to the *next* instruction's
# index, and is consulted by the switch back-fill


def test_label_index_resolves_branch_targets_to_real_instruction_indices() -> None:
    """``coverZero``'s if-nez has target ``:cond_a``; the label_index
    must record an entry for ``cond_a`` and its index must equal the
    index of the next decision (the if-ltz)."""
    md = _by_method(_parse_all()[0])["Lcom/trace/Gates;->coverZero(I)V"]
    label_dict = dict(md.label_index)
    # All five "cond_*" labels exist.
    assert {"cond_a", "cond_b", "cond_c", "cond_d", "cond_e"} <= label_dict.keys()
    # cond_a points at the if-ltz; the if-nez sits at index 0, so
    # cond_a must point at index 1 (the next instruction).
    assert label_dict["cond_a"] == 1


# ---------------------------------------------------------------------------
# packed-switch: case labels + default fall-through


def test_packed_switch_emits_one_branch_per_case_plus_default() -> None:
    md = _by_method(_parse_all()[0])["Lcom/trace/SwitchCases;->dispatchCode(I)V"]
    switches = [dp for dp in md.decision_points if dp.kind == DecisionKind.PACKED_SWITCH]
    assert len(switches) == 1
    sw = switches[0]
    assert sw.predicate_registers == ("p1",)
    assert sw.is_switch is True
    # The fixture starts at key 0 and lists three case labels.
    assert sw.branches == (
        Branch(label="case 0", target_label="pswitch_0"),
        Branch(label="case 1", target_label="pswitch_1"),
        Branch(label="case 2", target_label="pswitch_2"),
        Branch(label="default", target_label=None),
    )
    # All three case labels are present in the method's label_index so
    # 10.3's basic-block walker can resolve them.
    label_dict = dict(md.label_index)
    assert {"pswitch_0", "pswitch_1", "pswitch_2"} <= label_dict.keys()


# ---------------------------------------------------------------------------
# sparse-switch: explicit keys + default fall-through


def test_sparse_switch_preserves_original_key_strings_in_branch_labels() -> None:
    md = _by_method(_parse_all()[0])["Lcom/trace/SwitchCases;->dispatchHash(I)V"]
    switches = [dp for dp in md.decision_points if dp.kind == DecisionKind.SPARSE_SWITCH]
    assert len(switches) == 1
    sw = switches[0]
    # Keys are kept verbatim from the fixture (operator's mental model
    # is "case 0xa" not "case 10").
    assert sw.branches == (
        Branch(label="case 0x1", target_label="sswitch_0"),
        Branch(label="case 0x5", target_label="sswitch_1"),
        Branch(label="case 0xa", target_label="sswitch_2"),
        Branch(label="default", target_label=None),
    )


# ---------------------------------------------------------------------------
# Multi-dex: secondary dex roots are walked


def test_multi_dex_decisions_are_extracted() -> None:
    """``HiddenGate.isJailbroken`` lives under ``smali_classes2/``;
    its single if-eqz must appear in the parse output with the right
    src_file path so the secondary dex coverage is visible."""
    md = _by_method(_parse_all()[0])["Lcom/trace/HiddenGate;->isJailbroken()Z"]
    assert md.src_file.startswith("smali_classes2")
    assert len(md.decision_points) == 1
    assert md.decision_points[0].kind == DecisionKind.IF_EQZ


# ---------------------------------------------------------------------------
# Wire-format: DecisionPoint round-trips through asdict + JSON


def test_decision_point_serialises_to_json_without_custom_encoder() -> None:
    """The 10.5 trace.sqlite cache and the 10.6 wire format both rely
    on dataclasses.asdict + json.dumps round-tripping cleanly. Any
    field with a non-JSON-serialisable type (e.g. a non-str enum, a
    set, a path) breaks both at once — assert the contract here."""
    md = _by_method(_parse_all()[0])["Lcom/trace/Gates;->checkRoot(Z)V"]
    dp = md.decision_points[0]
    payload = json.dumps(dataclasses.asdict(dp), default=str)
    decoded = json.loads(payload)
    # Enum survives as its string value (DecisionKind subclasses str).
    assert decoded["kind"] == "if_eqz"
    assert decoded["predicate_registers"] == ["p1"]
    assert decoded["branches"][0]["label"] == "true"
    assert decoded["branches"][1]["target_label"] is None
    assert decoded["method"]["class_name"] == "com.trace.Gates"
