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


class _StubApplication:
    """Mirrors ``frida.core.Application`` (subset the adapter reads)."""

    def __init__(self, identifier: Any, name: Any, pid: int) -> None:
        self.identifier = identifier
        self.name = name
        self.pid = pid


class _StubProcess:
    """Mirrors ``frida.core.Process`` (subset the adapter reads)."""

    def __init__(self, name: Any, pid: int) -> None:
        self.name = name
        self.pid = pid


class _MatchAnyStr(str):
    """Sentinel string that compares equal to any other string.

    Lets the stub device synthesize a wildcard ``Application`` for
    every package the client attaches to, so the bulk of the test
    suite (which doesn't care about resolution semantics, only that
    attach succeeds) doesn't have to pre-populate per-test ``apps``
    lists. Tests that exercise the resolution logic itself (running
    vs not-running, identifier vs process-name fallback) override
    ``apps_override`` / ``processes_override`` explicitly.
    """

    def __new__(cls) -> "_MatchAnyStr":
        return super().__new__(cls, "*any*")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, str)

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash("*any*")


# Default PID returned by the stub's wildcard Application — matches
# the historical ``_next_pid`` seed so existing assertions stay valid.
_DEFAULT_RUNNING_PID = 1234


class _StubDevice:
    def __init__(self) -> None:
        self.attach_calls: list[Any] = []
        self.spawn_calls: list[str] = []
        self.enumerate_apps_calls = 0
        self.enumerate_procs_calls = 0
        self._next_pid = _DEFAULT_RUNNING_PID
        # When ``None``, ``enumerate_applications`` returns a single
        # wildcard entry that resolves any package id to
        # ``_DEFAULT_RUNNING_PID``. Tests can set this to an explicit
        # list to simulate a particular device snapshot — including
        # ``[]`` for "app not running on the device".
        self.apps_override: Optional[list[_StubApplication]] = None
        self.processes_override: Optional[list[_StubProcess]] = None

    def enumerate_applications(self) -> list[_StubApplication]:
        self.enumerate_apps_calls += 1
        if self.apps_override is not None:
            return list(self.apps_override)
        return [
            _StubApplication(
                identifier=_MatchAnyStr(),
                name=_MatchAnyStr(),
                pid=_DEFAULT_RUNNING_PID,
            )
        ]

    def enumerate_processes(self) -> list[_StubProcess]:
        self.enumerate_procs_calls += 1
        if self.processes_override is not None:
            return list(self.processes_override)
        return []

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
    assert session.pid == _DEFAULT_RUNNING_PID
    assert session.session_id  # opaque, but non-empty
    assert client.list_sessions() == [session]
    assert client.get_session(session.session_id) is session
    # ATTACH-BY-PID: the client now resolves the package id to a
    # running PID via ``enumerate_applications`` and attaches by
    # integer. See FridaClient.attach docstring for why (Android
    # string-attach matches Process.name, not Application.identifier).
    assert stub_frida.device.attach_calls == [_DEFAULT_RUNNING_PID]
    assert stub_frida.device.enumerate_apps_calls == 1


def test_attach_with_spawn_uses_pid_target(stub_frida: _StubFridaModule) -> None:
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example.target", spawn=True)
    assert stub_frida.device.spawn_calls == ["com.example.target"]
    # spawn() bumped to 1235; attach was called with that pid.
    assert stub_frida.device.attach_calls == [1235]
    assert session.pid == 1235
    # Spawn skips the running-PID lookup — by definition a freshly
    # spawned process isn't in enumerate_applications yet.
    assert stub_frida.device.enumerate_apps_calls == 0


def test_attach_rejects_blank_package(stub_frida: _StubFridaModule) -> None:
    client = fc.FridaClient(ring_size=200)
    with pytest.raises(ValueError):
        client.attach("")
    with pytest.raises(ValueError):
        client.attach("   ")


# ---- Resolution paths exercised by the Hook Lab Inject button ------
#
# These reproduce the exact scenario reported on Apr 29: the WeakBank
# app is in the foreground on an emulator, ``frida-ps -Uai`` shows
# ``2167  WeakBank Low  com.example.weakbank.low``, and the workbench
# was naively passing the package id (``com.example.weakbank.low``)
# straight to ``device.attach`` — which on Android matches the
# Process.name column, finds nothing, and raises. The fix resolves
# package id → PID via ``enumerate_applications`` first.


def test_attach_resolves_package_id_via_enumerate_applications(
    stub_frida: _StubFridaModule,
) -> None:
    """Android happy path: package id maps to a running PID via
    ``Application.identifier``; we attach by PID, never by name."""
    stub_frida.device.apps_override = [
        _StubApplication(identifier="com.other.app", name="Other", pid=999),
        _StubApplication(
            identifier="com.example.weakbank.low",
            name="WeakBank Low",
            pid=2167,
        ),
        _StubApplication(
            identifier="com.installed.but.dead",
            name="Dead",
            pid=0,
        ),
    ]
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example.weakbank.low")
    assert session.pid == 2167
    assert session.package == "com.example.weakbank.low"
    assert stub_frida.device.attach_calls == [2167]


