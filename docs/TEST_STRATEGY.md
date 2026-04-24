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

---

## Web UI and dynamic instrumentation (planned Phases 6–9)

When the **Interactive RE Workbench** lands:

- **REST / WebSocket handlers:** unit tests with FastAPI `TestClient` and mocked subprocess/ADB where possible; avoid requiring a live emulator in default CI.
- **Frontend:** prefer component/unit tests for parsers (e.g. uiautomator XML) in TypeScript; E2E browser tests optional and not a default CI gate unless explicitly added.
- **Call graph (Smali):** unit tests on **fixture Smali** snippets; golden or snapshot tests for small graphs; large-APK stress tests manual or opt-in job.
- **Frida:** default CI uses **mocks** for the adapter and hook template rendering; **device attach / hook / trace** integration tests are **opt-in** (developer machine or dedicated runner with emulator + frida-server).

See `docs/TASKS.md` § Interactive RE Workbench and `docs/DECISIONS.md` DEC-015–017.