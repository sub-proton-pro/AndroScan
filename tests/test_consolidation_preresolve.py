"""Tests for the consolidation pre-resolve defence (Fix C of the Gemma-4 evidence_refs bug).

Verifies that ``_hypothesis_to_dict`` rewrites raw, ambiguous evidence_refs into
canonical dossier paths before they're handed to the consolidation LLM, so thinking-mode
models (Gemma 4) have nothing left to paraphrase.
"""

from androscan.internal.workflow import (
    _hypothesis_to_dict,
    _post_process_consolidated,
    _rewrite_component_evidence_refs,
)
from androscan.llm.parser import Hypothesis


def _make_hypothesis(refs: list[str]) -> Hypothesis:
    return Hypothesis(
        id="H1",
        component_type="activity",
        component_name="com.example.weakbank.SecretActivity",
        title="t",
        description="d",
        evidence_refs=refs,
        exploitability=3,
        confidence=3,
        remediation_hint="",
    )


def test_hypothesis_to_dict_preserves_refs_when_no_dossier():
    """Backward compat: without a dossier, refs are passed through unchanged."""
    h = _make_hypothesis(["SecretActivity", "exported_activities[0]"])
    d = _hypothesis_to_dict(h)
    assert d["evidence_refs"] == ["SecretActivity", "exported_activities[0]"]


def test_hypothesis_to_dict_preresolves_component_names_with_dossier():
    """Component names get rewritten to canonical dossier paths when dossier is provided."""
    dossier = {
        "exported_activities": [{"name": "com.example.weakbank.SecretActivity"}],
        "exported_providers": [{"name": "com.example.weakbank.WeakBankContentProvider"}],
    }
    h = _make_hypothesis(["SecretActivity", "WeakBankContentProvider"])
    d = _hypothesis_to_dict(h, dossier_dict=dossier)
    assert d["evidence_refs"] == ["exported_activities[0]", "exported_providers[0]"]


def test_hypothesis_to_dict_preresolves_extension_decorated_names():
    """File-extension-decorated refs get rewritten to canonical paths
    (this is the Gemma-4 failure case)."""
    dossier = {
        "exported_activities": [{"name": "com.example.weakbank.SecretActivity"}],
    }
    h = _make_hypothesis(["SecretActivity.kt"])
    d = _hypothesis_to_dict(h, dossier_dict=dossier)
    assert d["evidence_refs"] == ["exported_activities[0]"]


def test_hypothesis_to_dict_passes_through_unresolvable_refs():
    """Unresolvable refs are kept as-is so the post-consolidation resolver
    still has a chance (or so the consolidation LLM can flag them)."""
    dossier = {"exported_activities": [{"name": "com.example.weakbank.SecretActivity"}]}
    h = _make_hypothesis(["SecretActivity", "WeakBankLab.kt", "classes3.dex"])
    d = _hypothesis_to_dict(h, dossier_dict=dossier)
    assert d["evidence_refs"] == ["exported_activities[0]", "WeakBankLab.kt", "classes3.dex"]


def test_hypothesis_to_dict_dedupes_after_resolution():
    """Two refs that resolve to the same dossier path collapse to one entry."""
    dossier = {
        "exported_activities": [{"name": "com.example.weakbank.SecretActivity"}],
    }
    h = _make_hypothesis([
        "SecretActivity",
        "SecretActivity.kt",
        "com.example.weakbank.SecretActivity",
        "exported_activities[0]",
    ])
    d = _hypothesis_to_dict(h, dossier_dict=dossier)
    assert d["evidence_refs"] == ["exported_activities[0]"]


def test_hypothesis_to_dict_preserves_canonical_paths():
    """Already-canonical dossier paths are preserved verbatim (no double-resolve)."""
    dossier = {
        "exported_activities": [{"name": "com.example.weakbank.SecretActivity"}],
        "exported_services": [{"name": "com.example.weakbank.PaymentService"}],
    }
    h = _make_hypothesis(["exported_activities[0]", "exported_services[0]"])
    d = _hypothesis_to_dict(h, dossier_dict=dossier)
    assert d["evidence_refs"] == ["exported_activities[0]", "exported_services[0]"]


def test_hypothesis_to_dict_handles_empty_refs():
    """Empty evidence_refs list survives both code paths."""
    h = _make_hypothesis([])
    assert _hypothesis_to_dict(h)["evidence_refs"] == []
    assert _hypothesis_to_dict(h, dossier_dict={"exported_activities": []})["evidence_refs"] == []


def test_hypothesis_to_dict_preserves_exploit_params():
    """Adding the dossier kwarg must not drop exploit_params."""
    dossier = {"exported_activities": [{"name": "com.example.weakbank.SecretActivity"}]}
    h = _make_hypothesis(["SecretActivity"])
    h.exploit_params = {"action": "android.intent.action.VIEW"}
    d = _hypothesis_to_dict(h, dossier_dict=dossier)
    assert d["exploit_params"] == {"action": "android.intent.action.VIEW"}


