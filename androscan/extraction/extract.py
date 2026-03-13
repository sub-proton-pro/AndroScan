"""Extract dossier from APK. Stub implementation returns minimal hardcoded dossier."""

from pathlib import Path

from androscan.internal.dossier import (
    ApkInfo,
    Dossier,
    ExportedActivity,
    IntentFilter,
    IntentFilterData,
)


def extract_dossier(apk_path: str) -> Dossier:
    """Extract a component dossier from an APK path.

    Stub: does not parse the APK. Verifies path exists (or is non-empty) and returns
    a minimal hardcoded dossier. Phase 3 will implement real manifest parsing.
    """
    path = Path(apk_path)
    if not apk_path.strip():
        raise ValueError("apk_path must be non-empty")
    # Optionally check path.exists() for clearer errors; for stub we allow missing path
    return _stub_dossier()


def _stub_dossier() -> Dossier:
    """Minimal dossier for skeleton: one activity, one permission."""
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
