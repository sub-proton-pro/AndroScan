"""Prompt building: system and user prompts per DESIGN_DOC §7 (global context, skills catalog, per-turn user)."""

import json
from typing import Any, Iterator, Optional

from androscan.skills.base import SkillMeta

# ---------------------------------------------------------------------------
# Risk-based component classification
# ---------------------------------------------------------------------------

_LIBRARY_PREFIXES = (
    "androidx.",
    "com.google.android.gms.",
    "com.google.firebase.",
    "com.google.android.datatransport.",
    "com.google.android.play.",
    "com.google.android.material.",
    "com.google.mlkit.",
    "com.google.android.exoplayer",
    "io.flutter.embedding.",
    "io.flutter.plugins.",
    "dev.fluttercommunity.",
    "com.facebook.react.",
    "mono.android.",
    "com.crashlytics.",
    "com.appsflyer.",
    "com.adjust.sdk.",
)

_SAFE_ACTIONS = frozenset({
    "android.intent.action.BOOT_COMPLETED",
    "android.intent.action.MY_PACKAGE_REPLACED",
    "android.intent.action.PACKAGE_REPLACED",
    "android.intent.action.BATTERY_LOW",
    "android.intent.action.BATTERY_OKAY",
    "android.intent.action.ACTION_POWER_CONNECTED",
    "android.intent.action.ACTION_POWER_DISCONNECTED",
    "android.intent.action.DEVICE_STORAGE_LOW",
    "android.intent.action.DEVICE_STORAGE_OK",
    "android.net.conn.CONNECTIVITY_CHANGE",
    "android.intent.action.TIME_SET",
    "android.intent.action.TIMEZONE_CHANGED",
    "android.intent.action.LOCALE_CHANGED",
    "android.intent.action.ACTION_SHUTDOWN",
    "android.intent.action.AIRPLANE_MODE_CHANGED",
    "android.intent.action.SCREEN_ON",
    "android.intent.action.SCREEN_OFF",
    "android.intent.action.USER_PRESENT",
    "android.intent.action.HEADSET_PLUG",
    "android.intent.action.CONFIGURATION_CHANGED",
    "android.intent.action.BATTERY_CHANGED",
    "android.intent.action.DREAMING_STARTED",
    "android.intent.action.DREAMING_STOPPED",
    "android.intent.action.INPUT_METHOD_CHANGED",
    "android.intent.action.DOCK_EVENT",
    "android.intent.action.MAIN",
})

_SAFE_ACTION_PREFIXES = (
    "androidx.",
)


def is_library_component(name: str) -> bool:
    """Return True if the component name belongs to a well-known library."""
    return any(name.startswith(pfx) for pfx in _LIBRARY_PREFIXES)


def _is_safe_action(action: str) -> bool:
    """Return True if the action is a protected system broadcast or framework-internal."""
    if action in _SAFE_ACTIONS:
        return True
    return any(action.startswith(pfx) for pfx in _SAFE_ACTION_PREFIXES)


def _matches_app_package(component_name: str, app_package: str) -> bool:
    """Check if a component likely belongs to the app, handling build flavor suffixes."""
    if not component_name or not app_package:
        return False
    if component_name.startswith(app_package + ".") or component_name.startswith(app_package + "$"):
        return True
    # Handle build flavor suffixes (e.g. package "com.company.app.qa" but
    # component is "com.company.app.MainActivity")
    parts = app_package.rsplit(".", 1)
    if len(parts) == 2:
        base = parts[0]
        if len(base) > 15 and not is_library_component(base + "."):
            if component_name.startswith(base + ".") or component_name.startswith(base + "$"):
                return True
    return False


