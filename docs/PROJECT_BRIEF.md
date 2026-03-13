# Project Brief

## Project name

[Replace with actual project name]

Example working name: Modular Mobile Security Analysis Platform

---

## What this project is

This project is a modular security-analysis tool intended to analyze mobile applications and related artifacts through a combination of:

- structured vulnerability checks
- tool-assisted analysis
- optional LLM-assisted reasoning
- multiple presentation/output channels

The project is designed to grow incrementally, one feature at a time, into a long-lived platform rather than a one-off script or prototype.

---

## Primary goal

Build a maintainable, extensible platform where new security-analysis capabilities can be added as independent modules without requiring major rewrites of the rest of the system.

The tool should support clean separation between:

- presentation/output
- orchestration/workflow logic
- shared business/domain logic
- LLM interactions
- vulnerability-specific logic
- external tool integrations
- infrastructure concerns

---

## Why this project exists

Security tooling often starts as a collection of scripts, checkers, and output hacks that become hard to extend, test, and reason about.

This project exists to avoid that failure mode.

The intended outcome is a platform that:

- supports feature-by-feature growth
- maintains architectural discipline from the beginning
- remains testable and auditable as capabilities increase
- makes it easy to add new vulnerability classes over time
- allows both humans and AI agents to work in the repo consistently

---

## Intended users

Primary users may include:

- security engineers
- mobile application security reviewers
- product security teams
- researchers or consultants running repeatable security checks
- developers or internal teams consuming structured findings and reports

---

## Core product idea

At a high level, the platform should eventually support workflows such as:

1. Accept a target artifact or analysis input
2. Determine what checks or workflows should run
3. Execute one or more vulnerability-analysis modules
4. Collect evidence and normalize findings
5. Present or export results through one or more channels

Potential output channels include:

- CLI / terminal output
- web UI
- JSON or machine-readable output
- PDF or report rendering
- API responses

---

## Expected long-term capabilities

Examples of future capabilities may include:

- multiple mobile vulnerability classes
- pluggable vulnerability modules
- orchestration of multi-step analysis workflows
- integration with static and dynamic analysis tools
- artifact parsing and metadata extraction
- evidence collection and normalization
- report generation in different formats
- optional LLM-assisted interpretation or summarization
- policy-based filtering or severity handling
- auditability and provenance of results

These capabilities are aspirational and may not all exist yet.

---

## Explicit product direction

This project should be treated as:

- modular
- incremental
- architecture-conscious
- security-conscious
- test-driven or at least test-required
- suitable for iterative AI-assisted development

This project should not be treated as:

- a single-shot generated app
- a monolithic script pile
- a UI-first demo without real architecture
- a loose collection of feature-specific hacks

---

## Out of scope for now

Unless explicitly added later, assume the following are out of scope for the current phase:

- building every future vulnerability module immediately
- solving all future extensibility needs upfront in implementation detail
- building unnecessary infrastructure before there is a real use case
- overengineering beyond current needs without preserving extensibility
- broad speculative feature work that is not tied to a defined task

---

## Development model

This repository is intended to be developed in stages:

1. Establish platform direction and architecture
2. Build a minimal but extensible skeleton
3. Implement one feature/module at a time
4. Test each feature
5. Review architectural fit after each increment
6. Expand capabilities without breaking modularity

Each feature should be built as part of the platform, not as a one-off solution.

---

## Success criteria

The project is successful when:

- new vulnerability features can be added with minimal cross-cutting changes
- architecture boundaries remain clear
- LLM usage, if any, is isolated and controlled
- output channels are separate from detection logic
- external tools are wrapped cleanly behind adapters/interfaces
- findings/evidence/results are normalized in shared models
- changes are testable and reviewable
- AI agents can reliably onboard and continue work using the repository docs

---

## Current phase

[Replace with actual current phase]

Suggested example:
- Phase: platform bootstrap + first feature implementation

---

## Notes for agents

Do not assume the entire long-term vision is already implemented.

Use:
- `docs/STATE.md` for what currently exists
- `docs/TASKS.md` for what should be done next
- `docs/CONVENTIONS.md` for implementation rules
- `docs/ARCHITECTURE.md` for deeper structure