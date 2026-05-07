"""Validate evidence_ref paths against the dossier (Phase 3: drop/flag invalid refs).
Supports normalization (strip) and resolving component names to dossier paths so LLM
output like 'SecretActivity', 'ProfileInstallReceiver', or 'SecretActivity.kt' is accepted."""

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
# Map dossier list-keys to the human-friendly component_type label used in reports.
_KEY_TO_COMPONENT_TYPE = {
    "exported_activities": "activity",
    "exported_services": "service",
    "exported_receivers": "receiver",
    "exported_providers": "content_provider",
    "deep_links": "deep_link",
}
# Common Android source-file suffixes that LLMs (especially thinking-mode models like Gemma 4)
# sometimes attach to evidence_refs (e.g. "SecretActivity.kt"). Stripped before name-matching
# so the resolver can map them to dossier paths. Match is suffix-only, case-insensitive.
_SOURCE_FILE_EXTENSIONS = (".kt", ".java", ".smali", ".dex", ".xml")


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
    Returns dossier path like exported_activities[0] or None if not resolvable.

    Accepts component names with common Android source-file suffixes (e.g. "SecretActivity.kt"
    is treated as "SecretActivity"). This handles thinking-mode LLMs that sometimes attach
    file extensions when paraphrasing component refs (notably Gemma 4 during consolidation)."""
    ref = (ref or "").strip() if isinstance(ref, str) else ""
    if not ref:
        return None
    if validate_ref(dossier_dict, ref):
        return ref  # already normalized by validate_ref's strip
    # Strip a single trailing source-file extension (case-insensitive) before name matching.
    # E.g. "SecretActivity.kt" -> "SecretActivity"; valid dossier paths never end with these
    # extensions so this is a no-op for the common case.
    ref_lower_check = ref.lower()
    for ext in _SOURCE_FILE_EXTENSIONS:
        if ref_lower_check.endswith(ext) and len(ref) > len(ext):
            ref = ref[: -len(ext)]
            break
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


def lookup_component_meta(dossier_dict: dict[str, Any], ref: str) -> Optional[tuple[str, str]]:
    """Resolve a canonical evidence_ref to ``(component_type, component_name)``.

    Returns ``None`` if the ref is not a valid dossier path. Used to backfill
    consolidation-LLM output where models (notably Gemma 4) sometimes leave
    ``component_type`` / ``component_name`` blank when merging findings.
    """
    if not validate_ref(dossier_dict, ref):
        return None
    key, rest = ref.split("[", 1)
    key = key.strip()
    idx = int(rest.rstrip("]").strip())
    item = (dossier_dict.get(key) or [])[idx]
    if not isinstance(item, dict):
        return None
    name_attr = _KEY_TO_NAME_ATTR.get(key, "name")
    name = (item.get(name_attr) or "").strip()
    if not name:
        return None
    component_type = _KEY_TO_COMPONENT_TYPE.get(key, key)
    return component_type, name
