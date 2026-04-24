"""Lane-1 RAG over decompiled sources.

This package is a self-contained adapter layer that:

* chunks ``.java`` / ``.kt`` files produced by jadx into method-sized units
  (``chunking``),
* embeds those chunks via a pluggable provider (``embed``),
* persists them in a per-app SQLite database keyed by the APK ``sha256``
  (``index``),
* answers semantic top-k queries (``search``).

The module is **lazy-importable**: importing ``androscan.rag`` does not pull
in any optional embedding backend. Callers that actually need embeddings
must request them via :func:`embed.get_provider`, which surfaces a clear
error if the chosen backend is missing.

The module is also **stateless at import time** — there are no module-level
connections, threads, or caches. Each public entry point opens a short-lived
SQLite connection scoped to the call.

See ``docs/DECISIONS.md`` for the rationale behind the brute-force-cosine
storage path and the deferred ``sqlite-vec`` swap.
"""

from __future__ import annotations

from androscan.rag.chunking import Chunk, chunk_sources
from androscan.rag.index import (
    INDEX_FILENAME,
    IndexStatus,
    build_index,
    get_status,
    invalidate,
    rag_db_path,
)
from androscan.rag.search import SearchHit, query

__all__ = [
    "Chunk",
    "INDEX_FILENAME",
    "IndexStatus",
    "SearchHit",
    "build_index",
    "chunk_sources",
    "get_status",
    "invalidate",
    "query",
    "rag_db_path",
]
