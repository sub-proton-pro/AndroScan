"""TestClient integration for :mod:`androscan.web.graph_routes`.

We build a real FastAPI app via :func:`create_app`, seed a fake app with
decompile status ``ready`` + a pre-extracted smali tree, and hit each
endpoint. That keeps the wiring honest — same helpers + dependency
injection as production, no fakes.

The fixture smali is reused from :mod:`test_call_graph_index` so we
don't pay the cost of curating a second corpus.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from androscan.analysis import call_graph
from androscan.config import Config
from androscan.web import decompile_cache as dc
from androscan.web.app import create_app


FIXTURES = Path(__file__).parent / "fixtures" / "call_graph_smali"


def _seed_app_with_graph(tmp_path: Path) -> tuple[Path, str]:
    """Build a fake app whose decompile cache is ``ready`` and whose
    call-graph SQLite is already populated from the fixture smali.

    Returns ``(apps_root, app_id)``.
    """
    apps_root = tmp_path / "apps"
    app_id = "myapp"
    app_dir = apps_root / app_id
    app_dir.mkdir(parents=True)
    sha = "f" * 40
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK")
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": sha, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )

    # Persist a decompile-cache index.json + empty sources/ so routes
    # see status=ready (get_status requires both).
    dc.sources_dir(app_dir, sha).mkdir(parents=True)
    dc._write_index(
        app_dir, sha,
        {"status": "ready", "apk_path": str(apk), "apk_sha256": sha, "file_count": 0},
    )

    # Drop the fixture smali into the per-sha cache so build_index can
    # skip apktool. We build synchronously so the TestClient doesn't
    # race against the background worker.
    cache = dc.cache_root_for(app_dir, sha)
    smali_out = cache / call_graph.APKTOOL_OUT_SUBDIR
    smali_out.mkdir(parents=True)
    shutil.copytree(FIXTURES / "smali", smali_out / "smali")
    shutil.copytree(FIXTURES / "smali_classes2", smali_out / "smali_classes2")
    st = call_graph.build_index(cache, apk_path=apk, sha=sha)
    assert st.status == "ready", st.error
    return apps_root, app_id


def _client(tmp_path: Path) -> tuple[TestClient, Path, str]:
    apps_root, app_id = _seed_app_with_graph(tmp_path)
    app = create_app(Config.default(), cwd=apps_root.parent)
    return TestClient(app), apps_root, app_id


# ---------------------------------------------------------------------------


def test_status_returns_ready_after_prebuild(tmp_path: Path) -> None:
    client, _root, app_id = _client(tmp_path)
    r = client.get(f"/api/graph/{app_id}/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == app_id
    cg = body["call_graph"]
    assert cg["status"] == "ready"
    assert cg["class_count"] == 7
    assert (cg["node_count"] or 0) > 0


def test_status_verbose_includes_meta(tmp_path: Path) -> None:
    client, _root, app_id = _client(tmp_path)
    r = client.get(f"/api/graph/{app_id}/status", params={"verbose": "true"})
    assert r.status_code == 200
    body = r.json()
    assert "call_graph_meta" in body
    assert body["call_graph_meta"].get("schema_version") == call_graph.SCHEMA_VERSION


def test_status_missing_when_decompile_not_ready(tmp_path: Path) -> None:
    """If the user hasn't decompiled yet, /status is ``missing`` rather than 404."""
    apps_root = tmp_path / "apps"
    (apps_root / "neverdecompiled").mkdir(parents=True)
    app = create_app(Config.default(), cwd=apps_root.parent)
    r = TestClient(app).get("/api/graph/neverdecompiled/status")
    assert r.status_code == 200
    body = r.json()
    assert body["call_graph"]["status"] == "missing"


