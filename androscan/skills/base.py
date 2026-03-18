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
    """Result of executing a skill: success, structured data, human/LLM-readable text."""

    success: bool
    data: Any = None  # skill-specific structured output
    text: str = ""   # human/LLM-readable summary
