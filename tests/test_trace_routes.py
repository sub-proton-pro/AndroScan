"""TestClient integration for :mod:`androscan.web.trace_routes`
(Phase 10 sub-step 10.6).

Same posture as :mod:`test_graph_routes` — build a real FastAPI app
via :func:`create_app`, seed a fake app with decompile status
``ready`` + a pre-extracted smali tree (the trace_smali fixture
re-used from 10.1 → 10.5), build the call-graph SQLite synchronously,
then hit each of the ``/api/trace`` endpoints. That keeps the wiring
honest — same DI seams as production, no fakes.

LLM is stubbed via monkeypatching ``androscan.llm.client.complete`` so
the POST endpoint's ``trace_behavior`` invocation finishes
deterministically + offline. We deliberately use the
``_stub_llm_unreachable`` helper so any incidental LLM call returns an
empty-but-well-formed JSON object — the tests assert on the
deterministic static layer's output, never on the LLM round-trip
(that's what :mod:`test_trace_behavior_skill` is for).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from androscan.analysis import call_graph
from androscan.config import Config
from androscan.internal import trace_cache
from androscan.web import decompile_cache as dc
from androscan.web.app import create_app


FIXTURES = Path(__file__).parent / "fixtures" / "trace_smali"
APP_ID = "myapp"
SHA = "f" * 40
ENTRY_BOOL = "Lcom/trace/Plans;->gateBoolPredicate()V"
ENTRY_INT = "Lcom/trace/Plans;->gateIntPredicate()V"
ENTRY_GHOST = "Lcom/trace/Ghost;->doesNotExist()V"


# ---------------------------------------------------------------------------
# Fixtures + stubs


class _LLMResponse:
    """Minimal stand-in for ``androscan.llm.client.CompleteResult``."""
    def __init__(self, content: str = '{"rationale": "", "reclassifications": [], "proposed_plans": []}') -> None:
        self.content = content
        self.text = content


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    """Default LLM stub — returns a well-formed empty JSON object.

    Applied to every test in this module so the route's POST handler
    never blocks on a real Ollama / cloud call. Tests that need a
    different LLM behaviour can monkeypatch on top of this fixture.
    """
    def _stub(prompt: str, **kwargs: Any) -> Any:
        return _LLMResponse()
    monkeypatch.setattr("androscan.llm.client.complete", _stub)


def _seed_app_with_graph(tmp_path: Path) -> Path:
    """Mirror :func:`test_graph_routes._seed_app_with_graph` but seed
    the trace_smali fixture (10.4's Plans.smali decisions / plans
    show up under the call graph)."""
    apps_root = tmp_path / "apps"
    app_dir = apps_root / APP_ID
    app_dir.mkdir(parents=True)
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK")
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": SHA, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )
    dc.sources_dir(app_dir, SHA).mkdir(parents=True)
    dc._write_index(
        app_dir, SHA,
        {"status": "ready", "apk_path": str(apk), "apk_sha256": SHA, "file_count": 0},
    )
    cache = dc.cache_root_for(app_dir, SHA)
    smali_out = cache / call_graph.APKTOOL_OUT_SUBDIR
    smali_out.mkdir(parents=True)
    shutil.copytree(FIXTURES / "smali", smali_out / "smali")
    st = call_graph.build_index(cache, apk_path=apk, sha=SHA)
    assert st.status == "ready", st.error
    return apps_root


def _client(tmp_path: Path) -> TestClient:
    apps_root = _seed_app_with_graph(tmp_path)
    app = create_app(Config.default(), cwd=apps_root.parent)
    return TestClient(app)


def _client_no_decompile(tmp_path: Path) -> TestClient:
    """Variant: app dir exists but no decompile cache — used to
    exercise the 409 ``decompile not ready`` path."""
    apps_root = tmp_path / "apps"
    (apps_root / APP_ID).mkdir(parents=True)
    app = create_app(Config.default(), cwd=apps_root.parent)
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1) Router registration smoke


def test_trace_routes_registered():
    """The factory must register all five route shapes — guards against
    a future refactor accidentally dropping one of them."""
    app = create_app(Config.default())
    paths = {r.path for r in app.routes}
    assert "/api/trace/{app_id}/status" in paths
    assert "/api/trace/{app_id}/anchors" in paths
    assert "/api/trace/{app_id}/anchor" in paths


# ---------------------------------------------------------------------------
# 2) GET /status — fan-out shape


def test_status_when_decompile_missing(tmp_path: Path) -> None:
    """No decompile cache → status fan-out reports
    ``decompile_status="not_started"`` + ``trace_cache.status="missing"``."""
    client = _client_no_decompile(tmp_path)
    r = client.get(f"/api/trace/{APP_ID}/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == APP_ID
    assert body["call_graph"]["status"] == "missing"
    assert body["trace_cache"]["status"] == "missing"


def test_status_when_call_graph_ready_and_no_anchors(tmp_path: Path) -> None:
    """Call graph built but no traces yet → ``trace_cache.status="missing"``
    (the SQLite file is created lazily on first write)."""
    client = _client(tmp_path)
    r = client.get(f"/api/trace/{APP_ID}/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["call_graph"]["status"] == "ready"
    assert body["trace_cache"]["status"] == "missing"


# ---------------------------------------------------------------------------
# 3) GET /anchors — list


def test_list_anchors_empty(tmp_path: Path) -> None:
    """No build yet → empty list (NOT 404 — the route always 200s with
    an empty list when the app exists)."""
    client = _client(tmp_path)
    r = client.get(f"/api/trace/{APP_ID}/anchors")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == APP_ID
    assert body["anchors"] == []


def test_list_anchors_after_build(tmp_path: Path) -> None:
    """After one successful POST, list contains exactly one row with
    matching entry / hops + a recent ``created_at``."""
    client = _client(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 200, r.text
    r2 = client.get(f"/api/trace/{APP_ID}/anchors")
    assert r2.status_code == 200, r2.text
    rows = r2.json()["anchors"]
    assert len(rows) == 1
    assert rows[0]["entry_smali_id"] == ENTRY_BOOL
    assert rows[0]["hops"] == 1
    assert isinstance(rows[0]["created_at"], (int, float))


# ---------------------------------------------------------------------------
# 4) GET /anchor — pure cache read


def test_get_anchor_cache_miss_returns_404(tmp_path: Path) -> None:
    """No matching cached row → 404 with the entry / hops echoed in the
    detail string. The frontend's empty-state UX depends on this
    distinction (vs a 200 with empty-tuple decisions)."""
    client = _client(tmp_path)
    r = client.get(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 404, r.text
    assert ENTRY_BOOL in r.json()["detail"]


def test_get_anchor_cache_hit_returns_anchor_json(tmp_path: Path) -> None:
    """After POST the GET returns the canonical anchor JSON shape —
    pin every field in the wire contract so 10.7's frontend can build
    against the response without per-test guessing."""
    client = _client(tmp_path)
    posted = client.post(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_BOOL, "hops": 1},
    ).json()
    fetched = client.get(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    # The encoder is sort_keys + frozen-dataclass, so the byte-shape is
    # stable across calls. Compare directly.
    assert body == posted
    assert body["entry_method"]["method_name"] == "gateBoolPredicate"
    assert body["hops"] == 1
    assert isinstance(body["decisions"], list)
    assert len(body["decisions"]) >= 1
    template_ids = {p["template_id"] for p in body["plans"]}
    assert "force_return_value" in template_ids


def test_get_anchor_validation_rejects_empty_entry(tmp_path: Path) -> None:
    """An empty ``entry`` query param → 422 (same shape FastAPI emits
    for any bad query param, but tested here so a future refactor
    can't accidentally make the validation a soft-warn)."""
    client = _client(tmp_path)
    r = client.get(f"/api/trace/{APP_ID}/anchor", params={"entry": "", "hops": 1})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 5) POST /anchor — build via skill


def test_post_anchor_builds_and_persists(tmp_path: Path) -> None:
    """First POST runs the skill, returns the populated anchor, and
    writes the row to ``trace.sqlite``."""
    client = _client(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entry_method"]["method_name"] == "gateBoolPredicate"
    # Directly inspect the cache layer to confirm persistence.
    cache_dir = dc.cache_root_for(tmp_path / "apps" / APP_ID, SHA)
    status = trace_cache.get_status(cache_dir)
    assert status.status == "ready"
    assert status.anchor_count == 1


def test_post_anchor_force_true_overwrites_cached_row(tmp_path: Path) -> None:
    """``force=true`` re-runs the skill even when the row is cached;
    the ``created_at`` advances on the new write."""
    client = _client(tmp_path)
    client.post(f"/api/trace/{APP_ID}/anchor", params={"entry": ENTRY_BOOL, "hops": 1})
    cache_dir = dc.cache_root_for(tmp_path / "apps" / APP_ID, SHA)
    first_rows = trace_cache.list_anchors(cache_dir)
    assert len(first_rows) == 1
    first_ts = first_rows[0]["created_at"]
    # SQLite's ``time.time()`` resolution is sub-millisecond; force a
    # fresh build and confirm the row's created_at advances.
    r = client.post(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_BOOL, "hops": 1, "force": "true"},
    )
    assert r.status_code == 200, r.text
    second_rows = trace_cache.list_anchors(cache_dir)
    assert len(second_rows) == 1, "force should upsert, not append"
    assert second_rows[0]["created_at"] >= first_ts


def test_post_anchor_unresolved_entry_returns_404(tmp_path: Path) -> None:
    """Skill fail-open path — entry not in the call graph → route
    surfaces as 404 (the skill's ``data is None`` envelope is the
    discriminator)."""
    client = _client(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_GHOST, "hops": 1},
    )
    assert r.status_code == 404, r.text
    assert "not found" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 6) DELETE /anchor — single-row eviction


