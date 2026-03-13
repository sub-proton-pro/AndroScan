"""Load config from environment or defaults.

Environment variables:
- ANDROSCAN_OLLAMA_URL: Ollama API base URL (default http://localhost:11434)
- ANDROSCAN_OLLAMA_TIMEOUT: Timeout in seconds (default 120)
- ANDROSCAN_RUN_FOLDER: Root for apps/<app_id>/<run_ts>/ (default apps)
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Load via load_config()."""

    ollama_base_url: str
    ollama_timeout_sec: int
    run_folder_root: str

    @classmethod
    def default(cls) -> "Config":
        return cls(
            ollama_base_url="http://localhost:11434",
            ollama_timeout_sec=120,
            run_folder_root="apps",
        )


def load_config(config_path: Optional[str] = None) -> Config:
    """Load config from environment (and optional config file).

    For skeleton, only env is used. config_path is reserved for future file-based config.
    """
    _ = config_path  # reserved
    base_url = os.environ.get("ANDROSCAN_OLLAMA_URL", "http://localhost:11434")
    timeout_str = os.environ.get("ANDROSCAN_OLLAMA_TIMEOUT", "120")
    try:
        timeout = int(timeout_str)
    except ValueError:
        timeout = 120
    run_root = os.environ.get("ANDROSCAN_RUN_FOLDER", "apps")
    return Config(
        ollama_base_url=base_url.rstrip("/"),
        ollama_timeout_sec=max(1, timeout),
        run_folder_root=run_root,
    )
