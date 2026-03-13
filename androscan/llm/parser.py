"""Parse LLM response JSON: skill_requests and hypotheses."""

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Hypothesis:
    id: str
    component_type: str
    component_name: str
    title: str
    description: str
    evidence_refs: list[str]
    exploitability: int
    confidence: int
    remediation_hint: str


@dataclass
class SkillRequest:
    skill: str
    params: dict[str, Any]


@dataclass
class LLMResponse:
    summary: Optional[str] = None
    skill_requests: list[SkillRequest] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)


def parse_response(raw: str) -> LLMResponse:
    """Parse raw LLM response into LLMResponse. On invalid JSON, returns empty response."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return LLMResponse()

    if not isinstance(data, dict):
        return LLMResponse()

    out = LLMResponse(summary=data.get("summary"))

    for req in data.get("skill_requests") or []:
        if isinstance(req, dict) and "skill" in req:
            out.skill_requests.append(
                SkillRequest(skill=req["skill"], params=req.get("params") or {})
            )

    for h in data.get("hypotheses") or []:
        if not isinstance(h, dict):
            continue
        try:
            out.hypotheses.append(
                Hypothesis(
                    id=str(h.get("id", "")),
                    component_type=str(h.get("component_type", "")),
                    component_name=str(h.get("component_name", "")),
                    title=str(h.get("title", "")),
                    description=str(h.get("description", "")),
                    evidence_refs=list(h.get("evidence_refs") or []),
                    exploitability=int(h.get("exploitability", 1)),
                    confidence=int(h.get("confidence", 1)),
                    remediation_hint=str(h.get("remediation_hint", "")),
                )
            )
        except (TypeError, ValueError):
            continue

    return out
