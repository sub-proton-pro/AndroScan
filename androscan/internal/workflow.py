"""Orchestration: pipeline skills -> dossier -> LLM (multi-turn) -> report skill."""

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from androscan.config import Config, load_config
from androscan.internal.app_meta import compute_apk_sha256, load_app_meta, save_app_meta
from androscan.internal.evidence_ref import validate_ref
from androscan.internal.observations_store import append_observations, load_observations
from androscan.internal.run_folder import write_run_meta
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

    started_at = datetime.now()
    ctx = SkillContext(config=config, run_folder=run_folder, apk_path=apk_path)
    app_id_root = run_folder.parent

    dossier_dict = None
    try:
        if Path(apk_path).exists():
            current_hash = compute_apk_sha256(apk_path)
            meta = load_app_meta(app_id_root)
            if meta and meta.get("apk_sha256") == current_hash and meta.get("dossier"):
                dossier_dict = meta["dossier"]
                if run_logger:
                    run_logger.task_update("Using cached dossier...")
    except (OSError, TypeError):
        pass

    if dossier_dict is None:
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
        if manifest_result.data.get("apk_sha256"):
            save_app_meta(app_id_root, manifest_result.data["apk_sha256"], dossier_dict, apk_path)

    ctx.dossier_dict = dossier_dict

    prior_skill_results: list[str] = []
    skill_result_memory_cache: dict[str, str] = {}
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
                # [INFORMATIONAL] skills requested by the LLM
                skill_descs = []
                for req in resp.skill_requests:
                    name = getattr(req, "skill", None) or (req.get("skill") if isinstance(req, dict) else "?")
                    params = getattr(req, "params", None) or (req.get("params") if isinstance(req, dict) else {}) or {}
                    skill_descs.append(f"{name}({params})")
                run_logger.info("Skills requested by LLM: " + ", ".join(skill_descs))
            results = run_skills(
                resp.skill_requests, dossier_dict, run_folder, ctx, memory_cache=skill_result_memory_cache
            )
            if run_logger:
                for name, res in results:
                    if not res.success:
                        run_logger.error(f"{name}: {res.text}")
                executed = [getattr(req, "skill", None) or (req.get("skill") if isinstance(req, dict) else "?") for req in resp.skill_requests]
                run_logger.info("Skills executed by tool: " + ", ".join(executed))
                result_texts = [r.text for _, r in results]
                next_prompt = build_prompt(dossier_dict, prior_skill_results + result_texts, list_llm_skills())
                run_logger.info("Data sent to LLM after executing requested skills:\n" + next_prompt)
            prior_skill_results.extend(r.text for _, r in results)
            continue

        if resp.hypotheses:
            hypotheses = resp.hypotheses
            break

    # Drop hypotheses with any invalid evidence_ref (Phase 3)
    validated = [
        h for h in hypotheses
        if all(validate_ref(dossier_dict, ref) for ref in (h.evidence_refs or []))
    ]
    if run_logger and len(validated) < len(hypotheses):
        run_logger.warning(f"Dropped {len(hypotheses) - len(validated)} hypotheses with invalid evidence_refs")

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
            for h in validated
        ],
        "summary": summary,
    }
    execute("generate_report", report_params, ctx)
    finished_at = datetime.now()
    write_run_meta(run_folder, apk_path, started_at, finished_at, hypotheses_count=len(validated))
    # Persistent observations store (app_id level) for future runs
    run_folder_root = run_folder.parent.parent
    app_id = run_folder.parent.name
    run_ts = run_folder.name
    observation_text = (summary or "").strip() or f"Run completed with {len(validated)} hypotheses."
    append_observations(run_folder_root, app_id, [{"run_ts": run_ts, "source": "run", "text": observation_text}])
