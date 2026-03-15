"""Validate evidence_ref paths against the dossier (Phase 3: drop/flag invalid refs).
Supports normalization (strip) and resolving component names to dossier paths so LLM
output like 'SecretActivity' or 'ProfileInstallReceiver' is accepted."""

from typing import Any, Optional

VALID_KEYS = ("exported_activities", "exported_services", "exported_receivers", "exported_providers", "deep_links")
# For each key, the attribute on list items that holds the component class name
_KEY_TO_NAME_ATTR = {
    "exported_activities": "name",
    "exported_services": "name",
    "exported_receivers": "name",
    "exported_providers": "name",
    "deep_links": "component",
}


def validate_ref(dossier_dict: dict[str, Any], ref: str) -> bool:
    """Return True if ref is a valid dossier path (e.g. exported_activities[0]). Ref is normalized (strip)."""
    ref = (ref or "").strip() if isinstance(ref, str) else ""
    if not ref or "[" not in ref or not ref.endswith("]"):
        return False
    key, rest = ref.split("[", 1)
    key = key.strip()
    try:
        idx = int(rest.rstrip("]").strip())
    except ValueError:
        return False
    if key not in VALID_KEYS:
        return False
    lst = dossier_dict.get(key) or []
    return 0 <= idx < len(lst)


def resolve_ref(dossier_dict: dict[str, Any], ref: str) -> Optional[str]:
    """Normalize ref (strip). If already valid path, return normalized ref. Else try to resolve as component name to path.
    Returns dossier path like exported_activities[0] or None if not resolvable."""
    ref = (ref or "").strip() if isinstance(ref, str) else ""
    if not ref:
        return None
    if validate_ref(dossier_dict, ref):
        return ref  # already normalized by validate_ref's strip
    # Try to match ref as component name (exact or suffix, e.g. SecretActivity or com.example.SecretActivity)
    ref_lower = ref.lower()
    for key in VALID_KEYS:
        attr = _KEY_TO_NAME_ATTR.get(key, "name")
        lst = dossier_dict.get(key) or []
        for idx, item in enumerate(lst):
            if not isinstance(item, dict):
                continue
            name = (item.get(attr) or "").strip()
            if not name:
                continue
            if name == ref or name.endswith("." + ref) or name.lower() == ref_lower or name.lower().endswith("." + ref_lower):
                return f"{key}[{idx}]"
    return None
