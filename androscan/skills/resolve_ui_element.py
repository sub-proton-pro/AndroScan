"""LLM-requestable skill: pick the most likely Android UI handler for a tap.

Takes the *raw* output of the click-to-code mapper (an element + a set of
deterministic handler candidates from regex-grep, plus the foreground
activity) and produces a single ``best`` answer with reasoning, plus a
ranked alternatives list. Optionally enriches the candidate set with
Lane-1 RAG hits when the embedding index is available.

Design:

- This is a **pure-function fuser** — it does not call an LLM. The "llm"
  tier label means *advertised in the prompt catalog so the planner can
  call it as a tool*, mirroring ``search_decompiled_sources``.
- Scoring is deterministic and explainable: a small additive model over
  (handler kind, foreground-activity match, line position, RAG cosine).
  The reasoning string lists which factors fired so the agent / UI can
  trust the pick.
- Fail-open: missing RAG, missing app context, or empty input never
  raises — the skill returns an empty result with a useful ``text``.

Used by:

- the LLM workflow agent (planner can request it);
- ``POST /api/inspect/map`` which calls it inline to attach a ``best``
  block to the response so the Inspect-tab UI gets a single ranked
  answer without a round-trip to the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="resolve_ui_element",
    description=(
        "Given a UI element (from uiautomator dump) + the foreground activity + "
        "raw handler candidates (from regex grep), pick the most likely class+method "
        "that handles a tap on it. Optionally augments candidates with Lane-1 RAG "
        "hits over decompiled sources. Returns {best, alternatives, rag_hits, "
        "reasoning, element}."
    ),
    params_schema={
        "element": (
            "dict from inspect_map.find_element_at — keys: resource_id, cls, "
            "text, content_desc, package, clickable, bounds. Optional but "
            "strongly preferred."
        ),
        "foreground_activity": (
            "FQCN of the focused activity, e.g. 'com.example.app/.MainActivity'. "
            "Used to bias scoring toward in-activity handlers."
        ),
        "candidates": (
            "list of dicts from inspect_map.find_handlers (each with file, line, "
            "snippet, kind). Optional."
        ),
        "app_id": "app_id (apps/<app_id>/) for optional RAG enrichment.",
        "top_k": "max RAG hits to fetch (default 5, cap 10).",
    },
    tier="llm",
)

# ---------------------------------------------------------------------------
# Scoring
#
# Each candidate gets a base weight from its handler-grep kind. We then add
# bonuses for matching the foreground activity (very strong signal),
# ``MainActivity``-ish files, and a small bonus for early-line matches
# (handler wiring is usually in onCreate near the top of the file).

# Higher = stronger signal that this line actually handles the tap.
_KIND_BASE_SCORE: dict[str, float] = {
    "findViewById": 1.00,
    "onClick_near": 0.80,
    "compose_id": 0.60,
    "reference": 0.20,
    # RAG-derived candidate (synthesised below); we use the cosine score
    # directly and clamp to a sensible range.
    "rag": 0.00,
}

# Bonuses (additive on top of base score).
_FOREGROUND_BONUS = 0.50  # candidate file == foreground activity simple name
_ACTIVITY_FILE_BONUS = 0.10  # candidate file ends with "Activity.java"
_EARLY_LINE_BONUS_MAX = 0.10  # linearly decays from this to 0 between line 1 and 200

# RAG enrichment is intentionally bounded — too many hits drown the
# deterministic signals.
_RAG_DEFAULT_TOP_K = 5
_RAG_MAX_TOP_K = 10
# Cosine similarity → score; we keep RAG below findViewById so a clean
# regex match in the foreground activity always wins.
_RAG_COSINE_TO_SCORE = 0.50


@dataclass
class _ScoredCandidate:
    """Internal: a candidate plus its computed score and the reasons that fired."""
    file: str
    line: int
    snippet: str
    kind: str
    class_name: Optional[str]
    method_name: Optional[str]
    source: str  # "deterministic" | "rag"
    score: float
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "snippet": self.snippet,
            "kind": self.kind,
            "class_name": self.class_name,
            "method_name": self.method_name,
            "source": self.source,
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
        }


def _foreground_simple_name(foreground_activity: Optional[str]) -> Optional[str]:
    """``com.example.app/.MainActivity`` → ``MainActivity`` (or None)."""
    if not foreground_activity or not isinstance(foreground_activity, str):
        return None
    tail = foreground_activity.rsplit("/", 1)[-1]
    if tail.startswith("."):
        tail = tail[1:]
    tail = tail.rsplit(".", 1)[-1]
    return tail or None


def _file_simple_name(file_rel: str) -> str:
    """``com/example/app/MainActivity.java`` → ``MainActivity``."""
    base = Path(file_rel).name
    for suf in (".java", ".kt"):
        if base.endswith(suf):
            return base[: -len(suf)]
    return base


def _early_line_bonus(line: int) -> float:
    if line <= 0:
        return 0.0
    if line >= 200:
        return 0.0
    # Linear decay from full bonus at line 1 to 0 at line 200.
    return _EARLY_LINE_BONUS_MAX * (1.0 - (line - 1) / 199.0)


def _score_deterministic(
    cand: dict[str, Any], foreground_simple: Optional[str]
) -> _ScoredCandidate:
    kind = (cand.get("kind") or "").strip() or "reference"
    base = _KIND_BASE_SCORE.get(kind, 0.0)
    reasons: list[str] = [f"kind={kind} (base={base:.2f})"]

    file_rel = (cand.get("file") or "").strip()
    line = int(cand.get("line") or 0)
    file_simple = _file_simple_name(file_rel)

    score = base
    if foreground_simple and file_simple == foreground_simple:
        score += _FOREGROUND_BONUS
        reasons.append(f"foreground match (+{_FOREGROUND_BONUS:.2f})")
    if file_rel.endswith("Activity.java") or file_rel.endswith("Activity.kt"):
        score += _ACTIVITY_FILE_BONUS
        reasons.append(f"activity-named file (+{_ACTIVITY_FILE_BONUS:.2f})")
    elb = _early_line_bonus(line)
    if elb > 0:
        score += elb
        reasons.append(f"early line {line} (+{elb:.3f})")

    # ``inspect_map.find_handlers`` (since the Phase 10 follow-up) carries a
    # best-effort ``method_name`` derived from a back-walk to the nearest
    # method header. ``None`` is the honest "couldn't pin one" answer; we
    # forward it as-is so the Inspect → Trace seed can fall back to a
    # class-prefix-only signature when needed (the picker UI in
    # ``LabTraceMode.tsx`` then takes over).
    raw_method = cand.get("method_name")
    method_name = raw_method.strip() if isinstance(raw_method, str) and raw_method.strip() else None
    if method_name:
        reasons.append(f"enclosing method: {method_name}")

    return _ScoredCandidate(
        file=file_rel,
        line=line,
        snippet=(cand.get("snippet") or "").strip(),
        kind=kind,
        class_name=file_simple or None,
        method_name=method_name,
        source="deterministic",
        score=score,
        reasons=reasons,
    )


def _score_rag_hit(hit: dict[str, Any], foreground_simple: Optional[str]) -> _ScoredCandidate:
    cosine = float(hit.get("score") or 0.0)
    base = _RAG_COSINE_TO_SCORE * max(0.0, min(1.0, cosine))
    reasons = [f"rag cosine={cosine:.3f} (base={base:.3f})"]

    file_rel = (hit.get("file") or "").strip()
    file_simple = _file_simple_name(file_rel)
    class_name = hit.get("class_name") or file_simple or None
    method_name = hit.get("method_name") or None

    score = base
    if foreground_simple and (file_simple == foreground_simple or class_name == foreground_simple):
        score += _FOREGROUND_BONUS
        reasons.append(f"foreground match (+{_FOREGROUND_BONUS:.2f})")

    return _ScoredCandidate(
        file=file_rel,
        line=int(hit.get("start_line") or 0),
        snippet=((hit.get("content") or "")[:400]).strip(),
        kind="rag",
        class_name=class_name,
        method_name=method_name,
        source="rag",
        score=score,
        reasons=reasons,
    )


def _build_rag_query(element: Optional[dict[str, Any]]) -> Optional[str]:
    """Synthesise a free-text RAG query from element metadata.

    Order of preference: visible text > content-desc > short resource id.
    Returns ``None`` if there is nothing to anchor on (RAG would just be
    noise in that case).
    """
    if not element:
        return None
    text = (element.get("text") or "").strip()
    desc = (element.get("content_desc") or "").strip()
    rid = (element.get("resource_id") or "").strip()
    short = rid.rsplit("/", 1)[-1] if "/" in rid else rid

    parts: list[str] = []
    if text:
        parts.append(f"click handler for '{text}'")
    if desc and desc not in parts:
        parts.append(f"content description '{desc}'")
    if short:
        parts.append(f"resource id {short}")
    if not parts:
        return None
    return "; ".join(parts)


def _resolve_app_dir_from_context(context: SkillContext, app_id: Optional[str]) -> Optional[Path]:
    """Locate ``apps/<app_id>/`` from the run folder + the (optional) app_id arg.

    The agent typically passes an explicit ``app_id`` because at planning
    time the run folder is the active run, but the tap belongs to a
    project on disk — these usually match, so falling back to
    ``run_folder.parent`` is fine.
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


