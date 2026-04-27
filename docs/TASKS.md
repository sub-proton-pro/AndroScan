# Tasks

This document is the working task queue for the repository.

Its purpose is to tell a human or AI agent what should be worked on next, what is currently active, what is blocked, and what completion looks like.

This file should be kept current.

---

## How to use this file

1. Start with the **Active Task** section.
2. If there is no active task, pick the top unblocked item from **Priority Queue**.
3. Before implementation, confirm:
   - scope
   - affected modules/layers
   - expected tests
   - documentation impact
4. After completion:
   - **update sub-task status in this file immediately** (mark the sub-task done, e.g. `[x]` or move to completed list) so other IDEs and agents know where to take over
   - move the task to Completed when the whole phase/task is done
   - update follow-ups if needed
   - update `docs/STATE.md` if current reality changed

Do not start multiple unrelated tasks at once unless explicitly instructed.

---

## Active Task

None. Phase 4 partial complete; CI remains parked. **Phase 6 (RE Workbench shell)** is implemented in code (`androscan/web/`, CLI `--serve`); Phases **7–9** remain as specified below. Pick from Priority Queue or continue RE roadmap.

---

## Interactive RE Workbench (Phases 6–9)

Planned work: web-based interactive reverse engineering on top of the existing CLI pipeline — emulator mirror, click-to-code, static call graph from Smali, Frida tracing/hooking. **Stack (target):** FastAPI + WebSocket backend, React + Vite + TypeScript frontend, Monaco Editor, Cytoscape.js.

### Sub-task status (implementation)

| Phase | Title                         | Status   |
|-------|-------------------------------|----------|
| 6     | Web UI shell + mirror         | MVP done (FastAPI + React build; `adb screencap` mirror + logcat WS + tap API; scrcpy / UI log filters = follow-up). **UX step 1 done:** tabbed shell with `react-resizable-panels` + per-tab `ChatDock`. **UX step 2 done:** triage endpoints + chat back-end with guardrails + Reports tab refresh (Markdown findings, per-card triage actions, chat wired to `/api/chat`). **UX step 3 done:** `androscan/rag/` (chunker + embed providers + SQLite vector index + cosine top-k), `/api/rag/{app_id}/{status,rebuild,query}`, auto-build on decompile completion, `search_decompiled_sources` LLM skill, Inspect-tab chat enrichment; click-to-code (`POST /api/inspect/map`) + persistent decompile cache (`/api/decompile`, `/api/code/{tree,file}`) + package-scoped logcat with stable Linux-UID filtering + mirror online/offline (`/api/device/status`) + sandboxed `/api/adb/shell` + `resolve_ui_element` LLM skill (deterministic fuser; also called inline by `/api/inspect/map` to attach a `resolution` block); Inspect-tab frontend wiring (4-column resizable layout, MirrorView with status dot + click-to-map, ElementMappingPanel with `BestBanner` / `ResolutionAlternatives` / `RagHitsList` for the fuser output, CodeView with scroll-to-line + highlight, ScopedLogcat, AdbShell). **UX step 3.5 done:** Settings tab (`#/settings`) — global config (form + raw-YAML editor + reset), per-app overrides (`apps/<app_id>/app_settings.json`), live status (global + per-app health probes via `androscan/web/health_probes.py` + `/api/status/{global,apps/{app_id}}` with `asyncio.gather` + 3 s cache), diagnostics (raw API + `/api/settings/reload`); `androscan/config/loader.py` extended with `CONFIG_FIELD_MAP` + `LIVE_RELOADABLE_FIELDS` + write-back helpers; `app.py` keeps live `Config` on `app.state.config`; `HealthDot` in the global header. See DEC-020 + DEC-021. |
| 7     | Click-to-code mapping         | Backend + frontend both done (`POST /api/inspect/map` + `androscan/web/inspect_map.py`; `resolve_ui_element` LLM skill fuses raw candidates + RAG into a single `best`/`alternatives`/`reasoning` block; Inspect-tab UI surfaces the fused pick in a prominent banner, alternatives + RAG hits as collapsible sections, and auto-jumps the Code Browser to `resolution.best`). |
| 8     | Static call graph (Smali)     | Not started (UX step 4 inside `Hook Lab`) |
| 9     | Frida integration             | Not started (UX step 4 inside `Hook Lab`) |

**Phase 6→9 UX rollout (in addition to backend phases above):**