def test_unknown_app_returns_404(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    app = create_app(Config.default(), cwd=tmp_path)
    r = TestClient(app).get("/api/graph/does-not-exist/status")
    assert r.status_code == 404


def test_list_route_default_excludes_external(tmp_path: Path) -> None:
    client, _root, app_id = _client(tmp_path)
    r = client.get(f"/api/graph/{app_id}", params={"limit": 100})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_nodes"] > 0
    assert all(n["is_external"] is False for n in body["nodes"])


def test_list_route_with_package_prefix(tmp_path: Path) -> None:
    client, _root, app_id = _client(tmp_path)
    r = client.get(
        f"/api/graph/{app_id}",
        params={"package_prefix": "com.example", "limit": 500},
    )
    assert r.status_code == 200
    body = r.json()
    names = {c["class_name"] for c in body["classes"]}
    assert "com.example.App" in names


def test_list_route_rejects_oversize_limit(tmp_path: Path) -> None:
    client, _root, app_id = _client(tmp_path)
    r = client.get(f"/api/graph/{app_id}", params={"limit": 9999})
    assert r.status_code == 422, "FastAPI Query(le=5000) should 422"


def test_neighbors_by_smali_id(tmp_path: Path) -> None:
    import urllib.parse
    client, _root, app_id = _client(tmp_path)
    sig = "Lcom/example/App;->main()V"
    encoded = urllib.parse.quote(sig, safe="")
    r = client.get(f"/api/graph/{app_id}/neighbors/{encoded}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["node"]["smali_id"] == sig
    callee_sigs = {c["node"]["smali_id"] for c in body["callees"]}
    # main calls Dog.<init> (direct) and Animal.speak (virtual_dispatch → Animal, Dog, Cat).
    assert "Lcom/example/Dog;->speak()V" in callee_sigs
    assert "Lcom/example/Dog;-><init>()V" in callee_sigs


def test_neighbors_unknown_node_is_404(tmp_path: Path) -> None:
    import urllib.parse
    client, _root, app_id = _client(tmp_path)
    bogus = urllib.parse.quote("Lcom/example/Nonexistent;->x()V", safe="")
    r = client.get(f"/api/graph/{app_id}/neighbors/{bogus}")
    assert r.status_code == 404


def test_paths_endpoint_returns_route(tmp_path: Path) -> None:
    import urllib.parse
    client, _root, app_id = _client(tmp_path)
    src = urllib.parse.quote("Lcom/example/App;->main()V", safe="")
    dst = urllib.parse.quote("Lcom/example/Cat;->speak()V", safe="")
    r = client.get(
        f"/api/graph/{app_id}/paths",
        params={"source": src, "target": dst, "max_hops": 3, "max_paths": 5},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paths"], "expected at least one path"


def test_paths_rejects_oversize_hops(tmp_path: Path) -> None:
    import urllib.parse
    client, _root, app_id = _client(tmp_path)
    src = urllib.parse.quote("Lcom/example/App;->main()V", safe="")
    dst = urllib.parse.quote("Lcom/example/Dog;->speak()V", safe="")
    r = client.get(
        f"/api/graph/{app_id}/paths",
        params={"source": src, "target": dst, "max_hops": 99},
    )
    assert r.status_code == 422


def test_rebuild_endpoint_kicks_worker(tmp_path: Path) -> None:
    client, _root, app_id = _client(tmp_path)
    r = client.post(f"/api/graph/{app_id}/rebuild")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == app_id
    assert body["sha"]
    # ``kicked`` is the start-status payload — either pending or ready.
    assert body["kicked"]["status"] in {"pending", "ready"}
    # Give the background thread a moment; then status should return ready.
    import time
    for _ in range(40):
        st = client.get(f"/api/graph/{app_id}/status").json()
        if st["call_graph"]["status"] == "ready":
            break
        time.sleep(0.05)
    assert client.get(f"/api/graph/{app_id}/status").json()["call_graph"]["status"] == "ready"


def test_rebuild_without_decompile_is_409(tmp_path: Path) -> None:
    apps_root = tmp_path / "apps"
    (apps_root / "nodec").mkdir(parents=True)
    client = TestClient(create_app(Config.default(), cwd=apps_root.parent))
    r = client.post("/api/graph/nodec/rebuild")
    assert r.status_code == 409


def test_per_app_status_includes_call_graph_card(tmp_path: Path) -> None:
    """Sanity check: the Settings tab's per-app status fan-out surfaces
    the new call-graph card next to the existing RAG card."""
    client, _root, app_id = _client(tmp_path)
    r = client.get(f"/api/status/apps/{app_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "call_graph" in body
    card = body["call_graph"]
    assert card["label"] == "Call graph"
    # Our fixture pre-builds the graph so status should be ready.
    assert card["status"] == "ready"
    assert card["ok"] is True
