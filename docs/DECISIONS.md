# Decisions

This document records important architectural, design, and implementation decisions for the repository.

Its purpose is to preserve rationale over time so that future contributors and AI agents can understand:

- what decision was made
- why it was made
- what alternatives were considered
- what tradeoffs were accepted
- whether the decision is still active

This is a lightweight decision log.
If the repository later adopts formal ADR files, this document can coexist with or index them.

---

## How to use this document

Read this document when:
- you need to understand why the system looks the way it does
- a design choice appears non-obvious
- you are considering changing an existing pattern
- you are unsure whether a constraint was intentional

Update this document when:
- a meaningful architectural or design decision is made
- a prior decision is reversed or superseded
- a tradeoff should be preserved for future contributors

Do not use this document for trivial edits or routine code changes.

---

## Status labels

Use one of the following status values for each decision:

- Active
- Superseded
- Deprecated
- Proposed

---

## Decision template

Use the following structure for new entries:

### DEC-XXX: [Title]
- status:
- date:
- owners:
- context:
- decision:
- rationale:
- alternatives considered:
- tradeoffs / consequences:
- follow-up:
- related docs:

---

## Decision log

### DEC-001: Build the project as a modular platform, not a one-shot application
- status: Active
- date: [replace date]
- owners: [replace owner(s)]
- context:
  The project is intended to support multiple security-analysis capabilities over time. A one-shot feature-oriented build would likely create coupling and rework as additional vulnerability classes and output modes are added.
- decision:
  The repository will be built as a long-lived modular platform that grows feature by feature.
- rationale:
  This reduces future rework, encourages clean interfaces, and supports incremental expansion of analysis capabilities.
- alternatives considered:
  - Build the whole app in one pass
  - Build an initial monolith and refactor later
  - Build isolated scripts per capability
- tradeoffs / consequences:
  - Requires upfront structure and discipline
  - Slightly slower initial development
  - Better long-term maintainability and extensibility
- follow-up:
  Preserve this direction in architecture and conventions docs.
- related docs:
  - `docs/PROJECT_BRIEF.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CONVENTIONS.md`

---

### DEC-002: Develop one feature at a time against the platform architecture
- status: Active
- date: [replace date]
- owners: [replace owner(s)]
- context:
  AI-assisted coding tools tend to optimize for immediate feature completion unless constrained. This can lead to poor architecture and weak reuse.
- decision:
  New functionality will be implemented one feature/module at a time, but always within the framework of the shared architecture.
- rationale:
  This allows controlled incremental progress without sacrificing long-term structure.
- alternatives considered:
  - Implement many features together
  - Delay architecture until several features exist
  - Treat early features as disposable prototypes
- tradeoffs / consequences:
  - Requires strong task discipline
  - Requires shared contracts to be introduced early
  - Reduces risk of architecture drift
- follow-up:
  Ensure tasks remain scoped and feature-specific.
- related docs:
  - `docs/TASKS.md`
  - `docs/CONVENTIONS.md`
  - `docs/ARCHITECTURE.md`

---

### DEC-003: Separate presentation, orchestration, domain/application, LLM, vulnerability, adapter, and infrastructure concerns
- status: Active
- date: [replace date]
- owners: [replace owner(s)]
- context:
  The platform must support multiple output modes, multiple feature modules, and possible LLM/tool integrations without creating a tangled system.
- decision:
  The architecture will use explicit layer separation across presentation, orchestration, application/domain, LLM, vulnerability checks, tool adapters, and infrastructure.
- rationale:
  This creates clear ownership of responsibilities and limits cross-cutting coupling.
- alternatives considered:
  - Merge orchestration and domain logic
  - Treat LLM behavior as generic business logic scattered through modules
  - Put feature logic close to the UI or entrypoint for speed
- tradeoffs / consequences:
  - Slightly more initial ceremony
  - Better modularity and testing boundaries
  - Easier future addition of features and output channels