def _maybe_rag_hits(
    config: Any,
    app_dir: Optional[Path],
    query: Optional[str],
    top_k: int,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Best-effort RAG enrichment. Returns (hits_as_dicts, error_text_or_None).

    Pure function over ``config`` + ``app_dir`` so the web layer can call
    it without constructing a SkillContext.
    """
    if not query:
        return [], None
    if app_dir is None:
        return [], "no app dir for RAG"

    try:
        from androscan.rag.embed import EmbedProviderError, get_provider
        from androscan.rag.index import get_status as rag_status
        from androscan.rag.search import query as rag_query
        from androscan.web.decompile_cache import (
            cache_root_for as decompile_cache_root,
            get_status as decompile_status,
        )
    except Exception as e:
        return [], f"rag layer unavailable: {e}"

    ds = decompile_status(app_dir)
    sha = ds.get("sha")
    if ds.get("status") != "ready" or not sha:
        return [], f"decompile not ready (status={ds.get('status')})"

    cache_dir = decompile_cache_root(app_dir, sha)
    rs = rag_status(cache_dir)
    if rs.status != "ready":
        return [], f"rag index not ready (status={rs.status})"

    try:
        provider = get_provider(config)
    except EmbedProviderError as e:
        return [], f"embed provider unavailable: {e}"

    try:
        hits = rag_query(cache_dir, query, provider, top_k=top_k)
    except EmbedProviderError as e:
        return [], f"rag query failed: {e}"

    return [h.to_dict() for h in hits], None


def resolve(
    *,
    element: Optional[dict[str, Any]],
    foreground_activity: Optional[str],
    candidates: list[dict[str, Any]],
    config: Any = None,
    app_dir: Optional[Path] = None,
    top_k: int = _RAG_DEFAULT_TOP_K,
) -> dict[str, Any]:
    """Pure-function entry point used by both ``execute()`` and the web layer.

    Returns the same shape as ``SkillResult.data`` plus a ``text`` summary
    so callers can produce a single ``best``/``alternatives``/``rag_hits``
    block without going through the skill registry.
    """
    top_k = max(1, min(_RAG_MAX_TOP_K, int(top_k or _RAG_DEFAULT_TOP_K)))

    foreground_simple = _foreground_simple_name(foreground_activity)

    scored: list[_ScoredCandidate] = []
    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        scored.append(_score_deterministic(cand, foreground_simple))

    rag_query_text = _build_rag_query(element)
    rag_hits, rag_error = _maybe_rag_hits(config, app_dir, rag_query_text, top_k)
    for hit in rag_hits:
        scored.append(_score_rag_hit(hit, foreground_simple))

    scored.sort(key=lambda c: (-c.score, c.file, c.line))

    short_rid = ""
    if element:
        rid = (element.get("resource_id") or "")
        short_rid = rid.rsplit("/", 1)[-1] if "/" in rid else rid

    if not scored:
        msg_parts = ["[resolve_ui_element] No handler candidates."]
        if not candidates:
            msg_parts.append("No deterministic candidates were supplied.")
        if rag_query_text and rag_error:
            msg_parts.append(f"RAG: {rag_error}.")
        if not rag_query_text:
            msg_parts.append("Element had no text/content-desc/resource-id to query RAG with.")
        return {
            "best": None,
            "alternatives": [],
            "rag_hits": rag_hits,
            "rag_query": rag_query_text,
            "rag_error": rag_error,
            "element": element,
            "foreground_activity": foreground_activity,
            "text": " ".join(msg_parts),
        }

    best = scored[0]
    alternatives = [c.to_dict() for c in scored[1:6]]

    summary_lines = [
        f"[resolve_ui_element] Best handler: "
        f"{best.class_name or '?'}{('.' + best.method_name) if best.method_name else ''} "
        f"@ {best.file}:{best.line} (score={best.score:.3f}, source={best.source})",
        f"  reasons: {', '.join(best.reasons)}",
    ]
    if foreground_activity:
        summary_lines.append(f"  foreground: {foreground_activity}")
    if short_rid:
        summary_lines.append(f"  element: resource_id={short_rid}")
    if alternatives:
        summary_lines.append(f"  {len(alternatives)} alternative(s):")
        for alt in alternatives:
            summary_lines.append(
                f"    - {alt['file']}:{alt['line']} kind={alt['kind']} score={alt['score']:.3f}"
            )
    if rag_error:
        summary_lines.append(f"  rag note: {rag_error}")

    return {
        "best": best.to_dict(),
        "alternatives": alternatives,
        "rag_hits": rag_hits,
        "rag_query": rag_query_text,
        "rag_error": rag_error,
        "element": element,
        "foreground_activity": foreground_activity,
        "text": "\n".join(summary_lines),
    }


def execute(params: dict, context: SkillContext) -> SkillResult:
    element = params.get("element") if isinstance(params.get("element"), dict) else None
    foreground_activity = (params.get("foreground_activity") or "").strip() or None
    candidates_raw = params.get("candidates") or []
    if not isinstance(candidates_raw, list):
        candidates_raw = []
    app_id = (params.get("app_id") or "").strip() or None

    try:
        top_k = int(params.get("top_k") or _RAG_DEFAULT_TOP_K)
    except (TypeError, ValueError):
        top_k = _RAG_DEFAULT_TOP_K

    app_dir = _resolve_app_dir_from_context(context, app_id)

    result = resolve(
        element=element,
        foreground_activity=foreground_activity,
        candidates=candidates_raw,
        config=getattr(context, "config", None),
        app_dir=app_dir,
        top_k=top_k,
    )
    text = result.pop("text")
    return SkillResult(success=True, data=result, text=text)
