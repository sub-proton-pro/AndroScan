"""LLM-requestable skill: return body of a specific method. Stub; Phase 3 uses jadx."""

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="get_decompiled_method",
    description="Body of a specific method in a class.",
    params_schema={"class_name": "fully qualified class name", "method_name": "method name"},
    tier="llm",
)


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Stub: return placeholder. Phase 3 will use jadx."""
    _ = context
    class_name = params.get("class_name", "unknown")
    method_name = params.get("method_name", "unknown")
    return SkillResult(
        success=True,
        data=None,
        text=f"[Stub] Method {class_name}.{method_name} body would appear here.",
    )