- follow-up:
  Revisit boundaries if real usage shows unnecessary fragmentation.
- related docs:
  - `docs/ARCHITECTURE.md`
  - `docs/CONVENTIONS.md`

---

### DEC-004: Treat vulnerability capabilities as independently testable modules
- status: Active
- date: [replace date]
- owners: [replace owner(s)]
- context:
  The project’s long-term value depends on being able to add multiple vulnerability classes without excessive cross-cutting changes.
- decision:
  Each vulnerability capability should be implemented as an independent module behind a shared contract where practical.
- rationale:
  This makes future additions more predictable, testable, and isolated.
- alternatives considered:
  - Put feature logic directly into the orchestration path
  - Maintain one generic analyzer with large internal branching
  - Couple checks tightly to output or UI logic
- tradeoffs / consequences:
  - Requires shared result models and interfaces
  - Encourages cleaner extension model
  - May require a small amount of upfront abstraction
- follow-up:
  Ensure new features conform to the common contract and normalized result model.
- related docs:
  - `docs/ARCHITECTURE.md`
  - `docs/CONVENTIONS.md`

---

### DEC-005: Centralize all LLM interactions in a dedicated LLM layer
- status: Active
- date: [replace date]
- owners: [replace owner(s)]
- context:
  If model calls are made ad hoc throughout the codebase, configuration, retries, parsing, security concerns, and provider-specific behavior become difficult to control.
- decision:
  All LLM interactions will go through a dedicated LLM layer or service abstraction.
- rationale:
  This centralizes provider behavior, enables output validation, and keeps model usage from leaking into unrelated layers.
- alternatives considered:
  - Direct provider calls from each feature module
  - Prompt and parsing logic embedded where needed
  - Treat model calls as simple helper utilities without clear ownership
- tradeoffs / consequences:
  - Requires shared abstraction design
  - Reduces flexibility for fast ad hoc experimentation
  - Improves consistency, observability, and security posture
- follow-up:
  Define clear contracts for structured outputs and validation behavior.
- related docs:
  - `docs/ARCHITECTURE.md`
  - `docs/SAFETY_AND_SECURITY.md`

---

### DEC-006: Use normalized shared finding/evidence/result models across features and output channels
- status: Active
- date: [replace date]
- owners: [replace owner(s)]
- context:
  The platform may support multiple output channels and multiple vulnerability modules. If each feature or renderer uses its own result shape, reuse will be weak and reporting logic will fragment.
- decision:
  The system should converge on normalized shared models for findings, evidence, and result/report structures.
- rationale:
  This allows multiple checks to emit compatible outputs and multiple renderers to consume the same normalized structure.
- alternatives considered:
  - Each module defines its own output shape
  - Each renderer reshapes data independently
  - Normalization happens only at the UI/report layer
- tradeoffs / consequences:
  - Requires early investment in common models
  - May need revision as real features expose missing fields
  - Strongly improves interoperability and modularity
- follow-up:
  Update models carefully when new real requirements appear.
- related docs:
  - `docs/ARCHITECTURE.md`
  - `docs/TEST_STRATEGY.md`

---

### DEC-007: Use repository docs as authoritative project memory for humans and AI agents
- status: Active
- date: [replace date]
- owners: [replace owner(s)]
- context:
  AI coding agents have limited memory and may change across sessions or tools. Humans also need a predictable way to understand project context.
- decision:
  The repository will maintain structured docs for brief, state, tasks, conventions, architecture, decisions, and design.
- rationale:
  This provides tool-agnostic continuity, reduces reliance on ephemeral prompts, and makes onboarding consistent.
- alternatives considered:
  - Rely only on prompts
  - Store context only in inline code comments
  - Use ad hoc notes with no standard structure
- tradeoffs / consequences:
  - Requires documentation upkeep
  - Greatly improves continuity and project operability
