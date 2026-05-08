"""Skill contract: SkillMeta, SkillContext, SkillResult, SkillWidget. Used by all skills."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Union


@dataclass(frozen=True)
class SkillMeta:
    """Metadata for a skill: name, description, params schema, tier, consent class.

    ``requires_confirmation`` (DEC-022) flags side-effecting skills the chat
    agentic loop must gate on operator approval before executing. False by
    default so every existing read-only skill (decompile / RAG / fuser) is
    unchanged. Hook Lab's ``generate_frida_hook`` is the first real consumer
    in v1; future device-mutating skills (``adb`` shell drivers, etc.) will
    set this flag too.
    """

    name: str
    description: str
    params_schema: dict[str, Any]  # e.g. {"component_ref": "dossier path", ...}
    tier: Literal["pipeline", "llm", "exploit"]
    requires_confirmation: bool = False


@dataclass
class SkillContext:
    """Context passed to every skill: config, run folder, dossier, apk path."""

    config: Any  # androscan.config.Config; avoid circular import
    run_folder: Path
    dossier_dict: Optional[dict[str, Any]] = None
    apk_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Phase 11 v2.1 sub-step v2.1.5 — Chat-interactive widget pattern
#
# DEC-025 v2.1 closing-note Q7 (ii) — extends DEC-022's chat agentic loop
# with a new outbound channel for LLM-emitted interactive widgets.
# Skills can attach a tuple of structured widgets to their ``SkillResult``;
# the chat agentic loop forwards each widget through a new ``widget`` SSE
# event to the chat client, which dispatches by ``kind`` to a matching
# React widget component.
#
# Architectural pattern lock — additive-by-design:
#   * ``SkillWidget`` is a typed union; new widget kinds add as new union
#     members without breaking the schema.
#   * The frontend ``<ChatWidgetRenderer>`` dispatcher gracefully handles
#     unknown widget kinds (renders the skill's ``text`` field only,
#     ignores the unknown widget). A server / client version skew is
#     non-fatal — operator never sees a broken render.
#   * ``SkillResult.widgets`` defaults to ``()`` so every existing skill
#     (decompile / RAG / fuser / planner / hook gen / etc.) is unchanged.
#
# v2.1.5 ships ONE widget kind — ``trace_entry_candidate`` — the first
# real consumer of the pattern (the ``suggest_trace_entry`` skill).
# Future kinds (Hook Builder template suggestions, Inspect "did you mean"
# UI elements) add as new dataclasses + new union members.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceEntryCandidateWidget:
    """LLM-emitted clickable trace-entry-method candidate. Rendered by
    the chat dock as a compact card with the candidate's Smali sig +
    rationale + confidence + a "Trace this" button. On click, the
    frontend writes ``pendingTraceEntry`` (the same plumbing the 10.8
    Inspect → Trace seed uses) and flips the workbench to Lab → Trace
    mode — auto-fire on landing if the seed has a complete return
    descriptor (DEC-025 v2.1 Q8 (a)).

    Fields are deliberately tight — DEC-022's per-skill output budget
    is ~6 KB total, and v2.1.5's ``suggest_trace_entry`` caps the
    candidate list at 3, so the per-widget envelope stays well under
    1 KB. ``confidence`` is a ``[0.0, 1.0]`` ratio (LLM ranking score
    normalised to that scale by the skill).
    """

    kind: Literal["trace_entry_candidate"] = "trace_entry_candidate"
    smali_id: str = ""
    """Full Smali method id — e.g. ``Lcom/example/Foo;->onClick(Landroid/view/View;)V``.
    The frontend "Trace this" button writes this verbatim to
    ``pendingTraceEntry.entryPrefix``; if the value already looks like
    a complete signature (return-descriptor terminal), the existing
    auto-fire path fires the trace immediately on landing in Lab →
    Trace mode."""
    rationale: str = ""
    """One-line operator-readable explanation of why this candidate
    was suggested. Capped at 200 chars by the skill (DEC-025 v2.1 risk
    note — keeps the LLM-budget squeeze bounded on apps with verbose
    rationale prose)."""
    confidence: float = 0.0
    """``[0.0, 1.0]``; higher is more confident. Used to sort
    candidates client-side and to render an opacity / colour cue on
    the widget card (matches the v2.1.3 ``SimilarClassCandidate``
    visual hierarchy)."""


@dataclass(frozen=True)
class MethodSummaryWidget:
    """LLM-emitted method summary card. Phase 13 sub-step 13.9 /
    DEC-029. Rendered by the chat dock as a compact card with the
    method's signature header, the LLM-generated summary paragraph,
    a ``cached`` pill when the summary came from
    ``skill_results_cache`` (no fresh LLM call), and three action
    buttons mirroring the Inspector's action row: ``[Hook this
    method]`` (writes ``pendingHookPrefill`` with the
    ``entry_exit_log`` template), ``[Trace this gate]`` (writes
    ``pendingTraceEntry`` with the full Smali signature), and
    ``[Open source]`` (writes ``pendingCodeNav`` and flips the tab
    to Inspect).

    The widget is the second consumer of the ``SkillResult.widgets``
    channel (after :class:`TraceEntryCandidateWidget`); the agentic
    loop forwards it via the SSE ``widget`` event verbatim. ``kind``
    discriminator on the FE matches ``"method_summary"``.

    Field shape is deliberately self-contained — the action handlers
    on the FE construct the cross-tab pendings from the widget's
    fields directly (no extra round-trip back to the BE), mirroring
    how :class:`TraceEntryCandidateWidget`'s ``smali_id`` drives the
    "Trace this" button without needing a round-trip.
    """

    kind: Literal["method_summary"] = "method_summary"
    class_smali: str = ""
    """Smali-form class descriptor (e.g.
    ``Lcom/example/MainActivity;``). Used as the canonical class id
    by the FE's action handlers."""
    class_name: str = ""
    """Java-dotted class name (e.g. ``com.example.MainActivity``).
    Inner-class suffix stripped (jadx emits inner classes inside
    the outer-class file). Drives ``pendingHookPrefill.params.class_name``
    + ``pendingCodeNav.className``."""
    method_name: str = ""
    """Bare method name (e.g. ``onClick``). Drives
    ``pendingHookPrefill.params.method_name`` +
    ``pendingCodeNav.method``."""
    descriptor: str = ""
    """Smali method descriptor (e.g. ``(Landroid/view/View;)V``).
    Carried for the chat-side "Trace this gate" affordance which
    constructs the full Smali signature inline."""
    summary: str = ""
    """The LLM-generated paragraph (3-5 sentences). Capped at the
    skill's :data:`SOURCE_BODY_BUDGET_BYTES`-derived prompt budget
    so the per-widget envelope stays under DEC-022's per-skill 6 KB
    output budget."""
    cached: bool = False
    """``True`` when the summary was loaded from
    :mod:`skill_results_cache` rather than a fresh LLM round-trip.
    Drives the FE's "(cached)" pill so the operator can tell at a
    glance whether the answer is fresh or replayed."""


