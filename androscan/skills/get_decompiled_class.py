"""LLM-requestable skill: return decompiled Java/Kotlin source for a class. Stub; Phase 3 uses jadx."""

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="get_decompiled_class",
    description="Decompiled Java/Kotlin source for the class named in the dossier component.",
    params_schema={"component_ref": "dossier path e.g. exported_activities[0]"},
    tier="llm",
)


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Stub: return placeholder. Phase 3 will decompile via jadx."""
    _ = context
    ref = params.get("component_ref", "unknown")
    return SkillResult(
        success=True,
        data=None,
        text=f"[Stub] Decompiled source for {ref} would appear here.",
    )