1. **[done]** Tab shell + `react-resizable-panels` + `WorkbenchContext` + `ChatDock` skeleton.
2. **[done]** Reports tab refresh + chat back-end. `androscan/web/triage.py` (`apps/<app>/<run>/triage.json`); `androscan/web/chat.py` with layered guardrails (allowlisted system prompt per tab, length caps, ANSI/zero-width/secret redaction, per-kind attachment budgets, `<context>` prompt-injection wrapping, rate limit, transcript JSONL). LLM client extended (`response_format=None`, `messages` override). Frontend `FindingCard` with Markdown + per-card triage buttons + severity override + note; `ChatDock` wired to `/api/chat`. **Note:** Lane-1 RAG indexer for static decompiled code is **deferred to step 3** where it pairs with click-to-code mapping (the chat needs code retrieval there; Reports works on dossier + finding alone).
3. Inspect tab — **done:**
   - **[done]** Lane-1 RAG indexer over decompiled sources (one-shot after jadx finishes). New `androscan/rag/` package: brace-balanced chunker for `.java`/`.kt`, `EmbedProvider` protocol with `fastembed` (default ONNX) / `ollama` / `hash` (test fallback) implementations, per-app SQLite vector store (WAL, packed `float32` blobs) at `apps/<app_id>/.decompiled/<sha>/rag.sqlite`, brute-force cosine top-k (numpy when present, pure-Python fallback). Endpoints `GET /api/rag/{app_id}/status`, `POST /api/rag/{app_id}/rebuild`, `POST /api/rag/{app_id}/query`. Auto-built via `schedule_rag_build_after_decompile` when the decompile cache turns ready. New `llm`-tier skill `search_decompiled_sources` (query, top_k, package_prefix, file_substr) advertised in the prompt catalog and fail-open. Inspect-tab chat enrichment in `androscan/web/chat.py::_enrich_inspect_with_rag` appends top-k snippets as `code` attachments (fail-soft when RAG is unavailable). New `pyproject` extra `[rag]` (fastembed, numpy). `Config` adds `rag_embed_provider` / `rag_embed_model` / `rag_top_k_default` (YAML `rag.*`, env `ANDROSCAN_RAG_*`). 197 tests pass.
   - **[done]** Package-scoped logcat backend (`/ws/logcat?package=…`) using **stable Linux-UID filtering** (`adb logcat --uid=<uid>` resolved via `stat /data/data/<pkg>` or `dumpsys package`) so the stream survives app restarts; falls back to `--pid=` re-resolution on older devices.
   - **[done]** Mirror online/offline probe at `GET /api/device/status` (`adb get-state`); UI badge wiring still pending.
   - **[done]** Click-to-map backend (`POST /api/inspect/map` in `androscan/web/inspect_map.py`): `dumpsys activity top` for foreground activity, `uiautomator dump /dev/tty` for view hierarchy, smallest-area-clickable bounds match, regex grep over the persistent decompile cache for handler candidates (`findViewById` > `onClick_near` > `compose_id` > `reference`). Persistent decompile cache backend in `androscan/web/decompile_cache.py` (`POST /api/decompile/{app_id}`, `GET /api/code/{app_id}/{tree,file}`) keyed by `sha256(apk)` under `apps/<app_id>/.decompiled/<sha>/sources/`. Sandboxed `POST /api/adb/shell` proxy with `shlex` parsing + denylist + 20 s timeout + 200 KB output cap.
   - **[done]** New `llm`-tier skill `resolve_ui_element` (`androscan/skills/resolve_ui_element.py`) — deterministic, explainable scorer over raw click-to-code candidates with optional Lane-1 RAG enrichment (synthesises a query from `text` > `content_desc` > short resource id). Pure-function `resolve()` reused inline by `/api/inspect/map` so the response now carries a `resolution` block ({`best`, `alternatives`, `rag_hits`, `reasoning`}) without an LLM round-trip. Tests: `tests/test_resolve_ui_element.py` (13 tests). **210 tests total, all passing.**
   - **[done]** Inspect-tab **frontend** wiring (`androscan/web/frontend/src/tabs/InspectTab.tsx` + components). Four-column resizable layout: Projects sidebar | Classes-&-methods tree + scoped logcat | UI-mapping/Code-browser tabs + chat dock | Mirror + adb-shell. `MirrorView` renders `/ws/mirror` PNG frames, polls `/api/device/status` for an online/offline status dot, translates clicks from image-pixel space to device-pixel space and forwards to `/api/inspect/map`. `ElementMappingPanel` shows a sub-activity progress bar during the map call and then renders the fused `resolution.best` as a prominent banner (`SourceBadge` regex/RAG pill, kind pill, score, reasons grid, snippet, "Open in Code browser"), the deterministic candidates list with kind pills + per-row "Open" buttons, plus collapsible `ResolutionAlternatives` and `RagHitsList` (with the synthesised RAG query). The Code-browser tab uses `CodeView` (line numbers, scroll-to-line, persistent line-range highlight, find query, gear prefs); a tap auto-opens `resolution.best.file` and scrolls/highlights the picked line range, falling back to `candidates[0]` when no fuser pick exists. Chat attachments (`attachments` memo in `InspectTab`) include the fused `best_handler` JSON block alongside the element + candidates so the LLM sees the picked file/line/reasons. New CSS for `.source-badge` (deterministic = green, rag = warn), `.best-banner` (accent border + soft background, reasons grid), `.resolution-alts`, `.rag-hits`. Vite production build clean (308 modules, 32 KB CSS, 393 KB JS).
3.5. **[done]** Settings tab — fourth top-level tab (`#/settings`) with four sub-panels:
   - **Global settings:** form view (with source pills `yaml`/`env`/`default` per field, env-lock indicator, restart-required pill on non-live-reloadable fields) **plus** a raw-YAML editor with server-side validation; "Reset to defaults" button.
   - **App settings (per-app):** project dropdown; per-key override-or-inherit toggles writing to `apps/<app_id>/app_settings.json` (atomic); "Reset to defaults" with force-with-warning when an override would diverge from a global field still consumed via the boot-time closure.
   - **Status:** live cards for global health (adb / jadx / apktool / frida / Ollama / embed-provider / disk free + writability) and per-app health (`pm path`, foreground activity, UID, uiautomator dump, apk-sha drift, RAG status, decompile cache freshness); "Refresh now" cache-bypass button.
   - **Diagnostics:** raw API JSON for global + per-app payloads, `POST /api/settings/reload`.
   - Backend modules: `androscan/web/health_probes.py` (timeboxed pure-function async probes — never raise, return `{ok, label, ...}`), `androscan/web/per_app_settings.py` (atomic schema-versioned writes, `effective_settings` merger), `androscan/web/status_routes.py` (`asyncio.gather` + 3 s in-process cache; `invalidate_status_cache()` on every settings save), `androscan/web/settings_routes.py` (validates raw YAML, `dump_to_yaml`, `restore_defaults_yaml`; returns `restart_required` per field). `androscan/config/loader.py` extended with `CONFIG_FIELD_MAP`, `LIVE_RELOADABLE_FIELDS`, `global_view_from_config`, `effective_sources`, `with_overrides`, `coerce_yaml_value`, `dump_to_yaml`, `save_raw_yaml`, `restore_defaults_yaml`, `validate_raw_yaml`, `read_raw_yaml`, `discover_config_path`. `app.py` keeps the live `Config` on `app.state.config` (`_current_config`/`_set_config` helpers) so live-reloadable fields take effect without restarting uvicorn.
   - Frontend: `SettingsTab` + sub-panels, `HealthDot` in the global header (30 s polling, deep-link to Settings), `api/settings.ts`, `api/status.ts`, ~300 lines of new CSS in `App.css`.
   - Tests: `tests/test_health_probes.py`, `tests/test_per_app_settings.py`, `tests/test_settings_routes.py`. All Python tests green; `tsc --noEmit` + `vite build` clean.
   - See **DEC-020** (settings tab design) and **DEC-021** (probe shape + asyncio.gather + cache).
4. Hook Lab tab: static smali call graph, Frida adapter + ring buffer + agentic tool calls (`frida_query`, `frida_stats`), Cytoscape overlay (static = muted grey, frida = bold cyan), Monaco decompiled + script editor, scope/hooks inspector, chat with frida summary + tail. **Implementation plan locked in DEC-023** (planning commit 2026-04-25); see § **Hook Lab v1 — sub-step backlog** below for the strictly-linear 8-sub-step rollout.

**§ Hook Lab v1 — sub-step backlog** *(strictly linear; one sub-step per Agent-mode session; locked in DEC-023 + amended DEC-016)*

