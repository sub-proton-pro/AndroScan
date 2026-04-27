"""LLM-requestable skill: query the per-app static call graph.

Thin wrapper around :mod:`androscan.analysis.call_graph` so the workflow
agent (and Hook Lab chat) can ask questions like *"who calls
``LoginManager.checkPassword``?"* or *"is there a path from
``MainActivity.onClick`` to ``Cipher.doFinal``?"* without hand-rolling
SQLite queries.

Three modes — picked via the ``mode`` parameter — map onto the three
read entrypoints already used by the 4.2 graph routes:

* ``"overview"`` → :func:`call_graph.list_graph` (paginated; package /
  edge-kind / external filters)
* ``"neighbors"`` → :func:`call_graph.neighbors` (callers + callees of a
  single node)
* ``"paths"`` → :func:`call_graph.paths` (bounded BFS between two nodes)

Sub-step 4.7 (DEC-023). **Read-only**, ``requires_confirmation=False`` —
no device touch, no file writes; safe for the chat loop to invoke
without an Allow / Deny prompt.

Like :mod:`search_decompiled_sources`, this skill is *fail-open*: a
missing decompile cache, an unbuilt call graph, or an unknown app id
returns ``success=True`` with an empty result and a clear ``text``
explanation, so it never derails an analysis run because of optional
infra. The LLM can read the empty result and pick a different tool
(e.g. ``search_decompiled_sources``) instead of a hard error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="query_call_graph",
    description=(
        "Query the per-app static call graph (Smali → resolved invokes). "
        "Modes: 'overview' (paginated nodes + edges, optional package_prefix / "
        "kind filters), 'neighbors' (callers + callees of one node, identified "
        "by smali_id like 'Lcom/example/Foo;->bar(I)V' or numeric node id), "
        "'paths' (bounded BFS between two nodes). Read-only; safe to call "
        "without confirmation."
    ),
    params_schema={
        "mode": "one of: 'overview' | 'neighbors' | 'paths' (required)",
        "app_id": (
            "app_id (apps/<app_id>/) to query. Optional; defaults to the "
            "current run's app_id derived from the skill context."
        ),
        # overview
        "package_prefix": "overview only: filter nodes whose class.package starts with this prefix",
        "kind": (
            "overview only: edge-kind filter "
            "('direct' | 'static' | 'super' | 'virtual_dispatch' | 'interface_dispatch' | 'external')"
        ),
        "include_external": "overview/paths: include external (framework/library) callees (default false)",
        "limit": "overview only: max nodes to return (default 200, hard cap 5000)",
        "offset": "overview only: pagination offset (default 0)",
        # neighbors
        "node_ref": (
            "neighbors only: numeric node id or smali_id "
            "(e.g. 'Lcom/example/Foo;->bar(I)V')"
        ),
        # paths
        "source": "paths only: numeric id or smali_id of the start node",
        "target": "paths only: numeric id or smali_id of the end node",
        "max_hops": "paths only: BFS depth cap (default 8, hard cap 12)",
        "max_paths": "paths only: max distinct paths to return (default 10, hard cap 50)",
    },
    tier="llm",
    requires_confirmation=False,
)

_VALID_MODES = ("overview", "neighbors", "paths")
_OVERVIEW_DEFAULT_LIMIT = 200
_OVERVIEW_HARD_CAP = 5000
_PATHS_DEFAULT_HOPS = 8
_PATHS_HARD_HOPS = 12
_PATHS_DEFAULT_MAX = 10
_PATHS_HARD_MAX = 50


def _resolve_app_dir(context: SkillContext, app_id: Optional[str]) -> Optional[Path]:
    """Locate ``apps/<app_id>/`` from the explicit arg + the run folder.

    Same fallback ladder as :mod:`resolve_ui_element`: the explicit
    ``app_id`` wins when supplied; otherwise we fall back to
    ``run_folder.parent`` which is exactly ``apps/<app_id>/`` for the
    active run.
    """
    rf = getattr(context, "run_folder", None)
    if rf is None:
        return None
    rf_path = Path(rf)
    apps_root = rf_path.parent.parent if rf_path.parent.parent.exists() else None
    if app_id and apps_root and (apps_root / app_id).is_dir():
        return apps_root / app_id
    if rf_path.parent.exists():
        return rf_path.parent
    return None


def _coerce_int(value: Any, default: int, *, lo: int = 1, hi: Optional[int] = None) -> int:
    try:
        out = int(value) if value is not None else default
    except (TypeError, ValueError):
        out = default
    out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _pretty_node(n: dict[str, Any]) -> str:
    """Compact one-line description for the human/LLM-readable summary."""
    cid = n.get("class_id")
    return f"#{n.get('id')} {n.get('smali_id')} (class_id={cid})"


def _summarize_overview(out: dict[str, Any]) -> str:
    nodes = out.get("nodes") or []
    edges = out.get("edges") or []
    classes = out.get("classes") or []
    lines = [
        f"[query_call_graph][overview] "
        f"{out.get('total_nodes', 0)} matching node(s); returning {len(nodes)} "
        f"({len(edges)} internal edges, {len(classes)} class rows)."
    ]
    if nodes:
        lines.append("  first 10 nodes:")
        for n in nodes[:10]:
            cls = next((c for c in classes if c.get("id") == n.get("class_id")), None)
            cls_name = (cls or {}).get("class_name") or "?"
            lines.append(f"    - {cls_name}.{n.get('method_name')} ({n.get('smali_id')})")
    if edges:
        lines.append(f"  edge kinds: { {e['kind'] for e in edges} }")
    return "\n".join(lines)


def _summarize_neighbors(out: dict[str, Any]) -> str:
    node = out.get("node") or {}
    callers = out.get("callers") or []
    callees = out.get("callees") or []
    lines = [
        f"[query_call_graph][neighbors] {_pretty_node(node)}: "
        f"{len(callers)} caller(s), {len(callees)} callee(s)."
    ]
    if callers:
        lines.append("  callers (up to 10):")
        for c in callers[:10]:
            cn = c.get("node", {})
            ek = (c.get("edge") or {}).get("kind")
            lines.append(f"    - {cn.get('smali_id')} [{ek}]")
    if callees:
        lines.append("  callees (up to 10):")
        for c in callees[:10]:
            cn = c.get("node", {})
            ek = (c.get("edge") or {}).get("kind")
            lines.append(f"    - {cn.get('smali_id')} [{ek}]")
    return "\n".join(lines)


def _summarize_paths(out: dict[str, Any]) -> str:
    paths = out.get("paths") or []
    lines = [
        f"[query_call_graph][paths] {len(paths)} path(s) "
        f"(max_hops={out.get('max_hops')}, max_paths={out.get('max_paths')})."
    ]
    for i, p in enumerate(paths[:10], 1):
        lines.append(f"  {i}. ids: {p}")
    return "\n".join(lines)


def execute(params: dict, context: SkillContext) -> SkillResult:
    mode = (params.get("mode") or "").strip().lower()
    if mode not in _VALID_MODES:
        return SkillResult(
            success=False,
            data=None,
            text=(
                f"[query_call_graph] 'mode' is required and must be one of "
                f"{_VALID_MODES}; got {mode!r}."
            ),
        )

    app_id = (params.get("app_id") or "").strip() or None
    app_dir = _resolve_app_dir(context, app_id)
    if app_dir is None or not app_dir.is_dir():
        return SkillResult(
            success=True,
            data=None,
            text=(
                f"[query_call_graph] No app directory available for "
                f"app_id={app_id!r}; cannot query the call graph."
            ),
        )

    # Lazy imports keep the skill discovery cheap even on machines that
    # haven't built a decompile cache yet.
    try:
        from androscan.analysis import call_graph
        from androscan.web.decompile_cache import (
            cache_root_for as decompile_cache_root,
            get_status as decompile_status,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return SkillResult(
            success=True,
            data=None,
            text=f"[query_call_graph] analysis layer unavailable: {exc}",
        )

    ds = decompile_status(app_dir)
    sha = ds.get("sha")
    if ds.get("status") != "ready" or not sha:
        return SkillResult(
            success=True,
            data=None,
            text=(
                f"[query_call_graph] Decompile cache not ready "
                f"(status={ds.get('status')}). Run jadx via the workbench first."
            ),
        )

    cache_dir = decompile_cache_root(app_dir, sha)
    cg_status = call_graph.get_status(cache_dir)
    if cg_status.status != "ready":
        return SkillResult(
            success=True,
            data=None,
            text=(
                f"[query_call_graph] Call graph not ready "
                f"(status={cg_status.status}, error={cg_status.error or '(none)'}). "
                "Click Rebuild on the Settings → Status card or wait for the "
                "auto-build that fires after decompile."
            ),
        )

    if mode == "overview":
        package_prefix = (params.get("package_prefix") or "").strip() or None
        kind = (params.get("kind") or "").strip() or None
        include_external = _coerce_bool(params.get("include_external"), default=False)
        limit = _coerce_int(
            params.get("limit"), default=_OVERVIEW_DEFAULT_LIMIT,
            lo=1, hi=_OVERVIEW_HARD_CAP,
        )
        offset = _coerce_int(params.get("offset"), default=0, lo=0)
        out = call_graph.list_graph(
            cache_dir,
            package_prefix=package_prefix,
            kind=kind,
            include_external=include_external,
            limit=limit,
            offset=offset,
        )
        return SkillResult(success=True, data=out, text=_summarize_overview(out))

    if mode == "neighbors":
        node_ref = (params.get("node_ref") or "").strip()
        if not node_ref:
            return SkillResult(
                success=False,
                data=None,
                text=(
                    "[query_call_graph][neighbors] 'node_ref' is required "
                    "(numeric node id or smali_id like "
                    "'Lcom/example/Foo;->bar(I)V')."
                ),
            )
        out = call_graph.neighbors(cache_dir, node_ref)
        if out is None:
            return SkillResult(
                success=True,
                data={"node": None, "callers": [], "callees": [], "classes": []},
                text=f"[query_call_graph][neighbors] No node matched node_ref={node_ref!r}.",
            )
        return SkillResult(success=True, data=out, text=_summarize_neighbors(out))

    # paths
    source = (params.get("source") or "").strip()
    target = (params.get("target") or "").strip()
    if not source or not target:
        return SkillResult(
            success=False,
            data=None,
            text=(
                "[query_call_graph][paths] both 'source' and 'target' are required "
                "(numeric id or smali_id)."
            ),
        )
    max_hops = _coerce_int(
        params.get("max_hops"), default=_PATHS_DEFAULT_HOPS,
        lo=1, hi=_PATHS_HARD_HOPS,
    )
    max_paths = _coerce_int(
        params.get("max_paths"), default=_PATHS_DEFAULT_MAX,
        lo=1, hi=_PATHS_HARD_MAX,
    )
    include_external = _coerce_bool(params.get("include_external"), default=False)
    out = call_graph.paths(
        cache_dir, source, target,
        max_hops=max_hops, max_paths=max_paths, include_external=include_external,
    )
    return SkillResult(success=True, data=out, text=_summarize_paths(out))
