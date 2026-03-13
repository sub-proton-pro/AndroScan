"""Pipeline skill: build component dossier from manifest data. Uses real parsed manifest or stub."""

from androscan.extraction.manifest_parser import build_dossier_dict_from_parsed
from androscan.internal.dossier import (
    ApkInfo,
    Dossier,
    ExportedActivity,
    IntentFilter,
    IntentFilterData,
)
from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="prepare_dossier",
    description="Build structured component dossier from manifest data.",
    params_schema={"manifest": "dict from extract_manifest"},
    tier="pipeline",
)


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Build dossier from manifest. If manifest is stub or missing, return hardcoded dossier dict."""
    _ = context
    manifest = params.get("manifest") or {}
    if manifest.get("stub") or not manifest.get("package"):
        dossier = _stub_dossier()
        dossier_dict = dossier.to_dict()
        return SkillResult(success=True, data=dossier_dict, text="Dossier prepared (stub).")

    dossier_dict = build_dossier_dict_from_parsed(manifest)
    if not dossier_dict:
        dossier = _stub_dossier()
        dossier_dict = dossier.to_dict()
        return SkillResult(success=True, data=dossier_dict, text="Dossier prepared (stub).")
    return SkillResult(success=True, data=dossier_dict, text="Dossier prepared.")


def _stub_dossier() -> Dossier:
    """Minimal dossier for skeleton; matches extraction stub."""
    return Dossier(
        apk_info=ApkInfo(
            package="com.example.app",
            version_name="1.0",
            version_code=1,
            min_sdk=21,
            target_sdk=30,
        ),
        permissions=["android.permission.INTERNET"],
        exported_activities=[
            ExportedActivity(
                name="com.example.app.MainActivity",
                exported=True,
                intent_filters=[
                    IntentFilter(
                        action=["android.intent.action.MAIN"],
                        category=["android.intent.category.LAUNCHER"],
                    ),
                    IntentFilter(
                        action=["android.intent.action.VIEW"],
                        category=["android.intent.category.DEFAULT", "android.intent.category.BROWSABLE"],
                        data=[
                            IntentFilterData(scheme="https", host="example.com", pathPrefix="/open"),
                        ],
                    ),
                ],
            ),
        ],
        exported_services=[],
        exported_receivers=[],
        exported_providers=[],
        deep_links=[],
    )
