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
- web UI (planned: **Phase 6+** local-only FastAPI + React “RE Workbench” — mirror, logcat, browse runs; later phases add graph + Frida controls)
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

**Planned web UI (Phases 6–9):** The server is a **presentation** concern: it serves dossier/report JSON and streams device/UI events over WebSockets. It must **not** embed vulnerability detection logic or LLM prompts; it invokes orchestration and existing skills/adapters (same dependency direction as CLI). Default bind **127.0.0.1**; baseline posture is local single-user (see `docs/SAFETY_AND_SECURITY.md`).

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
- after LLM-produced hypotheses, drive optional exploit verification (emulator/ADB, exploit-tier skills) before final reporting where the product requires it

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

### 4.3 Skills layer

The skills layer provides discrete, reusable capabilities that orchestration and the LLM can invoke through a single registry.

Examples:
- pipeline skills: extract_manifest, prepare_dossier, generate_report
- LLM-requestable skills: get_decompiled_class, get_decompiled_method, list_classes_in_package, **search_decompiled_sources** (Lane-1 RAG over the jadx output via `androscan.rag`) (planned: `resolve_ui_element`, `query_call_graph`, `generate_frida_hook` — Phases 7–9)
- exploit-tier skills (orchestration only; not in the LLM catalog): app_env_check, build_exploit_command, capture_signals, run_exploit_command, verify_exploit_result

Responsibilities:
- define the skill contract (SkillMeta, SkillContext, SkillResult)
- register and discover skills; execute by name
- expose only LLM-requestable (**llm** tier) skills to the prompt catalog (`list_llm_skills`); **exploit**-tier skills are never advertised to the model
- isolate tool-specific behavior (e.g. apktool, jadx) inside skill implementations

Non-responsibilities:
- workflow sequencing (orchestration decides order of pipeline skills)
- LLM provider or prompt construction (LLM layer uses skill catalog)
- business models like Dossier (domain layer)

Rules:
- each skill is a single file exporting SKILL_META and execute(params, context)
- tier is one of `"pipeline"`, `"llm"`, or `"exploit"`; pipeline and exploit skills are not advertised to the LLM; only **llm** tier is
- vulnerability modules may call execute() or run_skills() as needed

---

### 4.4 Application / Domain layer

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

### 4.5 LLM layer

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

### 4.6 Vulnerability checks layer

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

### 4.7 Tool adapter layer

The tool adapter layer wraps concrete tools and external integrations.

Examples:
- APK/IPA parsers
- static analysis tools
- dynamic analysis tools (planned: **Frida** client / frida-server lifecycle — Phase 9; keep behind a dedicated adapter)
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

**Planned Frida adapter (Phase 9):** Attach/load/detach scripts; stream messages to the web UI over WebSocket channels; optional new exploit-verification **signal** type (e.g. `frida_trace`) in `vuln_module_skills_signals.json`. LLM-generated hooks are **untrusted** until validated; user confirmation before deploy is a product rule (see `docs/DECISIONS.md` DEC-017).

---

### 4.8 Infrastructure layer

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

### 4.10 Retrieval / RAG layer (Lane-1 — implemented)

**Purpose:** Provide semantic retrieval over the per-app jadx decompiled sources so that the Inspect-tab chat and the `search_decompiled_sources` LLM skill can ground answers in concrete code without dumping the whole corpus into the context window.

**Placement:** `androscan/rag/` (`chunking.py`, `embed.py`, `index.py`, `search.py`). It is **infrastructure** in the architectural sense — it stores derived data and serves queries — and depends only on the dossier/run-folder contracts. It does **not** implement vulnerability logic and is not a presentation concern. Web routes live in `androscan/web/rag_routes.py` (presentation wiring); the LLM skill `search_decompiled_sources` lives under `androscan/skills/` and goes through the existing skill registry.

**Outputs:** A per-APK SQLite database at `apps/<app_id>/.decompiled/<sha>/rag.sqlite` (WAL; packed `float32` BLOB column). One DB per APK SHA — invalidation = drop the file. The schema is intentionally vector-ready so that `sqlite-vec` (or another ANN backend) can replace the brute-force scan without changing call sites.

**Embedding providers:** `EmbedProvider` protocol with `FastEmbedProvider` (default), `OllamaEmbedProvider`, and a deterministic `HashProvider` for tests / no-deps environments. Provider selection is config-driven (`rag.embed_provider`, `ANDROSCAN_RAG_PROVIDER`).

**Rules:**
- treat the index as **derived data**: it must be safe to delete and rebuild from the decompile cache at any time
- builds run in a daemon thread; the rest of the product **fails-open** if the index is missing or rebuilding
- the LLM never receives raw decompiled directories; it receives bounded, sanitized chunks routed through the chat guardrails (`code` attachment kind, per-kind budget, `<context>` wrapping)
- new retrieval surfaces must reuse `androscan.rag.query` rather than re-implement search

---

### 4.9 Static analysis / call graph (planned)

**Purpose (Phase 8):** Derive a **method-level call graph** (and optional class hierarchy) from **Smali** under the apktool decode output — accurate for dispatch targets, complements jadx for human-readable browsing.

**Placement:** Implementation lives in a bounded package (e.g. `androscan/analysis/`) or behind a small interface consumed by orchestration, the web API layer, and new **llm** skills (`query_call_graph`). It is **not** a new vulnerability module by default; it is shared infrastructure for reasoning and UI.

**Outputs:** Serialized graph artifacts under `apps/<app_id>/` (e.g. `call_graph.json`); APIs paginate/filter to avoid loading entire graphs into the browser or LLM context at once.

**Rules:**
- graph builders must not depend on presentation (React)
- prefer pure Python + tests on fixture Smali; optional heavy-path integration behind opt-in
- LLM receives **subgraphs** or path query results, not unbounded full dumps

---

## 5. Dependency model

The intended dependency direction is:

- Presentation -> Orchestration / Application entry points
- Orchestration -> Application / Domain, Skills (pipeline skills)
- Application / Domain -> vulnerability module contracts, LLM abstractions, adapter abstractions, infrastructure abstractions
- Vulnerability modules -> shared domain models/contracts, adapter abstractions, LLM abstractions, Skills where appropriate
- LLM layer -> Skills (for skill catalog only; execution is via orchestration)
- Skills -> tool adapters or concrete tools where needed
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

**Web RE Workbench (Phases 6–9):** Treat FastAPI routes + WebSockets as presentation **wiring** only; reuse dossier/report models and existing skills (e.g. jadx-backed source fetch). Add REST/WS handlers, not duplicate business rules.

### 6.3 Adding a new skill

A new skill should usually involve:
- a new file in `androscan/skills/` exporting SKILL_META and execute(params, context)
- tier = `"pipeline"` (orchestration-only), `"llm"` (advertised in prompt), or `"exploit"` (orchestration during verification; not in prompt catalog)
- add the module name to the registry’s discover list in `androscan/skills/__init__.py`
- unit test for the skill

### 6.4 Adding a new external tool

A new external tool should usually involve:
- a new adapter or implementation module
- stable integration through existing contracts
- minimal effect on unrelated business logic

### 6.5 Adding or changing LLM usage

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

Refer to `docs/SAFETY_AND_SECURITY.md` for more detailed controls.

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

**Roadmap extension:** Phases **6–9** (local web UI, click-to-code, Smali call graph, Frida) are specified in `docs/DESIGN_DOC.md` and `docs/TASKS.md` and must follow the same layer rules above.

This document defines the intended structural model.
It does not imply that every part is already fully implemented today.