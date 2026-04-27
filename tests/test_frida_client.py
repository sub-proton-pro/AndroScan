"""Unit tests for the Hook Lab Frida adapter (Phase 6 step 4 / DEC-023).

These tests cover :class:`FridaClient` and :class:`FridaSession` end-to-end
*without installing the ``[frida]`` extra* by monkeypatching the
:func:`androscan.adapters.frida_client._frida_python` seam to inject a
stub ``frida`` module. This mirrors the test pattern :mod:`androscan.rag`
uses for its optional ``fastembed`` dependency.

The stub matches just enough of the ``frida`` Python surface that the
adapter exercises: ``get_usb_device``, ``Device.attach``,
``Session.create_script``, ``Script.on / load / unload``, and
``Session.detach``. Real device-touching tests are reserved for the
``device``-marked suite (4.4+).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from androscan.adapters import frida_client as fc


# ---------------------------------------------------------------------------
# Stub ``frida`` module
# ---------------------------------------------------------------------------


class _StubScript:
    def __init__(self, source: str, name: str) -> None:
        self.source = source
        self.name = name
        self._handler: Optional[Callable[[dict[str, Any], Optional[bytes]], None]] = None
        self.loaded = False
        self.unloaded = False

    def on(self, event: str, cb: Callable[[dict[str, Any], Optional[bytes]], None]) -> None:
        assert event == "message", event
        self._handler = cb

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.unloaded = True

    def emit(self, message: dict[str, Any], data: Optional[bytes] = None) -> None:
        """Helper used by tests to simulate Frida's message thread."""
        assert self._handler is not None, "load() not called yet"
        self._handler(message, data)


class _StubSession:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.scripts: list[_StubScript] = []
        self.detached = False

    def create_script(self, source: str, name: str = "anonymous") -> _StubScript:
        s = _StubScript(source, name)
        self.scripts.append(s)
        return s

    def detach(self) -> None:
        self.detached = True


class _StubDevice:
    def __init__(self) -> None:
        self.attach_calls: list[Any] = []
        self.spawn_calls: list[str] = []
        self._next_pid = 1234

    def attach(self, target: Any) -> _StubSession:
        self.attach_calls.append(target)
        pid = target if isinstance(target, int) else self._next_pid
        return _StubSession(pid)

    def spawn(self, package: str) -> int:
        self.spawn_calls.append(package)
        self._next_pid += 1
        return self._next_pid


class _StubFridaModule:
    def __init__(self, *, raise_on_get_usb: Optional[Exception] = None) -> None:
        self._raise = raise_on_get_usb
        self.device = _StubDevice()
        self.get_usb_device_calls = 0

    def get_usb_device(self, timeout: float = 1.0) -> _StubDevice:
        self.get_usb_device_calls += 1
        if self._raise is not None:
            raise self._raise
        return self.device


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_frida(monkeypatch: pytest.MonkeyPatch) -> _StubFridaModule:
    mod = _StubFridaModule()
    monkeypatch.setattr(fc, "_frida_python", lambda: mod)
    return mod


# ---------------------------------------------------------------------------
# is_available + lazy import
# ---------------------------------------------------------------------------


def test_is_available_true_when_frida_imports_and_device_resolves(
    stub_frida: _StubFridaModule,
) -> None:
    client = fc.FridaClient(ring_size=200)
    assert client.is_available() is True
    # Idempotent: second call doesn't re-import.
    assert client.is_available() is True
    assert stub_frida.get_usb_device_calls == 1


def test_is_available_false_when_frida_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> Any:
        raise ImportError("No module named 'frida'")

    monkeypatch.setattr(fc, "_frida_python", _raise)
    client = fc.FridaClient(ring_size=200)
    assert client.is_available() is False
    # ``attach`` still raises FridaUnavailableError so callers get the install hint.
    with pytest.raises(fc.FridaUnavailableError) as excinfo:
        client.attach("com.example")
    assert "pip install" in str(excinfo.value)


