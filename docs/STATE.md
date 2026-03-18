# Current State

This document describes the current implementation state of the repository.

Its purpose is to prevent agents and contributors from assuming that the intended design is already implemented.

This file should be updated whenever a task materially changes what exists, what works, or what remains incomplete.

---

## Summary

Current status: **Phase 3 (first vertical slice) complete.** Real extraction (apktool), real Ollama client, real prompts and skills catalog, evidence_ref validation, and run artifacts are in place. One real APK produces a dossier and report with evidence-backed hypotheses. Phase 4 (harden and extend, CI) is next.

---

## What currently exists

### Implemented
- Repository layout per DESIGN_DOC: `androscan/`, `androscan/config/`, `androscan/internal/`, `androscan/internal/report/`, `androscan/skills/`, `androscan/extraction/`, `androscan/llm/`, `androscan/modules/`, `androscan/modules/exported_components/`, `tests/`.
- Python project: `pyproject.toml` with dependencies (requests, PyYAML), dev deps (pytest).
- CLI: `androscan.py` at repo root with `--apk`, `--task` (multi-valued), `--output`, `--config` (path to global_config.yaml).
- Constants: `androscan.constants` — APP_ID_MAX_LEN, MAX_TURNS_DEFAULT, ISSUE_SEVERITY_LABELS (Critical/High/Medium/Low/Informational), EXPLOITABILITY_LABELS, SECTION_RULE, tool cmd defaults.
- Config: `androscan.config.load_config(config_path?)` — merge order: defaults → `global_config.yaml` (if present) → env. Config includes ollama_*, run_folder_root, max_turns, apktool_cmd, jadx_cmd, section_rule_*, etc. Env: ANDROSCAN_OLLAMA_URL, ANDROSCAN_OLLAMA_TIMEOUT, ANDROSCAN_RUN_FOLDER.
- `global_config.yaml` at repo root (optional): YAML for ollama, paths, workflow, output; settings affect CLI, workflow, run folder, extraction, and decompilation.
- Dossier model: `androscan.internal.dossier` (ApkInfo, Dossier, exported components, deep_links); `app_id_from_dossier()`, `Dossier.from_dict()`.
- Skills layer: `androscan.skills` — SkillMeta, SkillContext, SkillResult; registry with discover(), execute(), list_llm_skills(), run_skills(). Pipeline skills: extract_manifest, prepare_dossier (real via apktool), generate_report. LLM-requestable: get_decompiled_class (real via jadx, with availability check and cache keyed by resolved class name); get_decompiled_method (real), list_classes_in_package. Prompt builder uses list_llm_skills() for catalog; prompts aligned with DESIGN_DOC §7.
- Extraction: `androscan.extraction.extract_dossier(apk_path)` delegates to skills (extract_manifest → prepare_dossier) and returns Dossier; real manifest parsing via apktool (decode APK, parse AndroidManifest.xml).
- LLM: Real Ollama HTTP client in `androscan.llm.client` — `complete()`, `is_ollama_available()`; `build_prompt(dossier, prior_results?, llm_skills?)`, `parse_response()` for skill_requests/hypotheses. Tests use mock so CI does not require live Ollama.
- Run folder: `androscan.internal.run_folder.create_run_folder(app_id)` creates `apps/<app_id>/<run_ts>/` with human-readable timestamp; `run_folder_display_path()` returns display string. Run artifacts: report.json, run_meta.json, run.log per run; observations.json at app_id level.
- Workflow: `run_workflow(apk_path, tasks, run_folder, config?)` — pipeline skills (extract_manifest, prepare_dossier) → multi-turn LLM with skill catalog → generate_report. evidence_ref validation: hypotheses with invalid evidence_refs are dropped before report.
- Skill results cache: in-memory and disk cache (apps/<app_id>/skill_results_cache.json); get_decompiled_class cache key uses resolved class name for per-component analysis.
- Tests: 54 tests (import, config, extraction, dossier/app_id, LLM prompt/parser/client, CLI parsing, workflow integration, skills layer, skill results cache).

### Partially implemented
- Modules: `exported_components` workflow is wired; task dispatch supports multiple --task values; first module is the only one implemented.

