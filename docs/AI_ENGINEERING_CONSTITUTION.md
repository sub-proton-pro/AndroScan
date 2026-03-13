# AI Engineering Constitution

This repository is built incrementally, one feature at a time, into a long-lived modular security-analysis platform.

These rules are mandatory for all code generation, refactoring, and design changes.

## 1. Development model
- Do not build the whole application in one shot.
- Build one feature/module at a time.
- Each feature must fit the long-term platform architecture.
- Do not take shortcuts in early features that block future extensibility.

## 2. Architectural principles
- Maintain clear separation between:
  - Presentation layer
  - AI agent orchestration layer
  - Application/domain layer
  - LLM layer
  - Vulnerability checks layer
  - Tool adapter layer
  - Infrastructure layer
- No business logic in presentation code.
- No direct LLM-provider calls outside the LLM layer.
- No direct tool-specific coupling outside adapter boundaries.
- Each vulnerability class must be implemented as an independent module behind a shared interface/contract.

## 3. Incremental feature rules
For every new feature:
- restate the goal
- define acceptance criteria
- identify edge cases
- identify abuse/security cases
- explain architecture fit
- implement tests
- update docs if architecture or design changes

## 4. Security rules
- Treat all external input as untrusted.
- Validate inputs at all trust boundaries.
- Use secure defaults.
- Never hardcode secrets.
- Avoid unsafe shell composition and injection-prone patterns.
- Do not log secrets or sensitive payloads.
- Make trust boundaries explicit.
- If LLMs consume untrusted content, account for prompt injection and output validation.

## 5. Error handling rules
- Error handling must be explicit.
- Do not swallow exceptions silently.
- Use typed/domain-specific errors where appropriate.
- Handle invalid input, tool failures, partial failures, timeout cases, and malformed outputs.
- Return safe but useful errors.

## 6. Testing rules
- Every feature must include tests.
- Business logic requires unit tests.
- Key workflows require integration tests.
- Invalid/untrusted input requires negative tests.
- Important failure paths must be tested.
- Architecture/design is not complete if tests are missing.

## 7. Documentation rules
Maintain and update when relevant:
- ARCHITECTURE.md
- SAFETY_AND_SECURITY.md
- TEST_STRATEGY.md
- ADRs in docs/adr/
- feature specs / acceptance criteria

## 8. Definition of done
A feature is not done unless:
- it respects architectural boundaries
- it is modular and testable
- acceptance criteria are satisfied
- tests are added
- security considerations are addressed
- error handling is implemented
- documentation is updated as needed

## 9. Forbidden behaviors
- Do not optimize only for “working code”.
- Do not bypass architecture for speed.
- Do not embed feature-specific logic in unrelated layers.
- Do not add hidden coupling that will make future features harder.
- Do not mark work complete if tests/docs/security review are missing.

## 10. Required output behavior for AI coding sessions
Before implementation:
1. summarize the feature
2. list affected layers/modules
3. list acceptance criteria
4. list edge/security cases
5. propose implementation plan

After implementation:
1. summarize files changed
2. map acceptance criteria to implementation
3. list tests added
4. identify known gaps or technical debt
5. verify conformance with this constitution