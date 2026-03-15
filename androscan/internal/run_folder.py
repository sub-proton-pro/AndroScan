"""Run folder creation: apps/<app_id>/<run_ts>/ with human-readable timestamp. Run metadata (run_meta.json)."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from androscan.config import Config

RUN_META_FILENAME = "run_meta.json"


def run_timestamp() -> str:
    """Human-readable run timestamp: DD-mon-YY_HH-MM-SS (e.g. 13-mar-26_01-30-52). Windows-safe (hyphens)."""
    now = datetime.now()
    return now.strftime("%d-%b-%y_%H-%M-%S").lower()


def run_folder_display_path(run_folder: Union[Path, str]) -> str:
    """Return a display string for the run folder with time as HH:MM:SS (colons). Does not change on-disk path."""
    p = Path(run_folder)
    name = p.name
    parts = name.rsplit("_", 1)
    if len(parts) == 2:
        time_part = parts[1]
        if time_part.count("-") == 2 and all(s.isdigit() for s in time_part.split("-")):
            display_name = parts[0] + "_" + time_part.replace("-", ":")
            return str(p.parent / display_name)
    return str(p)


def create_run_folder(app_id: str, config: Optional[Config] = None) -> Path:
    """Create apps/<app_id>/<run_ts>/ and return its path."""
    if config is None:
        from androscan.config import load_config
        config = load_config()
    root = Path(config.run_folder_root)
    folder = root / app_id / run_timestamp()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_run_meta(
    run_folder: Path,
    apk_path: str,
    started_at: datetime,
    finished_at: datetime,
    hypotheses_count: int = 0,
) -> None:
    """Write run_meta.json under run_folder. Schema: apk_path, app_id, run_timestamp, started_at, finished_at, hypotheses_count."""
    app_id = run_folder.parent.name
    run_timestamp_val = run_folder.name
    meta = {
        "apk_path": apk_path,
        "app_id": app_id,
        "run_timestamp": run_timestamp_val,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "hypotheses_count": hypotheses_count,
    }
    path = run_folder / RUN_META_FILENAME
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
