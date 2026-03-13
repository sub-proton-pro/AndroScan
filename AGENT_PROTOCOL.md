# Agent Protocol

This repository is intended to be worked on by both humans and AI coding agents.

This document defines how any agent should orient itself before making changes, how it should choose work, and how it should report completion.

Follow this protocol before editing code.

---

## Purpose

The goal of this protocol is to ensure that any agent working on this repository:

- understands the project before coding
- works from the current state, not assumptions
- follows architecture and implementation conventions
- makes task-scoped, reviewable changes
- preserves long-term maintainability of the codebase
- does not optimize only for “making the current task pass”

This repository is a long-lived project, not a throwaway prototype.

---

## Mandatory startup sequence

Before making any code changes, read the following documents in this order:

1. `docs/PROJECT_BRIEF.md`
   - Understand what the project is, who it is for, and the intended direction.

2. `docs/STATE.md`
   - Understand what is currently implemented, what works, what is incomplete, and what assumptions are unsafe.

3. `docs/TASKS.md`
   - Identify the active task, top priority item, blockers, dependencies, and expected next step.

4. `docs/CONVENTIONS.md`
   - Understand architecture boundaries, coding rules, testing expectations, security expectations, and workflow constraints.

Only after reading the above should you inspect code files relevant to the task.

Do not start by browsing the codebase randomly.

---

## Conditional reference documents

Read additional documents only when needed.

### Read `docs/ARCHITECTURE.md` when:
- the task affects architecture
- the task introduces a new module or abstraction
- the task spans multiple layers
- the task involves refactoring boundaries or interfaces
- there is uncertainty about dependency direction or ownership

### Read `docs/DECISIONS.md` when:
- you need to understand why something was designed a certain way
- you are considering changing an existing pattern
- there is a tradeoff that appears intentional
- you suspect a non-obvious implementation constraint

### Read `docs/DESIGN_DOC.md` when:
- the task depends on intended future direction
- deeper product or workflow context is needed
- architecture context is insufficient
- the task involves a feature that is only partially implemented today

### Read `docs/SAFETY_AND_SECURITY.md` when:
- the task handles untrusted input
- the task uses external tools, files, repos, archives, parsers, or network interactions
- the task touches auth, secrets, sensitive data, logging, or LLM usage
- the task involves scanners, evidence, or report generation

### Read `docs/TEST_STRATEGY.md` when:
- the task adds business logic
- the task changes integration flows
- the task requires defining or extending test coverage
- the expected tests are not obvious from local code

---

## Operating rules

### 1. Read docs before code
Do not begin by editing code based on guesswork.
Do not assume nearby code is the desired pattern.
Start from the docs, then inspect only the files relevant to the task.

### 2. Prefer task-scoped work
Make changes that are tightly related to the active task.
Avoid broad unrelated cleanup unless:
- it is required to complete the task safely, or
- the task explicitly requests refactoring

### 3. Preserve architecture
Do not break architecture boundaries to make a task faster.
Do not move business logic into presentation code.
Do not add direct dependencies that bypass established layers.

### 4. Do not silently resolve conflicts
If docs, code, and task requirements conflict:
- identify the conflict explicitly
- explain the possible interpretations
- choose the least risky path consistent with the repo conventions
- update relevant docs if needed

### 5. Treat docs as authoritative
Repository docs in `docs/` are authoritative unless clearly stale.
If you find drift between docs and code:
- note it explicitly
- do not silently ignore it
- propose or apply a documentation update when appropriate

### 6. Work incrementally
This repository is intended to evolve feature by feature.
Do not implement broad speculative future functionality.
Build the current slice in a way that preserves future extensibility.

### 7. Avoid full-codebase scanning unless necessary
Inspect only code relevant to the task.
Do not waste context reading unrelated files.

---

## Required behavior before implementation

Before writing code, state the following:

1. The task you are working on
2. The relevant layers/modules
3. The key files likely to be touched
4. The acceptance criteria or completion conditions
5. The edge cases / failure cases / security considerations
6. Any assumptions or missing information

This pre-implementation summary should be concise but explicit.

---

## Required behavior after implementation

After making changes, state the following:

1. What was implemented
2. Which files were changed
3. How the work satisfies the task or acceptance criteria
4. What tests were added or updated
5. What docs were updated or should be updated
6. Any known limitations, debt, or follow-up work

Do not claim completion without addressing tests and docs.

---

## Priority of authority

If multiple sources disagree, use this order of authority:

1. Explicit instructions from the current task/session
2. `docs/CONVENTIONS.md`
3. `docs/ARCHITECTURE.md`
4. `docs/SAFETY_AND_SECURITY.md`
5. `docs/TEST_STRATEGY.md`
6. `docs/DECISIONS.md`
7. `docs/STATE.md`
8. Established code patterns that appear intentional
9. Older code that appears inconsistent with current docs

If there is a conflict, do not silently choose one. State the conflict.

---

## Completion rule

A task is not complete unless:

- it satisfies the requested scope
- it respects architecture boundaries
- error handling has been considered
- relevant tests exist
- relevant docs are updated or explicitly noted as needing updates
- no avoidable architectural shortcuts were introduced

---

## Anti-patterns to avoid

Do not do the following:

- start coding before reading startup docs
- infer architecture only from nearby code
- embed business logic in UI/controllers/renderers
- bypass adapters and interfaces to directly call tools or providers everywhere
- add hidden coupling between feature modules
- hardcode secrets, paths, or environment-specific assumptions
- mark work done without tests
- treat target-state design docs as if they are already implemented
- rewrite broad parts of the system without task justification

---

## Default working style

Unless instructed otherwise:

- prefer small, reviewable changes
- preserve modularity
- preserve testability
- use explicit error handling
- keep interfaces clear
- document non-obvious decisions
- optimize for long-term maintainability, not only short-term output

---

## Summary

Read docs first.
Work from current state.
Follow conventions.
Inspect only relevant code.
Preserve architecture.
Add tests.
Update docs.
Do not take shortcuts that make future work harder.