"""Load vuln-module skills and signal profile matrix from JSON. Used by exploit verification skills."""

import json
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(__file__).resolve().parent / "vuln_module_skills_signals.json"

_cached: dict[str, Any] | None = None


def load_vuln_module_skills_signals() -> dict[str, Any]:
    """Load modules, profiles, and signal_type_metadata from vuln_module_skills_signals.json."""
    global _cached
    if _cached is not None:
        return _cached
    if not _CONFIG_PATH.exists():
        _cached = {"modules": {}, "signal_type_metadata": {}}
        return _cached
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        _cached = json.load(f)
    return _cached


def get_signal_type_metadata() -> dict[str, dict[str, Any]]:
    """Return signal_type_metadata (volatile, stub per type)."""
    data = load_vuln_module_skills_signals()
    return data.get("signal_type_metadata") or {}


def get_module_profiles(module_name: str) -> dict[str, Any]:
    """Return profiles dict for a vuln module, or empty dict if unknown."""
    data = load_vuln_module_skills_signals()
    modules = data.get("modules") or {}
    mod = modules.get(module_name) or {}
    return mod.get("profiles") or {}


def get_module_skills(module_name: str) -> list[str]:
    """Return list of skill names for a vuln module, or empty list if unknown."""
    data = load_vuln_module_skills_signals()
    modules = data.get("modules") or {}
    mod = modules.get(module_name) or {}
    return list(mod.get("skills") or [])
