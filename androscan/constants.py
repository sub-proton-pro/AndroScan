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