def test_attach_falls_back_to_enumerate_processes_when_app_missing(
    stub_frida: _StubFridaModule,
) -> None:
    """Non-Android target (or unusual setup) where the entry isn't in
    ``enumerate_applications`` but a process with that exact name is
    in ``enumerate_processes``."""
    stub_frida.device.apps_override = []
    stub_frida.device.processes_override = [
        _StubProcess(name="systemd", pid=1),
        _StubProcess(name="my-binary", pid=4321),
    ]
    client = fc.FridaClient(ring_size=200)
    session = client.attach("my-binary")
    assert session.pid == 4321
    assert stub_frida.device.attach_calls == [4321]


def test_attach_skips_zero_pid_applications(
    stub_frida: _StubFridaModule,
) -> None:
    """An installed-but-not-running app surfaces as ``pid == 0`` in
    ``enumerate_applications``; we must not treat that as 'running'
    and we must continue to ``enumerate_processes`` (and ultimately
    the friendly 'not running' error if neither layer has it)."""
    stub_frida.device.apps_override = [
        _StubApplication(
            identifier="com.example.weakbank.low",
            name="WeakBank Low",
            pid=0,
        ),
    ]
    stub_frida.device.processes_override = []
    client = fc.FridaClient(ring_size=200)
    with pytest.raises(fc.FridaUnavailableError) as excinfo:
        client.attach("com.example.weakbank.low")
    msg = str(excinfo.value)
    assert "com.example.weakbank.low" in msg
    assert "not running" in msg
    assert "Spawn" in msg  # operator hint to retry with cold-start
    assert stub_frida.device.attach_calls == []


def test_attach_raises_friendly_error_when_app_not_running(
    stub_frida: _StubFridaModule,
) -> None:
    """Both resolution layers come up empty → operator-actionable
    ``FridaUnavailableError`` (mapped to 503 by the route layer)."""
    stub_frida.device.apps_override = []
    stub_frida.device.processes_override = []
    client = fc.FridaClient(ring_size=200)
    with pytest.raises(fc.FridaUnavailableError) as excinfo:
        client.attach("com.example.weakbank.low")
    msg = str(excinfo.value)
    assert "com.example.weakbank.low" in msg
    assert "not running" in msg
    # Frida.attach was NOT called — we caught it before talking to
    # the device, so the 503 gets emitted with a clean message
    # instead of the cryptic "unable to find process with name '...'".
    assert stub_frida.device.attach_calls == []


def test_attach_swallows_enumerate_applications_exceptions(
    stub_frida: _StubFridaModule,
) -> None:
    """Old Frida builds may not implement ``enumerate_applications``
    on a USB device handle (or it raises for a non-Android target);
    the resolver degrades to ``enumerate_processes`` rather than
    bubbling up an attach failure."""

    def _boom() -> Any:
        raise AttributeError("ancient frida-server")

    stub_frida.device.enumerate_applications = _boom  # type: ignore[assignment]
    stub_frida.device.processes_override = [
        _StubProcess(name="com.example.weakbank.low", pid=2167),
    ]
    client = fc.FridaClient(ring_size=200)
    session = client.attach("com.example.weakbank.low")
    assert session.pid == 2167
    assert stub_frida.device.attach_calls == [2167]


def test_attach_wraps_device_attach_failure(
    stub_frida: _StubFridaModule,
) -> None:
    """If the resolver finds a PID but ``device.attach(pid)`` itself
    raises (e.g. ``frida-server`` died between enumerate and attach),
    we still surface a ``FridaUnavailableError`` with both the PID
    and the package id so the operator has enough context to
    diagnose without grepping logs."""
    stub_frida.device.apps_override = [
        _StubApplication(
            identifier="com.example.weakbank.low",
            name="WeakBank Low",
            pid=2167,
        ),
    ]

    def _boom(_target: Any) -> _StubSession:
        raise RuntimeError("frida-server dropped the connection")

    stub_frida.device.attach = _boom  # type: ignore[assignment]
    client = fc.FridaClient(ring_size=200)
    with pytest.raises(fc.FridaUnavailableError) as excinfo:
        client.attach("com.example.weakbank.low")
    msg = str(excinfo.value)
    assert "pid=2167" in msg
    assert "com.example.weakbank.low" in msg
    assert "frida-server dropped the connection" in msg


def test_attach_with_spawn_wraps_spawn_failure(
    stub_frida: _StubFridaModule,
) -> None:
    """Spawn-path failures (e.g. package not installed, signature
    mismatch) get the same FridaUnavailableError wrapping treatment
    as the running-attach failures, so the route's exception ladder
    can map both to 503 with a clean message."""

    def _boom(_pkg: str) -> int:
        raise RuntimeError("unable to find application with identifier")

    stub_frida.device.spawn = _boom  # type: ignore[assignment]
    client = fc.FridaClient(ring_size=200)
    with pytest.raises(fc.FridaUnavailableError) as excinfo:
        client.attach("com.example.does.not.exist", spawn=True)
    msg = str(excinfo.value)
    assert "frida.spawn" in msg
    assert "com.example.does.not.exist" in msg


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
