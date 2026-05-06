# AndroScan

LLM-native Android security analysis for pentesters. Analyzes APK attack surface (exported components, deep links) with a local LLM (Ollama or [llama.cpp](https://github.com/ggerganov/llama.cpp)) to produce evidence-backed exploitability findings. After hypotheses are produced, the workflow can run **exploit verification** on an emulator via ADB (device checks, command execution, signal capture, LLM-assisted verification) so the report can mark findings **verified** or **unverified**.

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

## Local LLM

AndroScan supports two local LLM runtimes; pick one in **Settings → Global → LLM provider** (or set `llm.provider` in `global_config.yaml`).

- **Ollama** (default; install via [https://ollama.com](https://ollama.com)): `ollama pull qwen3.5:35b` then `ollama serve`. Knobs live under the `ollama:` section of `global_config.yaml` (`base_url`, `model`, `temperature`, `num_predict`, `num_ctx`).
- **llama.cpp** (recommended for tight memory budgets — ~10–30% faster on M-series via Metal offload + flash attention): build / install per [https://github.com/ggerganov/llama.cpp](https://github.com/ggerganov/llama.cpp), then run `llama-server` against a Q5_K_M / UD-Q5_K_XL Qwen3-family GGUF:

    ```bash
    llama-server \
      -m <path-to-your.gguf> \
      -c 16384 \           # context window — set at server start (NOT a request-level param)
      -ngl 99 \            # offload all layers to GPU (Metal on M-series; one-shot speedup)
      -fa \                # flash attention — additive 10–15% on long contexts
      --port 8033 \        # AndroScan default; non-conflicting with llama.cpp's upstream 8080
      --host 127.0.0.1 \   # loopback-only per AndroScan's safety posture
      --jinja              # chat-template parity (required for Qwen3-family <think> tags)
    ```

    Knobs live under the `llamacpp:` section of `global_config.yaml` (`base_url`, `model` — free-text label; `llama-server` ignores the request-body model field, the actual GGUF is the one loaded at startup; `max_tokens`). Default `base_url` is `http://127.0.0.1:8033/v1` (the OpenAI-compat `/v1` suffix is required).

For the full set of supported quantization levels and per-quant memory tradeoffs, see the upstream `llama.cpp` documentation. **Aggressive quants (Q4_K_M / IQ4_XS) may produce occasional schema drift on AndroScan's structured-JSON workload** — see `docs/KNOWN_ISSUES.md` ISSUE-016 for the workaround (pick a less-aggressive quant, or wait for the LCP.6 GBNF grammar enforcement follow-up).

A third option is a **cloud LLM** (OpenAI / Gemini / Groq / DeepSeek / Together / Mistral) under the same Settings UI radio — useful for spot-checking against a frontier model. Cloud paths require an API key (`llm.cloud_api_key` in `global_config.yaml` or the vendor-specific `*_API_KEY` env var).

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
