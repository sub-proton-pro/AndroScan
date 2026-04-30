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
    Branch,
    BypassPlan,
    DecisionKind,
    DecisionPoint,
    FieldReadOrigin,
    FieldRef,
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


# ---------------------------------------------------------------------------
# Phase 11 sub-step 11.6 / DEC-025 — bypass-planner behaviour against v2
# PredicateOrigin terminals (those carrying ``descent_depth >= 1`` from
# the bounded inter-procedural slicer added in 11.4 + 11.5). The planner
# itself doesn't change in 11.6 — these tests confirm that:
#
#   * Plan A correctly targets the *deeper* method when the v2 slicer
#     descends through stateless helpers and surfaces a ``MethodCallOrigin``
#     at depth N (the depth-cap case from 11.4's ``gateThreeHopChainCapped``
#     fixture).
#   * Plan A correctly targets the *original* method when descent is
#     blocked (deny-list / stateful callee / cycle / external) — the v2
#     terminal is identity-equal to the v1 terminal modulo
#     ``descent_depth=0``.
#   * The new ``descent_depth`` field is metadata-only — it doesn't
#     change template selection, risk, or any other planner output.
#   * v1 callers (no descent kwargs to the slicer) and v2 callers
#     (with descent kwargs) produce byte-identical plans on inputs
#     where descent has nothing to do (external callee not in the
#     in-app classes index) — proves v2 is purely additive.


# ---- Synthetic-DecisionPoint helpers (no fixture dependency) --------------


def _synth_method_ref(
    class_name: str = "com.example.Foo",
    method_name: str = "isLicensed",
    return_descriptor: str = "Z",
) -> MethodRef:
    return MethodRef(
        class_name=class_name,
        method_name=method_name,
        param_descriptors=(),
        return_descriptor=return_descriptor,
    )


def _synth_field_ref(
    class_name: str = "com.example.Foo",
    field_name: str = "mLicensed",
    type_descriptor: str = "Z",
) -> FieldRef:
    return FieldRef(
        class_name=class_name,
        field_name=field_name,
        type_descriptor=type_descriptor,
    )


def _synth_clean_outcome() -> BranchOutcome:
    """``confidence=1.0`` outcome with one ``allow`` + one ``deny``
    verdict — the planner-acceptable shape that triggers Plan A + B."""
    return BranchOutcome(
        verdicts=(
            BranchVerdict(branch_label="true", verdict="deny", score=-1.0, reasons=()),
            BranchVerdict(branch_label="false", verdict="allow", score=1.0, reasons=()),
        ),
        confidence=1.0,
        reasons=(),
    )


def _synth_dp_for_method_call(
    origin: MethodCallOrigin,
    *,
    gate_class: str = "com.example.Gate",
    gate_method: str = "checkLicense",
) -> DecisionPoint:
    """Build a synthetic ``if-eqz``-style decision (one register) whose
    enclosing gate method is void — fires Plan A (against the
    predicate's source method) + Plan B (against the void gate)."""
    return DecisionPoint(
        method=MethodRef(
            class_name=gate_class,
            method_name=gate_method,
            param_descriptors=(),
            return_descriptor="V",
        ),
        instruction_index=4,
        source_line=42,
        kind=DecisionKind.IF_EQZ,
        predicate_registers=("v0",),
        branches=(
            Branch(label="true", target_label=":cond_take"),
            Branch(label="false", target_label=None),
        ),
        predicate_origin=origin,
        branch_outcome=_synth_clean_outcome(),
    )


def _synth_dp_for_field_read(origin: FieldReadOrigin) -> DecisionPoint:
    """Same shape as ``_synth_dp_for_method_call`` but with a
    ``FieldReadOrigin`` — Plan A skipped (Plan A is method-only), Plan B
    fires (void gate)."""
    return DecisionPoint(
        method=MethodRef(
            class_name="com.example.Gate",
            method_name="checkFlag",
            param_descriptors=(),
            return_descriptor="V",
        ),
        instruction_index=2,
        source_line=99,
        kind=DecisionKind.IF_EQZ,
        predicate_registers=("v0",),
        branches=(
            Branch(label="true", target_label=":cond_take"),
            Branch(label="false", target_label=None),
        ),
        predicate_origin=origin,
        branch_outcome=_synth_clean_outcome(),
    )


