"""Central app constants. Use for fixed values and labels used across the app."""

# Dossier / app identity
APP_ID_MAX_LEN = 128

# Workflow / LLM
MAX_TURNS_DEFAULT = 3
MAX_HYPOTHESES_PER_REPORT_DEFAULT = 10

# Ollama retry: timeout tiers (sec), num_predict tiers (tokens)
OLLAMA_TIMEOUT_TIERS = [150, 300, 600, 900]
OLLAMA_NUM_PREDICT_DEFAULT = 8192  # 2 * 4096
OLLAMA_NUM_PREDICT_TIERS = [8192, 16384]

# Exploitability scale 1-5 (LLM output)
EXPLOITABILITY_LABELS = {
    5: "critical",
    4: "high",
    3: "medium",
    2: "low",
    1: "minimal",
}

# CLI output
SECTION_RULE_CHAR = "─"
SECTION_RULE_LENGTH = 60
SECTION_RULE = SECTION_RULE_CHAR * SECTION_RULE_LENGTH

# External tools (Phase 3+)
APKTOOL_CMD_DEFAULT = "apktool"
JADX_CMD_DEFAULT = "jadx"
