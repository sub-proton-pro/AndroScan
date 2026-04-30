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
- status: Open
- impact: Medium
- area: web / chat / RAG
- introduced / observed: 2026-04 (Inspect-tab testing on a fixture banking APK)
- summary:
  `androscan/web/chat.py` is single-pass: it runs at most one `_enrich_inspect_with_rag` sweep (top-4 chunks, fail-soft) up-front, then calls the LLM once and returns prose. The LLM cannot request additional skills mid-turn the way `androscan/internal/workflow.py` does. When the question hinges on a specific method-level chunk that didn't make `_INSPECT_RAG_TOP_K = 4`, the model honestly hedges with "decompile and look it up yourself" instead of being able to call `search_decompiled_sources` (or `get_decompiled_method`) for the missing piece — even though those skills are registered and the analysis pipeline already uses them.
- why it matters:
  It produces low-trust answers for questions that the registered skill set can answer in milliseconds, undercutting the workbench's "ask the LLM about this APK" value proposition. Operators learn not to trust Inspect chat for deep questions, which weakens adoption.
- current workaround:
  Ask narrower questions, paste the suspected class name explicitly into the prompt so the RAG embedding aligns with the class header chunk, or run the full `androscan.py --apk ... --task ...` analysis pipeline (which has the agentic loop).
- recommended fix:
  Implement the bounded agentic skill loop + consent-class hook described in **DEC-022**. Independently, raise `_INSPECT_RAG_TOP_K` from 4 → 8–10 and bump `_INSPECT_RAG_PER_HIT_CHARS` proportionally as a same-day band-aid that reduces (but does not eliminate) the failure rate.
- related tasks:
  - "RE Workbench chat — agentic skill loop (P2, planned per DEC-022)" in `docs/TASKS.md` (under § Interactive RE Workbench)
- related docs:
  - `docs/DECISIONS.md` DEC-022
  - `docs/STATE.md` (Partially implemented — workbench chat single-pass)
  - `androscan/web/chat.py`
  - `androscan/internal/workflow.py`

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