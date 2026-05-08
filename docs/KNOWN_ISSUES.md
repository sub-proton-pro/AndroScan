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

### ISSUE-004: LLM usage patterns may still evolve beyond the current analysis + verification flows
- status: Open
- impact: Medium
- area: LLM layer / security / contracts
- introduced / observed: [replace date]
- summary:
  Multi-turn dossier analysis and Phase 5 verification use the LLM layer with structured prompts and parsing, but new features may introduce additional call sites, contracts, or failure modes that are not yet fully standardized.
- why it matters:
  Without explicit patterns for each new use case, contributors may improvise inconsistent handling of prompts, validation, and errors.
- current workaround:
  Keep LLM access centralized in the LLM layer; extend contracts and validation deliberately when adding new model-driven behavior.
- recommended fix:
  Document and test each major LLM workflow (analysis loop, verification, any future flows) with clear contracts, validation rules, and provenance behavior.
- related tasks:
  - LLM contract docs for new workflows as they are added
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
  - `AGENT_PROTOCOL.md`
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

### ISSUE-009: Workbench chat cannot dig deeper than the initial RAG sweep
- status: Resolved (Phase 11 v2.1.5 — landed 2026-05-05 as a side-effect of the chat-widget pattern needing an agentic-loop substrate to live on)
- impact: Medium
- area: web / chat / RAG
- introduced / observed: 2026-04 (Inspect-tab testing on a fixture banking APK)
- summary:
  `androscan/web/chat.py` was single-pass through Phase 11 v2: it ran at most one `_enrich_inspect_with_rag` sweep (top-4 chunks, fail-soft) up-front, then called the LLM once and returned prose. The LLM could not request additional skills mid-turn the way `androscan/internal/workflow.py` did. When the question hinged on a specific method-level chunk that didn't make `_INSPECT_RAG_TOP_K = 4`, the model honestly hedged with "decompile and look it up yourself" instead of being able to call `search_decompiled_sources` (or `get_decompiled_method`) for the missing piece — even though those skills were registered and the analysis pipeline already used them.
- why it mattered:
  Produced low-trust answers for questions that the registered skill set could answer in milliseconds, undercutting the workbench's "ask the LLM about this APK" value proposition. Operators learned not to trust Inspect chat for deep questions, which weakened adoption.