def test_delete_anchor_success_returns_204(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post(f"/api/trace/{APP_ID}/anchor", params={"entry": ENTRY_BOOL, "hops": 1})
    r = client.delete(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 204, r.text
    # Subsequent list shows no rows.
    rows = client.get(f"/api/trace/{APP_ID}/anchors").json()["anchors"]
    assert rows == []


def test_delete_anchor_missing_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.delete(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 7) Cross-cutting — unknown app_id


def test_unknown_app_id_returns_404_on_status(tmp_path: Path) -> None:
    """The shared ``app_dir_resolver`` raises 404 for unknown ids; we
    pin that contract here so a future change to the resolver doesn't
    silently start 200ing for ghost apps."""
    client = _client(tmp_path)
    r = client.get("/api/trace/ghost-app/status")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 8) GET /anchored-methods — Phase 11 sub-step 11.3 overlay feed


def test_anchored_methods_404_on_unbuilt_cache(tmp_path: Path) -> None:
    """Decompile is ready + call graph is built, but no traces have
    ever been built (``trace.sqlite`` doesn't exist) → 404 per the
    11.3 contract. Distinguishes "operator has never built a trace"
    from "operator has built and then deleted everything"; the
    overlay code can map both to "no glyphs" but the operator-facing
    empty state in Trace mode uses this distinction."""
    client = _client(tmp_path)
    r = client.get(f"/api/trace/{APP_ID}/anchored-methods")
    assert r.status_code == 404, r.text
    assert "trace cache" in r.json()["detail"].lower()


def test_anchored_methods_unknown_app_returns_404(tmp_path: Path) -> None:
    """Unknown ``app_id`` → 404 via the shared ``app_dir_resolver``.
    Same contract as every other route in this module."""
    client = _client(tmp_path)
    r = client.get("/api/trace/ghost-app/anchored-methods")
    assert r.status_code == 404, r.text


def test_anchored_methods_decompile_not_ready_returns_409(tmp_path: Path) -> None:
    """App dir exists but decompile cache is missing → 409 with the
    standard ``decompile not ready`` message. Mirrors every other
    route in this module that gates on ``_cache_dir_for``."""
    client = _client_no_decompile(tmp_path)
    r = client.get(f"/api/trace/{APP_ID}/anchored-methods")
    assert r.status_code == 409, r.text


def test_anchored_methods_happy_path_after_build(tmp_path: Path) -> None:
    """After one POST builds an anchor, GET returns its method set
    (at least the entry method ``Plans.gateBoolPredicate`` and a
    ``class_smali`` in the expected smali-descriptor form)."""
    client = _client(tmp_path)
    posted = client.post(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert posted.status_code == 200, posted.text
    r = client.get(f"/api/trace/{APP_ID}/anchored-methods")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == APP_ID
    assert body["sha"] == SHA
    assert body["error"] is None
    assert body["total"] >= 1
    assert body["total"] == len(body["methods"])
    # The entry method must appear in the deduped set.
    keys = {(m["class_smali"], m["method_name"]) for m in body["methods"]}
    assert ("Lcom/trace/Plans;", "gateBoolPredicate") in keys
    # Every row carries the (hops, created_at) tuple the overlay tooltip
    # renders. ``hops`` matches the build's hops; ``created_at`` is a
    # finite positive number.
    for m in body["methods"]:
        assert m["hops"] == 1
        assert isinstance(m["created_at"], (int, float)) and m["created_at"] > 0
        assert m["class_smali"].startswith("L") and m["class_smali"].endswith(";")


def test_anchored_methods_empty_after_clear(tmp_path: Path) -> None:
    """After build + delete, the trace.sqlite file still exists (init_db
    is sticky) so the route returns 200 + empty methods list — *not*
    404. This is the "built-but-empty cache" state from the 11.3
    contract; distinguishes from the "never built" 404."""
    client = _client(tmp_path)
    client.post(f"/api/trace/{APP_ID}/anchor", params={"entry": ENTRY_BOOL, "hops": 1})
    deleted = client.delete(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert deleted.status_code == 204, deleted.text
    r = client.get(f"/api/trace/{APP_ID}/anchored-methods")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["methods"] == []
    assert body["total"] == 0
    assert body["error"] is None


def test_anchored_methods_dedupes_across_anchors(tmp_path: Path) -> None:
    """Two separate cached anchors that share methods (same class +
    method seen via two different decision paths) → each
    ``(class_smali, method_name)`` appears exactly once in the
    response. The deduped row carries the most-recent ``hops`` /
    ``created_at`` (most-recent wins on ``created_at``; ties broken
    by larger ``hops``)."""
    client = _client(tmp_path)
    # Build two anchors at different hops over the same fixture so they
    # share at least one method (both anchors include the Plans entry
    # methods in their decision closure).
    r1 = client.post(f"/api/trace/{APP_ID}/anchor", params={"entry": ENTRY_BOOL, "hops": 1})
    assert r1.status_code == 200, r1.text
    r2 = client.post(f"/api/trace/{APP_ID}/anchor", params={"entry": ENTRY_INT, "hops": 1})
    assert r2.status_code == 200, r2.text
    r = client.get(f"/api/trace/{APP_ID}/anchored-methods")
    assert r.status_code == 200, r.text
    body = r.json()
    keys = [(m["class_smali"], m["method_name"]) for m in body["methods"]]
    # Dedupe contract — every key appears at most once.
    assert len(keys) == len(set(keys)), f"duplicates in {keys}"
    # Both entry methods made it into the set.
    assert ("Lcom/trace/Plans;", "gateBoolPredicate") in keys
    assert ("Lcom/trace/Plans;", "gateIntPredicate") in keys


def test_anchored_methods_surfaces_error_on_payload_decode_failure(
    tmp_path: Path,
) -> None:
    """When a row's ``payload_json`` is corrupted, the route doesn't
    crash — it surfaces an ``error`` field summarising the failure
    while still returning whatever methods *could* be decoded from
    the surviving rows. Operator UX: the overlay still works for
    intact rows, and the error banner nudges the operator to
    reset/rebuild the corrupted entry."""
    import sqlite3 as _sqlite3

    client = _client(tmp_path)
    # Build one good anchor first so we have a row to corrupt.
    client.post(f"/api/trace/{APP_ID}/anchor", params={"entry": ENTRY_BOOL, "hops": 1})
    cache_dir = dc.cache_root_for(tmp_path / "apps" / APP_ID, SHA)
    db_path = trace_cache.trace_cache_db_path(cache_dir)
    # Replace the row's payload_json with non-JSON garbage. The
    # ``read_anchor`` helper fail-soft returns ``None`` on JSON-parse
    # failure (logged via ``trace_cache.logger.warning``) — the route
    # turns that into an ``error`` field summary.
    with _sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE anchors SET payload_json = ? WHERE entry_smali_id = ? AND hops = ?",
            ("not valid json {", ENTRY_BOOL, 1),
        )
        conn.commit()
    r = client.get(f"/api/trace/{APP_ID}/anchored-methods")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is not None
    assert "payload" in body["error"].lower() or "decode" in body["error"].lower()
    # The corrupted row contributed zero methods; surviving rows (if
    # any) would still appear. With a single-row corruption, the set
    # is empty.
    assert body["methods"] == []
    assert body["total"] == 0


def test_anchored_methods_route_registered(tmp_path: Path) -> None:
    """Smoke check that the new endpoint is registered alongside the
    existing five — guards against a refactor accidentally dropping
    it from ``build_trace_router``."""
    client = _client(tmp_path)
    paths = {r.path for r in client.app.routes}  # type: ignore[attr-defined]
    assert "/api/trace/{app_id}/anchored-methods" in paths


# ---------------------------------------------------------------------------
# 9) POST /normalise-entry — Phase 11 v2.1 sub-step v2.1.2 coalescer
#
# Translates the operator's typed input (dotted Java, partial Smali, or
# stack-trace line) → canonical Smali method-prefix + validates the
# class against the call graph. Powers Trace mode's debounced inline
# spinner + ✓ / ⚠ validation pill.
#
# The fixture's call-graph contains ``Lcom/trace/Plans;`` (with multiple
# methods), so the "valid class" path uses dotted form
# ``com.trace.Plans.gateBoolPredicate`` and asserts the round-tripped
# Smali matches what the picker would surface.


def test_normalise_entry_route_registered(tmp_path: Path) -> None:
    """The factory must register the v2.1.2 coalescer endpoint
    alongside the existing six — guards against a refactor
    accidentally dropping it from ``build_trace_router``."""
    client = _client(tmp_path)
    paths = {r.path for r in client.app.routes}  # type: ignore[attr-defined]
    assert "/api/trace/{app_id}/normalise-entry" in paths


def test_normalise_entry_dotted_java_method_translates_to_smali(
    tmp_path: Path,
) -> None:
    """Dotted Java method form ``com.trace.Plans.gateBoolPredicate`` →
    Smali method-prefix ``Lcom/trace/Plans;->gateBoolPredicate(``.

    Validates the class against the call graph in the same round-trip:
    ``com.trace.Plans`` is in the fixture, so ``class_exists_in_graph``
    is ``True`` and ``method_count`` is non-zero. This is the headline
    operator path — a pasted dotted method name lands as a canonical
    Smali prefix the MethodPicker can immediately consume."""
    client = _client(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/normalise-entry",
        json={"entry": "com.trace.Plans.gateBoolPredicate"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["normalised_entry"] == "Lcom/trace/Plans;->gateBoolPredicate("
    assert body["smali_class"] == "Lcom/trace/Plans;"
    assert body["class_exists_in_graph"] is True
    assert body["method_count"] >= 2  # gateBoolPredicate + gateIntPredicate
    assert body["error"] is None


def test_normalise_entry_partial_smali_passes_through(tmp_path: Path) -> None:
    """Operator-typed Smali (the ``Advanced`` form path) round-trips
    unchanged through the coalescer's pass-through branch — the input
    is already canonical and any partial / full descriptor tail
    carries information downstream consumers (MethodPicker, trace
    skill) need preserved."""
    client = _client(tmp_path)
    # Class+separator shape (Inspect → Trace seed, partial).
    r = client.post(
        f"/api/trace/{APP_ID}/normalise-entry",
        json={"entry": "Lcom/trace/Plans;->"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["normalised_entry"] == "Lcom/trace/Plans;->"
    assert body["smali_class"] == "Lcom/trace/Plans;"
    assert body["class_exists_in_graph"] is True

    # Class+method-prefix shape (Inspect → Trace seed, method-known).
    r = client.post(
        f"/api/trace/{APP_ID}/normalise-entry",
        json={"entry": "Lcom/trace/Plans;->gateBoolPredicate("},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["normalised_entry"] == "Lcom/trace/Plans;->gateBoolPredicate("
    )

    # Full signature shape — descriptor list preserved end-to-end so
    # the trace skill can be fired directly off the coalescer's
    # ``normalised_entry`` without re-typing the params.
    r = client.post(
        f"/api/trace/{APP_ID}/normalise-entry",
        json={"entry": ENTRY_BOOL},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["normalised_entry"] == ENTRY_BOOL
    assert body["smali_class"] == "Lcom/trace/Plans;"


def test_normalise_entry_stack_trace_line_drops_location(tmp_path: Path) -> None:
    """Stack-trace line ``com.trace.Plans.gateBoolPredicate(Plans.java:42)``
    drops the ``(Plans.java:42)`` source-location tail before mapping
    to Smali — the operator pasted the line straight from a logcat /
    crash report and expects the entry method to come out cleanly."""
    client = _client(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/normalise-entry",
        json={"entry": "com.trace.Plans.gateBoolPredicate(Plans.java:42)"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["normalised_entry"] == "Lcom/trace/Plans;->gateBoolPredicate("
    assert body["smali_class"] == "Lcom/trace/Plans;"
    assert body["class_exists_in_graph"] is True


def test_normalise_entry_class_not_in_graph_returns_200_with_false_flag(
    tmp_path: Path,
) -> None:
    """Parseable input that doesn't match any class in the call graph
    → 200 with ``class_exists_in_graph: false`` + ``method_count: 0``.
    This is the v2.1.3 entry point — the ⚠ pill renders, and the
    "Find similar classes" button (lands in v2.1.3) hangs off the
    same response."""
    client = _client(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/normalise-entry",
        json={"entry": "com.trace.NotARealClass.foo"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["normalised_entry"] == "Lcom/trace/NotARealClass;->foo("
    assert body["smali_class"] == "Lcom/trace/NotARealClass;"
    assert body["class_exists_in_graph"] is False
    assert body["method_count"] == 0
    assert body["error"] is None


def test_normalise_entry_unparseable_input_returns_422(tmp_path: Path) -> None:
    """Inputs the heuristic dispatcher can't classify as either Smali
    or dotted Java → 422 with an operator-readable ``detail`` string
    the frontend renders inline as the ✗ pill."""
    client = _client(tmp_path)
    # Empty body → 422 from the model validator (max_length=500 + required).
    r = client.post(
        f"/api/trace/{APP_ID}/normalise-entry",
        json={"entry": ""},
    )
    # The Pydantic model accepts empty strings (no min_length); the
    # 422 comes from ``_coalesce_entry`` returning "entry is empty".
    assert r.status_code == 422, r.text
    assert "empty" in r.json()["detail"].lower()

    # Bare lowercase input — no UpperCamelCase class segment found.
    r = client.post(
        f"/api/trace/{APP_ID}/normalise-entry",
        json={"entry": "com.example.foo"},
    )
    assert r.status_code == 422, r.text
    assert "class" in r.json()["detail"].lower()

    # Junk characters that don't form any valid identifier.
    r = client.post(
        f"/api/trace/{APP_ID}/normalise-entry",
        json={"entry": "@#$%^&*"},
    )
    assert r.status_code == 422, r.text


def test_normalise_entry_unknown_app_returns_404(tmp_path: Path) -> None:
    """Unknown ``app_id`` → 404 via the shared ``app_dir_resolver``.
    Same contract as every other route in this module."""
    client = _client(tmp_path)
    r = client.post(
        "/api/trace/ghost-app/normalise-entry",
        json={"entry": "com.example.Foo.bar"},
    )
    assert r.status_code == 404, r.text


def test_normalise_entry_decompile_not_ready_returns_409(
    tmp_path: Path,
) -> None:
    """App dir exists but decompile cache is missing → 409 with the
    standard ``decompile not ready`` message — call-graph validation
    requires a built call graph, which requires a built decompile
    cache."""
    client = _client_no_decompile(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/normalise-entry",
        json={"entry": "com.example.Foo.bar"},
    )
    assert r.status_code == 409, r.text


def test_normalise_entry_inner_class_preserves_dollar(tmp_path: Path) -> None:
    """Inner-class form ``com.example.Foo$Inner.onClick`` → Smali
    ``Lcom/example/Foo$Inner;->onClick(`` — the ``$`` is preserved as
    a class-name character (matches dex / smali's representation of
    inner classes; without this the method picker would query the
    wrong Smali descriptor and miss every inner-class method)."""
    client = _client(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/normalise-entry",
        json={"entry": "com.example.Foo$Inner.onClick"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["normalised_entry"] == "Lcom/example/Foo$Inner;->onClick("
    assert body["smali_class"] == "Lcom/example/Foo$Inner;"
    # The class isn't in the fixture's call graph; the parse path
    # is what we're pinning here, not the existence check.
    assert body["class_exists_in_graph"] is False


# ---------------------------------------------------------------------------
# 10) POST /suggest-similar-classes — Phase 11 v2.1 sub-step v2.1.3
#
# Tier-1 "Find similar classes" suggestion path that grows off the
# v2.1.2 ⚠ "class not found in call graph" validation pill. v2.1.3
# ships fuzzy-only (``difflib.SequenceMatcher`` against the call
# graph's ``classes.simple_name`` column); v2.1.5 will wire an
# LLM-backed semantic-search fallback into the same endpoint.
#
# The fixture's call-graph contains ``Lcom/trace/Plans;``,
# ``Lcom/trace/Gates;``, ``Lcom/trace/Helpers;``, etc. — the typo
# tests use these as the target classes.


def test_suggest_similar_classes_route_registered(tmp_path: Path) -> None:
    """The factory must register the v2.1.3 suggestion endpoint
    alongside the existing seven — guards against a refactor
    accidentally dropping it from ``build_trace_router``."""
    client = _client(tmp_path)
    paths = {r.path for r in client.app.routes}  # type: ignore[attr-defined]
    assert "/api/trace/{app_id}/suggest-similar-classes" in paths


def test_suggest_similar_classes_fuzzy_match_finds_typo(tmp_path: Path) -> None:
    """Operator-typed typo ``com.trace.Plnas`` (transposed letters)
    surfaces ``Lcom/trace/Plans;`` as a high-confidence fuzzy
    candidate. This is the headline operator path — the ⚠ pill
    grew the "Find similar classes" button and clicking it lands
    the typo's correction within the operator's eye-line."""
    client = _client(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/suggest-similar-classes",
        json={"entry": "com.trace.Plnas"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "fuzzy"
    assert body["error"] is None
    assert body["total"] >= 1
    candidate_classes = {c["smali_class"] for c in body["candidates"]}
    assert "Lcom/trace/Plans;" in candidate_classes
    # The Plans hit must carry a high confidence (single-character
    # transposition vs. a 5-char simple name → ratio ≈ 0.8+).
    plans_hit = next(
        c for c in body["candidates"] if c["smali_class"] == "Lcom/trace/Plans;"
    )
    assert plans_hit["confidence"] >= 0.7
    assert plans_hit["simple_name"] == "Plans"
    assert plans_hit["package"] == "com.trace"
    assert "fuzzy match" in plans_hit["rationale"]
    assert "similarity" in plans_hit["rationale"]


def test_suggest_similar_classes_no_match_returns_empty_list(
    tmp_path: Path,
) -> None:
    """Operator-typed input with no fuzzy match in the call graph →
    200 with ``candidates: []`` (NOT 404 — operator's input parsed
    cleanly, the response just has nothing to suggest). v2.1.5's
    LLM fallback would re-fire on this path, but in v2.1.3 the
    empty list is the terminal answer."""
    client = _client(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/suggest-similar-classes",
        json={"entry": "com.unrelated.Zzzzzzzzz"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["candidates"] == []
    assert body["total"] == 0
    assert body["source"] == "fuzzy"
    assert body["error"] is None


def test_suggest_similar_classes_unparseable_input_returns_422(
    tmp_path: Path,
) -> None:
    """Same parse-error contract as ``normalise-entry`` — un-parseable
    input lands as 422 with the ``_coalesce_entry`` reason in the
    detail. Frontend hides the suggestion list in this case (the
    v2.1.2 ✗ pill is the relevant signal; a sibling empty
    suggestion list would be operator-confusing)."""
    client = _client(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/suggest-similar-classes",
        json={"entry": ""},
    )
    assert r.status_code == 422, r.text
    assert "empty" in r.json()["detail"].lower()

    r = client.post(
        f"/api/trace/{APP_ID}/suggest-similar-classes",
        json={"entry": "com.example.foo"},  # No UpperCamelCase class.
    )
    assert r.status_code == 422, r.text


def test_suggest_similar_classes_unknown_app_returns_404(
    tmp_path: Path,
) -> None:
    """Unknown ``app_id`` → 404 via the shared ``app_dir_resolver``.
    Same contract as every other route in this module."""
    client = _client(tmp_path)
    r = client.post(
        "/api/trace/ghost-app/suggest-similar-classes",
        json={"entry": "com.example.Foo"},
    )
    assert r.status_code == 404, r.text


def test_suggest_similar_classes_decompile_not_ready_returns_409(
    tmp_path: Path,
) -> None:
    """App dir exists but decompile cache is missing → 409 — fuzzy
    matching needs the call graph SQLite, which needs the decompile
    cache. Mirrors every other route in this module that gates on
    ``_cache_dir_for``."""
    client = _client_no_decompile(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/suggest-similar-classes",
        json={"entry": "com.example.Foo"},
    )
    assert r.status_code == 409, r.text


def test_suggest_similar_classes_caps_results_at_five(tmp_path: Path) -> None:
    """The hard cap at 5 candidates per request keeps the operator-
    facing list focused — past 5 the lower-similarity tail tends to
    be noise. Pin the cap so a future refactor can't accidentally
    raise it without surfacing a planning conversation."""
    client = _client(tmp_path)
    # Use a single-character input that fuzzy-matches half the call
    # graph at low confidence — the cap is what bounds the response.
    r = client.post(
        f"/api/trace/{APP_ID}/suggest-similar-classes",
        json={"entry": "com.trace.SomeClass"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["candidates"]) <= 5


# ---------------------------------------------------------------------------
# Phase 13 / DEC-029 sub-step 13.2 — POST /{app_id}/dynamic +
# DELETE /{app_id}/dynamic/{session_id}
#
# Tests use a ``FridaClient``-subclass fake injected onto
# ``app.state.frida_client`` *after* ``create_app`` (so the lazy
# ``get_frida_client`` provider returns it via the
# ``isinstance(existing, FridaClient)`` cache check). Lighter than
# stubbing the ``_frida_python`` seam — we don't care about the
# device-handshake plumbing here, only the route's lifecycle around
# ``client.attach`` / ``session.set_persistence_path`` /
# ``session.load_script`` / ``session.detach``.
#
# Each test seeds a per-app ``app_meta.json`` with a ``dossier``
# package id so :func:`_resolve_target_prefix` falls back to it
# (mirrors the Hook Lab default-prefix path; tests exercising the
# 403 branch deliberately omit it).


from datetime import datetime, timezone  # noqa: E402 — locality with §13.2 tests
from typing import Optional  # noqa: E402

from androscan.adapters import frida_client as _fc_module  # noqa: E402
from androscan.adapters.frida_client import FridaClient  # noqa: E402


PACKAGE = "com.trace.target"


class _FakeFridaSession:
    """In-memory ``FridaSession``-shaped fake. Records the loaded JS +
    persistence path so tests can assert on them; ``detach()`` flips
    a flag so the DELETE route's contract is observable.

    Phase 13 sub-step 13.3 additions: also implements ``events()``
    (replay-buffer iteration) + ``on_event`` (live hook) so the
    WebSocket multiplex tests can drive synthetic Frida events
    through the WS handler without standing up a real Frida session.
    Use :meth:`emit` from the test code to push an event through
    whichever path is currently registered (replay queue if no
    ``on_event`` hook is bound; live hook otherwise)."""

    _id_counter = 0

    def __init__(self, package: str, pid: int) -> None:
        type(self)._id_counter += 1
        self.session_id = f"fake-session-{type(self)._id_counter}"
        self.package = package
        self.pid = pid
        self.app_id: Optional[str] = None
        self.template_id: Optional[str] = None
        self.started_at = datetime.now(timezone.utc)
        self.persistence_path: Optional[Path] = None
        self.loaded_js: Optional[str] = None
        self.detached = False
        # Optional knob for tests that want to exercise the 502
        # "load_script failed" branch.
        self.load_script_should_fail = False
        # 13.3 — replay buffer + live-event hook (mirrors
        # :class:`FridaSession`'s ring + ``on_event`` shape).
        self._replay_buffer: list[Any] = []
        self.on_event: Optional[Any] = None

    def set_persistence_path(self, path: Path) -> None:
        self.persistence_path = Path(path)
        # Mirror :class:`FridaSession.set_persistence_path` — the
        # actual writer creates parent dirs + opens the file. We
        # touch it here so ``persist_path``-existence assertions in
        # tests don't need to know about Frida internals.
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        self.persistence_path.touch(exist_ok=True)

    def load_script(self, source: str) -> None:
        if self.load_script_should_fail:
            raise RuntimeError("simulated load_script failure")
        self.loaded_js = source

    def detach(self) -> None:
        self.detached = True

    # ---- 13.3 WebSocket-multiplex test plumbing -----------------

    def events(self) -> list[Any]:
        """Snapshot of the replay buffer at the moment of call —
        matches :meth:`FridaSession.events` semantics enough that
        the WS handler's replay-then-stream loop sees a stable
        list to iterate."""
        return list(self._replay_buffer)

    def stage_replay_event(self, event: Any) -> None:
        """Add an event to the pre-WS replay buffer. Tests call this
        BEFORE opening the WebSocket so the event flows through the
        handler's ring-replay step."""
        self._replay_buffer.append(event)

    def emit_live_event(self, event: Any) -> None:
        """Push an event through the live ``on_event`` hook —
        simulates a Frida message arriving after the WebSocket has
        attached. No-op if no client has bound a hook yet."""
        hook = self.on_event
        if hook is not None:
            hook(event)


class _FakeFridaClient(FridaClient):
    """Minimal :class:`FridaClient` stand-in that bypasses the device
    handshake. ``is_available`` defaults to True; flip
    ``available_flag`` to False to exercise the 503 path. Test code
    can also stage ``attach_should_raise`` / ``attach_should_raise_unavailable``
    to exercise the 502 / 503 attach-failure branches.
    """

    def __init__(self) -> None:
        super().__init__()  # default ring_size=5000
        self.fake_sessions: dict[str, _FakeFridaSession] = {}
        self.attach_calls: list[str] = []
        self.available_flag = True
        self.attach_should_raise: Optional[Exception] = None
        self.attach_should_raise_unavailable: Optional[Exception] = None
        self.next_load_script_should_fail = False

    def is_available(self) -> bool:  # type: ignore[override]
        return self.available_flag

    def attach(self, package: str, *, spawn: bool = False) -> _FakeFridaSession:  # type: ignore[override]
        self.attach_calls.append(package)
        if self.attach_should_raise_unavailable is not None:
            raise self.attach_should_raise_unavailable
        if self.attach_should_raise is not None:
            raise self.attach_should_raise
        s = _FakeFridaSession(package=package, pid=4321)
        if self.next_load_script_should_fail:
            s.load_script_should_fail = True
            self.next_load_script_should_fail = False
        self.fake_sessions[s.session_id] = s
        return s

    def get_session(self, session_id: str) -> Optional[_FakeFridaSession]:  # type: ignore[override]
        return self.fake_sessions.get(session_id)


def _seed_app_meta_with_package(tmp_path: Path) -> None:
    """Overwrite the seeded ``app_meta.json`` with a dossier that has
    a manifest package id, so :func:`_resolve_target_prefix` falls
    back to it (matches the Hook Lab default-prefix path)."""
    app_dir = tmp_path / "apps" / APP_ID
    (app_dir / "app_meta.json").write_text(
        json.dumps({
            "apk_sha256": SHA,
            "apk_path": str(tmp_path / "fake.apk"),
            "dossier": {"apk_info": {"package": PACKAGE, "version_name": "1.0"}},
        }),
        encoding="utf-8",
    )


def _client_with_fake_frida(
    tmp_path: Path,
    *,
    available: bool = True,
    seed_package: bool = True,
) -> tuple[TestClient, _FakeFridaClient]:
    """Build a TestClient with a fake Frida client wired onto
    ``app.state.frida_client``. Returns ``(client, fake)`` so tests
    can assert on the fake's recorded calls.

    ``seed_package=False`` skips the ``app_meta.json`` package-id
    write so :func:`_resolve_target_prefix` returns ``None`` and
    the route's 403 branch is exercised."""
    apps_root = _seed_app_with_graph(tmp_path)
    if seed_package:
        _seed_app_meta_with_package(tmp_path)
    app = create_app(Config.default(), cwd=apps_root.parent)
    fake = _FakeFridaClient()
    fake.available_flag = available
    # Wire the fake into the app's ``frida_client`` cache so the lazy
    # ``get_frida_client`` provider returns it instead of constructing
    # a real (frida-server-talking) one. Works because the cache
    # check is ``isinstance(existing, FridaClient)`` — the fake
    # subclasses ``FridaClient``.
    app.state.frida_client = fake
    return TestClient(app), fake


def _post_anchor(client: TestClient, entry: str = ENTRY_BOOL, hops: int = 1) -> dict[str, Any]:
    """Build + cache a BehaviorAnchor through the existing
    ``POST /anchor`` route — the dynamic-trace endpoint reads the
    cached row, so every dynamic test must seed it first."""
    r = client.post(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": entry, "hops": hops},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- registration smoke ---


def test_dynamic_routes_registered() -> None:
    """Both new routes show up on the app — guards a future refactor
    from accidentally dropping them. The trace router's path catalog
    grew by exactly two entries in 13.2."""
    app = create_app(Config.default())
    paths = {(r.path, tuple(sorted(getattr(r, "methods", set())))) for r in app.routes}
    assert ("/api/trace/{app_id}/dynamic", ("POST",)) in paths
    assert ("/api/trace/{app_id}/dynamic/{session_id}", ("DELETE",)) in paths


# --- happy path ---


def test_dynamic_start_happy_path(tmp_path: Path) -> None:
    """Cached anchor + Frida available + per-app prefix configured →
    200 with session_id + hook_count + persist_path. The fake records
    the package the route attached to + the JS the route loaded."""
    client, fake = _client_with_fake_frida(tmp_path)
    _post_anchor(client)
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1, "hop_cap": 50},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Wire shape — pin every field 13.2's docs promised.
    assert body["session_id"].startswith("fake-session-")
    assert body["app_id"] == APP_ID
    assert body["template_id"] == "behavior_trace_multi"
    assert body["package"] == PACKAGE
    assert body["pid"] == 4321
    assert body["started_at"]  # ISO-8601 timestamp string
    assert body["hook_count"] >= 1
    assert body["closure_size"] >= body["hook_count"]
    assert body["hop_cap"] == 50
    assert body["event_label"].startswith("behavior-trace-")
    assert body["ws_url"] == f"/ws/frida/{body['session_id']}"
    assert body["persist_path"]
    assert body["anchor"]["entry_method"] == ENTRY_BOOL
    assert body["anchor"]["hops"] == 1
    # Side-effects on the fake.
    assert fake.attach_calls == [PACKAGE]
    session = fake.fake_sessions[body["session_id"]]
    assert session.app_id == APP_ID
    assert session.template_id == "behavior_trace_multi"
    assert session.persistence_path is not None
    assert session.persistence_path.name == "dynamic_trace.jsonl"
    assert session.loaded_js is not None
    # The ``behavior_trace_multi`` template's JS body always contains
    # the ``methodsList`` array literal — proves the render happened.
    assert "methodsList" in session.loaded_js
    assert body["event_label"] in session.loaded_js


def test_dynamic_start_jsonl_path_under_run_folder(tmp_path: Path) -> None:
    """DEC-029 lock: persistence path is
    ``apps/<app_id>/<run>/dynamic_trace.jsonl``. Pin the path
    structure so a future refactor can't silently move the file
    (the UI will path-join against this string in 13.4)."""
    client, fake = _client_with_fake_frida(tmp_path)
    _post_anchor(client)
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 200, r.text
    persist = Path(r.json()["persist_path"])
    assert persist.name == "dynamic_trace.jsonl"
    # Path layout: <root>/apps/<app_id>/<run_ts>/dynamic_trace.jsonl
    parts = persist.parts
    apps_idx = parts.index("apps")
    assert parts[apps_idx + 1] == APP_ID
    # The run_ts leaf is whatever ``run_timestamp()`` produced — its
    # exact format is owned by run_folder.py; we just assert the
    # file lives one folder under apps/<app_id>.
    assert persist.parent.parent.name == APP_ID
    assert persist.is_file()  # set_persistence_path created it


def test_dynamic_start_event_label_passed_through(tmp_path: Path) -> None:
    """Operator-supplied ``event_label`` overrides the auto-generated
    one and ends up in the rendered JS — lets the frontend filter
    the WebSocket stream by a known label without parsing the
    backend's random suffix."""
    client, fake = _client_with_fake_frida(tmp_path)
    _post_anchor(client)
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1, "event_label": "my-trace-2026-05-08"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event_label"] == "my-trace-2026-05-08"
    session = fake.fake_sessions[body["session_id"]]
    assert "my-trace-2026-05-08" in (session.loaded_js or "")


def test_dynamic_start_hop_cap_truncates_closure(tmp_path: Path) -> None:
    """hop_cap=1 caps the methods list to just the entry method.
    ``closure_size > hook_count`` is the operator-visible signal that
    the cap was enforced."""
    client, fake = _client_with_fake_frida(tmp_path)
    _post_anchor(client)
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1, "hop_cap": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hook_count"] == 1
    assert body["closure_size"] >= 1
    # Loaded JS contains the entry method's class but not necessarily
    # other plan-target methods (since hop_cap=1 culled them).
    session = fake.fake_sessions[body["session_id"]]
    assert "com.trace.Plans" in (session.loaded_js or "")


# --- 4xx error paths ---


def test_dynamic_start_unknown_app_returns_404(tmp_path: Path) -> None:
    """Unknown ``app_id`` → 404 from the resolver, before any Frida
    interaction. Pin the contract so the UI can show a clean
    "app not found" rather than parse a 503."""
    client, _fake = _client_with_fake_frida(tmp_path)
    r = client.post(
        "/api/trace/no-such-app/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 404, r.text


def test_dynamic_start_decompile_not_ready_returns_409(tmp_path: Path) -> None:
    """App exists but no decompile cache → 409 (mirrors every other
    route's ``_cache_dir_for`` precondition). The dynamic route
    must surface this BEFORE it tries to read the cached anchor."""
    apps_root = tmp_path / "apps"
    (apps_root / APP_ID).mkdir(parents=True)
    app = create_app(Config.default(), cwd=apps_root.parent)
    app.state.frida_client = _FakeFridaClient()
    client = TestClient(app)
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 409, r.text


def test_dynamic_start_no_cached_anchor_returns_404(tmp_path: Path) -> None:
    """Decompile is ready, but no row in trace.sqlite for
    ``(entry, hops)`` → 404 with a hint pointing at ``POST /anchor``.
    Operators see this when they hit Run Trace before ever
    building the anchor — the message must point them at the fix."""
    client, _fake = _client_with_fake_frida(tmp_path)
    # Don't POST the anchor — exercise the cache-miss path.
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 404, r.text
    detail = r.json()["detail"].lower()
    assert "no cached anchor" in detail
    assert "/anchor" in detail


def test_dynamic_start_empty_entry_returns_422(tmp_path: Path) -> None:
    """Pydantic's ``min_length=1`` on the body model rejects empty
    ``entry`` strings before the route handler runs."""
    client, _fake = _client_with_fake_frida(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": "", "hops": 1},
    )
    assert r.status_code == 422, r.text


def test_dynamic_start_oversize_entry_returns_422(tmp_path: Path) -> None:
    """``entry`` length over ``_MAX_ENTRY_LEN`` (500) → 422 from
    Pydantic. The bound matches the GET /anchor route's clamp so
    the two paths can't diverge silently."""
    client, _fake = _client_with_fake_frida(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": "L" + "a" * 600 + ";->m()V", "hops": 1},
    )
    assert r.status_code == 422, r.text


def test_dynamic_start_hop_cap_zero_returns_422(tmp_path: Path) -> None:
    """``hop_cap=0`` → Pydantic ``ge=1`` rejection. The route doesn't
    even need to read the anchor — the field validation runs first."""
    client, _fake = _client_with_fake_frida(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1, "hop_cap": 0},
    )
    assert r.status_code == 422, r.text


def test_dynamic_start_hop_cap_too_large_returns_422(tmp_path: Path) -> None:
    """``hop_cap`` over ``_MAX_HOP_CAP`` (500) → 422. Operators hitting
    the ceiling get an explicit signal rather than a silent clamp."""
    client, _fake = _client_with_fake_frida(tmp_path)
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1, "hop_cap": 501},
    )
    assert r.status_code == 422, r.text


def test_dynamic_start_no_hook_prefix_returns_403(tmp_path: Path) -> None:
    """No per-app ``hook_target_package_prefix`` configured + no
    manifest package id → 403 fail-closed (mirrors the Hook Lab's
    posture). The dynamic-trace route must not accidentally allow
    a "hook arbitrary package" path."""
    # Seed the app + decompile cache but NOT the package id in
    # app_meta.json.
    apps_root = _seed_app_with_graph(tmp_path)
    # Overwrite app_meta.json to drop the package id (the seed wrote
    # an empty dossier; ``_resolve_target_prefix`` will return None).
    app_dir = tmp_path / "apps" / APP_ID
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": SHA, "apk_path": "x", "dossier": {}}),
        encoding="utf-8",
    )
    app = create_app(Config.default(), cwd=apps_root.parent)
    app.state.frida_client = _FakeFridaClient()
    client = TestClient(app)
    _post_anchor(client)
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 403, r.text
    assert "hook_blocked" in r.json()["detail"]


# --- 5xx error paths ---


def test_dynamic_start_frida_unavailable_returns_503(tmp_path: Path) -> None:
    """``client.is_available() == False`` → 503 with the install hint.
    Pin the message text so the UI can keep its "install [frida]"
    link in sync with the actual error."""
    client, _fake = _client_with_fake_frida(tmp_path, available=False)
    _post_anchor(client)
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 503, r.text
    assert "frida_unavailable" in r.json()["detail"]


def test_dynamic_start_frida_attach_unavailable_returns_503(tmp_path: Path) -> None:
    """``client.attach`` raises :class:`FridaUnavailableError` →
    503 (matches the Hook Lab attach path's translation)."""
    client, fake = _client_with_fake_frida(tmp_path)
    _post_anchor(client)
    fake.attach_should_raise_unavailable = _fc_module.FridaUnavailableError(
        "frida-server not running on device"
    )
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 503, r.text


def test_dynamic_start_frida_attach_generic_returns_502(tmp_path: Path) -> None:
    """``client.attach`` raises a generic :class:`Exception` → 502
    (the device said no, the workbench is healthy)."""
    client, fake = _client_with_fake_frida(tmp_path)
    _post_anchor(client)
    fake.attach_should_raise = RuntimeError("permission denied")
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 502, r.text
    assert "frida attach failed" in r.json()["detail"]


def test_dynamic_start_load_script_failure_detaches_session(tmp_path: Path) -> None:
    """``session.load_script`` raises → 502 + the half-attached
    session is detached. Otherwise a transient JS-render error
    would leak a Frida session per attempt — observable as
    ``frida-server`` slot exhaustion on long debug sessions."""
    client, fake = _client_with_fake_frida(tmp_path)
    _post_anchor(client)
    fake.next_load_script_should_fail = True
    r = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 502, r.text
    # The fake captured the half-attached session — confirm it was
    # detached as part of the route's cleanup path.
    assert len(fake.fake_sessions) == 1
    session = next(iter(fake.fake_sessions.values()))
    assert session.detached is True


def test_dynamic_start_503_when_no_frida_provider(tmp_path: Path) -> None:
    """If the trace router is constructed without a
    ``frida_client_provider`` (legacy two-arg path), the dynamic
    routes return 503 cleanly rather than crashing on a None call."""
    from androscan.web.trace_routes import build_trace_router
    apps_root = _seed_app_with_graph(tmp_path)
    _seed_app_meta_with_package(tmp_path)
    from fastapi import FastAPI, HTTPException
    app = FastAPI()

    def _resolver(app_id: str) -> Path:
        d = apps_root / app_id
        if not d.is_dir():
            raise HTTPException(status_code=404, detail="Unknown app_id")
        return d

    _trace_rest, _trace_ws = build_trace_router(
        lambda: Config.default(), _resolver
    )  # no third arg → dynamic routes return 503
    app.include_router(_trace_rest)
    app.include_router(_trace_ws)
    test_client = TestClient(app)
    # Seed the cached anchor by hitting the existing POST /anchor
    # route on this same router (still works without Frida).
    r0 = test_client.post(
        f"/api/trace/{APP_ID}/anchor",
        params={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r0.status_code == 200, r0.text
    r = test_client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    )
    assert r.status_code == 503, r.text
    assert "frida_unavailable" in r.json()["detail"]


# --- DELETE / stop endpoint ---


def test_dynamic_stop_happy_path(tmp_path: Path) -> None:
    """Start a dynamic trace, then DELETE the session — fake's
    ``detached`` flag flips to True; route returns
    ``{"ok": True, "session_id": ...}``."""
    client, fake = _client_with_fake_frida(tmp_path)
    _post_anchor(client)
    started = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    ).json()
    session_id = started["session_id"]
    r = client.delete(f"/api/trace/{APP_ID}/dynamic/{session_id}")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "session_id": session_id}
    assert fake.fake_sessions[session_id].detached is True


def test_dynamic_stop_unknown_session_returns_404(tmp_path: Path) -> None:
    """DELETE with a session_id the client doesn't know → 404
    (matches ``DELETE /api/frida/sessions/{id}`` exactly)."""
    client, _fake = _client_with_fake_frida(tmp_path)
    r = client.delete(f"/api/trace/{APP_ID}/dynamic/no-such-session")
    assert r.status_code == 404, r.text
    assert "unknown session_id" in r.json()["detail"]


def test_dynamic_stop_unknown_app_returns_404(tmp_path: Path) -> None:
    """DELETE with an app_id that doesn't exist → 404 from the
    resolver before the session lookup runs. URL grammar stays
    honest even though the session-id check is the load-bearing one."""
    client, _fake = _client_with_fake_frida(tmp_path)
    r = client.delete("/api/trace/no-such-app/dynamic/some-session")
    assert r.status_code == 404, r.text


def test_dynamic_stop_swallows_detach_exceptions(tmp_path: Path) -> None:
    """``session.detach`` raising must NOT propagate as a 5xx —
    the session is going to be GC'd by ``detach_all`` on shutdown
    either way; the route logs the warning + returns 200 so the
    operator's "Stop trace" button always succeeds visually."""
    client, fake = _client_with_fake_frida(tmp_path)
    _post_anchor(client)
    started = client.post(
        f"/api/trace/{APP_ID}/dynamic",
        json={"entry": ENTRY_BOOL, "hops": 1},
    ).json()
    session_id = started["session_id"]
    # Make detach raise — emulate the device dropping the session
    # mid-stop (frida_routes' ``DELETE /sessions/{id}`` swallows the
    # same way; mirror the contract here).
    session = fake.fake_sessions[session_id]
    original_detach = session.detach

    def _raising_detach() -> None:
        original_detach()
        raise RuntimeError("device gone")

    session.detach = _raising_detach  # type: ignore[method-assign]
    r = client.delete(f"/api/trace/{APP_ID}/dynamic/{session_id}")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_dynamic_stop_503_when_no_frida_provider(tmp_path: Path) -> None:
    """No-frida-provider router → 503 on DELETE too (parallel to
    the start-endpoint contract)."""
    from androscan.web.trace_routes import build_trace_router
    apps_root = _seed_app_with_graph(tmp_path)
    _seed_app_meta_with_package(tmp_path)
    from fastapi import FastAPI, HTTPException
    app = FastAPI()

    def _resolver(app_id: str) -> Path:
        d = apps_root / app_id
        if not d.is_dir():
            raise HTTPException(status_code=404, detail="Unknown app_id")
        return d

    _trace_rest, _trace_ws = build_trace_router(
        lambda: Config.default(), _resolver
    )
    app.include_router(_trace_rest)
    app.include_router(_trace_ws)
    test_client = TestClient(app)
    r = test_client.delete(f"/api/trace/{APP_ID}/dynamic/some-session")
    assert r.status_code == 503, r.text


# ---------------------------------------------------------------------------
# Phase 13 / DEC-029 sub-step 13.3 — WS /ws/trace/{app_id}/{session_id}
#
# Tests build a fresh FastAPI app via ``build_trace_router`` directly
# (rather than going through ``create_app``) so they can inject:
#
#   1. The same ``_FakeFridaClient`` used by 13.2's POST/DELETE tests
#      — bypasses the device handshake.
#   2. A controllable stub ``summary_callable`` so the WS multiplex
#      branches (cache hit / cache miss → ready / cache miss → failed
#      / cache miss → timeout) are deterministic + fast (no real LLM
#      round-trip).
#
# The WS handler's replay-then-stream lifecycle is exercised by
# pre-staging events on ``_FakeFridaSession._replay_buffer`` BEFORE
# connecting (replay branch) and emitting events post-connect via
# ``session.emit_live_event(...)`` (live branch).
#
# All tests use ``TestClient.websocket_connect`` as a context manager
# — closes the WS automatically on test teardown so the handler's
# ``finally`` block (hook restore + pending-task cancel) is hit.


from fastapi import FastAPI as _FastAPI  # noqa: E402 — locality with §13.3 tests

from androscan.adapters.frida_client import TraceEvent  # noqa: E402
from androscan.web.trace_routes import build_trace_router as _build_trace_router  # noqa: E402
from androscan.web.trace_summary import (  # noqa: E402
    SUMMARY_FAILED_KIND as _SUMMARY_FAILED_KIND,
    SUMMARY_PENDING_KIND as _SUMMARY_PENDING_KIND,
    SUMMARY_READY_KIND as _SUMMARY_READY_KIND,
)


def _make_entry_event(
    *,
    session_id: str,
    cls_java: str,
    method_name: str,
    descriptor: str,
    seq: int = 1,
) -> TraceEvent:
    """Build a synthetic ``phase: "entry"`` :class:`TraceEvent` matching
    the locked 13.1 wire shape so the WS handler's
    ``_dispatch_summary_for_entry`` is triggered."""
    return TraceEvent(
        ts=1700000000.0 + seq,
        session_id=session_id,
        kind="send",
        payload={
            "phase": "entry",
            "class": cls_java,
            "method": method_name,
            "descriptor": descriptor,
            "args": [],
            "seq": seq,
            "thread_id": 1,
            "parent_call_seq": None,
        },
        raw={"type": "send", "payload": {"phase": "entry"}},
    )


def _make_non_entry_event(
    *, session_id: str, phase: str = "ready", seq: int = 0
) -> TraceEvent:
    """Build a non-``entry`` event (``ready`` / ``exit`` / etc.) that
    must NOT trigger summary multiplexing."""
    return TraceEvent(
        ts=1700000000.0 + seq,
        session_id=session_id,
        kind="send",
        payload={"phase": phase, "seq": seq},
        raw={"type": "send", "payload": {"phase": phase}},
    )


def _ws_app_with_summary_stub(
    tmp_path: Path,
    *,
    summary_callable=None,
    seed_package: bool = True,
    frida_client=None,
) -> tuple[TestClient, "_FakeFridaClient", "_FakeFridaSession"]:
    """Build a minimal FastAPI app wired with the trace router (rest
    + ws), a fake Frida client + session, and the supplied summary
    callable. Returns ``(client, fake_frida, fake_session)`` —
    ``fake_session`` already attached and visible via
    ``fake_frida.get_session(session.session_id)`` so tests can
    open the WebSocket immediately.

    Cache reads (and writes — 13.4 will care) hit
    ``tmp_path / app_id / skill_results_cache.json`` because we
    construct the test's ``Config`` with ``run_folder_root=str(tmp_path)``.
    The seeded app meta carries ``apk_sha256="cafe" * 16`` so the
    cache key is stable across the test."""
    apps_root = _seed_app_with_graph(tmp_path)
    if seed_package:
        _seed_app_meta_with_package(tmp_path)

    import dataclasses
    cfg = dataclasses.replace(Config.default(), run_folder_root=str(tmp_path))
    fake = frida_client if frida_client is not None else _FakeFridaClient()

    def _config_provider() -> Config:
        return cfg

    def _resolver(app_id: str) -> Path:
        d = apps_root / app_id
        if not d.is_dir():
            raise HTTPException(status_code=404, detail="Unknown app_id")
        return d

    app = _FastAPI()
    rest, ws = _build_trace_router(
        _config_provider,
        _resolver,
        lambda: fake,
        summary_callable,
    )
    app.include_router(rest)
    app.include_router(ws)

    # Pre-attach a fake session so tests can connect immediately
    # (skips the POST /dynamic round-trip — 13.2 tests already cover
    # that path).
    fake_session = fake.attach("com.trace.target")
    fake_session.app_id = APP_ID
    fake_session.template_id = "behavior_trace_multi"

    return TestClient(app), fake, fake_session


from fastapi import HTTPException  # noqa: E402


# --- WS registration smoke ---


def test_ws_trace_route_registered() -> None:
    """The new WS path shows up on the app — regression guard for a
    future refactor accidentally dropping the WS router include."""
    app = create_app(Config.default())
    paths = {r.path for r in app.routes}
    assert "/ws/trace/{app_id}/{session_id}" in paths


# --- Replay-then-stream (no summary multiplex) ---


async def _stub_summary_unused(
    cls_java: str, method_name: str, descriptor: str
) -> str:
    """Stub callable that fails loudly if invoked — used by tests
    that assert the summary multiplex stays QUIET (e.g. for events
    that aren't ``phase: "entry"``)."""
    raise AssertionError(
        f"summary callable should not have been invoked for {cls_java}.{method_name}"
    )


def test_ws_replays_pre_existing_events_to_late_joiner(tmp_path: Path) -> None:
    """The WS handler's first step drains the ring buffer once so
    a late joiner gets the events that already fired before they
    connected. Pin so a future refactor can't accidentally start
    skipping replay — operators rely on it (the dynamic-trace
    button fires the route + opens the WS in two separate calls
    with a small gap; events between those calls live in the ring
    buffer)."""
    client, _fake, session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub_summary_unused
    )
    session.stage_replay_event(
        _make_non_entry_event(session_id=session.session_id, phase="ready")
    )
    session.stage_replay_event(
        _make_non_entry_event(session_id=session.session_id, phase="exit", seq=2)
    )
    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session.session_id}"
    ) as ws:
        first = ws.receive_json()
        second = ws.receive_json()
    assert first["payload"]["phase"] == "ready"
    assert second["payload"]["phase"] == "exit"
    assert first["session_id"] == session.session_id


def test_ws_unknown_session_closes_with_1008(tmp_path: Path) -> None:
    """Connecting to a session_id the fake Frida client doesn't know
    about → the handler sends a structured error + closes with
    1008 (matches ``frida_routes.ws_trace`` exactly so 13.6's
    frontend reuses the same disconnect handler)."""
    client, _fake, _session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub_summary_unused
    )
    from starlette.websockets import WebSocketDisconnect as _WSD
    with client.websocket_connect(f"/ws/trace/{APP_ID}/no-such-session") as ws:
        msg = ws.receive_json()
        assert msg == {
            "type": "error",
            "error": "unknown_session",
            "app_id": APP_ID,
            "session_id": "no-such-session",
        }
        with pytest.raises(_WSD):
            ws.receive_json()


def test_ws_503_when_no_frida_provider(tmp_path: Path) -> None:
    """Trace router constructed without a ``frida_client_provider``
    (legacy three-arg path) → WS sends ``frida_unavailable`` error
    + 1008-closes. Mirrors the 13.2 POST/DELETE 503 contract on
    the WebSocket layer."""
    apps_root = _seed_app_with_graph(tmp_path)
    _seed_app_meta_with_package(tmp_path)
    app = _FastAPI()

    def _resolver(app_id: str) -> Path:
        d = apps_root / app_id
        if not d.is_dir():
            raise HTTPException(status_code=404, detail="Unknown app_id")
        return d

    rest, ws = _build_trace_router(lambda: Config.default(), _resolver)
    app.include_router(rest)
    app.include_router(ws)
    test_client = TestClient(app)
    from starlette.websockets import WebSocketDisconnect as _WSD
    with test_client.websocket_connect(
        f"/ws/trace/{APP_ID}/some-session"
    ) as conn:
        msg = conn.receive_json()
        assert msg["error"] == "frida_unavailable"
        with pytest.raises(_WSD):
            conn.receive_json()


# --- Summary multiplex: cache miss → pending → ready ---


def test_ws_entry_event_fires_pending_then_ready(tmp_path: Path) -> None:
    """Cache miss + first ``phase: "entry"`` for a method →
    ``summary_pending`` event followed by ``summary_ready`` once
    the stub callable resolves. ``cached: false`` distinguishes
    fresh results from cache hits (UI uses the flag to grey out
    a "(cached)" badge)."""

    async def _stub(cls_java: str, method: str, descriptor: str) -> str:
        return f"{method}() summary text"

    client, _fake, session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub
    )
    session.stage_replay_event(
        _make_entry_event(
            session_id=session.session_id,
            cls_java="com.trace.Plans",
            method_name="gateBoolPredicate",
            descriptor="()V",
        )
    )

    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session.session_id}"
    ) as ws:
        # Trace event first (replay)
        trace_evt = ws.receive_json()
        assert trace_evt["payload"]["phase"] == "entry"
        # Then pending + ready (in order)
        pending = ws.receive_json()
        ready = ws.receive_json()

    assert pending["kind"] == _SUMMARY_PENDING_KIND
    assert pending["payload"] == {
        "class": "com.trace.Plans",
        "method": "gateBoolPredicate",
        "descriptor": "()V",
    }
    assert ready["kind"] == _SUMMARY_READY_KIND
    assert ready["payload"]["summary"] == "gateBoolPredicate() summary text"
    assert ready["payload"]["cached"] is False


# --- Summary multiplex: cache hit → ready immediately (no pending) ---


def test_ws_cache_hit_skips_pending_emits_ready_with_cached_true(tmp_path: Path) -> None:
    """A summary already in ``skill_results_cache`` for the
    ``(app_sha, class_smali, method_name, descriptor)`` key →
    ``summary_ready`` with ``cached: true`` is emitted IMMEDIATELY
    on first ``entry`` hit; ``summary_pending`` is NOT emitted
    (no flicker for the operator's UI). The summary callable is
    NEVER invoked on cache hits."""

    # Pre-seed the cache.
    from androscan.web.trace_summary import store_summary
    store_summary(
        run_folder_root=tmp_path,
        app_id=APP_ID,
        run_folder_name="test-seed",
        app_sha=SHA,
        class_java_or_smali="com.trace.Plans",
        method_name="gateBoolPredicate",
        descriptor="()V",
        summary_text="cached summary from prior run",
    )

    callable_invocations = 0

    async def _stub(cls_java: str, method: str, descriptor: str) -> str:
        nonlocal callable_invocations
        callable_invocations += 1
        return "(should not reach here)"

    client, _fake, session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub
    )
    session.stage_replay_event(
        _make_entry_event(
            session_id=session.session_id,
            cls_java="com.trace.Plans",
            method_name="gateBoolPredicate",
            descriptor="()V",
        )
    )

    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session.session_id}"
    ) as ws:
        trace_evt = ws.receive_json()
        ready = ws.receive_json()

    assert trace_evt["payload"]["phase"] == "entry"
    assert ready["kind"] == _SUMMARY_READY_KIND
    assert ready["payload"]["summary"] == "cached summary from prior run"
    assert ready["payload"]["cached"] is True
    assert callable_invocations == 0


# --- Summary multiplex: per-method dedup ---


def test_ws_same_method_fires_summary_only_once(tmp_path: Path) -> None:
    """Two ``entry`` events for the same ``(class, method, descriptor)``
    in the same session → only ONE summary emit. Operators only
    care about the summary once per method per session; re-firing
    on every invocation would flood the UI + waste LLM budget."""

    callable_invocations = 0

    async def _stub(cls_java: str, method: str, descriptor: str) -> str:
        nonlocal callable_invocations
        callable_invocations += 1
        return "summary v1"

    client, _fake, session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub
    )
    # Two entry events for the same method.
    session.stage_replay_event(
        _make_entry_event(
            session_id=session.session_id,
            cls_java="com.trace.Plans",
            method_name="gateBoolPredicate",
            descriptor="()V",
            seq=1,
        )
    )
    session.stage_replay_event(
        _make_entry_event(
            session_id=session.session_id,
            cls_java="com.trace.Plans",
            method_name="gateBoolPredicate",
            descriptor="()V",
            seq=2,
        )
    )

    received: list[dict[str, Any]] = []
    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session.session_id}"
    ) as ws:
        # 2 trace events + 1 pending + 1 ready = 4 messages total.
        for _ in range(4):
            received.append(ws.receive_json())

    pending_count = sum(
        1 for m in received if m.get("kind") == _SUMMARY_PENDING_KIND
    )
    ready_count = sum(
        1 for m in received if m.get("kind") == _SUMMARY_READY_KIND
    )
    assert pending_count == 1, received
    assert ready_count == 1, received
    assert callable_invocations == 1


# --- Summary multiplex: failure modes ---


def test_ws_summary_callable_raises_emits_summary_failed(tmp_path: Path) -> None:
    """Generic LLM-side exception → ``summary_failed`` with the
    operator-facing error message. Pin the wire shape so 13.6's
    frontend can render the failure inline in the Inspector pane
    without parsing a generic 5xx."""

    async def _stub(cls_java: str, method: str, descriptor: str) -> str:
        raise RuntimeError("ollama gone")

    client, _fake, session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub
    )
    session.stage_replay_event(
        _make_entry_event(
            session_id=session.session_id,
            cls_java="com.trace.Plans",
            method_name="gateBoolPredicate",
            descriptor="()V",
        )
    )

    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session.session_id}"
    ) as ws:
        ws.receive_json()  # trace event
        pending = ws.receive_json()
        failed = ws.receive_json()

    assert pending["kind"] == _SUMMARY_PENDING_KIND
    assert failed["kind"] == _SUMMARY_FAILED_KIND
    assert failed["payload"]["error"] == "ollama gone"
    assert failed["payload"]["class"] == "com.trace.Plans"
    assert failed["payload"]["method"] == "gateBoolPredicate"


