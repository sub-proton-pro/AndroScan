"""Resolve app_id from APK without creating persistent files at the project root.

Strategy (tiered):
1. Scan existing apps/*/app_meta.json for matching APK SHA-256 → instant cache hit.
2. Extract to system temp (tempfile.mkdtemp) → parse manifest → derive app_id.
   Caller moves temp extraction to apps/<app_id>/extracted_apk/ and cleans up.
"""

import json
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from androscan.config import Config
from androscan.internal.app_meta import compute_apk_sha256
from androscan.internal.dossier import Dossier, app_id_from_dossier
from androscan.skills import SkillContext, execute


def resolve_app_id(
    apk_path: str,
    config: Config,
) -> tuple[str, Dossier, Optional[Path], Optional[str]]:
    """Resolve app_id from APK path.

    Returns (app_id, dossier, temp_extraction_base, apk_sha256).

    - temp_extraction_base: set only when a fresh extraction produced files in
      ``<temp_base>/extracted_apk/``.  Caller should ``shutil.move`` that
      directory to ``apps/<app_id>/extracted_apk/`` and then
      ``shutil.rmtree(temp_base)``.  None when cache was used or no files
      were produced (e.g. APK missing).
    - apk_sha256: hex digest when APK exists, else None.
    """
    apk_file = Path(apk_path)
    apk_hash: Optional[str] = None

    if apk_file.exists():
        apk_hash = compute_apk_sha256(apk_file)
        run_root = Path(config.run_folder_root)
        if run_root.is_dir():
            for meta_file in sorted(run_root.glob("*/app_meta.json")):
                try:
                    raw = json.loads(meta_file.read_text(encoding="utf-8"))
                    if (
                        isinstance(raw, dict)
                        and raw.get("apk_sha256") == apk_hash
                        and raw.get("dossier")
                    ):
                        app_id = meta_file.parent.name
                        dossier = Dossier.from_dict(raw["dossier"])
                        return (app_id, dossier, None, apk_hash)
                except (json.JSONDecodeError, OSError, KeyError, TypeError):
                    continue

    temp_base = Path(tempfile.mkdtemp(prefix="androscan_"))
    try:
        fake_run = temp_base / "run"
        fake_run.mkdir()
        ctx = SkillContext(config=config, run_folder=fake_run, apk_path=apk_path)

        manifest_result = execute("extract_manifest", {}, ctx)
        if not manifest_result.success:
            raise RuntimeError(f"extract_manifest failed: {manifest_result.text}")

        dossier_result = execute("prepare_dossier", {"manifest": manifest_result.data}, ctx)
        if not dossier_result.success:
            raise RuntimeError(f"prepare_dossier failed: {dossier_result.text}")

        dossier = Dossier.from_dict(dossier_result.data)
        app_id = app_id_from_dossier(dossier) or "unknown_app"

        if apk_hash is None and apk_file.exists():
            apk_hash = compute_apk_sha256(apk_file)

        temp_extracted = temp_base / "extracted_apk"
        if temp_extracted.is_dir() and any(temp_extracted.iterdir()):
            return (app_id, dossier, temp_base, apk_hash)

        shutil.rmtree(temp_base, ignore_errors=True)
        return (app_id, dossier, None, apk_hash)
    except Exception:
        shutil.rmtree(temp_base, ignore_errors=True)
        raise
