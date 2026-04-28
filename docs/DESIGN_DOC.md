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

### Phase 10 — Behavior Trace (Lab tab gate-identification mode) *(planning checkpoint 10.0 done 2026-04-28)*

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

**Status:** Sub-step 10.0 (planning checkpoint + DEC-024 + Hook Lab → Lab rename pin + this phase stub) **done 2026-04-28**, docs-only commit. Sub-steps 10.1 → 10.8 strictly linear, one per Agent-mode session, with a brief Ask-mode planning checkpoint at the top of 10.6 to confirm the `BehaviorAnchor` JSON wire shape before the frontend in 10.7 starts depending on it. **Full sub-task checklist:** `docs/TASKS.md` § Phase 10 — Behavior Trace v1 — sub-step backlog. Rationale + alternatives + tradeoffs + risk taxonomy + rename policy: **DEC-024**.

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
