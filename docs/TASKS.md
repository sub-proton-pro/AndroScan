# Tasks

This document is the working task queue for the repository.

Its purpose is to tell a human or AI agent what should be worked on next, what is currently active, what is blocked, and what completion looks like.

This file should be kept current.

---

## How to use this file

1. Start with the **Active Task** section.
2. If there is no active task, pick the top unblocked item from **Priority Queue**.
3. Before implementation, confirm:
   - scope
   - affected modules/layers
   - expected tests
   - documentation impact
4. After completion:
   - move the task to Completed
   - update follow-ups if needed
   - update `docs/STATE.md` if current reality changed

Do not start multiple unrelated tasks at once unless explicitly instructed.

---

## Active Task

### Task ID
phase-3-vertical-slice

### Title
Phase 3: First vertical slice (exported components)

### Objective
Implement real extraction (manifest, exported components, permissions, deep links), real prompts and skills catalog, multi-turn LLM with real Ollama (or mock in tests), and report/run artifacts. One real APK should produce a dossier and hypotheses with valid evidence_refs.

### Why this task matters
Phase 2 skeleton is complete. This task delivers the first end-to-end security analysis: APK → dossier → LLM → report with evidence-backed findings.

### In scope
- Real manifest parsing and dossier build from APK using **apktool** (decode APK, parse AndroidManifest.xml).
- Real prompt templates (global context, skills catalog) per DESIGN_DOC.
- Real Ollama client (HTTP) in LLM layer; keep mock for tests.
- At least one real skill (e.g. get_decompiled_class via **jadx**) or keep stub for MVP.
- evidence_ref validation against dossier.
- Write report.json and optionally observations.json, scan_meta.json, scan.log under run folder.
- Integration test: fixture APK + mock LLM → report with expected structure and valid evidence_refs.

### Out of scope
- Phase 4 (CI, full hardening).
- Additional vulnerability modules.
- Full observations.json schema if not needed for MVP.

### Affected layers/modules
- skills (extract_manifest, prepare_dossier real implementation; optional get_decompiled_class)
- extraction (delegates to skills; no change to API)
- llm (real client, real prompts)
- internal/workflow, internal/report
- modules/exported_components (wire into workflow)
- tests

### Expected deliverables
- Real extraction and dossier from APK.
- Real prompts and LLM client; multi-turn with skills.
- Report and run artifacts in run folder.
- Integration test with fixture APK.
- Update STATE.md and TASKS.md when done.

### Completion conditions
- One real APK → dossier → (multi-turn) LLM → report with hypotheses and valid evidence_refs.
- Tests pass including new integration test.
- STATE.md reflects Phase 3 completion.

### Risks / edge cases / concerns
- Manifest parsing must handle malformed APKs; validate and fail cleanly.
- Keep tests runnable without live Ollama (mock).

### Phase 3 implementation plan (order of work)

Execute in this order; each step is a logical sub-task that can be verified before moving on.

1. **Real extraction (skills)**
   - Implement **extract_manifest** and **prepare_dossier** skills with **apktool** (decode APK, parse decoded AndroidManifest.xml); build dossier from manifest (exported activities, services, receivers, providers, permissions, deep links). Extraction layer already delegates to these skills.
   - Add integration test with a fixture APK: assert dossier shape and at least one exported component or permission.

2. **Real Ollama client**
   - Implement HTTP client that calls Ollama API (config.ollama_base_url); keep existing `complete()` interface so workflow is unchanged.
   - Tests continue to use a mock (patch or inject) so CI does not require live Ollama.

3. **Real prompts and skills catalog**
   - Implement prompt templates per DESIGN_DOC: global context (role, task), skills catalog from `list_llm_skills()` (already wired in build_prompt), and per-turn user prompt with dossier and optional prior skill results.
   - Optionally implement at least one real LLM skill (e.g. `get_decompiled_class` via jadx) or keep stub for MVP; ensure multi-turn loop can request and consume skill results.

4. **evidence_ref validation**
   - In workflow or report path: for each hypothesis, validate every `evidence_ref` against the dossier (e.g. resolve path like `exported_activities[0]` to actual dossier content). Drop or flag hypotheses with invalid refs.

5. **Run artifacts**
   - Ensure `report.json` is written with validated hypotheses; add optionally `observations.json`, `scan_meta.json`, `scan.log` under run folder as needed for the slice. Document schema if new.

**Completion check:** One real APK → real dossier → (multi-turn) LLM → report with hypotheses and valid evidence_refs; all tests pass (mock LLM in CI).

---

## Priority Queue

### P1
1. **Phase 4: Harden and extend** (after Phase 3)
   - goal: evidence_ref validation, error handling, config; pytest suite; CI runs tests.
   - dependencies: Phase 3 complete.
   - done when: Tests pass locally and in CI; STATE.md and TASKS.md updated.

---

## Blocked Tasks

Use this section for tasks that should not be started yet.

Format:

### [Task title]
- blocked by:
- why blocked:
- unblock condition:

Example:

### Add PDF report renderer
- blocked by: normalized report output contract
- why blocked: report model not stable enough
- unblock condition: shared rendering schema finalized

---

## Backlog / Future Work

Use for real future work, not vague ideas.

Examples:
- add second vulnerability module
- add JSON output renderer
- add richer evidence provenance tracking
- add adapter for additional mobile tooling
- add web UI shell
- add queue-backed job execution
- add configurable policy layer

---

## Completed Tasks

### 2026-03-13 Phase 2: Build skeleton
- outcome: Repo layout, pyproject.toml, CLI (androscan.py with --apk, --task multi-valued, --output), config, dossier model, extraction stub, LLM stub (client, prompt builder, parser), run folder creation, stub skills, workflow with multi-turn loop, report.json writing. 14 tests (import, config, extraction, LLM, CLI parsing, workflow integration).
- notes: Sub-tasks 1–7 (project setup, config, dossier+extraction stub, LLM stub, workflow+run folder, CLI wiring, minimal tests) completed. All stubs; Phase 3 will add real extraction and real LLM.
- follow-up: Phase 3 (first vertical slice).

### 2026-03-12 Phase 1: Finalize architecture
- outcome: DESIGN_DOC.md is the single source of truth for MVP (architecture, repo structure, dossier schema, LLM I/O schema, prompts/skills, roadmap, risks, first vertical slice).
- notes: Phase 2 (skeleton) completed 2026-03-13.
- follow-up: Phase 3 (first vertical slice).

### 2026-03-12 Added agent protocol and starter docs
- outcome: onboarding and task-loading docs added
- notes: architecture and design docs still pending
- follow-up: create first active engineering task

Keep this section concise.

---

## Task writing rules

Each task should be:
- specific
- scoped
- reviewable
- testable
- bounded in time and complexity

Avoid tasks like:
- “improve architecture”
- “make app better”
- “add security”
- “clean everything up”

Prefer tasks like:
- “add normalized finding model for first feature module”
- “implement adapter wrapper for tool X behind interface Y”
- “add negative tests for malformed artifact input in module Z”

---

## Default execution rule

Unless explicitly instructed otherwise:

- do one task at a time
- complete it properly
- add tests
- update docs
- then move to the next task