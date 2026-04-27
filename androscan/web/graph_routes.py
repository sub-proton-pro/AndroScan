"""HTTP routes for the Lane-2 static call graph (Hook Lab sub-step 4.1).

Sibling to :mod:`androscan.web.rag_routes` in spirit: the actual SQLite
backend lives in :mod:`androscan.analysis.call_graph`; this module only
does HTTP plumbing, argument validation, and per-app-dir lookup.

Endpoints (prefix ``/api/graph/{app_id}``):

* ``GET  /status``                — build state (missing/pending/ready/failed)
* ``POST /rebuild``               — invalidate + kick an async rebuild
* ``GET  /``                      — paginated nodes + intra-subgraph edges
* ``GET  /neighbors/{node_ref}``  — callers + callees for one node
* ``GET  /paths``                 — bounded BFS between two nodes

Auto-build hook
---------------

:func:`schedule_call_graph_build_after_decompile` mirrors the RAG
equivalent: once jadx finishes we kick a background call-graph build so
the user doesn't have to click Rebuild. The worker invokes apktool the
first time, then rebuilds from the cached smali on subsequent calls.
"""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from androscan.analysis import call_graph
from androscan.config import Config
from androscan.web.decompile_cache import (
    app_apk_info,
    cache_root_for as decompile_cache_root,
    get_status as decompile_status,
    sources_dir as decompile_sources_dir,
)


logger = logging.getLogger(__name__)


# Hard caps — keep the SQLite query cheap even if the UI forgets its own limits.
MAX_LIST_LIMIT = 5000
MAX_NEIGHBORS = 2000
MAX_PATH_HOPS = 12
MAX_PATH_RESULTS = 50


def schedule_call_graph_build_after_decompile(
    app_dir: Path,
    config: Config,
) -> None:
    """Invoked by ``/api/decompile`` on success: run apktool + parse + persist.

    Safe to call repeatedly — :func:`call_graph.build_index` short-circuits
    on a matching sha. We never block the HTTP caller; the build runs in a
    daemon thread.
    """
    status = decompile_status(app_dir)
    sha = status.get("sha")
    if status.get("status") != "ready" or not sha:
        return
    _apk_sha, apk_path = app_apk_info(app_dir)
    if not apk_path:
        logger.info("call_graph auto-build: no apk_path in app_meta for %s", app_dir)
        return

    cache_dir = decompile_cache_root(app_dir, sha)
    sources = decompile_sources_dir(app_dir, sha)
    apktool_cmd = getattr(config, "apktool_cmd", "apktool") or "apktool"

    cur = call_graph.get_status(cache_dir)
    if cur.status == "ready" and cur.sha == sha:
        return
    if cur.status == "pending":
        return

    call_graph.start_build_async(
        cache_dir,
        apk_path=Path(apk_path),
        sha=sha,
        apktool_cmd=apktool_cmd,
        sources_root=sources if sources.is_dir() else None,
    )


class PathsQuery(BaseModel):
    """Validated body for the ``/paths`` endpoint.

    (Express it as a pydantic model even though the route reads from query
    params; keeping a single source of truth for the validation rules.)
    """
    source: str = Field(..., max_length=500)
    target: str = Field(..., max_length=500)
    max_hops: int = Field(default=6, ge=1, le=MAX_PATH_HOPS)
    max_paths: int = Field(default=10, ge=1, le=MAX_PATH_RESULTS)
    include_external: bool = False


