"""Skills layer: registry, discover, execute, list_llm_skills. All skills implement SkillMeta + execute()."""

import importlib
from typing import Any, Callable

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

# Registry: name -> (SkillMeta, execute_fn)
_REGISTRY: dict[str, tuple[SkillMeta, Callable[[dict, SkillContext], SkillResult]]] = {}

# Known skill modules to discover
_SKILL_MODULES = [
    "androscan.skills.extract_manifest",
    "androscan.skills.prepare_dossier",
    "androscan.skills.generate_report",
    "androscan.skills.get_decompiled_class",
    "androscan.skills.get_decompiled_method",
    "androscan.skills.list_classes_in_package",
]


def discover() -> None:
    """Import skill modules and register any that export SKILL_META and execute."""
    for mod_name in _SKILL_MODULES:
        try:
            mod = importlib.import_module(mod_name)
            meta = getattr(mod, "SKILL_META", None)
            execute_fn = getattr(mod, "execute", None)
            if meta is not None and execute_fn is not None and isinstance(meta, SkillMeta):
                register(meta, execute_fn)
        except Exception:
            pass


def register(meta: SkillMeta, execute_fn: Callable[[dict, SkillContext], SkillResult]) -> None:
    """Register a skill by its metadata and execute function."""
    _REGISTRY[meta.name] = (meta, execute_fn)


def execute(name: str, params: dict[str, Any], context: SkillContext) -> SkillResult:
    """Run a skill by name; return SkillResult. Unknown skill returns failure result."""
    if name not in _REGISTRY:
        return SkillResult(success=False, data=None, text=f"[Unknown skill: {name}]")
    _, execute_fn = _REGISTRY[name]
    return execute_fn(params or {}, context)


def list_llm_skills() -> list[SkillMeta]:
    """Return metadata for all skills with tier=llm (for prompt catalog)."""
    return [meta for meta, _ in _REGISTRY.values() if meta.tier == "llm"]


def run_skills(skill_requests: list[Any], dossier_dict: dict[str, Any], run_folder: Any, context: SkillContext) -> list[str]:
    """Run requested skills (LLM format) and return list of result text strings. Backward compat for workflow."""
    results = []
    for req in skill_requests:
        skill_name = getattr(req, "skill", None) or (req.get("skill") if isinstance(req, dict) else None)
        params = getattr(req, "params", None) or (req.get("params") if isinstance(req, dict) else {}) or {}
        ctx = SkillContext(
            config=context.config,
            run_folder=context.run_folder,
            dossier_dict=dossier_dict,
            apk_path=context.apk_path,
        )
        result = execute(skill_name or "", params, ctx)
        results.append(result.text)
    return results


# Auto-discover on first import
if not _REGISTRY:
    discover()

__all__ = [
    "SkillContext",
    "SkillMeta",
    "SkillResult",
    "discover",
    "execute",
    "list_llm_skills",
    "register",
    "run_skills",
]
