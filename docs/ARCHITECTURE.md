# Architecture

This document describes the intended system architecture for the repository.

Its purpose is to define the structural model of the system, the major layers/modules, the dependency rules between them, and the extension model for future capabilities.

This document is architectural guidance, not a claim that every part is already implemented.

For current implementation reality, see `docs/STATE.md`.

---

## 1. Purpose of the architecture

This project is intended to become a modular security-analysis platform that can grow feature by feature without collapsing into a monolith or a collection of unrelated scripts.

The architecture exists to ensure that:

- new security-analysis capabilities can be added as independent modules
- shared business concepts are defined once and reused consistently
- output channels do not own business logic
- orchestration logic does not absorb feature-specific behavior
- LLM usage is isolated and controlled
- external tools are integrated through stable adapters
- the system remains testable, reviewable, and maintainable as it grows

---

## 2. Architectural goals

The architecture should optimize for the following goals:

- modularity
- incremental extensibility
- separation of concerns
- testability
- security-conscious design
- explicit boundaries
- adaptability to multiple output modes
- clean integration of external tools and optional LLM capabilities

The architecture should avoid:

- one-off feature implementations that become permanent structure
- UI-driven business logic
- orchestration layers that encode feature internals
- direct provider/tool coupling throughout the codebase
- hidden dependencies between vulnerability modules
- ad hoc output-specific data shaping in multiple places

---

## 3. High-level system model

The system is organized around a platform core with multiple surrounding layers and plugin-style modules.

At a high level, the flow is:

1. A user or caller initiates an analysis workflow through some presentation channel
2. The orchestration layer decides what workflow or sequence should run
3. The application/domain layer coordinates shared business behavior
4. One or more vulnerability check modules perform analysis
5. Check modules use tool adapters and optionally the LLM layer where required
6. Results are normalized into shared finding/evidence/result models
7. Presentation/reporting layers render the normalized output
8. Infrastructure handles persistence, queueing, configuration, secrets, and telemetry as needed

---

## 4. Core architectural layers

### 4.1 Presentation layer

The presentation layer is responsible for user interaction and output formatting.

Examples:
- CLI
- web UI
- API handlers
- report generation/rendering
- structured output serialization

Responsibilities:
- receive user input or invocation requests
- validate superficial request shape where appropriate
- invoke application or orchestration entry points
- present normalized results
- surface progress, status, and safe error messages

Non-responsibilities:
- vulnerability-specific detection logic
- core business policy
- deep workflow sequencing
- direct concrete tool usage
- direct LLM provider integration

Rules:
- keep this layer thin
- do not place business logic here
- presentation should consume normalized outputs from lower layers
- different presentation channels should not redefine domain semantics

---

### 4.2 Orchestration layer

The orchestration layer coordinates workflow execution.

Examples:
- scan workflow coordinator
- step sequencer
- job/run state manager
- policy/guardrail flow controller

Responsibilities:
- decide which actions should happen and in what order
- manage workflow progression and branching
- track execution context and run state
- coordinate calls to shared use cases, checks, and integrations
- enforce workflow-level guardrails or sequencing policies

Non-responsibilities:
- detailed vulnerability semantics
- UI behavior
- direct data rendering concerns
- provider-specific LLM handling
- embedding all business logic

Rules:
- orchestration should coordinate, not become the home for everything
- orchestration should call through stable interfaces
- adding a new vulnerability module should not require invasive orchestration rewrites
- workflow logic should remain inspectable and testable

---

### 4.3 Application / Domain layer

This is the core shared logic layer of the platform.

Responsibilities:
- define shared business concepts and models
- implement core use cases
- normalize findings/results/evidence
- apply common severity/confidence/policy logic
- coordinate shared behavior used by multiple features
- define common contracts used by vulnerability modules and renderers

Typical concepts may include:
- scan run
- target artifact
- finding
- evidence
- check result
- severity
- confidence
- policy decision
- report model

Rules:
- this is the preferred home for shared business semantics
- this layer should not depend on presentation details
- avoid leaking infrastructure concerns deeply into domain logic
- prefer explicit contracts and clear data models

This layer is one of the most important parts of the architecture because it enables modular growth across features.

---

### 4.4 LLM layer

The LLM layer isolates all model-related behavior.

