# Design Document — MVP: LLM-Native Android Pentesting Tool

This document describes the intended product and system design for the AndroScan MVP.

Its purpose is to capture:

- the target shape of the product and MVP architecture
- repository structure and module boundaries
- component dossier and LLM input/output schemas
- prompt design and skills-based analysis flow
- implementation roadmap (Phases 1–4)
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
| **Presentation** | CLI entrypoint (`androscan.py`), args (`--apk`, `--task` multi-valued, `--output`), render report to stdout and/or write under run folder. No business/LLM logic. |
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
    skills/                    # First-class skills layer: pipeline + LLM-requestable skills
      base.py                  # SkillMeta, SkillContext, SkillResult
      extract_manifest.py      # pipeline
      prepare_dossier.py        # pipeline
      generate_report.py        # pipeline
      get_decompiled_class.py   # llm
      get_decompiled_method.py  # llm
      list_classes_in_package.py # llm
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

Skills use a two-tier model: **pipeline** (orchestration only: extract_manifest, prepare_dossier, generate_report) and **llm** (advertised in the prompt: get_decompiled_class, get_decompiled_method, list_classes_in_package). The prompt builder uses the registry’s `list_llm_skills()` for the catalog.

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

5. **Run artifacts** — Write report.json with validated hypotheses; add optionally observations.json, scan_meta.json, scan.log under run folder.

**Acceptance:** One real APK → dossier → multi-turn LLM → report with 1–5 hypotheses and valid evidence_refs; all tests pass with mock LLM in CI.

### Phase 4 — Harden and extend

- Validate evidence_refs against dossier; error handling; config (Ollama URL, timeouts).
- **Tests:** Unit tests (dossier build, extraction parsing, LLM output parsing); integration test (extraction → dossier → mock LLM → report). **Who runs tests:** Developers run locally (e.g. `pytest`); CI runs the same suite on every push/PR. **How:** Pytest from repo root; no live Ollama in CI (mocks/fixtures only).
- Docs: Update `STATE.md`, `TASKS.md`, schema docs as needed.

### Phase 5 — Exploit verification

- **Workflow order:** Analysis → Hypotheses → **Exploit verification** → Report generation. Report is produced only after verification so it can include verified/unverified status and artifact refs.
- **Exploit verification step:** Use emulator + ADB: device selection (adb devices -l; user chooses if multiple), emulator check (getprop ro.kernel.qemu), app installed (pm path); build exploit command from template catalog (or RAG later); capture signals (volatile in parallel, then non-volatile; network_capture stub); run command; LLM verifies success from before/after signals.
- **Artifacts:** `apps/<app_id>/<run_ts>/exploit_verification/<vuln_module>/` (e.g. exported_components) with before/after screenshots, logcat, commands, and verification result. Each vuln module has its own subfolder.
- **Skills (exploit tier):** app_env_check, build_exploit_command, capture_signals, run_exploit_command, verify_exploit_result. Vuln–skill–signal_profile matrix (JSON) defines which signal types each module captures.

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
5. Write report and run artifacts (observations.json, scan.log, scan_meta.json, report.json) under `apps/<app_id>/<run_ts>/`.
6. Tests: Integration test with fixture APK and mock LLM response; assert report contains expected finding shape and valid evidence_refs.

---

## 11. Relationship to other docs

- **Target shape and MVP contracts:** this document.
- **Concise purpose:** `docs/PROJECT_BRIEF.md`.
- **Current implementation:** `docs/STATE.md`.
- **Active work:** `docs/TASKS.md`.
- **Structural boundaries and dependency rules:** `docs/ARCHITECTURE.md`.
- **Rationale for decisions:** `docs/DECISIONS.md`.
- **Implementation and workflow rules:** `docs/CONVENTIONS.md`.
- **Security and safety:** `docs/SAFETY_AND_SECURITY.md`.
- **Testing strategy:** `docs/TEST_STRATEGY.md`.
