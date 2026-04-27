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
  - Implement sub-steps 4.1 → 4.8 strictly linearly per `docs/TASKS.md` § Hook Lab v1 — sub-step backlog. One sub-step per Agent-mode session. Brief Ask-mode planning checkpoint at the top of 4.1 to settle the SQLite schema before code lands.
  - **[done 2026-04-27, in sub-step 4.3]** Add a "Hook Lab readiness" rollup probe to Settings → Status (frida CLI on host + frida-server reachable + version skew + target app installable). Two-card design landed: existing `tools.frida` (host CLI) + new `tools.frida_server` (device reachability + host/server version-skew, severity `None` / `"minor"` / `"major"`). Treated as **yellow** in `rollupGlobal` — non-critical for static-only workflows. "Target app installable" is deferred to 4.5 along with the Inject UI it would gate. Closes DEC-021's "Hook Lab readiness rollup probe" follow-up.
  - Once the Hook Lab agentic loop is wired (sub-step 4.7), revisit DEC-022's consent-class hook to confirm Hook Lab's `requires_confirmation=True` skills (frida hook injection) interact correctly with the chat consent UI.
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