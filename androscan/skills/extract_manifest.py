"""Pipeline skill: extract manifest from APK via apktool; parse AndroidManifest.xml. Falls back to stub on failure."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from androscan.extraction.manifest_parser import parse_manifest_xml
from androscan.internal.app_meta import (
    compute_apk_sha256,
    extracted_apk_path,
    load_app_meta,
)
from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="extract_manifest",
    description="Extract AndroidManifest data from the APK (decode and parse).",
    params_schema={},
    tier="pipeline",
)


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Decode APK with apktool (manifest only), parse AndroidManifest.xml. On failure return stub manifest.
    Uses apps/<app_id>/extracted_apk/ when run_folder is set; reuses if app_meta.apk_sha256 matches current APK hash.
    """
    _ = params
    apk_path = (context.apk_path or "").strip()
    if not apk_path:
        return SkillResult(success=False, data=None, text="apk_path is required")

    apk_file = Path(apk_path)
    if not apk_file.exists():
        return SkillResult(
            success=True,
            data={"stub": True, "apk_path": apk_path, "reason": "apk not found"},
            text="[Fallback] APK file not found; using stub manifest.",
        )

    apktool_cmd = getattr(context.config, "apktool_cmd", "apktool") or "apktool"
    if not shutil.which(apktool_cmd):
        return SkillResult(
            success=True,
            data={"stub": True, "apk_path": apk_path, "reason": "apktool not found"},
            text="[Fallback] apktool not available; using stub manifest.",
        )

    app_id_root = getattr(context.run_folder, "parent", None) if context.run_folder else None
    if app_id_root is not None:
        app_id_root = Path(app_id_root)
        current_hash = compute_apk_sha256(apk_file)
        meta = load_app_meta(app_id_root)
        extracted_dir = extracted_apk_path(app_id_root)
        manifest_xml = extracted_dir / "AndroidManifest.xml"
        if meta and meta.get("apk_sha256") == current_hash and manifest_xml.exists():
            parsed = parse_manifest_xml(manifest_xml)
            if not parsed.get("stub"):
                data = {**parsed, "apk_sha256": current_hash}
                return SkillResult(success=True, data=data, text="Manifest extracted (from cache).")

        try:
            extracted_dir.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                [apktool_cmd, "d", str(apk_file), "-o", str(extracted_dir), "-f", "--only-manifest"],
                capture_output=True,
                timeout=120,
                text=True,
            )
            if proc.returncode != 0:
                return SkillResult(
                    success=True,
                    data={"stub": True, "apk_path": apk_path, "reason": "apktool decode failed"},
                    text=f"[Fallback] apktool failed: {proc.stderr or proc.stdout or 'unknown'}; using stub.",
                )
            parsed = parse_manifest_xml(manifest_xml)
            if parsed.get("stub"):
                return SkillResult(
                    success=True,
                    data={"stub": True, "apk_path": apk_path},
                    text="[Fallback] Could not parse manifest; using stub.",
                )
            data = {**parsed, "apk_sha256": current_hash}
            return SkillResult(success=True, data=data, text="Manifest extracted.")
        except (subprocess.TimeoutExpired, OSError) as e:
            return SkillResult(
                success=True,
                data={"stub": True, "apk_path": apk_path, "reason": str(e)},
                text=f"[Fallback] {e}; using stub manifest.",
            )

    try:
        with tempfile.TemporaryDirectory(prefix="androscan_apk_") as tmp:
            decode_dir = Path(tmp)
            proc = subprocess.run(
                [apktool_cmd, "d", str(apk_file), "-o", str(decode_dir), "-f", "--only-manifest"],
                capture_output=True,
                timeout=120,
                text=True,
            )
            if proc.returncode != 0:
                return SkillResult(
                    success=True,
                    data={"stub": True, "apk_path": apk_path, "reason": "apktool decode failed"},
                    text=f"[Fallback] apktool failed: {proc.stderr or proc.stdout or 'unknown'}; using stub.",
                )
            manifest_path = decode_dir / "AndroidManifest.xml"
            parsed = parse_manifest_xml(manifest_path)
            if parsed.get("stub"):
                return SkillResult(
                    success=True,
                    data={"stub": True, "apk_path": apk_path},
                    text="[Fallback] Could not parse manifest; using stub.",
                )
            return SkillResult(success=True, data=parsed, text="Manifest extracted.")
    except (subprocess.TimeoutExpired, OSError) as e:
        return SkillResult(
            success=True,
            data={"stub": True, "apk_path": apk_path, "reason": str(e)},
            text=f"[Fallback] {e}; using stub manifest.",
        )
