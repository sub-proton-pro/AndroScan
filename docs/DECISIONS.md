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
  - `AGENT_PROTOCOL.md`
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

### DEC-011: Use apktool for manifest extraction, jadx for decompilation skills
- status: Active
- date: 2026-03
- owners: (project)
- context:
  Phase 3 requires real APK/manifest parsing to build the component dossier, and later decompilation for skills like get_decompiled_class. Androguard was considered but avoided due to maintenance and licensing concerns.
- decision:
  Use **apktool** for manifest extraction (decode APK, parse decoded AndroidManifest.xml). Use **jadx** later for decompilation skills (e.g. get_decompiled_class).
- rationale:
  apktool is well maintained, Apache 2.0, and yields plain XML manifest for straightforward parsing. jadx is the standard for DEX-to-Java decompilation and will be used when implementing decompilation skills.
- alternatives considered:
  - Androguard (avoided: maintenance and licensing)
  - aapt/aapt2 for manifest only (lighter but text parsing; apktool gives clean XML)
  - In-house AXML parser (more work; apktool is sufficient)
- tradeoffs / consequences:
  - Extraction layer depends on apktool being on PATH (or configurable).
  - Decompilation skills will depend on jadx when implemented.
- follow-up:
  Implement Phase 3 Task 1 with apktool; implement get_decompiled_class (or equivalent) with jadx when adding real decompilation skills.
- related docs:
  - `docs/DESIGN_DOC.md` (Phase 3, extraction)
  - `docs/TASKS.md` (Phase 3 implementation plan)

### DEC-012: Central constants file and global_config.yaml
- status: Active
- date: 2026-03
- owners: (project)
- context:
  App-wide constants (e.g. APP_ID_MAX_LEN, MAX_TURNS, exploitability labels, CLI section rule) were scattered. Config was env-only; settings that affect many parts of the app needed a file-based option.
- decision:
  Use a central **constants** file (`androscan/constants.py`) for fixed values and labels. Use **global_config.yaml** (optional, repo root or `--config` path) for runtime settings; merge order: defaults → YAML → env. CLI accepts **--config** to pass config file path.
- rationale:
  Single place for constants improves consistency; YAML allows tuning without code changes; env overrides keep deployment flexible.
- alternatives considered:
  - Env-only config (retained as override layer)
  - Constants only, no YAML (less flexible for “30%+ of app” tuning)
- tradeoffs / consequences:
  - PyYAML added as dependency. Config dataclass extended with more fields (ollama_model, max_turns, apktool_cmd, jadx_cmd, section_rule_*, etc.).
- follow-up:
  Phase 3 extraction and decompilation will use config.apktool_cmd, config.jadx_cmd.
- related docs:
  - `docs/STATE.md`
  - `androscan/constants.py`, `global_config.yaml`

### DEC-013: Skills as first-class layer with three-tier model
- status: Active
- date: 2026-03
- owners: (project)
- context:
  Skills were an internal stub in `internal/skills.py`. Extraction and report writing were hardcoded in workflow. To support reuse by multiple vulnerability modules and by the LLM when it requests additional evidence, skills are promoted to a first-class architectural layer.
- decision:
  Introduce a **skills layer** (`androscan/skills/`) with a uniform contract (SkillMeta, SkillContext, SkillResult). Each skill exports SKILL_META and execute(). Three tiers: **pipeline** skills (orchestration calls in fixed order; not advertised to the LLM) and **llm** skills (advertised in the prompt catalog; run when the LLM includes them in skill_requests) as before. **Exploit-tier** skills (`app_env_check`, `build_exploit_command`, `capture_signals`, `run_exploit_command`, `verify_exploit_result`) are orchestration-driven (not in the LLM catalog); they are used during Phase 5 exploit verification. Registry discovers skills from known modules; execute(), list_llm_skills(), run_skills() provide the API. Extraction and report writing remain pipeline skills; decompilation-related capabilities are LLM-requestable skills.
- rationale:
  Composability: modules and the LLM reuse the same skills. Clear boundary: orchestration composes pipeline and exploit-tier skills; the LLM requests only the **llm** subset it is allowed to use. Testability: each skill is a small, testable unit.
- alternatives considered:
  - Keep skills as internal implementation detail (rejected: no reuse across modules)
  - Single tier for all skills (rejected: would allow LLM to trigger extraction/report arbitrarily)
- tradeoffs / consequences:
  - New layer and contract to maintain
  - Phase 3 extraction work is now "implement extract_manifest and prepare_dossier skills with apktool"
- follow-up:
  Phase 3 implements real logic in pipeline and LLM skills; modules can compose skills in later phases.
- related docs:
  - `docs/ARCHITECTURE.md` (Skills layer)
  - `docs/DESIGN_DOC.md` (Section 7, skills)

### DEC-014: Ollama client uses /api/chat (not /api/generate)
- status: Active
- date: 2026-03
- owners: (project)
- context:
  On some Ollama versions (e.g. 0.17.2), POST /api/generate returns 404 while GET /api/tags and POST /api/chat work. Callers need a single `complete(prompt, config)` that returns the model’s text.
- decision:
  The Ollama client calls **POST /api/chat** with body `{ "model", "messages": [{"role": "user", "content": prompt}], "stream": false }` and parses the reply from `message.content`. The public API remains `complete(prompt, config=...)` returning a string.
- rationale:
  /api/chat is the stable chat completion endpoint; using it avoids 404 on setups where /api/generate is unavailable.
- alternatives considered:
  - Keep /api/generate and document minimum Ollama version (rejected: breaks current user setup)
  - Try /api/generate then fallback to /api/chat (adds complexity; chat is sufficient)
- tradeoffs / consequences:
  - Request/response shape differs from /api/generate (prompt → messages; response → message.content). Tests mock the chat response shape.
- follow-up: None.
- related docs:
  - `androscan/llm/client.py`

### DEC-015: Interactive RE Workbench — FastAPI + React (local web UI)
- status: Active
- date: 2026-04
- owners: (project)
- context:
  A second presentation channel is needed for emulator mirroring, logcat, browsing dossier/reports, and later graph/Frida controls. The stack must stay maintainable, testable without a browser farm, and aligned with layer separation (presentation thin; no business logic in React routes).
- decision:
  Use **FastAPI** (+ `uvicorn`, WebSockets) for the local server and **React + Vite + TypeScript** for the SPA. **Monaco Editor** for code; **Cytoscape.js** for call-graph visualization (Phase 8). Default listen address **127.0.0.1**; no baseline multi-user auth (local trusted operator).
- rationale:
  FastAPI gives typed routes, WebSocket support, and static file serving in one process; React has the richest ecosystem for Monaco and Cytoscape; matches the agreed product plan.
- alternatives considered:
  - Svelte (lighter but smaller ecosystem for editor/graph plugins)
  - Vanilla JS (fewest deps but more bespoke UI work)
  - Separate Node backend (rejected: extra moving parts for a local tool)
- tradeoffs / consequences:
  - Adds Python optional deps and a `frontend/` build step when implementing Phase 6.
  - Agents must keep API handlers free of vulnerability-specific logic.
- follow-up:
  Implement Phase 6 per `docs/TASKS.md`; record `web_*` config in `global_config.yaml` when code lands.
- related docs:
  - `docs/DESIGN_DOC.md` (Phase 6)
  - `docs/ARCHITECTURE.md` §4.1
  - `docs/SAFETY_AND_SECURITY.md` (local bind, when to strengthen)

### DEC-016: Call graphs from Smali (apktool output), not from jadx AST alone
- status: Active
- date: 2026-04
- owners: (project)
- context:
  The product needs accurate method-level edges for dispatch and JNI/native boundaries are less visible in Java decompilation. jadx is ideal for human-readable source browsing but is a lossy view for bytecode-level invokes.
