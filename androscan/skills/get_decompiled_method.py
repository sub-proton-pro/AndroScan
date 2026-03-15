"""LLM-requestable skill: return body of a specific method in a class via jadx."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="get_decompiled_method",
    description="Body of a specific method in a class. Use class_name from dossier or prior decompilation.",
    params_schema={
        "class_name": "fully qualified class name",
        "method_name": "method name (e.g. onCreate, onReceive)",
    },
    tier="llm",
)


def _extract_method_bodies(source: str, method_name: str) -> str:
    """Extract one or more method definitions matching method_name from Java/Kotlin source.
    Returns concatenated signature + body for each match, or empty string if none found.
    """
    if not method_name or not method_name.strip():
        return ""
    pattern = re.escape(method_name.strip()) + r"\s*\("
    parts = []
    for m in re.finditer(pattern, source):
        start = m.start()
        line_start = source.rfind("\n", 0, start) + 1
        paren_start = m.end() - 1
        depth = 0
        i = paren_start
        while i < len(source):
            c = source[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if depth != 0:
            continue
        i += 1
        while i < len(source) and source[i] in " \t\n\r":
            i += 1
        if i >= len(source) or source[i] != "{":
            continue
        depth = 0
        while i < len(source):
            c = source[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            elif c == '"' or c == "'":
                end = source.find(c, i + 1)
                while end != -1 and end > 0 and source[end - 1] == "\\":
                    end = source.find(c, end + 1)
                if end == -1:
                    break
                i = end
            i += 1
        if depth != 0:
            continue
        body = source[line_start : i + 1].strip()
        if body:
            parts.append(body)
    return "\n\n---\n\n".join(parts) if parts else ""


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Decompile the class via jadx, then extract the requested method body. Checks jadx availability first."""
    class_name = (params.get("class_name") or "").strip()
    method_name = (params.get("method_name") or "").strip()
    if not class_name:
        return SkillResult(
            success=False,
            data=None,
            text="[get_decompiled_method] class_name is required.",
        )
    if not method_name:
        return SkillResult(
            success=False,
            data=None,
            text="[get_decompiled_method] method_name is required.",
        )

    apk_path = (context.apk_path or "").strip()
    if not apk_path:
        return SkillResult(success=False, data=None, text="[get_decompiled_method] apk_path not set in context.")
    apk_file = Path(apk_path)
    if not apk_file.exists():
        return SkillResult(success=False, data=None, text=f"[get_decompiled_method] APK not found: {apk_path}")

    jadx_cmd = getattr(context.config, "jadx_cmd", "jadx") or "jadx"
    if not shutil.which(jadx_cmd):
        return SkillResult(
            success=False,
            data=None,
            text="[get_decompiled_method] jadx not available. Install jadx and ensure it is on PATH.",
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
                    text=f"[get_decompiled_method] jadx failed for {class_name}: {stderr or 'unknown error'}",
                )
            if not out_file.exists():
                return SkillResult(
                    success=False,
                    data=None,
                    text=f"[get_decompiled_method] jadx did not produce output for {class_name}.",
                )
            content = out_file.read_text(encoding="utf-8", errors="replace")
            body = _extract_method_bodies(content, method_name)
            if not body:
                return SkillResult(
                    success=False,
                    data=None,
                    text=f"[get_decompiled_method] No method named {method_name!r} found in {class_name}.",
                )
            return SkillResult(success=True, data=body, text=body)
        finally:
            out_file.unlink(missing_ok=True)
    except subprocess.TimeoutExpired:
        return SkillResult(success=False, data=None, text="[get_decompiled_method] jadx timed out.")
    except OSError as e:
        return SkillResult(success=False, data=None, text=f"[get_decompiled_method] {e}.")