# --- Per-component evidence-ref hardening (Fix D) -----------------------------


def _two_activity_dossier() -> dict:
    """A dossier with two activities so slice_ref vs full_ref differ for the second."""
    return {
        "exported_activities": [
            {"name": "com.example.weakbank.MainActivity"},
            {"name": "com.example.weakbank.SecretActivity"},
        ],
        "exported_services": [],
        "exported_receivers": [{"name": "com.example.weakbank.ResetPinReceiver"}],
        "exported_providers": [],
        "deep_links": [],
    }


def test_component_refs_empty_list_anchors_full_ref():
    """LLM emits no evidence_refs at all -> full_ref is injected as the sole anchor.
    This is the primary Gemma-4 failure mode."""
    refs = _rewrite_component_evidence_refs(
        raw_refs=[],
        full_ref="exported_receivers[0]",
        slice_ref="exported_receivers[0]",
        dossier_dict=_two_activity_dossier(),
    )
    assert refs == ["exported_receivers[0]"]


def test_component_refs_drops_unresolvable_helper_class():
    """`WeakBankLab.kt` (helper, not a component) is dropped; full_ref is anchored."""
    refs = _rewrite_component_evidence_refs(
        raw_refs=["WeakBankLab.kt"],
        full_ref="exported_activities[1]",
        slice_ref="exported_activities[0]",
        dossier_dict=_two_activity_dossier(),
    )
    assert refs == ["exported_activities[1]"]


def test_component_refs_rewrites_slice_local_index_to_full():
    """LLM uses index `[0]` because the slice it saw had only one component;
    we rewrite that to the actual full-dossier index."""
    refs = _rewrite_component_evidence_refs(
        raw_refs=["exported_activities[0]"],
        full_ref="exported_activities[1]",
        slice_ref="exported_activities[0]",
        dossier_dict=_two_activity_dossier(),
    )
    assert refs == ["exported_activities[1]"]


def test_component_refs_preserves_canonical_full_ref():
    """LLM correctly emits the canonical full path -> kept as-is, no duplicates."""
    refs = _rewrite_component_evidence_refs(
        raw_refs=["exported_activities[1]"],
        full_ref="exported_activities[1]",
        slice_ref="exported_activities[0]",
        dossier_dict=_two_activity_dossier(),
    )
    assert refs == ["exported_activities[1]"]


def test_component_refs_resolves_short_component_names_in_other_components():
    """LLM references a different valid component by short name -> kept as a 2nd anchor."""
    refs = _rewrite_component_evidence_refs(
        raw_refs=["ResetPinReceiver"],
        full_ref="exported_activities[1]",
        slice_ref="exported_activities[0]",
        dossier_dict=_two_activity_dossier(),
    )
    # full_ref anchors first, then the resolved cross-component ref.
    assert refs == ["exported_activities[1]", "exported_receivers[0]"]


def test_component_refs_handles_kotlin_extension_for_known_component():
    """Component name with .kt extension still resolves, then dedupes with full_ref."""
    refs = _rewrite_component_evidence_refs(
        raw_refs=["SecretActivity.kt"],
        full_ref="exported_activities[1]",
        slice_ref="exported_activities[0]",
        dossier_dict=_two_activity_dossier(),
    )
    assert refs == ["exported_activities[1]"]


def test_component_refs_dedupe_mixed_inputs():
    """Multiple inputs that all resolve to the same path collapse to one entry."""
    refs = _rewrite_component_evidence_refs(
        raw_refs=[
            "exported_activities[0]",  # slice-local -> full_ref
            "exported_activities[1]",  # already full
            "SecretActivity",
            "SecretActivity.kt",
            "com.example.weakbank.SecretActivity",
        ],
        full_ref="exported_activities[1]",
        slice_ref="exported_activities[0]",
        dossier_dict=_two_activity_dossier(),
    )
    assert refs == ["exported_activities[1]"]


def test_component_refs_skips_blank_and_non_string_entries():
    """Blank strings and non-strings (e.g. None) are filtered without crashing."""
    refs = _rewrite_component_evidence_refs(
        raw_refs=["", "   ", None, "WeakBankLab.kt"],
        full_ref="exported_activities[1]",
        slice_ref="exported_activities[0]",
        dossier_dict=_two_activity_dossier(),
    )
    assert refs == ["exported_activities[1]"]


# --- Consolidation post-processing (Fix E: dedupe IDs + backfill component meta) ---


def _full_dossier() -> dict:
    return {
        "exported_activities": [{"name": "com.example.MainActivity"}],
        "exported_services": [{"name": "com.example.PaymentService"}],
        "exported_receivers": [{"name": "com.example.ResetReceiver"}],
        "exported_providers": [{"name": "com.example.AppProvider"}],
        "deep_links": [],
    }


def _bare(id_: str, evidence_refs=None, component_type="", component_name="") -> Hypothesis:
    return Hypothesis(
        id=id_,
        component_type=component_type,
        component_name=component_name,
        title="t",
        description="d",
        evidence_refs=evidence_refs or [],
        exploitability=3,
        confidence=3,
        remediation_hint="",
    )


