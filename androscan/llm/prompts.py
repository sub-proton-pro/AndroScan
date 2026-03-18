"""Prompt building: system and user prompts per DESIGN_DOC §7 (global context, skills catalog, per-turn user)."""

import json
from typing import Any, Iterator, Optional

from androscan.skills.base import SkillMeta


def _empty_dossier_skeleton(d: dict[str, Any]) -> dict[str, Any]:
    """Copy apk_info and permissions; empty component lists."""
    return {
        "apk_info": d.get("apk_info") or {},
        "permissions": list(d.get("permissions") or []),
        "exported_activities": [],
        "exported_services": [],
        "exported_receivers": [],
        "exported_providers": [],
        "deep_links": [],
    }


def iter_dossier_components(dossier_dict: dict[str, Any]) -> Iterator[tuple[dict[str, Any], str, str, str, int]]:
    """Yield (slice_dict, component_type, label, list_key, full_index) for each exported component in fixed order.

    Order: activities -> services -> receivers -> providers -> deep_links.
    slice_dict is a dossier-shaped dict with only that one component (at index 0 in its list).
    list_key + full_index identify the component in the full dossier for evidence_ref rewriting.
    """
    skel = _empty_dossier_skeleton(dossier_dict)
    for i, item in enumerate(dossier_dict.get("exported_activities") or []):
        slice_dict = {**skel, "exported_activities": [item]}
        name = (item.get("name") or "").strip() or f"activity_{i}"
        yield slice_dict, "activity", name, "exported_activities", i
    for i, item in enumerate(dossier_dict.get("exported_services") or []):
        slice_dict = {**skel, "exported_services": [item]}
        name = (item.get("name") or "").strip() or f"service_{i}"
        yield slice_dict, "service", name, "exported_services", i
    for i, item in enumerate(dossier_dict.get("exported_receivers") or []):
        slice_dict = {**skel, "exported_receivers": [item]}
        name = (item.get("name") or "").strip() or f"receiver_{i}"
        yield slice_dict, "receiver", name, "exported_receivers", i
    for i, item in enumerate(dossier_dict.get("exported_providers") or []):
        slice_dict = {**skel, "exported_providers": [item]}
        name = (item.get("name") or "").strip() or f"provider_{i}"
        yield slice_dict, "provider", name, "exported_providers", i
    for i, item in enumerate(dossier_dict.get("deep_links") or []):
        slice_dict = {**skel, "deep_links": [item]}
        name = (item.get("component") or "").strip() or f"deeplink_{i}"
        yield slice_dict, "deep_link", name, "deep_links", i


def build_component_prompt(
    slice_dict: dict[str, Any],
    component_type: str,
    component_label: str,
    prior_skill_results: Optional[list[str]] = None,
    llm_skills: Optional[list[SkillMeta]] = None,
) -> str:
    """Build user prompt for a single exported component (per-component analysis mode)."""
    parts = [
        f"Analyse this single exported component ({component_type}: {component_label}).",
        "Produce hypotheses with evidence_refs, or request skills if you need more data. Output valid JSON only; exploitability and confidence are integers 1-5.",
        "",
        "## Dossier (single component, JSON)",
        json.dumps(slice_dict, indent=2),
    ]
    if llm_skills:
        parts.extend(["", "## Available skills (request with skill_requests in your JSON response)"])
        for meta in llm_skills:
            parts.append(f"- **{meta.name}**: {meta.description}")
            if meta.params_schema:
                parts.append(f"  Params: {json.dumps(meta.params_schema)}")
        parts.append("")
    if prior_skill_results:
        parts.extend(["", "## Prior skill results", *prior_skill_results])
    parts.extend([
        "",
        "Return valid JSON with optional 'skill_requests' and/or 'hypotheses'. "
        "Use evidence_refs as dossier paths (e.g. exported_activities[0]). exploitability and confidence are integers 1-5.",
    ])
    return "\n".join(parts)


def build_system_content() -> str:
    """System message: role and output format instructions per DESIGN_DOC §7.1."""
    return (
        "You are a Senior Android security assessor. Produce exploitability hypotheses with evidence_refs; "
        "prefer fewer, high-confidence findings. "
        "Available skills: from the skills layer (listed in the user message). For each: name, description, parameters. "
        "How to request skills: Include in your response: skill_requests: [{ \"skill\": \"<name>\", \"params\": {...} }]. "
        "The tool will run them and re-prompt you with the results. When you have enough evidence, omit skill_requests and return hypotheses only. "
        "Always return valid JSON with optional 'skill_requests' and/or 'hypotheses'. "
        "Use evidence_refs as dossier paths (e.g. exported_activities[0]). exploitability and confidence are integers 1-5."
    )


def build_prompt(
    dossier_dict: dict[str, Any],
    prior_skill_results: Optional[list[str]] = None,
    llm_skills: Optional[list[SkillMeta]] = None,
) -> str:
    """Build the user prompt: dossier + optional prior skill results + optional skills catalog (§7.1, §7.3)."""
    parts = [
        "Here is the dossier" + (" and prior skill results below." if prior_skill_results else "."),
        "Produce hypotheses with evidence_refs, or request skills if you need more data. Output valid JSON only; exploitability and confidence are integers 1-5.",
        "",
        "## Dossier (JSON)",
        json.dumps(dossier_dict, indent=2),
    ]
    if llm_skills:
        parts.extend(["", "## Available skills (request with skill_requests in your JSON response)"])
        for meta in llm_skills:
            parts.append(f"- **{meta.name}**: {meta.description}")
            if meta.params_schema:
                parts.append(f"  Params: {json.dumps(meta.params_schema)}")
        parts.append("")
    if prior_skill_results:
        parts.extend(["", "## Prior skill results", *prior_skill_results])
    parts.extend([
        "",
        "Return valid JSON with optional 'skill_requests' and/or 'hypotheses'. "
        "Use evidence_refs as dossier paths. exploitability and confidence are integers 1-5.",
    ])
    return "\n".join(parts)


def build_consolidation_prompt(hypotheses: list[dict[str, Any]]) -> str:
    """Build prompt for LLM to deduplicate and merge overlapping security findings."""
    if not hypotheses:
        return ""
    parts = [
        "Below are security findings from a per-component analysis. Some may duplicate or overlap (same component, same issue, different wording).",
        "",
        "Tasks:",
        "1. Deduplicate: merge findings that describe the same or overlapping issue (especially same evidence_ref / component).",
        "2. For merged findings: write one clear title and one clear description that captures the issue.",
        "3. Keep evidence_refs, exploitability (1-5), and confidence (1-5). Use the highest exploitability when merging.",
        "4. Return valid JSON only, with a single key \"hypotheses\" and an array of finding objects.",
        "5. Each object must have: id, component_type, component_name, title, description, evidence_refs (array of strings), exploitability, confidence, remediation_hint.",
        "",
        "## Findings (JSON)",
        json.dumps(hypotheses, indent=2),
        "",
        "Return valid JSON: { \"hypotheses\": [ ... ] }",
    ]
    return "\n".join(parts)


def build_consolidation_system_content() -> str:
    """System message for the consolidation LLM call."""
    return (
        "You are a security report editor. Merge duplicate or overlapping findings into a single, clear finding. "
        "Return only valid JSON with key \"hypotheses\" and an array of objects. "
        "Each object: id (string), component_type, component_name, title, description, evidence_refs (array of strings), "
        "exploitability (integer 1-5), confidence (integer 1-5), remediation_hint (string)."
    )
