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