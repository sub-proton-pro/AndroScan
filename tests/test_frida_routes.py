"""Integration tests for ``/api/frida/*`` and ``/ws/frida/*`` (sub-step 4.5).

Built on the same ``TestClient + stub-frida`` pattern as
``test_frida_client.py``: we monkeypatch the
:func:`androscan.adapters.frida_client._frida_python` seam so the
adapter believes a USB device is attached without ever talking to one.
The route layer's allowlist + render + WS-replay paths are exercised
end-to-end through HTTP + WS calls, with assertions on payload shape
and side-effects on disk.

Tests are grouped by route to keep failure messages localised; the
fixture creates one healthy app (``com.example.target`` + matching
``app_meta.json``) so the allowlist defaults work without per-test
plumbing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

import pytest
from fastapi.testclient import TestClient

from androscan.adapters import frida_client as fc
from androscan.config import Config
from androscan.web.app import create_app


# ---------------------------------------------------------------------------
# Stub frida — copied from test_frida_client.py minus the helpers we don't need
# here. The duplication is intentional: a shared conftest would couple two
# otherwise-independent test files.


class _StubScript:
    def __init__(self, source: str, name: str) -> None:
        self.source = source
        self.name = name
        self._handler: Optional[Callable[[dict[str, Any], Optional[bytes]], None]] = None
        self.loaded = False

    def on(self, event: str, cb: Callable[[dict[str, Any], Optional[bytes]], None]) -> None:
        assert event == "message"
        self._handler = cb

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        pass

    def emit(self, message: dict[str, Any]) -> None:
        assert self._handler is not None
        self._handler(message, None)


class _StubSession:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.scripts: list[_StubScript] = []

    def create_script(self, source: str, name: str = "anonymous") -> _StubScript:
        s = _StubScript(source, name)
        self.scripts.append(s)
        return s

    def detach(self) -> None:
        pass


class _StubDevice:
    def __init__(self) -> None:
        self._next_pid = 4321

    def attach(self, target: Any) -> _StubSession:
        return _StubSession(target if isinstance(target, int) else self._next_pid)

    def spawn(self, package: str) -> int:
        self._next_pid += 1
        return self._next_pid


class _StubFridaModule:
    def __init__(self) -> None:
        self.device = _StubDevice()

    def get_usb_device(self, timeout: float = 1.0) -> _StubDevice:
        return self.device


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def cfg() -> Config:
    return Config.default()


@pytest.fixture
def app_id() -> str:
    # Sanitised manifest package id (dots → underscores) per
    # ``app_id_from_dossier``; matches what the workbench actually
    # stores on disk.
    return "com_example_target"


@pytest.fixture
def app_package() -> str:
    return "com.example.target"


@pytest.fixture
def client(
    cfg: Config,
    tmp_path: Path,
    app_id: str,
    app_package: str,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Build a TestClient with a stub frida + one healthy app on disk.

    The stub frida module is installed via ``monkeypatch.setattr``
    *before* ``create_app`` runs, so the lazy ``_frida_provider``
    inside ``app.py`` resolves to it on the first route call. The
    fixture also drops a minimal ``app_meta.json`` so the route
    layer's prefix-default path (= the app's own package id) works
    without the operator first hitting Settings.
    """
    monkeypatch.chdir(tmp_path)
    apps_root = tmp_path / "apps"
    apps_root.mkdir()
    app_dir = apps_root / app_id
    app_dir.mkdir()
    (app_dir / "app_meta.json").write_text(
        json.dumps({
            "apk_sha256": "deadbeef" * 8,
            "dossier": {"apk_info": {"package": app_package, "version_name": "1.0"}},
        }),
        encoding="utf-8",
    )

    stub_mod = _StubFridaModule()
    monkeypatch.setattr(fc, "_frida_python", lambda: stub_mod)

    app = create_app(cfg, cwd=tmp_path)
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/frida/templates


