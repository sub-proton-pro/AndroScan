"""Pipeline skill: extract manifest from APK via apktool; parse AndroidManifest.xml. Falls back to stub on failure."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from androscan.extraction.manifest_parser import parse_manifest_xml
from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="extract_manifest",
    description="Extract AndroidManifest data from the APK (decode and parse).",
    params_schema={},
    tier="pipeline",
)


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Decode APK with apktool (manifest only), parse AndroidManifest.xml. On failure return stub manifest."""
    _ = params
    apk_path = (context.apk_path or "").strip()
    if not apk_path:
        return SkillResult(success=False, data=None, text="apk_path is required")

    apktool_cmd = getattr(context.config, "apktool_cmd", "apktool") or "apktool"
    if not shutil.which(apktool_cmd):
        return SkillResult(
            success=True,
            data={"stub": True, "apk_path": apk_path, "reason": "apktool not found"},
            text="[Fallback] apktool not available; using stub manifest.",
        )

    apk_file = Path(apk_path)
    if not apk_file.exists():
        return SkillResult(
            success=True,
            data={"stub": True, "apk_path": apk_path, "reason": "apk not found"},
            text="[Fallback] APK file not found; using stub manifest.",
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
