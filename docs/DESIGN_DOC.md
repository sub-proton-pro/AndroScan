# Design Document — MVP: LLM-Native Android Pentesting Tool

This document describes the intended product and system design for the AndroScan MVP.

Its purpose is to capture:

- the target shape of the product and MVP architecture
- repository structure and module boundaries
- component dossier and LLM input/output schemas
- prompt design and skills-based analysis flow
- implementation roadmap (Phases 1–5 complete for MVP core; Phases 6–9 planned for Interactive RE Workbench)
- risks and mitigations
- the first end-to-end vertical slice

This document describes intended design, not guaranteed current implementation.

For what exists today, see `docs/STATE.md`.

---

## 1. Document purpose

This design document is the single source of truth for Phase 1 (architecture finalization). It gives contributors and AI agents a complete mental model of the MVP so that:

- Phase 2 (skeleton) and Phase 3 (first vertical slice) can be implemented without ambiguity
- Schemas, folder layout, and workflows are defined in one place
- LLM centrality, grounding, and multi-turn behavior are explicit

---

## 2. Product vision (MVP)

AndroScan is an **LLM-native** Android (and later iOS) security-analysis tool for pentesters and security testers.

- **Primary user:** Pentester
- **Primary input:** APK only (no source code, no Android Studio)
- **Local-first:** All processing and LLM runs locally
- **Local LLM via Ollama is mandatory** and is the core USP — this is not a conventional scanner with optional AI

**Flagship feature:** Use the local LLM to deeply analyze exported Android attack surfaces and determine **actual exploitability**, not just exposure.

**Focus areas:** Exported Activities, Services, Broadcast Receivers, Content Providers, Deep Links.

**Principles:**

- LLM is central to the product
- Deterministic extraction (and optional inference) is the grounding layer
- Do not feed raw APK artifacts directly to the model
- Do not produce MobSF-style noise: fewer, higher-confidence, evidence-backed findings
- CLI-first MVP

---

## 3. MVP architecture

### 3.1 High-level flow

```
APK path (CLI)
  → [Ingestion] validate path, resolve to file
  → [Extraction] deterministic unpack + parse (manifest, components); optional inference/enrichment
  → [Dossier] build structured component dossier (+ decompiled snippets when needed)
  → [LLM] multi-turn: dossier + global context (skills) → LLM → optional skill_requests → run skills → re-prompt
  → [Reasoning] LLM returns exploit hypotheses (exploitability/confidence 1–5, evidence_refs)
  → [Exploit verification] emulator + ADB; orchestration runs exploit-tier skills per hypothesis (not advertised in the LLM catalog)
  → [Report] normalize to shared finding model → write under apps/<app_id>/<run_ts>/
```

### 3.2 Grounding

- **Structured dossier** is the spine: it defines what is analyzed (components, deep links, permissions).
- **Decompiled snippets or other artifacts** may be included when the LLM or workflow needs them (e.g. via skills).
- **Never** send raw APK bytes or unbounded dumps; we control what is sent (selection, size limits).

### 3.3 Workflows and modules

- **Orchestration is module-aware.** The tool supports multiple capabilities (tasks); each **vulnerability module** defines its own workflow.
- **First module:** “Unprotected exported components analysis” (e.g. `exported_components`).
- Multiple tasks can run together in one run (e.g. `--task exported_components --task <future>`).
- New modules live under `androscan/modules/<module_name>/` and plug into the same contracts.

### 3.4 Layers (aligned with CONVENTIONS and ARCHITECTURE)

| Layer | MVP responsibility |
|-------|---------------------|
| **Presentation** | CLI entrypoint (`androscan.py`), args (`--apk`, `--task` multi-valued, `--output`), render report to stdout and/or write under run folder. No business/LLM logic. **Planned (Phase 6+):** optional local web UI (FastAPI + React) for mirror/logcat/browse — see roadmap § Phase 6–9. |
| **Orchestration** | Select and run one or more task workflows; create run folder; hand off to module workflows; merge/normalize results. |
| **Internal (application/domain)** | Dossier schema, finding/exploit-hypothesis model, normalization from LLM output, severity/confidence rules, report generation code. |
| **LLM** | Ollama-only adapter, prompt construction (dossier + global context + skills), response parsing/validation, multi-turn loop, retries/timeouts. |
| **Extraction** | APK unpack, manifest parse, exported components + intent filters + permissions; optional decompilation for skills. Deterministic parsing + optional inference; reproducibility via versioning/caching where needed. |
| **Modules** | Per-module workflow (extraction → dossier/snippets → LLM → findings). First module: exported_components. |
| **Infrastructure** | Config (Ollama URL, timeouts), logging. No persistence beyond run artifacts in `apps/`. |

**Language:** Python.

---

## 4. Repository structure

```
AndroScan/
  androscan.py                 # Single CLI entrypoint (Option B)
  androscan/
    __init__.py
    config/                    # Config loading (directory)
    internal/
      __init__.py
      # Orchestration, domain, finding model
      report/                  # Report generation (writes to apps/<app_id>/<run_ts>/)
    skills/                    # First-class skills layer: pipeline + llm + exploit tiers
      base.py                  # SkillMeta, SkillContext, SkillResult
      extract_manifest.py      # pipeline
      prepare_dossier.py        # pipeline
      generate_report.py        # pipeline
      get_decompiled_class.py   # llm
      get_decompiled_method.py  # llm
      list_classes_in_package.py # llm
      app_env_check.py         # exploit
      build_exploit_command.py # exploit
      capture_signals.py       # exploit
      run_exploit_command.py   # exploit
      verify_exploit_result.py # exploit
    extraction/                # APK unpack, manifest (delegates to skills)
    llm/                       # Ollama, prompts, schema, multi-turn
    modules/                   # Vulnerability check modules
      exported_components/      # First module
        ...
      # Future: cert_pinning/, secure_storage/, ...
  apps/                         # Runtime output only; not in version control
    <app_id>/                   # app_id = sanitized package name; truncate if too long
      <run_timestamp>/           # Human-readable, e.g. 13-mar-26_01-30-52
        report.json             # Validated hypotheses + summary
        run_meta.json            # Run metadata: apk_path, app_id, run_timestamp, started_at, finished_at, hypotheses_count
        run.log                  # [task], [ERROR], [WARNING], [INFORMATIONAL], [retry], [thinking]; INFORMATIONAL = skills requested, skills executed, data sent to LLM after skills
      observations.json         # At app_id level: persistent store for LLM/tool observations across runs (schema: { "observations": [ { "run_ts?", "source", "text" } ] })
  docs/
  tests/
  global_config.yaml           # Optional runtime config (YAML); overridden by env. Use --config to pass path.
```

- **Configuration:** App constants in `androscan/constants.py`. Runtime settings from `global_config.yaml` (optional; merge: defaults → YAML → env). CLI `--config <file>` to pass config path. See `docs/DECISIONS.md` DEC-012.
- **app_id:** Default = sanitized package name (e.g. `com_example_myapp`). Replace `.` with `_`; truncate if too long (e.g. max 80–128 chars).
- **Run folder name:** Human-readable timestamp, e.g. `DD-mon-YY_HH-MM-SS` (e.g. `13-mar-26_01-30-52`).
- **Report and run artifacts:** Per-run artifacts under `apps/<app_id>/<run_timestamp>/` (report.json, run_meta.json, run.log). Persistent observations at `apps/<app_id>/observations.json`.

