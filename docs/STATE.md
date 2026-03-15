# Current State

This document describes the current implementation state of the repository.

Its purpose is to prevent agents and contributors from assuming that the intended design is already implemented.

This file should be updated whenever a task materially changes what exists, what works, or what remains incomplete.

---

## Summary

Current status: **Phase 2 (skeleton) complete.** Platform skeleton is in place with stub extraction, stub LLM, CLI, workflow, and run folder layout. All components are stubs; Phase 3 will add real extraction and real LLM prompts.

---

## What currently exists

### Implemented
- Repository layout per DESIGN_DOC: `androscan/`, `androscan/config/`, `androscan/internal/`, `androscan/internal/report/`, `androscan/skills/`, `androscan/extraction/`, `androscan/llm/`, `androscan/modules/`, `androscan/modules/exported_components/`, `tests/`.
- Python project: `pyproject.toml` with dependencies (requests, PyYAML), dev deps (pytest).
- CLI: `androscan.py` at repo root with `--apk`, `--task` (multi-valued), `--output`, `--config` (path to global_config.yaml).
- Constants: `androscan.constants` — APP_ID_MAX_LEN, MAX_TURNS_DEFAULT, ISSUE_SEVERITY_LABELS (Critical/High/Medium/Low/Informational), EXPLOITABILITY_LABELS, SECTION_RULE, tool cmd defaults.
- Config: `androscan.config.load_config(config_path?)` — merge order: defaults → `global_config.yaml` (if present) → env. Config includes ollama_*, run_folder_root, max_turns, apktool_cmd, jadx_cmd, section_rule_*, etc. Env: ANDROSCAN_OLLAMA_URL, ANDROSCAN_OLLAMA_TIMEOUT, ANDROSCAN_RUN_FOLDER.
- `global_config.yaml` at repo root (optional): YAML for ollama, paths, workflow, output; settings affect CLI, workflow, run folder, and future extraction/decompilation.
- Dossier model: `androscan.internal.dossier` (ApkInfo, Dossier, exported components, deep_links); `app_id_from_dossier()`, `Dossier.from_dict()`.
- Skills layer: `androscan.skills` — SkillMeta, SkillContext, SkillResult; registry with discover(), execute(), list_llm_skills(), run_skills(). Pipeline skills: extract_manifest, prepare_dossier, generate_report. LLM-requestable: get_decompiled_class (real via jadx, with availability check per CONVENTIONS); get_decompiled_method, list_classes_in_package stubs. Prompt builder uses list_llm_skills() for catalog; prompts aligned with DESIGN_DOC §7.
- Extraction: `androscan.extraction.extract_dossier(apk_path)` delegates to skills (extract_manifest → prepare_dossier) and returns Dossier; stub implementations, no real manifest parsing yet.
- LLM stub: `androscan.llm.complete()` returns fixed JSON (no live Ollama); `build_prompt(dossier, prior_results?, llm_skills?)`, `parse_response()` for skill_requests/hypotheses.
- Run folder: `androscan.internal.run_folder.create_run_folder(app_id)` creates `apps/<app_id>/<run_ts>/` with human-readable timestamp (hyphens in time for Windows); `run_folder_display_path()` returns a display string with time as HH:MM:SS for CLI Appendix.
- Workflow: `run_workflow(apk_path, tasks, run_folder, config?)` — pipeline skills (extract_manifest, prepare_dossier) → multi-turn LLM with skill catalog → generate_report skill.
- Tests: 20 tests (import, config, extraction, dossier/app_id, LLM prompt/parser, CLI parsing, workflow integration with mock, skills layer).

### Partially implemented
- LLM layer: client is stub only; Phase 3 will add real Ollama HTTP calls.
- Report: `report.json`, `run_meta.json`, `run.log` per run; `observations.json` at app_id (Phase 3 done).
- Modules: `exported_components` is an empty package; workflow does not dispatch by task name yet.

### Not yet implemented
- Real APK/manifest parsing.
- Real prompt templates and skills catalog in prompts.
- Phase 4 (hardening, CI).