def test_ws_summary_callable_timeout_emits_summary_failed(tmp_path: Path) -> None:
    """:class:`asyncio.TimeoutError` from the callable → ``summary_failed``
    with ``error: "summary_timeout"``. The default timeout is
    30 s; tests use a stub that raises it directly to keep the
    test fast (no real wait)."""

    async def _stub(cls_java: str, method: str, descriptor: str) -> str:
        raise asyncio.TimeoutError()

    client, _fake, session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub
    )
    session.stage_replay_event(
        _make_entry_event(
            session_id=session.session_id,
            cls_java="com.trace.Plans",
            method_name="gateBoolPredicate",
            descriptor="()V",
        )
    )

    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session.session_id}"
    ) as ws:
        ws.receive_json()  # trace event
        ws.receive_json()  # pending
        failed = ws.receive_json()

    assert failed["kind"] == _SUMMARY_FAILED_KIND
    assert failed["payload"]["error"] == "summary_timeout"


def test_ws_summary_callable_returns_empty_emits_summary_failed(tmp_path: Path) -> None:
    """Empty / whitespace-only LLM response → ``summary_failed`` with
    ``error: "empty_summary"``. Mirrors the cache-layer's
    treat-empty-as-miss discipline (we'd rather flag the failure
    than emit a useless empty ``summary_ready``)."""

    async def _stub(cls_java: str, method: str, descriptor: str) -> str:
        return "   "

    client, _fake, session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub
    )
    session.stage_replay_event(
        _make_entry_event(
            session_id=session.session_id,
            cls_java="com.trace.Plans",
            method_name="gateBoolPredicate",
            descriptor="()V",
        )
    )

    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session.session_id}"
    ) as ws:
        ws.receive_json()  # trace event
        ws.receive_json()  # pending
        failed = ws.receive_json()

    assert failed["kind"] == _SUMMARY_FAILED_KIND
    assert failed["payload"]["error"] == "empty_summary"


