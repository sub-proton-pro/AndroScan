"""Orchestration: pipeline skills -> dossier -> LLM (multi-turn) -> report skill."""

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from androscan.config import Config, load_config
from androscan.internal.app_meta import compute_apk_sha256, load_app_meta, save_app_meta
from androscan.internal.evidence_ref import resolve_ref, validate_ref
from androscan.internal.exploit_verification import run_exploit_verification
from androscan.internal.observations_store import append_observations, load_observations
from androscan.internal.run_folder import write_run_meta
from androscan.llm import (
    build_component_prompt,
    build_consolidation_prompt,
    build_consolidation_system_content,
    build_prompt,
    build_system_content,
    complete,
    iter_dossier_components,
    parse_response,
)
from androscan.llm.parser import Hypothesis
from androscan.skills import SkillContext, execute, list_llm_skills, run_skills

if TYPE_CHECKING:
    from androscan.internal.run_log import RunLogger


def _hypothesis_to_dict(h: Hypothesis) -> dict:
    """Serialize Hypothesis for consolidation prompt."""
    d: dict = {
        "id": h.id,
        "component_type": h.component_type,
        "component_name": h.component_name,
        "title": h.title,
        "description": h.description,
        "evidence_refs": list(h.evidence_refs or []),
        "exploitability": h.exploitability,
        "confidence": h.confidence,
        "remediation_hint": h.remediation_hint or "",
    }
    if h.exploit_params:
        d["exploit_params"] = h.exploit_params
    return d


def consolidate_hypotheses(
    hypotheses: List[Hypothesis],
    config: Optional[Config] = None,
    run_logger: Optional["RunLogger"] = None,
) -> List[Hypothesis]:
    """Deduplicate and merge overlapping hypotheses via one LLM call. On failure or empty response, return original list."""
    if not hypotheses:
        return []
    if config is None:
        config = load_config()

    dict_list = [_hypothesis_to_dict(h) for h in hypotheses]
    prompt = build_consolidation_prompt(dict_list)
    if not prompt:
        return hypotheses

    if run_logger:
        run_logger.task_update("Consolidating hypotheses...")
        run_logger.llm_busy(True)
    try:
        result = complete(
            prompt,
            config=config,
            system_content=build_consolidation_system_content(),
            stream=True,
            run_logger=run_logger,
        )
    except Exception as e:
        if run_logger:
            run_logger.warning(f"Consolidation LLM call failed ({e}); using original hypotheses.")
        return hypotheses
    finally:
        if run_logger:
            run_logger.llm_busy(False)
    if run_logger and result.thinking:
        run_logger.llm_thinking(result.thinking)

    resp = parse_response(result.content)
    if resp.hypotheses:
        if run_logger:
            run_logger.info(f"Consolidation: {len(hypotheses)} -> {len(resp.hypotheses)} hypotheses.")
        return resp.hypotheses
    if run_logger:
        run_logger.warning("Consolidation returned no hypotheses; using original list.")
    return hypotheses


