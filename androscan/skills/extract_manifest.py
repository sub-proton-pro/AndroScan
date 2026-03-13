"""Pipeline skill: extract manifest from APK. Stub returns minimal placeholder; Phase 3 uses apktool."""

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="extract_manifest",
    description="Extract AndroidManifest data from the APK (decode and parse).",
    params_schema={},
    tier="pipeline",
)


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Extract manifest; stub returns placeholder. Phase 3: apktool decode + parse XML."""
    _ = params
    if not (context.apk_path or "").strip():
        return SkillResult(success=False, data=None, text="apk_path is required")
    # Stub: no real extraction; return minimal dict for prepare_dossier to consume
    stub_manifest = {"stub": True, "apk_path": context.apk_path}
    return SkillResult(success=True, data=stub_manifest, text="[Stub] Manifest extracted.")