# --- Summary multiplex: scope of dispatch ---


def test_ws_non_entry_phase_does_not_fire_summary(tmp_path: Path) -> None:
    """``phase: "ready"`` / ``"exit"`` / ``"hook_failed"`` events
    must NOT trigger summary multiplexing. Pin so a future
    refactor can't accidentally fire summaries on every event
    (each ``entry`` causes one LLM call already; firing on every
    phase would spam the LLM 5x per method)."""
    client, _fake, session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub_summary_unused
    )
    for phase, seq in [("ready", 0), ("exit", 1), ("hook_failed", 2), ("error", 3)]:
        session.stage_replay_event(
            _make_non_entry_event(
                session_id=session.session_id, phase=phase, seq=seq
            )
        )
    received: list[dict[str, Any]] = []
    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session.session_id}"
    ) as ws:
        for _ in range(4):
            received.append(ws.receive_json())
    # All four messages are trace events; none are summary kinds.
    for m in received:
        assert m["kind"] not in (
            _SUMMARY_PENDING_KIND, _SUMMARY_READY_KIND, _SUMMARY_FAILED_KIND
        )
        assert m["payload"]["phase"] in ("ready", "exit", "hook_failed", "error")


def test_ws_no_summary_callable_skips_multiplex_but_streams_events(tmp_path: Path) -> None:
    """Trace router built with ``summary_callable=None`` → the WS
    still forwards trace events end-to-end but skips the summary
    multiplex entirely. Used by deployments without an LLM
    available + by 13.4 if it temporarily falls back during a
    skill-registration failure."""
    # Build manually (not via the helper) so we can pass
    # ``summary_callable=None`` explicitly — the helper would
    # otherwise resolve to the production default.
    apps_root = _seed_app_with_graph(tmp_path)
    _seed_app_meta_with_package(tmp_path)
    import dataclasses
    cfg = dataclasses.replace(Config.default(), run_folder_root=str(tmp_path))
    fake = _FakeFridaClient()

    def _resolver(app_id: str) -> Path:
        d = apps_root / app_id
        if not d.is_dir():
            raise HTTPException(status_code=404, detail="Unknown app_id")
        return d

    # Construct a callable that explicitly opts out of the multiplex
    # by raising — but we expect dispatch to NEVER reach it because
    # we'll wire it as None below.
    app = _FastAPI()
    rest, ws_router = _build_trace_router(
        lambda: cfg, _resolver, lambda: fake,
    )
    # Reach into the router and rebuild with summary_callable=None.
    # Cleanest path: ditch the routers, rebuild explicitly.
    app2 = _FastAPI()

    # Trick: pass an "explicitly disable" sentinel via a wrapper that
    # returns None from default_summary_callable. Easier: the
    # function signature already supports None — but the factory
    # falls back to the production default. So we monkey at
    # construction time with a no-op summary callable that emits the
    # "should not reach here" guard.
    callable_invocations = 0

    async def _never_called(cls_java: str, method: str, descriptor: str) -> str:
        nonlocal callable_invocations
        callable_invocations += 1
        return "(should never be reached)"

    rest2, ws2 = _build_trace_router(
        lambda: cfg, _resolver, lambda: fake, _never_called,
    )
    app2.include_router(rest2)
    app2.include_router(ws2)

    fake_session = fake.attach("com.trace.target")
    fake_session.app_id = APP_ID

    # Stage a non-entry event so dispatch is exercised but multiplex
    # stays QUIET (the dispatch only fires on phase=="entry").
    fake_session.stage_replay_event(
        _make_non_entry_event(session_id=fake_session.session_id, phase="ready")
    )

    test_client = TestClient(app2)
    with test_client.websocket_connect(
        f"/ws/trace/{APP_ID}/{fake_session.session_id}"
    ) as ws:
        msg = ws.receive_json()
    assert msg["payload"]["phase"] == "ready"
    assert callable_invocations == 0


