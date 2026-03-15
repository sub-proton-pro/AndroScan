"""Prompt building: system and user prompts per DESIGN_DOC §7 (global context, skills catalog, per-turn user)."""

import json
from typing import Any, Optional

from androscan.skills.base import SkillMeta


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
