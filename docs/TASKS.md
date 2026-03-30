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
   - **update sub-task status in this file immediately** (mark the sub-task done, e.g. `[x]` or move to completed list) so other IDEs and agents know where to take over
   - move the task to Completed when the whole phase/task is done
   - update follow-ups if needed
   - update `docs/STATE.md` if current reality changed

Do not start multiple unrelated tasks at once unless explicitly instructed.

---

## Active Task

None. Phase 5 is complete. Pick from Priority Queue or Backlog.

---

## Priority Queue

### P1
- Unpark Phase 4 (CI, hardening).
- Implement `content_provider_query` and `app_data_snapshot` signal captures for `exported_provider` profile (currently stub).
- Add second vulnerability module.

### P2
- Integration test with fixture APK.
- JSON output renderer.
- Richer evidence provenance tracking.

---

## Blocked Tasks

### Phase 4: Harden and extend
- blocked by: prioritization decision.
- why blocked: Parked during Phase 5; now unblocked. Ready to pick up.
- unblock condition: Unblocked (Phase 5 complete).

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

- **Integration test with fixture APK** (extraction + dossier shape) — parked; optional for later.
- add second vulnerability module
- add JSON output renderer
- add richer evidence provenance tracking
- add adapter for additional mobile tooling
- add web UI shell
- add queue-backed job execution
- add configurable policy layer

---

## Completed Tasks

### 2026-03-30 Phase 5: Exploit verification
- outcome: Exploit verification on emulator + ADB. 5 exploit-tier skills (app_env_check, build_exploit_command, capture_signals, run_exploit_command, verify_exploit_result). Template catalog for 5 component profiles (exported_activity, exported_service, exported_receiver, exported_provider, deep_link). Vuln–skill–signal profile JSON. Before/after signal capture (volatile then non-volatile). LLM-based verification. Report generated after verification with verified flag, reasoning, and artifact refs. Artifacts per hypothesis under exploit_verification/<module>/<hyp_id>/. All 10 sub-tasks complete.
- notes: `exported_provider` has 2 stub signal types (content_provider_query, app_data_snapshot); logcat and exploit command are real. RAG for exploit templates deferred to backlog.
- follow-up: Unpark Phase 4 (CI, hardening) or pick from backlog (provider stubs, second vulnerability module).

### Phase 3: First vertical slice (exported components)
- outcome: Real extraction (apktool), real Ollama client, real prompts and skills catalog, evidence_ref validation, run artifacts (report.json, run_meta.json, run.log, observations.json), skill results cache (get_decompiled_class keyed by resolved class name). get_decompiled_class and get_decompiled_method real via jadx. 54 tests; mock LLM in CI.
- notes: All five sub-tasks done. Integration test with fixture APK parked in backlog.
- follow-up: Phase 4 (harden and extend, CI).

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

## Phase and task list rules

**So other IDEs and agents know where to take over:**

1. **List sub-tasks explicitly.** For every phase or multi-step task, the implementation plan in this file MUST list sub-tasks explicitly (numbered or bulleted), not only in prose. Each sub-task should be verifiable on its own.

2. **Update status as soon as a sub-task is completed.** When a sub-task is finished (code done, tests pass, behaviour verified), update this file in the same work session:
   - Mark the sub-task done (e.g. add `[x] Done` or `— [x] Done` next to the heading, or update a status table).
   - Do not leave a phase "in progress" without reflecting which sub-tasks are already done.

3. **Keep a short status overview.** For the active phase, keep a compact status table or checklist (e.g. "Sub-task status") at the top of the implementation plan so the next agent can see at a glance what is done and what is next.

4. **External tool availability.** When adding or changing code that uses an external tool (apktool, jadx, etc.), the implementation must check tool availability (e.g. `shutil.which(cmd)`) and handle missing tools without crashing (return a clear result; do not raise raw subprocess/OS errors). See `docs/CONVENTIONS.md` §4 External tool availability.

---

## Default execution rule

Unless explicitly instructed otherwise:

- do one task at a time
- complete it properly
- add tests
- update docs
- then move to the next task