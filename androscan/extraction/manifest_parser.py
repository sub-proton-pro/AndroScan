"""Parse decoded AndroidManifest.xml (apktool output) into a structure for the dossier builder."""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

# Common Android manifest namespace
NS_ANDROID = "http://schemas.android.com/apk/res/android"


def _attr(el: ET.Element, name: str, default: str = "") -> str:
    """Get attribute value; try android:name then name."""
    v = el.get(f"{{{NS_ANDROID}}}{name}") or el.get(name)
    return (v or default).strip()


def _attr_int(el: ET.Element, name: str, default: int = 0) -> int:
    try:
        return int(_attr(el, name) or default)
    except ValueError:
        return default


def _attr_bool(el: ET.Element, name: str, default: bool = False) -> bool:
    v = _attr(el, name).lower()
    if v in ("true", "1"):
        return True
    if v in ("false", "0"):
        return False
    return default


def _parse_intent_filter(if_el: ET.Element) -> dict[str, Any]:
    actions = []
    categories = []
    data_list: list[dict[str, str]] = []
    for c in if_el:
        tag = c.tag.split("}")[-1] if "}" in c.tag else c.tag
        if tag == "action":
            actions.append(_attr(c, "name"))
        elif tag == "category":
            categories.append(_attr(c, "name"))
        elif tag == "data":
            data_list.append({
                "scheme": _attr(c, "scheme"),
                "host": _attr(c, "host"),
                "pathPrefix": _attr(c, "pathPrefix") or _attr(c, "path"),
            })
    return {"action": actions, "category": categories, "data": data_list}


def _parse_component(
    el: ET.Element,
    tag: str,
) -> dict[str, Any]:
    name = _attr(el, "name")
    if not name:
        return {}
    exported = _attr_bool(el, "exported")
    intent_filters = []
    for c in el:
        ctag = c.tag.split("}")[-1] if "}" in c.tag else c.tag
        if ctag == "intent-filter":
            intent_filters.append(_parse_intent_filter(c))
            if not exported and intent_filters:
                exported = True  # default true when has intent-filter
    out: dict[str, Any] = {
        "name": name,
        "exported": exported,
        "intent_filters": intent_filters,
    }
    if tag == "provider":
        out["authority"] = _attr(el, "authority")
        out["read_permission"] = _attr(el, "readPermission") or None
        out["write_permission"] = _attr(el, "writePermission") or None
        out["grant_uri_permissions"] = _attr_bool(el, "grantUriPermissions")
    return out


def parse_manifest_xml(manifest_path: Path) -> dict[str, Any]:
    """Parse AndroidManifest.xml into a dict suitable for build_dossier_from_parsed_manifest().

    Returns dict with: package, min_sdk, target_sdk, version_name, version_code,
    permissions, activities, services, receivers, providers.
    On parse error or missing manifest, returns {"stub": True}.
    """
    if not manifest_path.exists():
        return {"stub": True}
    try:
        tree = ET.parse(manifest_path)
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return {"stub": True}

    # Root is <manifest>
    package = _attr(root, "package")
    if not package:
        return {"stub": True}

    version_name = _attr(root, "versionName")
    version_code = _attr_int(root, "versionCode")

    min_sdk = 0
    target_sdk = 0
    for uses in root:
        tag = uses.tag.split("}")[-1] if "}" in uses.tag else uses.tag
        if tag == "uses-sdk":
            min_sdk = _attr_int(uses, "minSdkVersion")
            target_sdk = _attr_int(uses, "targetSdkVersion")
            break

    permissions: list[str] = []
    for child in root:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "uses-permission":
            name = _attr(child, "name")
            if name:
                permissions.append(name)

    activities: list[dict[str, Any]] = []
    services: list[dict[str, Any]] = []
    receivers: list[dict[str, Any]] = []
    providers: list[dict[str, Any]] = []
    app = None
    for child in root:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "application":
            app = child
            break

    if app is not None:
        for child in app:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "activity":
                a = _parse_component(child, "activity")
                if a:
                    activities.append(a)
            elif tag == "service":
                s = _parse_component(child, "service")
                if s:
                    services.append(s)
            elif tag == "receiver":
                r = _parse_component(child, "receiver")
                if r:
                    receivers.append(r)
            elif tag == "provider":
                p = _parse_component(child, "provider")
                if p:
                    providers.append(p)

    return {
        "stub": False,
        "package": package,
        "version_name": version_name or "",
        "version_code": version_code,
        "min_sdk": min_sdk,
        "target_sdk": target_sdk,
        "permissions": permissions,
        "activities": activities,
        "services": services,
        "receivers": receivers,
        "providers": providers,
    }


