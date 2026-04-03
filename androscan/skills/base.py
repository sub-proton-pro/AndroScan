"""Skill contract: SkillMeta, SkillContext, SkillResult. Used by all skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional


@dataclass(frozen=True)
class SkillMeta:
    """Metadata for a skill: name, description, params schema, tier."""

    name: str
    description: str
    params_schema: dict[str, Any]  # e.g. {"component_ref": "dossier path", ...}
    tier: Literal["pipeline", "llm", "exploit"]


@dataclass
class SkillContext:
    """Context passed to every skill: config, run folder, dossier, apk path."""

    config: Any  # androscan.config.Config; avoid circular import
    run_folder: Path
    dossier_dict: Optional[dict[str, Any]] = None
    apk_path: Optional[str] = None


@dataclass
class SkillResult:
    """Result of executing a skill: success, structured data, human/LLM-readable text.

    For exploit-tier skills, optional log_summary and spinner_text are used by
    orchestration to write a short line to run.log and to drive spinner/UI text.
    """

    success: bool
    data: Any = None  # skill-specific structured output
    text: str = ""   # human/LLM-readable summary
    log_summary: Optional[str] = None  # short line for run.log (exploit steps)
    spinner_text: Optional[str] = None  # spinner/UI label (exploit steps)


def resolve_short_class_name(short_name: str, dossier_dict: Optional[dict[str, Any]]) -> Optional[str]:
    """Try to resolve a short (unqualified) class name to a fully qualified one using the dossier.

    Searches exported components for a name ending with the short name,
    then falls back to prepending the package from apk_info.
    Returns None if resolution is not possible.
    """
    if not short_name or "." in short_name:
        return short_name or None
    if not dossier_dict:
        return None

    component_lists = [
        ("exported_activities", "name"),
        ("exported_services", "name"),
        ("exported_receivers", "name"),
        ("exported_providers", "name"),
        ("deep_links", "component"),
    ]
    for list_key, attr_name in component_lists:
        for item in dossier_dict.get(list_key) or []:
            fqcn = (item.get(attr_name) or "") if isinstance(item, dict) else ""
            if fqcn.endswith(f".{short_name}"):
                return fqcn

    package = ""
    apk_info = dossier_dict.get("apk_info")
    if isinstance(apk_info, dict):
        package = (apk_info.get("package") or "").strip()
    if package:
        return f"{package}.{short_name}"

    return None
