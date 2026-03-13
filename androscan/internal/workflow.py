"""Orchestration: extraction -> dossier -> LLM (multi-turn) -> report."""

import json
from pathlib import Path
from typing import Any, Optional

from androscan.config import Config, load_config
from androscan.extraction import extract_dossier
from androscan.internal.dossier import app_id_from_dossier
from androscan.internal.skills import run_skills
from androscan.llm import complete, build_prompt, parse_response
from androscan.llm.parser import LLMResponse, Hypothesis


def run_workflow(apk_path: str, tasks: list[str], run_folder: Path, config: Optional[Config] = None) -> None:
    """Run the analysis workflow: extract dossier, multi-turn LLM, write report.

    - tasks: list of task names (e.g. ["exported_components"]); stub uses first only.
    - run_folder: path to apps/<app_id>/<run_ts>/ where artifacts are written.
    - config: optional Config; if None, load_config() is called.
    """
    _ = tasks  # Stub: ignore which tasks; Phase 3 will dispatch per task.
    if config is None:
        config = load_config()
    dossier = extract_dossier(apk_path)
    dossier_dict = dossier.to_dict()
    prior_skill_results: list[str] = []
    hypotheses: list[Hypothesis] = []
    turn = 0
    max_turns = config.max_turns

    while turn < max_turns:
        turn += 1
        prompt = build_prompt(dossier_dict, prior_skill_results if prior_skill_results else None)
        raw = complete(prompt, config=config)
        resp = parse_response(raw)

        if resp.skill_requests:
            results = run_skills(resp.skill_requests, dossier_dict, run_folder)
            prior_skill_results.extend(results)
            continue

        if resp.hypotheses:
            hypotheses = resp.hypotheses
            break

    # Write minimal report
    report = {
        "summary": getattr(resp, "summary", None) or "",
        "hypotheses": [
            {
                "id": h.id,
                "component_type": h.component_type,
                "component_name": h.component_name,
                "title": h.title,
                "description": h.description,
                "evidence_refs": h.evidence_refs,
                "exploitability": h.exploitability,
                "confidence": h.confidence,
                "remediation_hint": h.remediation_hint,
            }
            for h in hypotheses
        ],
    }
    report_path = run_folder / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
