"""Extract dossier from APK. Delegates to skills layer; backward-compat API returns Dossier."""

from pathlib import Path

from androscan.config import load_config
from androscan.internal.dossier import Dossier
from androscan.skills import SkillContext, execute


def extract_dossier(apk_path: str) -> Dossier:
    """Extract a component dossier from an APK path.

    Delegates to pipeline skills extract_manifest and prepare_dossier; returns Dossier for backward compat.
    """
    if not (apk_path or "").strip():
        raise ValueError("apk_path must be non-empty")
    config = load_config()
    ctx = SkillContext(config=config, run_folder=Path("."), apk_path=apk_path)
    manifest_result = execute("extract_manifest", {}, ctx)
    if not manifest_result.success:
        raise RuntimeError(f"extract_manifest failed: {manifest_result.text}")
    dossier_result = execute("prepare_dossier", {"manifest": manifest_result.data}, ctx)
    if not dossier_result.success:
        raise RuntimeError(f"prepare_dossier failed: {dossier_result.text}")
    return Dossier.from_dict(dossier_result.data)
