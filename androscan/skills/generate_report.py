"""Pipeline skill: write report.json to run folder from hypotheses and optional summary."""

import json

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="generate_report",
    description="Write report.json with hypotheses, summary, and optional verification results (verified flag, artifact refs).",
    params_schema={
        "hypotheses": "list of hypothesis dicts",
        "summary": "optional str",
        "verification_results": "optional list of { hypothesis_id, verified, reasoning, artifact_dir }",
    },
    tier="pipeline",
)


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Write report.json under context.run_folder. Merges verification_results into hypotheses by hypothesis_id."""
    hypotheses = params.get("hypotheses") or []
    summary = params.get("summary") or ""
    verification_results = params.get("verification_results") or []
    # Map hypothesis_id -> { verified, reasoning, artifact_dir }
    verification_by_id = {
        v.get("hypothesis_id"): v
        for v in verification_results
        if isinstance(v, dict) and v.get("hypothesis_id") is not None
    }

    report_hypotheses = []
    for h in hypotheses:
        hid = h.get("id", "")
        entry = {
            "id": hid,
            "component_type": h.get("component_type", ""),
            "component_name": h.get("component_name", ""),
            "title": h.get("title", ""),
            "description": h.get("description", ""),
            "evidence_refs": h.get("evidence_refs", []),
            "exploitability": h.get("exploitability", 1),
            "confidence": h.get("confidence", 1),
            "remediation_hint": h.get("remediation_hint", ""),
        }
        ver = verification_by_id.get(hid)
        if ver is not None:
            entry["verified"] = bool(ver.get("verified"))
            entry["verification_reasoning"] = ver.get("reasoning") or ""
            entry["verification_artifact_dir"] = ver.get("artifact_dir")
        else:
            entry["verified"] = None
            entry["verification_reasoning"] = None
            entry["verification_artifact_dir"] = None
        report_hypotheses.append(entry)

    report = {
        "summary": summary,
        "hypotheses": report_hypotheses,
    }
    report_path = context.run_folder / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return SkillResult(success=True, data=report_path, text=f"Report written to {report_path}")
