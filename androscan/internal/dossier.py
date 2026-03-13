"""Dossier model: structured export of APK attack surface.

Schema matches DESIGN_DOC Section 5. Used as input to LLM and for app_id derivation.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

# Max length for app_id (sanitized package); truncate if longer.
APP_ID_MAX_LEN = 128


@dataclass
class ApkInfo:
    package: str
    version_name: str
    version_code: int
    min_sdk: int
    target_sdk: int


@dataclass
class IntentFilterData:
    scheme: Optional[str] = None
    host: Optional[str] = None
    pathPrefix: Optional[str] = None


@dataclass
class IntentFilter:
    action: list[str] = field(default_factory=list)
    category: list[str] = field(default_factory=list)
    data: list[IntentFilterData] = field(default_factory=list)


@dataclass
class ExportedActivity:
    name: str
    exported: bool
    intent_filters: list[IntentFilter] = field(default_factory=list)


@dataclass
class ExportedService:
    name: str
    exported: bool
    intent_filters: list[IntentFilter] = field(default_factory=list)


@dataclass
class ExportedReceiver:
    name: str
    exported: bool
    intent_filters: list[IntentFilter] = field(default_factory=list)


@dataclass
class ExportedProvider:
    name: str
    exported: bool
    authority: str
    read_permission: Optional[str] = None
    write_permission: Optional[str] = None
    grant_uri_permissions: bool = False


@dataclass
class DeepLink:
    component: str
    scheme: str
    host: str
    path_prefix: str
    intent_filter_index: int


@dataclass
class Dossier:
    apk_info: ApkInfo
    permissions: list[str]
    exported_activities: list[ExportedActivity]
    exported_services: list[ExportedService]
    exported_receivers: list[ExportedReceiver]
    exported_providers: list[ExportedProvider]
    deep_links: list[DeepLink]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-suitable dict (DESIGN_DOC schema)."""
        def _intent_filter_data(d: IntentFilterData) -> dict:
            out: dict[str, Any] = {}
            if d.scheme is not None:
                out["scheme"] = d.scheme
            if d.host is not None:
                out["host"] = d.host
            if d.pathPrefix is not None:
                out["pathPrefix"] = d.pathPrefix
            return out

        def _intent_filter(f: IntentFilter) -> dict:
            return {
                "action": f.action,
                "category": f.category,
                "data": [_intent_filter_data(x) for x in f.data],
            }

        return {
            "apk_info": {
                "package": self.apk_info.package,
                "version_name": self.apk_info.version_name,
                "version_code": self.apk_info.version_code,
                "min_sdk": self.apk_info.min_sdk,
                "target_sdk": self.apk_info.target_sdk,
            },
            "permissions": self.permissions,
            "exported_activities": [
                {"name": a.name, "exported": a.exported, "intent_filters": [_intent_filter(i) for i in a.intent_filters]}
                for a in self.exported_activities
            ],
            "exported_services": [
                {"name": s.name, "exported": s.exported, "intent_filters": [_intent_filter(i) for i in s.intent_filters]}
                for s in self.exported_services
            ],
            "exported_receivers": [
                {"name": r.name, "exported": r.exported, "intent_filters": [_intent_filter(i) for i in r.intent_filters]}
                for r in self.exported_receivers
            ],
            "exported_providers": [
                {
                    "name": p.name,
                    "exported": p.exported,
                    "authority": p.authority,
                    "read_permission": p.read_permission,
                    "write_permission": p.write_permission,
                    "grant_uri_permissions": p.grant_uri_permissions,
                }
                for p in self.exported_providers
            ],
            "deep_links": [
                {
                    "component": d.component,
                    "scheme": d.scheme,
                    "host": d.host,
                    "path_prefix": d.path_prefix,
                    "intent_filter_index": d.intent_filter_index,
                }
                for d in self.deep_links
            ],
        }


def app_id_from_dossier(dossier: Dossier) -> str:
    """Derive app_id from dossier: sanitize package (dots to underscores), truncate if too long."""
    raw = dossier.apk_info.package.replace(".", "_")
    # Sanitize: remove any character that is unsafe in paths
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in raw)
    if len(safe) > APP_ID_MAX_LEN:
        return safe[:APP_ID_MAX_LEN]
    return safe or "unknown"
