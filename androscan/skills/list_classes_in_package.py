"""LLM-requestable skill: list class names under a package from manifest + dossier."""

from pathlib import Path
from typing import Any

from androscan.skills.base import (
    SkillContext,
    SkillMeta,
    SkillResult,
    _manifest_path_from_run_folder,
    _parse_manifest_all_components,
)

SKILL_META = SkillMeta(
    name="list_classes_in_package",
    description="List all declared class names under a package prefix (from manifest and dossier). Useful for discovering helper classes.",
    params_schema={"package_prefix": "e.g. com.example.app"},
    tier="llm",
)


def _collect_dossier_names(dossier_dict: dict[str, Any]) -> list[str]:
    """Collect all component class names from the dossier."""
    names: list[str] = []
    for list_key, attr in [
        ("exported_activities", "name"),
        ("exported_services", "name"),
        ("exported_receivers", "name"),
        ("exported_providers", "name"),
        ("deep_links", "component"),
    ]:
        for item in dossier_dict.get(list_key) or []:
            n = (item.get(attr) or "").strip() if isinstance(item, dict) else ""
            if n:
                names.append(n)
    return names


def execute(params: dict, context: SkillContext) -> SkillResult:
    """List all classes under the given package prefix from the manifest and dossier."""
    prefix = (params.get("package_prefix") or "").strip()
    if not prefix:
        return SkillResult(
            success=False,
            data=None,
            text="[list_classes_in_package] package_prefix is required.",
        )

    all_names: set[str] = set()

    run_folder = Path(context.run_folder) if context.run_folder else None
    manifest = _manifest_path_from_run_folder(run_folder)
    if manifest:
        for name in _parse_manifest_all_components(manifest):
            all_names.add(name)

    dossier_dict = context.dossier_dict or {}
    for name in _collect_dossier_names(dossier_dict):
        all_names.add(name)

    matched = sorted(n for n in all_names if n.startswith(prefix + ".") or n.startswith(prefix + "$"))

    if not matched:
        return SkillResult(
            success=True,
            data=[],
            text=f"[list_classes_in_package] No classes found under '{prefix}'. The package prefix may differ from the applicationId — try a shorter prefix.",
        )

    lines = [f"[list_classes_in_package] {len(matched)} class(es) under '{prefix}':"]
    for name in matched:
        lines.append(f"  - {name}")
    text = "\n".join(lines)
    return SkillResult(success=True, data=matched, text=text)
