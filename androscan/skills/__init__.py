"""Skills layer: registry, discover, execute, list_llm_skills. All skills implement SkillMeta + execute()."""

import json
import importlib
from pathlib import Path
from typing import Any, Callable, Optional

from androscan.internal.skill_results_cache import lookup as cache_lookup, store as cache_store
from androscan.skills.base import SkillContext, SkillMeta, SkillResult
from androscan.skills.get_decompiled_class import resolve_component_ref

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
    "androscan.skills.app_env_check",
    "androscan.skills.build_exploit_command",
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
    return list_skills_by_tier("llm")


def list_skills_by_tier(tier: str) -> list[SkillMeta]:
    """Return metadata for all skills with the given tier (e.g. llm, exploit)."""
    return [meta for meta, _ in _REGISTRY.values() if meta.tier == tier]


def list_exploit_skills() -> list[SkillMeta]:
    """Return metadata for all skills with tier=exploit (for exploit verification)."""
    return list_skills_by_tier("exploit")


def _skill_cache_key(skill: str, params: dict[str, Any]) -> str:
    """Stable key for (skill, params). Must match skill_results_cache normalization."""
    return skill + "\n" + json.dumps(params, sort_keys=True)


def run_skills(
    skill_requests: list[Any],
    dossier_dict: dict[str, Any],
    run_folder: Any,
    context: SkillContext,
    memory_cache: Optional[dict[str, str]] = None,
) -> list[tuple[str, SkillResult]]:
    """Run requested skills (LLM format). Uses in-memory and disk cache for successful results.

    Returns list of (skill_name, SkillResult). Workflow should log failures and use result.text
    for prior_skill_results. memory_cache is keyed by _skill_cache_key(skill, params) -> result_text;
    pass the same dict across calls in one run to avoid reruns for same params.
    """
    run_folder_path = Path(context.run_folder)
    run_folder_root = run_folder_path.parent.parent
    app_id = run_folder_path.parent.name
    run_folder_name = run_folder_path.name
    mem = memory_cache if memory_cache is not None else {}

    results: list[tuple[str, SkillResult]] = []
    for req in skill_requests:
        skill_name = getattr(req, "skill", None) or (req.get("skill") if isinstance(req, dict) else None)
        params = getattr(req, "params", None) or (req.get("params") if isinstance(req, dict) else {}) or {}
        skill_name = skill_name or ""
        params = dict(params) if isinstance(params, dict) else {}

        ctx = SkillContext(
            config=context.config,
            run_folder=context.run_folder,
            dossier_dict=dossier_dict,
            apk_path=context.apk_path,
        )

        # For get_decompiled_class, key cache by resolved class name so per-component
        # analysis (e.g. exported_activities[0] in different contexts) gets correct entry.
        if skill_name == "get_decompiled_class" and "component_ref" in params and dossier_dict:
            resolved = resolve_component_ref(dossier_dict, params.get("component_ref") or "")
            params_for_cache = {"class_name": resolved} if resolved else params
        else:
            params_for_cache = params

        cache_key = _skill_cache_key(skill_name, params_for_cache)

        # In-memory cache (same run)
        if cache_key in mem:
            text = "[cached from run " + run_folder_name + "] " + mem[cache_key]
            results.append((skill_name, SkillResult(success=True, data=None, text=text)))
            continue

        # Disk cache
        cached = cache_lookup(run_folder_root, app_id, skill_name, params_for_cache)
        if cached:
            text = "[cached from run " + cached["run_folder"] + "] " + cached["result_text"]
            results.append((skill_name, SkillResult(success=True, data=None, text=text)))
            mem[cache_key] = cached["result_text"]
            continue

        result = execute(skill_name, params, ctx)
        if result.success:
            mem[cache_key] = result.text
            cache_store(run_folder_root, app_id, run_folder_name, skill_name, params_for_cache, result.text)
        results.append((skill_name, result))
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
    "list_exploit_skills",
    "list_llm_skills",
    "list_skills_by_tier",
    "register",
    "run_skills",
]
