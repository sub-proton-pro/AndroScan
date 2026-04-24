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
  Build the **primary** static call graph from **Smali** files produced by **apktool** decode. Use **jadx** for mapping UI taps and displaying source (Phases 7–8). Store graph artifacts under `apps/<app_id>/` (e.g. `call_graph.json`) with rebuild-on-APK-hash-change.
- rationale:
  Smali reflects actual `invoke-*` targets; aligns with existing apktool pipeline; keeps graph generation testable with small fixture Smali files.
- alternatives considered:
  - jadx IR / Java AST only (rejected as primary: less faithful to bytecode)
  - External binary analysis tool as mandatory dependency (deferred: keep in-house parser first, adapter later if needed)
- tradeoffs / consequences:
  - Custom parser maintenance; must handle large apps via pagination/filtering in APIs and UI.
- follow-up:
  Implement `androscan/analysis/` (or equivalent) per `docs/TASKS.md` Phase 8; add `query_call_graph` **llm** skill when subgraph contract is stable.
- related docs:
  - `docs/DESIGN_DOC.md` (Phase 8)
  - `docs/ARCHITECTURE.md` §4.9
  - `docs/CONVENTIONS.md` (graph artifact conventions when implemented)

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
  - Add a "Hook Lab readiness" rollup probe (frida-server present on device, frida CLI on host, target package gadget injectable) once Hook Lab work begins.
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
- related docs:
  - `docs/DECISIONS.md` DEC-013 (Skills as first-class layer with three-tier model)
  - `docs/DECISIONS.md` DEC-017 (Frida user-confirmation requirement — this DEC is the mechanism that fulfils it)
  - `docs/DECISIONS.md` DEC-018 (Lane-1 RAG — the primary read-only retrieval skill)
  - `docs/DESIGN_DOC.md` (Phases 6–9; chat loop section to be added)
  - `docs/SAFETY_AND_SECURITY.md` (consent semantics + per-skill budgets — section to be added)
  - `docs/TASKS.md` (backlog entry to track implementation; "RE Workbench chat — agentic loop" P2 item under § Interactive RE Workbench)
  - `androscan/web/chat.py` (current one-shot path — to be extended)
  - `androscan/internal/workflow.py` (existing `while turn < max_turns` loop pattern — the model to mirror)

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