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
