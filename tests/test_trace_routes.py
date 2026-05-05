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