---

## What is known to work

- `pip install -e ".[dev]"` and `pytest` from repo root succeed (20 tests pass).
- `python androscan.py --apk /dummy.apk --task exported_components` creates `apps/com_example_app/<run_ts>/report.json` with stub hypotheses.
- `--task` can be repeated (e.g. `--task a --task b`).
- Config loads from defaults, optional global_config.yaml (or --config path), and env overrides; default config has expected attributes.
- Extraction stub returns dossier with expected shape; `app_id_from_dossier` yields `com_example_app` for stub.
- LLM parser parses valid JSON with skill_requests and hypotheses; invalid JSON returns empty response without crash.
- Workflow integration test with mock LLM (skill_requests then hypotheses) runs two turns and writes report.
- Run summary shows severity in brackets (e.g. `[High]`), "Findings: n (1 high, 1 low)" wording, and "(confidence: …)" per finding; component name is resolved from dossier when `component_name` is missing (evidence_ref → e.g. `exported_activities[0]` → activity name), else first evidence_ref or "—".
- evidence_ref validation: hypotheses with any invalid evidence_ref are dropped before writing the report (`androscan.internal.evidence_ref.validate_ref`).
- Run artifacts: `run_meta.json` (apk_path, app_id, run_timestamp, started_at, finished_at, hypotheses_count); `run.log` with [task], [ERROR], [WARNING], [INFO], [retry], [thinking]; persistent `observations.json` at `apps/<app_id>/observations.json` for LLM/tool use across runs.

---

## What is incomplete or unverified

- No real APK has been analyzed; extraction is stub only.
- No live Ollama call; LLM is stub only.
- CLI does not validate APK path beyond existence (stub allows /dummy.apk).

---

## Active architectural realities

- Presentation: CLI only (`androscan.py`).
- Orchestration: `internal/workflow.run_workflow()`; composes skills (pipeline then LLM multi-turn), config, llm. Extraction delegates to skills.
- Dependency direction: CLI → workflow → skills, internal (dossier, run_folder), llm.
- Extraction and LLM are stubs; interfaces are in place for Phase 3 replacement.

---

## Known gaps / technical debt

- Stub LLM always returns hypotheses (no real model). Phase 3 will wire real Ollama.
- Stub extraction ignores APK content. Phase 3 will implement extract_manifest and prepare_dossier skills with apktool.
- Run folder artifacts: report.json, run_meta.json, run.log per run; observations.json at app_id level.

---

## Unsafe assumptions for new agents

- Do not assume extraction reads the APK; it is a stub.
- Do not assume LLM calls Ollama; it returns fixed JSON.
- Do not assume all run-folder artifacts exist; report.json, run_meta.json, run.log are written per run when workflow completes.

---

## Current blockers

No known blockers at this time.

---

## Recent completed work

- 2026-03 Skills refactor: skills layer (androscan/skills/) with two-tier model; workflow composes pipeline and LLM skills; extraction delegates to skills; Dossier.from_dict(); 20 tests. DEC-013, ARCHITECTURE, DESIGN_DOC, STATE, TASKS updated.
- 2026-03-13 Phase 2 skeleton: layout, config, dossier model, extraction stub, LLM stub, workflow, run folder, CLI, 14 tests. STATE.md updated.

---

## Next expected milestone

Phase 3: First vertical slice — real extraction (manifest, exported components, permissions, deep links), real prompts and skills catalog, multi-turn LLM with real Ollama, report and run artifacts; integration test with fixture APK + mock LLM.

---

## How to use this document

- Use this file for current reality
- Use `docs/TASKS.md` for active work
- Use `docs/PROJECT_BRIEF.md` for purpose
- Use `docs/ARCHITECTURE.md` for deeper structural intent
- Use `docs/DESIGN_DOC.md` for full target-state design

If this file and the code disagree, either:
- the file is stale, or
- the code drifted from the intended path

Do not ignore the mismatch.