# ``SkillWidget`` is a typed union — additive-by-design (DEC-022 +
# DEC-025 v2.1 closing-note Q7 (ii)). New widget kinds add here as
# new union members; the FE's ``<ChatWidgetRenderer>`` dispatcher
# gracefully ignores unknown kinds. The chat agentic loop's SSE
# payload for a widget is the dataclass's ``asdict`` (so the JSON
# wire format mirrors the dataclass shape exactly, ``kind`` field
# included for the frontend dispatcher).
SkillWidget = Union[TraceEntryCandidateWidget, MethodSummaryWidget]


@dataclass
class SkillResult:
    """Result of executing a skill: success, structured data, human/LLM-readable text.

    For exploit-tier skills, optional log_summary and spinner_text are used by
    orchestration to write a short line to run.log and to drive spinner/UI text.

    Phase 11 v2.1 sub-step v2.1.5 — ``widgets`` extends the result
    surface with structured chat-renderable widgets. Defaults to ``()``
    so every existing skill is unchanged; the chat agentic loop forwards
    each widget through a ``widget`` SSE event to the frontend
    ``<ChatWidgetRenderer>`` dispatcher. See module-level
    ``SkillWidget`` doc-block for the architectural pattern.
    """

    success: bool
    data: Any = None  # skill-specific structured output
    text: str = ""   # human/LLM-readable summary
    log_summary: Optional[str] = None  # short line for run.log (exploit steps)
    spinner_text: Optional[str] = None  # spinner/UI label (exploit steps)
    widgets: tuple[SkillWidget, ...] = field(default_factory=tuple)
    """Tuple of LLM-emitted interactive widgets surfaced through the
    chat dock. Empty by default. v2.1.5 introduces the first consumer
    (``suggest_trace_entry`` returning ``TraceEntryCandidateWidget``);
    future skills add new widget kinds as new ``SkillWidget`` union
    members without breaking the schema."""


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
