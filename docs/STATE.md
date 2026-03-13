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
- Repository layout per DESIGN_DOC: `androscan/`, `androscan/config/`, `androscan/internal/`, `androscan/internal/report/`, `androscan/extraction/`, `androscan/llm/`, `androscan/modules/`, `androscan/modules/exported_components/`, `tests/`.
- Python project: `pyproject.toml` with dependencies (requests), dev deps (pytest).
- CLI: `androscan.py` at repo root with `--apk`, `--task` (multi-valued), `--output`.
- Config: `androscan.config.load_config()` with `ollama_base_url`, `ollama_timeout_sec`, `run_folder_root` (env: ANDROSCAN_OLLAMA_URL, etc.).
- Dossier model: `androscan.internal.dossier` (ApkInfo, Dossier, exported components, deep_links); `app_id_from_dossier()` (sanitized package, truncate).
- Extraction stub: `androscan.extraction.extract_dossier(apk_path)` returns hardcoded minimal dossier; no manifest parsing.
- LLM stub: `androscan.llm.complete()` returns fixed JSON (no live Ollama); `build_prompt()`, `parse_response()` for skill_requests/hypotheses.
- Run folder: `androscan.internal.run_folder.create_run_folder(app_id)` creates `apps/<app_id>/<run_ts>/` with human-readable timestamp.
- Stub skills: registry with `get_decompiled_class` returning placeholder text.
- Workflow: `run_workflow(apk_path, tasks, run_folder)` — extraction → prompt → LLM (multi-turn up to 3) → write `report.json`.
- Tests: 14 tests (import, config, extraction, dossier/app_id, LLM prompt/parser, CLI parsing, workflow integration with mock).

### Partially implemented
- LLM layer: client is stub only; Phase 3 will add real Ollama HTTP calls.
- Report: only `report.json` written; no observations.json, scan.log, scan_meta.json yet (Phase 3).
- Modules: `exported_components` is an empty package; workflow does not dispatch by task name yet.

### Not yet implemented
- Real APK/manifest parsing.
- Real prompt templates and skills catalog in prompts.
- evidence_ref validation against dossier.
- observations.json, enriched_scan_config.json, scan.log, scan_meta.json in run folder.
- Phase 3 (first vertical slice) and Phase 4 (hardening, CI).

---

## What is known to work

- `pip install -e ".[dev]"` and `pytest` from repo root succeed (14 tests pass).
- `python androscan.py --apk /dummy.apk --task exported_components` creates `apps/com_example_app/<run_ts>/report.json` with stub hypotheses.
- `--task` can be repeated (e.g. `--task a --task b`).
- Config loads from env; default config has expected attributes.
- Extraction stub returns dossier with expected shape; `app_id_from_dossier` yields `com_example_app` for stub.
- LLM parser parses valid JSON with skill_requests and hypotheses; invalid JSON returns empty response without crash.
- Workflow integration test with mock LLM (skill_requests then hypotheses) runs two turns and writes report.

---

## What is incomplete or unverified

- No real APK has been analyzed; extraction is stub only.
- No live Ollama call; LLM is stub only.
- evidence_refs are not validated against dossier paths.
- Run folder does not yet contain scan.log, scan_meta.json, observations.json.
- CLI does not validate APK path beyond existence (stub allows /dummy.apk).

---

## Active architectural realities

- Presentation: CLI only (`androscan.py`).
- Orchestration: `internal/workflow.run_workflow()`; calls extraction, config, llm, skills.
- Dependency direction: CLI → workflow → extraction, internal (dossier, run_folder, skills), llm.
- Extraction and LLM are stubs; interfaces are in place for Phase 3 replacement.

---

## Known gaps / technical debt

- Stub LLM always returns hypotheses (no real model). Phase 3 will wire real Ollama.
- Stub extraction ignores APK content. Phase 3 will add manifest parsing (e.g. androguard or similar).
- Run folder artifacts: only report.json; other files (scan.log, etc.) to be added in Phase 3.

---

## Unsafe assumptions for new agents

- Do not assume extraction reads the APK; it is a stub.
- Do not assume LLM calls Ollama; it returns fixed JSON.
- Do not assume all run-folder artifacts exist; only report.json is written.

---

## Current blockers

No known blockers at this time.

---

## Recent completed work

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