| # | Sub-step | Key deliverables | Depends on |
|---|----------|------------------|------------|
| 4.1 | **[x] Smali call-graph backend** *(landed 2026-04-27)* | `androscan/analysis/` (`apktool_runner`, `smali_parser`, `dispatch`, `smali_types`, `call_graph`) with v2 fidelity (direct `invoke-*` edges + virtual/interface-dispatch BFS against in-app class hierarchy, `MAX_OVERRIDES_PER_INVOKE=64` guard with `truncated` flag on edges; locked edge-kind enum `direct | static | super | virtual_dispatch | interface_dispatch | external`; `may_have_unresolved_reflection` flag on reflection-callsite nodes). Per-app SQLite at `apps/<app_id>/.decompiled/<sha>/call_graph.sqlite` (schema_version 1: `classes` / `class_interfaces` / `nodes` / `edges` with INTEGER FKs, external callees materialised as `is_external=1` rows for uniform neighbour queries; WAL + autocommit; `meta` table; in-process `_RUNNING` registry with orphan-pending recovery after `PENDING_GRACE_SEC=30s`; atomic snapshot writes). Query API: `GET /api/graph/{app_id}` (paginated + `package_prefix` / `kind` / `include_external` filters, hard cap 5000), `GET /api/graph/{app_id}/neighbors/{node_ref:path}` (callers + callees; numeric id or URL-encoded smali signature), `GET /api/graph/{app_id}/paths?source=…&target=…` (bounded BFS, `max_hops≤12`, `max_paths≤50`), `GET /api/graph/{app_id}/status` + `POST /api/graph/{app_id}/rebuild`. Auto-build hook `schedule_call_graph_build_after_decompile` fires parallel to the RAG hook. New `_call_graph_card` in Settings → Status. 57 new tests in `tests/test_call_graph_{parser,dispatch,index}.py` + `tests/test_graph_routes.py` over fixtures at `tests/fixtures/call_graph_smali/` (multi-dex + interface + reflection). DEC-016 ratified in code. **354 / 354 tests green.** | apktool output |
| 4.2 | **[x] Hook Lab UI — Graph pane** *(landed 2026-04-27)* | Cytoscape.js viewer (`androscan/web/frontend/src/components/CallGraphView.tsx` + typed client `src/api/graph.ts`) with **package overview by default** (cose-bilkent layout, cross-package edge weights) and **right-click "Focus subgraph here"** with a 1–6 hops stepper rendering the neighbour subgraph in dagre LR. `virtual_dispatch` / `interface_dispatch` / `external` edges rendered dashed; `may_have_unresolved_reflection` nodes outlined in orange with an `[R]` suffix. Tippy.js node tooltips (method signature + reflection flag); inline filter; hidden-by-default external-node toggle; Rebuild button hits `POST /api/graph/{app_id}/rebuild`. Click a method node → in-tab `HookLabCodeView` loads the Java file via existing `/api/code/{app_id}/file` and renders `CodeView` with `emphasizeMethod`. Right-click context menu's "Open in Inspect" pumps a `pendingCodeNav` through `WorkbenchContext` and switches to the Inspect tab where a new `useEffect` consumes it via the existing `handleSelect` flow. Frontend contract locked by 3 new field-shape tests in `tests/test_graph_routes.py`. **357 / 357 tests green.** | 4.1 |
| 4.3 | **[x] Frida adapter foundation** *(landed 2026-04-27)* | New `androscan/adapters/{__init__.py, frida_client.py}`: `FridaClient` (process-wide; lazy `frida` import via `_frida_python()` test seam; `FridaUnavailableError` with install hint), `FridaSession` (per-attach state — `load_script` / `detach` / `stats`; thread-safe `collections.deque(maxlen=ring_size)` of `TraceEvent`s populated from the Frida message thread under a `threading.Lock`; optional `on_event` callback fired *after* the ring lands so 4.5's WS UI can never race ahead of `stats()`), `TraceEvent` mirroring Frida's native `send` / `error` / `log` kinds (raw message preserved). `detach_all()` wired on uvicorn shutdown via a guarded `@app.on_event("shutdown")` handler in `androscan/web/app.py`; `get_frida_client(app, config)` lazily caches on `app.state.frida_client`. New `frida_trace_ring_buffer_size` config knob (default 5000, clamped `>= 100`) wired through `Config` / `CONFIG_FIELD_MAP` / `LIVE_RELOADABLE_FIELDS` / `_merge_from_yaml` / `ANDROSCAN_FRIDA_TRACE_RING` env / `global_config.yaml`'s new `frida:` section. Two new health probes in `androscan/web/health_probes.py` — `probe_frida_server` (`adb shell pidof frida-server`; returns `{ok, running, pid, error}`) and `probe_frida_version_skew` (compares host `frida` CLI vs. `frida-server --version`; severity `None` / `"minor"` / `"major"`); aggregated by `status_routes.py` into a single `tools.frida_server` card via `asyncio.gather`. Frontend: `GlobalStatus.tools.frida_server` typed in `src/api/status.ts` (`{ok, label, running, pid, host_version, device_version, version_skew, error}`); `rollupGlobal` treats Frida readiness as **yellow** (non-critical for static workflows); one new `<StatusCardView>` in `SettingsTab.tsx`. New `[frida]` `pyproject.toml` extra (`frida>=16`, `frida-tools>=12`, `pyjsparser>=2.7`); `--setup` now installs `.[dev,rag,frida]` by default. New `device` pytest marker registered (deselect with `-m "not device"`; default suite is unchanged). 34 new tests across `tests/test_frida_client.py` (new), `tests/test_health_probes.py`, `tests/test_settings_routes.py`, `tests/test_config.py`, `tests/test_first_run_setup.py`. **391 / 391 tests green** (was 357). DEC-023 promoted Proposed → Active in this commit; DEC-021's "Hook Lab readiness rollup probe" follow-up ticked. **Headless by design** — no HTTP routes, no Hook Lab tab UI changes (only the Settings → Status readiness signal); 4.4 adds the template library and 4.5 lights up the Inject UI + WS + JSONL persistence. | `frida` Python pkg |
| 4.4 | **[x] Hook template library** *(landed 2026-04-27)* | New `androscan/adapters/frida_hooks/` package with the five v1 templates: `entry_exit_log` (3 params: class/method/label), `ssl_pinning_bypass` (multi-strategy: Conscrypt `TrustManagerImpl` + OkHttp3 `CertificatePinner` + generic `X509TrustManager`; 1 param: label), `crypto` (`javax.crypto.Cipher` init/doFinal observer; in/out lengths only — plaintext capture is an explicit consent gate left to hand-rolled scripts; 1 param: label), `shared_preferences` (getString/putString with key-prefix filter; 2 params: label + optional `key_prefix=""`), `intent` (`ContextWrapper.startActivity/startService/sendBroadcast` with action/data/component/package capture; 1 param: label). Each template = one Python module exporting `TEMPLATE: HookTemplate` (frozen dataclass: `id` / `name` / `description` / `params: tuple[HookTemplateParam, ...]` / `js_template` / `pentester_summary_template` / informational `sensitive_apis`). Renderer in `__init__.py`: `render(template, params) -> RenderedHook(template_id, js, summary, params_used)` validates in three stages (unknown-key → missing/empty-required → fill defaults), coerces every value to `str`, then `str.format`s the **same merged params dict** through both JS and summary; literal JS braces are escaped via `{{` / `}}`. Public API: `render` / `render_by_id` / `get_template` / `list_templates` / `register` / `discover` / `extract_format_fields`. Error taxonomy: `HookTemplateError` (base) → `HookTemplateNotFound` (unknown id; lists valid ids alongside) + `HookParamError` (one class for missing-required / empty-required / unknown-key). Explicit `_TEMPLATE_MODULES` tuple drives discovery (auto-globbing rejected — scratch file would silently become a registered template). New `tests/test_frida_hook_templates.py` (**+67 tests**): `TestRenderer` (contract against a hand-rolled fixture; happy path + every error variant + default fill + value coercion + `{{` literal-brace escape + `extract_format_fields` named-only / skip-positional / strip-attribute / index), `TestRegistryFailClosed` (parametrised over `_TEMPLATE_MODULES`: every module exports `TEMPLATE: HookTemplate`; id matches basename; JS + summary non-empty; both `str.format` placeholder sets ⊆ declared params; every required param appears in JS *or* summary; every template renders cleanly with stub inputs — this is the test that makes "stub or missing summary" a hard CI failure), `TestTemplates` (loose substring smoke renders per shipped template). Adding a new template is now a strict two-deliverable change (JS body + non-empty summary). Full suite: **458 / 458 green** (was 391; +67). `tsc --noEmit` + `vite build` clean (no FE changes — headless by design; 4.5 owns Stage→Inject + WS + JSONL persistence, 4.7 owns the `generate_frida_hook` LLM skill that fills these templates). DEC-023 gets a "Hook template library (sub-step 4.4 specifics)" sub-bullet documenting storage / renderer contract / fail-closed mechanism. | 4.3 |
| 4.5 | **[ ] Hook builder + Stage→Inject flow** | Hook Lab UI for picking a method (from the graph pane or by typing `class.method`), choosing a template, filling parameters (LLM-tier `generate_frida_hook` skill OR manual fill), `pyjsparser` syntax-checking the rendered JS, then displaying **(rendered JS in Monaco, syntax-highlighted) + (deterministic pentester summary from `pentester_summary_template`) + a single Inject button** (Option A; satisfies DEC-017). Server-side enforcement of `hook_target_package_prefix` before Inject can fire. Live trace WebSocket + frontend trace panel (pause / resume / search / export). Trace persisted to `apps/<app_id>/<run_ts>/frida/<session>.jsonl`. | 4.2 + 4.4 |
| 4.6 | **[ ] Scope inspector + hooks/stats panel** | Read-only v1 scope inspector (snapshot of `this`, args, fields via `Java.use`). Active-hooks list with hit counts and top return values. No mutation UI in v1 (modify-return is a v2 follow-up). | 4.5 |
| 4.7 | **[ ] LLM-tier skills (`query_call_graph`, `generate_frida_hook`)** | Both skills registered in the skill catalog and callable from `androscan.py --apk … --task …` analysis pipeline. `generate_frida_hook` ships with `requires_confirmation=True` (per DEC-022) — first real consumer of the consent-class hook once the chat agentic loop lands. **Not yet wired into workbench chat in this sub-step** (that wiring is DEC-022's job). Hook Lab chat dock attachments are wired here: selected method + active hooks + last-N trace events flow into the chat as `code` / `json` attachments. | 4.5 |
| 4.8 | **[ ] Frida overlay on graph + docs sweep** | Cytoscape overlay: live frida hits highlight the corresponding nodes/edges in **bold cyan**; static = muted grey; per-node hit counts shown on hover. Final docs sweep: `STATE.md`, `TASKS.md`, `DESIGN_DOC.md`, `DECISIONS.md` (flip DEC-023 from Proposed → Active), `KNOWN_ISSUES.md`, `SAFETY_AND_SECURITY.md` (per-app `hook_target_package_prefix` + trace-persistence path + frida-server operator-managed scope). New DEC entries for any architectural decisions that emerged mid-implementation. | 4.6 + 4.7 |

**Brief Ask-mode planning checkpoint at the top of 4.1** to settle the SQLite schema (tables, indices, foreign keys) before code lands — otherwise the Agent will improvise a schema and 4.2's query API may force a rework.

**Risks (per DEC-023):**
- JS pre-validation = **`pyjsparser`** (pure-Python, no Node.js dep). Inject button stays disabled until the rendered JS parses; parser errors shown inline.
- Device-touching tests are gated by `pytest -m device` (opt-in marker; not part of the default suite). `pytest -m "not device"` is the CI default.

**Settings tab follow-ups (incremental, P2):**
- Migrate hot-path route handlers (`/api/llm/info`, chat, RAG, decompile) to read from `app.state.config` so live reload covers their fields too — currently they capture `config` at boot, which is why the UI shows a `restart_required` pill for those fields.
- Add a "compare to global" diff view in the per-app settings panel so overrides are visible at a glance.
- Persist the raw-YAML editor's draft locally (sessionStorage) so an accidental tab switch doesn't lose unsaved work.
- **[done 2026-04-27, in Hook Lab 4.3]** Add a "Hook Lab readiness" rollup probe (frida-server present on device, frida CLI on host, target package gadget injectable) once Hook Lab work begins. Two-card design landed in Hook Lab 4.3: existing `tools.frida` (host CLI; from `probe_frida_version`) + new `tools.frida_server` (device-side reachability + host/server version-skew, severity `None` / `"minor"` / `"major"`); see `androscan/web/health_probes.py::probe_frida_server` + `probe_frida_version_skew` and `status_routes.py`. Treated as **yellow** in `rollupGlobal` (non-critical for static-only workflows). "Target package gadget injectable" check is deferred to 4.5 along with the Inject UI it would gate.

**RE Workbench chat — agentic skill loop (P2, planned per DEC-022):**
- Extend `androscan/web/chat.py` with a bounded `while turn < MAX_CHAT_TURNS` loop that calls `parse_response()` + `run_skills()` whenever the LLM emits `skill_requests` (mirroring `androscan/internal/workflow.py`'s pattern). Hard caps in code: `MAX_CHAT_TURNS = 5`, `MAX_SKILLS_PER_TURN = 3`, per-skill timeout = 5 s, per-turn skill-output budget ≈ 6 KB.
- Add `requires_confirmation: bool = False` to `SkillMeta`. Today's read-only LLM-tier skills stay `False`; Hook-Lab-introduced skills (frida hook injection, anything that mutates device or files) ship `True`.
- Extend the SSE vocabulary with `skill_request` / `skill_result` / `skill_pending` event types; render as collapsible cards inside the existing thinking block in `ChatDock`. For `skill_pending` (consent-required skills), the loop awaits `POST /api/chat/skill_decision/{request_id}` (Allow / Deny + optional edited args) with a 90 s TTL on pending state.
- Per-tab "always confirm" toggle in Settings → Per-app overrides (default off); when on, every skill is treated as `requires_confirmation=True` for that tab.
- Transcript schema: interleave `{type: "skill_call", turn, name, args, result_preview, duration_ms, decision?}` records into `apps/<app>/<run>/chat/<tab>.jsonl`.
- Ship behind a per-tab feature flag (`chat.agentic_loop.enabled`) so it can ship dark and enable per tab as confidence grows. Order: Inspect → Reports → Hook Lab. New tests in `tests/test_chat_agentic.py` covering happy path, max-turn cutoff, skill timeout, skill error mid-loop, consent deny, consent TTL expiry, SSE event ordering.
- Quick win to land independently: bump `_INSPECT_RAG_TOP_K` from 4 → 8–10 + raise `_INSPECT_RAG_PER_HIT_CHARS` proportionally. Reduces the failure rate that motivates this DEC but does not replace it.

---

### Phase 6 — Web UI shell with emulator mirror

**Goal:** Serve a React frontend from a FastAPI backend with live emulator screen mirroring and basic project browsing.

1. **Backend skeleton** (`androscan/web/`): FastAPI app with CORS, static file serving, WebSocket endpoints; `GET /api/projects` (list `apps/` folders), `GET /api/projects/{app_id}/runs`, `GET /api/dossier/{app_id}/{run_ts}`, `GET /api/findings/{app_id}/{run_ts}`; config keys `web_host`, `web_port` in `global_config.yaml`.
2. **React frontend scaffold** (`androscan/web/frontend/`): Vite + React + TypeScript; layout — sidebar (projects, dossier components, findings), main area (mirror + detail panels); dark theme, monospace code areas.
3. **Emulator screen mirror:** prefer `scrcpy` piped to WebSocket (MJPEG/h264) or `adb exec-out screencap` polling fallback; `<canvas>` or `<video>` in React; touch forwarding via `adb shell input tap x y`.
4. **Live logcat streaming:** backend `adb logcat` subprocess → WebSocket; frontend scrollable panel with level coloring; filter by tag/level/keyword.
5. **CLI integration:** e.g. `python androscan.py --serve [--port …]` starts server; optional `python androscan.py --apk … --serve` runs analysis then opens UI.
6. **Tests:** unit tests for REST responses (mock data); WebSocket connect/disconnect lifecycle.
7. **Dependencies (target):** Python `fastapi`, `uvicorn[standard]`, `websockets`; frontend `react`, `react-dom`, `vite`, `typescript`.

---

### Phase 7 — Click-to-code mapping

**Goal:** Tap on mirrored screen → identify UI element → trace to Activity/class/method → show decompiled source in Monaco.

1. **UI element identification:** on tap, `uiautomator dump` (or equivalent); parse XML; match coordinates to bounds; extract `resource-id`, `class`, `content-desc`, `text`, `package`, `bounds`.
2. **Activity resolution:** `dumpsys activity top` (or equivalent) for foreground Activity.
3. **Resource ID → source:** parse `R.java` / `R$id.java` from jadx output; search for `findViewById(R.id.…)` / View Binding; resolve class + method.
4. **Monaco Editor:** `@monaco-editor/react` in detail panel; Java/Kotlin highlighting; read-only default; jump to line for resource reference.
5. **API:** `POST /api/tap` (x, y) → element + source mapping; `GET /api/source/{class_name}` (delegate to existing jadx-backed decompilation path).
6. **LLM-assisted mapping (stretch):** when deterministic trace fails, optional LLM hint with reasoning shown alongside deterministic result.
7. **Tests:** unit — XML parsing, coordinate matching, R.java parsing; integration — tap → element → source with fixtures. **[done]** `tests/test_inspect_map.py` covers element pick + handler grep + adb glue; `tests/test_web.py::test_api_inspect_map_returns_element_and_candidates` covers the end-to-end `/api/inspect/map` response (including the new `resolution` block); `tests/test_resolve_ui_element.py` covers the fuser scorer + RAG enrichment + skill registry path.
8. **[done]** New skill `resolve_ui_element` (**llm** tier): takes element + foreground activity + raw handler candidates; scores them deterministically (kind base + foreground-activity bonus + activity-named-file bonus + early-line decay) and optionally enriches with Lane-1 RAG hits; returns `{best, alternatives, rag_hits, reasoning}`. Pure-function `resolve()` is also called inline by `/api/inspect/map`.

---

### Phase 8 — Static call graph from Smali

**Goal:** Build navigable call graph from decoded Smali; visualize with Cytoscape.js; support LLM-assisted path questions.

1. **Smali analysis** (`androscan/analysis/call_graph.py` or equivalent): walk apktool output; extract classes, methods, `invoke-*` targets; adjacency caller.method → callees; `extends` / `implements`.
2. **Graph storage:** `apps/<app_id>/call_graph.json` (nodes + edges); rebuild when APK hash changes; node metadata (exported flag from dossier where applicable).
3. **Graph query API:** `GET /api/graph/{app_id}` (paginated/filtered); `GET /api/graph/{app_id}/neighbors/{class.method}`; `GET /api/graph/{app_id}/paths?from=…&to=…`; `POST /api/graph/{app_id}/query` (LLM-assisted natural-language path questions).
4. **Cytoscape.js:** zoom/pan/search/filter; click node → Monaco source; highlight paths; layouts (e.g. dagre, cose-bilkent).
5. **New skill (target):** `query_call_graph` (**llm** tier): question + graph subset → relevant paths; usable from analysis workflow.
6. **Tests:** unit — fixture Smali, graph build, path algorithms; integration — APK → graph → query (where feasible without device).

---

### Phase 9 — Frida integration (live tracing & hooking)

**Goal:** Dynamic instrumentation from the UI — hook methods, trace calls, optional return tampering; LLM-assisted hook generation.

1. **Frida adapter** (`androscan/adapters/frida_client.py` or equivalent): detect/push frida-server (configurable); Python `frida` attach/load/detach; availability checks like other external tools.
2. **Hook templates** (`androscan/adapters/frida_hooks/`): parameterized JS — method entry/exit log, SSL pinning bypass patterns, crypto/SharedPreferences/Intent hooks; render from class/method/signature from graph or dossier.
3. **Live trace streaming:** Frida `message` → WebSocket → UI trace panel (pause/resume, search, export).
4. **Interactive hook builder:** pick method from graph or editor → hook type (log args, log return, modify return, custom JS); optional LLM “generate hook for objective”; **user confirm before deploy**.
5. **New skill (target):** `generate_frida_hook` (**llm** tier): class, method, objective → Frida JS; basic syntax validation before deploy.
6. **Exploit verification integration (optional):** extend signal matrix (`vuln_module_skills_signals.json`) with e.g. `frida_trace` where product requires deeper runtime evidence.
7. **Tests:** unit — template rendering, JS generation, adapter with mocks; device integration opt-in only.
8. **Dependencies (target):** `frida`, `frida-tools`; frida-server on device (operator-managed).

**Cross-cutting:** artifacts under `apps/<app_id>/` (graphs, traces, hooks); new skills follow three-tier model; web server **bind 127.0.0.1** by default (local single-user); `global_config.yaml` sections `web:` and `frida:` (when implemented).

---

## Priority Queue

### P1
- Phase 4 CI: pytest on every push/PR (remains parked).
- Implement `content_provider_query` and `app_data_snapshot` signal captures for `exported_provider` profile (currently stub).
- Add second vulnerability module.

### P2
- Integration test with fixture APK.
- JSON output renderer.
- Richer evidence provenance tracking.
- **Interactive RE Workbench — Phase 7+** (Phase 6 shell landed; continue with click-to-code, graph, Frida per § below).

---

## Blocked Tasks

### Phase 4: CI setup
- blocked by: prioritization decision.
- why blocked: Error handling and docs cleanup done; CI (pytest on push/PR) remains parked.
- unblock condition: Decision to prioritize CI setup.

Format:

### [Task title]
- blocked by:
- why blocked:
- unblock condition:

Example:

### Add PDF report renderer
- blocked by: normalized report output contract
- why blocked: report model not stable enough
- unblock condition: shared rendering schema finalized

---

## Backlog / Future Work

Use for real future work, not vague ideas.

- **Integration test with fixture APK** (extraction + dossier shape) — parked; optional for later.
- add second vulnerability module
- add JSON output renderer
- add richer evidence provenance tracking
- add adapter for additional mobile tooling
- **Interactive RE Workbench (Phases 6–9)** — see § Interactive RE Workbench in this file (replaces vague “web UI shell” backlog item)
- **Standalone "search code" UI for Lane-1 RAG** — backend (`POST /api/rag/{app_id}/query`) and the `SearchHit` shape are already shipped; today RAG is only consumed implicitly via Inspect-tab chat enrichment, the `/api/inspect/map` fuser, and the `search_decompiled_sources` LLM skill. A small operator-facing search affordance (e.g. a sub-tab inside Inspect, or a "Code search" panel inside the Code Browser) with click-to-jump into `CodeView` would make the index directly useful during manual RE. Pull this forward only if operators ask for it; nothing else depends on it.
- add queue-backed job execution
- add configurable policy layer

---

## Completed Tasks

### 2026-04 Phase 6 polish (UX step 3.5): Settings tab — global + per-app + status + diagnostics
- outcome: New top-level `Settings` tab (`#/settings`) covering global config, per-app overrides, live status, and diagnostics. Backend: `androscan/web/health_probes.py` (timeboxed pure-function async probes for adb/jadx/apktool/frida/Ollama/embed-provider/disk + per-app pm-path/foreground/UID/uiautomator/apk-sha-drift/RAG/decompile-cache; never raise; `{ok, label, ...}` shape); `androscan/web/per_app_settings.py` (atomic schema-versioned writes to `apps/<app_id>/app_settings.json`, `effective_settings(global_view, per_app)` merger, `apk_overrides_summary`); `androscan/web/status_routes.py` (`GET /api/status/global`, `GET /api/status/apps/{app_id}` — `asyncio.gather` + 3 s in-process cache; `invalidate_status_cache()` on every settings save/reset/reload); `androscan/web/settings_routes.py` (`GET/PUT /api/settings/global`, `POST /api/settings/global/raw`, `POST /api/settings/global/reset`, per-app GET/PUT/reset, `POST /api/settings/reload`; validates raw YAML server-side; atomic write-back via tempfile + `os.replace`; returns `restart_required` per field). `androscan/config/loader.py` grew `CONFIG_FIELD_MAP` (Config field → `(yaml_section, yaml_key, env_var)`), `LIVE_RELOADABLE_FIELDS`, `global_view_from_config`, `effective_sources` (returns `yaml`/`env`/`default` per field for source pills), `with_overrides`, `coerce_yaml_value`, `dump_to_yaml`, `save_raw_yaml`, `restore_defaults_yaml`, `validate_raw_yaml`, `read_raw_yaml`, `discover_config_path`. `app.py` refactored to keep the live `Config` on `app.state.config` (`_current_config`/`_set_config` helpers) so live-reloadable fields take effect without uvicorn restart; new routers read via callable providers. Frontend: `SettingsTab` + 4 sub-panels (`GlobalSettingsPanel` with form + raw-YAML editor + dirty state + reset; `AppSettingsPanel` with dropdown + override-or-inherit + force-with-warning reset; `StatusPanel` with cards + per-card `HealthDot` + "Refresh now"; `DiagnosticsPanel` with raw API JSON + `/api/settings/reload`). New `HealthDot` in the global header polls `/api/status/global` every 30 s and rolls up to a single green/yellow/red dot that deep-links to Settings on click. Two new API clients (`api/settings.ts`, `api/status.ts`) and ~300 lines of new CSS in `App.css` (`.source-badge`, `.env-lock`, `.restart-pill`, `.status-card`, `.health-dot.{green,yellow,red}`, `.settings-nav`, `.yaml-editor`). Tests: `tests/test_health_probes.py` (subprocess + urllib mocks; 20 cases), `tests/test_per_app_settings.py` (load/save/reset/merge/atomic), `tests/test_settings_routes.py` (integration). All Python tests green; `tsc --noEmit` + `vite build` clean.
- notes: One bug discovered during testing — the integration test was monkeypatching `androscan.web.health_probes.probe_adb_version`, but `androscan.web.status_routes` imports `probe_adb_version` *by name*, so the patch never reached the consumer. Fix: monkeypatch the **consumer** module (`androscan.web.status_routes`). Documented inline in `test_settings_routes.py` and in DEC-021. The "force-with-warning" per-app reset semantics + the `restart_required` pill for non-live-reloadable fields were both explicitly user-confirmed during design.
- follow-up: (P2) Migrate hot-path handlers (`/api/llm/info`, chat, RAG, decompile) to read from `app.state.config` so live reload covers their fields. (P2) "Compare to global" diff view in the per-app panel. (P2) Persist raw-YAML draft to sessionStorage. (when Hook Lab starts) Add a "Hook Lab readiness" rollup probe.

### 2026-04 Phase 6 polish (UX step 3, partial): Lane-1 RAG indexer
- outcome: New `androscan/rag/` package (`chunking.py`, `embed.py`, `index.py`, `search.py`) implementing a per-app SQLite vector index of method-level chunks over the jadx decompiled sources. Brace-balanced chunker for `.java`/`.kt` (no tree-sitter). `EmbedProvider` protocol with `FastEmbedProvider` (default ONNX local), `OllamaEmbedProvider` (HTTP), and `HashProvider` (deterministic, test/no-deps fallback). Per-app store at `apps/<app_id>/.decompiled/<sha>/rag.sqlite` (WAL, packed `float32` blobs); brute-force cosine top-k (numpy when present, pure-Python fallback). Endpoints `GET /api/rag/{app_id}/status`, `POST /api/rag/{app_id}/rebuild`, `POST /api/rag/{app_id}/query` in `androscan/web/rag_routes.py`; jadx completion auto-schedules a daemon-thread RAG build via `schedule_rag_build_after_decompile`. New `llm`-tier skill `search_decompiled_sources` (query, top_k, package_prefix, file_substr) — advertised in prompt catalog, fail-open. Inspect-tab chat (`androscan/web/chat.py::_enrich_inspect_with_rag`) appends top-k chunks as `code` attachments before guardrails run; fail-soft when RAG is unavailable. `Config` extended with `rag_embed_provider` / `rag_embed_model` / `rag_top_k_default`; `global_config.yaml` adds `rag:` section; env overrides `ANDROSCAN_RAG_*`. New `pyproject` extra `[rag]` (fastembed, numpy). New tests: `tests/test_rag.py`, `tests/test_rag_integration.py`, plus three RAG endpoint tests in `tests/test_web.py` (197 tests total, all passing).
- notes: Click-to-code backend was already in place via `POST /api/inspect/map` + `androscan/web/inspect_map.py` (foreground activity + uiautomator dump + handler grep) and persistent decompile cache (`/api/decompile`, `/api/code/{tree,file}`); package-scoped logcat (with stable Linux-UID filtering) and mirror online/offline (`/api/device/status`) were also already implemented. The remaining backend gap was the `resolve_ui_element` LLM skill — see the next entry. Multimodal chat attachments (PDF/CSV/screenshot) are deferred to after Hook Lab. `sqlite-vec` is reserved as a future drop-in replacement for the brute-force scan.
- follow-up: Done in two follow-up entries below — `resolve_ui_element` LLM skill + `/api/inspect/map` fusion (backend), and the Inspect-tab frontend wiring entry. Next is UX step 4 (Hook Lab — Smali graph + Frida).

### 2026-04 Phase 6 polish (UX step 3, partial): `resolve_ui_element` LLM skill + `/api/inspect/map` fusion
- outcome: New `androscan/skills/resolve_ui_element.py` — `llm`-tier deterministic fuser that takes the element + foreground activity + raw handler candidates from `inspect_map.find_handlers` and produces a single `best` answer with reasoning, plus a ranked `alternatives` list. Score model: handler-kind base (`findViewById` 1.00 > `onClick_near` 0.80 > `compose_id` 0.60 > `reference` 0.20 > rag-cosine), additive bonuses for foreground-activity match (+0.50), activity-named files (+0.10), and an early-line decay bonus (≤+0.10 for line 1, → 0 at line 200). Optional Lane-1 RAG enrichment: synthesises a free-text query from `text` > `content_desc` > short resource id, fetches top-k chunks, and feeds them through the same scorer (so a clean `findViewById` in the foreground activity always beats an arbitrary semantic match). Pure-function `resolve()` is reused inline by `POST /api/inspect/map` (`androscan/web/app.py`) which now returns a `resolution` block (`{best, alternatives, rag_hits, reasoning}`) so the UI gets one ranked answer without an LLM round-trip. Skill is fail-soft on missing app dir / decompile cache / embed provider. Registered in `androscan/skills/__init__.py`. New tests: `tests/test_resolve_ui_element.py` (13 tests — pure helpers, deterministic scoring, foreground boost, early-line tie-break, RAG enrichment with `HashProvider`, fail-soft, registry path); `tests/test_web.py::test_api_inspect_map_returns_element_and_candidates` extended to assert the new `resolution` block. **210 tests total, all passing.**
- notes: This closes the *backend* of Phase 6 step 3. The frontend wiring landed in the next entry below.
- follow-up: Inspect-tab frontend wiring (done — see next entry); then UX step 4 (Hook Lab — Smali graph + Frida).

### 2026-04 Phase 6 polish (UX step 3, final): Inspect-tab frontend wiring for the fuser
- outcome: Surfaced the `resolution` block from `POST /api/inspect/map` end-to-end in the Inspect tab UI. Extended `androscan/web/frontend/src/api/inspect.ts` with `Resolution`, `ResolutionCandidate`, `ResolutionRagHit` types. New sub-components in `ElementMappingPanel.tsx`: `BestBanner` (regex/RAG `SourceBadge`, kind pill, score, reasons grid, snippet, "Open in Code browser"), `ResolutionAlternatives` (collapsible details with per-alt source badge + score + open button), `RagHitsList` (collapsible details with synthesised query and per-hit class.method label). `InspectTab.handleTap` now prefers `resolution.best` over `candidates[0]` when auto-opening the Code Browser, seeds `scrollTarget` + `highlightRange` (via `requestAnimationFrame` so re-clicks still fire), and propagates `best.method_name` so the method-emphasis label is correct. Chat attachments memo now appends a `best_handler` JSON block (file, line, class, method, source, score, reasons) so the LLM sees the fused pick alongside the element + candidate list. New CSS: `.source-badge` (deterministic = green, rag = warn), `.best-banner` (accent border + soft background, reasons grid), `.resolution-alts` / `.rag-hits` collapsibles. Vite production build clean (308 modules, 32 KB CSS, 393 KB JS); `tsc --noEmit` clean; 210 Python tests still passing.
- notes: The rest of the Inspect-tab frontend (4-column resizable layout, mirror with status dot, click-to-map, scoped logcat, adb shell, Code-browser tab with `CodeView`) was already in place before this entry — only the fuser-output visualisation was missing. With this, Phase 6 step 3 is functionally complete (chat enrichment, RAG indexer, click-to-code backend, fuser skill, and full UI surface).
- follow-up: UX step 4 (Hook Lab — Smali graph + Frida overlay + scope/hooks).

### 2026-04 Phase 6 (initial): RE Workbench web shell
- outcome: `androscan/web/` FastAPI app with CORS; REST `/api/health`, `/api/projects`, `/api/projects/{app_id}/runs`, `/api/dossier/{app_id}/{run_ts}`, `/api/findings/{app_id}/{run_ts}`, `POST /api/input/tap`; WebSockets `/ws/mirror` (adb screencap PNG polling), `/ws/logcat`; config `web_host`, `web_port`, `web_screencap_interval_ms` + env overrides; CLI `--serve`, `--web-port`; optional `--apk … --serve` after run. Vite + React + TypeScript UI under `androscan/web/frontend/` (build → `static/`, gitignored). Tests in `tests/test_web.py`. Dependencies: fastapi, uvicorn, websockets; dev httpx for TestClient.
- notes: scrcpy path and logcat filter UI deferred; `GET /` returns JSON how-to when `static/` not built.
- follow-up: Phase 6 polish; Phase 7 click-to-code.

### 2026-03-30 Phase 4 partial: Error handling + docs cleanup
- outcome: Broader error handling across config, LLM client, ADB skills, workflow, and CLI. Config: explicit --config fails on missing/invalid file; type coercion with clear errors. LLM: resp.json() wrapped; empty content retried; non-404 HTTP errors show body detail; is_ollama_available returns diagnostic detail. ADB: TimeoutExpired caught in all subprocess calls (app_env_check, run_exploit_command, capture_signals). Workflow: generate_report result checked; disk errors on meta/observations caught; LLM parse failures logged. CLI: top-level exception handler; report read warnings. Misc: skills registry import errors logged; vuln_signals_config corrupted JSON caught; cache store disk errors caught; generate_report disk errors handled. Docs: DESIGN_DOC, ARCHITECTURE, DECISIONS, PROJECT_BRIEF, README, KNOWN_ISSUES, AI_ENGINEERING_CONSTITUTION updated for Phase 5, three-tier skills, and stale references fixed.
- notes: CI (pytest on push/PR) remains parked.
- follow-up: CI setup; provider signal stubs; second vulnerability module.

### 2026-03-30 Phase 5: Exploit verification
- outcome: Exploit verification on emulator + ADB. 5 exploit-tier skills (app_env_check, build_exploit_command, capture_signals, run_exploit_command, verify_exploit_result). Template catalog for 5 component profiles (exported_activity, exported_service, exported_receiver, exported_provider, deep_link). Vuln–skill–signal profile JSON. Before/after signal capture (volatile then non-volatile). LLM-based verification. Report generated after verification with verified flag, reasoning, and artifact refs. Artifacts per hypothesis under exploit_verification/<module>/<hyp_id>/. All 10 sub-tasks complete.
- notes: `exported_provider` has 2 stub signal types (content_provider_query, app_data_snapshot); logcat and exploit command are real. RAG for exploit templates deferred to backlog.
- follow-up: Unpark Phase 4 (CI, hardening) or pick from backlog (provider stubs, second vulnerability module).

### Phase 3: First vertical slice (exported components)
- outcome: Real extraction (apktool), real Ollama client, real prompts and skills catalog, evidence_ref validation, run artifacts (report.json, run_meta.json, run.log, observations.json), skill results cache (get_decompiled_class keyed by resolved class name). get_decompiled_class and get_decompiled_method real via jadx. 54 tests; mock LLM in CI.
- notes: All five sub-tasks done. Integration test with fixture APK parked in backlog.
- follow-up: Phase 4 (harden and extend, CI).

### 2026-03-13 Phase 2: Build skeleton
- outcome: Repo layout, pyproject.toml, CLI (androscan.py with --apk, --task multi-valued, --output), config, dossier model, extraction stub, LLM stub (client, prompt builder, parser), run folder creation, stub skills, workflow with multi-turn loop, report.json writing. 14 tests (import, config, extraction, LLM, CLI parsing, workflow integration).
- notes: Sub-tasks 1–7 (project setup, config, dossier+extraction stub, LLM stub, workflow+run folder, CLI wiring, minimal tests) completed. All stubs; Phase 3 will add real extraction and real LLM.
- follow-up: Phase 3 (first vertical slice).

### 2026-03-12 Phase 1: Finalize architecture
- outcome: DESIGN_DOC.md is the single source of truth for MVP (architecture, repo structure, dossier schema, LLM I/O schema, prompts/skills, roadmap, risks, first vertical slice).
- notes: Phase 2 (skeleton) completed 2026-03-13.
- follow-up: Phase 3 (first vertical slice).

### 2026-03-12 Added agent protocol and starter docs
- outcome: onboarding and task-loading docs added
- notes: architecture and design docs still pending
- follow-up: create first active engineering task

Keep this section concise.

---

## Task writing rules

Each task should be:
- specific
- scoped
- reviewable
- testable
- bounded in time and complexity

Avoid tasks like:
- “improve architecture”
- “make app better”
- “add security”
- “clean everything up”

Prefer tasks like:
- “add normalized finding model for first feature module”
- “implement adapter wrapper for tool X behind interface Y”
- “add negative tests for malformed artifact input in module Z”

---

## Phase and task list rules

**So other IDEs and agents know where to take over:**

1. **List sub-tasks explicitly.** For every phase or multi-step task, the implementation plan in this file MUST list sub-tasks explicitly (numbered or bulleted), not only in prose. Each sub-task should be verifiable on its own.

2. **Update status as soon as a sub-task is completed.** When a sub-task is finished (code done, tests pass, behaviour verified), update this file in the same work session:
   - Mark the sub-task done (e.g. add `[x] Done` or `— [x] Done` next to the heading, or update a status table).
   - Do not leave a phase "in progress" without reflecting which sub-tasks are already done.

3. **Keep a short status overview.** For the active phase, keep a compact status table or checklist (e.g. "Sub-task status") at the top of the implementation plan so the next agent can see at a glance what is done and what is next.

4. **External tool availability.** When adding or changing code that uses an external tool (apktool, jadx, etc.), the implementation must check tool availability (e.g. `shutil.which(cmd)`) and handle missing tools without crashing (return a clear result; do not raise raw subprocess/OS errors). See `docs/CONVENTIONS.md` §4 External tool availability.

---

## Default execution rule

Unless explicitly instructed otherwise:

- do one task at a time
- complete it properly
- add tests
- update docs
- then move to the next task