def test_post_process_assigns_unique_ids_when_all_collide():
    """All findings sharing id 'finding-0' get rewritten to finding-0, finding-1, finding-2, ..."""
    hyps = [_bare("finding-0"), _bare("finding-0"), _bare("finding-0")]
    out = _post_process_consolidated(hyps, dossier_dict=None)
    assert [h.id for h in out] == ["finding-0", "finding-1", "finding-2"]


def test_post_process_keeps_already_unique_ids():
    """If ids are already unique they are preserved verbatim."""
    hyps = [_bare("HYP-001"), _bare("HYP-002"), _bare("HYP-003")]
    out = _post_process_consolidated(hyps, dossier_dict=None)
    assert [h.id for h in out] == ["HYP-001", "HYP-002", "HYP-003"]


def test_post_process_replaces_blank_ids():
    """Empty and whitespace-only ids get a deterministic finding-N replacement."""
    hyps = [_bare(""), _bare("   "), _bare("HYP-1")]
    out = _post_process_consolidated(hyps, dossier_dict=None)
    assert out[0].id == "finding-0"
    assert out[1].id == "finding-1"
    assert out[2].id == "HYP-1"


def test_post_process_partial_collision_uses_index_then_collision_suffix():
    """When the natural finding-N would itself collide, we append -1 etc."""
    hyps = [_bare("finding-1"), _bare("X"), _bare("X")]  # idx 2 collides with idx 1 'X'
    out = _post_process_consolidated(hyps, dossier_dict=None)
    assert out[0].id == "finding-1"
    assert out[1].id == "X"
    # idx=2: starts at "finding-2", which is not in seen, so safe
    assert out[2].id == "finding-2"


def test_post_process_backfills_empty_component_fields_from_evidence_ref():
    """Empty component_type/component_name get backfilled from the first evidence_ref."""
    hyps = [_bare("finding-0", evidence_refs=["exported_services[0]"])]
    out = _post_process_consolidated(hyps, dossier_dict=_full_dossier())
    assert out[0].component_type == "service"
    assert out[0].component_name == "com.example.PaymentService"


def test_post_process_backfills_only_empty_fields():
    """If component_type is already set, only component_name is backfilled (and vice versa)."""
    hyps = [
        _bare("h1", evidence_refs=["exported_services[0]"], component_type="custom_type"),
        _bare("h2", evidence_refs=["exported_services[0]"], component_name="OverrideName"),
    ]
    out = _post_process_consolidated(hyps, dossier_dict=_full_dossier())
    assert out[0].component_type == "custom_type"
    assert out[0].component_name == "com.example.PaymentService"
    assert out[1].component_type == "service"
    assert out[1].component_name == "OverrideName"


def test_post_process_skips_backfill_when_no_dossier():
    """Without a dossier, empty component fields stay empty (no crash, no false data)."""
    hyps = [_bare("h1", evidence_refs=["exported_services[0]"])]
    out = _post_process_consolidated(hyps, dossier_dict=None)
    assert out[0].component_type == ""
    assert out[0].component_name == ""


def test_post_process_skips_backfill_when_no_resolvable_refs():
    """Refs that don't resolve (helper class names) leave component fields untouched."""
    hyps = [_bare("h1", evidence_refs=["WeakBankLab.kt"])]
    out = _post_process_consolidated(hyps, dossier_dict=_full_dossier())
    assert out[0].component_type == ""
    assert out[0].component_name == ""


def test_post_process_uses_first_resolvable_ref():
    """When multiple refs are present, the first that resolves wins for backfill."""
    hyps = [
        _bare(
            "h1",
            evidence_refs=["bogus.kt", "exported_receivers[0]", "exported_activities[0]"],
        )
    ]
    out = _post_process_consolidated(hyps, dossier_dict=_full_dossier())
    assert out[0].component_type == "receiver"
    assert out[0].component_name == "com.example.ResetReceiver"


def test_post_process_preserves_exploit_params_and_other_fields():
    """exploit_params, title, description, exploitability, confidence, remediation_hint all survive."""
    h = _bare("finding-0", evidence_refs=["exported_activities[0]"])
    h.title = "Test Finding"
    h.description = "Long description"
    h.exploitability = 5
    h.confidence = 4
    h.remediation_hint = "Set exported=false."
    h.exploit_params = {"action": "X.Y"}
    out = _post_process_consolidated([h, _bare("finding-0")], dossier_dict=_full_dossier())
    assert out[0].title == "Test Finding"
    assert out[0].description == "Long description"
    assert out[0].exploitability == 5
    assert out[0].confidence == 4
    assert out[0].remediation_hint == "Set exported=false."
    assert out[0].exploit_params == {"action": "X.Y"}
    # Backfilled component fields too
    assert out[0].component_type == "activity"
    assert out[0].component_name == "com.example.MainActivity"
    # Second one got renamed
    assert out[1].id == "finding-1"