def test_list_templates_returns_v1_set(client: TestClient) -> None:
    r = client.get("/api/frida/templates")
    assert r.status_code == 200
    body = r.json()
    ids = {t["id"] for t in body["templates"]}
    # v1 templates: 4.4 shipped 5, 4.6 added scope_inspector.
    expected = {
        "entry_exit_log",
        "scope_inspector",
        "ssl_pinning_bypass",
        "crypto",
        "shared_preferences",
        "intent",
    }
    assert expected <= ids
    # Wire shape never includes raw JS.
    for t in body["templates"]:
        assert "js_template" not in t
        assert "pentester_summary_template" not in t
        assert "params" in t and isinstance(t["params"], list)


def test_get_one_template_returns_full_param_schema(client: TestClient) -> None:
    r = client.get("/api/frida/templates/entry_exit_log")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "entry_exit_log"
    param_names = {p["name"] for p in body["params"]}
    assert {"class_name", "method_name", "event_label"} <= param_names


def test_get_unknown_template_404(client: TestClient) -> None:
    r = client.get("/api/frida/templates/does_not_exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/frida/render


def test_render_happy_path(client: TestClient) -> None:
    r = client.post(
        "/api/frida/render",
        json={
            "template_id": "entry_exit_log",
            "params": {
                "class_name": "com.example.MyClass",
                "method_name": "doThing",
                "event_label": "demo",
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rendered"]["template_id"] == "entry_exit_log"
    assert "com.example.MyClass" in body["rendered"]["js"]
    assert "doThing" in body["rendered"]["js"]
    assert body["parse"]["ok"] is True
    assert body["parse"]["error"] is None
    assert body["parse"]["available"] is True


def test_render_missing_required_param_400(client: TestClient) -> None:
    r = client.post(
        "/api/frida/render",
        json={"template_id": "entry_exit_log", "params": {}},
    )
    assert r.status_code == 400
    assert "class_name" in r.json()["detail"] or "method_name" in r.json()["detail"]


def test_render_unknown_param_400(client: TestClient) -> None:
    r = client.post(
        "/api/frida/render",
        json={
            "template_id": "ssl_pinning_bypass",
            "params": {"event_label": "x", "rogue_param": "y"},
        },
    )
    assert r.status_code == 400


def test_render_unknown_template_404(client: TestClient) -> None:
    r = client.post(
        "/api/frida/render",
        json={"template_id": "nope", "params": {}},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/frida/sessions create — allowlist + Frida attach


def _ok_session_body(app_id: str, app_package: str) -> dict[str, Any]:
    return {
        "app_id": app_id,
        "package": app_package,
        "template_id": "entry_exit_log",
        "params": {
            "class_name": "com.example.MyClass",
            "method_name": "doThing",
            "event_label": "demo",
        },
        "spawn": False,
        "persist": True,
    }


def test_create_session_happy_path(
    client: TestClient, app_id: str, app_package: str, tmp_path: Path,
) -> None:
    body = _ok_session_body(app_id, app_package)
    r = client.post("/api/frida/sessions", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["app_id"] == app_id
    assert payload["template_id"] == "entry_exit_log"
    assert payload["package"] == app_package
    assert payload["pid"] > 0
    assert payload["ws_url"].startswith(f"/ws/frida/sessions/{payload['session_id']}")
    assert payload["parse"]["ok"] is True
    # JSONL persistence path was allocated under apps/<app_id>/<run_ts>/frida.
    assert payload["persist_path"] is not None
    persist_path = Path(payload["persist_path"])
    # The path is always under apps/<app_id>/<run_ts>/frida/.
    assert persist_path.parent.name == "frida"
    assert persist_path.parent.parent.parent.name == app_id


def test_create_session_persist_false_skips_jsonl(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    body = _ok_session_body(app_id, app_package)
    body["persist"] = False
    r = client.post("/api/frida/sessions", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["persist_path"] is None


def test_create_session_allowlist_blocks_sibling_package(
    client: TestClient, app_id: str,
) -> None:
    body = _ok_session_body(app_id, "com.unrelated.evil")
    r = client.post("/api/frida/sessions", json=body)
    assert r.status_code == 403
    assert "hook_blocked" in r.json()["detail"]


def test_create_session_unknown_app_404(
    client: TestClient, app_package: str,
) -> None:
    body = _ok_session_body("missing_app_id", app_package)
    r = client.post("/api/frida/sessions", json=body)
    assert r.status_code == 404


def test_create_session_widened_prefix_via_settings(
    client: TestClient, app_id: str,
) -> None:
    """Operator widens ``hook_target_package_prefix`` to ``com.example`` —
    sibling packages under the same prefix are now allowed."""
    r = client.put(
        f"/api/settings/apps/{app_id}",
        json={"patch": {"hook": {"hook_target_package_prefix": "com.example"}}},
    )
    assert r.status_code == 200, r.text

    body = _ok_session_body(app_id, "com.example.sibling")
    r = client.post("/api/frida/sessions", json=body)
    assert r.status_code == 200, r.text


def test_create_session_unknown_template_404(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    body = _ok_session_body(app_id, app_package)
    body["template_id"] = "nope"
    r = client.post("/api/frida/sessions", json=body)
    assert r.status_code == 404


def test_create_session_param_error_400(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    body = _ok_session_body(app_id, app_package)
    body["params"] = {}  # missing required class_name + method_name
    r = client.post("/api/frida/sessions", json=body)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/frida/sessions list / get / delete


def test_list_and_delete_sessions(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    create_resp = client.post("/api/frida/sessions", json=_ok_session_body(app_id, app_package))
    assert create_resp.status_code == 200
    session_id = create_resp.json()["session_id"]

    list_resp = client.get("/api/frida/sessions")
    assert list_resp.status_code == 200
    sessions = list_resp.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == session_id
    assert sessions[0]["template_id"] == "entry_exit_log"
    assert sessions[0]["package"] == app_package

    get_resp = client.get(f"/api/frida/sessions/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["session_id"] == session_id

    del_resp = client.delete(f"/api/frida/sessions/{session_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True

    list_after = client.get("/api/frida/sessions")
    assert list_after.json()["sessions"] == []


def test_get_unknown_session_404(client: TestClient) -> None:
    r = client.get("/api/frida/sessions/does_not_exist")
    assert r.status_code == 404


def test_delete_unknown_session_404(client: TestClient) -> None:
    r = client.delete("/api/frida/sessions/does_not_exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/frida/sessions/{id}/events


def test_events_endpoint_returns_ring_snapshot(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    create_resp = client.post("/api/frida/sessions", json=_ok_session_body(app_id, app_package))
    session_id = create_resp.json()["session_id"]

    # Reach into the FridaClient to grab the session and emit events
    # via the stub script — same trick test_frida_client.py uses.
    app_client = client.app.state.frida_client  # type: ignore[attr-defined]
    session = app_client.get_session(session_id)
    assert session is not None
    script = session._scripts[0]  # noqa: SLF001 - test introspection
    script.emit({"type": "send", "payload": {"phase": "enter"}})
    script.emit({"type": "log", "level": "warning", "payload": "deprecated"})

    r = client.get(f"/api/frida/sessions/{session_id}/events")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == session_id
    kinds = [e["kind"] for e in body["events"]]
    assert kinds == ["send", "log"]
    assert body["events"][0]["payload"] == {"phase": "enter"}


def test_events_unknown_session_404(client: TestClient) -> None:
    r = client.get("/api/frida/sessions/nope/events")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/frida/sessions/{id}/export


def test_export_returns_jsonl_stream(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    create_resp = client.post("/api/frida/sessions", json=_ok_session_body(app_id, app_package))
    body = create_resp.json()
    session_id = body["session_id"]
    persist_path = Path(body["persist_path"])

    app_client = client.app.state.frida_client  # type: ignore[attr-defined]
    session = app_client.get_session(session_id)
    assert session is not None
    script = session._scripts[0]  # noqa: SLF001 - test introspection
    script.emit({"type": "send", "payload": "first"})
    script.emit({"type": "send", "payload": "second"})

    # Wait for the writer thread to flush (line-buffered, fast).
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if persist_path.is_file():
            text = persist_path.read_text(encoding="utf-8")
            if text.count("\n") >= 2:
                break
        time.sleep(0.02)

    r = client.get(f"/api/frida/sessions/{session_id}/export")
    assert r.status_code == 200
    assert "ndjson" in r.headers["content-type"]
    lines = [ln for ln in r.text.split("\n") if ln.strip()]
    assert len(lines) == 2
    rows = [json.loads(ln) for ln in lines]
    assert [r["payload"] for r in rows] == ["first", "second"]


def test_export_when_persist_disabled_404(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    body = _ok_session_body(app_id, app_package)
    body["persist"] = False
    create_resp = client.post("/api/frida/sessions", json=body)
    session_id = create_resp.json()["session_id"]
    r = client.get(f"/api/frida/sessions/{session_id}/export")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /ws/frida/sessions/{id}/trace


def test_ws_replay_then_stream(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    """The WS contract: on connect, replay the ring buffer once, then
    stream new events live. We populate the ring with two events
    *before* the WS connect so the catch-up phase is exercised."""
    create_resp = client.post("/api/frida/sessions", json=_ok_session_body(app_id, app_package))
    session_id = create_resp.json()["session_id"]

    app_client = client.app.state.frida_client  # type: ignore[attr-defined]
    session = app_client.get_session(session_id)
    assert session is not None
    script = session._scripts[0]  # noqa: SLF001 - test introspection
    # Pre-populate the ring before WS connect.
    script.emit({"type": "send", "payload": "pre-1"})
    script.emit({"type": "send", "payload": "pre-2"})

    with client.websocket_connect(f"/ws/frida/sessions/{session_id}/trace") as ws:
        # Replay phase (two events).
        ev1 = ws.receive_json()
        ev2 = ws.receive_json()
        assert ev1["payload"] == "pre-1"
        assert ev2["payload"] == "pre-2"
        # Live phase: emit one more after subscribing, expect to see it.
        script.emit({"type": "send", "payload": "live-1"})
        ev3 = ws.receive_json()
        assert ev3["payload"] == "live-1"


def test_ws_unknown_session_closes(client: TestClient) -> None:
    """Unknown ``session_id`` should send a structured error and close."""
    with client.websocket_connect("/ws/frida/sessions/nope/trace") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["error"] == "unknown_session"


# ---------------------------------------------------------------------------
# /api/frida/sessions/{id}/hooks  +  /scope  (sub-step 4.6 introspection)
#
# These are pure aggregations over the ring buffer; we drive the ring
# the same way the ``/events`` test does (``script.emit({...})`` runs
# ``_on_message`` synchronously) and assert on the response shape.


def _emit_entry(script: Any, *, class_name: str, method: str, args: list[str], label: str = "scope-1") -> None:
    """Helper: emit a `phase=entry` event without ``this_fields`` —
    matches what ``entry_exit_log`` produces."""
    script.emit({
        "type": "send",
        "payload": {
            "label": label,
            "phase": "entry",
            "class": class_name,
            "method": method,
            "args": args,
        },
    })


def _emit_exit(script: Any, *, class_name: str, method: str, return_value: str, label: str = "scope-1") -> None:
    script.emit({
        "type": "send",
        "payload": {
            "label": label,
            "phase": "exit",
            "class": class_name,
            "method": method,
            "return": return_value,
        },
    })


def _emit_scope_entry(
    script: Any, *, class_name: str, method: str, args: list[str], fields: dict[str, str], this_class: str | None = None,
) -> None:
    """Helper: emit a `phase=entry` event WITH ``this_fields`` —
    matches what ``scope_inspector`` produces."""
    script.emit({
        "type": "send",
        "payload": {
            "label": "scope-1",
            "phase": "entry",
            "class": class_name,
            "method": method,
            "args": args,
            "this_class": this_class or class_name,
            "this_fields": fields,
        },
    })


def _emit_scope_exit(
    script: Any, *, class_name: str, method: str, return_value: str, fields: dict[str, str],
) -> None:
    script.emit({
        "type": "send",
        "payload": {
            "label": "scope-1",
            "phase": "exit",
            "class": class_name,
            "method": method,
            "return": return_value,
            "this_fields": fields,
        },
    })


def test_hooks_endpoint_empty_session(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    create_resp = client.post("/api/frida/sessions", json=_ok_session_body(app_id, app_package))
    session_id = create_resp.json()["session_id"]
    r = client.get(f"/api/frida/sessions/{session_id}/hooks")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == session_id
    assert body["hooks"] == []


def test_hooks_endpoint_aggregates_by_class_method(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    """Three entries + two exits for one (class, method); one entry for
    a second (class, method); exit/return tally is grouped + sorted by
    count desc."""
    create_resp = client.post("/api/frida/sessions", json=_ok_session_body(app_id, app_package))
    session_id = create_resp.json()["session_id"]
    app_client = client.app.state.frida_client  # type: ignore[attr-defined]
    session = app_client.get_session(session_id)
    assert session is not None
    script = session._scripts[0]  # noqa: SLF001 - test introspection

    # Class A.foo: 3 entries, 2 exits returning "ok" and one "fail".
    _emit_entry(script, class_name="com.example.A", method="foo", args=["1"])
    _emit_exit(script, class_name="com.example.A", method="foo", return_value="ok")
    _emit_entry(script, class_name="com.example.A", method="foo", args=["2"])
    _emit_exit(script, class_name="com.example.A", method="foo", return_value="ok")
    _emit_entry(script, class_name="com.example.A", method="foo", args=["3"])
    _emit_exit(script, class_name="com.example.A", method="foo", return_value="fail")
    # Class B.bar: 1 entry, 0 exits.
    _emit_entry(script, class_name="com.example.B", method="bar", args=[])

    r = client.get(f"/api/frida/sessions/{session_id}/hooks")
    assert r.status_code == 200
    hooks = r.json()["hooks"]
    assert len(hooks) == 2
    by_key = {(h["class"], h["method"]): h for h in hooks}

    a = by_key[("com.example.A", "foo")]
    assert a["hits"] == 3
    assert a["template_id"] == "entry_exit_log"  # session's template
    # Top return values, sorted by count desc, with stable insertion-
    # order tiebreak.
    returns = [(r["value"], r["count"]) for r in a["top_returns"]]
    assert returns == [("ok", 2), ("fail", 1)]
    assert isinstance(a["last_seen_ts"], (int, float))

    b = by_key[("com.example.B", "bar")]
    assert b["hits"] == 1
    assert b["top_returns"] == []
    # Sort order: A.foo (3 hits) before B.bar (1 hit).
    assert hooks[0]["class"] == "com.example.A"


def test_hooks_endpoint_ignores_malformed_payloads(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    """Non-dict payloads, missing class/method, log/error events all
    pass through without crashing the aggregator."""
    create_resp = client.post("/api/frida/sessions", json=_ok_session_body(app_id, app_package))
    session_id = create_resp.json()["session_id"]
    app_client = client.app.state.frida_client  # type: ignore[attr-defined]
    session = app_client.get_session(session_id)
    script = session._scripts[0]  # noqa: SLF001

    script.emit({"type": "send", "payload": "scalar-not-a-dict"})
    script.emit({"type": "send", "payload": {"phase": "entry"}})  # missing class/method
    script.emit({"type": "log", "level": "info", "payload": "log line"})
    script.emit({"type": "error", "description": "boom"})
    # Plus one well-formed event so we have something to assert against.
    _emit_entry(script, class_name="com.example.C", method="ok", args=[])

    r = client.get(f"/api/frida/sessions/{session_id}/hooks")
    assert r.status_code == 200
    hooks = r.json()["hooks"]
    assert len(hooks) == 1
    assert hooks[0]["class"] == "com.example.C"
    assert hooks[0]["hits"] == 1


def test_hooks_endpoint_unknown_session_404(client: TestClient) -> None:
    r = client.get("/api/frida/sessions/does_not_exist/hooks")
    assert r.status_code == 404


def test_scope_endpoint_empty_session(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    create_resp = client.post("/api/frida/sessions", json=_ok_session_body(app_id, app_package))
    session_id = create_resp.json()["session_id"]
    r = client.get(f"/api/frida/sessions/{session_id}/scope")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == session_id
    assert body["snapshots"] == []


def test_scope_endpoint_filters_events_without_this_fields(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    """A session running an `entry_exit_log` hook (no this_fields)
    should produce an empty scope payload — the panel can't claim it
    has data when the trace doesn't carry the discriminator."""
    create_resp = client.post("/api/frida/sessions", json=_ok_session_body(app_id, app_package))
    session_id = create_resp.json()["session_id"]
    app_client = client.app.state.frida_client  # type: ignore[attr-defined]
    session = app_client.get_session(session_id)
    script = session._scripts[0]  # noqa: SLF001

    _emit_entry(script, class_name="com.example.A", method="foo", args=["1"])
    _emit_exit(script, class_name="com.example.A", method="foo", return_value="ok")

    r = client.get(f"/api/frida/sessions/{session_id}/scope")
    assert r.status_code == 200
    assert r.json()["snapshots"] == []


def test_scope_endpoint_keeps_latest_entry_and_exit_per_method(
    client: TestClient, app_id: str, app_package: str,
) -> None:
    create_resp = client.post("/api/frida/sessions", json=_ok_session_body(app_id, app_package))
    session_id = create_resp.json()["session_id"]
    app_client = client.app.state.frida_client  # type: ignore[attr-defined]
    session = app_client.get_session(session_id)
    script = session._scripts[0]  # noqa: SLF001

    # Two entry/exit pairs for the same method — only the latest pair
    # should land in the snapshot.
    _emit_scope_entry(
        script, class_name="com.example.S", method="step",
        args=["v1"], fields={"counter": "0"}, this_class="com.example.S$Sub",
    )
    _emit_scope_exit(
        script, class_name="com.example.S", method="step",
        return_value="r1", fields={"counter": "1"},
    )
    _emit_scope_entry(
        script, class_name="com.example.S", method="step",
        args=["v2"], fields={"counter": "1"},
    )
    _emit_scope_exit(
        script, class_name="com.example.S", method="step",
        return_value="r2", fields={"counter": "2"},
    )

    r = client.get(f"/api/frida/sessions/{session_id}/scope")
    assert r.status_code == 200
    snaps = r.json()["snapshots"]
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap["class"] == "com.example.S"
    assert snap["method"] == "step"
    # Last entry: args=v2, this_fields.counter="1" (pre-call).
    assert snap["last_entry"]["args"] == ["v2"]
    assert snap["last_entry"]["this_fields"] == {"counter": "1"}
    # Last exit: return=r2, this_fields.counter="2" (post-call).
    assert snap["last_exit"]["return"] == "r2"
    assert snap["last_exit"]["this_fields"] == {"counter": "2"}


def test_scope_endpoint_unknown_session_404(client: TestClient) -> None:
    r = client.get("/api/frida/sessions/does_not_exist/scope")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Frida unavailable surfaces 503 (templates + render still work)


def test_session_create_returns_503_when_frida_unavailable(
    cfg: Config, tmp_path: Path, app_id: str, app_package: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the [frida] extra is missing, ``POST /sessions`` returns
    503 — but ``/templates`` and ``/render`` still work."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps" / app_id).mkdir(parents=True)
    (tmp_path / "apps" / app_id / "app_meta.json").write_text(
        json.dumps({
            "apk_sha256": "deadbeef" * 8,
            "dossier": {"apk_info": {"package": app_package}},
        }),
        encoding="utf-8",
    )

    def _missing() -> object:
        raise ImportError("No module named 'frida'")

    monkeypatch.setattr(fc, "_frida_python", _missing)

    app = create_app(cfg, cwd=tmp_path)
    test_client = TestClient(app)

    # /templates and /render still succeed (pure Python).
    assert test_client.get("/api/frida/templates").status_code == 200
    r = test_client.post(
        "/api/frida/render",
        json={
            "template_id": "entry_exit_log",
            "params": {
                "class_name": "C", "method_name": "m", "event_label": "x",
            },
        },
    )
    assert r.status_code == 200

    # /sessions returns 503.
    r = test_client.post("/api/frida/sessions", json=_ok_session_body(app_id, app_package))
    assert r.status_code == 503
    assert "frida_unavailable" in r.json()["detail"]
