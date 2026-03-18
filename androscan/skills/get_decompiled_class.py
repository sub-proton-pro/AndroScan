"""LLM-requestable skill: return decompiled Java/Kotlin source for a class via jadx."""

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="get_decompiled_class",
    description="Decompiled Java/Kotlin source for a class. Accepts dossier path (e.g. exported_activities[0]) or full class name (e.g. com.example.WeakBankLab).",
    params_schema={"component_ref": "dossier path e.g. exported_activities[0], or full class name e.g. com.example.MyClass"},
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


def _is_dossier_path(ref: str) -> bool:
    """True if ref looks like a dossier path (e.g. exported_activities[0])."""
    return bool(ref and "[" in ref and ref.endswith("]"))


def resolve_component_ref(dossier_dict: dict[str, Any], component_ref: str) -> Optional[str]:
    """Resolve component_ref to a stable class name for cache keys.

    If component_ref is a dossier path (e.g. exported_activities[0]), resolve via dossier.
    Otherwise treat as class name and return stripped, or None if empty.
    """
    ref = (component_ref or "").strip()
    if not ref:
        return None
    if _is_dossier_path(ref):
        return _class_name_from_ref(dossier_dict, ref)
    return ref


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Decompile the class for the given component_ref via jadx. component_ref may be a dossier path or full class name."""
    component_ref = (params.get("component_ref") or "").strip()
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

    if _is_dossier_path(component_ref):
        class_name = _class_name_from_ref(dossier_dict, component_ref)
    else:
        class_name = component_ref if component_ref else None
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
