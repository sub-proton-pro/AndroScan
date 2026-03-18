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

### Task ID
phase-5-exploit-verification

### Title
Phase 5: Exploit verification

### Objective
Verify that reported vulnerabilities are actually exploitable on an emulator: device selection, app env check, build/run exploit commands, capture signals (volatile then non-volatile), LLM verification; then generate report after verification.

### Why this task matters
Phase 3 delivers hypotheses; Phase 5 adds evidence that each finding can be triggered on a live device (emulator + ADB). Report is generated only after exploit verification so it reflects verified/unverified status.

### Scope
- **Workflow order:** Analysis → Hypotheses → Exploit verification → Report generation (report after verification).
- **Exploit tier:** Skills used during exploit verification (e.g. `tier="exploit"`) separate from analysis LLM skills.
- **Skills:** app_env_check (device selection, emulator check, app installed), build_exploit_command (template catalog; RAG stub), capture_signals (volatile parallel then non-volatile; network_capture stub), run_exploit_command, verify_exploit_result (LLM).
- **Artifacts:** `apps/<app_id>/<run_ts>/exploit_verification/<vuln_module>/` (e.g. exported_components) with before/after signals, commands, screenshots.
- **Vuln–skill–signal_profile:** Single JSON file (modules, profiles, signal_type_metadata with volatile/stub) to drive which signals each module captures.
- **Run.log and spinner:** Each exploit verification step must emit a short line to run.log and relevant spinner text. Exploit-tier skills return optional `log_summary` and `spinner_text` on SkillResult; orchestration writes them via RunLogger (e.g. task_update(spinner_text), info(log_summary)).

### Out of scope (for Phase 5)
- Phase 4 (CI, hardening) — parked.
- RAG (LanceDB) for exploit templates — later task or backlog; build_exploit_command uses in-code catalog until then.
- Integration test with fixture APK (remains in backlog).

### Phase 5 implementation plan (one task at a time)

Execute in order; each step is a single-focus task verified before moving on.

**Sub-task status:**

| # | Task | Status |
|---|------|--------|
| 1 | Docs: park Phase 4, add Phase 5 active with this list | Done |
| 2 | Vuln–skill–signal_profile JSON | Done |
| 3 | Exploit skill tier | Done |
| 4 | app_env_check skill | Done |
| 5 | build_exploit_command skill | Done |
| 6 | capture_signals skill | Done |
| 7 | run_exploit_command skill | Done |
| 8 | verify_exploit_result skill | Done |
| 9 | Exploit verification orchestration | Done |
| 10 | Report after verification | Done |

| # | Task | Deliverable | Depends on |
|---|------|-------------|------------|
| 1 | **Docs** | Park Phase 4; add Phase 5 as active with this task list; update STATE.md | — |
| 2 | **Vuln–skill–signal_profile JSON** | Single JSON file (modules, profiles, signal_type_metadata with volatile/stub) | — |
| 3 | **Exploit skill tier** | Add tier="exploit" to contract and registry; list by tier | — |
| 4 | **app_env_check skill** | Skill: adb devices -l, getprop ro.kernel.qemu, pm path; device selection | 3 |
| 5 | **build_exploit_command skill** | In-code template catalog; resolve hypothesis + dossier → command; RAG stub | 2, 3 |
| 6 | **capture_signals skill** | Volatile (parallel) then non-volatile; read JSON; network_capture stub | 2, 3 |
| 7 | **run_exploit_command skill** | adb -s shell; return success, stdout, stderr | 3 |
| 8 | **verify_exploit_result skill** | LLM call with before/after signals; return verified + reasoning | 3 |
| 9 | **Exploit verification orchestration** | Workflow: after validated hypotheses, run exploit steps; write under exploit_verification/<module>/ | 4–8 |
| 10 | **Report after verification** | generate_report accepts verification results; report.json includes verified flag / artifact refs | 9 |

---

## Priority Queue

### P1
- Phase 5 complete. Next: Phase 4 (unpark) or backlog.

---

## Blocked Tasks

### Phase 4: Harden and extend
- blocked by: decision to park Phase 5 first.
- why blocked: Parked; unblock when ready.
- unblock condition: Resume Phase 4 when exploit verification work is paused or complete.

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