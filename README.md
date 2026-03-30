# AndroScan

LLM-native Android security analysis for pentesters. Analyzes APK attack surface (exported components, deep links) with a local LLM (Ollama) to produce evidence-backed exploitability findings. After hypotheses are produced, the workflow can run **exploit verification** on an emulator via ADB (device checks, command execution, signal capture, LLM-assisted verification) so the report can mark findings **verified** or **unverified**.

## Usage

```bash
python androscan.py --apk <path-to.apk> [--task exported_components] [--output <dir>]
```

See `python androscan.py --help` for options.

## Skills

Skills are grouped into three tiers: **pipeline** (fixed orchestration steps), **llm** (the LLM can request these during analysis, e.g. decompile a class), and **exploit** (orchestration-only during verification: env checks, building/running exploit commands, capturing signals, verifying results—not advertised in the prompt catalog).

- **Pipeline:** `extract_manifest`, `prepare_dossier`, `generate_report`
- **LLM-requestable:** `get_decompiled_class`, `get_decompiled_method`, `list_classes_in_package`, etc.
- **Exploit (orchestration):** `app_env_check`, `build_exploit_command`, `capture_signals`, `run_exploit_command`, `verify_exploit_result`

Skill definitions and parameters are in the skills layer (`androscan/skills/`). The prompt builder includes only **llm**-tier skills via `list_llm_skills()`.

## Docs

- `docs/DESIGN_DOC.md` — architecture and MVP design
- `docs/STATE.md` — current implementation state
- `docs/TASKS.md` — task queue and priorities