def _has_custom_deep_links(component: dict[str, Any]) -> bool:
    """Return True if the component handles custom URI schemes (deep link hijacking risk)."""
    for intent_filter in component.get("intent_filters") or []:
        for data in intent_filter.get("data") or []:
            scheme = (data.get("scheme") or "").strip().lower()
            if scheme and scheme not in ("", "http", "https"):
                return True
    scheme = (component.get("scheme") or "").strip().lower()
    if scheme and scheme not in ("", "http", "https"):
        return True
    return False


def _has_custom_actions(component: dict[str, Any]) -> bool:
    """Return True if any intent filter action is NOT a known safe system/framework action."""
    for intent_filter in component.get("intent_filters") or []:
        for action in intent_filter.get("action") or []:
            if not _is_safe_action(action.strip()):
                return True
    return False


def _only_safe_actions(component: dict[str, Any]) -> bool:
    """Return True if ALL intent filter actions are known-safe.

    Returns False if the component has no intent filters (can't prove it's safe).
    """
    has_any = False
    for intent_filter in component.get("intent_filters") or []:
        for action in intent_filter.get("action") or []:
            has_any = True
            if not _is_safe_action(action.strip()):
                return False
    return has_any


def _get_all_actions(component: dict[str, Any]) -> list[str]:
    """Extract all intent action strings from a component's intent filters."""
    actions: list[str] = []
    for intent_filter in component.get("intent_filters") or []:
        actions.extend(intent_filter.get("action") or [])
    return actions


def assess_component_risk(
    component: dict[str, Any],
    component_type: str,
    app_package: str,
    name_attr: str = "name",
) -> str:
    """Classify a single component's risk level.

    Returns "full", "summary", or "skip".
    - full:    deep LLM analysis + exploit verification
    - summary: listed in report with basic info, no deep analysis
    - skip:    pure system infrastructure, footnote in report
    """
    name = (component.get(name_attr) or "").strip()

    # Rule 1: App's own components always get full analysis
    if app_package and _matches_app_package(name, app_package):
        return "full"

    # Rule 2: Content providers always get full analysis (they expose data)
    if component_type == "provider":
        return "full"

    # Rule 3: Components with custom deep links always get full analysis
    if _has_custom_deep_links(component):
        return "full"

    # Rule 4: Components with custom/non-safe intent actions get full analysis
    if _has_custom_actions(component):
        return "full"

    # Rule 5: Unknown components (not in library list) get full analysis by default
    if not is_library_component(name):
        return "full"

    # --- From here, component IS a known library component ---

    # Rule 6: Known library + all actions are safe = skip
    if _only_safe_actions(component):
        return "skip"

    # Rule 7: Known library, other cases = summary
    return "summary"


