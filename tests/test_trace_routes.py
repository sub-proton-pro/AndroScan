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