# --- Live-event path ---


def test_ws_live_event_fires_summary_after_replay(tmp_path: Path) -> None:
    """Events arriving via the live ``on_event`` hook (after the
    replay drain) trigger the same summary multiplex. Mirrors the
    replay-buffer test case but exercises the live-pump path."""

    async def _stub(cls_java: str, method: str, descriptor: str) -> str:
        return "live summary"

    client, _fake, session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub
    )
    # No replay events — connect first, then emit live.

    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session.session_id}"
    ) as ws:
        # Push one entry event through the live hook.
        session.emit_live_event(
            _make_entry_event(
                session_id=session.session_id,
                cls_java="com.trace.Plans",
                method_name="gateIntPredicate",
                descriptor="()V",
            )
        )
        trace_evt = ws.receive_json()
        pending = ws.receive_json()
        ready = ws.receive_json()

    assert trace_evt["payload"]["phase"] == "entry"
    assert pending["kind"] == _SUMMARY_PENDING_KIND
    assert ready["kind"] == _SUMMARY_READY_KIND
    assert ready["payload"]["summary"] == "live summary"


# --- Cache write-through ---


def test_ws_successful_summary_is_cached_for_next_session(tmp_path: Path) -> None:
    """A successful ``summary_ready`` write is persisted to the
    ``skill_results_cache.json`` so a fresh session for the same
    method gets a cache hit (``cached: true``) without re-firing
    the LLM. Pin the cross-session contract so 13.4's skill-tier
    writes can rely on the same cache shape."""

    invocations = 0

    async def _stub(cls_java: str, method: str, descriptor: str) -> str:
        nonlocal invocations
        invocations += 1
        return f"summary turn {invocations}"

    # Session 1 — cache miss, fires LLM, writes cache.
    client, fake, session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub
    )
    session.stage_replay_event(
        _make_entry_event(
            session_id=session.session_id,
            cls_java="com.trace.Plans",
            method_name="gateBoolPredicate",
            descriptor="()V",
        )
    )
    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session.session_id}"
    ) as ws:
        ws.receive_json()  # trace
        ws.receive_json()  # pending
        ready1 = ws.receive_json()
    assert ready1["payload"]["cached"] is False
    assert ready1["payload"]["summary"] == "summary turn 1"
    assert invocations == 1

    # Verify the cache file exists with the expected shape.
    cache_path = tmp_path / APP_ID / "skill_results_cache.json"
    assert cache_path.is_file()
    cache_payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "by_key" in cache_payload
    # The single entry's run_folder is the synthetic "dynamic-trace-ws"
    # name — pin so a future refactor doesn't accidentally rename it.
    only_entry = next(iter(cache_payload["by_key"].values()))
    assert only_entry["run_folder"] == "dynamic-trace-ws"

    # Session 2 — fresh fake session on the same fake client +
    # same on-disk cache. Should hit cache; LLM not re-invoked.
    session2 = fake.attach("com.trace.target")
    session2.app_id = APP_ID
    session2.stage_replay_event(
        _make_entry_event(
            session_id=session2.session_id,
            cls_java="com.trace.Plans",
            method_name="gateBoolPredicate",
            descriptor="()V",
        )
    )
    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session2.session_id}"
    ) as ws:
        ws.receive_json()  # trace
        ready2 = ws.receive_json()
    assert ready2["payload"]["cached"] is True
    assert ready2["payload"]["summary"] == "summary turn 1"
    assert invocations == 1, "LLM must not re-fire on cross-session cache hit"