def test_is_available_false_when_no_usb_device(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _StubFridaModule(raise_on_get_usb=Exception("no USB device"))
    monkeypatch.setattr(fc, "_frida_python", lambda: mod)
    client = fc.FridaClient(ring_size=200)
    assert client.is_available() is False


def test_constructor_rejects_too_small_ring_size() -> None:
    with pytest.raises(ValueError) as excinfo:
        fc.FridaClient(ring_size=1)
    assert "ring_size" in str(excinfo.value)


# ---------------------------------------------------------------------------
# attach / detach lifecycle
# ---------------------------------------------------------------------------


def test_attach_returns_session_with_pid_and_package(stub_frida: _StubFridaModule) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example.target")
    assert session.package == "com.example.target"
    assert session.pid == 1234
    assert session.session_id  # opaque, but non-empty
    assert client.list_sessions() == [session]
    assert client.get_session(session.session_id) is session
    assert stub_frida.device.attach_calls == ["com.example.target"]


def test_attach_with_spawn_uses_pid_target(stub_frida: _StubFridaModule) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example.target", spawn=True)
    assert stub_frida.device.spawn_calls == ["com.example.target"]
    # spawn() bumped to 1235; attach was called with that pid.
    assert stub_frida.device.attach_calls == [1235]
    assert session.pid == 1235


def test_attach_rejects_blank_package(stub_frida: _StubFridaModule) -> None:
    client = fc.FridaClient(ring_size=200)
    with pytest.raises(ValueError):
        client.attach("")
    with pytest.raises(ValueError):
        client.attach("   ")


def test_detach_removes_session_and_unloads_scripts(
    stub_frida: _StubFridaModule,
) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    script = session.load_script("rpc.exports = {};", name="probe")
    assert client.list_sessions() == [session]

    session.detach()
    assert script.unloaded is True
    assert client.list_sessions() == []
    # Idempotent.
    session.detach()


def test_detach_all_is_idempotent(stub_frida: _StubFridaModule) -> None:
    client = fc.FridaClient(ring_size=200)
    s1 = client.attach("com.example.one")
    s2 = client.attach("com.example.two")
    assert len(client.list_sessions()) == 2

    client.detach_all()
    assert client.list_sessions() == []
    # Underlying frida sessions got detached.
    assert s1._frida_session.detached is True  # type: ignore[attr-defined]
    assert s2._frida_session.detached is True  # type: ignore[attr-defined]
    # No-op second call.
    client.detach_all()


# ---------------------------------------------------------------------------
# load_script + on_message → ring buffer
# ---------------------------------------------------------------------------


def test_load_script_rejects_blank_source(stub_frida: _StubFridaModule) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    with pytest.raises(ValueError):
        session.load_script("")
    with pytest.raises(ValueError):
        session.load_script("   ")


def test_send_message_lands_in_ring_buffer(stub_frida: _StubFridaModule) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    script = session.load_script("rpc.exports = {};", name="probe")

    script.emit({"type": "send", "payload": {"hello": "world"}})
    script.emit({"type": "log", "level": "warning", "payload": "deprecated"})
    script.emit({"type": "error", "description": "boom", "stack": "...", "lineNumber": 7})

    events = session.events()
    assert len(events) == 3
    assert [e.kind for e in events] == ["send", "log", "error"]
    assert events[0].payload == {"hello": "world"}
    assert events[1].payload == {"level": "warning", "payload": "deprecated"}
    assert events[2].payload["description"] == "boom"
    assert events[2].payload["lineNumber"] == 7

    stats = session.stats()
    assert stats["total_events"] == 3
    assert stats["dropped"] == 0
    assert stats["buffered"] == 3
    assert stats["by_kind"] == {"send": 1, "log": 1, "error": 1}
    assert stats["last_ts"] is not None


def test_unknown_message_type_is_classified_as_send(
    stub_frida: _StubFridaModule,
) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    script = session.load_script("rpc.exports = {};")
    script.emit({"type": "weird", "blob": [1, 2, 3]})

    events = session.events()
    assert len(events) == 1
    assert events[0].kind == "send"
    # The whole message survives untouched in ``raw``.
    assert events[0].raw == {"type": "weird", "blob": [1, 2, 3]}


def test_ring_buffer_evicts_oldest_and_tracks_dropped(
    stub_frida: _StubFridaModule,
) -> None:
    client = fc.FridaClient(ring_size=fc.MIN_RING_SIZE)  # 100
    session = client.attach("com.example")
    script = session.load_script("rpc.exports = {};")

    for i in range(fc.MIN_RING_SIZE + 25):
        script.emit({"type": "send", "payload": i})

    events = session.events()
    assert len(events) == fc.MIN_RING_SIZE
    # Oldest 25 were evicted; first surviving payload is i=25.
    assert events[0].payload == 25
    assert events[-1].payload == fc.MIN_RING_SIZE + 24

    stats = session.stats()
    assert stats["total_events"] == fc.MIN_RING_SIZE + 25
    assert stats["dropped"] == 25
    assert stats["buffered"] == fc.MIN_RING_SIZE


def test_events_limit_returns_tail(stub_frida: _StubFridaModule) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    script = session.load_script("rpc.exports = {};")
    for i in range(10):
        script.emit({"type": "send", "payload": i})

    tail = session.events(limit=3)
    assert [e.payload for e in tail] == [7, 8, 9]


def test_on_event_hook_fires_after_ring_append(stub_frida: _StubFridaModule) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    script = session.load_script("rpc.exports = {};")

    seen: list[fc.TraceEvent] = []
    def hook(ev: fc.TraceEvent) -> None:
        seen.append(ev)
        assert len(session.events()) >= 1

    session.on_event = hook
    script.emit({"type": "send", "payload": "ping"})
    assert len(seen) == 1
    assert seen[0].payload == "ping"


def test_on_event_hook_exceptions_do_not_break_delivery(
    stub_frida: _StubFridaModule,
) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    script = session.load_script("rpc.exports = {};")

    def bad_hook(_ev: fc.TraceEvent) -> None:
        raise RuntimeError("hook blew up")

    session.on_event = bad_hook
    script.emit({"type": "send", "payload": "still-buffered"})
    # The event still landed in the ring even though the hook raised.
    assert len(session.events()) == 1


def test_load_script_after_detach_raises(stub_frida: _StubFridaModule) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    session.detach()
    with pytest.raises(RuntimeError):
        session.load_script("rpc.exports = {};")


# ---------------------------------------------------------------------------
# Concurrency: producer thread vs reader thread
# ---------------------------------------------------------------------------


def test_concurrent_producer_and_reader(stub_frida: _StubFridaModule) -> None:
    """Smoke test: dozens of producers can hammer the ring while a reader
    snapshots ``stats()``/``events()`` without raising. We don't assert
    deterministic ordering; we only guarantee the buffer never tears."""
    client = fc.FridaClient(ring_size=500)
    session = client.attach("com.example")
    script = session.load_script("rpc.exports = {};")

    stop = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            while not stop.is_set():
                events = session.events()
                # Non-trivial structural assert: stats() agrees with the
                # snapshot length up to the moment we sample (snapshot is
                # always <= total_events).
                stats = session.stats()
                assert stats["buffered"] <= stats["total_events"]
                assert len(events) <= stats["ring_capacity"]
        except BaseException as e:  # pragma: no cover - test diagnostic
            errors.append(e)

    def producer(seed: int) -> None:
        try:
            for i in range(200):
                script.emit({"type": "send", "payload": (seed, i)})
        except BaseException as e:  # pragma: no cover - test diagnostic
            errors.append(e)

    r = threading.Thread(target=reader, daemon=True)
    r.start()
    producers = [threading.Thread(target=producer, args=(s,), daemon=True) for s in range(8)]
    for p in producers:
        p.start()
    for p in producers:
        p.join(timeout=10)
    stop.set()
    r.join(timeout=2)

    assert not errors, errors
    stats = session.stats()
    assert stats["total_events"] == 8 * 200


# ---------------------------------------------------------------------------
# get_frida_client cache on app.state
# ---------------------------------------------------------------------------


class _FakeAppState:
    pass


class _FakeApp:
    def __init__(self) -> None:
        self.state = _FakeAppState()


class _FakeConfig:
    frida_trace_ring_buffer_size = 333


def test_get_frida_client_caches_on_app_state(
    stub_frida: _StubFridaModule,
) -> None:
    app = _FakeApp()
    cfg = _FakeConfig()
    c1 = fc.get_frida_client(app, cfg)
    c2 = fc.get_frida_client(app, cfg)
    assert c1 is c2
    assert c1._ring_size == 333  # type: ignore[attr-defined]


def test_get_frida_client_clamps_below_minimum(stub_frida: _StubFridaModule) -> None:
    class _BadConfig:
        frida_trace_ring_buffer_size = 1

    app = _FakeApp()
    client = fc.get_frida_client(app, _BadConfig())
    assert client._ring_size >= fc.MIN_RING_SIZE  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# JSONL persistence (sub-step 4.5)
#
# These tests cover ``FridaSession.set_persistence_path`` and the writer
# thread that drains the queue to disk. We rely on ``thread.join`` inside
# ``detach`` to flush the writer synchronously before the assertions read
# the file — racy reads would surface as flaky tests, so a small helper
# below polls until the writer has produced ``n`` lines or times out.


def _wait_for_lines(path: Path, expected: int, timeout: float = 2.0) -> list[dict]:
    """Poll ``path`` until it has ``expected`` JSON lines (or raise).

    Used by tests to bridge the sync producer (``script.emit``) and the
    async writer thread without relying on hard sleeps. The writer is
    line-buffered so each ``write + \\n`` lands atomically.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            lines = [ln for ln in text.split("\n") if ln.strip()]
            if len(lines) >= expected:
                return [json.loads(ln) for ln in lines]
        time.sleep(0.02)
    raise AssertionError(
        f"persistence file {path} did not reach {expected} lines in {timeout}s"
    )


def test_persistence_writes_each_event_as_jsonl(
    stub_frida: _StubFridaModule, tmp_path: Path,
) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    persist = tmp_path / "trace.jsonl"
    session.set_persistence_path(persist)
    script = session.load_script("rpc.exports = {};")

    script.emit({"type": "send", "payload": {"hello": "world"}})
    script.emit({"type": "log", "level": "warning", "payload": "deprecated"})

    rows = _wait_for_lines(persist, 2)
    assert [r["kind"] for r in rows] == ["send", "log"]
    assert rows[0]["payload"] == {"hello": "world"}
    assert rows[0]["session_id"] == session.session_id
    # ``raw`` carries the unmodified Frida message dict.
    assert rows[0]["raw"]["type"] == "send"

    stats = session.stats()
    assert stats["persist_path"] == str(persist)
    assert stats["persist_dropped"] == 0


def test_persistence_creates_parent_directory(
    stub_frida: _StubFridaModule, tmp_path: Path,
) -> None:
    """A fresh ``apps/<id>/<run_ts>/frida/`` tree shouldn't need pre-creating."""
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    nested = tmp_path / "apps" / "abc" / "run_ts" / "frida" / "trace.jsonl"
    session.set_persistence_path(nested)
    script = session.load_script("rpc.exports = {};")

    script.emit({"type": "send", "payload": "ok"})
    rows = _wait_for_lines(nested, 1)
    assert rows[0]["payload"] == "ok"


def test_persistence_called_twice_raises(
    stub_frida: _StubFridaModule, tmp_path: Path,
) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    session.set_persistence_path(tmp_path / "trace.jsonl")
    with pytest.raises(RuntimeError):
        session.set_persistence_path(tmp_path / "other.jsonl")


def test_persistence_after_detach_raises(
    stub_frida: _StubFridaModule, tmp_path: Path,
) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    session.detach()
    with pytest.raises(RuntimeError):
        session.set_persistence_path(tmp_path / "trace.jsonl")


def test_persistence_drop_counter_on_unserializable_payload(
    stub_frida: _StubFridaModule, tmp_path: Path,
) -> None:
    """A non-serializable payload must NOT kill the writer.

    The fallback ``repr()`` should rescue this case; the explicit drop
    counter is exercised by the unwritable-path test below. We still
    assert the writer emits *something* so the rest of the trace
    survives a single bad event.
    """
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    persist = tmp_path / "trace.jsonl"
    session.set_persistence_path(persist)
    script = session.load_script("rpc.exports = {};")

    class _Weird:
        def __repr__(self) -> str:
            return "<weird>"

    # ``send`` payload that's not JSON-serializable.
    script.emit({"type": "send", "payload": _Weird()})
    script.emit({"type": "send", "payload": "after-weird"})

    rows = _wait_for_lines(persist, 2)
    assert rows[1]["payload"] == "after-weird"


def test_persistence_unwritable_path_increments_drop_counter(
    stub_frida: _StubFridaModule, tmp_path: Path,
) -> None:
    """An unwritable persistence target must not block the producer.

    We point the persistence path at a file that *is* a directory — open
    will raise ``IsADirectoryError`` — so the writer drains the queue
    into the drop counter and the ring keeps streaming live events.
    """
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    session.set_persistence_path(bad_dir)
    script = session.load_script("rpc.exports = {};")

    script.emit({"type": "send", "payload": "a"})
    script.emit({"type": "send", "payload": "b"})

    # Wait for the writer to consume both queue entries (it drains and
    # drops since it never managed to open the file).
    deadline = time.time() + 2.0
    while time.time() < deadline:
        stats = session.stats()
        if stats["persist_dropped"] >= 2:
            break
        time.sleep(0.02)

    stats = session.stats()
    assert stats["persist_dropped"] >= 2, stats
    # Ring buffer was unaffected.
    assert len(session.events()) == 2


def test_detach_flushes_persistence_writer(
    stub_frida: _StubFridaModule, tmp_path: Path,
) -> None:
    """``detach`` must wait for the writer thread to finish before
    returning, so the post-detach file read sees every event."""
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    persist = tmp_path / "trace.jsonl"
    session.set_persistence_path(persist)
    script = session.load_script("rpc.exports = {};")

    for i in range(20):
        script.emit({"type": "send", "payload": i})
    session.detach()

    text = persist.read_text(encoding="utf-8")
    rows = [json.loads(ln) for ln in text.split("\n") if ln.strip()]
    assert len(rows) == 20
    assert [r["payload"] for r in rows] == list(range(20))


def test_no_persistence_path_skips_writer_thread(
    stub_frida: _StubFridaModule,
) -> None:
    """Default sessions must NOT spin up a writer thread — verified by
    checking ``stats['persist_path']`` is None and the file system is
    untouched."""
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    script = session.load_script("rpc.exports = {};")
    script.emit({"type": "send", "payload": 1})

    stats = session.stats()
    assert stats["persist_path"] is None
    assert stats["persist_dropped"] == 0
    # ``_persist_thread`` stays None — no thread leak.
    assert session._persist_thread is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Hook-Lab metadata + started_at (sub-step 4.5 prep for the routes layer)


def test_session_app_id_and_template_id_default_none(
    stub_frida: _StubFridaModule,
) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    assert session.app_id is None
    assert session.template_id is None
    # ``started_at`` is set by ``__init__``; should be a recent epoch.
    assert session.started_at <= time.time()
    assert session.started_at >= time.time() - 5.0


def test_session_metadata_round_trips_through_stats(
    stub_frida: _StubFridaModule,
) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example")
    session.app_id = "com_example"
    session.template_id = "ssl_pinning_bypass"
    stats = session.stats()
    assert stats["app_id"] == "com_example"
    assert stats["template_id"] == "ssl_pinning_bypass"
    assert stats["started_at"] == session.started_at
