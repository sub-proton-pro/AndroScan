"""Pipeline skill: write report.json to run folder from hypotheses and optional summary."""

import json

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="generate_report",
    description="Write report.json with hypotheses and summary to the run folder.",
    params_schema={"hypotheses": "list of hypothesis dicts", "summary": "optional str"},
    tier="pipeline",
)


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Write report.json under context.run_folder."""
    hypotheses = params.get("hypotheses") or []
    summary = params.get("summary") or ""
    report = {
        "summary": summary,
        "hypotheses": [
            {
                "id": h.get("id", ""),
                "component_type": h.get("component_type", ""),
                "component_name": h.get("component_name", ""),
                "title": h.get("title", ""),
                "description": h.get("description", ""),
                "evidence_refs": h.get("evidence_refs", []),
                "exploitability": h.get("exploitability", 1),
                "confidence": h.get("confidence", 1),
                "remediation_hint": h.get("remediation_hint", ""),
            }
            for h in hypotheses
        ],
    }
    report_path = context.run_folder / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return SkillResult(success=True, data=report_path, text=f"Report written to {report_path}")
