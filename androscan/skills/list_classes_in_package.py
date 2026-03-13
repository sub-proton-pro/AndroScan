"""LLM-requestable skill: list class names under a package. Stub; Phase 3 uses jadx or dex listing."""

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="list_classes_in_package",
    description="Class names under a package prefix.",
    params_schema={"package_prefix": "e.g. com.example.app"},
    tier="llm",
)


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Stub: return placeholder. Phase 3 will list from decompiled output."""
    _ = context
    prefix = params.get("package_prefix", "unknown")
    return SkillResult(
        success=True,
        data=[],
        text=f"[Stub] Classes under package {prefix} would be listed here.",
    )
