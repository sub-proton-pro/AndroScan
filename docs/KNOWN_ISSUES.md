# Known Issues

This document tracks meaningful known issues, architectural gaps, temporary limitations, and technical debt that should remain visible to contributors and AI agents.

Its purpose is to prevent known problems from becoming invisible or repeatedly rediscovered.

This file is not a substitute for `docs/TASKS.md`.
Use `docs/TASKS.md` for prioritized active work.
Use this file for persistent issues, limitations, and debt that matter to future work.

---

## 1. Purpose

This document exists to make important known problems explicit.

It should help answer:

- What is currently weak, incomplete, or risky?
- What limitations should new contributors not overlook?
- What temporary compromises exist in the current implementation?
- What debts are important enough to track even if they are not the active task today?

Use this file to preserve visibility.
Do not let important issues exist only as tribal knowledge or scattered TODOs.

---

## 2. How to use this document

Add an entry when a problem is:

- real
- relevant to future work
- important enough that a new contributor should know about it
- likely to affect architecture, correctness, security, testing, or maintainability

Do not add entries for:
- trivial style issues
- one-off local cleanup
- vague dissatisfaction with code quality
- items better represented as an active task in `docs/TASKS.md`

When an issue is fixed:
- update or remove the entry
- add follow-up notes if useful
- reflect the change in `docs/STATE.md` if current reality changed materially

---

## 3. Status labels

Use one of the following labels:

- Open
- In Progress
- Mitigated
- Resolved
- Accepted Limitation

---

## 4. Severity / impact labels

Use one of the following labels where helpful:

- High
- Medium
- Low

Impact can refer to:
- architecture
- security
- correctness
- operability
- testing confidence
- future extensibility

---

## 5. Issue template

Use the following format for new entries:

### ISSUE-XXX: [Title]
- status:
- impact:
- area:
- introduced / observed:
- summary:
- why it matters:
- current workaround:
- recommended fix:
- related tasks:
- related docs:

---

## 6. Known issues

### ISSUE-001: Current implementation may lag behind documented target architecture
- status: Open
- impact: Medium
- area: architecture / documentation
- introduced / observed: [replace date]
- summary:
  The documentation describes the intended long-term architecture, but the codebase may still contain transitional or legacy patterns that do not fully match it.
- why it matters:
  New contributors or AI agents may incorrectly assume the current code already matches the desired structure.
- current workaround:
  Use `docs/STATE.md` as the source of truth for current implementation status, and call out doc/code mismatches explicitly.
- recommended fix:
  Continue converging code toward documented architecture and keep `docs/STATE.md` updated.
- related tasks:
  - architecture alignment tasks
- related docs:
  - `docs/STATE.md`
  - `docs/ARCHITECTURE.md`

---

### ISSUE-002: Normalized finding/evidence/result model may still evolve
- status: Open
- impact: High
- area: domain model / reporting / extensibility
- introduced / observed: [replace date]
- summary:
  The shared result model is expected to support multiple vulnerability modules and output channels, but early versions may be incomplete or subject to change as real features are added.
- why it matters:
  Premature assumptions about model completeness can create brittle feature modules or renderers.
- current workaround:
  Treat the shared model as intentional but evolving. Update carefully when real feature requirements reveal missing structure.
- recommended fix:
  Stabilize the model through real feature additions and contract-focused tests.
- related tasks:
  - first feature model refinement
  - renderer contract validation
- related docs:
  - `docs/ARCHITECTURE.md`
  - `docs/TEST_STRATEGY.md`
  - `docs/DECISIONS.md`

---

### ISSUE-003: Orchestration layer may be at risk of absorbing feature-specific logic over time
- status: Open
- impact: High
- area: architecture / extensibility
- introduced / observed: [replace date]
- summary:
  As multiple features are added, there is a risk that workflow coordination code accumulates feature-specific branching and becomes the place where business logic lives.
- why it matters:
  This would make new features harder to add, reduce clarity, and weaken modularity.
- current workaround:
  Review new feature work for orchestration bloat and keep feature semantics inside vulnerability modules or shared domain logic.
- recommended fix:
  Periodically review orchestration boundaries and refactor misplaced semantics out.
- related tasks:
  - architecture review after each feature
- related docs:
  - `docs/ARCHITECTURE.md`
  - `docs/CONVENTIONS.md`

---

### ISSUE-004: LLM usage patterns may be underdefined until first real LLM-backed feature exists
- status: Open
- impact: Medium
- area: LLM layer / security / contracts
- introduced / observed: [replace date]
- summary:
  The architecture reserves a dedicated LLM layer, but exact usage patterns, contracts, and failure behavior may remain underdefined until a real feature uses it.
- why it matters:
  Without concrete usage, contributors may improvise inconsistent patterns when the first LLM-assisted feature is added.
- current workaround:
  Keep all future LLM access centralized and require explicit contract/validation design before usage expands.
- recommended fix:
  Define the first canonical LLM-backed workflow and document its contracts, validation rules, and provenance behavior.
- related tasks:
  - first LLM-assisted feature design
