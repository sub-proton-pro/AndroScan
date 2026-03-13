"""Load config from global_config.yaml and environment.

Merge order: defaults (constants) -> global_config.yaml (if present) -> env vars.
Env vars override file. Pass config file path via --config or use default search paths.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from androscan import constants


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Load via load_config()."""

    ollama_base_url: str
    ollama_timeout_sec: int
    ollama_model: str
    run_folder_root: str
    max_turns: int
    max_hypotheses_per_report: int
    apktool_cmd: str
    jadx_cmd: str
    section_rule_char: str
    section_rule_length: int

    @classmethod
    def default(cls) -> "Config":
        return cls(
            ollama_base_url="http://localhost:11434",
            ollama_timeout_sec=120,
            ollama_model="qwen3.5:35b",
            run_folder_root="apps",
            max_turns=constants.MAX_TURNS_DEFAULT,
            max_hypotheses_per_report=constants.MAX_HYPOTHESES_PER_REPORT_DEFAULT,
            apktool_cmd=constants.APKTOOL_CMD_DEFAULT,
            jadx_cmd=constants.JADX_CMD_DEFAULT,
            section_rule_char=constants.SECTION_RULE_CHAR,
            section_rule_length=constants.SECTION_RULE_LENGTH,
        )

    @property
    def section_rule(self) -> str:
        return self.section_rule_char * self.section_rule_length


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file; return empty dict if missing or invalid."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except (yaml.YAMLError, OSError):
        return {}


def _merge_from_yaml(config_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract flat keys from nested YAML for Config. Uses constants as defaults."""
    out: dict[str, Any] = {}
    ollama = config_dict.get("ollama") or {}
    paths = config_dict.get("paths") or {}
    workflow = config_dict.get("workflow") or {}
    output = config_dict.get("output") or {}
    out["ollama_base_url"] = (ollama.get("base_url") or "").strip().rstrip("/") or "http://localhost:11434"
    out["ollama_timeout_sec"] = int(ollama.get("timeout_sec", 120)) if ollama.get("timeout_sec") is not None else 120
    out["ollama_model"] = ollama.get("model") or "qwen3.5:35b"
    out["run_folder_root"] = paths.get("run_folder_root") or "apps"
    out["max_turns"] = workflow.get("max_turns") if workflow.get("max_turns") is not None else constants.MAX_TURNS_DEFAULT
    out["max_hypotheses_per_report"] = workflow.get("max_hypotheses_per_report") or constants.MAX_HYPOTHESES_PER_REPORT_DEFAULT
    out["apktool_cmd"] = paths.get("apktool_cmd") or constants.APKTOOL_CMD_DEFAULT
    out["jadx_cmd"] = paths.get("jadx_cmd") or constants.JADX_CMD_DEFAULT
    out["section_rule_char"] = output.get("section_rule_char") or constants.SECTION_RULE_CHAR
    out["section_rule_length"] = int(output.get("section_rule_length", constants.SECTION_RULE_LENGTH))
    return out


def load_config(config_path: Optional[str] = None) -> Config:
    """Load config: defaults -> global_config.yaml (if found) -> env overrides.

    config_path: explicit path to YAML file. If None, search:
      - cwd / global_config.yaml
      - cwd / config / global_config.yaml
    Env: ANDROSCAN_OLLAMA_URL, ANDROSCAN_OLLAMA_TIMEOUT, ANDROSCAN_RUN_FOLDER, etc.
    """
    defaults = Config.default()
    yaml_data: dict[str, Any] = {}

    if config_path:
        yaml_data = _load_yaml(Path(config_path))
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
            pass
    if os.environ.get("ANDROSCAN_RUN_FOLDER"):
        merged["run_folder_root"] = os.environ["ANDROSCAN_RUN_FOLDER"]

    return Config(
        ollama_base_url=merged["ollama_base_url"],
        ollama_timeout_sec=merged["ollama_timeout_sec"],
        ollama_model=merged["ollama_model"],
        run_folder_root=merged["run_folder_root"],
        max_turns=max(1, merged["max_turns"]),
        max_hypotheses_per_report=max(0, merged["max_hypotheses_per_report"]),
        apktool_cmd=merged["apktool_cmd"],
        jadx_cmd=merged["jadx_cmd"],
        section_rule_char=merged["section_rule_char"] or constants.SECTION_RULE_CHAR,
        section_rule_length=max(1, merged["section_rule_length"]),
    )
