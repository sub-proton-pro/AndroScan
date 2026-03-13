# Test Strategy

## Objectives
Ensure every feature is:
- correct
- modular
- resilient
- secure against expected misuse/failure conditions

## Required test levels

### Unit tests
Required for:
- business logic
- normalization logic
- policy/severity/confidence calculations
- vulnerability check logic

### Integration tests
Required for:
- orchestration to check integration
- check to adapter integration
- LLM layer integration paths if used
- persistence/reporting interactions where relevant

### Negative/security tests
Required for:
- invalid inputs
- malformed outputs
- timeouts/failures from dependencies
- hostile/untrusted content
- unsafe or edge-case conditions

## Acceptance criteria mapping
Each feature should map acceptance criteria to one or more tests.

## Minimum completion rule
Feature work is incomplete if tests are absent or superficial.