"""Persistent observations store at apps/<app_id>/observations.json for LLM/tool use across runs."""

import json
from pathlib import Path
from typing import Any


OBSERVATIONS_FILENAME = "observations.json"


def _observations_path(run_folder_root: Path, app_id: str) -> Path:
    """Path to observations.json for this app_id."""
    return Path(run_folder_root) / app_id / OBSERVATIONS_FILENAME


def load_observations(run_folder_root: Path, app_id: str) -> list[dict[str, Any]]:
    """Load observations for app_id. Returns list of { run_ts?, source, text }. Empty list if missing."""
    path = _observations_path(run_folder_root, app_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "observations" in data:
            return list(data["observations"]) if isinstance(data["observations"], list) else []
        if isinstance(data, list):
            return list(data)
        return []
    except (json.JSONDecodeError, OSError):
        return []


def append_observations(
    run_folder_root: Path,
    app_id: str,
    new_entries: list[dict[str, Any]],
) -> None:
    """Append observation entries. Each entry should have run_ts (optional), source, text."""
    path = _observations_path(run_folder_root, app_id)
    existing = load_observations(run_folder_root, app_id)
    combined = existing + new_entries
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"observations": combined}, indent=2), encoding="utf-8")