- resolution (2026-05-05, v2.1.5 — architectural deviation from the v2.1.0 spec):
  The v2.1.0 spec for v2.1.5 was scoped to "Tier 3 skill + skill-response `widgets[]` schema extension + `<TraceEntryCandidateWidget>` chat renderer + auto-fire handoff" — and assumed the chat agentic loop already existed. It did not (the loop existed only in the CLI's `androscan/internal/workflow.py`, not in the chat-streaming code path). The chat-widget pattern needed an agentic loop substrate to live on (`widget` SSE events forwarded between turns, consent-class skills emitting `skill_pending` and halting, skill-result text appended to message history for subsequent turns) — so v2.1.5 had to ship the bounded agentic loop refactor first as the substrate the widget pattern is built on, then ship the widget pattern + the new `suggest_trace_entry` skill on top. **The agentic loop refactor was DEC-022's explicit recommended fix for this issue** — v2.1.5 just shipped it earlier than expected and as part of a different release than originally framed. **As-shipped:** new `_stream_chat_agentic_request` async generator in `androscan/web/chat.py` orchestrates multiple blocking `llm.client.complete()` calls within a bounded loop (`MAX_AGENTIC_TURNS = 5`, `MAX_SKILLS_PER_TURN = 3`), parses LLM JSON responses (`thinking`, `content`, `skill_requests`), executes non-confirmation skills server-side (consent-class `requires_confirmation=True` skills emit `skill_pending` and halt with explanatory `content` instead of auto-executing — safer than auto-execution because consent-class skills like `generate_frida_hook` / `extract_apk` / device-touching skills now stay explicitly gated behind operator UI consent which simply isn't wired yet), appends skill results to message history for subsequent LLM turns, and emits four new SSE events (`skill_request` / `skill_result` / `skill_pending` / `widget`). New `agentic_loop: bool` request body field (optional, defaults to `False` for backward compatibility); `stream_chat_request` dispatches to `_stream_chat_agentic_request` when `True`, else falls back to the legacy single-pass streaming path. Frontend opt-in via `AGENTIC_LOOP_TABS = new Set(["lab"])` in `ChatDock.tsx` — Lab tab opts into agentic loop; Reports / Inspect / Settings stay on the legacy single-pass path for now (rolling agentic-loop opt-in out to other tabs is a v3 / Phase 12 candidate — see DEC-022 cross-link below). Verified end-to-end in `tests/test_chat_stream.py` (+8 new tests covering: agentic loop dispatch on `agentic_loop: true`; backward-compat single-pass on `agentic_loop: false` / absent; `skill_request` + `skill_result` events forwarded; `widget` event forwarded after `skill_result`; bounded `MAX_AGENTIC_TURNS` halts gracefully; consent-class skills emit `skill_pending` and halt without executing). The `_INSPECT_RAG_TOP_K = 4` band-aid that the original "recommended fix" mentioned was not necessary — the agentic loop's ability to call `search_decompiled_sources` / `get_decompiled_method` / `query_call_graph` mid-turn covers the original failure mode.
- related tasks:
  - **Closed:** "RE Workbench chat — agentic skill loop (P2, planned per DEC-022)" in `docs/TASKS.md` (under § Interactive RE Workbench) — ratified as part of the v2.1.5 sub-step.
  - **Forward link:** rolling agentic-loop opt-in out to Inspect / Reports / Settings tabs is a v3 / Phase 12 candidate (operator-demand-gated; the v2.1.5 pattern needs to prove itself in operator hands on the Lab tab first).
- related docs:
  - `docs/DECISIONS.md` DEC-022 (parent — chat agentic loop)
  - `docs/DECISIONS.md` DEC-025 (v2.1 closing-note extension documents the architectural deviation in detail)
  - `docs/STATE.md` "Recent completed work" v2.1.5 entry
  - `docs/TASKS.md` § Phase 11 v2.1 follow-up release — sub-step backlog v2.1.5 row
  - `androscan/web/chat.py` (the bounded agentic loop substrate)
  - `androscan/skills/suggest_trace_entry.py` (the first widget-emitting skill — first concrete operator-visible benefit of the closed loop)
  - `androscan/internal/workflow.py` (the original CLI agentic loop — the chat loop is structurally similar but with SSE event emission for streaming UX)

---

### ISSUE-010: Monaco editor (Hook Lab) is loaded from a CDN
- status: Open
- impact: Medium
- area: web / Hook Lab / air-gap operability
- introduced / observed: 2026-04-27 (sub-step 4.5 — Hook Lab Stage→Inject UI)
- summary:
  `@monaco-editor/react@^4.6.0` (used in `HookBuilder.tsx`'s read-only JS view) lazy-fetches the Monaco editor assets from the default jsdelivr CDN (`cdn.jsdelivr.net/npm/monaco-editor@…/min/vs/`). Without an internet route to that host, the Monaco view never mounts: the inline `pyjsparser` markers don't render, and the operator sees an empty editor pane (the rest of the workbench keeps working).
- why it matters:
  Pentester laptops are routinely run air-gapped against an emulator on the same machine; one of the workbench's selling points is that it can be operated without an outbound network. CDN-loaded Monaco breaks that posture for Hook Lab specifically — the rest of the UI is fine — and the failure mode (empty editor, no network error) is silent enough to mistake for an editor bug.
- current workaround:
  Allow the laptop's outbound HTTPS to `cdn.jsdelivr.net` while using Hook Lab. The Inject button itself doesn't depend on Monaco — it consumes the `parse` result returned by `POST /api/frida/render` — so an operator who allows the CDN once gets full functionality.
- recommended fix:
  Self-host Monaco. One-line `loader.config({paths: {vs: '/monaco/min/vs'}})` in `HookBuilder.tsx`'s module init plus a postbuild copy of `node_modules/monaco-editor/min/vs/` into `androscan/web/static/monaco/` (and a Vite static-asset rule so it ships with the bundle). Trade-off: the production bundle grows by ~3 MB on disk but the runtime gzipped payload is unchanged because the CDN load is replaced 1:1 by a same-origin load. Land alongside a Hook Lab v2 sweep (sub-step 4.7+).
- related tasks:
  - `docs/TASKS.md` § Hook Lab v1 — sub-step backlog (4.5 deferred this; 4.7 / 4.8 sweep can pick it up)
- related docs:
  - `docs/DECISIONS.md` DEC-023 (sub-step 4.5 specifics — "Frontend: Monaco editor, but with a CDN footnote.")
  - `androscan/web/frontend/src/components/HookBuilder.tsx`

---

### ISSUE-011: FastAPI `@app.on_event("shutdown")` is deprecated
- status: Open
- impact: Low
- area: web / FastAPI compatibility
- introduced / observed: 2026-04-27 (surfaced in `tests/test_frida_routes.py` under FastAPI 0.110+; the call site itself landed in Hook Lab 4.3 with `detach_all()` on uvicorn shutdown — `androscan/web/app.py:854`)
- summary:
  `androscan/web/app.py` registers a `@app.on_event("shutdown")` handler that calls `FridaClient.detach_all()` so live Frida sessions are cleanly torn down on uvicorn stop. FastAPI emits a `DeprecationWarning` per registered handler (currently 24 warnings × 2 handlers across `tests/test_frida_routes.py`'s `TestClient` instantiations) — the recommended replacement is a `lifespan=…` async context manager passed to `FastAPI(...)`. Functionally fine today; the warning is a pure DeprecationWarning, not a behaviour change.
- why it matters:
  Pure tech-debt: the warnings clutter test output and a future FastAPI major bump may eventually drop `on_event` entirely. There is no functional impact on session cleanup right now.
- current workaround:
  None needed — the shutdown hook still fires and `detach_all()` runs as designed. Test output is noisy but not failing.
- recommended fix:
  Single-shot conversion of the existing `@app.on_event("startup")` / `@app.on_event("shutdown")` handlers in `androscan/web/app.py` to a `lifespan=…` async context manager passed to `FastAPI(...)`. Net change is small (~20 lines), but it is its own atomic refactor — better tracked as a standalone task than folded into a Hook Lab sub-step (Hook Lab 4.6+ keeps a clean, focused diff). Land alongside Hook Lab 4.8's docs sweep or any future "FastAPI compatibility" sweep, whichever comes first.
- related tasks:
  - `docs/TASKS.md` (no dedicated row yet; capture as a P3 cleanup when it surfaces again)
- related docs:
  - `androscan/web/app.py` (`@app.on_event("shutdown")` at line ~854; mirror logcat WS pattern when refactoring)
  - https://fastapi.tiangolo.com/advanced/events/ (lifespan context manager pattern)

---

### ISSUE-012: Frida overlay aggregates hits across method overloads
- status: Open (intentional v1 trade-off — captured for v2 follow-up)
- impact: Low
- area: web / Hook Lab / call-graph overlay
- introduced / observed: 2026-04-27 (sub-step 4.8 — Frida overlay on call graph)
- summary:
  The call graph keys nodes by `(class_name, method_name, descriptor)` (the full Smali signature is preserved on each node so `Foo.bar(String)` and `Foo.bar(int, String)` are distinct nodes). The Frida overlay's `hitsByMethod` prop, however, keys by `${class}::${method}` only — Frida's hook templates (`entry_exit_log`, `scope_inspector`, etc.) iterate `Java.use(class).method.overloads.forEach` and emit events with `{class, method}` only; the overload arity / descriptor isn't on the wire. Net effect: when *any* overload of `Foo.bar` fires, **every** node named `Foo.bar` in the graph (regardless of arity) lights up cyan with the same hit count. An operator looking at a hit on `Foo.bar(String)` cannot tell from the overlay alone whether it was the 1-arg or the 2-arg overload that actually fired.
- why it matters:
  For most v1 hook templates the operator's mental model is "I hooked `Foo.bar`" (the JS template's `Java.use(class)[method].overloads.forEach` makes that promise true at the runtime level), so over-attribution at the graph level matches the hook's own granularity. The caveat surfaces only on apps that have multiple overloads of the same method name — common enough in real Android apps (e.g. `String.format`, builder patterns) that an operator deep in the graph could mis-read attribution.
- current workaround:
  Cross-reference the overlay against the **Hooks panel** (sub-step 4.6) which renders one row per `(class, method)` plus per-overload context if the hook's payload includes args. The Trace WS / JSONL persistence carries the full args dict per event, so `apps/<app_id>/<run_ts>/frida/<session>.jsonl` is the durable record when per-overload attribution matters.
- recommended fix:
  Two-part change for v2: (1) extend `frida_hooks/entry_exit_log.py` (and `scope_inspector.py`) JS templates to emit `descriptor` (or `arity` + arg-type-list) in the payload — needs a wire-format bump in `_summarize_hooks`'s `(class, method)` group-by to `(class, method, descriptor)`; (2) update `CallGraphView.tsx`'s `hitKey` helper to take the descriptor as an optional third arg, and `HookLabTab.tsx`'s `useMemo` builder to populate it when present. Backwards-compatible default: if a hook event omits `descriptor`, fall back to the v1 `${class}::${method}` keying so old JSONL files replay correctly. Defer to Hook Lab v2 alongside per-overload hook-builder UI.
- related tasks:
  - `docs/TASKS.md` § Hook Lab v1 — sub-step backlog (v1 complete; capture as v2 follow-up when v2 backlog opens)
- related docs:
  - `docs/DECISIONS.md` DEC-023 (sub-step 4.8 specifics — "Method-overload precision caveat (intentional, documented in KNOWN_ISSUES ISSUE-012)")
  - `androscan/web/frontend/src/components/CallGraphView.tsx` (`hitKey` helper + overlay element builders)
  - `androscan/web/frontend/src/tabs/HookLabTab.tsx` (`hitsByMethod` `useMemo` derivation from `chatHooks`)
  - `androscan/adapters/frida_hooks/entry_exit_log.py` / `scope_inspector.py` (hook templates emitting `{class, method}` only)

---

### ISSUE-013: Behavior Trace v1 backward slicing is intra-procedural only
- status: **Resolved (Phase 11 v2)** — landed 2026-04-30 across sub-steps 11.4 (bounded inter-procedural method descent) + 11.5 (same-class field-write-site walking) + 11.6 (cache schema bump + LLM-budget bumps + frontend depth-pill UI + ISSUE-013 close-out). Production verification on dogfood-app traces pending (the spec's >50% false-negative reduction criterion needs real-app data); the corpus-wide v1-vs-v2 regression floor is locked in via `tests/test_decisions_slicing.py::test_v1_vs_v2_corpus_measurement_v2_resolves_strictly_more_terminals`. Re-open if real-app measurement shows the false-negative rate is still > 50% of the v1 baseline despite v2 descent.
- impact: Medium
- area: analysis / Behavior Trace / decision-point predicate origin
- introduced / observed: 2026-04-29 (Phase 10 v1 complete — sub-step 10.2 specifics)
- summary:
  `androscan/analysis/slicing.py` walks predicate origin **inside a single Smali method body only** — no aliasing, no field-flow analysis, no cross-method dataflow. When a predicate register's defining instruction is a method invocation (`invoke-virtual`, `invoke-static`, etc.), the slicer records the call as a `MethodCallOrigin` and stops; it does **not** descend into the callee to determine what the callee actually computes. Similarly, when a predicate register's defining instruction is an `iget` / `sget` (field load), the slicer records the field as a `FieldReadOrigin` and stops; it does not chase backwards through `iput` / `sput` write sites to find what value was last stored. The honest discriminated-union surface (`PredicateOrigin`'s `MethodCallOrigin`, `FieldReadOrigin`, `ParamOrigin`, `ConstOrigin`, `CompositeOrigin`) preserves the limitation in the data model — and `DecisionPoint.predicate_origin: PredicateOrigin | None` carries `None` when the slicer can't terminate at any of the five variants (max-walk exhaustion, unsupported defining opcode, etc.). The Trace UI surfaces a "trace may be incomplete" banner via `TraceIncompleteBanner.tsx` whenever any decision point in the closure has `predicate_origin: None` *or* sits in a method tagged `may_have_unresolved_reflection: true` from DEC-023's call-graph store.
- why it matters:
  Plenty of real Android gating idioms keep the predicate-computing call right next to the `if` that consumes its result (e.g. `if (rootDetector.isRooted()) { ... }`), and v1 handles those cleanly. Other idioms — multi-step builder predicates, helper-method extractions, field-cached results from a one-time check — produce a `MethodCallOrigin` or `FieldReadOrigin` that the operator must follow manually in the decompiled source to decide whether to write a hook against the immediate callee or against something deeper in the chain. Per DEC-024's "intra-procedural is honest about its limits" framing, the surface tells the truth (no false `Const`/`Param` claims), but the false-negative rate on what the planner can mechanically suggest a bypass for is the single largest known gap in v1's planner coverage.
- current workaround:
  Operators read the decompiled source via the Lab → Manual Hooks "Open in Inspect" handoff (which jumps from a decision point's `MethodRef` to the corresponding source line) and decide manually whether to hook the immediate callee, the field-write site, or some deeper helper. The Frida override templates `force_return_value` and `force_method_skip` work fine on any of those choices once the operator picks one — the limitation is in *suggestion*, not *execution*.
- recommended fix:
  **Locked into Phase 11 v2 per DEC-025; rolling out across sub-steps 11.4 → 11.6.** Two-part v2 change: (1) **11.4** — extend `slicing.py` with a bounded inter-procedural method-descent walker (`_descend_into_callee`) up to `MAX_SLICE_DEPTH = 2` (`trace.max_slice_depth` config knob, hard cap 4 in code), gated by a new type-driven `is_stateless(method, classes_by_smali, visited) -> bool` analyzer that walks the callee body looking for side effects (`iput-*` / `sput-*` / `aput-*` / `invoke-*` to non-stateless callees / `monitor-*` / throws / reflection-flagged methods) — recursive with cycle detection via a visited set keyed on `(class_smali, method_name, descriptor)`; small hand-curated `_STATELESS_LIB_DENYLIST` constant for stdlib classes we can't walk into (e.g. `Ljava/lang/Math;`, primitive boxing classes, `Ljava/lang/String;` getters, `Lkotlin/jvm/internal/Intrinsics;`). Type-driven was picked over hand-curated regex because the regex would silently miss app-private stateless helpers. (2) **11.5** — `_walk_field_write_sites` walks the same class's `iput-*` / `sput-*` write sites for `FieldReadOrigin` terminals (cross-class field-flow stays out of scope per the same depth rationale); both passes share a `_DescentBudget` so the closed-economy guarantee holds (a method that descends 2 hops via callees can't *also* walk a field-write site). (3) **11.6** — bypass-planner re-run against the deeper terminals (planner shape unchanged — emits `force_method_skip` / `force_return_value` against the new terminal); LLM-budget bump in `global_config.yaml` to absorb the ~2× input prompt growth (`num_ctx: 16384`) + ~1.5× output growth (`num_predict: 12288`); `trace.sqlite` `SCHEMA_VERSION` bump from `"1"` to `"2"` so v1 cached anchors silently re-build on first 11.x open via the existing reader's "drop-the-cache" path; close-out of this issue (status flips to *"Resolved (Phase 11 v2)"* if 11.4 + 11.5 measurably reduce the false-negative rate on the dogfood app — criterion: >50% of v1's "decision with `predicate_origin: None`" cases on existing cached anchors now resolve to a deeper terminal under v2 slicer; else status remains *"Open (partial v2 progress)"* with a note describing the remaining gap, most likely multi-step-builder predicates needing depth > 2). All three sub-steps feed back into the existing `PredicateOrigin` discriminated union — no schema bump on the variants themselves; v2 adds one optional `descent_depth: int = 0` field on `MethodCallOrigin` / `FieldReadOrigin` to drive the "via N helper method(s)" UI depth pill on `PredicateOriginView` per DEC-025 open question 4. Operator-facing: the "trace may be incomplete" banner becomes proportionately less common; the planner's `force_method_skip` / `force_return_value` plan list grows.
- related tasks:
  - `docs/TASKS.md` § Phase 11 — Behavior Trace v2 — sub-step backlog (sub-steps 11.4 + 11.5 + 11.6 close this issue per DEC-025; planning checkpoint 11.0 ratified 2026-04-30)
  - `docs/TASKS.md` § Phase 10 — Behavior Trace v1 — sub-step backlog (v1 complete 2026-04-29; this issue captured the deferred precision gap)
- related docs:
  - `docs/DECISIONS.md` DEC-024 (Phase 10 — "Decision extraction is intra-procedural" decision clause + closing note 2026-04-29 + cross-link bullet to DEC-025 added 2026-04-30)
  - `docs/DECISIONS.md` **DEC-025** (Phase 11 — Behavior Trace v2; the formal v2 follow-up DEC that picks up this issue plus three operator-feedback-driven UX deliverables)
  - `androscan/analysis/slicing.py` (the intra-procedural slicer — `MAX_SLICE_DEPTH` constant + `_DescentBudget` + `_STATELESS_LIB_DENYLIST` + `is_stateless` helper land here in 11.4 + 11.5)
  - `androscan/analysis/trace_types.py` (`PredicateOrigin` discriminated union — no schema bump on variants themselves; `descent_depth: int = 0` field on `MethodCallOrigin` / `FieldReadOrigin` lands in 11.6)
  - `androscan/internal/trace_cache.py` (`SCHEMA_VERSION` bumps from `"1"` to `"2"` in 11.6)
  - `androscan/web/frontend/src/components/trace/TraceBanners.tsx` (`TraceIncompleteBanner` surfaces `predicate_origin: None` closures today; copy updated in 11.6 to reflect v2's bounded inter-procedural improvements)
  - `androscan/web/frontend/src/components/trace/PredicateOriginView.tsx` (gains the "via N helper method(s)" / "via 1 field write" depth pill in 11.6 per DEC-025 open question 4)

---

### ISSUE-014: Behavior Trace v1 predicate_origin is per-overload imprecise on two-register comparisons
- status: Open (intentional v1 trade-off — v2 follow-up)
- impact: Low
- area: analysis / Behavior Trace / decision-point predicate origin / two-register comparisons
- introduced / observed: 2026-04-29 (Phase 10 v1 complete — sub-step 10.2 specifics)
- summary:
  Smali two-register comparison opcodes (`if-eq`, `if-ne`, `if-lt`, `if-le`, `if-gt`, `if-ge`) compare two registers `vA` and `vB` and branch on the relation. `trace_predicate.py` resolves each operand independently to its own `PredicateOrigin`, but `DecisionPoint.predicate_origin: PredicateOrigin | None` is a *single* origin field (not a 2-tuple). The current implementation reports the **left operand's origin** (`vA`) and surfaces the right operand only via the raw decompiled snippet attached to the decision point. For one-register comparisons (`if-eqz` / `if-nez` / etc.), the surface is precise — the lone register's origin is the only origin, and the data model fits. For two-register comparisons where neither operand traces back deterministically to a method-call origin (e.g. both are `iget` field loads, or one is a `const` and the other an `iget`), the operator looking at the structured `predicate_origin` alone may miss that a field-write hook on the *other* operand's source would be just as valid a bypass site as one on the reported operand's source.
- why it matters:
  Most production decision-points the v1 planner has seen on small-fixture and dogfood-app traces are one-register comparisons (`if-eqz vN` against the result of a method call or field load — the textbook "is this thing true?" idiom), so the precision gap mostly affects two-register predicates that compare two non-trivial values (e.g. `if-eq vRoot, vExpected` where both come from independent field loads). The `force_return_value` and `force_method_skip` planner outputs remain correct for the reported operand; they just miss the symmetric bypass site on the unreported operand.
- current workaround:
  Operator reads the decompiled snippet on the decision-point card (which shows both operands) and authors a manual hook against the unreported operand's source if the reported one isn't a convenient hook site. The Trace mode UI's `BypassPlanCard.tsx` "Stage in Manual Hooks" handoff already plumbs the operator into the HookBuilder with a partial prefill, so completing the picked operand's hook is a well-trodden v1 path.
- recommended fix:
  Schema bump on `DecisionPoint`: change `predicate_origin: PredicateOrigin | None` to `predicate_origins: tuple[PredicateOrigin, ...]` (length 1 for `if-eqz`-class opcodes, length 2 for two-register opcodes). The discriminated-union members don't change; the wire shape on `DecisionPoint` does. Frontend: `PredicateOriginView.tsx` becomes a list renderer (which it almost is already — it currently renders one origin in a card; `BehaviorAnchorCard.tsx` / `DecisionTimeline.tsx` would render N cards instead). Planner: `bypass_planner.py` enumerates plans against each operand independently and the existing risk-tier filtering applies per-operand. Storage: `trace.sqlite` schema_version bumps from 1 to 2 (the payload_json shape changes); per DEC-024's "drop-the-cache invalidation" model, this is a one-line migration. Risk: doubles the per-anchor card count for two-register-heavy methods, which a UI density review should weigh in on before shipping.
- related tasks:
  - `docs/TASKS.md` § Phase 10 — Behavior Trace v1 — sub-step backlog (v1 complete 2026-04-29; capture as v2 follow-up when v2 backlog opens)
- related docs:
  - `docs/DECISIONS.md` DEC-024 (Phase 10 — closing note 2026-04-29 references this issue by ID alongside ISSUE-013)
  - `androscan/analysis/trace_types.py` (`DecisionPoint.predicate_origin` field — schema bump lives here)
  - `androscan/analysis/trace_predicate.py` (resolves each operand; today drops the right operand for two-register opcodes)
  - `androscan/web/frontend/src/components/trace/PredicateOriginView.tsx` (renders the single origin today — list-renderer in v2)

---

### ISSUE-015: Settings tab "Save" silently strips comments + blank lines + quotes from `global_config.yaml`
- status: Open
- impact: Medium
- area: web / Settings / config persistence
- introduced / observed: 2026-04-30 (surfaced when `global_config.yaml` appeared as a 50-line destructive working-tree change after a Settings UI interaction during a Hook Lab UX session; the underlying behaviour predates the observation — `dump_to_yaml` has used `yaml.safe_dump` since the Settings UI's form-save path was first wired in Phase 6 / sub-step DEC-019).
- summary:
  Two write paths in `androscan/config/loader.py` round-trip the YAML through PyYAML's `yaml.safe_dump(..., sort_keys=False, default_flow_style=False)`:
  (1) `dump_to_yaml(config_path, partial)` at line 602 — invoked by `POST /api/settings/global` (the Settings tab's per-section "Save" button);
  (2) `restore_defaults_yaml(config_path)` at line 709 — invoked by the Settings tab's "Reset to defaults" button.
  PyYAML's parse layer (`yaml.safe_load`) does not retain comment positions, blank-line positions, original quoting style, or original Unicode encoding; the round-trip dump therefore (a) **strips every comment** including the ten-or-so multi-line documentation blocks that explain field semantics (e.g. the `bypass_risk_max` block, the `rag` provider block, the `frida.trace_ring_buffer_size` clamp note); (b) **removes blank lines** that visually separate sections; (c) **drops "unnecessary" quotes** around strings PyYAML deems unambiguous (`"http://localhost:11434"` → `http://localhost:11434`, `"qwen3.5:35b"` → `qwen3.5:35b`); (d) **escapes non-ASCII characters to `\uXXXX`** because `allow_unicode` defaults to `False` (so the `─` character used for `output.section_rule_char` becomes the unreadable `"\u2500"` escape). The sibling raw-text path `write_raw_yaml` (line ~688) is unaffected — it does `f.write(raw_text)` and preserves the operator's text byte-for-byte. The `dump_to_yaml` docstring says "user comments aren't *removed*" but that promise only applies to **unknown keys** (those are merge-preserved into the output dict before the dump); it does NOT apply to comments, which are unrecoverable after the round-trip. The docstring is misleading on this point.
- why it matters:
  The repo's `global_config.yaml` is the canonical, in-tree, self-documenting source of truth for every workbench config knob — comments explain not just what each field does but the trade-offs (e.g. the `max_hops_default` doc block explains the LLM-token / per-method-static-analysis cost trade-off). A single Settings-form save destroys that documentation in the operator's local checkout; if they then commit the change (it shows up as a 50-line diff that's easy to mistake for a deliberate edit when reviewing `git status`), the loss propagates into the repo. Even in single-operator use, the next time the operator opens the file to tune a knob they've lost the inline guidance that told them what the knob does. Trade-off explanation losses compound: future contributors reading the file see only `medium` instead of `# bypass_risk_max: maximum risk level the planner will emit ...`. Failure mode is silent — the form save returns `200 OK`, the values still apply, no warning surfaces in the UI.
- current workaround:
  Two options:
  (1) Use the Settings tab's **Raw YAML** sub-section ("Edit `global_config.yaml` directly. Validation runs on save —" — `SettingsTab.tsx:273`) instead of the form sub-sections. The Raw YAML save path goes through `write_raw_yaml` which preserves the operator's exact text. Validation still runs (the same `Config.from_dict` round-trip) so malformed YAML is rejected before the file is touched.
  (2) `git checkout -- global_config.yaml` immediately if the destructive reformat shows up unintentionally — the documented version in HEAD is the source of truth and the form-save's value changes can be re-applied through the Raw YAML path or by hand-editing.
- recommended fix:
  Switch `dump_to_yaml` and `restore_defaults_yaml` from PyYAML's `yaml.safe_dump` to **ruamel.yaml** in round-trip mode. ruamel.yaml is a YAML 1.2 implementation specifically designed to preserve comments, blank lines, original quoting style, and Unicode characters across a parse → mutate → dump round-trip. Drop-in replacement of ~10 LOC per function: `from ruamel.yaml import YAML; yaml_rt = YAML(typ="rt"); yaml_rt.preserve_quotes = True; yaml_rt.allow_unicode = True; yaml_rt.indent(mapping=2, sequence=4, offset=2); yaml_rt.dump(merged_dict, file_handle)`. Add `ruamel.yaml >= 0.18` to `pyproject.toml`'s install_requires (well-maintained, ~250 KB pure-Python wheel, no compiled deps — fine for the workbench's air-gap-friendly posture). The merge step in `dump_to_yaml` should switch from `existing.update(partial)` (which assumes a plain dict) to `existing[k] = v` per-key on the `CommentedMap` ruamel returns, so per-key comments survive value mutations (ruamel attaches comments to the surrounding `CommentedMap` / `CommentedSeq` nodes, not to the dict literal). Validation (the existing `Config.from_dict` call) runs unchanged because `Config.from_dict` accepts any `Mapping` and `CommentedMap` is one. While in there, also fix the misleading `dump_to_yaml` docstring to spell out that "comments preserved" depends on the YAML library, not on the merge step. Land alongside any future Settings-UI sweep, or as a standalone P3 cleanup PR (~30 LOC + 1 dep + 2 unit tests asserting comments round-trip).
- related tasks:
  - `docs/TASKS.md` (no dedicated row yet; capture as a P3 cleanup when next touching Settings or config plumbing).
- related docs:
  - `androscan/config/loader.py` (`dump_to_yaml` at line 602, `restore_defaults_yaml` at line 709 — both call sites of `yaml.safe_dump`).
  - `androscan/web/settings_routes.py` (line 171 — the `POST /api/settings/global` route handler that invokes `dump_to_yaml`).
  - `androscan/web/frontend/src/tabs/SettingsTab.tsx` (line 273 — the Raw YAML section copy operators currently fall back to as a workaround).
  - `global_config.yaml` (the documented in-tree source of truth — restore via `git checkout --` if a form save destroys it).

---

### ISSUE-017: Phase 13 v1 ships static fired-edge styling — marching-ants animation deferred
- status: Open (Phase 13 v2 candidate; operator-demand-gated)
- impact: Low
- area: Behavior Trace v3 / `ExecutionFlow` flowchart UX
- introduced / observed: 2026-05-08 (Phase 13 v1 ship)
- summary:
  Phase 13 v1's `ExecutionFlow` flowchart renders fired edges as accent-blue solid 1.5px stroke (vs. the dim/dashed-at-55%-opacity untaken edges) per DEC-029's color-only emphasis lock. A v1-considered alternative was to additionally animate the fired path with a marching-ants `stroke-dashoffset` animation to communicate "live execution" more obviously, but DEC-029 explicitly rejected the animation as chartjunk: the static-color emphasis is enough signal in a 5-30 node graph. Captured here so a future contributor doesn't re-add the animation thinking it's an improvement without operator sign-off.
- why it matters:
  If real-app dogfooding on dense graphs (50+ nodes) shows operators don't immediately recognize the fired path against the verdict-colored static edges in `Both` mode, the marching-ants animation becomes a candidate v2 affordance — but ONLY then; speculative addition adds bundle weight + visual noise without measured demand.
- current workaround:
  Operators can switch to `Dynamic` mode (untaken edges go gray-dashed-55%-opacity, fired path solid accent-blue at full opacity) for a higher-contrast view of just the runtime-observed call shape; this preserves the call-tree topology context while strongly emphasising what fired.
- recommended fix:
  Add a `.execution-flow-edge-fired-animated` CSS variant gated on a Settings toggle (default off) that sets `stroke-dasharray: 4 3` + `stroke-dashoffset` keyframe animation; promote to default-on only if operator demand justifies it. Keep DEC-029's color-only emphasis as the canonical baseline.
- related tasks:
  - none (v2 candidate; promote when operator demand surfaces)
- related docs:
  - `docs/DECISIONS.md` DEC-029 (locks color-only edge emphasis; documents the iteration history rejecting thicker fired edges + larger arrowheads)
  - `androscan/web/frontend/src/components/trace/ExecutionFlow.tsx` (`VerdictEdge` custom component)
  - `androscan/web/frontend/src/App.css` (`.execution-flow-edge-fired` namespace)

---

### ISSUE-018: Phase 13 v1 ships static heuristic verdicts — branch-outcome inference from dynamic data deferred
- status: Open (Phase 13 v2 candidate; operator-demand-gated)
- impact: Medium
- area: Behavior Trace v3 / `branch_classifier` precision
- introduced / observed: 2026-05-08 (Phase 13 v1 ship)
- summary:
  Phase 11 v2's `branch_classifier.classify_branch_outcomes` heuristically classifies each `DecisionPoint`'s branches as `deny` / `allow` / `neutral` with a 4-tier confidence score per the locked verdict catalog from 10.3 + DEC-024. Phase 13 v1 ships a multi-method dynamic tracer that observes which branches *actually* fire at runtime — but the dynamic data does NOT feed back into the static verdict classification. A "neutral"-classified branch that fires consistently as `deny` on real input keeps its static "neutral" label; the dynamic overlay just paints it accent-blue without re-classifying it. DEC-029 deferred this as out-of-scope-for-v1 because the static heuristic verdicts are already operator-actionable enough; revisit when dogfood shows a measurable gap.
- why it matters:
  The Inspector's "Predicate origin" + bypass-plan suggestions are driven by the static verdict — a "neutral" branch the planner skipped over might in fact be the actual deny gate on the real input the operator captured. Without re-classification, operators who run a dynamic trace don't get bypass-plan refinements from the runtime data; they only see *which* of the existing plans corresponds to the path that fired.
- current workaround:
  Operators can manually identify gates via the dynamic-trace overlay (the fired path with a return value of `false` flowing into a deny sink is observably the deny gate, regardless of the static verdict label) and use the existing `[Hook this method]` / `[Trace this gate]` action-row affordances in the Inspector to investigate further. The chat dock's `summarise_method` widget can also clarify intent on a per-method basis.
- recommended fix:
  Add a `branch_outcome_dynamic` field to `DecisionPoint` (additive, no schema bump on `trace.sqlite` — populated lazily from `dynamic_trace.jsonl` on read) carrying a `tuple[BranchVerdict, ConfidenceTier, ObservationCount]`. UI surfaces the dynamic verdict alongside the static one when both exist; bypass planner re-runs against the dynamic verdict when confidence > 0.85 + observation count >= N (N TBD via dogfood).
- related tasks:
  - none (v2 candidate; gates on dogfood telemetry showing the precision gap matters in practice)
- related docs:
  - `docs/DECISIONS.md` DEC-029 (alternatives considered: "Branch-outcome inference from dynamic data — rejected for v1")
  - `androscan/analysis/branch_classifier.py` (the static classifier; v1 surface)
  - `apps/<app_id>/<run>/dynamic_trace.jsonl` (the dynamic-data source the future inference would consume)

---

### ISSUE-019: Phase 13 v1 ships per-thread depth pill — full per-thread layout reshape deferred
- status: Open (Phase 13 v2 candidate; operator-demand-gated)
- impact: Low
- area: Behavior Trace v3 / `ExecutionFlow` layout
- introduced / observed: 2026-05-08 (Phase 13 v1 ship; sub-step 13.8)
- summary:
  Phase 13 v1's `ExecutionFlow` renders ALL methods in the active anchor's closure on a single left-to-right column-rank layout regardless of which thread they fire on at runtime; thread context is communicated via a corner depth pill (`d:N · t:M`) on each fired node. The canvas mockup envisioned an alternative "per-thread lane" layout where methods fired on Thread A render in one horizontal swim-lane and methods fired on Thread B render in a parallel one below, making cross-thread call patterns visually obvious. Sub-step 13.8 deferred the reshape on operator-dogfood-driven judgment that the corner pill preserves the call-tree mental model adequately for the typical 5-30-method anchor without the layout-substrate change cost.
- why it matters:
  Multi-threaded apps where callbacks fire on a worker thread and UI updates fire on the main thread (the dominant Android pattern) currently render as a single visual graph where the operator has to mentally diff `t:1` vs `t:13` corner pills to reconstruct the threading topology. For 5-30-method anchors this is feasible; for 50+-method anchors with 3+ threads the corner pill stops scaling.
- current workaround:
  Operators can hover the depth pill for a tooltip showing the full `threadId` + `threadDepth` + `lastFireTs` from the `LiveValueRecord`. The Inspector's "Live observation" section also surfaces the thread context. For complex anchors, operators can narrow the entry method to scope the closure tighter before running the dynamic trace.
- recommended fix:
  Add a `layoutMode: "single-column" | "per-thread-lanes"` prop on `<ExecutionFlow>` (default `"single-column"` matching v1); promote to operator-controllable Settings toggle if dogfood shows demand. Per-thread lanes would route nodes by `liveValues.get(overloadKey)?.threadId` into separate Y-bands with the existing column-rank layout preserved within each band.
- related tasks:
  - none (v2 candidate; gates on dogfood telemetry showing the corner-pill view insufficient on multi-threaded anchors)
- related docs:
  - `docs/DECISIONS.md` DEC-029 (locks per-thread depth visualization as a v1 deliverable; sub-step 13.8 closing note records the corner-pill-vs-lane-reshape decision)
  - `androscan/web/frontend/src/components/trace/ExecutionFlow.tsx` (`MethodNode` depth pill rendering)
  - `androscan/web/frontend/src/api/trace.ts` (`useDynamicTrace` hook populates `threadId` + `threadDepth` on every `LiveValueRecord`; the data is there for either layout)

---

### ISSUE-020: Phase 13 v1 surfaces cached summaries only via chat dock, not dedicated GET route
- status: Open (Phase 13 v2 candidate; operator-demand-gated)
- impact: Low
- area: Behavior Trace v3 / `Inspector` cached-summary discoverability
- introduced / observed: 2026-05-08 (Phase 13 v1 ship; sub-step 13.9)
- summary:
  Phase 13 v1's `Inspector` Summary section shows a generated summary ONLY when the corresponding method has fired during the active dynamic-trace session OR the operator clicks the "Discuss in chat" button which fires the `summarise_method` skill via the agentic loop (cache-hit returns the cached summary verbatim; cache-miss generates a fresh one). On a static-only inspection of a never-fired-this-session method that DOES have a cached summary in `skill_results_cache.json` from a previous run, the Inspector renders the empty-state placeholder ("Summary not yet generated. Run a dynamic trace…") instead of the cached summary. The chat-widget path is the operator-discoverable workaround but adds one click + one chat-dock round-trip vs. an in-place Inspector render.
- why it matters:
  Operators returning to a previously-inspected app and clicking through Inspector nodes to refresh their memory get the empty-state placeholder for every method until they fire a dynamic trace, even though the summaries are already cached. The chat-widget click works but adds friction.
- current workaround:
  Operator clicks "Discuss in chat" on the Inspector → the agentic loop fires `summarise_method` → the skill cache-hit returns `cached=True` widget → the chat dock renders the interactive `<MethodSummaryWidget>` card with the same content the Inspector would show. One extra click vs. an in-place render.
- recommended fix:
  Add a small `GET /api/trace/{app_id}/summary?class=...&method=...&descriptor=...` route reading from the existing `skill_results_cache.json` storage with byte-equal cache-key derivation (mirrors `androscan/web/trace_summary.py::summary_cache_params`); Inspector mounts a `useEffect` on selection-change that fires this GET and populates the Summary section's `cached` state directly. No dedicated endpoint authority surface — the route is read-only over the existing cache file. Defer the route until dogfood shows operators routinely want one-click cache lookups without the chat-dock round-trip.
- related tasks:
  - none (v2 candidate; operator-demand-gated per the 13.9 closing note)
- related docs:
  - `docs/DECISIONS.md` DEC-029 v1 closing note (records this deferral)
  - `androscan/web/trace_summary.py` (`summary_cache_params` — the byte-equal cache-key derivation a future GET route would reuse)
  - `androscan/internal/skill_results_cache.py` (the cache layer the future route would read)
  - `androscan/web/frontend/src/components/trace/Inspector.tsx` ("Discuss in chat" button — the v1 chat-widget path)

---

### ISSUE-021: Phase 13 v1 ships no `<MethodSummaryWidget>` "Refresh summary" + no pan-to-fit-on-selection
- status: Open (Phase 13 v2 candidate; operator-demand-gated)
- impact: Low
- area: Behavior Trace v3 / chat widget UX + flowchart UX
- introduced / observed: 2026-05-08 (Phase 13 v1 ship; sub-step 13.9)
- summary:
  Two small UX polish items deferred from Phase 13 v1: (1) the `<MethodSummaryWidget>` chat card has no "Refresh summary" affordance — once a cached summary is rendered, the operator has to either re-run the dynamic trace (which fires `summarise_method` cache-hit and re-emits the same widget) or re-fire the prompt manually (which produces a fresh LLM call). (2) `<ExecutionFlow>` doesn't pan-to-fit on selection — when the operator clicks a node that's currently outside the viewport (panned offscreen on a large graph), the Inspector opens but the flowchart stays parked where it was; the operator has to manually pan to find the node they just clicked.
- why it matters:
  (1) Cached summaries that turn out to be stale (e.g. the LLM's summary was wrong, or the operator wants a different summary perspective) require a workflow-level workaround. (2) Large graphs (50+ nodes) make node-finding-after-selection a manual chore.
- current workaround:
  (1) Operator drops the `apps/<app_id>/skill_results_cache.json` slot for the affected method (manual JSON edit) OR re-fires `summarise_method` from the chat dock with explicit "ignore cache" prompt language (the LLM may or may not honor it). (2) Operator manually pans the flowchart to the selected node OR uses the React Flow `<MiniMap>` to navigate.
- recommended fix:
  (1) Add a "Refresh summary" button to `<MethodSummaryWidget>` that fires `summarise_method` with a `force_refresh: true` param the skill respects (skip cache lookup; overwrite cache on success). (2) Add a `panToFitOnSelection: boolean` prop on `<ExecutionFlow>` (default `true`); fire `reactFlowInstance.fitView({ nodes: [selectedNode], padding: 0.3 })` on `selectedNodeId` change. Both are small additions; defer until operator demand surfaces.
- related tasks:
  - none (v2 candidate; operator-demand-gated per the 13.9 closing note)
- related docs:
  - `docs/DECISIONS.md` DEC-029 v1 closing note (records both deferrals)
  - `androscan/web/frontend/src/components/chat/widgets/MethodSummaryWidget.tsx` (current v1 widget — no refresh affordance)
  - `androscan/web/frontend/src/components/trace/ExecutionFlow.tsx` (current v1 — no pan-to-fit on `selectedNodeId` change)
  - `androscan/skills/summarise_method.py` (the skill `force_refresh: true` would route through)

---

_(See § 8 Resolved issues — `ISSUE-016` was closed by **LCP.6 (2026-05-06)**: GBNF grammar enforcement for llama.cpp + JSON-schema mode for Ollama 0.5.0+ now constrain the response envelope at the model's logits-sampling level. The fail-soft retry path in `androscan/internal/workflow.py` is still in place but is no longer the primary defense; operators on Q4_K_M / IQ4_XS quants regain reliable structured-JSON output.)_

---

## 7. Accepted limitations

Use this section for limitations that are currently acceptable and not immediate defects.

Keep these explicit so they are not mistaken for bugs or forgotten assumptions.

### LIMIT-001: Web UI requires a frontend build for static assets
- status: Accepted Limitation
- reason: Phase 6 serves the React bundle from `androscan/web/static/` after `npm run build` in `androscan/web/frontend/`. That directory is **gitignored**; without a local build, `GET /` returns JSON instructions (503-style) while **REST and WebSockets still work**.
- impact: Low for API-only use; operators run one npm build for full UI.
- revisit when: optional checked-in production bundle or install hook is added.
- related docs:
  - `docs/STATE.md`
  - `androscan/web/frontend/README.md`
  - `docs/DECISIONS.md` DEC-015

---

## 8. Resolved issues

Move resolved entries here if keeping historical memory is useful.

Format:

### ISSUE-XXX: [Title]
- status: Resolved
- resolved date:
- resolution summary:
- related tasks/docs:

### ISSUE-016: JSON-validity drift on aggressive quants under v1 LCP local providers (Q4_K_M / IQ4_XS)
- status: Resolved
- resolved date: 2026-05-06 (LCP.6 — GBNF grammar enforcement + Ollama JSON-schema mode landed; closes the JSON-validity-drift gap from the LCP.5 ship)
- resolution summary:
  Single LCP.6 commit (no 6a/6b split needed — the planning checkpoint surveyed `SkillMeta.params_schema` and confirmed the per-skill `params` shape is **operator-prose, not typed JSON Schema**, so the LCP.0 estimate of "+200 LOC including per-skill schema introspection" was over-scoped; the actual emitter is ~95 LOC of pure-Python in `androscan/llm/grammar.py`). The grammar / JSON-schema constrains the response envelope at the model's logits-sampling level, replacing the fail-soft post-hoc parser-rejection path as the primary defense.

  **As-built shape:**
  - **`androscan/llm/grammar.py`** (NEW, ~+330 LOC including doc-blocks): emits two parallel constraint shapes from a shared registry-driven skill-name discovery — `build_response_json_schema(skill_names)` (Ollama's `format: <schema>` payload, supported since Ollama 0.5.0 / Dec 2024) + `build_response_gbnf(skill_names)` (llama.cpp's `grammar:` field on `/v1/chat/completions`). Per-key shape: `summary` (optional string), `skill_requests[*].skill` (discriminated-union enum over the 9 LLM-tier skills from `list_llm_skills()`), `skill_requests[*].params` (permissive object), `hypotheses[*]` (permissive object — the parser default-fills missing fields, over-constraining would trigger spurious sampling rejections). `top-level additionalProperties: False` is the strongest constraint that doesn't break real LLM output. `is_grammar_enabled(config)` is the single feature-flag helper read by both client branches.
  - **`androscan/llm/client.py`** wiring: pre-computes the schema / GBNF outside the retry loop (cheap), attaches it to the request payload when `Config.local_grammar_enabled` is True (default) AND the base_url isn't in the per-process disabled-set cache. On HTTP 400 with body matching the narrow heuristic `_looks_like_schema_error` (Ollama) / `_looks_like_grammar_error` (llama.cpp), the base_url is added to the cache and the request is retried once with the v1-LCP wire shape (Ollama: `format: "json"` string; llama.cpp: drop the `grammar` field, keep `response_format: {"type": "json_object"}`). Cache is process-lifetime — operators upgrading their runtime get a clean slate on next AndroScan restart. Cloud path is unchanged (the OpenAI-compat `response_format` contract enforces validity at the SDK layer).
  - **`Config.local_grammar_enabled`** (NEW, defaults `True`): single kill-switch under YAML `llm.local_grammar_enabled`, env `ANDROSCAN_LOCAL_GRAMMAR_ENABLED` (accepts the standard truthy/falsy aliases). Live-reloadable. Surfaced in the Settings UI under both the "Local (Ollama)" and "Local (llama.cpp)" provider subsections (cross-provider local knob; never under Cloud).
  - **Tests**: 38 new in `tests/test_grammar.py` (skill discovery, JSON-schema shape, GBNF well-formedness + per-skill alternative coverage, parser-invariance) + 11 new in `tests/test_llm_client.py` (Ollama schema-mode happy / 400-fallback / kill-switch off / unrelated-400 unaffected; llama.cpp grammar-mode happy / 400-fallback / kill-switch off / unrelated-400 unaffected) + 13 new in `tests/test_config.py` (`TestLocalGrammarEnabled`: default, YAML + env round-trip, env truthy/falsy alias coverage, `with_overrides` coercion, `global_view_from_config` round-trip). Total: 1092 tests pass post-LCP.6 (up from 1030 at LCP.5; +62 new tests as planned).
- related tasks/docs:
  - `docs/TASKS.md` § **LCP — llama.cpp local provider — sub-step backlog** row LCP.6 (marked complete 2026-05-06).
  - `docs/DECISIONS.md` **DEC-027** LCP.6 closing note (final closing note for the LCP track).
  - `docs/STATE.md` (LCP.6 entry under "Recent completed work" + "What currently exists" updates for `androscan/llm/grammar.py` + the kill-switch knob).
  - `docs/DESIGN_DOC.md` (LCP track promoted to fully-complete).
  - `androscan/llm/grammar.py` (new module — emitter + skill-name discovery + feature-flag helper).
  - `androscan/llm/client.py` (`_complete_ollama` + `_complete_llamacpp` — grammar-mode wiring + opportunistic 400-fallback + per-base-url disabled-set cache).
  - `androscan/config/loader.py` (`Config.local_grammar_enabled` + `LIVE_RELOADABLE_FIELDS` + `CONFIG_FIELD_MAP` + `_merge_from_yaml` + env override + `with_overrides` coercion).
  - `androscan/web/frontend/src/tabs/SettingsTab.tsx` (`LlmProviderRadio` — renders the new field under both local provider subsections).
  - `global_config.yaml` (`llm.local_grammar_enabled` doc comment block).

### LIMIT-002: No static call graph or Frida in-repo yet
- status: Resolved
- resolved date: 2026-04-27 (Hook Lab v1 complete — sub-steps 4.1 → 4.8 all landed)
- resolution summary:
  Phases 8 (static call graph from Smali) and 9 (Frida integration) both landed in **Hook Lab v1** between April 25–27 2026. The static call graph ships as: Smali parser + virtual-dispatch resolver in `androscan/analysis/call_graph.py`, per-app SQLite store at `apps/<app_id>/.decompiled/<sha>/call_graph.sqlite` (DEC-016 amended by DEC-023 — the original `call_graph.json` blob clause was superseded), five REST routes (`GET /api/graph/{app_id}` + paginated neighbors / paths / status + `POST .../rebuild`), a Cytoscape.js pane in `CallGraphView.tsx` with package-overview / focus-subgraph layouts, and the `query_call_graph` LLM-tier skill (sub-step 4.7) for agentic graph queries. The Frida integration ships as: a headless adapter in `androscan/adapters/frida.py` behind a single import seam (`[frida]` extra is opt-in), six hook templates (`entry_exit_log`, `ssl_pinning_bypass`, `crypto`, `shared_preferences`, `intent`, `scope_inspector`) with deterministic pentester summaries, a Stage→Inject UI in `HookLabTab.tsx` with `pyjsparser`-driven JS pre-validation, WS trace + JSONL persistence to `apps/<app_id>/<run_ts>/frida/<session>.jsonl`, server-side `hook_target_package_prefix` allowlist (403 `hook_blocked` on violation), the `generate_frida_hook` LLM-tier skill (sub-step 4.7 — first real consumer of DEC-022's `requires_confirmation=True` consent class), and a live Cytoscape overlay (sub-step 4.8 — fired methods render in bold cyan with hit counts on hover; static = muted grey per DEC-023). Open follow-ups captured separately: ISSUE-010 (Monaco from CDN — air-gap), ISSUE-011 (FastAPI `on_event` deprecation — pure tech-debt), ISSUE-012 (Frida overlay aggregates hits across method overloads — intentional v1 trade-off, queued for v2). Free-form LLM JS, reflection-based dispatch, modify-return / mutation hooks, and `frida-server` auto-provisioning are explicitly v2 / v3 scope.
- related tasks/docs:
  - `docs/STATE.md` (Hook Lab 4.1 → 4.8 sub-bullets)
  - `docs/TASKS.md` § Hook Lab v1 — sub-step backlog (v1 complete 2026-04-27)
  - `docs/DESIGN_DOC.md` (Phases 8 + 9 — both annotated "landed 2026-04-27 via Hook Lab v1")
  - `docs/DECISIONS.md` DEC-016 (Smali-first call graph; storage clause amended by DEC-023), DEC-017 (Frida user-confirmation requirement — fulfilled by Option A + deterministic pentester summary), DEC-023 (Hook Lab v1 — eight sub-step specifics + Hook-Lab-complete closing note)
  - `docs/SAFETY_AND_SECURITY.md` §12 (Hook Lab v1 — what now-shipped controls do)

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