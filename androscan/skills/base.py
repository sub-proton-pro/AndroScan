"""Skill contract: SkillMeta, SkillContext, SkillResult. Used by all skills."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional


@dataclass(frozen=True)
class SkillMeta:
    """Metadata for a skill: name, description, params schema, tier."""

    name: str
    description: str
    params_schema: dict[str, Any]  # e.g. {"component_ref": "dossier path", ...}
    tier: Literal["pipeline", "llm", "exploit"]


@dataclass
class SkillContext:
    """Context passed to every skill: config, run folder, dossier, apk path."""

    config: Any  # androscan.config.Config; avoid circular import
    run_folder: Path
    dossier_dict: Optional[dict[str, Any]] = None
    apk_path: Optional[str] = None


@dataclass
class SkillResult:
    """Result of executing a skill: success, structured data, human/LLM-readable text.

    For exploit-tier skills, optional log_summary and spinner_text are used by
    orchestration to write a short line to run.log and to drive spinner/UI text.
    """

    success: bool
    data: Any = None  # skill-specific structured output
    text: str = ""   # human/LLM-readable summary
    log_summary: Optional[str] = None  # short line for run.log (exploit steps)
    spinner_text: Optional[str] = None  # spinner/UI label (exploit steps)


_NS_ANDROID = "http://schemas.android.com/apk/res/android"

_FLAVOR_SUFFIXES = (
    ".debug", ".release", ".low", ".high", ".qa", ".staging",
    ".dev", ".beta", ".alpha", ".prod", ".free", ".paid",
    ".demo", ".full", ".lite", ".internal",
)


def _manifest_path_from_run_folder(run_folder: Optional[Path]) -> Optional[Path]:
    """Locate the extracted AndroidManifest.xml relative to a run folder."""
    if not run_folder:
        return None
    p = Path(run_folder).parent / "extracted_apk" / "AndroidManifest.xml"
    return p if p.exists() else None


def _parse_manifest_all_components(manifest_path: Path) -> list[str]:
    """Parse AndroidManifest.xml and return ALL declared component FQCNs (not just exported)."""
    try:
        tree = ET.parse(manifest_path)
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return []

    manifest_package = (root.get("package") or root.get(f"{{{_NS_ANDROID}}}package") or "").strip()

    names: list[str] = []
    app = None
    for child in root:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "application":
            app = child
            break
    if app is None:
        return []

    for child in app:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag in ("activity", "activity-alias", "service", "receiver", "provider"):
            name = child.get(f"{{{_NS_ANDROID}}}name") or child.get("name") or ""
            name = name.strip()
            if not name:
                continue
            if name.startswith("."):
                name = manifest_package + name
            elif "." not in name:
                name = f"{manifest_package}.{name}" if manifest_package else name
            names.append(name)
    return names


def _manifest_package_attr(manifest_path: Path) -> Optional[str]:
    """Return the raw `package` attribute from AndroidManifest.xml (the code namespace)."""
    try:
        tree = ET.parse(manifest_path)
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return None
    pkg = (root.get("package") or "").strip()
    return pkg or None


def infer_code_package(dossier_dict: Optional[dict[str, Any]], run_folder: Optional[Path] = None) -> Optional[str]:
    """Infer the actual Java/Kotlin code package, which may differ from applicationId.

    In compiled APKs the manifest `package` attribute is always the applicationId,
    so we infer the code package from actual component class names declared in the
    manifest and dossier.

    Tries: (1) common prefix of all manifest + dossier component names (excluding
    well-known libraries), (2) stripping build flavor suffixes from applicationId.
    """
    app_package = ""
    if dossier_dict:
        app_package = ((dossier_dict.get("apk_info") or {}).get("package") or "").strip()

    all_names: list[str] = []

    manifest = _manifest_path_from_run_folder(run_folder)
    if manifest:
        for n in _parse_manifest_all_components(manifest):
            if n and "." in n:
                all_names.append(n)

    if dossier_dict:
        for list_key, attr in [
            ("exported_activities", "name"),
            ("exported_services", "name"),
            ("exported_receivers", "name"),
            ("exported_providers", "name"),
        ]:
            for item in dossier_dict.get(list_key) or []:
                n = (item.get(attr) or "").strip() if isinstance(item, dict) else ""
                if n and "." in n:
                    all_names.append(n)

    from androscan.llm.prompts import is_library_component
    app_names = [n for n in all_names if not is_library_component(n)]

    if app_names:
        packages = set()
        for n in app_names:
            packages.add(n.rsplit(".", 1)[0])
        if len(packages) == 1:
            return packages.pop()
        common = _longest_common_package_prefix(app_names)
        if common and len(common) > 10:
            return common

    if app_package:
        lower = app_package.lower()
        for suffix in _FLAVOR_SUFFIXES:
            if lower.endswith(suffix):
                return app_package[: len(app_package) - len(suffix)]

    return None


def _longest_common_package_prefix(names: list[str]) -> Optional[str]:
    """Find the longest common dot-separated package prefix among class names."""
    if not names:
        return None
    split_names = [n.rsplit(".", 1)[0].split(".") for n in names]
    prefix_parts: list[str] = []
    for parts in zip(*split_names):
        if len(set(parts)) == 1:
            prefix_parts.append(parts[0])
        else:
            break
    return ".".join(prefix_parts) if prefix_parts else None


def resolve_short_class_name(short_name: str, dossier_dict: Optional[dict[str, Any]]) -> Optional[str]:
    """Try to resolve a short (unqualified) class name to a fully qualified one using the dossier.

    Searches exported components for a name ending with the short name,
    then falls back to prepending the package from apk_info.
    Returns None if resolution is not possible.
    """
    if not short_name or "." in short_name:
        return short_name or None
    if not dossier_dict:
        return None

    component_lists = [
        ("exported_activities", "name"),
        ("exported_services", "name"),
        ("exported_receivers", "name"),
        ("exported_providers", "name"),
        ("deep_links", "component"),
    ]
    for list_key, attr_name in component_lists:
        for item in dossier_dict.get(list_key) or []:
            fqcn = (item.get(attr_name) or "") if isinstance(item, dict) else ""
            if fqcn.endswith(f".{short_name}"):
                return fqcn

    package = ""
    apk_info = dossier_dict.get("apk_info")
    if isinstance(apk_info, dict):
        package = (apk_info.get("package") or "").strip()
    if package:
        return f"{package}.{short_name}"

    return None


def generate_class_name_alternatives(
    failed_name: str,
    dossier_dict: Optional[dict[str, Any]],
    run_folder: Optional[Path] = None,
) -> list[str]:
    """Generate alternative FQCNs to try when jadx fails with the given class name.

    Strategies:
    1. Search the manifest for a component with the same simple name
    2. Use the inferred code package (may differ from applicationId)
    3. Strip build flavor suffixes from the failed name's package
    """
    if not failed_name or "." not in failed_name:
        return []

    simple_name = failed_name.rsplit(".", 1)[-1]
    seen: set[str] = {failed_name}
    alternatives: list[str] = []

    manifest = _manifest_path_from_run_folder(run_folder)
    if manifest:
        for comp_name in _parse_manifest_all_components(manifest):
            if comp_name.endswith(f".{simple_name}") and comp_name not in seen:
                alternatives.append(comp_name)
                seen.add(comp_name)

    code_pkg = infer_code_package(dossier_dict, run_folder)
    if code_pkg:
        candidate = f"{code_pkg}.{simple_name}"
        if candidate not in seen:
            alternatives.append(candidate)
            seen.add(candidate)

    failed_pkg = failed_name.rsplit(".", 1)[0]
    for suffix in _FLAVOR_SUFFIXES:
        if failed_pkg.lower().endswith(suffix):
            base_pkg = failed_pkg[: len(failed_pkg) - len(suffix)]
            candidate = f"{base_pkg}.{simple_name}"
            if candidate not in seen:
                alternatives.append(candidate)
                seen.add(candidate)
            break

    parts = failed_pkg.rsplit(".", 1)
    if len(parts) == 2:
        candidate = f"{parts[0]}.{simple_name}"
        if candidate not in seen:
            alternatives.append(candidate)
            seen.add(candidate)

    return alternatives
