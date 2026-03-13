# Conventions

This document defines the repository’s implementation rules, architectural conventions, workflow expectations, and coding discipline.

These conventions are authoritative.

If existing code violates them, do not copy the violation forward unless there is a compelling documented reason.

---

## 1. Core principles

This codebase is built as a long-lived modular platform.

Optimize for:
- maintainability
- modularity
- testability
- explicitness
- reviewability
- security
- incremental feature growth

Do not optimize only for:
- minimum code to satisfy the current prompt
- shortest path to a passing demo
- convenience at the expense of architecture

---

## 2. Development model

### Feature-by-feature development
The system is built incrementally.
Implement one feature/module at a time.

Each feature must:
- fit the long-term architecture
- preserve extension points
- avoid locking the repo into one-off design decisions
- include tests
- update docs when relevant

### Build the platform while building the feature
Do not overengineer for hypothetical future needs.
Do not underengineer in ways that block the next real feature.

The standard is:
- just enough structure for current and near-next work
- no shortcuts that create avoidable rework

---

## 3. Architectural boundaries

The intended architecture separates concerns across layers.

### Presentation layer
Examples:
- CLI
- web UI
- API handlers
- report renderers

Rules:
- no business logic
- no vulnerability-specific logic
- no direct provider/tool logic unless explicitly designed as a presentation concern
- consume normalized outputs from lower layers

### Orchestration layer
Responsibilities:
- workflow sequencing
- dependency ordering
- execution progression
- job/run coordination
- high-level policy/guardrails if applicable

Rules:
- do not embed detailed vulnerability semantics here
- do not hardcode check-specific business rules unless unavoidable and documented
- orchestrate through stable contracts/interfaces

### Application / Domain layer
Responsibilities:
- core business logic
- shared use cases
- finding/evidence/result models
- normalization logic
- policy/severity/confidence logic

Rules:
- this is the preferred home for shared business semantics
- must not depend on presentation details

### LLM layer
Responsibilities:
- provider abstraction
- prompt building
- response parsing
- retries/timeouts/configuration
- structured output validation

Rules:
- all LLM interaction goes through this layer
- do not scatter provider-specific logic through the codebase
- do not use raw model output without validation where structure matters

### Vulnerability checks layer
Responsibilities:
- one module per vulnerability class
- detection logic for that class
- evidence collection specific to that class
- mapping outputs into normalized shared result models

Rules:
- each module should be independently testable
- modules should not depend on presentation concerns
- modules should not know details of unrelated feature modules
- avoid cross-module coupling

### Tool adapter layer
Responsibilities:
- wrap concrete tools, parsers, analyzers, or external systems

Rules:
- do not call concrete tools ad hoc from many places
- use adapters or interfaces
- isolate provider/tool-specific behavior

### Infrastructure layer
Responsibilities:
- persistence
- storage
- queueing
- configuration
- secrets
- telemetry

Rules:
- keep infrastructure concerns from leaking into business logic where possible
- use abstractions where appropriate

---

## 4. Dependency rules

Prefer dependency direction like this:

- presentation -> orchestration/application
- orchestration -> application/domain
- application/domain -> check interfaces / adapters / LLM abstractions / infra abstractions
- infrastructure provides implementations

Avoid:
- presentation -> vulnerability modules directly
- presentation -> LLM provider directly
- vulnerability module -> presentation
- arbitrary direct calls across unrelated feature modules

If a task requires a new dependency direction, document and justify it.

---

## 5. Modularity rules

Each new feature should be implemented as if it is one module in a long-lived platform.

This means:
- clear ownership
- clear interfaces
- no hidden coupling
- no broad invasive rewrites unless justified
- common contracts for shared behavior
- normalized output models where appropriate

Do not let the first working implementation become the accidental permanent architecture.

---

## 6. Coding style expectations

These are language-agnostic defaults unless a language-specific standard is defined elsewhere.

### Prefer:
- explicit naming
- small focused units
- clear interfaces
- deterministic behavior where possible
- readable control flow
- meaningful error handling
- testable design

### Avoid:
- overly clever abstractions
- feature logic mixed into wiring code
- giant multifunction files
- magic constants without explanation
- hidden side effects
- fragile implicit coupling

Use existing local style where it is good and consistent with repo conventions.

---

## 7. Error handling conventions

Error handling is required, not optional.

### Rules
- do not silently swallow exceptions
- handle expected failure modes explicitly
- distinguish invalid input from dependency failure where useful
- return safe but useful error information
- standardize error shapes/types where practical
- document non-obvious failure behavior

### Common failure types to think about
- invalid user input
- malformed artifacts
- parser/tool failure
- timeout
- missing configuration
- unexpected external responses
- invalid LLM output
- partial workflow failure

Add tests for important failure paths.

---

## 8. Security conventions

Treat security as a design constraint, not cleanup work.

### Always assume untrusted input for:
- user input
- uploaded files
- source code being analyzed
- archives and artifacts
- tool outputs
- LLM outputs
- external API responses

### Required behavior
- validate inputs at trust boundaries
- normalize/sanitize paths, identifiers, and externally provided values
- avoid command injection patterns
- avoid unsafe dynamic execution
- do not hardcode secrets
- do not log sensitive data carelessly
- define trust boundaries where needed
- validate structured model output before downstream use

If the task touches sensitive flows, consult `docs/SAFETY_AND_SECURITY.md`.

---

## 9. Testing conventions

Every meaningful feature requires tests.

### Minimum expectations
- unit tests for business logic
- tests for normalization logic
- integration tests for key interactions or workflows where relevant
- negative tests for invalid or hostile input
- tests for major failure paths

### Completion rule
A feature is not done if:
- behavior changed but tests did not
- only happy-path behavior is covered
- architecture-critical logic is untested

If needed, consult `docs/TEST_STRATEGY.md`.

---

## 10. Documentation conventions

Update docs when they are affected by the change.

Typical docs that may require updates:
- `docs/STATE.md`
- `docs/TASKS.md`
- `docs/ARCHITECTURE.md`
- `docs/DECISIONS.md`
- `docs/SAFETY_AND_SECURITY.md`
- `docs/TEST_STRATEGY.md`

Update docs when:
- current state changed
- task status changed
- architecture changed
- a meaningful design decision was made
- a new limitation or debt item should be recorded

Do not leave important context only inside code.

---

## 11. Decision logging

When a significant design decision is made, record it in:
- `docs/DECISIONS.md`, or
- the ADR mechanism if one exists in this repo

Examples of decisions worth recording:
- new module boundary
- new interface/contract
- new dependency direction
- choosing one adapter pattern over another
- changing result normalization shape
- introducing or restricting LLM usage
- selecting an orchestration pattern

---

## 12. Workflow expectations for agents

Before coding:
- restate the task
- identify relevant layers/files
- identify risks and edge cases
- identify tests to add
- note any doc impact

After coding:
- summarize files changed
- summarize what was implemented
- list tests added/updated
- list docs updated/needed
- note known limitations or follow-ups

Do not declare work complete without this summary.

---

## 13. What not to do

Do not:
- code before reading startup docs
- treat stale local code as the best pattern automatically
- bypass architecture for convenience
- mix feature logic into presentation/wiring code
- couple one vulnerability module tightly to another
- introduce hidden dependencies
- mark work done without tests
- silently ignore doc/code drift
- broaden scope unnecessarily

---

## 14. Default rule when uncertain

When uncertain:
- choose the path that preserves modularity
- choose the path that preserves testability
- choose the path that keeps responsibilities clear
- document the uncertainty if it matters
- avoid shortcuts that future tasks will have to undo