# ---- Pipeline-driven harness for v2 descent -------------------------------


def _pipeline_v2() -> dict[str, decisions.MethodDecisions]:
    """Variant of ``_pipeline()`` that passes the descent kwargs through
    ``slice_predicate_origins`` so the slicer's bounded inter-procedural
    descent fires. Mirrors what the production ``trace_behavior`` skill
    does."""
    roots = [FIXTURES / "smali", FIXTURES / "smali_classes2"]
    classes, _ = smali_parser.parse_classes(roots)
    mds, _ = decisions.parse_decisions(roots, classes, include_branchless=True)
    classes_by_smali = {c.class_desc: c for c in classes}
    decisions_by_sig = {md.method_signature: md for md in mds}
    out: dict[str, decisions.MethodDecisions] = {}
    budget = slicing._DescentBudget.fresh()
    for md in mds:
        if not md.decision_points:
            continue
        sliced = slicing.slice_predicate_origins(
            md,
            classes_by_smali=classes_by_smali,
            decisions_by_method_sig=decisions_by_sig,
            descent_budget=budget,
        )
        classified = branch_classifier.classify_branch_outcomes(sliced)
        out[md.method_signature] = classified
    return out


class TestPlannerWithV2Descent:
    """Phase 11 sub-step 11.6 — planner against v2 PredicateOrigin
    terminals (``descent_depth`` field-aware)."""

    # --- Synthetic-origin tests (no fixture, isolates planner logic) ------

    def test_method_call_origin_with_descent_depth_targets_deeper_method(self) -> None:
        """Plan A's target should be the predicate origin's ``method``
        regardless of how the slicer arrived at it. When the v2 slicer
        descends 2 hops and surfaces ``MethodCallOrigin(deeperMethod,
        descent_depth=2)``, Plan A correctly targets ``deeperMethod`` —
        operator hooks the actual computation site, not some intermediate
        helper. (Plan A is "force the predicate's source method to
        return X", and the v2 slicer's job is to find the *truest*
        source — depth-2 deep, in this case.)"""
        deeper = _synth_method_ref(
            class_name="com.app.Helpers",
            method_name="pureDeepHelper",
            return_descriptor="Z",
        )
        origin = MethodCallOrigin(method=deeper, invoke_kind="virtual", descent_depth=2)
        dp = _synth_dp_for_method_call(origin)
        plans = plan_bypasses(dp)
        plan_a = _plan_by_template(plans, "force_return_value")
        assert plan_a.target_method is not None
        assert plan_a.target_method.smali_signature == deeper.smali_signature
        assert plan_a.params["method_name"] == "pureDeepHelper"
        assert plan_a.params["class_name"] == "com.app.Helpers"

    def test_field_read_origin_with_descent_depth_still_skips_plan_a(self) -> None:
        """The v2 slicer's same-class field-write walk produces a
        ``FieldReadOrigin`` with ``descent_depth >= 1`` when it walks
        through the field's write site(s). Plan A (method-only) still
        skips — even though descent fired, the terminal variant is
        ``field_read``, not ``method_call``. Plan B (void gate) still
        fires."""
        field = _synth_field_ref(
            class_name="com.app.Cache",
            field_name="mAccessGranted",
            type_descriptor="Z",
        )
        origin = FieldReadOrigin(field=field, is_static=False, descent_depth=1)
        dp = _synth_dp_for_field_read(origin)
        plans = plan_bypasses(dp)
        template_ids = [p.template_id for p in plans]
        assert "force_return_value" not in template_ids
        assert "force_method_skip" in template_ids

    def test_descent_depth_metadata_does_not_affect_plan_output(self) -> None:
        """Same target method, two ``descent_depth`` values (0 vs 2):
        plans must be byte-identical (template_id, risk, params,
        target_method). Planner is descent-agnostic — the depth signal
        is operator-facing UI metadata only, not selection input."""
        target = _synth_method_ref(method_name="getRootStatus")
        origin_v1_shape = MethodCallOrigin(method=target, invoke_kind="virtual", descent_depth=0)
        origin_v2_shape = MethodCallOrigin(method=target, invoke_kind="virtual", descent_depth=2)
        plans_v1 = plan_bypasses(_synth_dp_for_method_call(origin_v1_shape))
        plans_v2 = plan_bypasses(_synth_dp_for_method_call(origin_v2_shape))
        # Order + count + every field except the depth-on-origin must
        # match. The plans tuple is what the UI / Trace SQLite cache
        # consume, and it must not flip on metadata.
        assert len(plans_v1) == len(plans_v2)
        for a, b in zip(plans_v1, plans_v2):
            assert a.template_id == b.template_id
            assert a.params == b.params
            assert a.risk == b.risk
            assert a.target_method == b.target_method
            assert a.source_decision_method == b.source_decision_method

    # --- Pipeline-driven tests (slicer + planner end-to-end) --------------

    def test_pipeline_v2_three_hop_chain_carries_descent_depth_2(self) -> None:
        """``gateThreeHopChainCapped`` calls a chain of three pure
        helpers (``pureChainHopOne → pureChainHopTwo → pureChainHopThree
        → const 0x1``). With ``MAX_SLICE_DEPTH=2`` the descent fires
        twice and caps at the third hop; the cap-stop terminal is
        ``pureChainHopThree`` (the call that ``pureChainHopTwo`` makes
        — the slicer enters its ``_maybe_descend_method_call`` with
        ``budget.remaining_depth == 0`` and tags it with
        ``descent_depth=2``). (No Plan A here because both branches
        return void → no clean DENY/ALLOW split → planner emits zero
        plans; this test pins the slicer-side wire shape that feeds
        the planner.)"""
        sliced = _pipeline_v2()["Lcom/trace/Helpers;->gateThreeHopChainCapped()V"]
        dp = sliced.decision_points[0]
        origin = dp.predicate_origin
        assert isinstance(origin, MethodCallOrigin)
        assert origin.method.method_name == "pureChainHopThree"
        assert origin.descent_depth == 2

    def test_pipeline_v2_stateful_callee_blocks_descent_no_depth_tag(self) -> None:
        """``gateStatefulFieldWriteCallee`` calls
        ``statefulIputCallee()`` whose body contains an ``iput-boolean``
        side effect. ``is_stateless`` returns False — descent doesn't
        fire — the v1 terminal is preserved with ``descent_depth=0``
        (no spurious depth-1 tag). This is the "v2 stays honest when
        descent is blocked" guarantee."""
        sliced = _pipeline_v2()["Lcom/trace/Helpers;->gateStatefulFieldWriteCallee()V"]
        dp = sliced.decision_points[0]
        origin = dp.predicate_origin
        assert isinstance(origin, MethodCallOrigin)
        assert origin.method.method_name == "statefulIputCallee"
        assert origin.descent_depth == 0

    def test_pipeline_v1_vs_v2_plans_unchanged_for_external_callee(self) -> None:
        """``gateBoolPredicate`` calls ``Lcom/trace/Plans;->isPremium()Z``
        — but ``isPremium``'s body isn't in the fixture's
        ``classes_by_smali`` index, so v2 descent can't fire (external
        callee → ``return _tag_descent_depth(origin, budget)`` at the
        top-level ``current_descent_depth=0`` → no depth tag). v1
        plans (no descent kwargs) and v2 plans (with descent kwargs)
        must be byte-identical: same template_ids, same risks, same
        params, same target methods. Confirms v2 is additive — when
        descent has nothing to do, the planner sees the same shape
        it did under v1.

        Also cross-checks that the v2 origin carries
        ``descent_depth=0`` (not ``None``, not missing) — wire shape
        must always be present even when no descent fired so the
        frontend's depth pill renders consistently (i.e. doesn't
        render at all for these cases).
        """
        v1_plans = _plans_for("Lcom/trace/Plans;->gateBoolPredicate()V")
        v2_md = _pipeline_v2()["Lcom/trace/Plans;->gateBoolPredicate()V"]
        v2_dp = v2_md.decision_points[0]
        # Wire-shape check on v2 origin.
        assert isinstance(v2_dp.predicate_origin, MethodCallOrigin)
        assert v2_dp.predicate_origin.descent_depth == 0
        # Plan parity check.
        v2_plans = plan_bypasses(v2_dp, v2_md.instructions, dict(v2_md.label_index))
        assert len(v1_plans) == len(v2_plans)
        for a, b in zip(v1_plans, v2_plans):
            assert a.template_id == b.template_id
            assert a.params == b.params
            assert a.risk == b.risk
            assert a.target_method == b.target_method
            assert a.source_decision_method == b.source_decision_method
