"""Orchestration: pipeline skills -> dossier -> LLM (multi-turn) -> report skill."""

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from androscan.config import Config, load_config
from androscan.llm import build_prompt, build_system_content, complete, parse_response
from androscan.llm.parser import Hypothesis
from androscan.skills import SkillContext, execute, list_llm_skills, run_skills

if TYPE_CHECKING:
    from androscan.internal.run_log import RunLogger


def run_workflow(
    apk_path: str,
    tasks: list[str],
    run_folder: Path,
    config: Optional[Config] = None,
    run_logger: Optional["RunLogger"] = None,
) -> None:
    """Run the analysis workflow: pipeline skills (extract_manifest, prepare_dossier), multi-turn LLM, generate_report.

    - tasks: list of task names (e.g. ["exported_components"]); stub uses first only.
    - run_folder: path to apps/<app_id>/<run_ts>/ where artifacts are written.
    - config: optional Config; if None, load_config() is called.
    - run_logger: optional RunLogger for task updates, llm_busy, and thinking log.
    """
    _ = tasks  # Stub: ignore which tasks; Phase 3 will dispatch per task.
    if config is None:
        config = load_config()

    ctx = SkillContext(config=config, run_folder=run_folder, apk_path=apk_path)

    if run_logger:
        run_logger.task_update("Extracting manifest...")
    manifest_result = execute("extract_manifest", {}, ctx)
    if not manifest_result.success:
        raise RuntimeError(f"extract_manifest failed: {manifest_result.text}")
    if run_logger:
        run_logger.task_update("Building dossier...")
    dossier_result = execute("prepare_dossier", {"manifest": manifest_result.data}, ctx)
    if not dossier_result.success:
        raise RuntimeError(f"prepare_dossier failed: {dossier_result.text}")

    dossier_dict = dossier_result.data
    ctx.dossier_dict = dossier_dict

    prior_skill_results: list[str] = []
    hypotheses: list[Hypothesis] = []
    resp = None
    turn = 0
    max_turns = config.max_turns

    while turn < max_turns:
        turn += 1
        prompt = build_prompt(dossier_dict, prior_skill_results if prior_skill_results else None, list_llm_skills())
        if run_logger:
            run_logger.task_update("LLM is analysing exported components...")
            run_logger.llm_busy(True)
        try:
            result = complete(
                prompt,
                config=config,
                system_content=build_system_content(),
                stream=True,
                run_logger=run_logger,
            )
        finally:
            if run_logger:
                run_logger.llm_busy(False)
        if run_logger and result.thinking:
            run_logger.llm_thinking(result.thinking)
        raw = result.content
        resp = parse_response(raw)

        if resp.skill_requests:
            if run_logger:
                run_logger.task_update("Running requested skills...")
            results = run_skills(resp.skill_requests, dossier_dict, run_folder, ctx)
            prior_skill_results.extend(results)
            continue

        if resp.hypotheses:
            hypotheses = resp.hypotheses
            break

    summary = getattr(resp, "summary", None) or "" if resp else ""
    report_params = {
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
        "summary": summary,
    }
    execute("generate_report", report_params, ctx)