---

## 5. Component dossier schema

Structured description of **exported** attack surface and declared permissions. All fields from deterministic extraction (and optional enrichment); no free-form text from raw APK.

```json
{
  "apk_info": {
    "package": "com.example.app",
    "version_name": "1.0",
    "version_code": 1,
    "min_sdk": 21,
    "target_sdk": 30
  },
  "permissions": [
    "android.permission.INTERNET",
    "android.permission.READ_EXTERNAL_STORAGE"
  ],
  "exported_activities": [
    {
      "name": "com.example.app.MainActivity",
      "exported": true,
      "intent_filters": [
        {
          "action": ["android.intent.action.MAIN"],
          "category": ["android.intent.category.LAUNCHER"]
        },
        {
          "action": ["android.intent.action.VIEW"],
          "category": ["android.intent.category.DEFAULT", "android.intent.category.BROWSABLE"],
          "data": [
            { "scheme": "https", "host": "example.com", "pathPrefix": "/open" }
          ]
        }
      ]
    }
  ],
  "exported_services": [
    {
      "name": "com.example.app.BackgroundService",
      "exported": true,
      "intent_filters": []
    }
  ],
  "exported_receivers": [
    {
      "name": "com.example.app.BootReceiver",
      "exported": true,
      "intent_filters": [
        { "action": ["android.intent.action.BOOT_COMPLETED"] }
      ]
    }
  ],
  "exported_providers": [
    {
      "name": "com.example.app.FileProvider",
      "exported": true,
      "authority": "com.example.app.files",
      "read_permission": null,
      "write_permission": null,
      "grant_uri_permissions": true
    }
  ],
  "deep_links": [
    {
      "component": "com.example.app.MainActivity",
      "scheme": "https",
      "host": "example.com",
      "path_prefix": "/open",
      "intent_filter_index": 0
    }
  ]
}
```

- **exported_receivers** = broadcast receivers.
- **permissions** = list of permission name strings (MVP).

---

## 6. LLM input/output schema

### 6.1 Input to LLM

- **Structured dossier** (JSON) as in Section 5.
- **Global context:** Role, task, and **catalog of available skills** (name, description, parameters, when to use). The LLM is told it can request skills to gather more evidence.
- **Optional:** Results of previously requested skills (decompiled snippets, etc.) appended to context for subsequent turns.

### 6.2 Output schema (enforced and parsed)

Response is JSON with two optional top-level keys:

**1. skill_requests (optional)**

- Array of `{ "skill": "<name>", "params": { ... } }`.
- If present, the tool runs these skills, appends results to context, and re-prompts the LLM (multi-turn). No single-call assumption.

**2. hypotheses (optional)**

- Array of exploitability hypotheses. When the LLM has enough evidence, it omits `skill_requests` and returns `hypotheses` only.

**Hypothesis object:**

| Field | Type | Description |
|-------|------|-------------|
| id | string | Short id (e.g. H1, H2) |
| component_type | string | activity \| service \| receiver \| provider |
| component_name | string | Fully qualified name from dossier |
| title | string | Short title |
| description | string | Explanation |
| evidence_refs | array of string | Dossier paths (e.g. `exported_providers[0]`, `deep_links[0]`) — must be valid paths in the dossier |
| exploitability | integer | 1–5 (1=Informational, 5=Critical; see ISSUE_SEVERITY_LABELS) |
| confidence | integer | 1–5 (1=low, 5=very high) |
| remediation_hint | string | Brief remediation guidance |

- **exploitability** and **confidence** are integers 1–5 (not high/medium/low) for finer granularity and ordering.
- Validation: parse JSON; validate each `evidence_ref` against the dossier; drop or flag hypotheses with invalid refs. Cap total hypotheses (e.g. top 10) in the prompt.

**Optional top-level summary:**

- `summary`: string — one paragraph overall risk and main attack vectors.

---

## 7. Prompt design and skills

Skills use a three-tier model: **pipeline** (orchestration only: extract_manifest, prepare_dossier, generate_report), **llm** (advertised in the prompt: get_decompiled_class, get_decompiled_method, list_classes_in_package), and **exploit** (orchestration during Phase 5: app_env_check, build_exploit_command, capture_signals, run_exploit_command, verify_exploit_result). **Exploit-tier skills are not included in the LLM prompt catalog**; only `list_llm_skills()` feeds the catalog.

### 7.1 Global context (provided every turn)

- **Role:** Senior Android security assessor; produce exploitability hypotheses with evidence_refs; prefer fewer, high-confidence findings.
- **Available skills:** From the skills layer (`list_llm_skills()`). For each: name, description, parameters. Example skills:
  - **get_decompiled_class:** Decompiled Java/Kotlin for the class named in the dossier component. Params: `component_ref` (e.g. `exported_activities[0]`).
  - **get_decompiled_method:** Body of a specific method. Params: `class_name`, `method_name`.
  - **list_classes_in_package:** Class names under a package. Params: `package_prefix`.