Responsibilities:
- provider abstraction
- model configuration
- prompt/template construction
- response parsing
- structured output handling
- retries/timeouts
- validation of model output
- separation of trusted instructions from untrusted content

Non-responsibilities:
- owning all business decisions
- replacing domain logic
- direct rendering to end users
- serving as the project’s catch-all “AI logic” bucket

Rules:
- all LLM interaction should go through this layer
- do not scatter model-provider calls through unrelated modules
- model output should be treated as untrusted until validated
- prompt injection and context contamination must be considered when untrusted inputs are included

The LLM layer is a dependency of the platform, not the center of the architecture.

---

### 4.5 Vulnerability checks layer

This layer contains feature modules for individual vulnerability classes or analysis capabilities.

Examples:
- insecure storage checks
- weak cryptography checks
- auth/session checks
- network/TLS checks
- manifest/configuration checks
- future vulnerability-specific modules

Responsibilities:
- implement vulnerability-specific detection logic
- collect and shape evidence relevant to the check
- use shared domain models/contracts
- emit normalized results through the common model
- encapsulate logic specific to one analysis capability

Rules:
- each vulnerability class should be its own independently testable module
- modules should not depend on presentation concerns
- modules should not be tightly coupled to unrelated modules
- modules should emit shared normalized result types
- feature-specific logic should remain inside feature modules, not spread across the system

This layer is the main extension mechanism for adding new features over time.

---

### 4.6 Tool adapter layer

The tool adapter layer wraps concrete tools and external integrations.

Examples:
- APK/IPA parsers
- static analysis tools
- dynamic analysis tools
- filesystem readers
- mobile platform tooling
- external intelligence APIs
- archive extractors
- metadata parsers

Responsibilities:
- isolate concrete tool-specific behavior
- adapt external tool I/O into stable internal contracts
- shield the rest of the codebase from tool-specific quirks
- handle integration-specific error mapping where appropriate

Rules:
- do not call concrete tools ad hoc from many places
- prefer adapters/interfaces over raw direct usage
- avoid leaking provider-specific details into business logic
- keep integration behavior testable and replaceable

---

### 4.7 Infrastructure layer

The infrastructure layer supports runtime and operational concerns.

Examples:
- persistence/database access
- artifact storage
- queueing and worker execution
- configuration loading
- secret management
- telemetry/logging/metrics
- environment-specific bindings

Responsibilities:
- provide implementations of infrastructure dependencies
- handle persistence and retrieval
- handle runtime services and platform concerns
- expose operational building blocks to the rest of the system

Rules:
- infrastructure should support the domain/application layer without dominating it
- secret handling and configuration should be centralized and explicit
- logging and metrics should be designed intentionally rather than scattered

---

## 5. Dependency model

The intended dependency direction is:

- Presentation -> Orchestration / Application entry points
- Orchestration -> Application / Domain
- Application / Domain -> vulnerability module contracts, LLM abstractions, adapter abstractions, infrastructure abstractions
- Vulnerability modules -> shared domain models/contracts, adapter abstractions, LLM abstractions where appropriate
- Tool adapters -> concrete external tools
- Infrastructure -> implementations backing abstract dependencies

### Preferred direction summary

- upper layers depend on stable lower-level contracts
- business logic should not depend on rendering
- feature modules should depend on shared contracts, not presentation
- concrete implementations should sit behind interfaces or clearly bounded modules

### Disallowed or discouraged patterns

Avoid:
- Presentation -> vulnerability module direct coupling
- Presentation -> concrete LLM provider direct calls
- Presentation -> concrete tool integration direct calls
- vulnerability module -> presentation layer dependency
- arbitrary direct coupling between vulnerability modules
- orchestration containing all feature-specific implementation details
- repeated ad hoc prompt/provider logic across the system

If a task introduces a new dependency direction, it should be documented and justified.

---

## 6. Extension model

The platform is intended to grow by extension, not by repeated cross-cutting rewrites.

### 6.1 Adding a new vulnerability module

A new feature should usually involve:
- a new vulnerability module implementing the shared contract
- any module-specific evidence logic
- tests for that module
- registration or configuration for orchestration where required
- no or minimal change to unrelated modules

A new feature should not usually require:
- broad rewrites across unrelated features
- presentation-layer feature logic
- direct modification of multiple adapters unless genuinely necessary
- major orchestration rewrites

### 6.2 Adding a new presentation mode