- follow-up:
  Keep docs current as implementation evolves.
- related docs:
  - `docs/AGENT_PROTOCOL.md`
  - `docs/PROJECT_BRIEF.md`
  - `docs/STATE.md`
  - `docs/TASKS.md`
  - `docs/CONVENTIONS.md`

---

### DEC-008: Treat documentation of current state separately from target-state design
- status: Active
- date: [replace date]
- owners: [replace owner(s)]
- context:
  A common source of confusion in iterative projects is mixing “what exists today” with “what we want eventually.”
- decision:
  Current reality and intended design will be maintained in separate documents.
- rationale:
  This prevents contributors and AI agents from coding against assumptions that are not yet true.
- alternatives considered:
  - Use one large design/status document
  - Infer implementation state from code only
  - Keep only a target-state architecture document
- tradeoffs / consequences:
  - Requires maintaining more than one doc
  - Significantly reduces confusion and mistaken assumptions
- follow-up:
  Keep `STATE.md` honest and current.
- related docs:
  - `docs/STATE.md`
  - `docs/DESIGN_DOC.md`
  - `docs/ARCHITECTURE.md`

---

### DEC-009: Require tests for meaningful feature work
- status: Active
- date: [replace date]
- owners: [replace owner(s)]
- context:
  AI-generated or AI-assisted code tends to over-index on happy-path implementation unless tests are a hard requirement.
- decision:
  Meaningful feature work is incomplete without relevant tests.
- rationale:
  This improves confidence, prevents regressions, and forces clearer design.
- alternatives considered:
  - Add tests later
  - Test only manually in early phases
  - Require tests only for selected modules
- tradeoffs / consequences:
  - Slightly increases task overhead
  - Strongly improves reliability and review quality
- follow-up:
  Maintain minimum testing expectations in `docs/TEST_STRATEGY.md`.
- related docs:
  - `docs/CONVENTIONS.md`
  - `docs/TEST_STRATEGY.md`

---

### DEC-010: Prefer adapter-wrapped tool integration over direct concrete tool usage
- status: Active
- date: [replace date]
- owners: [replace owner(s)]
- context:
  Security-analysis tooling often integrates with parsers, scanners, and external executables or services. Direct usage in many parts of the codebase creates coupling and brittle behavior.
- decision:
  Concrete tools and integrations should be wrapped behind adapters or clearly bounded integration modules.
- rationale:
  This isolates tool-specific behavior, simplifies replacement and testing, and reduces leakage of external complexity into domain logic.
- alternatives considered:
  - Direct concrete tool usage from feature modules
  - Utility helpers used everywhere with no adapter boundary
- tradeoffs / consequences:
  - Requires a little more structure early
  - Greatly improves replaceability and clarity
- follow-up:
  Watch for drift where code bypasses adapter boundaries.
- related docs:
  - `docs/ARCHITECTURE.md`
  - `docs/CONVENTIONS.md`

---

## Superseded / deprecated decisions

Use this section when a previous decision is replaced.

Example format:

### DEC-XXX: [Title]
- status: Superseded
- superseded by:
- note:

Leave empty until needed.

---

## Decision hygiene rules

Record a decision when:
- a new system boundary is introduced
- a major tradeoff is accepted
- a pattern is chosen over plausible alternatives
- a future contributor may reasonably ask “why is it done this way?”

Do not record:
- minor naming choices
- routine refactors
- trivial bug fixes
- purely local implementation details with no lasting significance

---

## Relationship to other docs

Use this document for rationale.

Use:
- `docs/ARCHITECTURE.md` for structure
- `docs/CONVENTIONS.md` for working rules
- `docs/STATE.md` for what currently exists
- `docs/DESIGN_DOC.md` for broader intended design
- ADR files if a more formal decision record is later adopted

---

## Summary

This document exists to preserve design memory.

It helps humans and AI agents understand not just what the system is, but why it was shaped that way.