def run_workflow(
    apk_path: str,
    tasks: list[str],
    run_folder: Path,
    config: Optional[Config] = None,
    run_logger: Optional["RunLogger"] = None,
    exploit_verification_test_mode: bool = False,
    preloaded_hypotheses: Optional[List[Hypothesis]] = None,
) -> None:
    """Run the analysis workflow.

    Normal mode: pipeline skills (extract_manifest, prepare_dossier), multi-turn LLM, exploit verification, generate_report.
    exploit_verification_test_mode: skip extraction and LLM analysis; use preloaded_hypotheses directly
    and run exploit verification + report generation.

    - tasks: list of task names (e.g. ["exported_components"]); stub uses first only.
    - run_folder: path to apps/<app_id>/<run_ts>/ where artifacts are written.
    - config: optional Config; if None, load_config() is called.
    - run_logger: optional RunLogger for task updates, llm_busy, and thinking log.
    - exploit_verification_test_mode: if True, skip extraction/LLM, use preloaded_hypotheses.
    - preloaded_hypotheses: pre-validated hypotheses to use in test mode.
    """
    if config is None:
        config = load_config()

    started_at = datetime.now()
    ctx = SkillContext(config=config, run_folder=run_folder, apk_path=apk_path)
    app_id_root = run_folder.parent

    # In test mode, skip extraction and LLM analysis entirely
    if exploit_verification_test_mode and preloaded_hypotheses is not None:
        if run_logger:
            run_logger.task_update(f"Exploit verification test mode: {len(preloaded_hypotheses)} hypothesis(es) loaded.")
        dossier_dict = None
        try:
            meta = load_app_meta(app_id_root)
            if meta and meta.get("dossier"):
                dossier_dict = meta["dossier"]
        except (OSError, TypeError):
            pass
        if not dossier_dict:
            from androscan.internal.resolve_app_id import resolve_app_id as _resolve
            try:
                _, dossier_obj, _, _ = _resolve(apk_path, config)
                dossier_dict = dossier_obj.to_dict()
            except Exception:
                dossier_dict = {}
        ctx.dossier_dict = dossier_dict
        validated = list(preloaded_hypotheses)
        resp = None
    else:
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
                try:
                    save_app_meta(app_id_root, manifest_result.data["apk_sha256"], dossier_dict, apk_path)
                except OSError as e:
                    if run_logger:
                        run_logger.warning(f"Failed to save app_meta.json: {e}")

        ctx.dossier_dict = dossier_dict

        prior_skill_results: list[str] = []
        skill_result_memory_cache: dict[str, str] = {}
        hypotheses: list[Hypothesis] = []
        resp = None
        max_turns = config.max_turns

        if getattr(config, "per_component_analysis", False):
            # Per-component: one prompt per exported component, then aggregate and consolidate.
            all_hypotheses: list[Hypothesis] = []
            for slice_dict, component_type, label, list_key, full_index in iter_dossier_components(dossier_dict):
                if run_logger:
                    run_logger.write_raw("\n---------- Component: " + component_type + " - " + label + " ----------\n")
                comp_prior: list[str] = []
                turn = 0
                comp_resp = None
                while turn < max_turns:
                    turn += 1
                    prompt = build_component_prompt(
                        slice_dict, component_type, label,
                        comp_prior if comp_prior else None,
                        list_llm_skills(),
                    )
                    if run_logger:
                        run_logger.info("Prompt sent to LLM:\n" + prompt)
                        run_logger.task_update(f"LLM analysing {component_type}: {label}...")
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
                    comp_resp = parse_response(result.content)

                    if not comp_resp.skill_requests and not comp_resp.hypotheses and result.content:
                        if run_logger:
                            preview = result.content[:200] + "..." if len(result.content) > 200 else result.content
                            run_logger.warning(f"LLM response could not be parsed (no skill_requests or hypotheses): {preview}")

                    if comp_resp.skill_requests:
                        if run_logger:
                            run_logger.task_update("Running requested skills...")
                            skill_descs = []
                            for req in comp_resp.skill_requests:
                                name = getattr(req, "skill", None) or (req.get("skill") if isinstance(req, dict) else "?")
                                params = getattr(req, "params", None) or (req.get("params") if isinstance(req, dict) else {}) or {}
                                skill_descs.append(f"{name}({params})")
                            run_logger.info("Skills requested by LLM: " + ", ".join(skill_descs))
                        results = run_skills(
                            comp_resp.skill_requests, dossier_dict, run_folder, ctx, memory_cache=skill_result_memory_cache
                        )
                        if run_logger:
                            for name, res in results:
                                if not res.success:
                                    run_logger.error(f"{name}: {res.text}")
                            executed = [getattr(req, "skill", None) or (req.get("skill") if isinstance(req, dict) else "?") for req in comp_resp.skill_requests]
                            run_logger.info("Skills executed by tool: " + ", ".join(executed))
                        comp_prior.extend(r.text for _, r in results)
                        continue

                    if comp_resp.hypotheses:
                        slice_ref = f"{list_key}[0]"
                        full_ref = f"{list_key}[{full_index}]"
                        component_hyps: list[Hypothesis] = []
                        for h in comp_resp.hypotheses:
                            refs = list(h.evidence_refs or [])
                            refs = [full_ref if r.strip() == slice_ref else r for r in refs]
                            rewritten = Hypothesis(
                                id=h.id,
                                component_type=h.component_type,
                                component_name=h.component_name,
                                title=h.title,
                                description=h.description,
                                evidence_refs=refs,
                                exploitability=h.exploitability,
                                confidence=h.confidence,
                                remediation_hint=h.remediation_hint,
                                exploit_params=h.exploit_params,
                            )
                            component_hyps.append(rewritten)
                            all_hypotheses.append(rewritten)
                        if run_logger and component_hyps:
                            run_logger.component_findings(component_type, label, component_hyps)
                        break
            hypotheses = consolidate_hypotheses(all_hypotheses, config, run_logger)
        else:
            # Single-shot: one prompt with full dossier.
            turn = 0
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

                if not resp.skill_requests and not resp.hypotheses and raw:
                    if run_logger:
                        preview = raw[:200] + "..." if len(raw) > 200 else raw
                        run_logger.warning(f"LLM response could not be parsed (no skill_requests or hypotheses): {preview}")

                if resp.skill_requests:
                    if run_logger:
                        run_logger.task_update("Running requested skills...")
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

        # Normalize and resolve evidence_refs
        validated = []
        for h in hypotheses:
            resolved_refs = []
            for ref in h.evidence_refs or []:
                resolved = resolve_ref(dossier_dict, ref)
                if resolved and validate_ref(dossier_dict, resolved) and resolved not in resolved_refs:
                    resolved_refs.append(resolved)
            if resolved_refs:
                validated.append(
                    Hypothesis(
                        id=h.id,
                        component_type=h.component_type,
                        component_name=h.component_name,
                        title=h.title,
                        description=h.description,
                        evidence_refs=resolved_refs,
                        exploitability=h.exploitability,
                        confidence=h.confidence,
                        remediation_hint=h.remediation_hint,
                        exploit_params=h.exploit_params,
                    )
                )
        if run_logger and len(validated) < len(hypotheses):
            run_logger.warning(f"Dropped {len(hypotheses) - len(validated)} hypotheses with no valid evidence_refs after resolution")

        # Write hypotheses.json so --exploit_verification_test can reload it later
        hypotheses_path = run_folder / "hypotheses.json"
        try:
            hypotheses_path.write_text(json.dumps(
                [_hypothesis_to_dict(h) for h in validated], indent=2,
            ), encoding="utf-8")
        except OSError as e:
            if run_logger:
                run_logger.warning(f"Failed to write hypotheses.json: {e}")

    # --- Common path: exploit verification, report, run_meta, observations ---

    # Auto-assign unique IDs to hypotheses that lack them so that
    # generate_report can map verification results back correctly.
    for idx, h in enumerate(validated):
        if not (h.id or "").strip():
            h.id = f"HYP-{idx}"

    verification_results: list = []
    if validated and tasks:
        vuln_module = tasks[0]
        if run_logger:
            run_logger.task_update("Running exploit verification...")
        verification_results = run_exploit_verification(
            validated, dossier_dict, run_folder, vuln_module, ctx, run_logger
        )

    summary = getattr(resp, "summary", None) or "" if resp else ""
    report_params = {
        "hypotheses": [_hypothesis_to_dict(h) for h in validated],
        "summary": summary,
        "verification_results": verification_results,
    }
    report_result = execute("generate_report", report_params, ctx)
    if not report_result.success and run_logger:
        run_logger.error(f"generate_report failed: {report_result.text}")
    finished_at = datetime.now()
    try:
        write_run_meta(run_folder, apk_path, started_at, finished_at, hypotheses_count=len(validated))
    except OSError as e:
        if run_logger:
            run_logger.warning(f"Failed to write run_meta.json: {e}")
    run_folder_root = run_folder.parent.parent
    app_id = run_folder.parent.name
    run_ts = run_folder.name
    observation_text = (summary or "").strip() or f"Run completed with {len(validated)} hypotheses."
    try:
        append_observations(run_folder_root, app_id, [{"run_ts": run_ts, "source": "run", "text": observation_text}])
    except OSError as e:
        if run_logger:
            run_logger.warning(f"Failed to write observations.json: {e}")
