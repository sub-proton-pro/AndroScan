"""Top-k semantic search over the SQLite RAG index.

Implementation is intentionally simple — load every vector, compute cosine,
return the top ``k``. For the apps we target (a few thousand to a few tens
of thousands of method-sized chunks) brute force on a normalized 384-dim
matrix takes single-digit milliseconds with numpy. The interface here is
shaped so a future ``sqlite-vec`` ANN backend can replace this body
without touching callers.

NumPy is preferred (fast vectorized cosine). When NumPy is not installed,
we fall back to pure-Python — slower but functional. Production deployments
that need RAG should install the ``[rag]`` extra which pulls NumPy in.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from androscan.rag.embed import EmbedProvider, EmbedProviderError
from androscan.rag.index import IndexStatus, get_status, rag_db_path

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard
    import numpy as np  # type: ignore[import-not-found]

    _HAS_NUMPY = True
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


@dataclass(frozen=True)
class SearchHit:
    """One ranked chunk returned by :func:`query`."""

    chunk_id: str
    file: str
    package: str
    class_name: str
    method_name: Optional[str]
    kind: str  # "class_header" | "method"
    start_line: int
    end_line: int
    content: str
    score: float  # cosine similarity in [-1, 1]; higher is better

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "file": self.file,
            "package": self.package,
            "class_name": self.class_name,
            "method_name": self.method_name,
            "kind": self.kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "content": self.content,
            "score": round(self.score, 6),
        }


def _decode_vec(blob: bytes, dim: int) -> tuple[float, ...]:
    return struct.unpack(f"<{dim}f", blob)


def _cosine_pure(query_vec: Sequence[float], rows: Sequence[tuple[Any, ...]], dim: int) -> list[tuple[int, float]]:
    """Return ``[(row_idx, score)]`` sorted by score desc."""
    qn = math.sqrt(sum(x * x for x in query_vec)) or 1.0
    qn_inv = 1.0 / qn
    qv = [x * qn_inv for x in query_vec]
    scored: list[tuple[int, float]] = []
    for i, row in enumerate(rows):
        v = _decode_vec(row[-1], dim)
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        s = sum(a * b for a, b in zip(qv, v)) / n
        scored.append((i, s))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored


def _cosine_numpy(query_vec: Sequence[float], rows: Sequence[tuple[Any, ...]], dim: int) -> list[tuple[int, float]]:
    """NumPy-vectorized cosine similarity. Same return shape as ``_cosine_pure``."""
    assert np is not None  # for type-checkers
    n = len(rows)
    mat = np.empty((n, dim), dtype=np.float32)
    for i, row in enumerate(rows):
        mat[i] = np.frombuffer(row[-1], dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1)
    norms[norms == 0] = 1.0
    mat = mat / norms[:, None]
    q = np.asarray(list(query_vec), dtype=np.float32)
    qn = float(np.linalg.norm(q)) or 1.0
    q = q / qn
    scores = mat @ q
    order = np.argsort(-scores)
    return [(int(i), float(scores[i])) for i in order]


# ---------------------------------------------------------------------------
# Filter clause builder
#
# Filters are intentionally narrow: callers may scope by file (substring) or
# by package prefix. Class- and method-name filters happen post-hoc in Python
# because they're only meaningful for top-N narrowing, not index pruning.


def _build_where(
    file_substr: Optional[str],
    package_prefix: Optional[str],
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if file_substr:
        where.append("file LIKE ?")
        params.append(f"%{file_substr}%")
    if package_prefix:
        where.append("(package = ? OR package LIKE ?)")
        params.append(package_prefix)
        params.append(f"{package_prefix}.%")
    if not where:
        return "", params
    return " WHERE " + " AND ".join(where), params


# ---------------------------------------------------------------------------
# Public query


def query(
    decompile_cache_dir: Path,
    text: str,
    provider: EmbedProvider,
    *,
    top_k: int = 8,
    file_substr: Optional[str] = None,
    package_prefix: Optional[str] = None,
    kinds: Optional[Sequence[str]] = None,
) -> list[SearchHit]:
    """Embed ``text`` and return the top ``top_k`` similar chunks.

    Raises :class:`EmbedProviderError` if the index is missing/incompatible
    with the supplied provider, or if the provider rejects the embedding.
    """
    text = (text or "").strip()
    if not text:
        return []
    status: IndexStatus = get_status(decompile_cache_dir)
    if status.status != "ready":
        raise EmbedProviderError(
            f"RAG index is not ready (status={status.status!r}, "
            f"error={status.error!r})"
        )
    if status.dim is None:
        raise EmbedProviderError("RAG index has no recorded dim")
    if status.provider_name != provider.name or status.provider_model != provider.model:
        raise EmbedProviderError(
            f"RAG provider mismatch: index built with "
            f"{status.provider_name}/{status.provider_model}, "
            f"current is {provider.name}/{provider.model}. Rebuild required."
        )
    if status.dim != provider.dim:
        raise EmbedProviderError(
            f"RAG dim mismatch: index dim={status.dim}, provider dim={provider.dim}"
        )

    db = rag_db_path(decompile_cache_dir)
    where, where_params = _build_where(file_substr, package_prefix)
    sql = (
        "SELECT id, file, package, class_name, method_name, kind, "
        "       start_line, end_line, content, vector "
        "FROM chunks" + where
    )

    with sqlite3.connect(str(db), timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, where_params)
        rows: list[tuple[Any, ...]] = cur.fetchall()

    if not rows:
        return []

    if kinds:
        wanted = {str(k) for k in kinds}
        rows = [r for r in rows if r["kind"] in wanted]
        if not rows:
            return []

    # Embed the query (single text -> single vector).
    qvecs = provider.embed([text])
    if not qvecs or not qvecs[0]:
        raise EmbedProviderError("provider returned empty query vector")
    qv = qvecs[0]

    # Tuple form for cosine helpers (uses last column = vector blob).
    tuple_rows = [tuple(r) for r in rows]
    if _HAS_NUMPY:
        order = _cosine_numpy(qv, tuple_rows, status.dim)
    else:
        order = _cosine_pure(qv, tuple_rows, status.dim)

    hits: list[SearchHit] = []
    for idx, score in order[: max(0, top_k)]:
        r = rows[idx]
        hits.append(
            SearchHit(
                chunk_id=r["id"],
                file=r["file"],
                package=r["package"],
                class_name=r["class_name"],
                method_name=r["method_name"],
                kind=r["kind"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                content=r["content"],
                score=score,
            )
        )
    return hits
