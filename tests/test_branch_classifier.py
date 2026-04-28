"""Tests for :mod:`androscan.analysis.branch_classifier` and the
:class:`BranchOutcome` extensions in
:mod:`androscan.analysis.trace_types` — Phase 10 sub-step 10.3.

Run purely against the fixture smali under
``tests/fixtures/trace_smali/`` (Outcomes.smali in particular) and
the existing 10.1 / 10.2 fixtures — no apktool, no SQLite, no LLM.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from androscan.analysis import branch_classifier, decisions, smali_parser
from androscan.analysis.branch_classifier import (
    LLM_RECLASSIFY_THRESHOLD,
    classify_branch_outcomes,
)
from androscan.analysis.trace_types import BranchOutcome, BranchVerdict


FIXTURES = Path(__file__).parent / "fixtures" / "trace_smali"


def _roots() -> list[Path]:
    return [FIXTURES / "smali", FIXTURES / "smali_classes2"]


def _classified() -> dict[str, decisions.MethodDecisions]:
    """Common harness: pass-1 (classes) → pass-3 (decisions) → 10.3 classifier.

    Returns a dict keyed by smali method signature so each test can
    pull one method without re-parsing.
    """
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes)
    return {md.method_signature: classify_branch_outcomes(md) for md in mds}


def _outcome(sig: str) -> BranchOutcome:
    """Helper: pull the (single) decision's outcome from a fixture method."""
    md = _classified()[sig]
    assert len(md.decision_points) == 1, (
        f"expected one decision in {sig}, got {len(md.decision_points)}"
    )
    outcome = md.decision_points[0].branch_outcome
    assert outcome is not None
    return outcome


def _verdict_for(outcome: BranchOutcome, label: str) -> BranchVerdict:
    """Pull the BranchVerdict whose ``branch_label`` matches ``label``."""
    for v in outcome.verdicts:
        if v.branch_label == label:
            return v
    raise AssertionError(f"no verdict for branch label {label!r} in {outcome.verdicts!r}")


# ---------------------------------------------------------------------------
# Data-model wiring


def test_branch_outcome_defaults_to_none_pre_classification() -> None:
    """Pre-classification DecisionPoints (10.1's output, before 10.3
    runs) carry ``branch_outcome=None`` so the post-classify value is
    unambiguous."""
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes)
    for md in mds:
        for dp in md.decision_points:
            assert dp.branch_outcome is None


def test_classify_does_not_mutate_input() -> None:
    """``classify_branch_outcomes`` must return a fresh
    :class:`MethodDecisions` (frozen dataclass replace) — the input's
    decision points stay None-valued."""
    classes, _ = smali_parser.parse_classes(_roots())
    mds, _ = decisions.parse_decisions(_roots(), classes)
    md_in = next(m for m in mds if m.method_signature == "Lcom/trace/Outcomes;->denyByThrow(Z)V")
    md_out = classify_branch_outcomes(md_in)
    assert md_out is not md_in
    assert md_in.decision_points[0].branch_outcome is None
    assert md_out.decision_points[0].branch_outcome is not None


# ---------------------------------------------------------------------------
# Strong DENY signals (score = -1.0, confidence = 1.0)


def test_throw_yields_strong_deny() -> None:
    outcome = _outcome("Lcom/trace/Outcomes;->denyByThrow(Z)V")
    false_branch = _verdict_for(outcome, "false")
    assert false_branch.verdict == "deny"
    assert false_branch.score == -1.0
    assert any("throw" in r for r in false_branch.reasons)
    assert outcome.confidence == 1.0
    # The cond_safe (true) branch is just `return-void` — no signal.
    assert _verdict_for(outcome, "true").verdict == "neutral"


def test_system_exit_yields_strong_deny() -> None:
    outcome = _outcome("Lcom/trace/Outcomes;->denyBySystemExit(Z)V")
    false_branch = _verdict_for(outcome, "false")
    assert false_branch.verdict == "deny"
    assert false_branch.score == -1.0
    assert any("System.exit" in r for r in false_branch.reasons)
    assert outcome.confidence == 1.0


def test_kill_process_yields_strong_deny() -> None:
    outcome = _outcome("Lcom/trace/Outcomes;->denyByKillProcess(Z)V")
    false_branch = _verdict_for(outcome, "false")
    assert false_branch.verdict == "deny"
    assert false_branch.score == -1.0
    assert any("Process.killProcess" in r for r in false_branch.reasons)
    assert outcome.confidence == 1.0


# ---------------------------------------------------------------------------
# Moderate DENY: finish() without preceding setResult


def test_finish_without_setresult_yields_moderate_deny() -> None:
    outcome = _outcome("Lcom/trace/Outcomes;->denyByFinish(Z)V")
    false_branch = _verdict_for(outcome, "false")
    assert false_branch.verdict == "deny"
    assert false_branch.score == pytest.approx(-0.7)
    assert any("finish without setResult" in r for r in false_branch.reasons)
    # Moderate signal → confidence 0.85 (above 0.6 threshold; no LLM re-classify).
    assert outcome.confidence == 0.85
    assert outcome.confidence > LLM_RECLASSIFY_THRESHOLD


