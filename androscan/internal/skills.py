"""Skill registry: run skills requested by LLM. Stub implementations for skeleton."""

from typing import Any, Callable

# Registry: skill name -> callable(params: dict, dossier_dict: dict, run_folder: Path) -> str
SKILL_REGISTRY: dict[str, Callable[..., str]] = {}


def register_skill(name: str) -> Callable[[Callable[..., str]], Callable[..., str]]:
    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        SKILL_REGISTRY[name] = fn
        return fn
    return decorator


@register_skill("get_decompiled_class")
def get_decompiled_class(params: dict[str, Any], dossier_dict: dict[str, Any], run_folder: Any = None) -> str:
    """Stub: return placeholder. Phase 3 will decompile the class for component_ref."""
    _ = dossier_dict
    _ = run_folder
    ref = params.get("component_ref", "unknown")
    return f"[Stub] Decompiled source for {ref} would appear here."


def run_skills(skill_requests: list[Any], dossier_dict: dict[str, Any], run_folder: Any) -> list[str]:
    """Run requested skills and return list of result strings (in order)."""
    results = []
    for req in skill_requests:
        name = getattr(req, "skill", None) or (req.get("skill") if isinstance(req, dict) else None)
        params = getattr(req, "params", None) or (req.get("params") if isinstance(req, dict) else {}) or {}
        if name in SKILL_REGISTRY:
            results.append(SKILL_REGISTRY[name](params, dossier_dict, run_folder))
        else:
            results.append(f"[Stub] Unknown skill: {name}")
    return results
