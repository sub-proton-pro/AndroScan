"""Dossier model: structured export of APK attack surface.

Schema matches DESIGN_DOC Section 5. Used as input to LLM and for app_id derivation.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from androscan import constants


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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Dossier":
        """Deserialize from JSON-suitable dict (output of to_dict())."""
        ai = d.get("apk_info") or {}
        apk_info = ApkInfo(
            package=ai.get("package", ""),
            version_name=ai.get("version_name", ""),
            version_code=int(ai.get("version_code", 0)),
            min_sdk=int(ai.get("min_sdk", 0)),
            target_sdk=int(ai.get("target_sdk", 0)),
        )

        def _parse_data(x: dict) -> IntentFilterData:
            return IntentFilterData(
                scheme=x.get("scheme"),
                host=x.get("host"),
                pathPrefix=x.get("pathPrefix"),
            )

        def _parse_intent_filter(f: dict) -> IntentFilter:
            return IntentFilter(
                action=list(f.get("action") or []),
                category=list(f.get("category") or []),
                data=[_parse_data(i) for i in (f.get("data") or [])],
            )

        def _parse_activity(a: dict) -> ExportedActivity:
            return ExportedActivity(
                name=a.get("name", ""),
                exported=bool(a.get("exported", True)),
                intent_filters=[_parse_intent_filter(i) for i in (a.get("intent_filters") or [])],
            )

        def _parse_service(s: dict) -> ExportedService:
            return ExportedService(
                name=s.get("name", ""),
                exported=bool(s.get("exported", True)),
                intent_filters=[_parse_intent_filter(i) for i in (s.get("intent_filters") or [])],
            )

        def _parse_receiver(r: dict) -> ExportedReceiver:
            return ExportedReceiver(
                name=r.get("name", ""),
                exported=bool(r.get("exported", True)),
                intent_filters=[_parse_intent_filter(i) for i in (r.get("intent_filters") or [])],
            )

        def _parse_provider(p: dict) -> ExportedProvider:
            return ExportedProvider(
                name=p.get("name", ""),
                exported=bool(p.get("exported", True)),
                authority=p.get("authority", ""),
                read_permission=p.get("read_permission"),
                write_permission=p.get("write_permission"),
                grant_uri_permissions=bool(p.get("grant_uri_permissions", False)),
            )

        def _parse_deep_link(dl: dict) -> DeepLink:
            return DeepLink(
                component=dl.get("component", ""),
                scheme=dl.get("scheme", ""),
                host=dl.get("host", ""),
                path_prefix=dl.get("path_prefix", ""),
                intent_filter_index=int(dl.get("intent_filter_index", 0)),
            )

        return cls(
            apk_info=apk_info,
            permissions=list(d.get("permissions") or []),
            exported_activities=[_parse_activity(a) for a in (d.get("exported_activities") or [])],
            exported_services=[_parse_service(s) for s in (d.get("exported_services") or [])],
            exported_receivers=[_parse_receiver(r) for r in (d.get("exported_receivers") or [])],
            exported_providers=[_parse_provider(p) for p in (d.get("exported_providers") or [])],
            deep_links=[_parse_deep_link(dl) for dl in (d.get("deep_links") or [])],
        )

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
    if len(safe) > constants.APP_ID_MAX_LEN:
        return safe[:constants.APP_ID_MAX_LEN]
    return safe or "unknown"