### Not yet implemented
- Phase 4: CI (pytest on every push/PR), additional hardening and error handling.
- Integration test with fixture APK (extraction + dossier shape) — parked in backlog.

---

## What is known to work

- `pip install -e ".[dev]"` and `pytest` from repo root succeed (54 tests pass).
- `python androscan.py --apk <path_to_apk> --task exported_components` runs real extraction → dossier → multi-turn LLM (Ollama) → report under `apps/<app_id>/<run_ts>/`. With real APK and Ollama running, report contains hypotheses with valid evidence_refs.
- `--task` can be repeated (e.g. `--task a --task b`). CLI checks Ollama reachability before analysis; clear error and setup tip if unreachable.
- Config loads from defaults, optional global_config.yaml (or --config path), and env overrides; default config has expected attributes.
- Real extraction: apktool decodes APK; AndroidManifest.xml is parsed to build dossier (exported activities, services, receivers, providers, permissions, deep links). `app_id_from_dossier` yields sanitized package name.
- LLM: Real Ollama HTTP client; parser handles valid JSON with skill_requests and hypotheses; invalid JSON returns empty response without crash. Tests use mock so no live Ollama in CI.
- Workflow integration tests: multi-turn with mock (skill_requests then hypotheses), evidence_ref validation, run_meta and run.log written.
- Run summary shows severity in brackets (e.g. `[High]`), "Findings: n (1 high, 1 low)" wording, and "(confidence: …)" per finding; component name is resolved from dossier when `component_name` is missing (evidence_ref → e.g. `exported_activities[0]` → activity name), else first evidence_ref or "—".
- evidence_ref validation: hypotheses with any invalid evidence_ref are dropped before writing the report (`androscan.internal.evidence_ref.validate_ref`).
- Run artifacts: `report.json` (validated hypotheses), `run_meta.json` (apk_path, app_id, run_timestamp, started_at, finished_at, hypotheses_count); `run.log` with [task], [ERROR], [WARNING], [INFORMATIONAL], [retry], [thinking]; persistent `observations.json` at `apps/<app_id>/observations.json`. Skill results cache at `apps/<app_id>/skill_results_cache.json` (cache key for get_decompiled_class uses resolved class name).

---

## What is incomplete or unverified

- CI does not yet run tests on every push/PR (Phase 4).
- Integration test with fixture APK (assert dossier shape from extraction) is parked; optional for later.
- CLI validates APK path existence; malformed APKs may produce unclear extraction errors until Phase 4 hardening.

---

## Active architectural realities

- Presentation: CLI only (`androscan.py`).
- Orchestration: `internal/workflow.run_workflow()`; composes skills (pipeline then LLM multi-turn), config, llm. Extraction delegates to skills.
- Dependency direction: CLI → workflow → skills, internal (dossier, run_folder, evidence_ref), llm.
- Extraction is real (apktool); LLM is real (Ollama HTTP); skills include real get_decompiled_class (jadx) and get_decompiled_method.

---

## Known gaps / technical debt

- Phase 4: CI (pytest on push/PR), broader error handling and user-facing messages for extraction/Ollama failures.
- Run folder artifacts are in place; optional fixture-APK integration test is in backlog.

---

## Unsafe assumptions for new agents

- Extraction reads the APK via apktool; LLM calls Ollama. Tests use mocks/fixtures and do not require live Ollama or a real APK.
- report.json, run_meta.json, run.log are written per run when workflow completes; observations.json is at app_id level.

---

## Current blockers

No known blockers at this time.

---

## Recent completed work

- Phase 3 vertical slice: real extraction (apktool), real Ollama client, real prompts and skills catalog, evidence_ref validation, run artifacts, skill results cache (with resolved class name for get_decompiled_class). 54 tests.
- 2026-03 Skills refactor: skills layer (androscan/skills/) with two-tier model; workflow composes pipeline and LLM skills; extraction delegates to skills; Dossier.from_dict().
- 2026-03-13 Phase 2 skeleton: layout, config, dossier model, extraction stub, LLM stub, workflow, run folder, CLI.

---

## Next expected milestone

Phase 4: Harden and extend — CI runs pytest on every push/PR; error handling and config; STATE.md and TASKS.md kept current.

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