def test_setresult_before_finish_suppresses_finish_signal() -> None:
    """When ``setResult`` precedes ``finish`` in the same basic block,
    the finish-deny signal must be suppressed — the operator's "report
    a result then close" pattern is a legitimate allow flow, not a
    gate."""
    outcome = _outcome("Lcom/trace/Outcomes;->setResultBeforeFinish(Z)V")
    false_branch = _verdict_for(outcome, "false")
    # Net score = +1.0 (setResult) with finish suppressed — pure ALLOW.
    assert false_branch.verdict == "allow"
    assert false_branch.score == 1.0
    assert any("setResult" in r for r in false_branch.reasons)
    # No "finish without setResult" reason should fire.
    assert not any("finish without setResult" in r for r in false_branch.reasons)


# ---------------------------------------------------------------------------
# ALLOW signals


def test_setresult_alone_yields_strong_allow() -> None:
    outcome = _outcome("Lcom/trace/Outcomes;->allowBySetResult(Z)V")
    false_branch = _verdict_for(outcome, "false")
    assert false_branch.verdict == "allow"
    assert false_branch.score == 1.0
    assert any("setResult" in r for r in false_branch.reasons)
    assert outcome.confidence == 1.0


def test_startactivity_yields_moderate_allow() -> None:
    outcome = _outcome("Lcom/trace/Outcomes;->allowByStartActivity(Z)V")
    false_branch = _verdict_for(outcome, "false")
    assert false_branch.verdict == "allow"
    assert false_branch.score == pytest.approx(0.7)
    assert any("startActivity" in r for r in false_branch.reasons)
    assert outcome.confidence == 0.85


# ---------------------------------------------------------------------------
# String-keyword DENY signals (case-insensitive, suffix-aware)


def test_string_keyword_rooted_yields_moderate_deny() -> None:
    """``"Device is rooted"`` matches ``\\broot(?:ed|ing)?\\b`` — the
    suffix-aware extension of DEC-024's spec is what makes the
    canonical "rooted" detection string trigger."""
    outcome = _outcome("Lcom/trace/Outcomes;->denyByRootedString(Z)V")
    false_branch = _verdict_for(outcome, "false")
    assert false_branch.verdict == "deny"
    assert false_branch.score == pytest.approx(-0.7)
    assert any("rooted" in r.lower() for r in false_branch.reasons)


def test_string_keyword_case_insensitive_premium_match() -> None:
    """``"PREMIUM ONLY"`` (uppercase) matches ``premium`` — verifies
    ``re.IGNORECASE`` is wired."""
    outcome = _outcome("Lcom/trace/Outcomes;->denyByPremiumString(Z)V")
    false_branch = _verdict_for(outcome, "false")
    assert false_branch.verdict == "deny"
    assert false_branch.score == pytest.approx(-0.7)
    assert any("premium" in r.lower() for r in false_branch.reasons)


def test_string_keyword_british_unauthorised_match() -> None:
    """``"unauthorised"`` (British spelling, ``s`` not ``z``) matches
    ``unauthori[sz]ed`` — verifies the alternation accepts both."""
    outcome = _outcome("Lcom/trace/Outcomes;->denyByUnauthorizedString(Z)V")
    false_branch = _verdict_for(outcome, "false")
    assert false_branch.verdict == "deny"
    assert false_branch.score == pytest.approx(-0.7)
    assert any("unauthori" in r.lower() for r in false_branch.reasons)


def test_word_boundary_excludes_rootview_substring() -> None:
    """``"rootView focused"`` must NOT match ``root`` because there's
    no word boundary between ``root`` and ``View``. This is the
    word-boundary contract that prevents the deny-keyword regex from
    flagging legitimate UI code."""
    outcome = _outcome("Lcom/trace/Outcomes;->wordBoundaryExcludesRootView(Z)V")
    false_branch = _verdict_for(outcome, "false")
    assert false_branch.verdict == "neutral"
    assert false_branch.score == 0.0
    # No reasons fired since no signal triggered.
    assert false_branch.reasons == ()


# ---------------------------------------------------------------------------
# Cross-branch length ratio (weak DENY only when otherwise neutral)


def test_length_ratio_yields_weak_deny_on_short_side() -> None:
    """Short branch (1 instr) vs long branch (8 instr) → ratio ≥ 3:1
    fires the weak-deny heuristic on the short side. Confidence is
    *below* the LLM-reclassify threshold so 10.5 will see this gate."""
    outcome = _outcome("Lcom/trace/Outcomes;->lengthRatioGate(Z)V")
    # The cond_short (true) branch is the 1-instruction side.
    short_branch = _verdict_for(outcome, "true")
    assert short_branch.verdict == "neutral"
    assert short_branch.score == pytest.approx(-0.3)
    assert any("length ratio" in r for r in short_branch.reasons)
    # The long fall-through (false) branch stays neutral with no signal.
    long_branch = _verdict_for(outcome, "false")
    assert long_branch.verdict == "neutral"
    assert long_branch.score == 0.0
    # Weak signal only → confidence 0.45 → below threshold → LLM re-classifies.
    assert outcome.confidence == 0.45
    assert outcome.confidence < LLM_RECLASSIFY_THRESHOLD
    # Method-level reason surfaces the cross-branch ratio.
    assert any("length ratio" in r for r in outcome.reasons)


