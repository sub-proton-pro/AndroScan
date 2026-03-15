# AndroScan

LLM-native Android security analysis for pentesters. Analyzes APK attack surface (exported components, deep links) with a local LLM (Ollama) to produce evidence-backed exploitability findings.

## Usage

```bash
python androscan.py --apk <path-to.apk> [--task exported_components] [--output <dir>]
```

See `python androscan.py --help` for options.

## Skills

Skills are capabilities the LLM can request during analysis (e.g. decompile a class, list classes). Pipeline skills run during extraction; LLM skills are advertised in the prompt and executed on demand.

- **Pipeline:** `extract_manifest`, `prepare_dossier`, `generate_report`
- **LLM-requestable:** `get_decompiled_class`, `get_decompiled_method`, `list_classes_in_package`, etc.

Skill definitions and parameters are in the skills layer (`androscan/skills/`). The prompt builder includes the skills catalog from `list_llm_skills()`.

## Docs

- `docs/DESIGN_DOC.md` — architecture and MVP design
- `docs/STATE.md` — current implementation state
- `docs/TASKS.md` — task queue and priorities
