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
[Replace with task identifier]

### Title
[Replace with active task title]

### Objective
Describe the exact outcome expected from this task.

Example:
Implement the first vulnerability check module using the shared check interface and normalized finding model, with unit tests and basic integration coverage.

### Why this task matters
Explain why this task is the current priority.

Example:
This task validates whether the platform architecture can support a real feature without collapsing into special-case code.

### In scope
List what is included.

Example:
- add one vulnerability module
- integrate it through the shared orchestration path
- normalize results into shared finding model
- add tests
- update relevant docs

### Out of scope
List what is explicitly not included.

Example:
- additional vulnerability modules
- full UI implementation
- PDF renderer
- unrelated refactors
- speculative future abstractions beyond what this feature needs

### Affected layers/modules
List expected areas touched.

Example:
- vulnerability checks layer
- application/domain layer
- orchestration layer
- tests
- docs

### Expected deliverables
- implementation code
- tests
- doc updates
- completion summary

### Completion conditions
The task is complete only if:
- required functionality is implemented
- architecture boundaries are preserved
- tests are added and pass
- docs are updated if behavior or structure changed
- known limitations are explicitly called out

### Risks / edge cases / concerns
List anything important to watch.

Example:
- avoid coupling the feature directly to presentation code
- avoid embedding module-specific logic inside orchestration
- ensure malformed input handling is tested
- preserve normalized result contract

---

## Priority Queue

Use this section for ordered, ready-to-pick tasks.

### P1
1. [Task title]
   - goal:
   - dependencies:
   - done when:

2. [Task title]
   - goal:
   - dependencies:
   - done when:

### P2
1. [Task title]
   - goal:
   - dependencies:
   - done when:

2. [Task title]
   - goal:
   - dependencies:
   - done when:

### P3
1. [Task title]
   - goal:
   - dependencies:
   - done when:

Remove placeholders and replace with real queue items.

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

Move completed items here with date and short outcome.

Format:

### [date] [Task title]
- outcome:
- notes:
- follow-up:

Example:

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