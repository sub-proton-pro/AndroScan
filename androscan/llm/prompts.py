"""Prompt building. Stub: simple string with dossier JSON and optional skills catalog. Phase 3 will add real templates."""

import json
from typing import Any, Optional

from androscan.skills.base import SkillMeta


def build_prompt(
    dossier_dict: dict[str, Any],
    prior_skill_results: Optional[list[str]] = None,
    llm_skills: Optional[list[SkillMeta]] = None,
) -> str:
    """Build the user prompt: dossier + optional prior skill results + optional skills catalog.

    If llm_skills is None, the skills catalog section is omitted (or call list_llm_skills() from caller).
    """
    parts = [
        "Analyze this exported component dossier and produce exploitability hypotheses.",
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
