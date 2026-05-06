"""Central app constants. Use for fixed values and labels used across the app."""

# Dossier / app identity
APP_ID_MAX_LEN = 128

# Workflow / LLM (used when global_config.yaml does not set workflow.max_turns)
MAX_TURNS_DEFAULT = 5
MAX_HYPOTHESES_PER_REPORT_DEFAULT = 10
PER_COMPONENT_ANALYSIS_DEFAULT = False

# Ollama retry: timeout tiers (sec), num_predict tiers (tokens), num_ctx default.
# Phase 11 sub-step 11.6 / DEC-025 — ``OLLAMA_NUM_PREDICT_DEFAULT`` bumped
# from 8192 → 12288 to absorb the v2 inter-procedural slicer's ~1.5×
# response payload growth (deeper PredicateOrigin chains → richer per-
# decision rationale prose). ``OLLAMA_NUM_CTX_DEFAULT`` is new in 11.6:
# Ollama's default context window is 8192 tokens, which the v2 slicer's
# ~2× input prompt growth (deeper chains in the per-anchor closure)
# can squeeze; bumping to 16384 preserves headroom. Both knobs are
# also surfaced in ``Config.ollama_num_predict`` / ``ollama_num_ctx``
# so operators can override per-deployment via global_config.yaml.
OLLAMA_TIMEOUT_TIERS = [150, 300, 600, 900]
OLLAMA_NUM_PREDICT_DEFAULT = 12288  # 11.6 — was 8192 (DEC-025)
# Third tier (24576) added for verbose thinking-mode models like gemma4:26b
# whose internal reasoning routinely emits 15-20K tokens before the JSON body.
# Auto-increase only kicks in on truncation, so smaller models pay no cost.
OLLAMA_NUM_PREDICT_TIERS = [12288, 16384, 24576]
OLLAMA_NUM_CTX_DEFAULT = 16384  # 11.6 — Ollama default is 8192 (DEC-025)
# Whether to enable Ollama "thinking mode" for reasoning-capable models. Default
# True preserves the existing chain-of-thought logging used by the workbench.
# Operators can flip this off in global_config.yaml (``ollama.think: false``)
# for models whose thinking mode is verbose-loop pathological (notably
# ``gemma4:26b``, which can consume 25K+ tokens of repetitive analysis before
# emitting any JSON). Has no effect on non-thinking models.
OLLAMA_THINK_DEFAULT = True

# Phase 11 sub-step 11.6 / DEC-025 — ``trace.max_slice_depth`` config
# knob default. Mirrors ``slicing.MAX_SLICE_DEPTH``; the slicer's
# ``HARD_CAP_DEPTH = 4`` constant is the ceiling regardless of what
# operators set in YAML.
TRACE_MAX_SLICE_DEPTH_DEFAULT = 2

# Issue severity (exploitability + impact); 1-5 from LLM. CVSS 3 scoring in a later phase.
ISSUE_SEVERITY_LABELS = {
    5: "Critical",
    4: "High",
    3: "Medium",
    2: "Low",
    1: "Informational",
}
# Backward compatibility: same mapping, lowercase (e.g. for aggregate "1 high, 2 medium")
EXPLOITABILITY_LABELS = {k: v.lower() for k, v in ISSUE_SEVERITY_LABELS.items()}

# CLI output
SECTION_RULE_CHAR = "─"
SECTION_RULE_LENGTH = 60
SECTION_RULE = SECTION_RULE_CHAR * SECTION_RULE_LENGTH

# External tools (Phase 3+)
APKTOOL_CMD_DEFAULT = "apktool"
JADX_CMD_DEFAULT = "jadx"
