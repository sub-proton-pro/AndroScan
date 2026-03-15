"""Validate evidence_ref paths against the dossier (Phase 3: drop/flag invalid refs)."""

from typing import Any


def validate_ref(dossier_dict: dict[str, Any], ref: str) -> bool:
    """Return True if ref is a valid dossier path (e.g. exported_activities[0])."""
    if not ref or not isinstance(ref, str) or "[" not in ref or not ref.endswith("]"):
        return False
    key, rest = ref.split("[", 1)
    try:
        idx = int(rest.rstrip("]"))
    except ValueError:
        return False
    valid_keys = ("exported_activities", "exported_services", "exported_receivers", "exported_providers", "deep_links")
    if key not in valid_keys:
        return False
    lst = dossier_dict.get(key) or []
    return 0 <= idx < len(lst)
