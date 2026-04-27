"""Frida adapter foundation for the Hook Lab (Phase 6 step 4 / DEC-023).

This module provides a small, class-based wrapper around the host-side
``frida`` Python bindings:

* :class:`FridaClient` — process-wide handle on the local Frida USB device.
  Holds the lazy ``frida`` import, the device handle, and the dict of
  active sessions. One instance per workbench process; cached on
  ``app.state.frida_client`` by :func:`get_frida_client`.
* :class:`FridaSession` — per-attach state. Wraps a single
  ``frida.core.Session``; exposes :meth:`load_script`,
  :meth:`detach`, and a thread-safe ring buffer of :class:`TraceEvent`
  entries that the Frida message thread populates via :meth:`_on_message`.

Sub-step 4.3 stops here: there are no HTTP routes, no UI consumers, and
no JSONL persistence yet. 4.4 will add hook templates that produce the
``js`` strings; 4.5 will wire the Inject UI and the WebSocket that drains
the ring buffer; 4.7 will let the LLM call this via the
``generate_frida_hook`` skill. The ring-buffer / event-shape contract
established here is what those layers will consume — see
``docs/STATE.md`` for the live picture.

The design follows DEC-022 (optional dependency) and DEC-023 (Hook Lab
phasing): ``frida`` is imported lazily inside :func:`_frida_python` so
the default ``pytest`` suite (which does not install the ``[frida]``
extra) can monkeypatch the seam with a stub. On the device side,
``frida-server`` is operator-managed — see
``docs/SAFETY_AND_SECURITY.md``.
"""

from __future__ import annotations

import collections
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional, Union


logger = logging.getLogger(__name__)


# ``trace_ring_buffer_size`` is clamped to ``>= MIN_RING_SIZE`` in
# :mod:`androscan.config.loader`; any further drift would mean ``deque``
# silently drops every event the moment the wrap-around hits, which is a
# very unfun thing to debug.
MIN_RING_SIZE = 100


class FridaUnavailableError(RuntimeError):
    """Raised when the ``frida`` Python package is not importable.

    Mirrors the ``EmbedProviderError`` install-hint shape from
    :mod:`androscan.rag.embed` so the Settings → Status card can show
    the operator a single, copy-pasteable remediation.
    """


@dataclass
class TraceEvent:
    """A single message coming back from the Frida script.

    ``kind`` mirrors the ``message["type"]`` Frida itself uses:

    * ``"send"`` — the JS called ``send(payload, data)``; ``payload`` is
      the user-supplied object (typed however the script chose).
    * ``"error"`` — the JS threw or otherwise produced a runtime error;
      ``payload`` is a ``{"description", "stack", "lineNumber",
      "fileName"}`` dict copied from the raw message for convenience.
    * ``"log"`` — ``console.log`` / ``console.warn`` / ``console.error``
      from the JS (Frida emits these as ``{"type": "log", "level": ...,
      "payload": ...}``).

    ``raw`` is the unmodified Frida message dict; consumers (4.5 WS,
    4.7 LLM skill) can read fields we haven't promoted to first-class
    yet without forcing a schema change here.
    """

    ts: float
    session_id: str
    kind: Literal["send", "error", "log"]
    payload: Any
    raw: dict[str, Any]


@dataclass
class _SessionStats:
    """Mutable counters tracked alongside the ring buffer.

    Kept separate from :class:`FridaSession` to make the locking story
    obvious: every read/write of these counters happens under the
    session's lock.

    ``persist_dropped`` is bumped by the JSONL writer thread when a
    serialization failure forces it to drop a line; ``persist_path``
    surfaces the active sink so the UI can show "writing to <path>".
    Both are ``None`` / ``0`` when persistence is disabled.
    """

    total_events: int = 0
    dropped: int = 0
    last_ts: Optional[float] = None
    by_kind: dict[str, int] = field(default_factory=dict)
    persist_dropped: int = 0
    persist_path: Optional[str] = None


