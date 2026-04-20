"""Load config from global_config.yaml and environment.

Merge order: defaults (constants) -> global_config.yaml (if present) -> env vars.
Env vars override file. Pass config file path via --config or use default search paths.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from androscan import constants


CLOUD_PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "key_env": "TOGETHERAI_API_KEY",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "key_env": "MISTRAL_API_KEY",
    },
}


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Load via load_config()."""

    ollama_base_url: str
    ollama_timeout_sec: int
    ollama_model: str
    ollama_temperature: float
    ollama_num_predict: int
    llm_provider: str  # "ollama" or cloud provider name from CLOUD_PROVIDERS
    cloud_model: str
    cloud_api_key: str
    cloud_temperature: float
    run_folder_root: str
    max_turns: int
    max_hypotheses_per_report: int
    per_component_analysis: bool
    apktool_cmd: str
    jadx_cmd: str
    section_rule_char: str
    section_rule_length: int

    @classmethod
    def default(cls) -> "Config":
        return cls(
            ollama_base_url="http://localhost:11434",
            ollama_timeout_sec=150,
            ollama_model="qwen3.5:35b",
            ollama_temperature=0.2,
            ollama_num_predict=constants.OLLAMA_NUM_PREDICT_DEFAULT,
            llm_provider="ollama",
            cloud_model="",
            cloud_api_key="",
            cloud_temperature=0.2,
            run_folder_root="apps",
            max_turns=constants.MAX_TURNS_DEFAULT,
            max_hypotheses_per_report=constants.MAX_HYPOTHESES_PER_REPORT_DEFAULT,
            per_component_analysis=constants.PER_COMPONENT_ANALYSIS_DEFAULT,
            apktool_cmd=constants.APKTOOL_CMD_DEFAULT,
            jadx_cmd=constants.JADX_CMD_DEFAULT,
            section_rule_char=constants.SECTION_RULE_CHAR,
            section_rule_length=constants.SECTION_RULE_LENGTH,
        )

    @property
    def is_cloud(self) -> bool:
        return self.llm_provider != "ollama"

    @property
    def active_model(self) -> str:
        """Return the model name for the active provider."""
        if self.is_cloud:
            return self.cloud_model or "(not set)"
        return self.ollama_model

    def resolve_cloud_api_key(self) -> str:
        """Return cloud API key from config or environment."""
        if self.cloud_api_key:
            return self.cloud_api_key
        provider_info = CLOUD_PROVIDERS.get(self.llm_provider, {})
        env_var = provider_info.get("key_env", "")
        return os.environ.get(env_var, "") if env_var else ""

    def resolve_cloud_base_url(self) -> str:
        """Return the OpenAI-compatible base URL for the cloud provider."""
        provider_info = CLOUD_PROVIDERS.get(self.llm_provider, {})
        return provider_info.get("base_url", "")

    @property
    def section_rule(self) -> str:
        return self.section_rule_char * self.section_rule_length


def _load_yaml(path: Path, explicit: bool = False) -> dict[str, Any]:
    """Load YAML file; return empty dict if missing (auto-discovered) or raise if explicit and broken."""
    if not path.exists():
        if explicit:
            raise FileNotFoundError(f"Config file not found: {path}")
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        if explicit:
            raise ValueError(f"Invalid YAML in config file {path}: {e}") from e
        return {}
    except OSError as e:
        if explicit:
            raise OSError(f"Cannot read config file {path}: {e}") from e
        return {}


def _safe_int(value: Any, default: int, name: str) -> int:
    """Coerce value to int with a clear error on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        raise ValueError(f"Config key '{name}' must be an integer, got: {value!r}") from None


def _safe_float(value: Any, default: float, name: str) -> float:
    """Coerce value to float with a clear error on failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Config key '{name}' must be a number, got: {value!r}") from None


