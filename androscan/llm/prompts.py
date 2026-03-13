"""Prompt building. Stub: simple string with dossier JSON. Phase 3 will add real templates."""

import json
from typing import Any, Optional


def build_prompt(dossier_dict: dict[str, Any], prior_skill_results: Optional[list[str]] = None) -> str:
    """Build the user prompt: dossier + optional prior skill results.

    Stub: no real templates. Returns a single string suitable for LLM complete().
    """
    parts = [
        "Analyze this exported component dossier and produce exploitability hypotheses.",
        "",
        "## Dossier (JSON)",
        json.dumps(dossier_dict, indent=2),
    ]
    if prior_skill_results:
        parts.extend(["", "## Prior skill results", *prior_skill_results])
    parts.extend([
        "",
        "Return valid JSON with optional 'skill_requests' and/or 'hypotheses'. "
        "Use evidence_refs as dossier paths. exploitability and confidence are integers 1-5.",
    ])
    return "\n".join(parts)
