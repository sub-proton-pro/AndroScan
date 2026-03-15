"""Persistent cache of successful skill results at apps/<app_id>/skill_results_cache.json.

Each entry: serial, run_folder, skill, params, result_text. Lookup by (skill, normalized params).
Only successful results are stored. When serving from cache, tag with "[cached from run <run_folder>]".
"""

import json
from pathlib import Path
from typing import Any, Optional


CACHE_FILENAME = "skill_results_cache.json"


def _cache_path(run_folder_root: Path, app_id: str) -> Path:
    """Path to skill_results_cache.json for this app_id."""
    return Path(run_folder_root) / app_id / CACHE_FILENAME


def _cache_key(skill: str, params: dict[str, Any]) -> str:
    """Stable key for (skill, params). Params normalized with sort_keys."""
    return skill + "\n" + json.dumps(params, sort_keys=True)


def lookup(
    run_folder_root: Path,
    app_id: str,
    skill: str,
    params: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Return cached entry if present: { run_folder, result_text }. Else None."""
    path = _cache_path(run_folder_root, app_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "by_key" not in data:
            return None
        key = _cache_key(skill, params or {})
        entry = data.get("by_key", {}).get(key)
        if not entry or not isinstance(entry, dict):
            return None
        run_folder = entry.get("run_folder")
        result_text = entry.get("result_text")
        if run_folder is None or result_text is None:
            return None
        return {"run_folder": str(run_folder), "result_text": str(result_text)}
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def store(
    run_folder_root: Path,
    app_id: str,
    run_folder_name: str,
    skill: str,
    params: dict[str, Any],
    result_text: str,
) -> None:
    """Store a successful skill result. Overwrites any existing entry for (skill, params)."""
    path = _cache_path(run_folder_root, app_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = _cache_key(skill, params or {})

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
            by_key = data.get("by_key")
            next_serial = data.get("next_serial", 0)
            if not isinstance(by_key, dict):
                by_key = {}
        except (json.JSONDecodeError, OSError):
            by_key = {}
            next_serial = 0
    else:
        by_key = {}
        next_serial = 0

    serial = next_serial
    next_serial += 1
    by_key[key] = {
        "serial": serial,
        "run_folder": run_folder_name,
        "skill": skill,
        "params": params or {},
        "result_text": result_text,
    }
    path.write_text(
        json.dumps({"by_key": by_key, "next_serial": next_serial}, indent=2),
        encoding="utf-8",
    )
