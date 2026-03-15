"""LLM-requestable skill: return decompiled Java/Kotlin source for a class via jadx."""

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="get_decompiled_class",
    description="Decompiled Java/Kotlin source for the class named in the dossier component.",
    params_schema={"component_ref": "dossier path e.g. exported_activities[0]"},
    tier="llm",
)


def _class_name_from_ref(dossier_dict: dict[str, Any], ref: str) -> Optional[str]:
    """Resolve component_ref (e.g. exported_activities[0]) to component class name from dossier dict."""
    if not ref or not isinstance(ref, str) or "[" not in ref or not ref.endswith("]"):
        return None
    key, rest = ref.split("[", 1)
    try:
        idx = int(rest.rstrip("]"))
    except ValueError:
        return None
    for list_key, attr_name in [
        ("exported_activities", "name"),
        ("exported_services", "name"),
        ("exported_receivers", "name"),
        ("exported_providers", "name"),
        ("deep_links", "component"),
    ]:
        if key == list_key:
            lst = dossier_dict.get(list_key) or []
            if 0 <= idx < len(lst):
                item = lst[idx]
                if isinstance(item, dict):
                    return item.get(attr_name) or None
            return None
    return None


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Decompile the class for the given component_ref via jadx. Checks jadx availability first."""
    component_ref = params.get("component_ref") or ""
    dossier_dict = context.dossier_dict or {}
    apk_path = (context.apk_path or "").strip()
    if not apk_path:
        return SkillResult(success=False, data=None, text="[get_decompiled_class] apk_path not set in context.")
    apk_file = Path(apk_path)
    if not apk_file.exists():
        return SkillResult(success=False, data=None, text=f"[get_decompiled_class] APK not found: {apk_path}")

    jadx_cmd = getattr(context.config, "jadx_cmd", "jadx") or "jadx"
    if not shutil.which(jadx_cmd):
        return SkillResult(
            success=False,
            data=None,
            text="[get_decompiled_class] jadx not available. Install jadx and ensure it is on PATH.",
        )

    class_name = _class_name_from_ref(dossier_dict, component_ref)
    if not class_name:
        return SkillResult(
            success=False,
            data=None,
            text=f"[get_decompiled_class] Invalid or unresolved component_ref: {component_ref!r}",
        )

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".java", delete=False) as f:
            out_file = Path(f.name)
        try:
            proc = subprocess.run(
                [
                    jadx_cmd,
                    "--single-class", class_name,
                    "--single-class-output", str(out_file),
                    str(apk_file),
                ],
                capture_output=True,
                timeout=120,
                text=True,
            )
            if proc.returncode != 0:
                stderr = (proc.stderr or proc.stdout or "").strip()
                return SkillResult(
                    success=False,
                    data=None,
                    text=f"[get_decompiled_class] jadx failed for {class_name}: {stderr or 'unknown error'}",
                )
            if not out_file.exists():
                return SkillResult(
                    success=False,
                    data=None,
                    text=f"[get_decompiled_class] jadx did not produce output for {class_name}.",
                )
            content = out_file.read_text(encoding="utf-8", errors="replace")
            return SkillResult(success=True, data=content, text=content)
        finally:
            out_file.unlink(missing_ok=True)
    except subprocess.TimeoutExpired:
        return SkillResult(success=False, data=None, text="[get_decompiled_class] jadx timed out.")
    except OSError as e:
        return SkillResult(success=False, data=None, text=f"[get_decompiled_class] {e}.")
