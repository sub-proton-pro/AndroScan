"""HTTP routes for the Lane-1 RAG indexer + decompile auto-build glue.

Kept separate from ``app.py`` so the RAG concerns (status / query / rebuild)
have a single owner and can evolve without touching unrelated handlers.

Auto-build flow
---------------

1. The user POSTs ``/api/decompile/{app_id}`` (or it is implicitly kicked off
   by an Inspect-tab interaction).
2. ``decompile_cache.start_decompile`` runs jadx in a background thread.
3. When jadx succeeds we call :func:`schedule_rag_build_after_decompile`'s
   ``on_done`` callback, which spawns a *second* daemon thread that
   instantiates the configured embedding provider and writes the SQLite
   index next to the decompiled sources.

Both phases are observable independently:
``GET /api/decompile/{app_id}`` for jadx, ``GET /api/rag/{app_id}/status``
for the embedder.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from androscan.config import Config
from androscan.web.decompile_cache import (
    cache_root_for as decompile_cache_root,
    get_status as decompile_status,
    sources_dir as decompile_sources_dir,
)

logger = logging.getLogger(__name__)


class RagQueryBody(BaseModel):
    text: str = Field(..., max_length=4000, description="Free-text query to embed")
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    file_substr: Optional[str] = Field(default=None, max_length=400)
    package_prefix: Optional[str] = Field(default=None, max_length=200)
    kinds: Optional[list[str]] = Field(default=None, max_length=4)


def _provider_factory(config: Config):
    """Defer provider construction (model load / network probe) to the call site."""
    def make():
        from androscan.rag.embed import get_provider
        return get_provider(config)
    return make


def schedule_rag_build_after_decompile(
    app_dir: Path,
    config: Config,
) -> None:
    """If decompile is ``ready`` and no compatible RAG index exists, kick a build.

    Safe to call repeatedly: ``rag.build_index`` short-circuits when the
    existing index already matches ``(sha, provider, model)``. We never block
    the caller — the build runs in its own daemon thread.
    """
    status = decompile_status(app_dir)
    sha = status.get("sha")
    if status.get("status") != "ready" or not sha:
        return

    cache_dir = decompile_cache_root(app_dir, sha)
    sources = decompile_sources_dir(app_dir, sha)
    if not sources.is_dir():
        return

    # Lazy import keeps the optional embedding deps from blocking ``app.py`` import.
    from androscan.rag.index import get_status as rag_status, start_build_async

    rs = rag_status(cache_dir)
    if rs.status == "ready" and rs.sha == sha:
        return  # already up to date for this sha
    if rs.status == "pending":
        return  # another worker is on it

    start_build_async(
        cache_dir,
        sources_root=sources,
        sha=sha,
        provider_factory=_provider_factory(config),
    )


def build_rag_router(config: Config, app_dir_resolver) -> APIRouter:
    """Return a FastAPI router exposing ``/api/rag/{app_id}/...`` endpoints.

    ``app_dir_resolver`` is a callable ``(app_id) -> Path`` that performs the
    project-relative directory lookup (and raises :class:`HTTPException` on
    unknown ``app_id``). We pass it in instead of importing ``app.py``'s
    helper to keep the dependency direction one-way.
    """
    router = APIRouter(prefix="/api/rag", tags=["rag"])

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
    def rag_get_status(app_id: str, verbose: bool = Query(default=False)) -> dict[str, Any]:
        from androscan.rag.index import dump_meta, get_status as rag_status

        app_dir: Path = app_dir_resolver(app_id)
        ds = decompile_status(app_dir)
        sha = ds.get("sha")
        if not sha:
            return {"app_id": app_id, "decompile_status": ds.get("status", "unknown"), "rag": {"status": "missing"}}
        cache_dir = decompile_cache_root(app_dir, sha)
        rs = rag_status(cache_dir).to_dict()
        out = {"app_id": app_id, "decompile_status": ds.get("status"), "rag": rs}
        if verbose:
            out["rag_meta"] = dump_meta(cache_dir)
        return out

    @router.post("/{app_id}/rebuild")
    def rag_rebuild(app_id: str) -> dict[str, Any]:
        from androscan.rag.index import invalidate, start_build_async

        cache_dir, sha = _cache_dir_for(app_id)
        invalidate(cache_dir)
        sources = decompile_sources_dir(app_dir_resolver(app_id), sha)
        kicked = start_build_async(
            cache_dir,
            sources_root=sources,
            sha=sha,
            provider_factory=_provider_factory(config),
        )
        return {"app_id": app_id, "sha": sha, "kicked": kicked}

    @router.post("/{app_id}/query")
    def rag_query(app_id: str, body: RagQueryBody) -> dict[str, Any]:
        from androscan.rag.embed import EmbedProviderError
        from androscan.rag.search import query as rag_query_fn

        cache_dir, sha = _cache_dir_for(app_id)
        try:
            provider = _provider_factory(config)()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"embed provider unavailable: {e}")
        top_k = body.top_k or getattr(config, "rag_top_k_default", 8)
        try:
            hits = rag_query_fn(
                cache_dir,
                body.text,
                provider,
                top_k=top_k,
                file_substr=body.file_substr,
                package_prefix=body.package_prefix,
                kinds=body.kinds,
            )
        except EmbedProviderError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {
            "app_id": app_id,
            "sha": sha,
            "top_k": top_k,
            "provider": {"name": provider.name, "model": provider.model, "dim": provider.dim},
            "hits": [h.to_dict() for h in hits],
        }

    return router