def build_graph_router(config: Config, app_dir_resolver) -> APIRouter:
    """Create the ``/api/graph`` router, wired to ``app_dir_resolver``.

    ``app_dir_resolver(app_id) -> Path`` must raise
    :class:`HTTPException(404)` for unknown ids (same contract as the RAG
    router) — we don't ship our own.
    """
    router = APIRouter(prefix="/api/graph", tags=["graph"])

    def _cache_dir_for(app_id: str) -> tuple[Path, str]:
        app_dir: Path = app_dir_resolver(app_id)
        ds = decompile_status(app_dir)
        if ds.get("status") != "ready" or not ds.get("sha"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Decompile cache is not ready. "
                    "POST /api/decompile/{app_id} first."
                ),
            )
        sha = str(ds["sha"])
        return decompile_cache_root(app_dir, sha), sha

    @router.get("/{app_id}/status")
    def graph_get_status(
        app_id: str, verbose: bool = Query(default=False)
    ) -> dict[str, Any]:
        """Surface the current build state. ``verbose=true`` also returns the
        raw ``meta`` table (used by the Settings tab's diagnostic drawer)."""
        app_dir: Path = app_dir_resolver(app_id)
        ds = decompile_status(app_dir)
        sha = ds.get("sha")
        if not sha:
            return {
                "app_id": app_id,
                "decompile_status": ds.get("status", "unknown"),
                "call_graph": {"status": "missing"},
            }
        cache_dir = decompile_cache_root(app_dir, sha)
        st = call_graph.get_status(cache_dir).to_dict()
        out: dict[str, Any] = {
            "app_id": app_id,
            "decompile_status": ds.get("status"),
            "call_graph": st,
        }
        if verbose:
            out["call_graph_meta"] = call_graph.dump_meta(cache_dir)
        return out

    @router.post("/{app_id}/rebuild")
    def graph_rebuild(
        app_id: str,
        drop_apktool: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Force a fresh build. ``drop_apktool=true`` also wipes the cached
        smali tree so apktool re-runs (use after upgrading apktool)."""
        app_dir: Path = app_dir_resolver(app_id)
        cache_dir, sha = _cache_dir_for(app_id)
        _apk_sha, apk_path = app_apk_info(app_dir)
        if not apk_path:
            raise HTTPException(
                status_code=409,
                detail="app_meta.json missing apk_path; can't rebuild the call graph.",
            )
        call_graph.invalidate(cache_dir, drop_apktool=drop_apktool)
        sources = decompile_sources_dir(app_dir, sha)
        apktool_cmd = getattr(config, "apktool_cmd", "apktool") or "apktool"
        kicked = call_graph.start_build_async(
            cache_dir,
            apk_path=Path(apk_path),
            sha=sha,
            apktool_cmd=apktool_cmd,
            sources_root=sources if sources.is_dir() else None,
        )
        return {"app_id": app_id, "sha": sha, "kicked": kicked}

    @router.get("/{app_id}")
    def graph_list(
        app_id: str,
        package_prefix: Optional[str] = Query(default=None, max_length=200),
        kind: Optional[str] = Query(default=None, max_length=32),
        include_external: bool = Query(default=False),
        limit: int = Query(default=500, ge=1, le=MAX_LIST_LIMIT),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """Paginated node + edge dump — input to the 4.2 Cytoscape renderer.

        Edges in the response are restricted to those whose *both endpoints*
        are in the returned node window, so the client can render the
        subgraph without any extra round-trips.
        """
        cache_dir, sha = _cache_dir_for(app_id)
        payload = call_graph.list_graph(
            cache_dir,
            package_prefix=package_prefix,
            kind=kind,
            include_external=include_external,
            limit=limit,
            offset=offset,
        )
        return {
            "app_id": app_id,
            "sha": sha,
            "limit": limit,
            "offset": offset,
            **payload,
        }

    @router.get("/{app_id}/neighbors/{node_ref:path}")
    def graph_neighbors(
        app_id: str,
        node_ref: str,
        limit_each: int = Query(default=200, ge=1, le=MAX_NEIGHBORS),
    ) -> dict[str, Any]:
        """Callers + callees of a single node.

        ``node_ref`` can be either a numeric ``nodes.id`` (as a path segment)
        or the URL-encoded smali signature. The ``:path`` converter accepts
        the embedded ``/`` and ``;`` characters that make up a smali id.
        """
        cache_dir, _sha = _cache_dir_for(app_id)
        decoded = urllib.parse.unquote(node_ref)
        res = call_graph.neighbors(cache_dir, decoded, limit_each=limit_each)
        if res is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown node: {decoded!r}",
            )
        return {"app_id": app_id, **res}

    @router.get("/{app_id}/paths")
    def graph_paths(
        app_id: str,
        source: str = Query(..., max_length=500),
        target: str = Query(..., max_length=500),
        max_hops: int = Query(default=6, ge=1, le=MAX_PATH_HOPS),
        max_paths: int = Query(default=10, ge=1, le=MAX_PATH_RESULTS),
        include_external: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Up to ``max_paths`` simple paths of length ≤ ``max_hops`` between
        two nodes. Capped so a hub-star graph can't melt the server."""
        cache_dir, _sha = _cache_dir_for(app_id)
        res = call_graph.paths(
            cache_dir,
            urllib.parse.unquote(source),
            urllib.parse.unquote(target),
            max_hops=max_hops,
            max_paths=max_paths,
            include_external=include_external,
        )
        return {"app_id": app_id, **res}

    return router