class FridaSession:
    """Per-attach state: process info, ring buffer, script handle, hook.

    A session is created by :meth:`FridaClient.attach`; the caller is
    responsible for eventually calling :meth:`detach` (or letting
    :meth:`FridaClient.detach_all` do it on shutdown). All Frida-thread
    callbacks land on :meth:`_on_message`, which appends to the deque
    under :attr:`_lock` so 4.5's WebSocket draining and 4.7's LLM skill
    can read the buffer concurrently without seeing a half-applied push.
    """

    def __init__(
        self,
        *,
        client: "FridaClient",
        package: str,
        pid: int,
        session: Any,
        ring_size: int,
        session_id: Optional[str] = None,
    ) -> None:
        self.session_id: str = session_id or uuid.uuid4().hex[:12]
        self.package: str = package
        self.pid: int = pid
        # Hook-Lab metadata. Both stay ``None`` for raw adapter use
        # (e.g. unit tests that build a session without going through
        # the route layer); 4.5's :mod:`androscan.web.frida_routes`
        # populates them post-attach so list/export endpoints can
        # surface "this session was launched from template X for app
        # Y" without a parallel registry.
        self.app_id: Optional[str] = None
        self.template_id: Optional[str] = None
        self.started_at: float = time.time()
        self._client = client
        self._frida_session = session
        self._ring: collections.deque[TraceEvent] = collections.deque(
            maxlen=max(MIN_RING_SIZE, int(ring_size))
        )
        self._lock = threading.Lock()
        self._stats = _SessionStats()
        self._scripts: list[Any] = []
        self._detached = False
        # 4.5 wires WebSocket pumps via this slot; 4.7's LLM skill may
        # also register one. The hook fires *after* the event is in the
        # ring so the consumer can never observe a buffer shorter than
        # ``stats()`` claims.
        self.on_event: Optional[Callable[[TraceEvent], None]] = None
        # JSONL persistence (set_persistence_path). The writer thread
        # owns the file handle; the main thread only ever pushes events
        # to the unbounded ``queue.Queue`` and reads counters under
        # ``_lock``. ``None`` everywhere = persistence disabled, which
        # is the default.
        self._persist_path: Optional[Path] = None
        self._persist_queue: Optional["queue.Queue[Optional[TraceEvent]]"] = None
        self._persist_thread: Optional[threading.Thread] = None
        self._persist_started = False

    # -- script lifecycle -------------------------------------------------

    def load_script(self, js: str, name: str = "anonymous") -> Any:
        """Compile and run ``js`` inside the attached process.

        ``name`` is forwarded to Frida purely for log/error attribution
        (it shows up in ``stack`` traces from the runtime). Returns the
        underlying ``frida.core.Script`` so callers in 4.5 can call
        ``post(...)`` on it; the script is also tracked internally so
        :meth:`detach` can unload it.
        """
        if self._detached:
            raise RuntimeError(
                f"FridaSession {self.session_id!r} is detached; create a new attach"
            )
        if not isinstance(js, str) or not js.strip():
            raise ValueError("frida script must be a non-empty string")

        script = self._frida_session.create_script(js, name=name)
        # The Frida Python binding emits messages on a background thread;
        # ``script.on("message", cb)`` registers a callback that takes
        # ``(message, data)`` where ``data`` is optional binary attached
        # via ``send(payload, data)``.
        script.on("message", self._on_message)
        script.load()
        self._scripts.append(script)
        return script

    # -- JSONL persistence (4.5) -----------------------------------------

    def set_persistence_path(self, path: Union[str, Path]) -> None:
        """Persist every subsequent :class:`TraceEvent` to ``path`` as JSONL.

        One line per event, formatted as the same
        ``{ts, session_id, kind, payload, raw}`` shape ``events()``
        returns. Best-effort: serialization failures bump
        ``persist_dropped`` in :meth:`stats` and log at WARNING but
        never raise back into the Frida message thread (which would
        kill message delivery for the rest of the session).

        Implementation note: the writer runs on a dedicated daemon
        thread fed by an unbounded ``queue.Queue``. We pick a separate
        queue (rather than re-using the ring buffer) so the on-disk
        trace is loss-less even when the in-memory ring rotates — the
        ring is for live UI, the JSONL is for forensics. ``detach``
        sends a poison-pill ``None`` and joins the thread so the file
        is closed cleanly.

        Calling this method twice on the same session raises
        ``RuntimeError``: switching mid-session would either lose
        events (close-then-reopen race) or leave two open files, both
        of which violate operator expectations. Detach + re-attach is
        the supported way to rotate.
        """

        if self._detached:
            raise RuntimeError(
                f"FridaSession {self.session_id!r} is detached; persistence cannot be set"
            )
        if self._persist_started:
            raise RuntimeError(
                f"FridaSession {self.session_id!r} already has a persistence path; "
                "re-attach to rotate"
            )
        target = Path(path)
        # Create the parent directory eagerly so the writer thread's
        # first ``open`` doesn't fail on a fresh ``apps/<id>/<run_ts>/``
        # tree. We tolerate an existing file (append mode) so a crashed
        # session can be resumed by the operator if they keep the
        # session_id stable.
        target.parent.mkdir(parents=True, exist_ok=True)

        q: "queue.Queue[Optional[TraceEvent]]" = queue.Queue()
        thread = threading.Thread(
            target=self._persist_worker,
            args=(target, q),
            name=f"frida-persist-{self.session_id}",
            daemon=True,
        )
        with self._lock:
            self._persist_path = target
            self._persist_queue = q
            self._persist_thread = thread
            self._persist_started = True
            self._stats.persist_path = str(target)
        thread.start()

    def _persist_worker(
        self,
        path: Path,
        q: "queue.Queue[Optional[TraceEvent]]",
    ) -> None:
        """Drain ``q`` to ``path`` until a ``None`` poison pill arrives.

        Failures are isolated per-event: if a single payload is not
        JSON-serializable we increment ``persist_dropped`` and continue
        rather than aborting the whole writer (the next event might be
        fine and the operator wants the rest of the trace). A failure
        to *open* the file is also non-fatal — we log + bump the drop
        counter for every queued event so ``stats()`` still tells the
        UI something useful, but the session keeps streaming to the
        ring + WebSocket.
        """

        try:
            f = path.open("a", encoding="utf-8", buffering=1)  # line-buffered
        except OSError as e:
            logger.warning(
                "FridaSession %s: cannot open persistence path %s: %s",
                self.session_id, path, e,
            )
            # Drain and drop so producers aren't blocked forever.
            while True:
                item = q.get()
                if item is None:
                    return
                with self._lock:
                    self._stats.persist_dropped += 1
            return  # pragma: no cover - unreachable but keeps type-checkers happy

        try:
            while True:
                item = q.get()
                if item is None:
                    return
                try:
                    line = json.dumps(
                        _event_to_jsonable(item),
                        ensure_ascii=False,
                        default=_jsonl_fallback,
                    )
                    f.write(line)
                    f.write("\n")
                except Exception as e:  # serialization OR write
                    logger.warning(
                        "FridaSession %s: persist write failed (%s: %s); dropping event",
                        self.session_id, type(e).__name__, e,
                    )
                    with self._lock:
                        self._stats.persist_dropped += 1
        finally:
            try:
                f.flush()
                f.close()
            except OSError:  # pragma: no cover - best-effort cleanup
                pass

    # -- buffer access ----------------------------------------------------

    def events(self, limit: Optional[int] = None) -> list[TraceEvent]:
        """Snapshot the current ring buffer (oldest → newest).

        Used by 4.5's initial WS catch-up and 4.7's LLM skill to read
        recent activity without racing the producer thread. ``limit`` is
        applied from the *tail* (most recent ``limit`` events) which is
        usually what consumers want.
        """
        with self._lock:
            items = list(self._ring)
        if limit is not None and limit >= 0 and len(items) > limit:
            items = items[-limit:]
        return items

    def stats(self) -> dict[str, Any]:
        """Return a snapshot of bookkeeping counters."""
        with self._lock:
            return {
                "session_id": self.session_id,
                "package": self.package,
                "pid": self.pid,
                "app_id": self.app_id,
                "template_id": self.template_id,
                "started_at": self.started_at,
                "total_events": self._stats.total_events,
                "dropped": self._stats.dropped,
                "last_ts": self._stats.last_ts,
                "buffered": len(self._ring),
                "ring_capacity": self._ring.maxlen,
                "by_kind": dict(self._stats.by_kind),
                "detached": self._detached,
                "persist_path": self._stats.persist_path,
                "persist_dropped": self._stats.persist_dropped,
            }

    # -- detach -----------------------------------------------------------

    def detach(self) -> None:
        """Unload scripts, detach from the target, drop from the client.

        Idempotent — the second call is a no-op so shutdown handlers
        and explicit user-driven detach can both run without worrying
        about ordering. If a JSONL persistence path is active, sends
        the writer-thread poison pill and waits up to two seconds for
        it to flush; tests with a 0-second timeout rely on this being
        finite.
        """
        if self._detached:
            return
        self._detached = True
        for script in list(self._scripts):
            try:
                script.unload()
            except Exception as e:  # pragma: no cover - exercised on real Frida
                logger.debug("FridaSession %s: script.unload failed: %s", self.session_id, e)
        self._scripts.clear()
        try:
            self._frida_session.detach()
        except Exception as e:  # pragma: no cover - exercised on real Frida
            logger.debug("FridaSession %s: session.detach failed: %s", self.session_id, e)
        # Flush + close the persistence writer if it was started.
        # ``_persist_queue`` / ``_persist_thread`` are set together
        # inside ``set_persistence_path`` so checking either is fine,
        # but we double-check both for clarity.
        q = self._persist_queue
        thread = self._persist_thread
        if q is not None:
            q.put(None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._client._forget(self)  # noqa: SLF001 — intentional bidirectional cleanup

    # -- internal: Frida message thread ----------------------------------

    def _on_message(self, message: dict[str, Any], _data: Optional[bytes]) -> None:
        """Frida message-thread entry point.

        Runs **off the asyncio event loop**: the binding spins a private
        thread for message delivery, so we lock the deque on push and
        only invoke ``on_event`` after the buffer is consistent. Errors
        in user-supplied ``on_event`` hooks are logged but never
        propagated back into the Frida thread (which would silently kill
        message delivery for the rest of the session's lifetime).
        """
        kind, payload = self._classify_message(message)
        ts = time.time()
        event = TraceEvent(
            ts=ts,
            session_id=self.session_id,
            kind=kind,
            payload=payload,
            raw=dict(message) if isinstance(message, dict) else {"value": message},
        )
        with self._lock:
            # ``deque(maxlen=N)`` rotates silently; we keep an explicit
            # ``dropped`` counter so the UI can show "you missed 17
            # events because the buffer was full".
            if self._ring.maxlen is not None and len(self._ring) == self._ring.maxlen:
                self._stats.dropped += 1
            self._ring.append(event)
            self._stats.total_events += 1
            self._stats.last_ts = ts
            self._stats.by_kind[kind] = self._stats.by_kind.get(kind, 0) + 1
            hook = self.on_event
            persist_q = self._persist_queue
        # Enqueue *outside* the lock — ``queue.put`` on an unbounded
        # queue is wait-free, but we still want to keep ``_lock``
        # tight (it's contended by every UI poll of ``stats()``).
        if persist_q is not None:
            persist_q.put(event)
        if hook is not None:
            try:
                hook(event)
            except Exception as e:  # pragma: no cover - hook-author responsibility
                logger.warning(
                    "FridaSession %s on_event hook raised %s: %s",
                    self.session_id, type(e).__name__, e,
                )

    @staticmethod
    def _classify_message(message: Any) -> tuple[Literal["send", "error", "log"], Any]:
        """Map a raw Frida message dict to a stable ``(kind, payload)``.

        Frida uses ``message["type"]`` ∈ ``{"send", "error", "log"}``;
        anything else is normalised to ``"send"`` with the whole message
        as the payload so we never silently drop unknown shapes (which
        would surprise debuggers wondering why ``stats()`` shows fewer
        events than the script logged).
        """
        if not isinstance(message, dict):
            return "send", message
        mtype = message.get("type")
        if mtype == "send":
            return "send", message.get("payload")
        if mtype == "error":
            return "error", {
                "description": message.get("description"),
                "stack": message.get("stack"),
                "lineNumber": message.get("lineNumber"),
                "fileName": message.get("fileName"),
            }
        if mtype == "log":
            return "log", {
                "level": message.get("level", "info"),
                "payload": message.get("payload"),
            }
        return "send", message


class FridaClient:
    """Process-wide Frida handle. One per workbench process.

    Construction is deliberately cheap (no I/O); :meth:`attach` is the
    first call that actually talks to ``frida-server``. Tests
    monkeypatch :func:`_frida_python` to inject a stub ``frida`` module
    whose :class:`Device` exposes :meth:`attach` returning a stub
    session — see ``tests/test_frida_client.py`` for the shape.
    """

    def __init__(self, *, ring_size: int = 5000) -> None:
        if ring_size < MIN_RING_SIZE:
            raise ValueError(
                f"ring_size must be >= {MIN_RING_SIZE} (got {ring_size}); "
                "lower values silently drop every event after wrap-around"
            )
        self._ring_size = int(ring_size)
        self._sessions: dict[str, FridaSession] = {}
        self._lock = threading.Lock()
        self._frida: Any = None
        self._device: Any = None
        self._import_error: Optional[BaseException] = None
        self._closed = False

    # -- availability + lazy bootstrap -----------------------------------

    def is_available(self) -> bool:
        """``True`` if ``frida`` imported and a USB device handle is reachable.

        Non-raising: the Settings → Status card calls this in a tight
        loop and we don't want a missing optional dep to blow up the
        whole status payload.
        """
        if self._closed:
            return False
        try:
            self._ensure_device()
        except FridaUnavailableError:
            return False
        except Exception as e:  # pragma: no cover - exercised on real Frida outage
            logger.debug("FridaClient.is_available probe error: %s", e)
            return False
        return self._device is not None

    def _ensure_frida(self) -> Any:
        """Lazily import ``frida``; cache the module or the import error."""
        if self._frida is not None:
            return self._frida
        if self._import_error is not None:
            raise FridaUnavailableError(
                "frida not installed. Install with: pip install -e '.[frida]'"
            ) from self._import_error
        try:
            self._frida = _frida_python()
        except ImportError as e:
            self._import_error = e
            raise FridaUnavailableError(
                "frida not installed. Install with: pip install -e '.[frida]'"
            ) from e
        return self._frida

    def _ensure_device(self) -> Any:
        """Resolve the local USB device handle, lazily."""
        if self._device is not None:
            return self._device
        frida = self._ensure_frida()
        # ``get_usb_device(timeout=...)`` is the common path for an
        # emulator + adb setup; on hosts with no USB bridge it raises,
        # which we re-raise as ``FridaUnavailableError`` so callers can
        # treat "no frida" and "no device" identically.
        try:
            self._device = frida.get_usb_device(timeout=1)
        except Exception as e:
            raise FridaUnavailableError(
                f"No Frida USB device available: {e}"
            ) from e
        return self._device

    # -- session lifecycle -----------------------------------------------

    def attach(self, package: str, *, spawn: bool = False) -> FridaSession:
        """Attach to ``package``; optionally spawn it first.

        ``spawn=True`` mirrors the ``frida -f`` flag: the device spawns
        the process suspended, we attach, the caller loads scripts, and
        then 4.5's UI code calls ``device.resume(pid)``. 4.3 doesn't yet
        ship a UI for this; the path exists so 4.4 templates that need
        instrumentation in the constructor can rely on it.
        """
        if not isinstance(package, str) or not package.strip():
            raise ValueError("package must be a non-empty string")
        device = self._ensure_device()
        target: Any = package
        pid: int
        if spawn:
            pid = int(device.spawn(package))
            target = pid
        try:
            frida_session = device.attach(target)
        except Exception as e:
            raise FridaUnavailableError(f"frida.attach({package!r}) failed: {e}") from e
        # Resolve the actual pid (Frida exposes either ``session.pid`` or
        # nothing depending on version; fall back to whatever target was).
        if not spawn:
            pid = int(getattr(frida_session, "pid", 0) or 0)
        session = FridaSession(
            client=self,
            package=package,
            pid=pid,
            session=frida_session,
            ring_size=self._ring_size,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[FridaSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def list_sessions(self) -> list[FridaSession]:
        with self._lock:
            return list(self._sessions.values())

    def detach_all(self) -> None:
        """Detach every active session. Safe to call multiple times.

        Wired to ``app.on_event("shutdown")`` in
        :mod:`androscan.web.app`; also useful in tests that swap clients
        between cases.
        """
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._closed = True
        for s in sessions:
            try:
                s.detach()
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(
                    "FridaClient.detach_all: session %s detach raised: %s",
                    s.session_id, e,
                )

    # -- internal -------------------------------------------------------

    def _forget(self, session: FridaSession) -> None:
        """Remove ``session`` from the active map (called by ``Session.detach``)."""
        with self._lock:
            self._sessions.pop(session.session_id, None)


# ---------------------------------------------------------------------------
# JSONL persistence helpers (4.5).
#
# These are at module level so the ``frida_routes`` layer (4.5) can also
# call ``_event_to_jsonable`` when streaming events over the WebSocket —
# the wire format and the on-disk format are deliberately identical so
# the export endpoint is just ``StreamingResponse(open(path))``.


def _event_to_jsonable(event: TraceEvent) -> dict[str, Any]:
    """Project a :class:`TraceEvent` to the JSONL line shape.

    Keeps the dict order stable (``ts`` first, then identifying fields,
    then payload + raw) so a human eyeballing the file finds the
    timestamp without scanning right. ``raw`` is included verbatim so
    the persisted trace is a strict superset of the WebSocket stream
    (forensics > UI compactness).
    """
    return {
        "ts": event.ts,
        "session_id": event.session_id,
        "kind": event.kind,
        "payload": event.payload,
        "raw": event.raw,
    }


def _jsonl_fallback(obj: Any) -> Any:
    """Fallback for ``json.dumps(default=...)``.

    Frida payloads are *usually* JSON-serializable (the binding hands
    us dicts/lists/primitives) but operator-supplied scripts can call
    ``send(someBuffer)`` and similar; ``repr()`` is the least-surprising
    last-ditch path. We deliberately do **not** raise — a single bad
    event must not kill the writer for the rest of the session.
    """
    try:
        return repr(obj)
    except Exception:  # pragma: no cover - defensive
        return f"<unserializable {type(obj).__name__}>"


# ---------------------------------------------------------------------------
# Test seam: monkeypatch this in ``tests/test_frida_client.py`` to inject a
# stub ``frida`` module without installing the ``[frida]`` extra. The seam is
# intentionally minimal so the lazy-import semantics in :class:`FridaClient`
# stay verifiable from the test side.


def _frida_python() -> Any:
    """Import and return the ``frida`` Python module.

    Isolated so tests can ``monkeypatch.setattr(frida_client, "_frida_python", ...)``
    and exercise :class:`FridaClient` end-to-end without touching a real
    device. Production callers should use :class:`FridaClient` directly;
    this function is not part of the public API.
    """
    import frida  # type: ignore[import-not-found]

    return frida


# ---------------------------------------------------------------------------
# FastAPI integration helpers (used by ``androscan.web.app``).
#
# We attach the cached :class:`FridaClient` to ``app.state.frida_client`` so
# tests that want to swap a mock can do so without touching a module-level
# global. ``get_frida_client`` is the only thing imported by ``app.py``; the
# import is wrapped in a ``try`` there so a missing ``[frida]`` extra never
# blows up app startup.


def get_frida_client(app: Any, config: Any) -> FridaClient:
    """Return the cached :class:`FridaClient` on ``app.state``, creating it lazily.

    ``app`` is the live FastAPI instance; ``config`` is whatever
    :func:`androscan.config.loader.load_config` returned (we read
    ``frida_trace_ring_buffer_size`` off it). Construction is cheap —
    we don't talk to the device until :meth:`FridaClient.attach` or
    :meth:`FridaClient.is_available` is called.
    """
    existing = getattr(app.state, "frida_client", None)
    if isinstance(existing, FridaClient):
        return existing
    ring_size = int(getattr(config, "frida_trace_ring_buffer_size", 5000) or 5000)
    client = FridaClient(ring_size=max(MIN_RING_SIZE, ring_size))
    app.state.frida_client = client
    return client


__all__ = [
    "FridaClient",
    "FridaSession",
    "FridaUnavailableError",
    "TraceEvent",
    "MIN_RING_SIZE",
    "get_frida_client",
    "_event_to_jsonable",
]
