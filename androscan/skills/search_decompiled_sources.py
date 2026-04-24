"""LLM-requestable skill: semantic search over decompiled sources via Lane-1 RAG.

Thin wrapper around :mod:`androscan.rag.search`. Available to the workflow
agent (``tier="llm"``) so the planner can ask, e.g., *"find code that
handles password verification"* and get top-k method-sized chunks back
without hand-crafting jadx + grep calls.

Returns chunk previews as ``data`` and a compact human/LLM-readable
summary in ``text``. Designed to fail open (returns ``success=True`` with
an empty list) when the index is missing or the embedding provider is not
installed, so it never derails an analysis run because of optional infra.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="search_decompiled_sources",
    description=(
        "Semantic top-k search over decompiled Java/Kotlin sources using the "
        "Lane-1 RAG index. Use for free-text queries like 'password validation' "
        "or 'AES key derivation'. Returns method-sized chunks with file paths "
        "and line ranges."
    ),
    params_schema={
        "query": "free-text natural language query (e.g. 'password verification')",
        "top_k": "optional max results to return (default 8, cap 20)",
        "package_prefix": "optional package prefix to scope (e.g. 'com.example.weakbank')",
        "file_substr": "optional substring filter on the source file path",
    },
    tier="llm",
)

_MAX_TOP_K = 20
_PREVIEW_CHARS = 320


def _resolve_app_dir(context: SkillContext) -> Optional[Path]:
    """Return ``apps/<app>/`` from the skill context, or ``None``."""
    if not context.run_folder:
        return None
    rf = Path(context.run_folder)
    return rf.parent if rf.parent.exists() else None


def _format_hit_text(hit_dict: dict[str, Any]) -> str:
    method = hit_dict.get("method_name") or hit_dict.get("kind")
    return (
        f"  - {hit_dict['file']}:{hit_dict['start_line']}-{hit_dict['end_line']} "
        f"({hit_dict['class_name']}.{method}, score={hit_dict['score']:.3f})"
    )


def execute(params: dict, context: SkillContext) -> SkillResult:
    query = (params.get("query") or "").strip()
    if not query:
        return SkillResult(
            success=False,
            data=None,
            text="[search_decompiled_sources] 'query' is required.",
        )
    top_k = params.get("top_k") or 8
    try:
        top_k = max(1, min(_MAX_TOP_K, int(top_k)))
    except (TypeError, ValueError):
        top_k = 8

    package_prefix = (params.get("package_prefix") or "").strip() or None
    file_substr = (params.get("file_substr") or "").strip() or None

    app_dir = _resolve_app_dir(context)
    if app_dir is None:
        return SkillResult(
            success=True,
            data=[],
            text="[search_decompiled_sources] No app context available; returning empty.",
        )

    # Lazy imports keep skill discovery cheap on machines without the [rag] extra.
    try:
        from androscan.rag.embed import EmbedProviderError, get_provider
        from androscan.rag.index import get_status as rag_status
        from androscan.rag.search import query as rag_query
        from androscan.web.decompile_cache import (
            cache_root_for as decompile_cache_root,
            get_status as decompile_status,
        )
    except Exception as e:
        return SkillResult(
            success=True,
            data=[],
            text=f"[search_decompiled_sources] RAG layer unavailable: {e}",
        )

    ds = decompile_status(app_dir)
    sha = ds.get("sha")
    if ds.get("status") != "ready" or not sha:
        return SkillResult(
            success=True,
            data=[],
            text=(
                "[search_decompiled_sources] Decompile cache not ready "
                f"(status={ds.get('status')}). Run jadx via the workbench first."
            ),
        )

    cache_dir = decompile_cache_root(app_dir, sha)
    rs = rag_status(cache_dir)
    if rs.status != "ready":
        return SkillResult(
            success=True,
            data=[],
            text=(
                f"[search_decompiled_sources] RAG index not ready "
                f"(status={rs.status}, error={rs.error or '(none)'})."
            ),
        )

    try:
        provider = get_provider(context.config)
    except EmbedProviderError as e:
        return SkillResult(
            success=True,
            data=[],
            text=f"[search_decompiled_sources] Embed provider unavailable: {e}",
        )

    try:
        hits = rag_query(
            cache_dir,
            query,
            provider,
            top_k=top_k,
            file_substr=file_substr,
            package_prefix=package_prefix,
        )
    except EmbedProviderError as e:
        return SkillResult(
            success=False,
            data=None,
            text=f"[search_decompiled_sources] query failed: {e}",
        )

    if not hits:
        return SkillResult(
            success=True,
            data=[],
            text=f"[search_decompiled_sources] No matches for {query!r}.",
        )

    data = [h.to_dict() for h in hits]
    lines = [
        f"[search_decompiled_sources] {len(hits)} match(es) for {query!r}:"
    ]
    lines.extend(_format_hit_text(d) for d in data)
    # Inline previews keep the LLM grounded without forcing it to call
    # ``get_decompiled_method`` for every candidate.
    lines.append("\n--- previews ---")
    for d in data:
        body = d["content"].strip()
        if len(body) > _PREVIEW_CHARS:
            body = body[: _PREVIEW_CHARS - 1] + "…"
        lines.append(
            f"\n# {d['file']}:{d['start_line']}-{d['end_line']} "
            f"({d['class_name']}.{d.get('method_name') or d['kind']})"
        )
        lines.append(body)
    return SkillResult(success=True, data=data, text="\n".join(lines))