- **How to request skills:** “Include in your response: `skill_requests`: [{ \"skill\": \"<name>\", \"params\": {...} }]. The tool will run them and re-prompt you with the results. When you have enough evidence, omit skill_requests and return hypotheses only.”

### 7.2 Multi-turn flow

- Turn 1: Send dossier + global context. LLM may return `skill_requests` and/or `hypotheses`.
- If `skill_requests` present: run skills, append results to context, re-prompt (Turn 2, …). Optionally allow user-in-the-loop in future (with or without user interference).
- Stop when LLM returns `hypotheses` (and no `skill_requests`) or when max turns (e.g. 3) is reached.
- Do not limit the design to a single LLM call; back-and-forth is expected for better hypotheses.

### 7.3 User prompt (per turn)

- “Here is the dossier [and optional prior skill results]. Produce hypotheses with evidence_refs, or request skills if you need more data. Output valid JSON only; exploitability and confidence are integers 1–5.”

---

## 8. Implementation roadmap

### Phase 1 — Finalize architecture

- **Deliverable:** This document (`docs/DESIGN_DOC.md`) is the single source of truth.
- **Content:** Architecture, repo structure, dossier schema, LLM I/O schema, prompt/skills design, app_id and run folder rules, roadmap, risks, first vertical slice.
- **Outcome:** No code yet; all contracts and structure documented.

### Phase 2 — Skeleton

- Create repo layout (directories, stub modules).
- CLI: `androscan.py` with `--apk`, `--task` (multi-valued), `--output`; create `apps/<app_id>/<run_ts>/`.
- Extraction: stub that returns minimal/hardcoded dossier from a path.
- LLM: Ollama client stub; prompt builder stub; response parser stub (expects JSON with skill_requests/hypotheses).
- Workflow: one orchestration path that runs extraction → dossier → prompt → LLM → parse; support multi-turn loop (stub skills).
- Outcome: End-to-end path from CLI to “fake” dossier and stub/mock LLM response; clear places to plug in real extraction and prompts.

### Phase 3 — First vertical slice

**Goal:** Replace stubs with real extraction and real LLM; one real APK produces a dossier and report with evidence-backed hypotheses.

**Implementation order (sub-steps):**

1. **Real extraction** — Add real APK/manifest parsing using **apktool** (decode APK, parse decoded AndroidManifest.xml); build dossier from manifest (exported activities, services, receivers, providers, permissions, deep links). Replace extraction stub. Add integration test with fixture APK (dossier shape, at least one component or permission).

2. **Real Ollama client** — Implement HTTP client calling Ollama API (config.ollama_base_url); keep `complete()` interface. Tests use mock so CI does not require live Ollama.

3. **Real prompts and skills catalog** — Implement prompt templates per DESIGN_DOC (global context, skills catalog, per-turn user prompt). Optionally implement one real skill (e.g. get_decompiled_class via **jadx**) or keep stub; multi-turn loop consumes skill results.

4. **evidence_ref validation** — Validate each hypothesis’s evidence_refs against dossier paths; drop or flag invalid refs before writing report.

5. **Run artifacts** — Write report.json with validated hypotheses; add optionally observations.json, run_meta.json, run.log under run folder.

**Acceptance:** One real APK → dossier → multi-turn LLM → report with 1–5 hypotheses and valid evidence_refs; all tests pass with mock LLM in CI.

### Phase 4 — Harden and extend

- Validate evidence_refs against dossier; error handling; config (Ollama URL, timeouts).
- **Tests:** Unit tests (dossier build, extraction parsing, LLM output parsing); integration test (extraction → dossier → mock LLM → report). **Who runs tests:** Developers run locally (e.g. `pytest`); CI runs the same suite on every push/PR. **How:** Pytest from repo root; no live Ollama in CI (mocks/fixtures only).
- Docs: Update `STATE.md`, `TASKS.md`, schema docs as needed.

### Phase 5 — Exploit verification

- **Workflow order:** Analysis → Hypotheses → **Exploit verification** → Report generation. Report is produced only after verification so it can include verified/unverified status and artifact refs.
- **Exploit verification step:** Use emulator + ADB: device selection (adb devices -l; user chooses if multiple), emulator check (getprop ro.kernel.qemu), app installed (pm path); build exploit command from template catalog (or RAG later); capture signals (volatile in parallel, then non-volatile; network_capture stub); run command; LLM verifies success from before/after signals.
- **Artifacts:** `apps/<app_id>/<run_ts>/exploit_verification/<vuln_module>/<hyp_id>/` (e.g. exported_components) with before/after screenshots, logcat, commands, and verification result. Each vuln module has its own subfolder; each hypothesis has a per-hypothesis directory.
- **Skills (exploit tier):** app_env_check, build_exploit_command, capture_signals, run_exploit_command, verify_exploit_result. Vuln–skill–signal_profile matrix (JSON) defines which signal types each module captures.

### Phase 6 — Interactive RE Workbench: web UI shell + emulator mirror

**Goal:** Local web UI on top of existing runs: browse `apps/<app_id>/` runs, dossier, findings; live emulator mirror and logcat; thin presentation only (no new vulnerability logic in the UI).

**Deliverables (target):**

- FastAPI + WebSocket server under `androscan/web/`; static-built React app (`androscan/web/frontend/`); bind **127.0.0.1** by default; config `web_host` / `web_port` in `global_config.yaml`.
- REST: projects, runs, dossier JSON, report/findings JSON (see `docs/TASKS.md` Phase 6 list).
- Mirror: scrcpy or `screencap` polling; forward taps via `adb shell input tap`.
- Logcat stream over WebSocket; CLI entry e.g. `--serve` (optional `--apk … --serve` after analysis).

**Tests:** mocked REST/WebSocket; no live Ollama/device required in default CI.

### Phase 7 — Click-to-code mapping

**Goal:** User taps mirror → resolve UI node (uiautomator XML + coordinate hit-test) → foreground Activity → resource-id / layout → jadx source references (`findViewById`, bindings) → open in Monaco at the right line.

**Deliverables (target):** `POST /api/tap`, `GET /api/source/{class}`; optional LLM-assisted fallback; new **llm** skill `resolve_ui_element` (catalog + tests). Deterministic path preferred; LLM is advisory when tracing is ambiguous.

### Phase 8 — Static call graph from Smali  *(landed 2026-04-27 via Hook Lab v1)*

**Goal:** Offline graph from apktool Smali: methods as nodes, `invoke-*` as edges; optional class hierarchy; graph APIs for neighbors/paths and LLM-assisted queries; Cytoscape.js in the UI.

**Status:** Landed in **Hook Lab v1** sub-steps **4.1** (Smali parser + dispatch resolver + per-app SQLite store + REST routes) and **4.2** (Cytoscape pane with package-overview / focus-subgraph layouts + click-to-source). DEC-016 was amended to switch the call-graph store from a single `apps/<app_id>/call_graph.json` to per-app SQLite at `apps/<app_id>/.decompiled/<sha>/call_graph.sqlite` (schema_version 1). Routes shipped: `GET /api/graph/{app_id}` (paginated), `GET /api/graph/{app_id}/neighbors/{node_ref}`, `GET /api/graph/{app_id}/paths`, `GET /api/graph/{app_id}/status`, `POST /api/graph/{app_id}/rebuild`. The LLM-assisted query path is the **`query_call_graph`** llm-tier skill (sub-step 4.7 — `overview` / `neighbors` / `paths` modes with parameter clamping; fail-open when the cache is unbuilt). See `docs/STATE.md` (Hook Lab 4.1, 4.2, 4.7) for the as-built reality.

### Phase 9 — Frida integration  *(landed 2026-04-27 via Hook Lab v1)*

**Goal:** Attach to package on device/emulator; load parameterized hook scripts; stream trace events to UI; gate LLM-generated hooks behind operator confirmation; show live hits on the call graph.

**Status:** Landed in **Hook Lab v1** sub-steps **4.3** (headless Frida adapter behind a single import seam; readiness probes for `frida-server` + version-skew), **4.4** + **4.6** (hook template library — `entry_exit_log`, `ssl_pinning_bypass`, `crypto`, `shared_preferences`, `intent`, `scope_inspector`; locked renderer contract; fail-closed registry walk), **4.5** (Stage→Inject UI with `pyjsparser` pre-validation, WS trace, JSONL persistence at `apps/<app_id>/<run_ts>/frida/<session>.jsonl`, per-app `hook_target_package_prefix` server-side allowlist, **403 hook_blocked** when violated), **4.7** (`generate_frida_hook` llm-tier skill — first consumer of DEC-022's `SkillMeta.requires_confirmation=True` consent class; render-only, never injects), and **4.8** (live Cytoscape overlay — fired methods render in bold cyan with hit counts on hover; static = muted grey per DEC-023). Optional integration with exploit verification (new `frida_trace` signal type) is **deferred** beyond v1; v1 surfaces the trace through the Hook Lab tab only. Modify-return / mutation hooks, self-hosted Monaco (ISSUE-010), and `frida-server` auto-provisioning are **explicitly out of scope** for v1 (`frida-server` is operator-managed by design — same posture as `adb` / `jadx` / `apktool`). See `docs/STATE.md` Hook Lab 4.1–4.8 sub-bullets and `docs/DECISIONS.md` DEC-023 for the as-built reality + rationale.

**Full sub-task checklists:** `docs/TASKS.md` § Interactive RE Workbench → § Hook Lab v1 — sub-step backlog (now flagged "v1 complete").

### Phase 10 — Behavior Trace (Lab tab gate-identification mode) *(**v1 landed 2026-04-29**, sub-steps 10.0 → 10.8 — see `docs/STATE.md` § "Phase 10 — Behavior Trace (Lab tab gate-identification mode)" sub-bullets for the as-built record)*

**Goal:** From a UI element or method anchor, enumerate the closure of conditional gates that govern its behaviour, classify each gate's outcome (deny / allow / neutral with confidence), and propose template-bound bypass plans the operator can stage → inject via the existing Hook Lab v1 Stage→Inject path. Trace becomes the headline mode of the **Lab** tab (renamed from Hook Lab per **DEC-024**, concurrent with this phase); the existing Cytoscape pane is demoted to "Graph mode" alongside `Manual Hooks` and `Trace`. Trace adds **zero new device-touching surface area** — every state mutation flows through the same paths Hook Lab v1 already established.

**Workflow:** *Anchor → Locate → Trace → Classify → Manipulate → Verify* — operator picks an anchor (Mirror tap → click-to-code resolution from Phase 7, or a method node from the Lab Graph mode), the static layer enumerates branches in the forward closure (≤ `MAX_TRACE_HOPS = 3` by default, hard-capped at 6, `MAX_TRACE_METHODS = 30`), the LLM is invoked **once per anchor** to interpret + propose plans, the operator stages a plan via the existing `HookBuilder.tsx` flow (now Lab's `Manual Hooks` mode) and injects via the existing `POST /api/frida/sessions` allowlist-gated path, verification is operator-driven (re-tap the anchor, watch the Frida overlay light up the same gate methods in cyan).

**Deliverables (target):**

- New `androscan/analysis/` modules: `trace_types.py` (platform-neutral data model — `BehaviorAnchor` / `DecisionPoint` / `BypassPlan` / `MethodRef` / `FieldRef` as frozen dataclasses), `decisions.py` (decision-point extraction over the Smali instruction stream — one extra pass over `smali_parser.py`'s lexical pipeline), `slicing.py` (intra-procedural backward slicing for predicate origin — no aliasing / no field-flow / no escape analysis; honestly surfaced via `predicate_origin: None` when the slice fails), `branch_classifier.py` (deterministic `classify(decision_point) -> BranchOutcome` with deny / allow / neutral + confidence float; gates with `confidence < 0.6` flagged for LLM re-classification), `bypass_planner.py` (template-bound `BypassPlan` synthesis — references existing `frida_hooks/` templates plus new `force_return_value` / `force_string_compare_equal` / `force_method_skip` templates; risk taxonomy `low / medium / high` with operator-configurable threshold).
- New LLM-tier skill `trace_behavior` (`requires_confirmation=False` per DEC-022 / DEC-024); per-anchor LLM call only — per-decision LLM calls explicitly rejected as unaffordable in DEC-022's per-turn skill-output budget. Persists populated `BehaviorAnchor` to per-app SQLite at `apps/<app_id>/.decompiled/<sha>/trace.sqlite` (schema_version 1, mirrors the DEC-016 / DEC-018 / DEC-023 `<sha>`-keyed cache pattern). Fail-open on missing app context, unbuilt call graph, or unresolved entry method.
- New REST routes `GET / POST / DELETE /api/trace/{app_id}/anchor` in new `androscan/web/trace_routes.py` (factory-built via `build_trace_router(...)` and wired through `androscan/web/app.py`'s same DI seams as `status_routes` / `settings_routes` / `frida_routes`).
- New frontend Trace mode under `androscan/web/frontend/src/components/trace/` (anchor card, decision timeline with verdict badges, bypass plan cards with "Stage in Manual Hooks" buttons that pre-fill `HookBuilder` via `pendingHookPrefill` plumbing, "trace truncated" / "trace may be incomplete" banners). Lab tab gains a 3-mode left-rail switcher: `Trace | Manual Hooks | Graph` defaulting to `Trace`. Cross-tab `Inspect → Trace` plumbing via `pendingTraceEntry` so the existing click-to-code `resolution.best` (Phase 7 / DEC-019) can seed an anchor in one click. New `trace` `ChatAttachment` kind for the Lab chat dock.
- Hook Lab → Lab code rename (`HookLabTab.tsx` → `LabTab.tsx`, `HookLabCodeView` → `LabCodeView`, URL hash `#/hook` → `#/lab` with a back-compat redirect, per-tab chat-prompt key in `androscan/web/chat.py`, `frontend/README.md` reference) — all in sub-step 10.6 alongside the tab routing changes, keeping Phase 10's docs-vs-code commits cleanly separated. Until then code-level identifiers in DEC-024 / TASKS.md Phase 10 entries reference existing `HookLabTab.tsx` filename for accuracy.

**Tests (target):** ~80 new tests across `tests/test_decisions_extract.py`, `tests/test_decisions_slicing.py`, `tests/test_branch_classifier.py`, `tests/test_bypass_planner.py`, `tests/test_trace_behavior_skill.py`, `tests/test_trace_routes.py`. Deterministic fixture-driven static layer (small Smali under `tests/fixtures/trace_smali/`); LLM mocked at the test boundary; no device touching in default suite (the `device` pytest marker from DEC-023 sub-step 4.3 covers any opt-in cases).

**Why this phase, not "more call-graph affordances":** the Cytoscape topological view is infrastructure, not deliverable. Operators want to know *what stops a particular UI behaviour from working* and *how to bypass it* — not which method calls which. DEC-024 §rationale captures the full reasoning, including why client-side trust manipulation is the right framing (vs. source/sink reachability, which is well-served by other tools and is the workflow most operators already have a path to) and why static enumeration + per-anchor LLM interpretation is the right architectural split (vs. LLM-only or per-decision LLM calls).

**Status:** **v1 complete 2026-04-29** — all eight sub-steps landed: 10.0 planning checkpoint + DEC-024 (2026-04-28); 10.1 decision-point extraction (10 if-* + packed/sparse switch over the apktool tree, +13 tests); 10.2 backward slicing for predicate origin (intra-procedural; 5 `*Origin` discriminated-union variants, +16 tests); 10.3 heuristic branch outcome classifier (locked deny/allow/neutral catalog + 4-tier confidence pinned to `[1.0, 0.85, 0.45, 0.0]`, +19 tests); 10.4 bypass planner + three Frida override templates (`force_return_value` LOW / `force_method_skip` MEDIUM / `force_string_compare_equal` MEDIUM; risk taxonomy with operator-configurable `trace.bypass_risk_max`, +29 tests); 10.5 `trace_behavior` LLM-tier skill + per-app `trace.sqlite` cache (one LLM call per anchor, fail-soft, +22 tests); 10.6 REST endpoint + tab shell + Hook Lab → Lab code rename (5 routes under `/api/trace`, 3-mode left-rail switcher `Trace | Manual Hooks | Graph`, `#/hook` → `#/lab` back-compat redirect, `tab="hook"` chat alias, +18 tests); 10.7 Trace mode frontend (the headline UI — six new components under `components/trace/` + `useTraceAnchor` hook + `BypassPlanCard` "Stage in Manual Hooks" cross-mode handoff via `pendingHookPrefill`, +0 tests per FE convention); 10.8 Mirror → Trace integration + chat hook + docs sweep (Inspect-tab `BestBanner` "trace ↗" button + `pendingTraceEntry` plumbing + new `trace` `ChatAttachment` kind for the Lab chat dock with top-3 plans + 40-decision summary capped at 6,000 chars + this docs sweep, +4 tests). Final v1 test count: **~104 new tests across Phase 10**, over the 80-test target from DEC-024. Rationale + alternatives + tradeoffs + risk taxonomy + rename policy: **DEC-024**. v2 candidates: backend smali-signature autocomplete in the Trace mode form (✅ landed 2026-04-29 as the `MethodPicker` + `GET /api/graph/{app_id}/methods` route — operator hit the gap day-1 of v1, pulled forward), cross-tab "Trace from here" button on the Manual Hooks call-graph nodes, `BehaviorAnchor`-aware in-graph overlay, per-app TTL on cached anchors, inter-procedural slicing for `ParamOrigin`, switch-case bypass plans, the cross-platform mirroring DEC-024 explicitly defers (iOS / WebView / native binary). The remaining v2 candidates are picked up as **Phase 11 — Behavior Trace v2**; see § Phase 11 below.

---

### Phase 11 — Behavior Trace v2 (Lab tab — bounded inter-procedural slicing + operator-UX polish) *(**v2 landed 2026-04-30 + v2.1 follow-up release landed 2026-05-05** — v2: all sub-steps 11.0 → 11.7 complete + ISSUE-013 → Resolved (Phase 11 v2); v2.1: all sub-steps v2.1.0 → v2.1.6 complete + ISSUE-009 → Resolved (Phase 11 v2.1.5) as a side-effect of the bounded chat agentic loop substrate v2.1.5 had to ship as the substrate the chat-widget pattern is built on; see `docs/STATE.md` § "Phase 11 — Behavior Trace v2 (Lab tab — bounded inter-procedural slicing + operator-UX polish)" sub-bullets + "Recent completed work" v2.1 sub-bullets for the as-built record + DEC-025's v2 closing note + v2.1 closing-note extension)*

**Goal:** Close the largest known v1 precision gap (ISSUE-013 — intra-procedural slicing's false-negative rate on production apps) via bounded inter-procedural backward slicing for `predicate_origin`, and land three operator-feedback-driven UX deliverables surfaced during the first week of v1 use. Phase 11 v2 adds **zero new device-touching surface area** — every state mutation still flows through the existing Hook Lab v1 Stage→Inject controls + the per-app `hook_target_package_prefix` allowlist DEC-024 / DEC-023 already established.

**Workflow (unchanged from v1):** *Anchor → Locate → Trace → Classify → Manipulate → Verify*. The slicer changes are invisible to the workflow shape — operators see *more* mechanically-suggested bypass plans on the same anchor, *fewer* "trace may be incomplete" banners, and a small "via N helper method(s)" / "via 1 field write" depth pill on the `PredicateOriginView` cards where the slicer descended past a v1 terminal. The UX deliverables tighten the loop's per-step friction (decision-timeline order is now disclosed inline; "Verify with runtime trace" is a one-button stage of `entry_exit_log` instead of a multi-tab maneuver; "Trace from here" cross-tab button on call-graph nodes mirrors 10.8's Inspect → Trace handoff for the Manual Hooks side; cached `BehaviorAnchor`s render with a small ⚓ glyph on the call-graph pane so operators can see which methods are already explored).

**Deliverables (target):**

- **Bounded inter-procedural slicer** in `androscan/analysis/slicing.py` (depth-2 default, hard cap 4 in code; `trace.max_slice_depth` config knob added in 11.6). Two new walkers: `_descend_into_callee` (descends past `MethodCallOrigin` terminals when the callee resolves in the call graph + a new `is_stateless(method, ...)` analyzer returns True) and `_walk_field_write_sites` (walks the same class's `iput-*` / `sput-*` write sites for `FieldReadOrigin` terminals; cross-class field-flow stays out of scope per ISSUE-013's recommended-fix note). Shared `_DescentBudget` dataclass keeps the closed-economy guarantee — a method that descends 2 hops via callees can't *also* walk a field-write site, and vice versa. Visited set keyed on `(class_smali, method_name, descriptor)` terminates cycles.
- **Type-driven `is_stateless(method, classes_by_smali, visited) -> bool`** analyzer in `slicing.py` — walks the method's body looking for *side effects* (`iput-*` / `sput-*` / `aput-*`, `invoke-*` to non-stateless callees, `monitor-*`, throws, reflection-flagged methods). Recursive with cycle detection. Hand-curated `_STATELESS_LIB_DENYLIST` constant lists stdlib classes we can't walk into (no Smali source for them — `Ljava/lang/Math;`, primitive boxing classes, `Ljava/lang/String;` getters, `Lkotlin/jvm/internal/Intrinsics;`); small list, easy to audit, lives next to `_DescentBudget`. Type-driven was picked over hand-curated regex because the regex would silently miss app-private stateless helpers (e.g. `ResourceUtils.getStringResource(int)` — stateless by construction, no regex would mark it).
- **Three frontend operator-UX deliverables (Tier 1 a+b+c):** (a) **Decision-timeline UX clarity polish** — `DecisionTimeline.tsx` header gains a static-traversal-order disclosure ("Listed in static traversal order — not runtime execution order"); each `DecisionPointCard` gains a "Verify with runtime trace" button that stages `entry_exit_log` against the decision's enclosing method via the existing `pendingHookPrefill` plumbing + auto-flips `labMode` to `"manual-hooks"`; high-confidence hits (BranchOutcome.confidence >= 0.85 + at least one non-neutral verdict) get an action hint pill above the card body. (b) **"Trace from here" on call-graph nodes** — `CallGraphView.tsx` right-click context menu gains a new "Trace this method" entry that writes `pendingTraceEntry` (re-uses 10.8's plumbing established for Inspect → Trace) + flips `labMode` to `"trace"` + `setTab("lab")`. External nodes get the entry disabled with a tooltip. (c) **`BehaviorAnchor`-aware overlay on Manual Hooks Cytoscape pane** — new route `GET /api/trace/{app_id}/anchored-methods` enumerates every method in every cached anchor's closure; `CallGraphView.tsx` gains an optional `anchoredMethods?: ReadonlySet<string>` prop that mirrors the existing `hitsByMethod` overlay pattern (uses the same `hitKey` join helper for cohesion); cached methods render a small purple ⚓ glyph in the corner. Frida hits still beat trace anchors visually because they're live data (DEC-023 / 4.8 sets the same precedence rule).
- **Cache invalidation via `trace.sqlite` schema bump** — `androscan/internal/trace_cache.py` `SCHEMA_VERSION` bumps from `"1"` to `"2"` in 11.6. Existing `get_status()` reader already returns `status="failed"` with `error="schema_version mismatch"` on mismatch, so all v1 cached anchors silently re-build on first 11.x deploy via the existing route layer's "missing → build" fallback path. No migration code, no UI banner needed (the existing `from cache | freshly built` footer on `BehaviorAnchorCard` will read "freshly built" the first time each anchor is re-opened post-deploy). Mirrors DEC-024's "drop-the-cache invalidation" model.
- **LLM budget bumps** in `global_config.yaml`'s `ollama:` section (per DEC-025 open question 1): `num_ctx: 16384` (was unset → defaulted to 8192; covers ~2× input prompt growth from deeper `predicate_origin` chains) AND `num_predict: 12288` (from today's 8192; covers ~1.5× output growth from richer rationale + more plans). New `Config.ollama_num_ctx: int = 8192` field added to `androscan/config/loader.py` next to the existing `ollama_num_predict`; both wired through `CONFIG_FIELD_MAP` + `LIVE_RELOADABLE_FIELDS` (both True — Ollama re-reads them on each request).
- **UI density on deeper chains: pill-only depth indicator** on `PredicateOriginView.tsx` ("via N helper method(s)" / "via 1 field write" pill next to the existing origin-kind tag when the slicer descended past at least one terminal). Tooltip lists the methods walked through so operators can audit the path without expanding the card. NO "show full chain" expander — keeps card density flat (per DEC-025 open question 4).
- **Bypass planner re-run** in 11.6 against the deeper terminals — `BypassPlan` discriminated-union shape unchanged (per DEC-025: "richer leaves, no schema bump"); planner correctly emits `force_method_skip` against the deeper terminal method when descent succeeds, `force_return_value` against the field-write site's source method when field-walk succeeds. Verified with new tests in `tests/test_bypass_planner.py`.

**Tests (target):** ~32 new tests across Phase 11 (versus Phase 10 v1's ~104; v2 is a tighter scope because the discriminated-union shape didn't change — no new wire-shape contracts to lock except the one optional `descent_depth: int = 0` field on `MethodCallOrigin` / `FieldReadOrigin`). Spread weighted toward the static layer: ~18–20 in `tests/test_decisions_slicing.py` for the slicer descent + `is_stateless` analyzer (11.4); ~8 for field-write-site walking (11.5); ~6 for the bypass-planner re-run (11.6); ~3 for the new `descent_depth` field's payload-shape assertions in `tests/test_trace_routes.py` (11.6). Plus ~6 for the new `/api/trace/{app_id}/anchored-methods` route in 11.3. Deterministic fixture-driven (extends `tests/fixtures/trace_smali/` with `Helpers.smali`); LLM mocked at the test boundary in 11.6's bypass-planner re-run tests; no device touching in default suite.

**Why this phase, not "wait for more telemetry":** the operator-feedback-driven UX deliverables (Tier 1 a+b+c) address friction we *measured* in the first week of v1 use (decision-timeline order confusion, "how do I get a runtime trace?" workflow, etc.); the slicer precision gain (Tier 2 d) addresses ISSUE-013, the largest known v1 precision gap. Measured > predicted in priority order, and the slicer work + the UX bundle compose well (the UX bundle ships first on a Trace mode that already feels right; the slicer work ships after into a UI that's been polished for it). Bundling into a single v2 phase is the better calendar than splitting them across two phases. Per-overload precision on two-register comparisons (ISSUE-014) is explicitly *not* in scope — that's a schema bump on `DecisionPoint.predicate_origin` from a single field to a tuple, doubles per-anchor card count for two-register-heavy methods, and ships when v2 telemetry shows the precision gap matters in practice. Same calculus for auto-verify, free-form LLM JS, per-app TTL, and iOS / WebView / native-binary adapters — all v3 / Phase 12 candidates per DEC-025.

**Status:** **v2 landed 2026-04-30 + v2.1 follow-up release landed 2026-05-05.** **v2:** all eight sub-steps 11.0 → 11.7 complete: 11.0 planning checkpoint + DEC-025 (2026-04-30, docs only); 11.1 Tier 1 (a) decision-timeline UX clarity polish (frontend-only on `DecisionTimeline.tsx` + `App.css`); 11.2 Tier 1 (b) "Trace this method" on call-graph nodes (frontend-only on `CallGraphView.tsx`); 11.3 Tier 1 (c) `BehaviorAnchor`-aware overlay on Manual Hooks Cytoscape pane (new route `GET /api/trace/{app_id}/anchored-methods` + frontend overlay layer + 7 backend tests); 11.4 Tier 2 (d) part 1 — bounded inter-procedural slicer descent + type-driven `is_stateless` analyzer (slicer +~330 LOC over the v1 base + `_DescentBudget` + `_STATELESS_LIB_DENYLIST` + 27 new tests); 11.5 Tier 2 (d) part 2 — same-class field-write-site walking (slicer +~280 LOC + `_walk_field_write_sites` walker + constructor-priority rule + 8 new tests); 11.6 bypass-planner re-run + LLM-tier `trace_behavior` budget revisit + cache schema bump + ISSUE-013 close-out (slicer ~60 LOC delta for `descent_depth` tagging + `trace.sqlite` `SCHEMA_VERSION` 1 → 2 + two new config knobs `ollama_num_ctx` + `trace_max_slice_depth` + LLM client `num_ctx` forwarding + frontend depth-pill UI + 26 new tests); 11.7 docs sweep + DEC-025 closing note (this section's status promotion + the 11.7 row in `docs/TASKS.md` + the parent line + sub-bullet + Recent-completed-work entry + Next-expected-milestone rework in `docs/STATE.md` + DEC-025's v2 closing note in `docs/DECISIONS.md`). Final Phase 11 test count: **+26 new tests** (`tests/test_bypass_planner.py` +6 + `tests/test_decisions_slicing.py` +35 across 11.4 / 11.5 / 11.6 + `tests/test_config.py` +14 + `tests/test_trace_routes.py` +7); near the spec target of ~32 tests. **ISSUE-013 → Resolved (Phase 11 v2)** against the v1-vs-v2 corpus regression-floor measurement test (`tests/test_decisions_slicing.py::test_v1_vs_v2_corpus_measurement_v2_resolves_strictly_more_terminals`); the > 50% production-dogfood threshold from the original DEC-025 framing remains a separate pending verification (re-open if real-app measurement shows v2 still leaves > 50% of v1's None-cases unresolved). **LLM-budget measurement outcome deferred** per Q1 (A) of the 11.7 planning checkpoint — 11.6 shipped the bumped budgets per spec but the actual real-app token-usage measurement was deferred (no dogfood-app traces accrued under v2 within the planning window); follow-up tracked separately when telemetry surfaces, with the existing measure-first-tighten-if-needed posture intact (revert + tighten `_format_decision_for_llm` per-decision prose payload if qwen3.5:35b runtime can't actually use the larger context). See `docs/STATE.md` § Phase 11 sub-bullets for the as-built per-sub-step record + DEC-025's v2 closing note for the v3 hand-off. **v2.1 follow-up release:** all seven sub-steps v2.1.0 → v2.1.6 complete (2026-05-05; mirrors Phase 11's 11.0 → 11.7 cadence): v2.1.0 planning checkpoint + DEC-025 closing-note v2.1 extension (docs only); v2.1.1 deterministic UX backbone — `ClassMethodTree` browser embedded in Trace mode + Smali-field collapse + Hops-inline restructure (FE-only on `LabTraceMode.tsx` + `ClassMethodTree.tsx` + `App.css`); v2.1.2 backend coalescer + frontend debounced spinner + ✓/⚠ validation pill (new `POST /api/trace/{app_id}/normalise-entry` endpoint + +8 backend tests); v2.1.3 Tier 1 "Find similar classes" via `POST /api/trace/{app_id}/suggest-similar-classes` (sibling endpoint using `difflib.SequenceMatcher`; +7 tests); v2.1.4 Tier 2(a) "Ask AI" button + `pendingChatPrefill` plumbing (FE-only); v2.1.5 Tier 3 `androscan/skills/suggest_trace_entry.py` skill + skill-response `widgets[]` schema extension (`SkillResult.widgets: tuple[SkillWidget, ...]` field + `SkillWidget` typed union with `TraceEntryCandidateWidget` as first member) + `<TraceEntryCandidateWidget>` chat renderer + bounded chat agentic loop substrate (`androscan/web/chat.py` refactor with `MAX_AGENTIC_TURNS = 5` / `MAX_SKILLS_PER_TURN = 3` + new `skill_request` / `skill_result` / `skill_pending` / `widget` SSE events; +37 tests; **closes ISSUE-009 as a side-effect** — the chat-widget pattern requires an agentic-loop substrate that DEC-022 had recommended but `androscan/web/chat.py` had been single-pass through Phase 11 v2; v2.1.5 had to ship the loop first as the substrate the widget pattern is built on, an architectural deviation from the v2.1.0 spec which had assumed the loop existed); v2.1.6 docs sweep + DEC-025 closing-note v2.1 extension (docs only). Final v2.1 test count: **+28 new tests** (test count baseline at v2.1.0 was 907; HEAD is 935). **Architectural pattern lock per Q7 (ii) (load-bearing addition):** structured-output extension to skill response schema — first instance of LLM-emitted interactive widgets in chat, deliberately extensible (future skills can reuse the same `widgets[]` schema + render seam; cross-link DEC-022 — the chat-widget pattern extends DEC-022's chat agentic loop with the new `widgets[]` outbound channel + structured-skill-result render seam). DEC-025 stays Active (no new DEC for v2.1 per Q10: α — still in v2 family). **Phase 12 — Behavior Trace v3 / cross-tab follow-ups planning checkpoint (12.0)** is the next active item (was deferred-after-v2.1 between 2026-05-05 v2.1.0 ratification and 2026-05-05 v2.1.6 wrap-up; flips back to Active now that the v2.1 follow-up release is complete); DEC-026 to be ratified picking the operator-demand-gated v3 candidates from DEC-025's deferral list (ISSUE-014, auto-verify, free-form LLM JS, per-app TTL, iOS / WebView / native-binary adapters, Smali patching, LLM-budget real-app measurement follow-up). **Parallel non-blocking thread:** **CI.0 — Phase 4 CI** (pytest on push/PR; long-deferred from Phase 4); originally promoted to Active by DEC-025 in service of the Phase 11 slicer changes but ultimately shipped *after* Phase 11 v2 (the suite passed locally throughout 11.x and across the v2.1 follow-up release, and operator's "Lab engine before CI" direction (2026-04-30) deprioritized it relative to v3 work). Currently deprioritized — promote when Lab engine reaches a stable v3 baseline. Rationale + alternatives + tradeoffs + the four pre-answered planning-checkpoint questions + the v3 deferral list + the v2 closing note + the v2.1 closing-note extension: **DEC-025**.

---

### LCP track — `LLM_PROVIDERS` table refactor + llama.cpp local provider *(**LCP.0 ratified 2026-05-06** — parallel infrastructure track on `main`, not a Phase, closest precedent CI.0; orthogonal to Phase 11 / 12 feature work; sub-steps LCP.0 → LCP.6 strictly linear; see `docs/STATE.md` "Recent completed work" LCP sub-bullets + `docs/TASKS.md` § **LCP — llama.cpp local provider — sub-step backlog** + DEC-027 for full Q1-Q5 lock-in record)*

**Goal:** Swap the local LLM runtime from Ollama (`qwen3.5:35b` heavy on the operator's 36 GB M3 unified-memory budget per the 2026-05-05 status share) to **llama.cpp**'s OpenAI-compatible HTTP shim (`llama-server`). Operator runs `llama-server -c 16384 -ngl 99 -fa --port 8033 --host 127.0.0.1 --jinja` against a Q5_K_M / UD-Q5_K_XL Qwen3-family GGUF; AndroScan consumes `http://127.0.0.1:8033/v1/chat/completions` via a new dedicated `_call_llamacpp()` HTTP path parallel to `_call_ollama()`. Tighter Metal acceleration on M-series (per-layer offload via `-ngl 99`, no host↔device copy cost) + flash attention (`-fa`) freeing KV-cache budget delivers operator-controllable per-quant memory tradeoffs without re-pulling Ollama-managed model files.

**Workflow (unchanged from operator's perspective):** Operator picks "Local (Ollama)" / "Local (llama.cpp)" / "Cloud" radio in Settings → Global; the underlying transport switch is invisible to every Behavior Trace / Hook Lab / Reports / Inspect tab feature. AndroScan's "tool calling" stays as JSON-mode-driven prompt orchestration over the `skill_requests` / `hypotheses` schema — verified runtime-agnostic per the previous Ask-mode session's Grep audit (no OpenAI-native `tools` / `tool_calls` usage anywhere in the codebase). The runtime swap is a transport-layer concern, not a protocol-layer concern; skills, parser, workflow loop, and the chat agentic loop substrate (v2.1.5) all continue to work without modification.

**Deliverables (target):**

- **`LLM_PROVIDERS` table refactor** in `androscan/config/loader.py` (LCP.1) — top-level dict with `local` / `cloud` sub-sections; `local` contains `"ollama"` (`base_url_default: "http://localhost:11434"`, `kind: "local-ollama"`) + `"llamacpp"` (`base_url_default: "http://127.0.0.1:8033/v1"`, `kind: "local-openai-compat"`, `key_env: None`); `cloud` contains the existing 6 cloud providers verbatim. Backwards-compat alias `CLOUD_PROVIDERS = LLM_PROVIDERS["cloud"]` keeps existing imports working through the LCP.1 refactor commit. New `Config.provider_kind() -> Literal["local-ollama", "local-openai-compat", "cloud"]` method; `is_cloud()` becomes a backwards-compat shim returning `provider_kind() == "cloud"`.
- **Dedicated `_call_llamacpp()` HTTP path** in `androscan/llm/client.py` (LCP.2) — `~80-120 LOC` parallel structure to `_call_ollama()` (both use `requests` directly; both forward AndroScan-specific JSON-mode + temperature + max_tokens knobs but to different request shapes). Router in `complete()` becomes a three-way switch on `Config.provider_kind()`. Defensive `<think>...</think>` strip on the response `content` field for parity with Qwen3-family reasoning models that may leak think-blocks into the content channel depending on the build's `--reasoning-format` flag.
- **`probe_llamacpp` health probe** in `androscan/web/health_probes.py` (LCP.3) — hits `GET /v1/models` (newer builds also have `/health`); structured result mirrors `probe_ollama` (`{ok, label, version, models, error}`). Settings → Status panel renders an `LlamaCppStatus` block conditional on `provider_kind === "local-openai-compat"` — replaces the Ollama block when the operator's selected provider is llamacpp (only one local LLM runtime at a time per operator config).
- **Settings UI top-level radio restructure** in `androscan/web/frontend/src/tabs/SettingsTab.tsx` (LCP.4) — replaces the existing flat provider dropdown with a three-way radio "Local (Ollama)" / "Local (llama.cpp)" / "Cloud" with the existing 6-cloud-provider dropdown nested under "Cloud" when selected. Pre-existing Ollama users land in "Local (Ollama)" by default with no semantic knob change. llama.cpp sub-knobs include `base_url` (default `http://127.0.0.1:8033/v1`), `model` (free-text GGUF label), a read-only "context size set at server start (`--ctx-size 16384`)" line, and `max_tokens` (mapped from the existing `num_predict` field internally so the field/env-var/yaml infrastructure doesn't churn). May split into LCP.4a (UI restructure, defaults preserved) + LCP.4b (llama.cpp sub-knobs land) if the diff balloons during planning.
- **JSON-mode parity baseline (LCP.2) + committed GBNF grammar enforcement follow-up (LCP.6).** v1 ships `response_format: {"type": "json_object"}` mirroring Ollama's `format: "json"`; LCP.6 emits GBNF grammar from the existing JSON schema for `skill_requests` / `hypotheses` (with optional `widgets[]` post-v2.1.5) — benefits **both** local providers (Ollama supports JSON-schema mode in newer builds; gate on the relevant Ollama version detected by `health_probes.probe_ollama`). Sub-step ordering decision (single LCP.6 vs LCP.6a + LCP.6b split) lives in the LCP.6 planning checkpoint based on the JSON-schema → GBNF emitter scope at that point.
- **README pointer to upstream `llama.cpp` documentation** at LCP.5 — no `docs/LLAMACPP.md` operator setup guide per Q5 (C). Notes the AndroScan-specific knobs operators should set (`--port 8033`, `--ctx-size 16384`, `-ngl 99`, `-fa`, `--host 127.0.0.1`, `--jinja`); operators install + build + run `llama-server` via upstream's canonical source-of-truth (https://github.com/ggerganov/llama.cpp).

**Tests (target):** ~30-35 new tests across LCP.1 → LCP.4 + LCP.6 (LCP.0 + LCP.5 are docs-only). Spread weighted toward the LLM client + grammar layers: ~+5-7 in `tests/test_config.py` for the `LLM_PROVIDERS` table refactor + `Config.provider_kind()` method (LCP.1); new `tests/test_llm_client.py` ~+8-10 for the `_call_llamacpp()` HTTP path + JSON-mode parity + `<think>` strip (LCP.2); ~+5-7 in `tests/test_health_probes.py` for the new `probe_llamacpp` (LCP.3); ~+8 in `tests/test_config.py` for the new `llamacpp_*` `Config` fields (LCP.4); new `tests/test_grammar.py` ~+15-20 for the GBNF emitter + per-skill schema introspection (LCP.6). Deterministic mock-`requests` driven; no real network calls in the default suite.

**Why this track, not "stay on Ollama":** operator hardware reality (M3 MacBook Pro, 36 GB unified memory) is squeezed by the current Ollama-managed `qwen3.5:35b` runtime; switching to llama.cpp gives the operator agency over per-quant memory tradeoffs (UD-Q5_K_XL ~20 GB sweet spot for AndroScan's structured-JSON workload on a 36 GB M3 Pro) + tighter Metal acceleration without re-architecting any feature-layer work. Track is genuinely orthogonal to Phase 11 / 12 (different layer — infrastructure vs. feature); both can be Active simultaneously with no scheduling conflict, conflict surface on shared files is tiny (`docs/STATE.md` + `docs/TASKS.md` Active Task block append-newer-on-top by convention).

**Status:** **LCP.0 ratified 2026-05-06** (this commit — planning checkpoint + DEC-027, docs only — five planning-checkpoint pre-answers Q1-Q5 locked: Q1 (B + table refactor) dedicated `_call_llamacpp()` codepath using `requests` directly NOT extending `_call_cloud()` + `LLM_PROVIDERS` table refactor; Q2 (a) defer GBNF to LCP.6, but **promote LCP.6 from optional → committed** follow-up; Q3 (B) Settings UI top-level radio + nested cloud dropdown; Q4 (A) default `base_url: http://127.0.0.1:8033/v1` with port `8033` operator-chosen non-default; Q5 (C) README pointer only — no `docs/LLAMACPP.md`). Branch posture: ship on `main` with selective-staging discipline (rejected feature-branch alternative — operator chose the established Phase 10 / 11 / v2.1 sub-step-by-sub-step cadence after the v2.1 follow-up release fully landed and `main` became quiescent at HEAD `15e7c1a`). LCP.1 → LCP.6 pending; sub-step backlog table in `docs/TASKS.md` § **LCP — llama.cpp local provider — sub-step backlog**. Rationale + alternatives + tradeoffs + Q1-Q5 lock-in record + risks: **DEC-027**.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| LLM hallucination (wrong evidence_refs or irrelevant findings) | Strict output schema; validate every evidence_ref against dossier; drop invalid entries; cap number of hypotheses in prompt. |
| Dossier too large (context limit) | Not a primary risk with a large-context local LLM; only relevant if using a small-context model (then truncate/summarize or chunk). |
| Ollama unavailable or slow | Timeouts and retries in LLM layer; clear CLI error; optional `--dry-run` that stops after dossier and prints it. **AC (Ollama):** (1) Before analysis, CLI checks Ollama reachability (e.g. GET /api/tags); if unreachable, print clear error (orange) and tip (grey) with setup link, then exit. (2) On 404 or other HTTP error from Ollama, raise a user-friendly message (e.g. "Ollama API endpoint not found… Ensure Ollama is running…") not raw HTTP text. (3) Connection and timeout errors already raise clear messages with setup tip. |
| Manifest parsing fragile (malformed APKs) | Validate early (zip, AndroidManifest.xml); use a well-tested parser; fail with clear message; no raw bytes to LLM. |
| Scope creep (MobSF-style feature list) | Strict MVP: exported components + deep links + permissions; prompt and schema enforce fewer, evidence-backed findings. |
| Uncontrolled variation in extraction | Deterministic parsing for canonical structure (ordering, schema). Where inference is used in extraction/enrichment, version it, cache it, or provide a deterministic-only mode so runs are reproducible when needed. |

---

## 10. First end-to-end vertical slice (summary)

**Slice:** One APK → dossier → multi-turn LLM (with optional skills) → report under run folder.

**CLI:** `androscan.py --apk /path/to/app.apk --task exported_components` (and optionally `--output`). Support multiple `--task` values from the start; first slice implements only the `exported_components` task.

**Flow:**

1. Resolve APK path; derive app_id (sanitized package); create `apps/<app_id>/<run_ts>/`.
2. Extraction: unpack APK; parse manifest; build dossier (exported activities, services, receivers, providers, deep links, permissions).
3. LLM: Send dossier + global context (skills). Multi-turn: if LLM returns skill_requests, run skills (e.g. get_decompiled_class), append results, re-prompt until hypotheses are returned or max turns.
4. Validate hypotheses (evidence_refs, 1–5 exploitability/confidence); normalize to finding model.
5. Write report and run artifacts (observations.json, run.log, run_meta.json, report.json) under `apps/<app_id>/<run_ts>/`.
6. Tests: Integration test with fixture APK and mock LLM response; assert report contains expected finding shape and valid evidence_refs.

---

## 11. Relationship to other docs

- **Phases 6–9** (Interactive RE Workbench): detailed sub-tasks in `docs/TASKS.md`; architecture deltas in `docs/ARCHITECTURE.md`; rationale in `docs/DECISIONS.md` (DEC-015–023).
- **Phase 10** (Behavior Trace — Lab tab gate-identification mode): detailed sub-tasks in `docs/TASKS.md` § Phase 10 sub-step backlog; rationale + data model + rename policy in `docs/DECISIONS.md` **DEC-024**.
- **Target shape and MVP contracts:** this document.
- **Concise purpose:** `docs/PROJECT_BRIEF.md`.
- **Current implementation:** `docs/STATE.md`.
- **Active work:** `docs/TASKS.md`.
- **Structural boundaries and dependency rules:** `docs/ARCHITECTURE.md`.
- **Rationale for decisions:** `docs/DECISIONS.md`.
- **Implementation and workflow rules:** `docs/CONVENTIONS.md`.
- **Security and safety:** `docs/SAFETY_AND_SECURITY.md`.
- **Testing strategy:** `docs/TEST_STRATEGY.md`.
