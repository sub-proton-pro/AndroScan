"""Tests for the LLM-tier ``trace_behavior`` skill — Phase 10 sub-step 10.5.

Same end-to-end posture as ``test_query_call_graph_skill``: seed a real
``apps/<app_id>/.decompiled/<sha>/`` tree with the trace_smali fixture
(reused from 10.1–10.4), build the call-graph SQLite synchronously,
and exercise each path through the skill registry. The skill's
app-dir resolver expects ``run_folder.parent`` to be the app directory,
so the fixture mirrors the production layout.

The LLM round-trip + cache persistence + risk-partition paths are
covered too — these are the fragile bits where regressions would
manifest as silent output drift rather than an explicit test failure
on a hand-rolled unit test.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

import pytest

from androscan.analysis import call_graph
from androscan.analysis.bypass_planner import partition_by_risk
from androscan.analysis.trace_types import (
    BehaviorAnchor,
    BypassPlan,
    DecisionPoint,
    MethodCallOrigin,
    MethodRef,
)
from androscan.config import Config
from androscan.config.loader import (
    CONFIG_FIELD_MAP,
    LIVE_RELOADABLE_FIELDS,
)
from androscan.internal import trace_cache
from androscan.skills import (
    SkillContext,
    execute,
    list_llm_skills,
    list_skills_by_tier,
)
from androscan.skills import trace_behavior as tb_skill
from androscan.web import decompile_cache as dc

FIXTURES = Path(__file__).parent / "fixtures" / "trace_smali"
ENTRY_BOOL = "Lcom/trace/Plans;->gateBoolPredicate()V"
ENTRY_INT = "Lcom/trace/Plans;->gateIntPredicate()V"
ENTRY_BOTH_DENY = "Lcom/trace/Plans;->gateBothBranchesDeny(Z)V"
ENTRY_GHOST = "Lcom/trace/Ghost;->doesNotExist()V"


# ---------------------------------------------------------------------------
# Shared seeders


def _seed_apps_with_graph(tmp_path: Path, app_id: str = "myapp") -> Path:
    """Mirror ``test_query_call_graph_skill._seed_apps_with_graph`` but
    seed the trace_smali fixture (so 10.4's Plans.smali decisions /
    plans show up under the call graph)."""
    apps_root = tmp_path / "apps"
    app_dir = apps_root / app_id
    app_dir.mkdir(parents=True)
    sha = "f" * 40
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK")
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": sha, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )
    dc.sources_dir(app_dir, sha).mkdir(parents=True)
    dc._write_index(
        app_dir, sha,
        {"status": "ready", "apk_path": str(apk), "apk_sha256": sha, "file_count": 0},
    )
    cache = dc.cache_root_for(app_dir, sha)
    smali_out = cache / call_graph.APKTOOL_OUT_SUBDIR
    smali_out.mkdir(parents=True)
    shutil.copytree(FIXTURES / "smali", smali_out / "smali")
    st = call_graph.build_index(cache, apk_path=apk, sha=sha)
    assert st.status == "ready", st.error
    return app_dir


def _ctx_for(app_dir: Path, *, config: Optional[Config] = None) -> SkillContext:
    """Build a SkillContext whose ``run_folder.parent`` is ``app_dir``."""
    run_folder = app_dir / "run-1"
    run_folder.mkdir(parents=True, exist_ok=True)
    return SkillContext(
        config=config or Config.default(),
        run_folder=run_folder,
        dossier_dict={},
        apk_path=str(app_dir / "fake.apk"),
    )


def _cache_dir_for(app_dir: Path) -> Path:
    return dc.cache_root_for(app_dir, "f" * 40)


# ---------------------------------------------------------------------------
# 1) Catalog / registration


def test_trace_behavior_in_llm_catalog():
    """Skill is advertised to the LLM with tier=llm and read-only consent."""
    metas = list_llm_skills()
    by_name = {m.name: m for m in metas}
    assert "trace_behavior" in by_name
    assert by_name["trace_behavior"].tier == "llm"
    assert by_name["trace_behavior"].requires_confirmation is False


def test_trace_behavior_not_in_exploit_catalog():
    """Read-only skills must never accidentally surface as exploit-tier."""
    names = {m.name for m in list_skills_by_tier("exploit")}
    assert "trace_behavior" not in names


def test_trace_behavior_schema_documents_required_params():
    """The schema is surfaced verbatim into the LLM's tool catalog —
    pin the four params we promised in the design."""
    metas = list_llm_skills()
    meta = next(m for m in metas if m.name == "trace_behavior")
    assert set(meta.params_schema.keys()) >= {"entry_method", "app_id", "hops", "force"}


# ---------------------------------------------------------------------------
# 2) Input validation


def test_missing_entry_method_returns_failure():
    ctx = SkillContext(config=Config.default(), run_folder=None)
    r = execute("trace_behavior", {}, ctx)
    assert r.success is False
    assert "entry_method" in r.text


def test_invalid_hops_value_falls_back_to_default(tmp_path):
    """A non-numeric ``hops`` must clamp to the default rather than raise.
    Operator can pass ``hops="bad"`` from a CLI arg without a 500."""
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL, "hops": "not-a-number"},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.data is not None
    assert r.data["hops"] == Config.default().trace_max_hops_default


# ---------------------------------------------------------------------------
# 3) Fail-open paths — the LLM should be able to read empty results & pivot


def test_no_app_context_is_fail_open(tmp_path):
    """Bare run-folder (no surrounding app dir / decompile cache) →
    fail-open with the ``[trace_behavior]`` prefix. The exact text
    varies (``No app directory`` if the parent fall-back fails;
    ``Decompile cache not ready`` if the parent exists but is
    unbuilt) — both are valid fail-open paths."""
    bare = tmp_path / "isolated"
    bare.mkdir()
    ctx = SkillContext(config=Config.default(), run_folder=bare)
    r = execute("trace_behavior", {"entry_method": ENTRY_BOOL}, ctx)
    assert r.success is True
    assert r.data is None
    assert r.text.startswith("[trace_behavior] ")


def test_decompile_not_ready_is_fail_open(tmp_path):
    """App dir exists but no decompile cache → empty result, clear text."""
    app_dir = tmp_path / "apps" / "fresh"
    app_dir.mkdir(parents=True)
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.data is None
    assert "Decompile cache" in r.text


def test_call_graph_not_ready_is_fail_open(tmp_path):
    """Decompile=ready but no call graph → empty result, fail open."""
    app_dir = tmp_path / "apps" / "halfbuilt"
    app_dir.mkdir(parents=True)
    sha = "0" * 40
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK")
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": sha, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )
    dc.sources_dir(app_dir, sha).mkdir(parents=True)
    dc._write_index(
        app_dir, sha,
        {"status": "ready", "apk_path": str(apk), "apk_sha256": sha, "file_count": 0},
    )
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.data is None
    assert "Call graph" in r.text


def test_unknown_entry_method_is_fail_open(tmp_path):
    """Entry method not in the call graph → empty result, fail open
    with a message naming the missing method."""
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_GHOST},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.data is None
    assert "not found" in r.text.lower()
    assert ENTRY_GHOST in r.text


# ---------------------------------------------------------------------------
# 4) Closure walk + per-method static layer (deterministic, no LLM)


def test_single_method_closure_emits_decisions_and_plans(tmp_path, monkeypatch):
    """``gateBoolPredicate()V`` has one decision (if-eqz on isPremium)
    + Plan A force_return_value + Plan B force_method_skip. With
    hops=1 the closure is just the gate method (callees are external
    framework methods)."""
    # Stub LLM so the skill returns deterministically — this gate IS
    # planless from the LLM-call POV (it gets default plans), but the
    # heuristic outcome's confidence is high enough that the LLM is
    # actually NOT invoked. Stubbing is just defensive in case
    # planner thresholds change.
    _stub_llm_unreachable(monkeypatch)
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL, "hops": 1},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.data is not None
    assert r.data["entry_method"]["method_name"] == "gateBoolPredicate"
    assert r.data["hops"] == 1
    assert r.data["truncated"] is False
    assert len(r.data["decisions"]) == 1
    template_ids = {p["template_id"] for p in r.data["plans"]}
    assert "force_return_value" in template_ids
    assert "force_method_skip" in template_ids


def test_closure_walk_writes_to_trace_cache(tmp_path, monkeypatch):
    """First call writes the anchor to ``trace.sqlite``; the row is
    visible via the pure cache-layer reader."""
    _stub_llm_unreachable(monkeypatch)
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL, "hops": 1},
        _ctx_for(app_dir),
    )
    assert r.success is True
    cache = _cache_dir_for(app_dir)
    status = trace_cache.get_status(cache)
    assert status.status == "ready"
    assert status.anchor_count == 1
    cached = trace_cache.read_anchor(cache, ENTRY_BOOL, 1)
    assert cached is not None
    assert cached.entry_method.smali_signature == ENTRY_BOOL
    assert len(cached.plans) == len(r.data["plans"])


# ---------------------------------------------------------------------------
# 5) Cache hit / force=True


def test_second_call_hits_cache_without_re_walking(tmp_path, monkeypatch):
    """Second call returns the cached anchor (text prefixed [cached])
    and does NOT re-invoke the LLM (verified by an unreachable stub
    on the second call)."""
    _stub_llm_unreachable(monkeypatch)
    app_dir = _seed_apps_with_graph(tmp_path)
    ctx = _ctx_for(app_dir)
    r1 = execute("trace_behavior", {"entry_method": ENTRY_BOOL, "hops": 1}, ctx)
    assert r1.success is True
    # Replace the LLM stub with one that fails the test if invoked —
    # cache hit must short-circuit before _invoke_llm runs.
    _stub_llm_must_not_call(monkeypatch)
    r2 = execute("trace_behavior", {"entry_method": ENTRY_BOOL, "hops": 1}, ctx)
    assert r2.success is True
    assert r2.text.startswith("[cached] ")
    # Same payload (data is JSON-equal because the encoder is sort_keys).
    assert r2.data == r1.data


def test_force_true_bypasses_cache_and_rewrites(tmp_path, monkeypatch):
    """``force=True`` re-walks even when the anchor is cached and
    overwrites the stored payload."""
    _stub_llm_unreachable(monkeypatch)
    app_dir = _seed_apps_with_graph(tmp_path)
    ctx = _ctx_for(app_dir)
    execute("trace_behavior", {"entry_method": ENTRY_BOOL, "hops": 1}, ctx)
    cache = _cache_dir_for(app_dir)
    first_status = trace_cache.get_status(cache)
    assert first_status.anchor_count == 1
    r2 = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL, "hops": 1, "force": True},
        ctx,
    )
    assert r2.success is True
    assert not r2.text.startswith("[cached] ")
    second_status = trace_cache.get_status(cache)
    # Still one row (upsert) but it WAS rewritten.
    assert second_status.anchor_count == 1


# ---------------------------------------------------------------------------
# 6) LLM round-trip — re-classification + proposed plan


def test_llm_reclassification_lifts_branch_outcomes(tmp_path, monkeypatch):
    """When a decision's heuristic outcome is below the re-classification
    threshold, the LLM's ``reclassifications`` entry replaces the
    verdict labels and bumps confidence to LLM_RECLASSIFY_CONFIDENCE."""
    # Force every decision into the low-confidence bucket by patching
    # the threshold above 1.0 (the heuristic ceiling) so the LLM is
    # always invoked regardless of how confident the classifier was.
    monkeypatch.setattr(tb_skill, "LLM_RECLASSIFY_THRESHOLD", 2.0)

    captured: dict[str, Any] = {}

    def fake_complete(prompt: str, **kwargs: Any) -> Any:
        captured["prompt"] = prompt
        captured["system"] = kwargs.get("system_content")
        return _LLMResponse(json.dumps({
            "rationale": "Both branches throw — focus the bypass on the predicate value.",
            "reclassifications": [
                {
                    "method": ENTRY_BOOL,
                    "instruction_index": _first_decision_index(tmp_path, ENTRY_BOOL),
                    "branch_label": "true",
                    "verdict": "deny",
                    "reason": "throws SecurityException on this branch",
                },
            ],
            "proposed_plans": [],
        }))

    monkeypatch.setattr("androscan.llm.client.complete", fake_complete)

    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL, "hops": 1},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert "rationale" in r.data
    assert r.data["rationale"].startswith("Both branches throw")
    # The decision's outcome confidence is bumped to LLM tier.
    dp_dict = r.data["decisions"][0]
    assert dp_dict["branch_outcome"]["confidence"] == pytest.approx(
        tb_skill.LLM_RECLASSIFY_CONFIDENCE
    )
    assert any(
        v["branch_label"] == "true" and v["verdict"] == "deny"
        for v in dp_dict["branch_outcome"]["verdicts"]
    )
    assert any(
        "llm-reclassified" in r for v in dp_dict["branch_outcome"]["verdicts"] for r in v["reasons"]
    )
    # The system prompt is the JSON-schema contract; no point pinning
    # every word, but it must mention the schema response key names.
    assert "reclassifications" in captured["system"]
    assert "proposed_plans" in captured["system"]


def test_llm_proposed_plan_with_valid_template_is_accepted(tmp_path, monkeypatch):
    """The LLM proposes a ``force_return_value`` plan; the validator
    round-trips it through the Frida renderer and surfaces it in
    ``data.plans`` (the deterministic plans are preserved)."""
    monkeypatch.setattr(tb_skill, "LLM_RECLASSIFY_THRESHOLD", 2.0)
    target_idx = _first_decision_index(tmp_path, ENTRY_BOOL)

    def fake_complete(prompt: str, **kwargs: Any) -> Any:
        return _LLMResponse(json.dumps({
            "rationale": "Try forcing the predicate to non-zero.",
            "reclassifications": [],
            "proposed_plans": [
                {
                    "method": ENTRY_BOOL,
                    "instruction_index": target_idx,
                    "template_id": "force_return_value",
                    "params": {
                        "class_name": "com.trace.Plans",
                        "method_name": "isPremium",
                        "return_value_expr": "true",
                        "event_label": "llm-proposed",
                    },
                    "rationale": "LLM-derived: flip predicate true",
                    "risk": "low",
                },
            ],
        }))

    monkeypatch.setattr("androscan.llm.client.complete", fake_complete)
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL, "hops": 1},
        _ctx_for(app_dir),
    )
    assert r.success is True
    # Plan list contains both deterministic + LLM-proposed entries.
    rationales = [p.get("rationale") or "" for p in r.data["plans"]]
    assert any("LLM-derived" in r for r in rationales)


def test_llm_proposed_plan_with_invalid_template_is_dropped(tmp_path, monkeypatch):
    """A proposed plan with an unknown ``template_id`` is silently
    dropped; the deterministic plan list is preserved unchanged."""
    monkeypatch.setattr(tb_skill, "LLM_RECLASSIFY_THRESHOLD", 2.0)

    def fake_complete(prompt: str, **kwargs: Any) -> Any:
        return _LLMResponse(json.dumps({
            "rationale": "",
            "reclassifications": [],
            "proposed_plans": [
                {
                    "method": ENTRY_BOOL,
                    "instruction_index": 0,
                    "template_id": "ghost_template_does_not_exist",
                    "params": {"foo": "bar"},
                    "rationale": "should be dropped",
                    "risk": "low",
                },
            ],
        }))

    monkeypatch.setattr("androscan.llm.client.complete", fake_complete)
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL, "hops": 1},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert all(p.get("rationale") != "should be dropped" for p in r.data["plans"])


# ---------------------------------------------------------------------------
# 7) LLM fail-soft — transport failure + malformed JSON


def test_llm_transport_failure_falls_back_to_deterministic(tmp_path, monkeypatch):
    """``complete()`` raises → skill returns the deterministic-only
    payload with ``[llm-skipped: transport: ...]`` in text."""
    monkeypatch.setattr(tb_skill, "LLM_RECLASSIFY_THRESHOLD", 2.0)

    def boom(prompt: str, **kwargs: Any) -> Any:
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr("androscan.llm.client.complete", boom)
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL, "hops": 1},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert "[llm-skipped:" in r.text
    assert "transport" in r.text
    # Deterministic plans still shipped.
    assert len(r.data["plans"]) >= 1


def test_llm_malformed_json_falls_back_to_deterministic(tmp_path, monkeypatch):
    """Non-JSON LLM output → skill returns deterministic payload with
    a ``[llm-skipped: json-parse: ...]`` marker."""
    monkeypatch.setattr(tb_skill, "LLM_RECLASSIFY_THRESHOLD", 2.0)

    def garbage(prompt: str, **kwargs: Any) -> Any:
        return _LLMResponse("not actually json {")

    monkeypatch.setattr("androscan.llm.client.complete", garbage)
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL, "hops": 1},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert "[llm-skipped: json-parse" in r.text
    assert len(r.data["plans"]) >= 1


# ---------------------------------------------------------------------------
# 8) Risk partitioning — advanced_plans surface separately


def test_risk_partition_routes_high_risk_to_advanced(tmp_path, monkeypatch):
    """A ``trace_bypass_risk_max="low"`` config knob routes Plan B
    (force_method_skip = MEDIUM risk) into ``advanced_plans`` while
    Plan A (force_return_value = LOW risk) stays in ``plans``."""
    _stub_llm_unreachable(monkeypatch)
    from androscan.config.loader import with_overrides
    cfg = with_overrides(Config.default(), trace_bypass_risk_max="low")
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute(
        "trace_behavior",
        {"entry_method": ENTRY_BOOL, "hops": 1},
        _ctx_for(app_dir, config=cfg),
    )
    assert r.success is True
    default_templates = {p["template_id"] for p in r.data["plans"]}
    advanced_templates = {p["template_id"] for p in r.data["advanced_plans"]}
    assert "force_return_value" in default_templates
    assert "force_method_skip" in advanced_templates


# ---------------------------------------------------------------------------
# 9) Config knob smoke tests (10.5 adds two more on top of 10.4's
#    trace_bypass_risk_max).


def test_trace_max_hops_config_defaults():
    cfg = Config.default()
    assert cfg.trace_max_hops_default == 3
    assert cfg.trace_max_hops_hard_cap == 6


def test_trace_max_hops_are_live_reloadable():
    """Both new knobs must be in LIVE_RELOADABLE_FIELDS so the workbench
    /api/config/reload endpoint picks up changes without a restart."""
    assert "trace_max_hops_default" in LIVE_RELOADABLE_FIELDS
    assert "trace_max_hops_hard_cap" in LIVE_RELOADABLE_FIELDS


def test_trace_max_hops_in_field_map():
    assert "trace_max_hops_default" in CONFIG_FIELD_MAP
    assert "trace_max_hops_hard_cap" in CONFIG_FIELD_MAP


# ---------------------------------------------------------------------------
# Helpers


class _LLMResponse:
    """Minimal stand-in for :class:`androscan.llm.client.CompleteResult`.

    The skill only reads ``content``/``text`` — we don't need to ship
    a full result object."""
    def __init__(self, content: str) -> None:
        self.content = content
        self.text = content


def _stub_llm_unreachable(monkeypatch) -> None:
    """Default stub: returns an empty JSON object so any incidental LLM
    invocation doesn't crash the test, but doesn't change behaviour
    either. Used by tests that exercise the *deterministic* path and
    don't care whether the LLM is hit (the heuristic confidence is
    usually high enough that it isn't)."""
    def _stub(prompt: str, **kwargs: Any) -> Any:
        return _LLMResponse('{"rationale": "", "reclassifications": [], "proposed_plans": []}')
    monkeypatch.setattr("androscan.llm.client.complete", _stub)


def _stub_llm_must_not_call(monkeypatch) -> None:
    """Stub that fails the test if the LLM is invoked. Used to verify
    cache-hit short-circuiting."""
    def _bomb(prompt: str, **kwargs: Any) -> Any:
        raise AssertionError(
            "LLM must not be invoked — the trace.sqlite cache hit should "
            "short-circuit before _invoke_llm runs."
        )
    monkeypatch.setattr("androscan.llm.client.complete", _bomb)


def _first_decision_index(tmp_path: Path, entry: str) -> int:
    """Resolve the entry method's first decision's instruction_index
    by running the deterministic part of the pipeline. Tests need
    this to construct LLM-output stubs that target the right gate."""
    from androscan.analysis import (
        branch_classifier,
        decisions as decisions_mod,
        slicing,
        smali_parser,
    )
    roots = [FIXTURES / "smali"]
    classes, _ = smali_parser.parse_classes(roots)
    method_decisions, _ = decisions_mod.parse_decisions(roots, classes)
    md = next(m for m in method_decisions if m.method_signature == entry)
    sliced = slicing.slice_predicate_origins(md)
    classified = branch_classifier.classify_branch_outcomes(sliced)
    return classified.decision_points[0].instruction_index