- decision:
  Build the **primary** static call graph from **Smali** files produced by **apktool** decode. Use **jadx** for mapping UI taps and displaying source (Phases 7–8). Store graph artifacts in a **per-app SQLite** database at `apps/<app_id>/.decompiled/<sha>/call_graph.sqlite` (mirroring DEC-018's RAG store layout — same `<sha>`-keyed cache directory, same drop-the-file-to-invalidate model). Rebuild on APK hash change.
- rationale:
  Smali reflects actual `invoke-*` targets; aligns with existing apktool pipeline; keeps graph generation testable with small fixture Smali files. SQLite (vs. a JSON blob) matches DEC-018, gives ACID writes + indexed lookups for `neighbors` / `paths` queries on real apps without loading the whole graph into memory, and survives partial writes during long apktool runs.
- alternatives considered:
  - jadx IR / Java AST only (rejected as primary: less faithful to bytecode)
  - External binary analysis tool as mandatory dependency (deferred: keep in-house parser first, adapter later if needed)
  - JSON blob at `apps/<app_id>/call_graph.json` (originally chosen here, superseded — see footnote below; doesn't scale to real apps and breaks the `<sha>`-keyed invalidation model)
- tradeoffs / consequences:
  - Custom parser maintenance; must handle large apps via pagination/filtering in APIs and UI.
  - Adds a third per-app SQLite store alongside the RAG index (DEC-018) — operationally the same shape; tests can use the existing fixture-driven pattern.
- follow-up:
  Implement `androscan/analysis/` (or equivalent) per `docs/TASKS.md` Phase 8; add `query_call_graph` **llm** skill when subgraph contract is stable.
- related docs:
  - `docs/DESIGN_DOC.md` (Phase 8)
  - `docs/ARCHITECTURE.md` §4.9
  - `docs/CONVENTIONS.md` (graph artifact conventions when implemented)
  - **DEC-023** (Hook Lab v1 — Smali call graph + Frida adapter; ratifies the SQLite storage choice as part of the Hook Lab plan)
  - **DEC-024** (Phase 10 Behavior Trace — reuses the call-graph SQLite store unchanged for forward-closure walks; no schema changes to `call_graph.sqlite`)

**Superseded sub-decision (2026-04-25):** the original storage clause specified a JSON blob at `apps/<app_id>/call_graph.json`. That clause was superseded by **DEC-023** during Hook Lab v1 planning in favour of per-app SQLite at `apps/<app_id>/.decompiled/<sha>/call_graph.sqlite`. DEC-016 itself remains Active — the Smali-first sourcing decision and the rebuild-on-APK-hash-change semantic are unchanged; only the on-disk format and path were tightened.

### DEC-018: Lane-1 RAG over decompiled sources — embeddings + SQLite, not BM25 or JSON
- status: Active
- date: 2026-04
- owners: (project)
- context:
  Phase 6 step 3 needs the Inspect-tab chat (and the new `search_decompiled_sources` LLM skill) to retrieve relevant decompiled code by semantic intent (e.g. "where is the deep-link parsed for activity X?"). The corpus is per-app jadx output (often hundreds of MB of `.java`/`.kt`); the corpus is rebuilt every time the APK changes (decompile cache is keyed by `sha256(apk)`). A naive lexical match (BM25) loses too much intent on decompiled identifiers (`a.b.c.d.foo()`); an in-memory JSON index hurts cold-start and makes invalidation messy.
- decision:
  - **Storage:** per-app **SQLite** at `apps/<app_id>/.decompiled/<sha>/rag.sqlite` (WAL, packed `float32` BLOB column). One DB per APK SHA — invalidation = drop the file. No global DB; no JSON index.
  - **Retrieval:** **vector embeddings** (cosine top-k), **not** BM25 as the primary path. Brute-force scan in Python (numpy when available, pure-Python fallback); `sqlite-vec` is a planned drop-in replacement and the column layout is already `float32` so the swap is mechanical.
  - **Embedding provider:** pluggable `EmbedProvider` protocol with three implementations — `fastembed` (default; small ONNX models, no GPU), `ollama` (HTTP `/api/embeddings`), and a deterministic `HashProvider` for tests / no-deps environments. Configurable via `rag.embed_provider` / `rag.embed_model` and `ANDROSCAN_RAG_*` env vars.
  - **Chunking:** brace-balanced over `.java`/`.kt` (class headers + methods, keyword blacklist, char budgets). **Not** tree-sitter — jadx output is messy enough that a custom balancer is more reliable and avoids a heavy native dep.
  - **Lifecycle:** auto-build in a **daemon thread** when `decompile_cache` finishes (`schedule_rag_build_after_decompile`). In-process lock prevents duplicate builds. The skill, the routes, and the chat enrichment all **fail-open** — if RAG is unavailable, the rest of the product still works.
  - **LLM exposure:** retrieval is exposed to the model via the `search_decompiled_sources` **llm**-tier skill **and** as a transparent attachment-injection step in the Inspect tab chat (`_enrich_inspect_with_rag`). Both go through the same chat guardrails (per-kind budget for `kind="code"`, `<context>` wrapping, sanitization).
- rationale:
  Vectors handle paraphrase and intent better than lexical scores on decompiled identifiers. SQLite gives ACID writes, WAL concurrency, and trivial per-APK invalidation without a service dependency. The provider protocol means we can ship hermetic tests (Hash) and still reach for `fastembed` / `ollama` / `llama.cpp` without touching call sites.
- alternatives considered:
  - **BM25 / Whoosh / Tantivy** (rejected as primary: too lossy on decompiled identifiers; can be added later as a hybrid layer).
  - **Single JSON index per app** (rejected: cold-start, no concurrent reads, awkward invalidation, no easy swap to a vector DB).
  - **`sqlite-vec` from day one** (deferred: keeps the install matrix simpler; brute-force is fine at the per-app scale we need today and the schema is already vector-ready).
  - **`tree-sitter` for chunking** (rejected: heavy native dep; jadx output already has enough structure for brace-balancing).
  - **Always-on Ollama embeddings** (rejected as default: requires a running server even for static analysis; kept as an option for users who already have one).
- tradeoffs / consequences:
  - New optional dep set (`pyproject` extra `[rag]`: fastembed, numpy). The `hash` provider keeps the test path hermetic.
  - Build is asynchronous, so callers must check `GET /api/rag/{app_id}/status` rather than assume readiness; tests for the endpoint monkeypatch `start_build_async` to run synchronously.
  - Brute-force cosine is O(N·d) per query — fine for today's per-app sizes; revisit when we add `sqlite-vec`.
  - Code retrieval enlarges the chat context — handled by the existing per-kind attachment budget (`code = 6 KB`) and the 32 KB total context cap (DEC-005 / `androscan/web/chat.py`).
- follow-up:
  - **[done]** `resolve_ui_element` LLM skill that consumes RAG hits + the existing `/api/inspect/map` (a.k.a. click-to-code) candidates — see DEC-019.
  - Evaluate `sqlite-vec` once it's available across our target Python versions; benchmark vs. brute force.
  - Optional: hybrid BM25 + vector reranking once the corpus and quality bar grow.

### DEC-019: `resolve_ui_element` is a deterministic fuser, not an LLM-call wrapper
- status: Active
- date: 2026-04
- owners: (project)
- context:
  Phase 6 step 3 needed an LLM-tier skill that turns a tap on the mirror into a confident answer about *which class.method handles it*. The raw output of `POST /api/inspect/map` (foreground activity, element with `resource_id`/`text`/bounds, and a list of regex-grep handler candidates by kind) is structurally rich, and the picking heuristics — "prefer `findViewById` matches in the foreground activity, near the top of the file" — are well-known. We need both an LLM-callable tool *and* a synchronous answer for the Inspect-tab UI without paying a model round-trip per click.
- decision:
  - Implement `resolve_ui_element` (`androscan/skills/resolve_ui_element.py`) as a **pure-function, deterministic, explainable scorer** — no LLM call inside. The `tier="llm"` label means "advertised in the prompt catalog so the planner can call it as a tool", mirroring `search_decompiled_sources` (DEC-018).
  - Score model is small and additive so reasons are auditable: kind base (`findViewById` 1.00 > `onClick_near` 0.80 > `compose_id` 0.60 > `reference` 0.20 > rag-cosine), foreground-activity match bonus (+0.50), activity-named-file bonus (+0.10), early-line decay bonus (≤+0.10 for line 1, → 0 at line 200). RAG hits feed into the same scorer so a clean `findViewById` in the foreground activity always beats an arbitrary semantic match.
  - Optional Lane-1 RAG enrichment uses a query synthesised from `text` > `content_desc` > short resource id (only when there is anchor text — RAG isn't asked random nonsense).
  - The core logic lives in a pure-function `resolve(...)` helper; `execute(params, context)` adapts a `SkillContext` to it. The same helper is called inline by `POST /api/inspect/map` so the response carries a `resolution` block (`{best, alternatives, rag_hits, reasoning}`) — the UI gets a single ranked answer with no LLM round-trip per click.
  - Fail-soft: missing app dir, missing decompile cache, missing embed provider all return `success=True` with a clean note in `text`/`rag_error`. The `/api/inspect/map` wiring catches any exception so the fuser can never break click-to-code.
- rationale:
  Tap → handler is a **closed-form ranking problem** once you have the inputs. A deterministic scorer is faster, cheaper, easier to test, and far easier to debug than asking an LLM to pick. Keeping it tier `llm` still lets the planner call it explicitly when reasoning about UI flows in chat — which is the actual collaboration mode we want.
- alternatives considered:
  - **LLM-call inside the skill** (rejected: latency per click, cost, and we'd lose explainability — every alt would need a justification round-trip).
  - **Inline-only in `app.py`, no skill** (rejected: the planner can't request it as a tool; chat workflows lose the "find the handler for the Login button" capability).
  - **More elaborate scoring (PageRank over call graph, etc.)** (deferred: out of scope for step 3; revisit when the Smali static call graph from step 4 is available — that data could feed a future scorer iteration).
- tradeoffs / consequences:
  - The scorer's weights are heuristic; if they prove wrong on real apps, tweaking is cheap because reasons are surfaced in the result (`best.reasons`).
  - The `resolution` block adds a second pass over the candidate list per `/api/inspect/map` call, but the cost is negligible compared to `uiautomator dump`.
  - `resolve_ui_element` has two callers (skill registry + `/api/inspect/map`) — both go through the same `resolve()` helper, so behaviour stays consistent.
- follow-up:
  - Wire the Inspect-tab frontend to `resolution.best` so click-to-map jumps the Monaco viewer to `file:line` and badges the source (`source: deterministic | rag`) and `score`.
  - Once the Smali static call graph (Phase 8) lands, evaluate adding a graph-distance term to the scorer.
- related docs:
  - `docs/STATE.md` (Lane-1 RAG indexer)
  - `docs/TASKS.md` (Phase 6 UX step 3)
  - `docs/ARCHITECTURE.md` §4.10 (RAG layer)
  - `docs/SAFETY_AND_SECURITY.md` §12.4 (RAG attachment handling)

### DEC-020: Settings tab — first-class UI for global + per-app configuration
- status: Active
- date: 2026-04
- owners: (project)
- context:
  Up to Phase 6 polish step 3, all configuration lived in `global_config.yaml` (with env-var overrides). There was no UI to inspect *what was actually loaded*, no way to express per-app overrides (e.g. "use `ollama` embeddings for this banking app, `fastembed` for everything else"), and no live status view of the moving parts the workbench depends on (adb, jadx, Ollama, the embed provider, the RAG index per app, the on-device foreground activity, etc.). Operators were debugging by `tail -f` and `curl`.
- decision:
  Add a dedicated **Settings tab** as the fourth top-level tab. The tab is split into four sections via a left-rail nav: *Global settings* (form **and** raw-YAML editor for `global_config.yaml`, with a "Reset to defaults" button), *App settings* (per-app overrides written to `apps/<app_id>/app_settings.json` — flat file, atomic writes, schema-versioned, never touches `app_meta.json`), *Status* (live cards for both global health and per-app device/decompile/RAG state), and *Diagnostics* (raw API payloads + uvicorn-less reload).
  Backend: three new modules — `androscan/web/health_probes.py` (pure-function, timeboxed probes for adb/jadx/apktool/frida/ollama/fastembed/disk/uid/foreground-activity/uiautomator/apk-sha-drift), `androscan/web/per_app_settings.py` (load/save/reset + override merger), `androscan/web/settings_routes.py` and `androscan/web/status_routes.py` (FastAPI routers). `androscan/config/loader.py` grew `CONFIG_FIELD_MAP`, `LIVE_RELOADABLE_FIELDS`, `global_view_from_config`, `effective_sources`, `dump_to_yaml`, `save_raw_yaml`, `restore_defaults_yaml`, `with_overrides`, `discover_config_path`. `app.py` now stores config on `app.state.config` and the new routers read via a callable provider so live reload sticks for newly-added consumers (legacy closures keep boot-time config — the UI surfaces this via a `restart_required` pill).
  Frontend: new `SettingsTab` (sectioned panels), `HealthDot` in the global header (polls global status every 30s, deep-links to Settings on click), and two API clients (`api/settings.ts`, `api/status.ts`).
- rationale:
  - **Discoverability**: the field map + source pills (`yaml`/`env`/`default`) tell operators *exactly* why a value is what it is — much better than reading code.
  - **Per-app overrides** are necessary for real workflows (model swap per app, RAG provider swap, custom logcat retention) but they must not pollute `app_meta.json` (which is the analysis pipeline's output, not a settings store). A separate file keeps the concerns clean.
  - **YAML editor + form**: the user can use whichever input mode fits their flow. Validation runs server-side via `validate_raw_yaml` so a bad save returns a clean 400 with the parse/type error.
  - **Live status** turns previously hidden failure modes (Ollama down, fastembed not installed, jadx missing, apk sha drifted under us, uiautomator dump empty, foreground activity ≠ analysed app) into a single colour-coded grid.
  - **Restart-required pill**: changing `web.host`/`web.port`/CORS won't take effect until uvicorn restarts; we mark these via `LIVE_RELOADABLE_FIELDS` rather than pretending we can hot-swap them.
  - **Force-with-warning per-app overrides**: per the user's choice, we never refuse a valid override; we only warn when the override would diverge from a global setting that the rest of the codebase still reads via the boot-time closure (these will be migrated incrementally to read from `app.state.config`).
- alternatives considered:
  - **Form-only editor** (rejected: power users wanted to copy-paste YAML chunks; raw editor unblocks that).
  - **Co-locate per-app settings inside `app_meta.json`** (rejected: mixes settings with pipeline output, makes "reset to defaults" risky).
  - **Polled status with no caching** (rejected: opening the UI mounts multiple status cards; we cache for 3 s in-process to keep adb/Ollama happy).
  - **Auto-restart uvicorn after a save** (rejected: would disconnect mirror/logcat WS sessions silently; the pill is honest about what needs a restart).
- tradeoffs / consequences:
  - Existing route handlers that captured `config` directly do not pick up live reloads. We accept this for now (documented via `restart_required`); the migration to `app.state.config` is incremental and per-handler.
  - `app_settings.json` adds a third per-app file (alongside `app_meta.json`, `triage.json`); the `apk_overrides_summary` helper makes it easy to grep what's overridden.
  - Probes are best-effort and timeboxed — a slow Ollama only adds its own timeout to `/api/status/global`, not the sum of all probe timeouts (we use `asyncio.gather`).
- follow-up:
  - Migrate hot-path route handlers (`/api/llm/info`, chat, RAG, decompile) to read from `app.state.config` so live reload covers their fields too.
  - Add a "compare to global" diff view in the per-app panel so users can see overrides at a glance.
  - Persist the YAML editor's draft locally (sessionStorage) so an accidental tab switch doesn't lose work.
- related docs:
  - `docs/STATE.md` (Settings tab implementation summary)
  - `docs/TASKS.md` (Phase 6 follow-ups)
  - `docs/SAFETY_AND_SECURITY.md` (config write-paths + env-lock semantics)
  - `docs/ARCHITECTURE.md` (web layer module map)

### DEC-021: Health probes are pure functions, timeboxed, and consumed via a callable map
- status: Active
- date: 2026-04
- owners: (project)
- context:
  The Settings status panel needs to surface a dozen+ external dependencies (adb, jadx, apktool, frida, Ollama daemon, embed provider availability, disk free, apk-sha drift, on-device package state, foreground activity, uiautomator dump, etc.). Naive synchronous probes would either freeze the UI when Ollama is unreachable, or worse, time out the entire status request because of one slow check.
- decision:
  Implement every probe in `androscan/web/health_probes.py` as a small, side-effect-free, **async coroutine** with a hard wall-clock cap (default 2 s, 1 s for adb-shell probes, 1.5 s for HTTP). Probes never raise — they always return a dict shaped `{ok: bool, label: str, ...probe_extras, error?: str}`. The aggregator `androscan/web/status_routes.py` runs them in parallel via `asyncio.gather(..., return_exceptions=False)` so the slowest probe sets the response latency, not their sum. A 3-second in-process cache (`_STATUS_CACHE`) absorbs the burst of requests when multiple status cards mount at once.
- rationale:
  - **Pure functions are trivially unit-testable** without standing up the whole FastAPI app — `tests/test_health_probes.py` covers 20 cases by monkeypatching `asyncio.create_subprocess_exec` and `urllib.request.urlopen`.
  - **Hard timeouts** prevent a wedged adb / Ollama from poisoning the rest of the status payload.
  - **Asyncio.gather** keeps the status fan-out cheap; the user gets a full picture in well under a second on a healthy host.
  - **Returning a dict instead of raising** means the aggregator code stays linear (no try/except wrapping every probe call).
- alternatives considered:
  - **Threadpool + sync probes** (rejected: more locks, more book-keeping; FastAPI is async first).
  - **Per-probe HTTP endpoints** (rejected: chatty for the UI; the aggregator gives one call per panel).
  - **No cache** (rejected: opening Settings would fan out to adb/Ollama 5+ times concurrently).
- tradeoffs / consequences:
  - The 3 s cache means a freshly-fixed Ollama might still report "down" for ≤3 s — acceptable; the user has a manual "Refresh now" button that bypasses the cache via the same URL (the cache is invalidated on settings save / reset / reload).
  - Probe functions are imported by name into `status_routes` (for IDE-friendliness); tests must patch the **consumer module** to override behaviour, not the producer module. This is documented inline in `test_settings_routes.py`.
- follow-up:
  - **[done 2026-04-27, in Hook Lab sub-step 4.3]** Add a "Hook Lab readiness" rollup probe (frida-server present on device, frida CLI on host, target package gadget injectable) once Hook Lab work begins. Two-card design landed: existing `tools.frida` (host CLI; from `probe_frida_version`) + new `tools.frida_server` card combining `probe_frida_server` (device reachability via `adb shell pidof frida-server`) + `probe_frida_version_skew` (host CLI vs. `frida-server --version`; severity `None` / `"minor"` / `"major"`). Treated as **yellow** in `rollupGlobal` — non-critical for static workflows. Sufficient for v1 per DEC-023's 4.3 sub-bullet; "target package gadget injectable" is deferred to 4.5 along with the Inject UI it would gate.
  - Consider a small SSE channel to push status updates instead of 15 s polling once we have more than ~30 cards.
- related docs:
  - `docs/STATE.md` (status probes summary)
  - `docs/TEST_STRATEGY.md` (monkeypatch-the-consumer pattern)

### DEC-017: Frida as dynamic-analysis adapter; user confirmation for LLM-generated hooks
- status: Active
- date: 2026-04
- owners: (project)
- context:
  Live instrumentation is powerful but risky: wrong scripts can crash the app, and LLM output must not run unchecked on the operator’s device.
- decision:
  Integrate **Frida** only through a **tool adapter** (Python `frida` client + optional frida-server push/start helpers). **LLM-generated** hook scripts require **explicit user confirmation** in the UI before injection. Optional Phase 5 extension: new signal type (e.g. `frida_trace`) in `vuln_module_skills_signals.json` for deeper verification evidence.
- rationale:
  Preserves adapter boundary (DEC-010), keeps LLM output untrusted (DEC-005), matches single-user local threat model with proportionate controls.
- alternatives considered:
  - Auto-run LLM hooks without confirmation (rejected)
  - Frida calls scattered in presentation layer (rejected)
- tradeoffs / consequences:
  - CI cannot rely on real devices; tests mock the adapter; opt-in integration jobs for attach/hook lifecycle.
- follow-up:
  Implement Phase 9 per `docs/TASKS.md`; extend `docs/SAFETY_AND_SECURITY.md` / `docs/TEST_STRATEGY.md` as behavior lands.
- related docs:
  - `docs/DESIGN_DOC.md` (Phase 9)
  - `docs/ARCHITECTURE.md` §4.7
  - `docs/SAFETY_AND_SECURITY.md`

### DEC-022: Workbench chat — agentic skill loop with a consent-class hook for side-effecting skills
- status: Proposed
- date: 2026-04
- owners: (project)
- context:
  The workbench chat path (`androscan/web/chat.py`, `POST /api/chat` and `POST /api/chat/stream`) is one-shot: validate → optional one-pass `_enrich_inspect_with_rag` (top-4 chunks, fail-soft) → call `complete()` once → return prose. The LLM cannot ask for more data mid-turn. By contrast, the analysis pipeline in `androscan/internal/workflow.py` runs a real **`while turn < max_turns`** loop that calls `parse_response()` and `run_skills(...)` whenever the model emits `skill_requests`, feeding results back into the next turn's prompt.

  Real symptom (April 2026 testing): in the Inspect-tab chat, an operator asked *"what is the name of this sqlite db and where in the device filesystem is it located?"*. RAG correctly retrieved the `BalanceDatabase` and `WeakBankContentProvider` chunks, but the chunker splits at the method level and the **constructor** chunk (where `super(context, "<name>", null, 1)` lives) didn't make the top-4. The model honestly hedged: *"the actual database name constant... is not shown — to find it, decompile and look in the constructor / `onCreate`."* The exact follow-up the model wanted to do (a second `search_decompiled_sources` call scoped to `BalanceDatabase.<init>`) is a registered LLM-tier skill that the chat path simply doesn't expose.

  The gap matters more as the workbench grows: Hook Lab will introduce frida-related skills (DEC-017) that *do* have side effects on the device, so any agentic-loop design has to accommodate both read-only static-analysis skills (current state) and consent-required side-effecting skills (Hook Lab onward) without two parallel code paths.
- decision:
  - **Add a bounded agentic skill loop to the workbench chat path** that mirrors `workflow.py`'s pattern. Inspect, Reports, and Hook Lab tabs share one implementation in `androscan/web/chat.py`; tabs scope which skills are reachable via a per-tab allowlist (the existing `_TAB_SYSTEM_PROMPT` allowlist gains a `skills` axis).
  - **Hard caps in code, not config:** `MAX_CHAT_TURNS = 5`, `MAX_SKILLS_PER_TURN = 3`, per-skill wall-clock timeout = 5 s, total per-chat-turn skill-output budget = ~6 KB (each skill result is truncated and headed with `# skill_name(args)` so the model knows what got cut).
  - **Stream every step.** Extend the SSE event vocabulary (`thinking` / `content` / `done` / `error`) with two new types:
    - `skill_request` — `{turn, skill, args, request_id}`
    - `skill_result` — `{request_id, ok, duration_ms, preview, truncated}`
    The frontend renders these as collapsible cards inside the existing thinking block. The user always sees what the LLM looked at, with click-to-expand for full results — that is the audit trail.
  - **Consent-class hook, even though no skill needs it yet.** `SkillMeta` gains a `requires_confirmation: bool = False` field. The chat loop branches on it: `False` → execute immediately; `True` → emit a `skill_pending` SSE event with `{request_id, skill, args, rationale}`, the loop awaits a follow-up `POST /api/chat/skill_decision/{request_id}` from the client (Allow / Deny + optional edited args), then either runs or short-circuits with a "denied by operator" skill result. Pending state lives in an in-process `dict[request_id -> PendingSkill]` keyed by chat session, with a 90 s TTL.
  - **Per-tab "always confirm" toggle** in Settings → Per-app overrides (defaults off). When on, every skill is treated as `requires_confirmation=True` for that tab. Operators who want pure-manual mode get it without a code change.
  - **Transcript schema extension.** `apps/<app>/<run>/chat/<tab>.jsonl` adds `{type: "skill_call", turn, name, args, result_preview, duration_ms, decision?}` records interleaved with the existing `{type: "user"|"assistant"}` lines. Reports can cite "the LLM looked at `BalanceDatabase.<init>` lines 23–28" verbatim.
  - **Today's classification:** all currently-registered LLM-tier skills (`get_decompiled_class`, `get_decompiled_method`, `list_classes_in_package`, `search_decompiled_sources`, `resolve_ui_element`) keep `requires_confirmation=False`. Hook-Lab-introduced skills (frida hook injection, `adb shell`-driving skills, anything that mutates device or files) ship with `requires_confirmation=True`.
- rationale:
  - **Read-only static-analysis skills do not justify confirm-mode friction.** Every current LLM-tier skill reads from the persistent decompile cache or hits the local SQLite RAG index — no `adb`, no network, no writes. The risk profile is roughly equivalent to "the IDE auto-completed by reading more files than I asked about." Gating each call behind a click would add 3–5 seconds of human-reaction latency per question with no safety benefit.
  - **Streamed skill cards are the audit trail.** The thing operators actually want from confirm-mode (chain of evidence, reproducibility, "screenshot for the report") is delivered by visible-and-clickable skill events plus the transcript JSONL. They get to *see* what happened without having to *gate* it.
  - **The consent hook is built once, used forever.** Hook Lab will need exactly this flow for frida hook injection (DEC-017 already commits us to user confirmation for LLM-generated hooks). Building the protocol now — even with zero skills using it — means Hook Lab inherits a working consent UI and a tested pause-and-resume protocol on day one. Otherwise we'll either skip safety in the rush to ship Hook Lab, or pause the Hook Lab milestone to retrofit consent.
  - **One implementation, two modes.** A unified loop with a per-skill flag avoids the bug-magnet of maintaining "auto chat" and "manual chat" as separate code paths. Per-tab and per-app overrides are policy on top of the same engine.
  - **Bounded loop > unbounded planner.** Hard caps in code (not config) prevent runaway costs and keep latency predictable. The model can ask for "more search" up to a budget; past that, it has to commit to an answer.
- alternatives considered:
  - **Pure auto-loop, no consent hook** (rejected): cheaper today, but Hook Lab will need the consent flow within the same milestone — building it incrementally there would either delay Hook Lab or compromise DEC-017.
  - **Pure confirm-each-step UX with Allow/Stop on every skill** (rejected): permanent UX friction (3–5 clicks per multi-step question), pause-and-resume protocol is real engineering, and the safety justification is weak when 100% of currently-callable skills are read-only. Also: nothing stops a tired operator from clicking Allow 30 times.
  - **No change; tune `_INSPECT_RAG_TOP_K` upward and accept the limitation** (rejected): increasing top-k to 10 + per-hit budget improves the failure rate but doesn't fix the underlying "lexical embedding mismatch" problem (a query like "where is X defined" will keep missing constructor chunks because they're mostly `super(...)` boilerplate). It's a band-aid we may apply *as well*, but it isn't a substitute for letting the model dig.
  - **Tool-call API (Ollama / OpenAI native function-calling)** (deferred but not rejected): for Ollama models that support it (`llama3.1`, `qwen2.5-coder`, `mistral-nemo`), the loop body can use tool-calling instead of the `parse_response()` JSON convention `workflow.py` uses today. We keep the option open by abstracting the "extract skill requests from a model turn" step behind a small interface; the initial implementation reuses `parse_response()` for parity with the analysis pipeline.
- tradeoffs / consequences:
  - **Latency per chat turn grows with the loop.** A question that needed 3 skill calls now waits for 4 LLM turns + 3 skill executions before any final content streams. Mitigated by: streaming each `skill_request` / `skill_result` event so the user sees activity, hard cap on `MAX_CHAT_TURNS`, per-skill timeout.
  - **Context window grows.** Each skill result gets appended to the next turn's messages. We need a small summarisation strategy past 2–3 results (keep most recent N raw, replace older ones with one-line citations).
  - **Two consumers of the skill registry now exist** (`workflow.py` and `chat.py`). They share `run_skills()` but call it from different orchestration loops; behaviour drift is a risk. Mitigated by: shared `parse_response()` + `run_skills()`; integration tests that exercise both paths against the same fake skill set.
  - **Pending-skill state is in-process.** A uvicorn restart drops pending consents. Acceptable for the single-operator local deployment; documented in `KNOWN_ISSUES.md` if/when it bites someone. Persisting to SQLite is a one-day follow-up if needed.
  - **The streaming SSE schema is now larger** — frontends that don't recognise `skill_request` / `skill_result` / `skill_pending` ignore unknown event types (current `streamChat` parser already does this), so backwards compatibility is preserved.
  - **`/api/chat` (non-streaming) cannot do interactive consent** — it returns 409 if the LLM requests a `requires_confirmation=True` skill. The streaming path is the only consent-capable surface. Non-streaming chat is mostly a test-friendly fallback at this point; documented.
- follow-up:
  - Build behind a per-tab feature flag (`chat.agentic_loop.enabled`) so it can ship dark and be enabled per tab as confidence grows.
  - Add `tests/test_chat_agentic.py` covering: happy path with 1+ skill turns, max-turn cutoff, skill timeout, skill error mid-loop, consent-required skill with deny, consent-required skill with TTL expiry, SSE event ordering invariants.
  - Ship Inspect-tab first (most obvious win), then Reports, then Hook Lab (which will exercise the consent path for the first real `requires_confirmation=True` skills).
  - Revisit this DEC during Hook Lab to confirm the consent-class hook is sufficient for frida hook injection per DEC-017, or extend it (e.g. require a typed-confirmation phrase for hook scripts that touch security-sensitive APIs).
  - Independently of this work, also bump `_INSPECT_RAG_TOP_K` from 4 → 8–10 as a quick UX win — the loop reduces but doesn't eliminate the value of better one-shot retrieval.
- 4.7 activation (2026-04-27, in Hook Lab sub-step 4.7):
  - **`SkillMeta.requires_confirmation` field has landed** in `androscan/skills/base.py` — `bool = False` default, so every existing read-only LLM-tier skill (`get_decompiled_class`, `get_decompiled_method`, `list_classes_in_package`, `search_decompiled_sources`, `resolve_ui_element`, the new `query_call_graph`) is unaffected. **`tests/test_skills.py` now invariant-checks** that pipeline + exploit-tier skills never accidentally ship `requires_confirmation=True` (this DEC scopes consent only to the LLM-driven loop; pipeline and exploit-tier skills are orchestrated, not LLM-picked).
  - **First real consumer:** `androscan/skills/generate_frida_hook.py` ships with `requires_confirmation=True` — the LLM-tier skill that wraps `frida_hooks.render_by_id` and returns rendered JS + deterministic pentester summary + sensitive-APIs list. The skill is **prep-only** by construction: it does not attach to a process, does not load the script into Frida, and does not touch the device — its `text` includes an "Operator action required: review the JS + summary above, then stage / inject from the Hook Lab UI" footer. This means even before the agentic loop and consent SSE plumbing are wired (still pending under this DEC), the skill is **safe to register** in the LLM catalog because the operator is the only mechanism that can turn rendered JS into an injection (via the existing 4.5 Stage→Inject UI). When the SSE consent flow lands, `generate_frida_hook` becomes the canonical first test of the `skill_pending` / `skill_decision` round-trip.
  - **What 4.7 explicitly does *not* ship of this DEC:** the bounded loop in `androscan/web/chat.py` itself, the `skill_request` / `skill_result` / `skill_pending` SSE event vocabulary, the `POST /api/chat/skill_decision/{request_id}` endpoint, the per-tab "always confirm" toggle, the transcript-JSONL `skill_call` record extension, and `tests/test_chat_agentic.py`. Those remain follow-ups; they are gated by the chat-path refactor (ISSUE-009 is still single-pass) which lives outside Hook Lab's milestone budget. The `requires_confirmation` plumbing is the **only** half of this DEC that 4.7 needed for the Hook Lab promise (a renderable, audit-trailable hook generator), so the rest can ship when it ships without blocking Hook Lab v1's completion.
- related docs:
  - `docs/DECISIONS.md` DEC-013 (Skills as first-class layer with three-tier model)
  - `docs/DECISIONS.md` DEC-017 (Frida user-confirmation requirement — this DEC is the mechanism that fulfils it)
  - `docs/DECISIONS.md` DEC-018 (Lane-1 RAG — the primary read-only retrieval skill)
  - `docs/DESIGN_DOC.md` (Phases 6–9; chat loop section to be added)
  - `docs/SAFETY_AND_SECURITY.md` (consent semantics + per-skill budgets — section to be added)
  - `docs/TASKS.md` (backlog entry to track implementation; "RE Workbench chat — agentic loop" P2 item under § Interactive RE Workbench)
  - `androscan/web/chat.py` (current one-shot path — to be extended)
  - `androscan/internal/workflow.py` (existing `while turn < max_turns` loop pattern — the model to mirror)

### DEC-023: Hook Lab v1 — Smali call graph + Frida adapter with template-driven hooks and deterministic pentester summaries
- status: Active
- date: 2026-04-25 (proposed); promoted to Active 2026-04-27 alongside Hook Lab sub-step 4.3
- owners: (project)
- context:
  Phase 6 step 4 (Hook Lab) brings together two previously-deferred pieces of the RE Workbench: a static call graph from Smali (DEC-016) and live Frida instrumentation (DEC-017). Together they unlock the planned "click a method → see its callers/callees → optionally hook it and watch live traces" workflow that the Inspect tab cannot deliver alone. Before any code lands, the planning conversation (April 2026) settled eight open design questions that span call-graph fidelity, on-disk storage, frida-server provisioning, hook source policy, the Inject-button confirmation UX, trace persistence, per-app vs. global safety knobs, and graph-visualisation strategy. This entry records those decisions in one place so the Hook Lab implementation rolls out in a strictly linear 8-sub-step sequence (4.1 → 4.8 in `docs/TASKS.md`) without re-litigating policy mid-implementation.
- decision:
  - **Call-graph fidelity (v1 = "v2" in the planning shorthand):** the Smali parser resolves **direct `invoke-*`** edges *and* performs **virtual-dispatch resolution** for `invoke-virtual` / `invoke-interface` against the in-app class hierarchy (no framework classes). Edges that come from dispatch resolution rather than a direct invoke are tagged `kind: "virtual_dispatch"` and rendered **dashed** in Cytoscape; the LLM is told they are inferences, not literal bytecode targets. Reflection-resolved edges are deferred to v3; nodes whose method body contains `Class.forName` / `Method.invoke` patterns carry `may_have_unresolved_reflection: true` so consumers (UI, LLM skill) can flag the gap honestly.
  - **Storage:** per-app SQLite at `apps/<app_id>/.decompiled/<sha>/call_graph.sqlite`, mirroring DEC-018's RAG store. Drop-the-file-to-invalidate; same `<sha>`-keyed cache directory as decompile + RAG. **DEC-016 is amended** in this same commit to reflect this — its original JSON-blob clause is footnoted as a superseded sub-decision.
  - **Frida overlay on call graph (sub-step 4.8 specifics, ratified 2026-04-27 — Hook Lab v1 complete):**
    - **Single new prop, single new keying contract.** `CallGraphView.tsx` gains exactly one new prop — `hitsByMethod?: ReadonlyMap<string, number> | null` — and exports one helper, `hitKey(className, methodName)` (stable `${className}::${methodName}` joiner). The Cytoscape pane never reads back from the parent; it only consumes the map and recomputes elements when the reference changes. **Three states, three meanings:** (a) `null` → no active session pinned in `HookLabTab`, render in the plain 4.2 styling (no `.hit` / `.unhit` / `.has-hits` / `.no-hits` classes applied — same look the Inspect / Reports tabs see if they ever embed the same component); (b) empty `Map` → session is pinned but no events have fired yet, every method node renders as `unhit` (dimmed grey, opacity 0.55) so the operator immediately sees "session is wired, ring is empty"; (c) non-empty `Map` → method nodes whose `hitKey(class, method)` is present render as `hit` (bold cyan `#56d4dd`, font-weight 600, drop-shadow), the rest as `unhit`. Package-overview nodes aggregate the same data — `has-hits` (cyan border + total-hits in label) when ≥1 contained method is in the map, `no-hits` (dimmed) when an overlay is active but no methods in this package fired. Tooltips (Tippy.js) show per-node hit counts in `HIT_CYAN` so the cyan token is consistent across Cytoscape and HTML.
    - **Polling-cadence reuse — zero new network requests.** `HookLabTab.tsx` already polls `/api/frida/sessions/{id}/hooks` every 2.5 s for the chat-attachment payload (4.7). 4.8 derives `hitsByMethod` from the *same* `chatHooks` array via `useMemo` (`out.set(hitKey(h.class, h.method), h.hits)` in a tight loop) and passes it to `<CallGraphView>`. The graph overlay, the Hooks panel, the Scope panel, and the chat attachment all reference the same observed state ± 2.5 s — operators who Inject and watch the graph "light up" are looking at the exact same numbers the chat sees. Reused polling means: no second `useEffect` for graph hits, no second backpressure surface, no second loading state — and a paused trace WS doesn't pause the overlay because the underlying ring keeps receiving events while `_summarize_hooks` keeps reading them.
    - **Method-overload precision caveat (intentional, documented in KNOWN_ISSUES ISSUE-012):** the call graph's nodes are keyed by `(class_name, method_name, descriptor)` (the full Smali signature is preserved on each node), but Frida's `frida_hooks.entry_exit_log` template logs `(class, method)` only — overload arity / descriptor goes through Frida's `Java.use(class).method.overloads.forEach` and the emitted event payload doesn't carry the arity discriminator (this would change the wire shape and the `_summarize_hooks` aggregator contract from 4.6). Net effect: an app with `Foo.bar(String)` and `Foo.bar(int, String)` in its Smali shows hits aggregated under both nodes when *either* overload fires. v1 chooses correctness-of-presence over precision-of-attribution: a hit overlay that occasionally over-attributes is more useful than one that under-attributes — the operator's instinct on a coloured node is "this method (or one of its overloads) was called", which matches the v1 hook templates' own granularity. Per-overload precision is a v2 concern that needs both a hook-template payload extension and an aggregator-contract bump; LIMIT-002's resolution explicitly defers this to v2.
    - **What 4.8 explicitly does *not* ship:** (a) a second WebSocket pushing graph-hit events — the polling cadence is sufficient and adding a parallel WS would be redundant data + doubled producer cost (same reasoning as 4.6's "no parallel WS for /hooks /scope"); (b) per-overload hit attribution — see method-overload caveat; (c) animated "pulse" on first-hit transition — visually noisy on a dense graph, deferred until operator feedback says it's needed; (d) historical-hit replay across page reloads — the overlay reflects the in-memory ring, which a uvicorn restart clears; the JSONL trace persistence (4.5) is the durable record, not the overlay; (e) clearable / mutable hits — `hitsByMethod` is `ReadonlyMap` by type so the parent hands the same reference across renders without us mutating it.
  - **Hook Lab graph-pane UI (sub-step 4.2 specifics, ratified 2026-04-27):**
    - **Cytoscape extension set:** `cytoscape@^3` + `cytoscape-dagre` (focus subgraph, LR) + `cytoscape-cose-bilkent` (package overview) + `cytoscape-popper` + `tippy.js@^6` (rich hover tooltips). The richer extension set is preferred over a minimal install because the call-graph pane is dense — without first-class tooltips, operators have to click every node to read its full method signature and reflection flag, which breaks the "scan visually first, click to drill" workflow. Bundle cost (~370 KB gzip incremental) is acceptable for a power-user RE tool.
    - **Default view = package overview** (cose-bilkent, package nodes labelled with `<pkg>` + class/method counts, cross-package edges weighted by aggregated call count). **Drill-down = right-click "Focus subgraph here"** with a 1–6 hops stepper (default 2) calling `/api/graph/{app_id}/neighbors/{id}` and laying the result out with dagre LR. Strategy (c) virtualisation from the original 4.2 row in `TASKS.md` is intentionally skipped — package-level aggregation already collapses 10K+-method apps into a handful of nodes for the first-load layout, so we don't need a virtualisation layer in v1.
    - **Click target file format = Java** (via the existing `/api/code/{app_id}/file?path=<rel_path>` endpoint, where `rel_path` is derived by `util/smaliClassToFile.ts`'s `com.example.Foo$Inner` → `com/example/Foo.java` rule). The call graph stores Smali line numbers but Smali files are not currently served and operators want Java first; `CodeView`'s existing `emphasizeMethod` prop carries the location signal. Smali side-by-side is deferred to a later sub-step.
    - **Click-to-code surfaces both an in-tab CodeView and a cross-tab Inspect link.** The in-tab `HookLabCodeView` (a thin `CodeView` wrapper) keeps the Hook Lab workflow self-contained for fast scanning; a "Open in Inspect" link in the right-click context menu pumps a new `pendingCodeNav` field through `WorkbenchContext` to switch tabs and hand off to `InspectTab`'s existing `handleSelect` flow when the operator wants the full Inspect-tab experience (file tree, find-and-replace, scroll-to-line).
    - **External nodes hidden by default**, exposed via a top-of-pane `Show external` toggle that re-fetches with `include_external=true`. Matches the backend's default and keeps the package-overview readable on apps with many third-party SDKs. Edges connecting hidden externals are also dropped client-side so the rendered subgraph stays self-consistent.
    - **No Cmd+K palette in v1** — top-of-pane inline filter input live-hides non-matching nodes (matches `class_name` / `method_name` / `package` substring, case-insensitive). Matches the existing `CodeView` find UX so operators don't need to learn two search modes.
    - **No frontend test runner is configured project-wide**, so 4.2 ships without frontend unit tests (consistent with all prior frontend deliverables). The backend contract the frontend consumes is locked instead by 3 new field-shape tests in `tests/test_graph_routes.py` (list / status / neighbors response shapes); a future schema rename in `androscan/analysis/call_graph.py` therefore can't silently break the UI without a test failure.
  - **frida-server provisioning:** **operator-managed for v1.** Androscan does not push/start frida-server. A new probe in `androscan/web/health_probes.py` checks (a) frida CLI on the host, (b) frida-server reachable on the device, (c) host/server version skew. Results surface as a Settings → Status card (same pattern as the existing adb / jadx / Ollama cards). No `adb push frida-server` helper in v1; deferred to v2 if operator demand justifies it.
  - **Frida adapter foundation (sub-step 4.3 specifics, ratified 2026-04-27):**
    - **Two readiness cards, not one:** the existing `tools.frida` host-CLI card (already populated by `probe_frida_version`) is kept as-is; a new `tools.frida_server` card combines `probe_frida_server` (device-side reachability via `adb shell pidof frida-server`) + `probe_frida_version_skew` (host CLI vs. `frida-server --version`, severity `None` / `"minor"` / `"major"`). Two cards because the operator's remediation differs (host CLI = `pip install frida-tools`; device side = push/start the matching `frida-server` build). Together they fulfil DEC-021's "Hook Lab readiness rollup probe" follow-up; a fancier rollup is left to 4.8 if needed. The frontend `rollupGlobal` treats Frida readiness as **yellow** (non-critical) so a missing frida-server doesn't redden the global health dot for operators running purely static workflows.
    - **Full session lifecycle in 4.3, but no persistence:** `FridaClient.attach(package)` / `FridaSession.load_script(js)` / `detach()` / `detach_all()` are real, with the on-message callback landing events in a per-session `collections.deque(maxlen=N)`. JSONL persistence to `apps/<app_id>/<run_ts>/frida/<session>.jsonl` is **deferred to 4.5** because it needs a `<run_ts>` allocator and a session-lifecycle owner that naturally lives with the Inject UI; in 4.3 traces are in-memory only and survive only until the FastAPI app shuts down (where `detach_all()` is invoked from the `@app.on_event("shutdown")` handler).
    - **Two classes, not one:** `FridaClient` (one per workbench process; lazy `frida` import via the `_frida_python()` test seam; holds the device handle) and `FridaSession` (one per attach; pid/package, ring buffer, script handle, message callback). Mirrors the natural Frida lifecycle — every Frida workflow is "attach → load → message → detach" — and keeps the test surface for `attach_all` / `detach_all` separate from the per-session ring-buffer + event-shape contract that 4.5–4.7 will consume.
    - **Optional-dep handling mirrors RAG (DEC-018):** `frida` is imported lazily inside `_frida_python()`; `FridaClient.__init__` calls it; on `ImportError` raises `FridaUnavailableError("frida not installed. Install with: pip install -e '.[frida]'")`. The default `pytest` suite stubs `_frida_python` via `monkeypatch.setattr` and never imports the real `frida` — so 4.3 does not regress the no-extras install path.
    - **`pyjsparser` pinned but dormant:** added to the `[frida]` extra now per `docs/TASKS.md` row 4.3 so 4.5 doesn't have to touch `pyproject.toml` or first-run setup; it is **only** consumed by 4.5's Inject-button JS pre-validation.
    - **`--setup` now installs `[dev,rag,frida]` by default** from 4.3 forward (per the original DEC-023 follow-up). One-line change in `androscan/internal/first_run_setup.py`: `.[dev,rag]` → `.[dev,rag,frida]`.
    - **`device` pytest marker introduced in 4.3** even though no device-touching tests exist yet — registered in `pyproject.toml` `[tool.pytest.ini_options].markers` so 4.4–4.7 can opt into real-device runs without a separate churn commit. `pytest -m "not device"` is the CI default; the default `pytest -q` deselects nothing in 4.3 because no tests carry the marker yet.
    - **Per-app safety knobs deferred to 4.5:** `hook_target_package_prefix` and `auto_attach_on_session_start` are explicitly **not** added to `per_app_settings.py` in 4.3 because there's no enforcement point yet — adding the keys without consumers would be dead code and would silently appear in the per-app Settings UI as unused fields. They land in 4.5 alongside the Inject route they gate.
    - **Trace ring-buffer size is a global config knob, not per-app:** new `frida_trace_ring_buffer_size` (default 5000, clamped `>= 100`) wired through `Config` / `CONFIG_FIELD_MAP` / `LIVE_RELOADABLE_FIELDS` / `_merge_from_yaml` / `ANDROSCAN_FRIDA_TRACE_RING` env / `global_config.yaml`'s new `frida:` section. Per the parent decision: the limit is a UI/memory cost, not a per-app policy. Live-reloadable because it only affects new sessions; nothing in flight cares.
    - **No `/api/frida/*` HTTP routes in 4.3:** the adapter has no HTTP callers in 4.3 — readiness flows through `/api/status/global`'s existing payload, and the adapter is reached internally via `app.state.frida_client`. Adding REST surface without UI consumers would be premature; routes land in 4.5+ when there's UI to drive them.
  - **Hook template library (sub-step 4.4 specifics, ratified 2026-04-27):**
    - **One module per template, dataclass per `TEMPLATE` symbol:** templates live as Python modules under `androscan/adapters/frida_hooks/<id>.py`, each exporting a single module-level `TEMPLATE: HookTemplate` (frozen dataclass). Mirrors `androscan.skills`'s discoverable-Python-module idiom; YAML / JS-file pair-files were considered and rejected because (a) the Python form lets us add typed validators per parameter in v2 without a new file format, and (b) editor experience for hand-rolled JS plus parameter schema in one place is materially better than three coordinated files.
    - **Discovery is explicit, not glob-based:** an `_TEMPLATE_MODULES: tuple[str, ...]` in `__init__.py` lists the importable module paths; `discover()` walks it, calls `importlib.import_module`, and registers the module's `TEMPLATE`. Auto-globbing the directory was considered and rejected because a stray `frida_hooks/scratch.py` during development would silently become a registered template — the explicit list makes "what ships" auditable on one line.
    - **`HookTemplateParam` is intentionally flat:** `(name, description, required: bool, default: str)` — no per-param type, no validators, no enum. v1 schemas are stringly-typed; the renderer coerces every supplied value to `str` before substitution. Typed validators (regex / int range / java-identifier) are deferred to v2 alongside 4.7's LLM-side schema-aware fill — adding them now would lock in a contract the LLM skill hasn't been built against.
    - **Renderer contract (`render(template, params) -> RenderedHook`):** validates in three explicit stages — (1) reject unknown keys (drops typo'd LLM output early; an unknown key surfaces as `HookParamError`, not silent omission); (2) reject missing or empty / `None` for `required=True` params; (3) fill `default` for omitted optional params. Then `str.format`s **the same merged params dict** through both `js_template` and `pentester_summary_template`. A single dict means the JS placeholder vocabulary and the summary placeholder vocabulary are guaranteed to use the same identifiers — no risk of "the JS shows `{class_name}` while the summary references `{cls}`". Output `RenderedHook` carries `template_id` + post-substitution `js` + post-substitution `summary` + the merged `params_used` dict so 4.5's UI / 4.7's skill / future audit logging can persist the exact inputs that produced this script.
    - **Error taxonomy is intentionally thin:** `HookTemplateError` (base) → `HookTemplateNotFound` (unknown id; the message lists the registered ids alongside) + `HookParamError` (one class for missing-required / empty-required / unknown-key — the consumers want one `except` block plus a human-readable reason, not three sibling exception types). Splitting into separate classes per error mode was considered and dropped — the call sites (Inject UI, LLM skill) all surface `str(e)` to the operator, so the structural distinction would be untyped from their perspective anyway.
    - **JS literal-brace escaping:** because both bodies render via `str.format`, every literal `{` / `}` in the JS body must be doubled to `{{` / `}}`. This is not a separate validation step — it's enforced indirectly by the registry-walk render test: any unescaped brace surfaces as a `KeyError` from `str.format` during `TestRegistryFailClosed.test_template_renders_with_placeholder_inputs`, which fails CI before review.
    - **Fail-closed registry walk (the DEC-023 promise):** `TestRegistryFailClosed` is parametrised over every entry in `_TEMPLATE_MODULES` and asserts seven structural invariants per template — module exports `TEMPLATE: HookTemplate`, `TEMPLATE.id` matches the module basename, `js_template` non-empty, `pentester_summary_template` non-empty, both `str.format` placeholder sets ⊆ declared params, every `required=True` param appears in the JS *or* the summary (no dead schema fields), and the template renders cleanly under `render(...)` with stub inputs. A new template with a stub or missing summary, drifted placeholders, or an unused-required param fails the suite at this layer — the operator-facing consent surface stays honest by construction.
    - **No `pyjsparser` integration in 4.4:** the parser was pinned by 4.3 in the `[frida]` extra but stays dormant in 4.4 — JS pre-validation is a 4.5 concern (it gates the Inject button and surfaces inline error positions in Monaco). Calling it from `render()` would mean the renderer fails on parse errors *during* template authoring, which is the exact case the test-time render covers more cleanly anyway.
    - **No LLM skill in 4.4:** `generate_frida_hook` is explicitly 4.7's deliverable. 4.4 closes the contract those layers will consume (`HookTemplate` schema + `RenderedHook` shape + the error taxonomy); 4.5 wires it into the Inject UI; 4.7 plugs the LLM in to fill parameters.
    - **No HTTP routes in 4.4:** identical justification to 4.3 — there are no consumers yet. The Hook Lab UI will get template-listing / rendering routes in 4.5 alongside the Inject button they back.
  - **LLM-tier skills (sub-step 4.7 specifics, ratified 2026-04-27):**
    - **Two skills, two consent classes, one registry hook.** `androscan/skills/query_call_graph.py` (read-only — `requires_confirmation=False`) and `androscan/skills/generate_frida_hook.py` (consent-class — `requires_confirmation=True`) are the v1 LLM-tier surface for Hook Lab. Both are registered in `androscan/skills/__init__._SKILL_MODULES`. The new `requires_confirmation: bool = False` field on `SkillMeta` (added in `androscan/skills/base.py` per DEC-022's "build the protocol now even with zero skills using it") is no longer hypothetical: `generate_frida_hook` is its first real consumer, and `tests/test_skills.py` now invariant-checks that pipeline + exploit-tier skills never accidentally opt in (DEC-022's consent class is scoped to the LLM-driven loop only).
    - **`query_call_graph` fail-open contract (mirrors `search_decompiled_sources`):** the skill wraps `androscan.analysis.call_graph.list_graph` / `neighbors` / `paths` with three modes — `overview` (top-N nodes filtered by package prefix), `neighbors` (callers / callees of a single `node_ref`), `paths` (BFS pathfinding between `source` and `target` with hop / count caps). Resolves `app_id` from the explicit param OR `run_folder.parent` (same fallback ladder as `resolve_ui_element`). On *every* unavailability mode — missing app context, unknown explicit `app_id`, decompile cache not ready, call graph not built, ring buffer empty — the skill returns `success=True` with empty data and a clear `[query_call_graph] …` text so the LLM can read "nothing here, pivot" and not loop on a hard failure. **Why fail-open and not fail-closed:** the LLM agentic loop (DEC-022) treats `success=False` as a real error worth surfacing to the operator; treating "no graph yet" as an error would force an alarm UI for a normal first-touch state on a freshly decompiled app. Parameter clamping is done in-skill (`limit ≤ 5000`, `max_hops ≤ 12`, `max_paths ≤ 50`) rather than relying on backend-side clamps so the deterministic human-readable summary the skill ships always reflects the actually-applied limits.
    - **`generate_frida_hook` is intentionally prep-only:** the skill takes `template_id` + `params` + optional `rationale`, calls `frida_hooks.render_by_id`, and returns `{template_id, js, summary, params_used, sensitive_apis, rationale}` + a human-readable `text` containing a JS preview (first 8 lines / 600 chars) + the deterministic pentester summary + the sensitive-APIs list + an **"Operator action required: review the JS + summary above, then stage / inject from the Hook Lab UI. This skill does not attach to a process or inject the script."** footer. **It does not touch Frida.** It does not call `attach`, does not call `load_script`, does not call `set_persistence_path` — the operator is the *only* mechanism that turns the rendered JS into an injection (via the existing 4.5 Stage→Inject UI). This is the strongest possible interpretation of DEC-023's Option-A confirmation UX: even with no consent SSE flow wired yet, an LLM call to `generate_frida_hook` cannot side-effect the device. When the agentic loop lands, `requires_confirmation=True` becomes belt-and-braces over what is already a structurally safe surface.
    - **Schema-aware error text:** `HookTemplateNotFound` / `HookParamError` are not just propagated — they are unwrapped and reformatted before being returned as `SkillResult.text`. Unknown `template_id` returns the list of valid template ids alongside the error; missing-required or unknown-key errors return the declared schema (`name=…?` for optional params with defaults; `name` for required) so the LLM sees enough context to self-correct on the very next turn without needing a separate "describe the schema" round-trip. `params` non-dict returns a typed-message + the schema. **Why bake the schema into errors:** the chat agentic loop's per-skill output budget is ~6 KB (DEC-022); a separate "describe schema" skill would burn an additional turn + budget for what is naturally part of the failure context.
    - **Frontend chat-attachment payload composition (HookLabTab, no new components):** `HookLabTab.tsx` polls `/api/frida/sessions/{id}/hooks` + `/api/frida/sessions/{id}/events?limit=30` every 2.5 s when an active session is pinned (cancellation-safe; clears on session swap / detach), and `useMemo`-builds a `ChatAttachment[]` for `<ChatDock />` with three new attachments: `default` for the selected method header (class / method / java_rel_path / smali_id), `code` for the decompiled Java source (capped client-side at 6_000 chars to mirror backend `ATTACHMENT_BUDGETS["code"]` — the operator's "show context" preview matches what the model sees post-truncation), and `frida_summary` for a JSON document combining the active session's hooks aggregate + the last-30 trace-event tail. `HookLabCodeView` lifted its `source` state up via a new `onSourceLoaded?` callback so the parent has the full text without re-fetching. `buildHookChatContextSummary` was rewritten to enumerate exactly what's being sent (selected method, code-attachment cap, hook-row count, event-tail count, refresh cadence) — operators see the actual payload composition, not a stale boilerplate. **Why the same 2.5 s cadence as the Hooks/Scope panels (4.6):** they read the same in-memory ring; sharing the cadence means the chat attachment, the Hooks panel, and the Scope panel are always referencing the same observed state ± 2.5 s, which makes "ask the LLM about what I'm looking at" a straightforward operator interaction.
    - **What 4.7 explicitly does *not* ship:** (a) wiring `query_call_graph` and `generate_frida_hook` into `androscan/web/chat.py`'s agentic loop — the chat path is still single-pass (ISSUE-009) and the bounded `while turn < max_turns` refactor + the `skill_request` / `skill_result` / `skill_pending` SSE event vocabulary live under DEC-022 and remain pending; (b) the Cytoscape graph overlay highlighting hit nodes — that's 4.8's deliverable and consumes the same `/hooks` aggregator endpoint shipped in 4.6; (c) typed parameter validators for hook templates (regex / int range / java-identifier) — `HookTemplateParam` stays stringly-typed in v1 per the 4.4 sub-bullet, the LLM sees coerced strings, and schema-aware error text is sufficient feedback for the parameter-fill task; (d) free-form LLM JS — explicitly v2 per the parent decision; (e) per-app `hook_template_allowlist` — the LLM sees every registered template, gating happens at the operator-Inject step, not at the LLM-discovery step.
  - **Scope inspector + hooks/stats panel (sub-step 4.6 specifics, ratified 2026-04-27):**
    - **Sixth v1 hook template, slot in:** the original DEC-023 plan called out "five templates for v1" (entry/exit log, SSL pinning bypass, crypto, SharedPreferences, Intent). Sub-step 4.6 adds a sixth — `androscan/adapters/frida_hooks/scope_inspector.py` — and explicitly amends that count to **six**. The new template keeps the same 3-param shape as `entry_exit_log` (`class_name`, `method_name`, `event_label`) so the call-graph node prefill flow from 4.2 keeps working; the divergence is purely in the JS body, which walks `this.getClass().getDeclaredFields()` per call (with `Field.setAccessible(true)` and per-field try/catch so a single throwing getter never voids the whole snapshot — failures land inline as `"<unreadable: …>"`). It emits two payload variants the new aggregator pattern-matches on: `{phase:"entry", class, method, args, this_class, this_fields:{f: String(v), ...}}` and `{phase:"exit", class, method, return, this_fields:{...}}`. The 4.4 fail-closed registry walk auto-parametrises over the new template — the seven structural invariants are picked up for free, so adding a v1.7 template later only needs +1 smoke test, not +8.
    - **Aggregation contract (the introspection promise):** `androscan/web/frida_routes.py` gains two pure helpers — `_summarize_hooks(session)` and `_summarize_scope(session)` — that iterate `session.events()` *once per request*, group by `(payload.class, payload.method)`, and never touch Frida I/O. `_summarize_hooks` counts `phase=="entry"` events as hits, tallies `phase=="exit"` returns into `top_returns: [{value, count}]` (capped at 5, sorted by count desc with stable insertion-order tiebreak; values truncated to 256 chars so a 200 KB stringified buffer can't blow up the response), and sorts rows by hits desc → last_seen desc → name. `_summarize_scope` adds one extra discriminator — `payload.this_fields` must be a dict — which is what makes scope_inspector events distinct from `entry_exit_log` events sharing the same wire shape; it then keeps the most-recent entry **and** most-recent exit *independently* per `(class, method)` so a method that's been entered-but-not-yet-returned still surfaces useful entry data. Both helpers are defensive against malformed / non-dict payloads (silent skip, never raise) — keeps the summary endpoints robust against a buggy template the operator forgot to remove. **Why pure helpers, not a class:** the data is read-mostly; the helpers are independently unit-testable without standing up a session; the same pattern (single-pass, group-by, cap-and-sort) is exactly what the call-graph overlay in 4.8 will need, so the helpers are designed to be reused with light shape adjustments.
    - **Two introspection routes, no new WebSocket:** `GET /api/frida/sessions/{id}/hooks` returns `{session_id, hooks: [...]}`; `GET /api/frida/sessions/{id}/scope` returns `{session_id, snapshots: [...]}`. Both 404 on unknown session and both work *without* the `[frida]` extra installed (they only read the in-memory ring). **Why polling, not a parallel WS:** `/hooks` and `/scope` are pure aggregations over the *same* ring buffer the trace WS consumes — a parallel WS would be redundant data, doubled producer cost, and a second backpressure surface. The 2.5 s poll lag is invisible to a human eye scanning a summary table and means a paused trace WS doesn't pause the Hooks/Scope panels (the underlying ring keeps receiving events). Operators who want the raw stream still have the trace WS; operators who want digestible summaries get them on a cheap REST contract.
    - **Frontend: tab strip on the right pane, Trace stays default.** `HookLabTab.tsx`'s right-pane bottom slot becomes a 3-button tab strip — `Trace | Hooks | Scope` — defaulting to Trace so existing operators see no behaviour change. Hooks + Scope are disabled with an explanatory tooltip when no session is active (mirrors the Inject-button gating pattern from 4.5 — three disabled-reasons each with its own tooltip). Two new components live alongside `FridaTracePanel`: `HookStatsPanel.tsx` (polls `/hooks` every 2.5 s, renders one row per `(class, method)` with hits / template id badge / last-seen relative time / top-3 return values) and `ScopeInspectorPanel.tsx` (polls `/scope` every 2.5 s, renders collapsible `<details>` cards per method, computes the entry-vs-exit `this_fields` diff client-side, highlights changed keys with a `color-mix(in srgb, var(--accent) 15%, transparent)` background, surfaces an `N mutated` pill on the card header). **No Monaco for the scope view:** the data is shallow JSON — args + this_fields + return — and a `<pre>` block is the right primitive; the panel is air-gap-friendly while ISSUE-010 (Monaco CDN dependency) stays open. ~360 lines of new CSS under `.right-pane-tabs-*` / `.hookstats-*` / `.scope-*` namespaces, same grep-affordance discipline as the `.hookbuilder-*` / `.frida-session-*` / `.trace-*` prefixes from 4.5.
    - **What 4.6 explicitly does *not* ship:** (a) the LLM-tier `generate_frida_hook` skill — that's 4.7's deliverable; (b) modify-return / mutation UI — explicitly v2 per the parent decision, scope_inspector is read-only by design (it captures field values, never writes them); (c) the Cytoscape graph overlay highlighting hit nodes — 4.8's deliverable, will consume the same `/hooks` endpoint shipped here; (d) consumption of `auto_attach_on_session_start` from 4.5 — keyword stays wired in the override store but no callsite reads it yet, awaiting a chat / agentic loop driver in a later sub-step.
  - **Hook builder + Stage→Inject flow (sub-step 4.5 specifics, ratified 2026-04-27):**
    - **Eight routes, one factory:** `androscan/web/frida_routes.py` exposes `GET /api/frida/templates` + `/{id}`, `POST /api/frida/render`, `POST /api/frida/sessions`, `GET /api/frida/sessions` + `/{id}`, `DELETE /api/frida/sessions/{id}`, `GET /api/frida/sessions/{id}/events`, `GET /api/frida/sessions/{id}/export`, and `WS /ws/frida/sessions/{id}/trace`. Built via `build_frida_router(get_config, get_client, run_folder_root, web_apps_root)` and wired through `androscan/web/app.py`'s same DI seams as `status_routes` / `settings_routes` — keeps live-`Config` reload semantics + per-app paths consistent with the rest of the workbench. Routes that don't need a device (`templates`, `render`) still work when `frida` isn't installed (just `pyjsparser`-driven validation on `render`); session routes return **503 `frida_unavailable`** with a copy-pasteable install hint — same shape as the readiness probe in 4.3 — instead of a generic 500, so the operator gets actionable remediation.
    - **JSONL persistence design — three guarantees that justify the writer-thread + queue cost:** (1) a slow disk can never block the Frida message thread or the asyncio loop because the producer just `put_nowait`s onto an unbounded `queue.Queue`; (2) a single bad event bumps `persist_dropped` (via `_jsonl_fallback`'s `default=str` retry) instead of killing the session; (3) the wire format on the WS and the on-disk JSONL are byte-identical because both go through `_event_to_jsonable`, so the export endpoint is a thin `StreamingResponse` over the file. `detach()` poisons the queue and `join`s the writer so all events flush before the route returns — operators who Detach right after Inject still get a complete `<session>.jsonl`. Persistence is opt-in per session (`persist=True` default in the create body) so hook authoring with `persist=False` doesn't leave breadcrumbs in `apps/<app_id>/<run_ts>/frida/`.
    - **WS replay-then-stream contract:** the WebSocket route drains the session's ring buffer first (catch-up for late joiners — late `connect` after Inject + 30 s of activity still gets the full 5000-event tail), then registers a non-async `on_event` hook that bounces events to an `asyncio.Queue(maxsize=2000)` via `loop.call_soon_threadsafe`. Queue overflow drops one item and emits a `{type: 'drop'}` notice rather than blocking the Frida message thread or growing memory unboundedly; the frontend coalesces consecutive drops into a single counter pill. The WS stream goes through `_event_to_jsonable` for byte-identical wire shape with the JSONL file, so a client can reconstruct exactly what was persisted by replaying the WS messages.
    - **Allowlist enforcement is server-side, not advisory:** `POST /api/frida/sessions` resolves `effective_settings(global_view, per_app, app_package=…)` (via the new `_app_package_from_meta` helper in `settings_routes.py`) to get `hook_target_package_prefix`, defaulting to the app's `app_meta.json` package id when nothing is set, and rejects with **403 `hook_blocked`** if `target_package` doesn't start with the prefix. The per-app Settings UI can widen the prefix (e.g. `com.target` to also match `com.target.staging`) but never narrower-than-default behaviour without explicit operator action. The allowlist sits **before** Frida attach, so a misconfigured request never produces a half-attached session that needs cleanup. **Why server-side, not client-side gating:** the LLM-tier `generate_frida_hook` skill (4.7) will eventually call this endpoint directly without going through the UI; gating in the frontend would leave a hole the LLM could drive a truck through.
    - **JS pre-validation as a UX gate, not a security gate:** `parse_frida_js` runs `pyjsparser.PyJsParser().parse()` and returns `ParseResult{ok, error, line, column, available}`. When `pyjsparser` is missing (the `[frida]` extra is opt-in for the default install) it returns `available=False` rather than a hard error — the Inject button is enabled in that case (we don't gate on a tool we don't have). When `pyjsparser` *is* available, `POST /api/frida/render` returns the parse result alongside the rendered JS, so the frontend can attach inline Monaco markers via `setModelMarkers(model, "androscan-jsparse", …)`; `POST /api/frida/sessions` runs the same parser and rejects with **400 `render_parse_error`** + `{message, line, column}` if it fails, so an LLM-generated payload can't sneak past a UI that ignores its own markers. Errors here mean the *renderer* drifted (template author bug or LLM filling unescaped braces), not that the operator did something wrong — the Frida runtime would have surfaced the same error one step later, after the operator clicked Inject; catching it pre-attach keeps the ring buffer / persistence file clean and gives a precise line/column instead of a Frida wraparound stack.
    - **Per-app hook settings ride the existing override store:** `_HOOK_KEYS = {hook_target_package_prefix, auto_attach_on_session_start}` is added to `androscan/web/per_app_settings.py` alongside the existing `_KNOWN_TOP_LEVEL_KEYS` flow; `effective_settings` gains an optional `app_package: Optional[str]` for the prefix default, threaded through `settings_routes.py`'s `get_app_settings` / `put_app_settings` / `reset_app` via the new `_app_package_from_meta` helper that reads `apps/<app_id>/app_meta.json`. **Why not a separate `apps/<app_id>/hook_settings.json`:** a separate file would mean a separate atomic-write code path, a separate read-merge step, a separate UI sub-panel, and three separate test paths — for a two-key block. Reusing the existing per-app settings store keeps DEC-020 coherent (one per-app config file end-to-end) and keeps the Settings tab's existing UI affordances usable for hook config. `auto_attach_on_session_start` is wired into `coerce_partial_update` and `apk_overrides_summary` but **not** consumed in 4.5 — the consumer is 4.6+ when the chat / agentic loop can drive a session-start event; 4.5 just makes the knob non-broken so operators can pre-stage their override. Adding the key without a consumer is fine here (unlike the 4.3-deferred case) because the consumer arrives in the very next sub-step.
    - **Run-folder layout for trace artifacts:** persistence writes `apps/<app_id>/<run_ts>/frida/<session_id>.jsonl` — same `<run_ts>` shape as chat transcripts (`apps/<app_id>/<run_ts>/chat/<tab>.jsonl`) and exploit-verification artifacts. Reports / triage will be able to cite exact frida observations later by `<run_ts>` + `<session_id>` without inventing a third namespace. `<run_ts>` is taken from `run_folder_root` if a current run is active, otherwise a freshly created run folder is used — mirrors the same fallback as exploit-verification.
    - **Frontend: Monaco editor, but with a CDN footnote.** `@monaco-editor/react@^4.6.0` is added; the read-only JS view in `HookBuilder.tsx` uses `setModelMarkers(model, "androscan-jsparse", …)` for inline `pyjsparser` errors — first-class editor affordance, no toast popups. **Bundle bookkeeping:** `@monaco-editor/react`'s default loader lazy-fetches Monaco from a jsdelivr CDN, so the main bundle stays at ~1.13 MB / 354 KB gzipped instead of doubling. Self-hosting Monaco (single `loader.config({paths: {vs: '/monaco/min/vs'}})` + a postbuild copy of `node_modules/monaco-editor/min/vs/` into `androscan/web/static/monaco/`) is a real future requirement — air-gapped pentester laptops shouldn't fetch from a CDN — but it's deferred to v2 with a `KNOWN_ISSUES.md` note rather than landing in 4.5. The trade-off: 4.5 keeps the FE bundle small and ships now; v2 gets full air-gap support behind one config call. Documenting the CDN behaviour explicitly here means an operator who hits it in air-gap testing can find the fix in five minutes.
    - **Frontend: `useFridaTrace(sessionId)` owns the WS lifecycle.** A single React hook in `src/api/frida.ts` handles connect / replay / stream / reconnect-on-error, capped buffer at 2000 events, pause/resume with overflow buffering, drop-coalescing, and a `dropped` counter surfaced in the panel header. Components that consume it (`FridaTracePanel.tsx`) just render — they never touch `WebSocket` directly. **Why a custom hook rather than `react-use-websocket`:** the replay-then-stream contract is server-defined (the `{type: 'drop'}` notice is a wire-protocol thing, not a client-state thing) and the buffer/coalesce semantics are tied to UI state, so wrapping a generic library would just hide where bugs would actually live. The hook is ~120 lines; a generic library wrapper would be the same lines, just spread across two files.
    - **Frontend: layout changes are scoped to `HookLabTab.tsx`.** Three columns: graph (left, was already there from 4.2) + (CodeView, HookBuilder, ChatDock) stacked centre + (SessionsList, TracePanel) stacked right. Selected graph node prefills `class_name` / `method_name` in `HookBuilder`; successful Inject pins the trace pane to the new session id and the sessions list refreshes eagerly. ~480 lines of new CSS in `App.css` are namespaced under explicit `.hookbuilder-*` / `.frida-session-*` / `.trace-*` prefixes so the grep-affordance tells operators which file owns which style. The Inject button is gated on `parse.ok || !parse.available` + missing-required + render-in-flight — three independent conditions, each with a hover tooltip explaining *why* it's disabled, so operators don't have to guess.
    - **What 4.5 explicitly does *not* ship:** (a) the LLM-tier `generate_frida_hook` skill — that's 4.7's deliverable, this sub-step closes the contract it will consume (the `POST /api/frida/render` and `POST /api/frida/sessions` shapes); (b) scope inspector / hooks-stats panel — 4.6 owns those, behind the same `/api/frida/sessions/{id}/...` namespace; (c) consumption of `auto_attach_on_session_start` — keyword is wired into the override store but no callsite reads it yet; (d) Monaco self-hosting — see CDN footnote above; (e) `modify-return` / mutation UI — explicitly v2 per the parent decision, the UI is read-only by design.
  - **Hook source policy (v1):** **template library + LLM parameter-fill only — no free-form LLM JS.** The library lives at `androscan/adapters/frida_hooks/` and ships with five templates for v1: method entry/exit log, SSL-pinning bypass, crypto, SharedPreferences, and Intent. Each template defines a parameter schema (which the LLM-tier `generate_frida_hook` skill fills) and a `pentester_summary_template: str` (Python `.format()`-style with the same parameter names as the JS template). The LLM only fills parameters; it never emits raw JS in v1. Free-form LLM JS is a future possibility but explicitly out of v1 scope.
  - **Confirmation UX (Option A — single Inject button):** the Hook Lab UI renders the parametrised JS (Monaco, syntax-highlighted) **plus a deterministic pentester-perspective summary** (rendered from the template's `pentester_summary_template` — what class/method is hooked, what the script observes/modifies, what data it might capture such as auth tokens or crypto material) above a single `Inject` button. The summary is a plain-text render — **no separate LLM call**, no probabilistic prose. This satisfies DEC-017's "explicit user confirmation for LLM-generated hooks" requirement. Option B (typed-confirmation phrase, sensitive-API allowlist) was considered and dropped — the operator is a pentester whose entire workflow is security-sensitive, so a typed phrase per hook would be permanent friction without proportional safety benefit.
  - **Trace persistence:** Frida `message` events stream to the UI ring buffer **and** persist to `apps/<app_id>/<run_ts>/frida/<session>.jsonl` alongside chat transcripts and exploit-verification artifacts. Reports / triage can cite exact frida observations later; tests can assert the on-disk shape without standing up a real device.
  - **Per-app safety knobs (in `app_settings.json`):** exactly two —
    - `hook_target_package_prefix: str` (default = the app's own package id; hooks targeting classes outside this prefix are rejected server-side before the Inject button can fire),
    - `auto_attach_on_session_start: bool` (default `false`).
    Notably **no `hook_template_allowlist`**: every template in `frida_hooks/` is available to the LLM by default. The earlier planning draft proposed an allowlist; it was dropped because gating templates per-app adds configuration burden with little safety upside (the templates are vetted on commit; the Inject confirmation gate already covers per-call review).
  - **Global perf knob (in `global_config.yaml`):** `frida.trace_ring_buffer_size: int` (default 5000 events). Not per-app — the limit is a UI/memory cost, not a per-app policy.
  - **JS pre-validation (Risk #1 mitigation):** the rendered JS is parsed with **`pyjsparser`** (pure-Python, ~30 KB, zero runtime deps — no Node.js requirement) before the Inject button is enabled. Parse failures show inline with the parser's error position; Inject stays disabled until the script parses. This catches template-rendering bugs and obviously malformed parameter substitutions without needing a real frida session to fail. `esprima-python` was considered but `pyjsparser` is smaller and sufficient for syntax-grade validation (we don't need full ECMAScript semantics).
- rationale:
  - **v2 fidelity hits the sweet spot:** direct invokes alone miss the most common Android pattern (callbacks dispatched through interface references); full reflection resolution is a separate hard problem (taint analysis on string concat) that would block Hook Lab indefinitely. The dashed-edge convention keeps the v2/v3 boundary visible to operators rather than hiding it.
  - **SQLite mirrors DEC-018:** one storage idiom across decompile cache, RAG index, and call graph keeps invalidation, backups, and ops mental model consistent. The `<sha>`-keyed cache directory is already the unit of invalidation; piggybacking on it is the lowest-friction choice.
  - **Operator-managed frida-server for v1:** automating frida-server push/start on rooted/Magisk/userspace-gadget devices is genuinely complex and varies per OEM. Surfacing version skew is a 90% win without owning that complexity; we can revisit when it becomes a real complaint.
  - **Templates + parameter-fill, no free-form JS:** Frida scripts can crash the target app or persist data exfiltration code; the v1 risk profile of letting the LLM emit raw JS is unattractive. Templates are reviewed on commit, parameter substitution is bounded, and the LLM stays in its strongest mode (structured JSON for parameter values, not novel code).
  - **Single Inject button + pentester summary:** for an operator whose entire job is touching security-sensitive surfaces, a typed-phrase ceremony per hook would burn through trust quickly. The pentester summary gives them the *information* needed to consent (what does this script *do*, in plain English, from their perspective) without making consent itself a chore. Deterministic rendering (vs. an LLM-generated summary) keeps the consent surface non-negotiable: the same parameters always produce the same summary, so an operator who Inject'd "log SharedPreferences reads with key prefix `auth_`" yesterday gets the exact same words today.
  - **Trace persistence:** parity with chat / exploit-verification artifacts; without it, frida is the one part of the workbench whose evidence trail evaporates on a uvicorn restart.
  - **Safety knobs are intentionally minimal:** every additional knob is a new failure mode and a new doc surface. `hook_target_package_prefix` covers the "don't accidentally hook Chrome" case; `auto_attach_on_session_start` covers the "I don't want frida running until I say so" case. Anything more granular can be added when a real workflow asks for it.
  - **Global ring buffer:** memory cost is uniform regardless of which app is being analysed; no per-app override needed.
  - **`pyjsparser` over `esprima-python` or a Node.js subprocess:** pure-Python keeps the install matrix simple (no `nodeenv`, no platform-specific binaries); syntax-grade validation is what we need, not full semantic checking; `pyjsparser` is small enough to vendor if upstream goes quiet.
- alternatives considered:
  - **v1 with v3 fidelity (full reflection resolution)**: rejected — open-ended scope, would push Hook Lab past the milestone budget. Captured as a v3 follow-up.
  - **JSON-blob storage** (the original DEC-016 wording): rejected for the same reasons as DEC-018 (cold-start cost, awkward partial-update semantics, no indexed lookups for `neighbors` / `paths`).
  - **Auto-provisioned frida-server**: deferred — see rationale above.
  - **Free-form LLM JS for hooks**: rejected for v1 — risk profile incompatible with the single-Inject UX. Reconsidered for v2 when telemetry shows which template gaps drive operators to ask for custom JS.
  - **Option B confirmation UX (typed phrase + per-API allowlist)**: rejected — friction-to-safety ratio is wrong for a pentester operator.
  - **Per-app `hook_template_allowlist`**: dropped during planning — adds config burden without an operator scenario that motivates it.
  - **`esprima-python` for JS pre-validation**: viable; `pyjsparser` is smaller and sufficient.
  - **Skip JS pre-validation, rely on frida's runtime errors**: rejected — runtime failures land *after* the operator clicks Inject, which is exactly the moment we should be most careful.
- tradeoffs / consequences:
  - **Virtual-dispatch resolution adds parser complexity** — class-hierarchy walks are bounded by the in-app type set (no framework classes), so the cost is linear in app size. Tests use small fixture Smali to assert the dashed-vs-solid edge classification.
  - **SQLite store adds a third per-app file under `<sha>/`** — operationally identical to RAG; no new ops complexity.
  - **`pyjsparser` and `frida` / `frida-tools` become Python deps.** `pyjsparser` is pure-Python (negligible install cost). `frida` and `frida-tools` add a real install step but are necessary for the feature to exist; they go under a new `pyproject.toml` extra `[frida]` so users who only want static analysis don't pay for them. `--setup` will install the extra by default once Hook Lab ships (parallel to how `[rag]` is currently handled).
  - **Operator-managed frida-server means a higher first-run-friction floor** — the Settings → Status card is the mitigation; the README will get a Hook Lab section pointing at `frida-server` install docs when sub-step 4.3 lands.
  - **Pentester-summary templates ship with each hook template** — adding a new template is now a two-deliverable change (JS template + summary template). Templates without a summary template fail the test suite; this keeps the consent surface honest by construction.
  - **No free-form JS in v1** means a small set of operator workflows ("hook a custom obfuscated method that doesn't fit any template") are blocked until v2. Acceptable for the v1 scope.
- follow-up:
  - **[done 2026-04-27, sub-steps 4.1 → 4.8 all landed — Hook Lab v1 complete]** Implement sub-steps 4.1 → 4.8 strictly linearly per `docs/TASKS.md` § Hook Lab v1 — sub-step backlog. One sub-step per Agent-mode session. Brief Ask-mode planning checkpoint at the top of 4.1 to settle the SQLite schema before code lands. **All eight sub-steps shipped in linear order across April 25–27 2026** — call graph backend (4.1), graph-pane UI (4.2), Frida adapter foundation (4.3), hook templates + renderer (4.4), Stage→Inject UI + JSONL persistence + 403 hook_blocked allowlist (4.5), scope inspector + hooks-stats panel (4.6), LLM-tier `query_call_graph` + `generate_frida_hook` skills (4.7), and the live Cytoscape overlay (4.8). DEC-023 transitions to **closed** for v1 scope; future Hook Lab work (free-form LLM JS, reflection resolution, per-overload precision, modify-return / mutation, self-hosted Monaco) is captured under the v2/v3 follow-ups below and `docs/KNOWN_ISSUES.md` rather than re-opening this DEC.
  - **[done 2026-04-27, in sub-step 4.3]** Add a "Hook Lab readiness" rollup probe to Settings → Status (frida CLI on host + frida-server reachable + version skew + target app installable). Two-card design landed: existing `tools.frida` (host CLI) + new `tools.frida_server` (device reachability + host/server version-skew, severity `None` / `"minor"` / `"major"`). Treated as **yellow** in `rollupGlobal` — non-critical for static-only workflows. "Target app installable" is deferred to 4.5 along with the Inject UI it would gate. Closes DEC-021's "Hook Lab readiness rollup probe" follow-up.
  - **[partial 2026-04-27, in sub-step 4.7]** Revisit DEC-022's consent-class hook to confirm Hook Lab's `requires_confirmation=True` skills (frida hook injection) interact correctly with the chat consent UI. Sub-step 4.7 has shipped the LLM-tier skills (`query_call_graph` read-only + `generate_frida_hook` consent-class — first real consumer of the `requires_confirmation` flag from DEC-022) and confirmed at the unit-test level that pipeline + exploit-tier skills cannot accidentally opt into the consent class. **Still open:** end-to-end confirmation that the chat consent UI (`skill_pending` SSE event + `POST /api/chat/skill_decision/{request_id}` round-trip) routes a consent-class hook through the Allow / Deny flow correctly — this is gated by the chat-loop refactor in DEC-022 itself (still single-pass per ISSUE-009) and lives outside Hook Lab's milestone budget. The structural safety is already in place: `generate_frida_hook` is prep-only by construction (no `attach`, no `load_script`, no device I/O — operator-driven Inject is the only path to side effects), so the consent UI is belt-and-braces over what is already a non-side-effecting LLM call.
  - **[done 2026-04-27, in sub-step 4.3]** Promote DEC-023 from Proposed → Active when sub-step 4.1 lands and the design is no longer hypothetical. Originally targeted at 4.1; missed there and folded into 4.3 since the design is now thoroughly de-risked across 4.1 (call-graph backend + SQLite schema), 4.2 (graph-pane UI), and 4.3 (Frida adapter + readiness signal).
  - Reconsider free-form LLM JS (v2) and reflection resolution (v3) once we have telemetry from real Hook Lab use.
- related docs:
  - `docs/DECISIONS.md` **DEC-016** (Smali-first call graph; storage clause amended in this commit to match)
  - `docs/DECISIONS.md` **DEC-017** (Frida user-confirmation requirement — Option A + the deterministic pentester summary fulfil it for v1)
  - `docs/DECISIONS.md` **DEC-018** (RAG SQLite store layout — Hook Lab's call-graph store mirrors it)
  - `docs/DECISIONS.md` **DEC-022** (Workbench chat agentic loop — its consent-class hook is the consent UI Hook Lab's `generate_frida_hook` will exercise once both are wired)
  - `docs/TASKS.md` § Hook Lab v1 — sub-step backlog (the implementation plan)
  - `docs/STATE.md` ("Not yet implemented" — Hook Lab pointer)
  - `docs/DESIGN_DOC.md` Phases 8 + 9
  - `docs/SAFETY_AND_SECURITY.md` (Frida adapter scope + per-app `hook_target_package_prefix` semantics — to be extended when sub-steps 4.3 / 4.5 land)
  - **DEC-024** (Phase 10 Behavior Trace — supersedes DEC-023's "Hook Lab" tab name with **Lab** going forward; reuses every Hook Lab v1 substrate unchanged: call-graph SQLite, `frida_hooks/` templates, `generate_frida_hook` skill, per-app `hook_target_package_prefix` allowlist, Frida overlay)

### DEC-024: Phase 10 — Lab tab "Behavior Trace" mode (gate identification + bypass planning) and the Hook Lab → Lab rename
- status: Active
- date: 2026-04-28
- owners: (project)
- context:
  Hook Lab v1 (DEC-023, completed 2026-04-27) shipped the Smali static call graph + Cytoscape pane + Frida adapter + Stage→Inject UI + LLM-tier skills. The planning conversation that produced this DEC observed two related concerns about how the v1 surface maps to real pentesting work:

  1. **The Cytoscape call-graph pane is infrastructure, not deliverable.** It's well-implemented, but a topological view of methods/classes is not how an experienced pentester actually navigates an APK. Operators want to know *what stops a particular UI behaviour from working* and *how to bypass it* — not which method calls which. The graph view answers a question operators rarely ask first; the questions they do ask first ("why doesn't this button work without a subscription?") get no first-class surface today.
  2. **The pentesting framing the tool was unconsciously optimising for ("source → sink reachability") describes mediocre mobile pentesting** — proxy with Burp, scan for hardcoded API keys, leave server-side weaknesses to web tooling, ship report. Advanced pentesters across mobile, desktop, and embedded targets work in **client-side trust manipulation**: identify the gate that controls a behaviour, manipulate the gate to flip the behaviour, verify dynamically. This loop is what AndroScan can uniquely automate because every substrate it needs already exists (call graph, decompile cache, Frida adapter, Mirror, RAG, chat).

  Phase 10 operationalises this around a new **"Behavior Trace" mode** living inside the existing Hook Lab tab. Concurrent with this DEC, the **Hook Lab tab is renamed "Lab"** to reflect the broader scope (gate-identification + manual hooks + topological graph all in one place), with the existing Cytoscape pane demoted from "headline view" to "Graph mode" alongside Trace and Manual Hooks.
- decision:
  - **Workflow framing locked: client-side trust manipulation.** The product's primary loop is *Anchor → Locate → Trace → Classify → Manipulate → Verify*, and it generalises across Android, iOS, desktop, embedded, and browser-extension targets. Source/sink reachability is intentionally **not** the framing — it remains a useful adjunct (e.g. for auditing exported components / IPC surfaces) but is not the headline workflow. Future contributors who reach for "let's add taint analysis" should re-read this DEC + the planning transcript first; taint is fine as a tactic, but it is not the strategy.
  - **Platform-neutral data model.** New module `androscan/analysis/trace_types.py` (ships in 10.1) defines `BehaviorAnchor`, `DecisionPoint`, `BypassPlan`, `MethodRef`, `FieldRef`. The shapes are platform-neutral on purpose; v1 ships an Android adapter only, but iOS / desktop / embedded adapters can land later as additional implementations of the same contract without invalidating the data model or the Lab UI. The Smali implementation is one adapter; the data model lives one layer above.
  - **Static enumeration + LLM interpretation (the key architectural split).** The static layer is *deterministic and auditable* — it extracts every conditional branch in a method's forward-reachable closure (≤ `MAX_TRACE_HOPS = 3` by default), runs intra-procedural backward slicing for predicate origin, and applies a heuristic deny/allow/neutral classifier (`System.exit`, `throw`, `Activity.finish()` without `setResult`, branch length, string-constant scoring against a curated regex list of "premium / locked / jailbroken / unauthorised" patterns). The LLM is invoked **once per anchor** with the populated structure + branch source snippets and is asked to (a) re-classify low-confidence gates, (b) author rationale strings, (c) propose template-bound bypass plans for cases the deterministic planner didn't cover. **Per-decision LLM calls are explicitly rejected** — they would multiply the round-trip count by ~5–10× per trace and burn DEC-022's per-turn skill-output budget without buying interpretation quality the per-anchor call doesn't already provide.
  - **Bypass planner is template-bound for v1.** Every `BypassPlan` references one of the existing `frida_hooks/` templates (or new ones added in 10.4) plus a parameter dict. Free-form LLM-generated Frida JS for bypass plans is **out of scope for v1** — that path already exists via `generate_frida_hook` (DEC-022's confirmation gate) and stays as a separate operator-driven path. The Trace planner emits structured suggestions; the operator (or the existing `HookBuilder.tsx` flow) materialises them into actual scripts.
  - **Closure bound = 3 hops, hard-capped at 6.** New `trace.max_hops_default: int = 3` and `trace.max_hops_hard_cap: int = 6` knobs in `global_config.yaml` (live-reloadable; wired through `Config` / `CONFIG_FIELD_MAP` / `LIVE_RELOADABLE_FIELDS` / env in 10.5). The default keeps LLM context bounded; the hard cap prevents an operator from accidentally requesting a 12-hop closure that would exceed the per-anchor LLM budget. Closure also has a `MAX_TRACE_METHODS = 30` second cap (in code, not config) — methods past that are listed but not fed to the LLM, with a "trace truncated, narrow the entry method" affordance in the UI.
  - **Decision extraction is intra-procedural.** Backward slicing for predicate origin walks within a single method body only — no aliasing, no field-flow analysis, no escape analysis. Honestly surfaced via `predicate_origin: None` in the data model when the slice can't resolve, and via a "trace may be incomplete" banner in the UI when any decision point in the closure has unresolved origin or crosses a `may_have_unresolved_reflection: true` node (carried forward from DEC-023). Interprocedural slicing is an explicit v2 concern, gated by operator demand and informed by the v1 false-negative rate measured on real apps.
  - **Cytoscape pane demoted, not deleted.** The existing `CallGraphView.tsx` keeps working unchanged; Lab gains a 3-mode left-rail switcher — `Trace | Manual Hooks | Graph` — defaulting to `Trace` once 10.6 lands. `Manual Hooks` is today's `HookBuilder.tsx` flow; `Graph` is the existing Cytoscape pane (with its existing Frida overlay). Operators familiar with Hook Lab v1 will see Trace mode by default after upgrade; a one-time tooltip on first launch after the upgrade explains the new default. Per-app "default mode" setting deferred to a 10.6 follow-up if operator complaints surface.
  - **Storage:** per-app SQLite at `apps/<app_id>/.decompiled/<sha>/trace.sqlite`, schema_version 1. Mirrors DEC-016 / DEC-018 / DEC-023's `<sha>`-keyed cache layout so invalidation is uniform (drop-the-file). `BehaviorAnchor` rows carry `(entry_method_smali_id, hops, payload_json, created_at)`; the renderer walks the closure on first request and writes the populated payload back so re-opening the same anchor is fast. Schema-versioned from day 1 to avoid migration pain in v2.
  - **Risk taxonomy on `BypassPlan`.** Every plan ships with `risk: "low" | "medium" | "high"` and a string `risks: string[]`. Examples: hooking `RootDetector.check()` is `low` (single call site, no side effects); hooking `String.equals` globally is `high` (touches every equality check in the JVM, observable behaviour change everywhere). The planner refuses to emit any plan above operator-configured threshold (default `medium`); high-risk plans are hidden behind an "Advanced" expander and require an explicit toggle to surface. Threshold lives in `global_config.yaml`'s new `trace:` section, live-reloadable.
  - **Trace itself is read-only.** `trace_behavior` (the new LLM-tier skill, ships in 10.5) is `requires_confirmation=False` (DEC-022) — it walks the call graph, reads decompiled source, and asks the LLM to interpret. Bypass *injection* still goes through the existing `generate_frida_hook` + Stage→Inject flow with all the consent / `pyjsparser` pre-validation / per-app `hook_target_package_prefix` allowlist that ships today (DEC-023 sub-steps 4.5 / 4.7). Phase 10 adds **zero new device-touching surface area** — every state mutation goes through the same paths Hook Lab v1 already established.
  - **Tests are deterministic.** LLM calls in 10.5 are mocked at the test boundary; static analysis in 10.1–10.4 is fixture-driven (small Smali files in `tests/fixtures/trace_smali/`). No device touching in default suite; the `device` pytest marker (registered in DEC-023 sub-step 4.3) covers any opt-in cases. Phase 10's test count target is ~80 new tests, mostly in `tests/test_decisions_*.py`, `tests/test_branch_classifier.py`, `tests/test_bypass_planner.py`, `tests/test_trace_behavior_skill.py`, `tests/test_trace_routes.py`.
  - **Hook Lab → Lab rename (forward-looking, not retroactive).** The tab is named **Lab** as of this DEC. Historical references — DEC-023's title (`Hook Lab v1 — Smali call graph + Frida adapter…`), the `### Hook Lab v1 — sub-step backlog` section in `docs/TASKS.md`, completed-task entries in `docs/STATE.md`, ISSUE-010 / ISSUE-011 / ISSUE-012 / LIMIT-002 entries in `docs/KNOWN_ISSUES.md`, §12.6 in `docs/SAFETY_AND_SECURITY.md` — **retain the original "Hook Lab" naming as accurate historical record.** Code rename (`HookLabTab.tsx` → `LabTab.tsx`, `HookLabCodeView` → `LabCodeView`, `tabs/HookLabTab` URL hash from `#/hook` to `#/lab`, the per-tab chat prompt key in `androscan/web/chat.py`, the `frontend/README.md` reference) is deferred to **sub-step 10.6** where the tab routing changes anyway, keeping Phase 10's docs-vs-code commits cleanly separated. A future contributor reading "Hook Lab" in any Phase 6→9-era doc should treat it as referring to the present-day Lab tab; a top-of-file pointer in `docs/TASKS.md` flags the policy.
- rationale:
  - **Why not "just keep iterating on the call-graph view"?** A topological diagram is the wrong abstraction for a workflow whose inputs are observable behaviours and whose outputs are bypass scripts. The call-graph backend is gold; the cytoscape canvas is an inadequate UI for the question operators actually ask. Adding more affordances (filters, search, hop sliders) would not change the shape of the answer; replacing the headline surface with a question-shaped one (Trace) does.
  - **Why client-side trust manipulation, not source/sink?** Source/sink reachability is well-served by existing tools (FlowDroid, semgrep-android, MobSF) and it's a workflow most operators already have a path to. Client-side gate identification + bypass is something the typical mobile pentester does manually with `jadx` + ad-hoc Frida snippets — exactly the manual loop AndroScan can systematise *because* it already has every substrate (call graph, decompile cache, Frida adapter, Mirror, RAG, chat). The differentiator isn't "we have a static analyser too"; it's "we close the static→dynamic loop in one tab."
  - **Why platform-neutral data model now, when only Android ships?** The cost of getting the shapes right at v1 is one design-review session; the cost of refactoring after iOS / desktop adapters land is much higher. Even if those adapters never ship, the discipline keeps the data model honest about what it knows (predicate origin, branch outcome, risk) vs. what it assumes (which platform's bytecode it's looking at — answer: it shouldn't care).
  - **Why static-enumerate / LLM-interpret instead of LLM-only?** Static enumeration is auditable and reproducible — an operator can re-run the same trace and get the same gate list. LLM interpretation adds the "is this gate actually a security check or just business logic" judgement that pure heuristics can't reliably make. Splitting them keeps the auditable layer auditable and confines the LLM to the layer where its judgement adds genuine value, mirroring the same split DEC-018 / DEC-019 made for RAG retrieval (deterministic top-k, LLM reasoning) and DEC-022 / DEC-023's `resolve_ui_element` made for click-to-code (deterministic fuser + optional RAG, no LLM round-trip in the hot path).
  - **Why template-bound bypasses, not free-form?** Same reasoning as DEC-023's hook-source policy. Free-form LLM JS for bypass plans is a valuable v2 capability *if* it composes with the existing per-app `hook_target_package_prefix` allowlist, the `pyjsparser` pre-validation, and the consent flow — composing those for a planner-generated script is its own design conversation, deferred until v1 telemetry says it's worth having.
  - **Why demote the Cytoscape view rather than delete it?** It still has a legitimate use ("show me the topology of this subsystem") for power users debugging the analysis itself, or for operators who genuinely want to see how a complex closure fans out. Keeping it costs zero engineering, and removing it would erase prior investment for no functional gain. The only thing changing is the *default* surface operators see when they open Lab.
  - **Why the rename now?** The tab name "Hook Lab" was honest when its sole job was hook injection; with Trace as the new headline mode, it becomes misleading. Renaming once, at the start of Phase 10, is cheaper than renaming twice (now + after Trace ships). And keeping the "Hook Lab v1" name as a frozen proper noun for the completed milestone preserves historical accuracy without freezing the *tab* name to a v1 scope it has now outgrown.
- alternatives considered:
  - **Build a separate "Trace" top-level tab** (rejected): would mean four primary tabs (Inspect, Reports, Lab, Trace), and Trace would only ever be useful in the same workflow as Manual Hooks (stage a bypass → inject it). Splitting them across tabs would force operators to context-switch mid-loop. The 3-mode left-rail switcher inside Lab keeps the workflow continuous.
  - **Replace the cytoscape pane outright** (rejected): operator pull for the topological view is real, just not as the default. Cost of keeping it is zero; cost of removing it is signalling "we don't care about your existing workflow."
  - **Defer the rename to v2** (rejected): cheap to do now, expensive to do mid-development of Trace (every PR would have to navigate "is this the new Lab or the old Hook Lab?"). Doing it concurrently with DEC-024 keeps the Hook Lab name as an accurate historical proper noun for the v1 milestone, and the Lab name as the forward identifier.
  - **Per-decision LLM calls** (rejected): per-decision multiplies the LLM round-trip count by ~5–10× per trace and burns the per-turn skill-output budget without measurable interpretation-quality wins. Per-anchor is the right granularity.
  - **Free-form LLM JS for bypass plans in v1** (rejected as v1 scope): same reasoning as DEC-023 for the original hook-source policy; revisit in v2 with operator telemetry.
  - **Interprocedural slicing for predicate origin in v1** (rejected as v1 scope): the engineering complexity (alias analysis, field-sensitivity, escaping closures) doesn't fit a 2-week milestone, and the intra-procedural form already resolves the ~80% case (Android idioms put the predicate-computing call right before the `if`). Documented as v2 follow-up.
  - **Smali patching as a bypass technique** (rejected as v1 scope): patching → repackaging → re-signing → re-installing is a fundamentally different workflow class than "Frida hook a live process." Worth supporting eventually for cases where Frida is detected by anti-tamper, but it deserves its own design conversation and toolchain (apktool re-encode, `jarsigner`, install + verify). Capture as a v2 follow-up if operator demand surfaces.
  - **Auto-verify bypass after injection** (rejected as v1 scope): would require Mirror to programmatically re-trigger the anchor (re-tap the button) and diff the resulting UI state — interesting but its own design conversation. v1 keeps verification operator-driven (re-tap manually, observe the cyan Frida hits flow in).
- tradeoffs / consequences:
  - **The smali parser gains a "decisions" pass.** Adds maintenance surface; mitigated by fixture-driven tests and the existing `smali_parser.py`'s lexical-pass design (decisions extraction is one more pass over the same instruction stream — no re-parsing).
  - **The LLM's per-anchor budget bumps** (from chat-RAG's ~6 KB to ~10–12 KB to fit the populated `BehaviorAnchor`). DEC-022's `MAX_SKILLS_PER_TURN = 3` and per-skill timeout = 5 s still apply — the `trace_behavior` skill is one skill call, just with a richer payload. Tested end-to-end with mocked LLM in 10.5.
  - **Adds a fourth per-app SQLite store** (`trace.sqlite` next to `call_graph.sqlite`, `rag.sqlite`, decompile cache). Operationally identical pattern; no new ops complexity, no new invalidation rules.
  - **The Cytoscape pane stops being the default surface for new Lab visitors.** Operators familiar with Hook Lab v1 will see Trace mode by default after upgrade; the one-time tooltip + the visible mode switcher mitigate the surprise. Per-app setting to pin `Graph` as the default is a P2 follow-up if operator complaints surface.
  - **The rename creates one transitional doc state** where some text reads "Lab tab" (forward-looking, post-DEC-024) and some reads "Hook Lab" (historical, pre-DEC-024). Pinned in this DEC + the top-of-file note in `docs/TASKS.md`; if it becomes confusing in practice, a follow-up sweep in v2 can decide whether to backfill historical text.
  - **Phase 10 is the first cross-cutting feature that materially reuses every Hook Lab v1 substrate.** The Cytoscape pane reuse is mostly visual (Frida overlay still drives it); the call-graph SQLite store, the `frida_hooks/` templates, the `generate_frida_hook` skill, the chat consent flow (DEC-022 plumbing), and the per-app `hook_target_package_prefix` allowlist all flow through to Phase 10 unchanged. If any of those start growing Trace-specific branches, that's a smell — surface it in code review and consider whether the abstraction needs to land at the Lab level instead.
- follow-up:
  - Implement sub-steps 10.1 → 10.8 strictly linearly per `docs/TASKS.md` § Phase 10 — Behavior Trace v1 — sub-step backlog. One sub-step per Agent-mode session. Brief Ask-mode planning checkpoint at the top of 10.6 to confirm the `BehaviorAnchor` JSON wire shape before the frontend in 10.7 starts depending on it (mirrors the 4.1 SQLite-schema checkpoint that locked the call-graph store before the routes API).
  - Code rename (`HookLabTab.tsx` → `LabTab.tsx`, `HookLabCodeView` → `LabCodeView`, `#/hook` URL hash → `#/lab`, `frontend/README.md` reference, the per-tab chat-prompt key in `androscan/web/chat.py`) lands in **10.6** alongside the tab routing changes. Until then, code-level identifiers in this DEC and the Phase 10 sub-step entries reference the existing `HookLabTab.tsx` filename for accuracy.
  - Cytoscape demotion's per-app "default mode" setting — design lives in 10.6; if it doesn't ship cleanly there, capture as a P2 follow-up rather than blocking 10.6.
  - Revisit free-form LLM JS for bypass plans (v2) once Phase 10 v1 telemetry shows operator demand.
  - Revisit interprocedural slicing (v2) once intra-procedural's false-negative rate is measurable on real apps.
  - Revisit auto-verify (re-trigger anchor + UI diff) once Trace is in operator hands and the verification step's friction is known.
  - iOS / desktop / embedded adapters live behind the platform-neutral data model — when (if) operator demand justifies one, the contract is already locked; the work is one adapter at a time.
  - Cross-link this DEC from `docs/STATE.md` when sub-step 10.1 lands (the first code commit) so the "what currently exists" view reflects Phase 10 reality.
- related docs:
  - `docs/DECISIONS.md` **DEC-016** (Smali-first call graph — Phase 10 reuses the SQLite store unchanged for forward-closure walks)
  - `docs/DECISIONS.md` **DEC-018** (RAG SQLite store layout — `trace.sqlite` mirrors it)
  - `docs/DECISIONS.md` **DEC-022** (Workbench chat agentic loop — Phase 10's `trace_behavior` skill is a consumer of the same skill-execution pattern; no new agentic-loop changes needed beyond what 4.7 / DEC-022 already plumbs)
  - `docs/DECISIONS.md` **DEC-023** (Hook Lab v1 — Phase 10 reuses every substrate; the Hook Lab tab is renamed "Lab" forward as part of this DEC)
  - `docs/TASKS.md` § Phase 10 — Behavior Trace v1 — sub-step backlog (the implementation plan)
  - `docs/STATE.md` (Phase 10 stub to be added when 10.1 lands)
  - `docs/DESIGN_DOC.md` Phase 10 (added in this same commit)
  - `docs/SAFETY_AND_SECURITY.md` (Trace mode adds zero new device-touching surface; existing §12.6 controls cover the bypass flow unchanged)

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