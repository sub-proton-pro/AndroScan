"""Tests for :mod:`androscan.analysis.bypass_planner` and the
:class:`BypassPlan` extensions in
:mod:`androscan.analysis.trace_types` — Phase 10 sub-step 10.4.

Run end-to-end through the static layer
(``parse_classes → parse_decisions → slice_predicate_origins →
classify_branch_outcomes → plan_bypasses``) so each test is anchored
to real Smali rather than synthesized data — catching contract drifts
between layers that pure-unit tests would miss.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from androscan.adapters import frida_hooks
from androscan.analysis import (
    branch_classifier,
    bypass_planner,
    decisions,
    slicing,
    smali_parser,
)
from androscan.analysis.bypass_planner import (
    DEFAULT_RISK_THRESHOLD,
    VALID_RISKS,
    partition_by_risk,
    plan_bypasses,
    risk_at_or_below,
)
from androscan.analysis.trace_types import (
    BranchOutcome,
    BranchVerdict,
    BypassPlan,
    DecisionKind,
    DecisionPoint,
    MethodCallOrigin,
    MethodRef,
)


FIXTURES = Path(__file__).parent / "fixtures" / "trace_smali"


# ---------------------------------------------------------------------------
# Pipeline harness — pulls the enriched + classified MethodDecisions for any
# fixture method by smali signature.


def _pipeline() -> dict[str, decisions.MethodDecisions]:
    roots = [FIXTURES / "smali", FIXTURES / "smali_classes2"]
    classes, _ = smali_parser.parse_classes(roots)
    mds, _ = decisions.parse_decisions(roots, classes)
    out: dict[str, decisions.MethodDecisions] = {}
    for md in mds:
        sliced = slicing.slice_predicate_origins(md)
        classified = branch_classifier.classify_branch_outcomes(sliced)
        out[md.method_signature] = classified
    return out


def _plans_for(sig: str) -> tuple[BypassPlan, ...]:
    """Run the planner against the (single) decision in fixture method ``sig``."""
    md = _pipeline()[sig]
    assert len(md.decision_points) == 1, (
        f"fixture {sig} expected one decision, got {len(md.decision_points)}"
    )
    dp = md.decision_points[0]
    return plan_bypasses(dp, md.instructions, dict(md.label_index))


def _plan_by_template(plans: tuple[BypassPlan, ...], template_id: str) -> BypassPlan:
    matches = [p for p in plans if p.template_id == template_id]
    assert len(matches) == 1, (
        f"expected exactly one {template_id!r} plan, got {len(matches)}: "
        f"{[p.template_id for p in plans]}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# Plan A — force_return_value on the predicate's source method


class TestPlanA_ForceReturnValue:
    """Layer A bypass — flip the predicate value at its source."""

    def test_bool_predicate_emits_force_return_true(self) -> None:
        """``isPremium()Z`` predicate, allow=false (fall-through to setResult).
        if-eqz means the false branch fires when v0 is non-zero — so the
        operator wants ``isPremium()`` to return non-zero → ``"true"``.
        """
        plans = _plans_for("Lcom/trace/Plans;->gateBoolPredicate()V")
        plan = _plan_by_template(plans, "force_return_value")
        assert plan.params["return_value_expr"] == "true"
        assert plan.params["class_name"] == "com.trace.Plans"
        assert plan.params["method_name"] == "isPremium"
        assert plan.risk == "low"
        assert plan.target_method is not None
        assert plan.target_method.smali_signature == "Lcom/trace/Plans;->isPremium()Z"
        # Back-reference to the gate that triggered the plan.
        assert plan.source_decision_method is not None
        assert plan.source_decision_method.method_name == "gateBoolPredicate"
        # Rendered hook references the chosen literal — proves planner
        # output round-trips through the Frida template renderer.
        rendered = frida_hooks.render_by_id(plan.template_id, plan.params)
        assert "isPremium" in rendered.js
        assert "(true)" in rendered.js or "= (true)" in rendered.js or "var forced = (true)" in rendered.js

    def test_int_predicate_emits_force_return_one(self) -> None:
        """``getCheckCode()I`` predicate, allow=true (cond_allow). if-nez
        true branch fires when v0 is non-zero — so the operator wants
        ``getCheckCode()`` to return non-zero → ``"1"``.
        """
        plans = _plans_for("Lcom/trace/Plans;->gateIntPredicate()V")
        plan = _plan_by_template(plans, "force_return_value")
        assert plan.params["return_value_expr"] == "1"
        assert plan.params["method_name"] == "getCheckCode"
        assert plan.risk == "low"

    def test_ref_predicate_zero_side_emits_force_return_null(self) -> None:
        """``getDenyToken()Ljava/lang/String;`` predicate, allow=true (the
        null side per ``if-eqz``). For reference types, the zero literal
        is ``"null"``."""
        plans = _plans_for("Lcom/trace/Plans;->gateRefAllowNull()V")
        plan = _plan_by_template(plans, "force_return_value")
        assert plan.params["return_value_expr"] == "null"
        assert plan.params["method_name"] == "getDenyToken"
        assert plan.risk == "low"

    def test_ref_predicate_non_null_side_skips_plan_a(self) -> None:
        """Allow side wants non-null reference — planner can't synthesise
        an instance, so Plan A is honestly skipped. Plan B still fires."""
        plans = _plans_for("Lcom/trace/Plans;->gateRefAllowNonNull()V")
        template_ids = [p.template_id for p in plans]
        assert "force_return_value" not in template_ids
        # Plan B still fires (gate is void).
        assert "force_method_skip" in template_ids

    def test_const_origin_skips_plan_a(self) -> None:
        plans = _plans_for("Lcom/trace/Plans;->gateConstPredicate()V")
        assert "force_return_value" not in [p.template_id for p in plans]

    def test_field_origin_skips_plan_a(self) -> None:
        plans = _plans_for("Lcom/trace/Plans;->gateFieldPredicate()V")
        assert "force_return_value" not in [p.template_id for p in plans]

    def test_param_origin_skips_plan_a(self) -> None:
        # ``denyAllowSplit`` from the 10.3 Outcomes fixture: predicate is
        # the method parameter ``p1`` (ParamOrigin), gate is void, clean
        # DENY/ALLOW split. Plan A skipped (not MethodCall), Plan B fires.
        plans = _plans_for("Lcom/trace/Outcomes;->denyAllowSplit(Z)V")
        assert "force_return_value" not in [p.template_id for p in plans]
        assert "force_method_skip" in [p.template_id for p in plans]

    def test_composite_origin_skips_plan_a(self) -> None:
        plans = _plans_for("Lcom/trace/Plans;->gateCompositePredicate(II)V")
        assert "force_return_value" not in [p.template_id for p in plans]


# ---------------------------------------------------------------------------
# Plan B — force_method_skip on a void gate method


class TestPlanB_ForceMethodSkip:
    """Layer B bypass — short-circuit the gate method itself."""

    def test_void_gate_emits_force_method_skip(self) -> None:
        plans = _plans_for("Lcom/trace/Plans;->gateBoolPredicate()V")
        plan = _plan_by_template(plans, "force_method_skip")
        assert plan.params["method_name"] == "gateBoolPredicate"
        assert plan.params["return_descriptor"] == "V"
        assert plan.risk == "medium"
        # Target == source — the gate method itself is what gets stubbed.
        assert plan.target_method is not None
        assert plan.target_method == plan.source_decision_method
        rendered = frida_hooks.render_by_id(plan.template_id, plan.params)
        assert "gateBoolPredicate" in rendered.js
        assert '"V"' in rendered.js or 'descriptor: V' in rendered.js

    def test_non_void_gate_skips_plan_b(self) -> None:
        """Gate method returns ``Z`` (boolean) — Plan B (which is
        void-only by design) does not fire. Plan A still fires."""
        plans = _plans_for("Lcom/trace/Plans;->gateNonVoidReturn()Z")
        template_ids = [p.template_id for p in plans]
        assert "force_method_skip" not in template_ids
        assert "force_return_value" in template_ids


# ---------------------------------------------------------------------------
# Plan C — force_string_compare_equal


class TestPlanC_ForceStringCompareEqual:
    """Literal-gated app-wide ``String.equals`` interception."""

    def test_string_equals_with_literal_emits_plan_c(self) -> None:
        plans = _plans_for("Lcom/trace/Plans;->gateStringEqualsWithLiteral(Ljava/lang/String;)V")
        plan = _plan_by_template(plans, "force_string_compare_equal")
        assert plan.params["target_literal"] == "LICENSE_VALID_42"
        assert plan.risk == "medium"
        # Plan C's target is the synthetic ``String.equals(Object)`` ref
        # (the hook is on String.equals app-wide).
        assert plan.target_method is not None
        assert plan.target_method.smali_signature == "Ljava/lang/String;->equals(Ljava/lang/Object;)Z"
        # Source back-reference points at the gate method.
        assert plan.source_decision_method is not None
        assert plan.source_decision_method.method_name == "gateStringEqualsWithLiteral"
        rendered = frida_hooks.render_by_id(plan.template_id, plan.params)
        assert "LICENSE_VALID_42" in rendered.js

    def test_string_equals_routes_away_from_plan_a(self) -> None:
        """Plan A would emit a blanket force-true on every String.equals
        call (catastrophic — the JVM uses it internally for class
        loading / hash lookups). The planner must route String.equals
        cases to Plan C only."""
        plans = _plans_for("Lcom/trace/Plans;->gateStringEqualsWithLiteral(Ljava/lang/String;)V")
        template_ids = [p.template_id for p in plans]
        assert "force_return_value" not in template_ids

    def test_string_equals_without_literal_skips_plan_c(self) -> None:
        """Both arguments come from method params — no const-string
        anywhere in the method body to fill ``target_literal``. Plan C
        honestly skipped; Plan B still fires (void gate)."""
        plans = _plans_for("Lcom/trace/Plans;->gateStringEqualsNoLiteral(Ljava/lang/String;Ljava/lang/String;)V")
        template_ids = [p.template_id for p in plans]
        assert "force_string_compare_equal" not in template_ids
        assert "force_method_skip" in template_ids


# ---------------------------------------------------------------------------
# Skip / fail-soft cases


class TestPlannerSkips:
    def test_no_outcome_returns_empty(self) -> None:
        """Pre-classify decision (branch_outcome=None) yields no plans —
        the planner refuses to guess without classifier signal."""
        # Fresh parse without running the classifier.
        roots = [FIXTURES / "smali"]
        classes, _ = smali_parser.parse_classes(roots)
        mds, _ = decisions.parse_decisions(roots, classes)
        md = next(m for m in mds if m.method_signature == "Lcom/trace/Plans;->gateBoolPredicate()V")
        sliced = slicing.slice_predicate_origins(md)
        # NOTE: classify NOT called here — branch_outcome stays None.
        dp = sliced.decision_points[0]
        assert dp.branch_outcome is None
        plans = plan_bypasses(dp, sliced.instructions, dict(sliced.label_index))
        assert plans == ()

    def test_zero_confidence_outcome_returns_empty(self) -> None:
        """Outcomes.smali::neutralWhenSymmetric has confidence == 0.0
        (no signals). Plan emission must skip honestly."""
        plans = _plans_for("Lcom/trace/Outcomes;->neutralWhenSymmetric(Z)V")
        assert plans == ()

    def test_both_branches_deny_returns_empty(self) -> None:
        """No flip target — both branches deny → no plans."""
        plans = _plans_for("Lcom/trace/Plans;->gateBothBranchesDeny(Z)V")
        assert plans == ()

    def test_switch_decision_returns_empty(self) -> None:
        """Outcomes.smali::switchOutcomes is a packed-switch — v1 punts."""
        plans = _plans_for("Lcom/trace/Outcomes;->switchOutcomes(I)V")
        assert plans == ()

    def test_string_equals_without_instructions_skips_plan_c(self) -> None:
        """Plan C requires the raw instruction stream to scan for
        ``const-string``. Calling ``plan_bypasses`` without the
        instructions+label_index args (the ergonomic shape for callers
        that only want Plans A + B) yields A + B and skips C."""
        md = _pipeline()["Lcom/trace/Plans;->gateStringEqualsWithLiteral(Ljava/lang/String;)V"]
        dp = md.decision_points[0]
        plans = plan_bypasses(dp)  # no instructions / label_index
        template_ids = [p.template_id for p in plans]
        assert "force_string_compare_equal" not in template_ids


# ---------------------------------------------------------------------------
# Risk taxonomy + threshold filtering


class TestRiskTaxonomy:
    def test_valid_risks_locked_order(self) -> None:
        assert VALID_RISKS == ("low", "medium", "high")

    def test_default_threshold(self) -> None:
        assert DEFAULT_RISK_THRESHOLD == "medium"

    def test_risk_at_or_below_lt_inclusive(self) -> None:
        assert risk_at_or_below("low", "medium") is True
        assert risk_at_or_below("medium", "medium") is True
        assert risk_at_or_below("high", "medium") is False
        assert risk_at_or_below("low", "high") is True

    def test_risk_at_or_below_unknown_falls_to_medium(self) -> None:
        # Unknown values on either side coerce to ``"medium"`` — fail-soft.
        assert risk_at_or_below("nonsense", "low") is False  # medium > low
        assert risk_at_or_below("low", "nonsense") is True   # low <= medium

    def test_partition_by_threshold_medium(self) -> None:
        """Default threshold ``medium`` keeps low + medium plans in the
        default tuple; high plans go to advanced."""
        plans = (
            BypassPlan(template_id="force_return_value", params={}, rationale="", risk="low"),
            BypassPlan(template_id="force_method_skip", params={}, rationale="", risk="medium"),
            BypassPlan(template_id="force_string_compare_equal", params={}, rationale="", risk="high"),
        )
        default, advanced = partition_by_risk(plans, "medium")
        assert [p.risk for p in default] == ["low", "medium"]
        assert [p.risk for p in advanced] == ["high"]

    def test_partition_by_threshold_low_pushes_medium_to_advanced(self) -> None:
        plans = (
            BypassPlan(template_id="force_return_value", params={}, rationale="", risk="low"),
            BypassPlan(template_id="force_method_skip", params={}, rationale="", risk="medium"),
        )
        default, advanced = partition_by_risk(plans, "low")
        assert [p.risk for p in default] == ["low"]
        assert [p.risk for p in advanced] == ["medium"]

    def test_partition_by_threshold_high_keeps_everything(self) -> None:
        plans = (
            BypassPlan(template_id="force_return_value", params={}, rationale="", risk="low"),
            BypassPlan(template_id="force_method_skip", params={}, rationale="", risk="medium"),
            BypassPlan(template_id="force_string_compare_equal", params={}, rationale="", risk="high"),
        )
        default, advanced = partition_by_risk(plans, "high")
        assert len(default) == 3
        assert advanced == ()


# ---------------------------------------------------------------------------
# Integration: the bool fixture's plans render cleanly through the Frida
# template registry. Closes the loop on "planner output is consumable
# by the renderer without further massaging".


def test_planner_output_renders_through_frida_registry() -> None:
    plans = _plans_for("Lcom/trace/Plans;->gateBoolPredicate()V")
    assert len(plans) == 2  # Plan A + Plan B
    for plan in plans:
        rendered = frida_hooks.render_by_id(plan.template_id, plan.params)
        assert rendered.js.strip()
        assert rendered.summary.strip()
        # The rendered JS references the gate method's class name (Plan B)
        # or the predicate's source class (Plan A) — both happen to be
        # the same fixture class, so a substring assertion is sufficient.
        assert "com.trace.Plans" in rendered.js


# ---------------------------------------------------------------------------
# Config loader integration: the new ``trace_bypass_risk_max`` knob is
# wired through Config + default + LIVE_RELOADABLE_FIELDS.


class TestConfigKnob:
    def test_default_is_medium(self) -> None:
        from androscan.config.loader import Config
        assert Config.default().trace_bypass_risk_max == "medium"

    def test_field_is_live_reloadable(self) -> None:
        from androscan.config.loader import LIVE_RELOADABLE_FIELDS
        assert "trace_bypass_risk_max" in LIVE_RELOADABLE_FIELDS

    def test_field_is_in_field_map(self) -> None:
        from androscan.config.loader import CONFIG_FIELD_MAP
        section, key, env = CONFIG_FIELD_MAP["trace_bypass_risk_max"]
        assert section == "trace"
        assert key == "bypass_risk_max"
        assert env == "ANDROSCAN_TRACE_BYPASS_RISK_MAX"