def build_dossier_dict_from_parsed(parsed: dict[str, Any]) -> dict[str, Any]:
    """Convert parsed manifest dict to dossier dict (Dossier.to_dict() shape). Only exported components."""
    if parsed.get("stub"):
        return {}

    def to_intent_filter(if_dict: dict) -> dict[str, Any]:
        data = [
            {"scheme": d.get("scheme"), "host": d.get("host"), "pathPrefix": d.get("pathPrefix")}
            for d in (if_dict.get("data") or [])
        ]
        return {
            "action": if_dict.get("action") or [],
            "category": if_dict.get("category") or [],
            "data": data,
        }

    def to_activity(a: dict) -> dict[str, Any]:
        return {
            "name": a.get("name", ""),
            "exported": bool(a.get("exported", False)),
            "intent_filters": [to_intent_filter(i) for i in (a.get("intent_filters") or [])],
        }

    def to_service(s: dict) -> dict[str, Any]:
        return {
            "name": s.get("name", ""),
            "exported": bool(s.get("exported", False)),
            "intent_filters": [to_intent_filter(i) for i in (s.get("intent_filters") or [])],
        }

    def to_receiver(r: dict) -> dict[str, Any]:
        return {
            "name": r.get("name", ""),
            "exported": bool(r.get("exported", False)),
            "intent_filters": [to_intent_filter(i) for i in (r.get("intent_filters") or [])],
        }

    def to_provider(p: dict) -> dict[str, Any]:
        return {
            "name": p.get("name", ""),
            "exported": bool(p.get("exported", False)),
            "authority": p.get("authority", ""),
            "read_permission": p.get("read_permission"),
            "write_permission": p.get("write_permission"),
            "grant_uri_permissions": bool(p.get("grant_uri_permissions", False)),
        }

    exported_activities = [to_activity(a) for a in (parsed.get("activities") or []) if a.get("exported")]
    exported_services = [to_service(s) for s in (parsed.get("services") or []) if s.get("exported")]
    exported_receivers = [to_receiver(r) for r in (parsed.get("receivers") or []) if r.get("exported")]
    exported_providers = [to_provider(p) for p in (parsed.get("providers") or []) if p.get("exported")]

    deep_links: list[dict[str, Any]] = []
    for comp_type, comp_list in [
        ("activities", exported_activities),
        ("services", exported_services),
        ("receivers", exported_receivers),
    ]:
        for comp in comp_list:
            for idx, if_dict in enumerate(comp.get("intent_filters") or []):
                actions = set(if_dict.get("action") or [])
                categories = set(if_dict.get("category") or [])
                if "android.intent.action.VIEW" not in actions:
                    continue
                if "android.intent.category.BROWSABLE" not in categories:
                    continue
                for d in (if_dict.get("data") or []):
                    scheme = (d.get("scheme") or "").strip()
                    host = (d.get("host") or "").strip()
                    path = (d.get("pathPrefix") or d.get("path") or "").strip()
                    if scheme or host or path:
                        deep_links.append({
                            "component": comp.get("name", ""),
                            "scheme": scheme,
                            "host": host,
                            "path_prefix": path,
                            "intent_filter_index": idx,
                        })

    return {
        "apk_info": {
            "package": parsed.get("package", ""),
            "version_name": parsed.get("version_name", ""),
            "version_code": int(parsed.get("version_code", 0)),
            "min_sdk": int(parsed.get("min_sdk", 0)),
            "target_sdk": int(parsed.get("target_sdk", 0)),
        },
        "permissions": list(parsed.get("permissions") or []),
        "exported_activities": exported_activities,
        "exported_services": exported_services,
        "exported_receivers": exported_receivers,
        "exported_providers": exported_providers,
        "deep_links": deep_links,
    }