A new output mode should usually involve:
- a new renderer or presentation adapter
- reuse of normalized shared result models
- minimal or no change to vulnerability modules

### 6.3 Adding a new external tool

A new external tool should usually involve:
- a new adapter or implementation module
- stable integration through existing contracts
- minimal effect on unrelated business logic

### 6.4 Adding or changing LLM usage

Changes to LLM use should usually involve:
- LLM layer changes
- prompt/template/parser changes
- validated structured outputs
- explicit documentation if the dependency or trust model changes

---

## 7. Shared contracts and common models

The architecture assumes the existence of shared contracts and normalized models.

Exact names may differ by implementation, but the system should converge toward concepts like:

### Shared contracts
- vulnerability check contract/interface
- workflow step or orchestration step contract
- LLM service/provider contract
- tool adapter contract
- report renderer contract
- configuration provider contract where relevant

### Shared models
- scan run
- target artifact
- finding
- evidence
- check result
- severity/confidence model
- normalized report/result envelope

These shared contracts are critical to extensibility.

If they are weak or inconsistent, the architecture will degrade quickly.

---

## 8. Error model expectations

Error handling should be explicit across layers.

### Recommended pattern
- presentation maps safe errors to user-facing output
- orchestration handles workflow-level failure behavior
- domain/application distinguishes meaningful business failures
- adapters map tool/provider failures into internal error types
- LLM layer distinguishes malformed output, provider failure, timeout, and validation failure

### Key principle
Do not silently swallow errors.
Do not flatten all failures into one generic response if the distinction matters for behavior, testing, or recovery.

---

## 9. Security and trust boundaries

This platform processes untrusted inputs and must assume adversarial or malformed content in many places.

Examples of untrusted input:
- user input
- uploaded artifacts
- code or binaries under analysis
- archives, metadata, and parser outputs
- external tool outputs
- LLM outputs
- external API responses

### Architectural security implications
- trust boundaries should be explicit
- validation should happen at relevant boundaries
- parser/tool isolation concerns should be considered
- command execution should be carefully wrapped
- secrets should not be scattered
- model output should not be treated as trusted by default
- logging must avoid accidental sensitive leakage

Refer to `docs/SECURITY_REQUIREMENTS.md` for more detailed controls.

---

## 10. Testing implications of the architecture

The architecture is designed to support testing at multiple levels.

### Unit testing should be straightforward for:
- domain logic
- normalization logic
- vulnerability module logic
- LLM output parsing/validation
- adapter behavior with mocked dependencies

### Integration testing should cover:
- orchestration to check interaction
- check to adapter interaction
- domain to persistence/reporting interaction where appropriate
- presentation to application entry path where useful

### Architectural benefit
If a change is difficult to test, that may indicate a boundary problem.

Refer to `docs/TEST_STRATEGY.md` for detailed expectations.

---

## 11. Architectural review questions

Use these questions to sanity-check changes:

- Does this change preserve clear ownership of logic?
- Is business behavior being moved into a wrong layer?
- Does this introduce hidden coupling between modules?
- Can the new feature be tested independently?
- Does the orchestration layer remain a coordinator rather than a dumping ground?
- Is LLM usage isolated appropriately?
- Can a new output channel reuse the same normalized result model?
- Can a new vulnerability module be added without widespread changes?

If the answer to several of these is “no,” re-evaluate the design.

---

## 12. Relationship to other docs

Use this document for:
- structural system shape
- layer definitions
- dependency rules
- extension model
- common contracts and boundaries

Use other documents for:
- `docs/PROJECT_BRIEF.md` -> project purpose
- `docs/STATE.md` -> current implementation reality
- `docs/TASKS.md` -> current work queue
- `docs/CONVENTIONS.md` -> implementation rules and workflow expectations
- `docs/DECISIONS.md` -> rationale for important design choices
- `docs/DESIGN_DOC.md` -> fuller intended product design and workflows

---

## 13. Summary

This architecture is intended to support a modular, incrementally built security-analysis platform.

Its core principles are:

- separate concerns clearly
- centralize shared business semantics
- treat vulnerability capabilities as independent modules
- isolate LLM usage
- isolate external tools through adapters
- normalize outputs for reuse across presentation channels
- preserve testability and extensibility as the system grows

This document defines the intended structural model.
It does not imply that every part is already fully implemented today.