# ---------------------------------------------------------------------------
# Neutral — no signals fire


def test_neutral_when_both_branches_signal_free() -> None:
    """Symmetric branches with no keywords / no security calls produce
    NEUTRAL/NEUTRAL with confidence 0.0 — 10.5 will invoke the LLM
    here since heuristics had nothing to say."""
    outcome = _outcome("Lcom/trace/Outcomes;->neutralWhenSymmetric(Z)V")
    for verdict in outcome.verdicts:
        assert verdict.verdict == "neutral"
        assert verdict.score == 0.0
        assert verdict.reasons == ()
    assert outcome.confidence == 0.0
    assert outcome.confidence < LLM_RECLASSIFY_THRESHOLD


# ---------------------------------------------------------------------------
# Switch — per-case verdicts independently classified


def test_switch_classifies_each_case_independently() -> None:
    """Packed-switch with throw / setResult / neutral cases + neutral
    default — each branch gets its own verdict; max-signal confidence
    stays high because case 0's throw is a strong signal."""
    md = _classified()["Lcom/trace/Outcomes;->switchOutcomes(I)V"]
    dp = md.decision_points[0]
    outcome = dp.branch_outcome
    assert outcome is not None
    assert _verdict_for(outcome, "case 0").verdict == "deny"
    assert _verdict_for(outcome, "case 0").score == -1.0
    assert _verdict_for(outcome, "case 1").verdict == "allow"
    assert _verdict_for(outcome, "case 1").score == 1.0
    assert _verdict_for(outcome, "case 2").verdict == "neutral"
    assert _verdict_for(outcome, "default").verdict == "neutral"
    # Confidence reflects max signal across all branches → strong.
    assert outcome.confidence == 1.0


# ---------------------------------------------------------------------------
# Clean two-branch DENY/ALLOW split


def test_two_branches_split_into_deny_and_allow() -> None:
    """``denyAllowSplit`` is the canonical pentest gate: throw on one
    side, setResult on the other. Each branch gets its own verdict and
    confidence is high (both branches have strong signals)."""
    outcome = _outcome("Lcom/trace/Outcomes;->denyAllowSplit(Z)V")
    # if-eqz p1, :cond_allow → true branch goes to cond_allow (setResult).
    assert _verdict_for(outcome, "true").verdict == "allow"
    assert _verdict_for(outcome, "true").score == 1.0
    # False branch falls through into the throw.
    assert _verdict_for(outcome, "false").verdict == "deny"
    assert _verdict_for(outcome, "false").score == -1.0
    assert outcome.confidence == 1.0


# ---------------------------------------------------------------------------
# Confidence tier contract


def test_confidence_tiers_pin_to_documented_values() -> None:
    """Pin the four confidence tiers (1.0 / 0.85 / 0.45 / 0.0) by
    asserting one fixture method per tier. 10.5 / 10.7 read these
    tiers as a stable contract — the exact values cannot drift
    silently."""
    sliced = _classified()
    # Tier 1: strong (throw → 1.0).
    assert sliced["Lcom/trace/Outcomes;->denyByThrow(Z)V"].decision_points[0].branch_outcome.confidence == 1.0
    # Tier 2: moderate (string keyword → 0.85).
    assert sliced["Lcom/trace/Outcomes;->denyByRootedString(Z)V"].decision_points[0].branch_outcome.confidence == 0.85
    # Tier 3: weak (length ratio only → 0.45 — below LLM threshold).
    assert sliced["Lcom/trace/Outcomes;->lengthRatioGate(Z)V"].decision_points[0].branch_outcome.confidence == 0.45
    # Tier 4: nothing (symmetric branches, no signals → 0.0).
    assert sliced["Lcom/trace/Outcomes;->neutralWhenSymmetric(Z)V"].decision_points[0].branch_outcome.confidence == 0.0


# ---------------------------------------------------------------------------
# Wire-format: BranchOutcome round-trips through asdict + JSON


def test_branch_outcome_round_trips_through_json() -> None:
    """The 10.5 ``trace.sqlite`` cache + 10.6 wire format both rely on
    ``dataclasses.asdict`` + ``json.dumps`` round-tripping cleanly for
    the full enriched DecisionPoint payload (branches + verdicts +
    confidence + reasons)."""
    outcome = _outcome("Lcom/trace/Outcomes;->denyAllowSplit(Z)V")
    payload = json.dumps(dataclasses.asdict(outcome), default=str)
    decoded = json.loads(payload)
    assert decoded["confidence"] == 1.0
    assert len(decoded["verdicts"]) == 2
    labels_to_verdicts = {v["branch_label"]: v["verdict"] for v in decoded["verdicts"]}
    assert labels_to_verdicts == {"true": "allow", "false": "deny"}