- related docs:
  - `docs/ARCHITECTURE.md`
  - `docs/SAFETY_AND_SECURITY.md`
  - `docs/DECISIONS.md`

---

### ISSUE-005: Test coverage may be uneven during early platform bootstrap
- status: Open
- impact: Medium
- area: testing / confidence
- introduced / observed: [replace date]
- summary:
  Early platform scaffolding may exist before all critical paths have strong unit, negative, and integration coverage.
- why it matters:
  Contributors may overestimate confidence in the platform core or feature modules.
- current workaround:
  Treat untested behavior as unverified. Require new work to add tests and identify gaps explicitly in task completion summaries.
- recommended fix:
  Expand coverage around domain logic, module contracts, orchestration paths, and failure handling as features are added.
- related tasks:
  - add contract and negative tests
- related docs:
  - `docs/TEST_STRATEGY.md`
  - `docs/STATE.md`

---

### ISSUE-006: Documentation drift is a recurring operational risk
- status: Open
- impact: Medium
- area: project operability / contributor onboarding
- introduced / observed: [replace date]
- summary:
  The repository relies on docs as persistent project memory for both humans and AI agents. If docs are not updated consistently, agents may act on stale assumptions.
- why it matters:
  Stale docs reduce the value of the entire operating model and can cause repeated confusion or incorrect implementation.
- current workaround:
  Require doc updates or explicit doc follow-up notes whenever behavior, state, or decisions change materially.
- recommended fix:
  Keep docs part of the definition of done for meaningful tasks.
- related tasks:
  - doc hygiene tasks
- related docs:
  - `docs/AGENT_PROTOCOL.md`
  - `docs/STATE.md`
  - `docs/TASKS.md`
  - `docs/CONVENTIONS.md`

---

### ISSUE-007: Security controls may be documented before fully implemented
- status: Accepted Limitation
- impact: Medium
- area: security / maturity
- introduced / observed: [replace date]
- summary:
  The security requirements document may define desired safeguards before all of them are implemented in code.
- why it matters:
  Contributors may mistake documented expectations for delivered controls.
- current workaround:
  Use `docs/STATE.md` to distinguish current reality from desired posture, and avoid claiming security guarantees that are not implemented or tested.
- recommended fix:
  Convert important security requirements into concrete tasks and tests over time.
- related tasks:
  - validation hardening
  - adapter safety tests
  - LLM output validation
- related docs:
  - `docs/SAFETY_AND_SECURITY.md`
  - `docs/STATE.md`
  - `docs/TASKS.md`

---

### ISSUE-008: Adapter boundaries may be bypassed by expedient feature work
- status: Open
- impact: Medium
- area: architecture / maintainability
- introduced / observed: [replace date]
- summary:
  Contributors may be tempted to call concrete tools or providers directly from feature modules or orchestration for speed.
- why it matters:
  This weakens replaceability, testing, and architectural clarity.
- current workaround:
  Treat adapter bypass as an architectural smell and call it out in reviews.
- recommended fix:
  Preserve adapter contracts and refactor bypasses quickly if they appear.
- related tasks:
  - adapter boundary enforcement
- related docs:
  - `docs/ARCHITECTURE.md`
  - `docs/CONVENTIONS.md`
  - `docs/DECISIONS.md`

---

## 7. Accepted limitations

Use this section for limitations that are currently acceptable and not immediate defects.

Example categories:
- no web UI yet
- CLI-first presentation mode only
- persistence intentionally deferred
- first feature uses simplified orchestration
- report rendering supports one format only

Keep these explicit so they are not mistaken for bugs or forgotten assumptions.

### Example entry format

### LIMIT-XXX: [Title]
- status: Accepted Limitation
- reason:
- impact:
- revisit when:
- related docs:

Replace this section with real project-specific accepted limitations over time.

---

## 8. Resolved issues

Move resolved entries here if keeping historical memory is useful.

Format:

### ISSUE-XXX: [Title]
- status: Resolved
- resolved date:
- resolution summary:
- related tasks/docs:

Leave empty until needed.

---

## 9. Review prompts

Use these questions when deciding whether to add an issue here:

- Is this problem likely to matter to future work?
- Could a new contributor make a bad decision if they do not know about it?
- Does this issue affect architecture, security, correctness, testing, or maintainability?
- Is this more persistent than a normal task?
- Is this better tracked here than as a transient TODO?

If yes, it likely belongs here.

---

## 10. Relationship to other docs

Use this document for:
- persistent known problems
- architectural risks
- accepted limitations
- important technical debt

Use:
- `docs/TASKS.md` for active prioritized work
- `docs/STATE.md` for current implementation truth
- `docs/DECISIONS.md` for rationale
- `docs/ARCHITECTURE.md` for intended structure
- `docs/SAFETY_AND_SECURITY.md` for desired security posture
- `docs/TEST_STRATEGY.md` for testing expectations

---

## 11. Summary

This document keeps known issues visible.

It exists to prevent:
- repeated rediscovery of known problems
- silent acceptance of architectural drift
- false assumptions about maturity or completeness
- loss of important context across contributors and AI tools

Keep it current enough to be useful, but focused enough to stay readable.