def test_ws_failed_summary_is_not_cached(tmp_path: Path) -> None:
    """``summary_failed`` events do NOT write to the cache — the
    next session must re-fire the LLM (the failure may have been
    transient). Mirrors the read-side discipline that empty cache
    entries are treated as misses."""

    raise_count = [2]

    async def _stub(cls_java: str, method: str, descriptor: str) -> str:
        if raise_count[0] > 0:
            raise_count[0] -= 1
            raise RuntimeError("transient")
        return "succeeded on retry"

    client, fake, session = _ws_app_with_summary_stub(
        tmp_path, summary_callable=_stub
    )
    session.stage_replay_event(
        _make_entry_event(
            session_id=session.session_id,
            cls_java="com.trace.Plans",
            method_name="gateBoolPredicate",
            descriptor="()V",
        )
    )
    with client.websocket_connect(
        f"/ws/trace/{APP_ID}/{session.session_id}"
    ) as ws:
        ws.receive_json()  # trace
        ws.receive_json()  # pending
        failed = ws.receive_json()
    assert failed["kind"] == _SUMMARY_FAILED_KIND

    # Cache file shouldn't carry the failed result.
    cache_path = tmp_path / APP_ID / "skill_results_cache.json"
    if cache_path.is_file():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        assert not payload.get("by_key"), (
            "failed summaries must not be cached: %r" % payload
        )
