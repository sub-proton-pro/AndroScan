# AndroScan

LLM-native Android security analysis for pentesters. Analyzes APK attack surface (exported components, deep links) with a local LLM (Ollama) to produce evidence-backed exploitability findings. After hypotheses are produced, the workflow can run **exploit verification** on an emulator via ADB (device checks, command execution, signal capture, LLM-assisted verification) so the report can mark findings **verified** or **unverified**.

## Setup (first run)

```bash
python androscan.py --setup
```

Runs `pip install -e ".[dev,rag]"` (editable install + test deps + the
[`fastembed`](https://qdrant.github.io/fastembed/) embedder used by the RE
Workbench's semantic code search) and, if Node.js is available, `npm ci` +
`npm run build` in `androscan/web/frontend/` to produce the RE Workbench
static assets at `androscan/web/static/`. Re-run after pulling changes that
touch dependencies. (You can still install manually if you prefer.)

> The first time you click **Build now** on the Settings → Status RAG card
> (or the first time the LLM calls `search_decompiled_sources`), fastembed
> downloads the `BAAI/bge-small-en-v1.5` ONNX model (~130 MB) into its
> per-user cache. Subsequent index builds reuse the cached model.

## Usage

```bash
python androscan.py --apk <path-to.apk> [--task exported_components] [--output <dir>]
```

See `python androscan.py --help` for options.

### RE Workbench (local web UI, Phase 6)

```bash
# Needs adb. UI assets must exist (run --setup once, or `npm run build` manually).
python androscan.py --serve
# Open http://127.0.0.1:8420/  (port from global_config.yaml web.port or --web-port)
```

After a full APK run, start the server with the same analysis:

```bash
python androscan.py --apk <path-to.apk> --task exported_components --serve
```

Dev mode (Vite proxy to API): run `python androscan.py --serve`, then `cd androscan/web/frontend && npm run dev`.

## Skills

Skills are grouped into three tiers: **pipeline** (fixed orchestration steps), **llm** (the LLM can request these during analysis, e.g. decompile a class), and **exploit** (orchestration-only during verification: env checks, building/running exploit commands, capturing signals, verifying results—not advertised in the prompt catalog).

- **Pipeline:** `extract_manifest`, `prepare_dossier`, `generate_report`
- **LLM-requestable:** `get_decompiled_class`, `get_decompiled_method`, `list_classes_in_package`, etc.
- **Exploit (orchestration):** `app_env_check`, `build_exploit_command`, `capture_signals`, `run_exploit_command`, `verify_exploit_result`

Skill definitions and parameters are in the skills layer (`androscan/skills/`). The prompt builder includes only **llm**-tier skills via `list_llm_skills()`.

## Docs

- `docs/DESIGN_DOC.md` — architecture and MVP design (includes **planned** Phases 6–9: Interactive RE Workbench)
- `docs/STATE.md` — current implementation state
- `docs/TASKS.md` — task queue and priorities (§ Interactive RE Workbench for phased milestones)
- `docs/ARCHITECTURE.md`, `docs/DECISIONS.md` — structure and ADRs (DEC-015–017 for web UI, call graph, Frida)
