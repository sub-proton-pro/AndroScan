"""Per-app SQLite cache for ``BehaviorAnchor`` payloads — Phase 10 sub-step 10.5.

Lives at ``apps/<app_id>/.decompiled/<sha>/trace.sqlite`` (alongside
the call-graph SQLite from DEC-016) so both indices share the
decompile-cache lifetime: when the operator clicks "Re-decompile" the
sha changes, both caches get a fresh directory, and stale data
disappears without an explicit purge.

Why a dedicated SQLite (not a JSON sidecar like
``skill_results_cache.json``)
--------------------------------------------------------------------

* Anchor payloads are big (the decisions + plans tuple for a 30-method
  closure runs to tens of KB after JSON marshalling). A single JSON
  file would have to be re-read + re-written in full on every cache
  write, which is fine for the skill_results cache (~few KB total)
  but quadratic for the trace cache.
* 10.6's REST routes will read this file directly without invoking
  the skill (``GET /api/trace/<app>/anchor?...`` should not pay for
  the full ``parse → slice → classify → plan → LLM`` chain when the
  payload is already cached). Keeping the layer pure (no Smali / call-
  graph imports) is what enables that separation.
* The schema-versioning + atomic-replace patterns from
  ``call_graph._connect`` already exist; mirroring them keeps the
  fail-safe story uniform.

Schema
------

::

    CREATE TABLE meta    (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE anchors (
        entry_smali_id TEXT NOT NULL,
        hops           INTEGER NOT NULL,
        payload_json   TEXT NOT NULL,
        created_at     REAL NOT NULL,
        PRIMARY KEY (entry_smali_id, hops)
    );

The ``meta`` table carries ``schema_version`` only in v1 — additional
keys (``built_at`` / ``last_invalidation`` etc.) may land in later
sub-steps but the v1 reader already tolerates unknown keys. Schema
mismatches surface as ``status="failed"`` from :func:`get_status` so
the route layer in 10.6 can offer a one-click "Reset trace cache"
without prompting the operator to delete files manually.

API surface
-----------

Three read functions (``get_status`` / ``read_anchor`` /
``list_anchors``) and three write functions (``init_db`` /
``write_anchor`` / ``delete_anchor``) — all pure file-I/O over a
``cache_dir: Path`` argument. The skill in 10.5 uses all six; 10.6's
REST routes use the read functions only.

Marshalling lives here too so 10.6 doesn't have to re-implement the
``dataclasses.asdict + json.dumps`` round-trip for the
:class:`BehaviorAnchor` union variants — :func:`anchor_to_json` /
:func:`anchor_from_json` are the canonical encoder/decoder.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from androscan.analysis.trace_types import (
    BehaviorAnchor,
    Branch,
    BranchOutcome,
    BranchVerdict,
    BypassPlan,
    CallSite,
    CompositeOrigin,
    ConstOrigin,
    DecisionKind,
    DecisionPoint,
    FieldReadOrigin,
    FieldRef,
    MethodCallOrigin,
    MethodRef,
    ParamOrigin,
    PredicateOrigin,
)


logger = logging.getLogger(__name__)


SCHEMA_VERSION = "2"
# Schema version history:
#   "1" — Phase 10 v1 (DEC-024). Intra-procedural slicer; PredicateOrigin
#         variants ``MethodCallOrigin`` / ``FieldReadOrigin`` had no
#         ``descent_depth`` field on the wire.
#   "2" — Phase 11 v2 sub-step 11.6 (DEC-025). v2 inter-procedural
#         slicer landed in 11.4 (method descent) + 11.5 (field-write
#         walking); ``MethodCallOrigin.descent_depth`` and
#         ``FieldReadOrigin.descent_depth`` (default ``0``) added to
#         the wire shape. v1-cached anchors silently re-build on
#         first 11.x open via the existing route layer's
#         "missing → build" fallback path; ``get_status()`` returns
#         ``status="failed"`` with ``error="schema_version mismatch"``
#         on a v1 read so the route can drop + re-run the trace.
INDEX_FILENAME = "trace.sqlite"


# ---------------------------------------------------------------------------
# Status envelope


@dataclass(frozen=True)
class TraceCacheStatus:
    """Status envelope parallel to
    :class:`androscan.analysis.call_graph.IndexStatus`. The Settings
    tab can render both with the same React component once 10.6 wires
    the route up.
    """
    status: str  # "missing" | "ready" | "failed"
    db_path: Optional[str] = None
    schema_version: Optional[str] = None
    anchor_count: Optional[int] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Path + connection helpers


def trace_cache_db_path(decompile_cache_dir: Path) -> Path:
    """Resolve the SQLite path inside an existing decompile-cache directory."""
    return Path(decompile_cache_dir) / INDEX_FILENAME


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open the cache with the same row-factory + foreign-key posture
    the rest of the project uses for SQLite stores."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(decompile_cache_dir: Path) -> Path:
    """Create the schema if missing; idempotent (existing DBs are
    left untouched). Returns the DB path so callers can chain
    ``cache = init_db(cache_dir); ...``."""
    db = trace_cache_db_path(decompile_cache_dir)
    db.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS anchors (
                entry_smali_id TEXT NOT NULL,
                hops           INTEGER NOT NULL,
                payload_json   TEXT NOT NULL,
                created_at     REAL NOT NULL,
                PRIMARY KEY (entry_smali_id, hops)
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
        conn.commit()
    return db


# ---------------------------------------------------------------------------
# Read API


def get_status(decompile_cache_dir: Path) -> TraceCacheStatus:
    """Schema + sanity check. Mirrors :func:`call_graph.get_status` —
    a schema-version mismatch surfaces as ``status="failed"`` so 10.6
    can offer a one-click reset without prompting the operator to
    delete files manually."""
    db = trace_cache_db_path(decompile_cache_dir)
    if not db.is_file():
        return TraceCacheStatus(status="missing")
    try:
        with _connect(db) as conn:
            sv_row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", ("schema_version",)
            ).fetchone()
            sv = sv_row["value"] if sv_row else None
            if sv != SCHEMA_VERSION:
                return TraceCacheStatus(
                    status="failed",
                    db_path=str(db),
                    schema_version=sv,
                    error=f"schema_version mismatch (have {sv!r}, want {SCHEMA_VERSION!r})",
                )
            count_row = conn.execute("SELECT COUNT(*) AS n FROM anchors").fetchone()
            return TraceCacheStatus(
                status="ready",
                db_path=str(db),
                schema_version=sv,
                anchor_count=int(count_row["n"]) if count_row else 0,
            )
    except sqlite3.DatabaseError as exc:  # pragma: no cover - defensive
        return TraceCacheStatus(
            status="failed",
            db_path=str(db),
            error=f"sqlite error: {exc}",
        )


def read_anchor(
    decompile_cache_dir: Path,
    entry_smali_id: str,
    hops: int,
) -> Optional[BehaviorAnchor]:
    """Return the cached :class:`BehaviorAnchor` for ``(entry, hops)``
    or ``None`` if absent / unreadable. Quietly fail-soft on JSON
    parse errors (delete-and-rewrite is the operator's repair path)."""
    db = trace_cache_db_path(decompile_cache_dir)
    if not db.is_file():
        return None
    try:
        with _connect(db) as conn:
            row = conn.execute(
                "SELECT payload_json FROM anchors WHERE entry_smali_id = ? AND hops = ?",
                (entry_smali_id, int(hops)),
            ).fetchone()
            if row is None:
                return None
            return anchor_from_json(row["payload_json"])
    except (sqlite3.DatabaseError, ValueError, KeyError) as exc:
        logger.warning(
            "trace_cache: failed to read anchor (entry=%s, hops=%s): %s",
            entry_smali_id, hops, exc,
        )
        return None


def list_anchors(decompile_cache_dir: Path) -> list[dict[str, Any]]:
    """Return ``[{entry_smali_id, hops, created_at}, ...]`` for every
    cached anchor. Used by 10.6's ``GET /api/trace/<app>/anchors``
    route + the Settings status card."""
    db = trace_cache_db_path(decompile_cache_dir)
    if not db.is_file():
        return []
    try:
        with _connect(db) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT entry_smali_id, hops, created_at FROM anchors "
                    "ORDER BY created_at DESC, entry_smali_id, hops"
                )
            ]
    except sqlite3.DatabaseError as exc:  # pragma: no cover - defensive
        logger.warning("trace_cache: failed to list anchors: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Write API


def write_anchor(decompile_cache_dir: Path, anchor: BehaviorAnchor) -> None:
    """Upsert one anchor keyed by ``(entry_method.smali_signature, hops)``.

    Initialises the DB on first write so callers don't need to call
    :func:`init_db` separately (matches the lazy-init pattern used by
    ``skill_results_cache``).
    """
    db = init_db(decompile_cache_dir)
    payload = anchor_to_json(anchor)
    with _connect(db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO anchors "
            "(entry_smali_id, hops, payload_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (anchor.entry_method.smali_signature, anchor.hops, payload, time.time()),
        )
        conn.commit()


def delete_anchor(
    decompile_cache_dir: Path,
    entry_smali_id: str,
    hops: int,
) -> bool:
    """Delete one cached anchor; returns True if a row was removed."""
    db = trace_cache_db_path(decompile_cache_dir)
    if not db.is_file():
        return False
    with _connect(db) as conn:
        cur = conn.execute(
            "DELETE FROM anchors WHERE entry_smali_id = ? AND hops = ?",
            (entry_smali_id, int(hops)),
        )
        conn.commit()
        return cur.rowcount > 0


def clear_all_anchors(decompile_cache_dir: Path) -> int:
    """Wipe every anchor; returns the number of rows deleted. Used by
    the Settings tab's "Reset trace cache" button (10.6) — the table
    + meta row stay intact so the next write doesn't have to re-init."""
    db = trace_cache_db_path(decompile_cache_dir)
    if not db.is_file():
        return 0
    with _connect(db) as conn:
        cur = conn.execute("DELETE FROM anchors")
        conn.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# JSON marshalling — the canonical encoder/decoder for BehaviorAnchor.
#
# Frozen dataclasses + tuples + ``Optional[Union[...]]`` predicate-origin
# make ``json.dumps(dataclasses.asdict(anchor))`` "almost" work — except
# the ``PredicateOrigin`` discriminator (the ``kind`` field on each
# *Origin variant) needs to be honoured on the decode side. The
# encoder is trivial (asdict already emits ``kind``); the decoder
# dispatches by ``kind`` to the right dataclass constructor.


_PREDICATE_ORIGIN_BY_KIND: dict[str, type] = {
    "method_call":  MethodCallOrigin,
    "field_read":   FieldReadOrigin,
    "const":        ConstOrigin,
    "param":        ParamOrigin,
    "composite":    CompositeOrigin,
}


def anchor_to_json(anchor: BehaviorAnchor) -> str:
    """Encode a :class:`BehaviorAnchor` to a deterministic JSON string.

    ``sort_keys=True`` makes the output stable for diffing — useful
    when 10.6's REST returns a payload byte-equal across requests for
    cache validation by the frontend (ETag-style behaviour comes in v2).
    """
    return json.dumps(dataclasses.asdict(anchor), sort_keys=True)


def anchor_from_json(payload_json: str) -> BehaviorAnchor:
    """Decode a JSON-encoded :class:`BehaviorAnchor`. Raises
    :class:`ValueError` (wrapped from any underlying decode error) if
    the payload is malformed — the cache layer catches and treats as
    "row missing"."""
    try:
        raw = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"trace_cache: anchor payload is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("trace_cache: anchor payload must be a JSON object")
    return _decode_anchor(raw)


def _decode_method_ref(raw: dict[str, Any]) -> MethodRef:
    return MethodRef(
        class_name=str(raw["class_name"]),
        method_name=str(raw["method_name"]),
        param_descriptors=tuple(raw.get("param_descriptors") or ()),
        return_descriptor=str(raw["return_descriptor"]),
    )


def _decode_field_ref(raw: dict[str, Any]) -> FieldRef:
    return FieldRef(
        class_name=str(raw["class_name"]),
        field_name=str(raw["field_name"]),
        type_descriptor=str(raw["type_descriptor"]),
    )


def _decode_predicate_origin(raw: Optional[dict[str, Any]]) -> Optional[PredicateOrigin]:
    if raw is None:
        return None
    kind = raw.get("kind")
    cls = _PREDICATE_ORIGIN_BY_KIND.get(kind)
    if cls is None:
        # Unknown discriminator — defensive; shouldn't happen on
        # round-tripped payloads. Treat as "no origin known" so the
        # rest of the anchor is still usable.
        logger.warning("trace_cache: unknown PredicateOrigin kind %r", kind)
        return None
    if cls is MethodCallOrigin:
        return MethodCallOrigin(
            method=_decode_method_ref(raw["method"]),
            invoke_kind=str(raw["invoke_kind"]),
        )
    if cls is FieldReadOrigin:
        return FieldReadOrigin(
            field=_decode_field_ref(raw["field"]),
            is_static=bool(raw["is_static"]),
        )
    if cls is ConstOrigin:
        return ConstOrigin(
            value=str(raw["value"]),
            smali_op=str(raw["smali_op"]),
        )
    if cls is ParamOrigin:
        return ParamOrigin(register=str(raw["register"]))
    # composite
    return CompositeOrigin(reason=str(raw["reason"]))


def _decode_branch(raw: dict[str, Any]) -> Branch:
    target = raw.get("target_label")
    return Branch(
        label=str(raw["label"]),
        target_label=None if target is None else str(target),
    )


def _decode_branch_verdict(raw: dict[str, Any]) -> BranchVerdict:
    return BranchVerdict(
        branch_label=str(raw["branch_label"]),
        verdict=str(raw["verdict"]),
        score=float(raw["score"]),
        reasons=tuple(raw.get("reasons") or ()),
    )


def _decode_branch_outcome(raw: Optional[dict[str, Any]]) -> Optional[BranchOutcome]:
    if raw is None:
        return None
    return BranchOutcome(
        verdicts=tuple(_decode_branch_verdict(v) for v in raw.get("verdicts") or ()),
        confidence=float(raw["confidence"]),
        reasons=tuple(raw.get("reasons") or ()),
    )


def _decode_decision(raw: dict[str, Any]) -> DecisionPoint:
    src_line = raw.get("source_line")
    return DecisionPoint(
        method=_decode_method_ref(raw["method"]),
        instruction_index=int(raw["instruction_index"]),
        source_line=None if src_line is None else int(src_line),
        kind=DecisionKind(raw["kind"]),
        predicate_registers=tuple(raw.get("predicate_registers") or ()),
        branches=tuple(_decode_branch(b) for b in raw.get("branches") or ()),
        predicate_origin=_decode_predicate_origin(raw.get("predicate_origin")),
        branch_outcome=_decode_branch_outcome(raw.get("branch_outcome")),
    )


def _decode_plan(raw: dict[str, Any]) -> BypassPlan:
    target = raw.get("target_method")
    src_method = raw.get("source_decision_method")
    return BypassPlan(
        template_id=str(raw["template_id"]),
        params=dict(raw.get("params") or {}),
        rationale=str(raw.get("rationale") or ""),
        risk=str(raw["risk"]),
        risks=tuple(raw.get("risks") or ()),
        target_method=_decode_method_ref(target) if target else None,
        source_decision_method=_decode_method_ref(src_method) if src_method else None,
        source_decision_instruction_index=(
            int(raw["source_decision_instruction_index"])
            if raw.get("source_decision_instruction_index") is not None
            else None
        ),
    )


def _decode_call_site(raw: dict[str, Any]) -> CallSite:
    """Decode one :class:`CallSite` from its JSON shape.

    Phase 13 v3.X-next.1 / DEC-031 — additive at schema v2. Anchors
    persisted before v3.X-next.1 have no ``method_invocations`` key in
    their JSON; :func:`_decode_anchor` short-circuits in that case and
    this helper isn't called. New (v3.X-next.1+) anchors round-trip
    faithfully: every CallSite tuple in ``method_invocations`` is
    reconstructed with its caller / callee :class:`MethodRef`s + the
    dominator bookkeeping intact.
    """
    in_branch = raw.get("in_branch_of")
    branch_label = raw.get("branch_label")
    return CallSite(
        caller=_decode_method_ref(raw["caller"]),
        instruction_index=int(raw["instruction_index"]),
        callee=_decode_method_ref(raw["callee"]),
        in_branch_of=None if in_branch is None else int(in_branch),
        branch_label=None if branch_label is None else str(branch_label),
    )


def _decode_method_invocations(
    raw: Optional[dict[str, Any]],
) -> dict[str, tuple[CallSite, ...]]:
    """Decode the ``method_invocations`` payload back into a
    ``dict[str, tuple[CallSite, ...]]``.

    Tolerant by design — old cached anchors (pre-v3.X-next.1) have no
    ``method_invocations`` key, so ``raw`` is ``None`` and we return an
    empty dict. Matches the additive-field contract on
    :class:`BehaviorAnchor`.
    """
    if not raw:
        return {}
    out: dict[str, tuple[CallSite, ...]] = {}
    for key, sites in raw.items():
        if not sites:
            continue
        out[str(key)] = tuple(_decode_call_site(cs) for cs in sites)
    return out


def _decode_anchor(raw: dict[str, Any]) -> BehaviorAnchor:
    return BehaviorAnchor(
        entry_method=_decode_method_ref(raw["entry_method"]),
        hops=int(raw["hops"]),
        truncated=bool(raw.get("truncated", False)),
        incomplete=bool(raw.get("incomplete", False)),
        decisions=tuple(_decode_decision(d) for d in raw.get("decisions") or ()),
        plans=tuple(_decode_plan(p) for p in raw.get("plans") or ()),
        advanced_plans=tuple(_decode_plan(p) for p in raw.get("advanced_plans") or ()),
        rationale=str(raw.get("rationale") or ""),
        low_confidence_decision_indices=tuple(
            int(i) for i in (raw.get("low_confidence_decision_indices") or ())
        ),
        method_invocations=_decode_method_invocations(raw.get("method_invocations")),
    )