def classify_dossier_components(
    dossier_dict: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    """Classify all exported components by risk and build a filtered analysis dossier.

    Returns (analysis_dossier, summary_components, skipped_components):
    - analysis_dossier: dossier containing only FULL-risk components for LLM analysis
    - summary_components: list of {name, type, reason} for noted library components
    - skipped_components: list of {name, type, reason} for system infrastructure
    """
    app_package = ((dossier_dict.get("apk_info") or {}).get("package") or "").strip()

    filtered = dict(dossier_dict)
    summary: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for list_key, name_attr, comp_type in [
        ("exported_activities", "name", "activity"),
        ("exported_services", "name", "service"),
        ("exported_receivers", "name", "receiver"),
        ("exported_providers", "name", "provider"),
    ]:
        original = dossier_dict.get(list_key) or []
        kept: list[dict[str, Any]] = []
        for item in original:
            cname = (item.get(name_attr) or "").strip()
            risk = assess_component_risk(item, comp_type, app_package, name_attr)
            if risk == "full":
                kept.append(item)
            elif risk == "skip":
                actions = _get_all_actions(item)
                reason = (
                    f"System/framework actions only ({', '.join(actions[:3])})"
                    if actions else "Known library infrastructure"
                )
                skipped.append({"name": cname, "type": comp_type, "reason": reason})
            else:
                summary.append({"name": cname, "type": comp_type, "reason": "Known library component"})
        filtered[list_key] = kept

    # Deduplicate deep_links whose activity is already in FULL analysis
    full_activity_names = {
        (a.get("name") or "").strip() for a in filtered.get("exported_activities") or []
    }
    kept_links: list[dict[str, Any]] = []
    for item in dossier_dict.get("deep_links") or []:
        cname = (item.get("component") or "").strip()
        if cname and cname in full_activity_names:
            skipped.append({
                "name": cname, "type": "deep_link",
                "reason": "Already analyzed as exported activity",
            })
        else:
            risk = assess_component_risk(item, "deep_link", app_package, "component")
            if risk == "full":
                kept_links.append(item)
            elif risk == "skip":
                skipped.append({"name": cname, "type": "deep_link", "reason": "Known library infrastructure"})
            else:
                summary.append({"name": cname, "type": "deep_link", "reason": "Known library component"})
    filtered["deep_links"] = kept_links

    return filtered, summary, skipped


def _empty_dossier_skeleton(d: dict[str, Any]) -> dict[str, Any]:
    """Copy apk_info and permissions; empty component lists."""
    return {
        "apk_info": d.get("apk_info") or {},
        "permissions": list(d.get("permissions") or []),
        "exported_activities": [],
        "exported_services": [],
        "exported_receivers": [],
        "exported_providers": [],
        "deep_links": [],
    }


def iter_dossier_components(dossier_dict: dict[str, Any]) -> Iterator[tuple[dict[str, Any], str, str, str, int]]:
    """Yield (slice_dict, component_type, label, list_key, full_index) for each exported component in fixed order.

    Order: activities -> services -> receivers -> providers -> deep_links.
    slice_dict is a dossier-shaped dict with only that one component (at index 0 in its list).
    list_key + full_index identify the component in the full dossier for evidence_ref rewriting.
    """
    skel = _empty_dossier_skeleton(dossier_dict)
    for i, item in enumerate(dossier_dict.get("exported_activities") or []):
        slice_dict = {**skel, "exported_activities": [item]}
        name = (item.get("name") or "").strip() or f"activity_{i}"
        yield slice_dict, "activity", name, "exported_activities", i
    for i, item in enumerate(dossier_dict.get("exported_services") or []):
        slice_dict = {**skel, "exported_services": [item]}
        name = (item.get("name") or "").strip() or f"service_{i}"
        yield slice_dict, "service", name, "exported_services", i
    for i, item in enumerate(dossier_dict.get("exported_receivers") or []):
        slice_dict = {**skel, "exported_receivers": [item]}
        name = (item.get("name") or "").strip() or f"receiver_{i}"
        yield slice_dict, "receiver", name, "exported_receivers", i
    for i, item in enumerate(dossier_dict.get("exported_providers") or []):
        slice_dict = {**skel, "exported_providers": [item]}
        name = (item.get("name") or "").strip() or f"provider_{i}"
        yield slice_dict, "provider", name, "exported_providers", i
    for i, item in enumerate(dossier_dict.get("deep_links") or []):
        slice_dict = {**skel, "deep_links": [item]}
        name = (item.get("component") or "").strip() or f"deeplink_{i}"
        yield slice_dict, "deep_link", name, "deep_links", i


def build_component_prompt(
    slice_dict: dict[str, Any],
    component_type: str,
    component_label: str,
    prior_skill_results: Optional[list[str]] = None,
    llm_skills: Optional[list[SkillMeta]] = None,
) -> str:
    """Build user prompt for a single exported component (per-component analysis mode)."""
    parts = [
        f"Analyse this single exported component ({component_type}: {component_label}).",
        "Produce hypotheses with evidence_refs, or request skills if you need more data. Output valid JSON only; exploitability and confidence are integers 1-5.",
        "Include exploit_params when the component needs specific intent extras, actions, or data URIs to trigger the vulnerability.",
        "",
        "## Dossier (single component, JSON)",
        json.dumps(slice_dict, indent=2),
    ]
    if llm_skills:
        parts.extend(["", "## Available skills (request with skill_requests in your JSON response)"])
        for meta in llm_skills:
            parts.append(f"- **{meta.name}**: {meta.description}")
            if meta.params_schema:
                parts.append(f"  Params: {json.dumps(meta.params_schema)}")
        parts.append("")
    if prior_skill_results:
        parts.extend(["", "## Prior skill results", *prior_skill_results])
    parts.extend([
        "",
        "Return valid JSON with optional 'skill_requests' and/or 'hypotheses'. "
        "Use evidence_refs as dossier paths (e.g. exported_activities[0]). exploitability and confidence are integers 1-5.",
    ])
    return "\n".join(parts)


def build_system_content() -> str:
    """System message: role and output format instructions per DESIGN_DOC §7.1."""
    return (
        "You are a Senior Android security assessor. Produce exploitability hypotheses with evidence_refs; "
        "prefer fewer, high-confidence findings. "
        "Available skills: from the skills layer (listed in the user message). For each: name, description, parameters. "
        "How to request skills: Include in your response: skill_requests: [{ \"skill\": \"<name>\", \"params\": {...} }]. "
        "The tool will run them and re-prompt you with the results. When you have enough evidence, omit skill_requests and return hypotheses only. "
        "Always return valid JSON with optional 'skill_requests' and/or 'hypotheses'. "
        "Use evidence_refs as dossier paths (e.g. exported_activities[0]). exploitability and confidence are integers 1-5.\n"
        "\n"
        "IMPORTANT: Do NOT report findings for well-known library/infrastructure components "
        "(AndroidX, Firebase, Google Play Services, Flutter engine, WorkManager, ProfileInstaller, etc.). "
        "These are standard framework internals — not app-specific vulnerabilities. "
        "Focus only on components that belong to the application's own codebase.\n"
        "\n"
        "For each hypothesis, include an optional \"exploit_params\" object describing how to trigger the component. "
        "This is used to build an ADB exploit command. The object may contain:\n"
        "  - \"action\": intent action string (e.g. \"com.example.TRANSFER\")\n"
        "  - \"category\": intent category string\n"
        "  - \"data_uri\": data URI string for the intent\n"
        "  - \"flags\": array of intent flag strings (e.g. [\"FLAG_ACTIVITY_NEW_TASK\"])\n"
        "  - \"extras\": array of {\"key\": string, \"type\": \"string\"|\"int\"|\"long\"|\"float\"|\"bool\"|\"uri\", \"test_value\": ...}\n"
        "  - \"grant_uri_permissions\": boolean\n"
        "INTENT EXTRAS KEYS: Use the EXACT string value assigned to the constant in the decompiled "
        "source. For example, if the source shows 'public static final String EXTRA_AMOUNT = \"amount\";', "
        "the key is \"amount\" — NOT \"EXTRA_AMOUNT\", NOT \"com.example.app.amount\". "
        "Do NOT invent package prefixes. Some apps use simple keys (\"amount\", \"to\", \"pin\") "
        "while others use namespaced keys (\"com.example.app.EXTRA_PIN\") — always check the "
        "assigned string value, not the constant name.\n"
        "\n"
        "INTENT EXTRAS TYPES: Check how the component reads each extra to pick the correct type:\n"
        "  - getStringExtra(key) or getString(key) -> type: \"string\" (use --es in ADB)\n"
        "  - getIntExtra(key, default) or getInt(key) -> type: \"int\" (use --ei in ADB)\n"
        "  - getLongExtra(key, default) -> type: \"long\"\n"
        "  - getBooleanExtra(key, default) -> type: \"bool\"\n"
        "  - getFloatExtra(key, default) -> type: \"float\"\n"
        "Using the wrong type causes the extra to be null/default, which may block the exploit.\n"
        "\n"
        "Only include exploit_params when the component requires specific parameters (actions, extras, data URIs) to trigger the vulnerability. "
        "Omit it for components that can be exploited with a bare launch/start command.\n"
        "\n"
        "DEEP LINK data_uri: When constructing data_uri for deep links, carefully read the decompiled source "
        "to determine whether the app uses PATH-BASED routing (e.g. scheme://host/transfer) or QUERY-PARAMETER "
        "routing (e.g. scheme://host?route=transfer). Use the correct format. Path-based is more common.\n"
        "\n"
        "CLASS NAME RESOLUTION: The applicationId (from build.gradle) may include a build flavor suffix "
        "(e.g. .debug, .low, .qa) that is NOT part of the Java/Kotlin code package. When requesting "
        "get_decompiled_class, use the code package from already-decompiled sources or try list_classes_in_package "
        "to discover the correct package prefix."
    )


def build_prompt(
    dossier_dict: dict[str, Any],
    prior_skill_results: Optional[list[str]] = None,
    llm_skills: Optional[list[SkillMeta]] = None,
) -> str:
    """Build the user prompt: dossier + optional prior skill results + optional skills catalog (§7.1, §7.3)."""
    parts = [
        "Here is the dossier" + (" and prior skill results below." if prior_skill_results else "."),
        "Produce hypotheses with evidence_refs, or request skills if you need more data. Output valid JSON only; exploitability and confidence are integers 1-5.",
        "Include exploit_params when the component needs specific intent extras, actions, or data URIs to trigger the vulnerability.",
        "",
        "## Dossier (JSON)",
        json.dumps(dossier_dict, indent=2),
    ]
    if llm_skills:
        parts.extend(["", "## Available skills (request with skill_requests in your JSON response)"])
        for meta in llm_skills:
            parts.append(f"- **{meta.name}**: {meta.description}")
            if meta.params_schema:
                parts.append(f"  Params: {json.dumps(meta.params_schema)}")
        parts.append("")
    if prior_skill_results:
        parts.extend(["", "## Prior skill results", *prior_skill_results])
    parts.extend([
        "",
        "Return valid JSON with optional 'skill_requests' and/or 'hypotheses'. "
        "Use evidence_refs as dossier paths. exploitability and confidence are integers 1-5.",
    ])
    return "\n".join(parts)


def build_consolidation_prompt(hypotheses: list[dict[str, Any]]) -> str:
    """Build prompt for LLM to deduplicate and merge overlapping security findings."""
    if not hypotheses:
        return ""
    parts = [
        "Below are security findings from a per-component analysis. Some may duplicate or overlap (same component, same issue, different wording).",
        "",
        "Tasks:",
        "1. Deduplicate: merge findings that describe the same or overlapping issue (especially same evidence_ref / component).",
        "2. For merged findings: write one clear title and one clear description that captures the issue.",
        "3. Keep evidence_refs, exploitability (1-5), and confidence (1-5). Use the highest exploitability when merging.",
        "4. Return valid JSON only, with a single key \"hypotheses\" and an array of finding objects.",
        "5. Each object must have: id, component_type, component_name, title, description, evidence_refs (array of strings), exploitability, confidence, remediation_hint. Preserve exploit_params if present.",
        "",
        "## Findings (JSON)",
        json.dumps(hypotheses, indent=2),
        "",
        "Return valid JSON: { \"hypotheses\": [ ... ] }",
    ]
    return "\n".join(parts)


def build_consolidation_system_content() -> str:
    """System message for the consolidation LLM call."""
    return (
        "You are a security report editor. Merge duplicate or overlapping findings into a single, clear finding. "
        "Return only valid JSON with key \"hypotheses\" and an array of objects. "
        "Each object: id (string), component_type, component_name, title, description, evidence_refs (array of strings), "
        "exploitability (integer 1-5), confidence (integer 1-5), remediation_hint (string). Preserve exploit_params if present."
    )