def _merge_from_yaml(config_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract flat keys from nested YAML for Config. Uses constants as defaults."""
    out: dict[str, Any] = {}
    ollama = config_dict.get("ollama") or {}
    llm = config_dict.get("llm") or {}
    paths = config_dict.get("paths") or {}
    workflow = config_dict.get("workflow") or {}
    output = config_dict.get("output") or {}
    out["ollama_base_url"] = (ollama.get("base_url") or "").strip().rstrip("/") or "http://localhost:11434"
    out["ollama_timeout_sec"] = _safe_int(ollama.get("timeout_sec"), 150, "ollama.timeout_sec")
    out["ollama_model"] = ollama.get("model") or "qwen3.5:35b"
    out["ollama_temperature"] = _safe_float(ollama.get("temperature"), 0.2, "ollama.temperature")
    out["ollama_num_predict"] = _safe_int(ollama.get("num_predict"), constants.OLLAMA_NUM_PREDICT_DEFAULT, "ollama.num_predict")
    out["llm_provider"] = (llm.get("provider") or "ollama").strip().lower()
    out["cloud_model"] = (llm.get("cloud_model") or "").strip()
    out["cloud_api_key"] = (llm.get("cloud_api_key") or "").strip()
    out["cloud_temperature"] = _safe_float(llm.get("cloud_temperature"), 0.2, "llm.cloud_temperature")
    out["run_folder_root"] = paths.get("run_folder_root") or "apps"
    out["max_turns"] = _safe_int(workflow.get("max_turns"), constants.MAX_TURNS_DEFAULT, "workflow.max_turns")
    out["max_hypotheses_per_report"] = _safe_int(workflow.get("max_hypotheses_per_report"), constants.MAX_HYPOTHESES_PER_REPORT_DEFAULT, "workflow.max_hypotheses_per_report")
    out["per_component_analysis"] = bool(workflow.get("per_component_analysis") if workflow.get("per_component_analysis") is not None else constants.PER_COMPONENT_ANALYSIS_DEFAULT)
    out["apktool_cmd"] = paths.get("apktool_cmd") or constants.APKTOOL_CMD_DEFAULT
    out["jadx_cmd"] = paths.get("jadx_cmd") or constants.JADX_CMD_DEFAULT
    out["section_rule_char"] = output.get("section_rule_char") or constants.SECTION_RULE_CHAR
    out["section_rule_length"] = _safe_int(output.get("section_rule_length"), constants.SECTION_RULE_LENGTH, "output.section_rule_length")
    return out


def load_config(config_path: Optional[str] = None) -> Config:
    """Load config: defaults -> global_config.yaml (if found) -> env overrides.

    config_path: explicit path to YAML file. If None, search:
      - cwd / global_config.yaml
      - cwd / config / global_config.yaml
    Env: ANDROSCAN_OLLAMA_URL, ANDROSCAN_OLLAMA_TIMEOUT, ANDROSCAN_OLLAMA_MODEL, ANDROSCAN_RUN_FOLDER.
    """
    defaults = Config.default()
    yaml_data: dict[str, Any] = {}

    if config_path:
        yaml_data = _load_yaml(Path(config_path), explicit=True)
    else:
        cwd = Path.cwd()
        for candidate in [cwd / "global_config.yaml", cwd / "config" / "global_config.yaml"]:
            yaml_data = _load_yaml(candidate)
            if yaml_data:
                break

    merged = _merge_from_yaml(yaml_data)

    # Env overrides
    if os.environ.get("ANDROSCAN_OLLAMA_URL"):
        merged["ollama_base_url"] = os.environ["ANDROSCAN_OLLAMA_URL"].strip().rstrip("/")
    if os.environ.get("ANDROSCAN_OLLAMA_TIMEOUT"):
        try:
            merged["ollama_timeout_sec"] = max(1, int(os.environ["ANDROSCAN_OLLAMA_TIMEOUT"]))
        except ValueError:
            print(
                f"Warning: ANDROSCAN_OLLAMA_TIMEOUT={os.environ['ANDROSCAN_OLLAMA_TIMEOUT']!r} is not a valid integer; using default.",
                file=sys.stderr,
            )
    if os.environ.get("ANDROSCAN_OLLAMA_MODEL"):
        merged["ollama_model"] = os.environ["ANDROSCAN_OLLAMA_MODEL"].strip()
    if os.environ.get("ANDROSCAN_RUN_FOLDER"):
        merged["run_folder_root"] = os.environ["ANDROSCAN_RUN_FOLDER"]
    if os.environ.get("ANDROSCAN_LLM_PROVIDER"):
        merged["llm_provider"] = os.environ["ANDROSCAN_LLM_PROVIDER"].strip().lower()
    if os.environ.get("ANDROSCAN_CLOUD_MODEL"):
        merged["cloud_model"] = os.environ["ANDROSCAN_CLOUD_MODEL"].strip()

    return Config(
        ollama_base_url=merged["ollama_base_url"],
        ollama_timeout_sec=merged["ollama_timeout_sec"],
        ollama_model=merged["ollama_model"],
        ollama_temperature=merged["ollama_temperature"],
        ollama_num_predict=max(1, merged["ollama_num_predict"]),
        llm_provider=merged["llm_provider"],
        cloud_model=merged["cloud_model"],
        cloud_api_key=merged["cloud_api_key"],
        cloud_temperature=merged["cloud_temperature"],
        run_folder_root=merged["run_folder_root"],
        max_turns=max(1, merged["max_turns"]),
        max_hypotheses_per_report=max(0, merged["max_hypotheses_per_report"]),
        per_component_analysis=bool(merged.get("per_component_analysis", constants.PER_COMPONENT_ANALYSIS_DEFAULT)),
        apktool_cmd=merged["apktool_cmd"],
        jadx_cmd=merged["jadx_cmd"],
        section_rule_char=merged["section_rule_char"] or constants.SECTION_RULE_CHAR,
        section_rule_length=max(1, merged["section_rule_length"]),
    )
