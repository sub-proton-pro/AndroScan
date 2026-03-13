"""Run folder creation: apps/<app_id>/<run_ts>/ with human-readable timestamp."""

from datetime import datetime
from pathlib import Path
from typing import Optional

from androscan.config import Config


def run_timestamp() -> str:
    """Human-readable run timestamp: DD-mon-YY_HH-MM-SS (e.g. 13-mar-26_01-30-52)."""
    now = datetime.now()
    return now.strftime("%d-%b-%y_%H-%M-%S").lower()


def create_run_folder(app_id: str, config: Optional[Config] = None) -> Path:
    """Create apps/<app_id>/<run_ts>/ and return its path."""
    if config is None:
        from androscan.config import load_config
        config = load_config()
    root = Path(config.run_folder_root)
    folder = root / app_id / run_timestamp()
    folder.mkdir(parents=True, exist_ok=True)
    return folder
