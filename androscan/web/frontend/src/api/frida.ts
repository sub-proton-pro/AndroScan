/**
 * Frontend client for ``/api/frida/*`` and ``/ws/frida/*`` (Hook Lab,
 * sub-step 4.5). Mirrors the backend contract in
 * ``androscan/web/frida_routes.py``.
 *
 * Two layers:
 *
 * 1. Typed REST helpers — thin ``fetch`` wrappers that surface the
 *    backend's structured error envelopes via a discriminated union
 *    (``FridaResult<T>``).
 * 2. ``useFridaTrace(sessionId)`` — React hook owning the WS lifecycle
 *    (open / close on session change, capped buffer, pause/resume,
 *    drop coalescing) so the panel components stay declarative.
 *
 * The wire format on the WS is identical to the JSONL persistence
 * format (``_event_to_jsonable`` on the backend), with one extension:
 * a ``{type: 'drop', session_id: ...}`` notice when WS backpressure
 * forced a queue drop. We carry the drop notice through to the UI so
 * the operator sees that events were lost.
 */
import { useCallback, useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Types — kept aligned with `androscan/web/frida_routes.py` payload helpers.

export type HookTemplateParam = {
  name: string;
  description: string;
  required: boolean;
  default: string;
};

export type HookTemplate = {
  id: string;
  name: string;
  description: string;
  params: HookTemplateParam[];
  sensitive_apis: string[];
};

export type ParseInfo = {
  /** ``true`` iff the rendered JS parsed cleanly. */
  ok: boolean;
  /** Human-readable error message; ``null`` on success. */
  error: string | null;
  /** 1-based line number when extractable; ``null`` otherwise. */
  line: number | null;
  /** Column number; ``pyjsparser`` doesn't surface columns so this is
   *  almost always ``null``. */
  column: number | null;
  /** ``false`` when ``pyjsparser`` isn't installed — UI should soften
   *  the Inject button rather than block it (DEC-023 Option-A). */
  available: boolean;
};

export type RenderResult = {
  rendered: {
    template_id: string;
    js: string;
    summary: string;
    params_used: Record<string, string>;
  };
  parse: ParseInfo;
};

export type FridaSessionInfo = {
  session_id: string;
  app_id: string | null;
  template_id: string | null;
  package: string;
  pid: number | null;
  started_at: number | null;
  buffered: number;
  ring_capacity: number | null;
  total_events: number;
  dropped: number;
  by_kind: Record<string, number>;
  last_ts: number | null;
  detached: boolean;
  persist_path: string | null;
  persist_dropped: number;
};

export type CreateSessionResult = {
  session_id: string;
  app_id: string;
  template_id: string;
  package: string;
  pid: number | null;
  started_at: number;
  ring_capacity: number | null;
  ws_url: string;
  persist_path: string | null;
  summary: string;
  parse: ParseInfo;
};

export type CreateSessionBody = {
  app_id: string;
  package: string;
  template_id: string;
  params?: Record<string, string>;
  spawn?: boolean;
  persist?: boolean;
};

/** Shape of a single Frida trace event on the WebSocket. Mirrors
 *  ``_event_to_jsonable`` (``ts``, ``session_id``, ``kind``, ``payload``,
 *  ``raw``). The optional ``__drop__`` shape is *not* emitted on the
 *  wire — it's an internal sentinel — instead the server sends
 *  ``{type: 'drop', session_id}`` which we expose as ``TraceDropNotice``
 *  below. */
export type TraceEvent = {
  ts: number;
  session_id: string;
  kind: "send" | "error" | "log";
  payload: unknown;
  raw: Record<string, unknown>;
};

export type TraceDropNotice = {
  type: "drop";
  session_id: string;
};

/** Discriminated union an UI consumer can ``switch`` on without
 *  remembering all the success / error shapes. */
export type FridaResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status: number; raw?: unknown };

// ---------------------------------------------------------------------------
// REST helpers

async function _read<T>(url: string): Promise<FridaResult<T>> {
  try {
    const r = await fetch(url);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      return {
        ok: false,
        error: _extractError(body) ?? `HTTP ${r.status}`,
        status: r.status,
        raw: body,
      };
    }
    return { ok: true, data: body as T };
  } catch (e) {
    return { ok: false, error: (e as Error).message ?? "network error", status: 0 };
  }
}

async function _send<T>(
  url: string,
  method: "POST" | "DELETE",
  body?: unknown,
): Promise<FridaResult<T>> {
  try {
    const r = await fetch(url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      return {
        ok: false,
        error: _extractError(j) ?? `HTTP ${r.status}`,
        status: r.status,
        raw: j,
      };
    }
    return { ok: true, data: j as T };
  } catch (e) {
    return { ok: false, error: (e as Error).message ?? "network error", status: 0 };
  }
}

/** FastAPI's HTTPException renders as ``{"detail": ...}``. ``detail`` may
 *  be a string OR (for the 400 ``render_parse_error`` shape) a structured
 *  object — we collapse it to a one-liner here so callers can show it
 *  inline. The full structured payload is still in ``raw`` for callers
 *  that want to render the error in a richer way (e.g. inline Monaco
 *  marker for ``render_parse_error.line``). */
function _extractError(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as Record<string, unknown>).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const obj = detail as Record<string, unknown>;
    const message = typeof obj.message === "string" ? obj.message : null;
    const error = typeof obj.error === "string" ? obj.error : null;
    if (message && error) return `${error}: ${message}`;
    return message ?? error ?? JSON.stringify(detail);
  }
  return null;
}

export function listTemplates() {
  return _read<{ templates: HookTemplate[] }>("/api/frida/templates");
}

export function getTemplate(templateId: string) {
  return _read<HookTemplate>(`/api/frida/templates/${encodeURIComponent(templateId)}`);
}

export function renderTemplate(template_id: string, params: Record<string, string>) {
  return _send<RenderResult>("/api/frida/render", "POST", { template_id, params });
}

export function createSession(body: CreateSessionBody) {
  return _send<CreateSessionResult>("/api/frida/sessions", "POST", body);
}

/** Result shape for ``POST /api/frida/server/start``.
 *
 * The route is idempotent — calling it when frida-server is already
 * running as root returns ``ok=true, started=false, already_running=true``
 * without doing anything device-side. ``started=true`` only when this
 * call actually fired the daemon-fork.
 *
 * Failure paths surface via the standard ``FridaResult`` envelope (``ok=false``)
 * with ``status`` carrying the HTTP code so the Settings card can branch:
 *   * 409 → device not rooted, or running as non-root that we won't auto-promote.
 *   * 404 → binary not pushed to ``/data/local/tmp/frida-server`` yet.
 *   * 502 → start command failed, or server didn't appear in ``ps`` after the start.
 */
export type StartServerResult = {
  ok: true;
  started: boolean;
  already_running: boolean;
  pid: number | null;
  uid: string | null;
  message: string;
};

/** Start ``frida-server`` as root on the connected device.
 *
 * Wired to the "Start frida-server (as root)" button on the Settings
 * tab's Frida-server card; shown when the card detects the server is
 * either down OR running as a non-root uid (which can list processes
 * but can't ``ptrace`` into apps, so every Inject would fail with
 * ``unable to connect to remote frida-server: closed``).
 */
export function startFridaServer() {
  return _send<StartServerResult>("/api/frida/server/start", "POST");
}

export function listSessions() {
  return _read<{ sessions: FridaSessionInfo[] }>("/api/frida/sessions");
}

export function getSession(sessionId: string) {
  return _read<FridaSessionInfo>(`/api/frida/sessions/${encodeURIComponent(sessionId)}`);
}

export function deleteSession(sessionId: string) {
  return _send<{ ok: true; session_id: string }>(
    `/api/frida/sessions/${encodeURIComponent(sessionId)}`,
    "DELETE",
  );
}

export function getSessionEvents(sessionId: string, limit = 200) {
  return _read<{ session_id: string; events: TraceEvent[] }>(
    `/api/frida/sessions/${encodeURIComponent(sessionId)}/events?limit=${limit}`,
  );
}

/** Build the absolute URL for the JSONL export. Used for the ``Export``
 *  button — we let the browser handle the download via ``<a download>``
 *  rather than fetching here, so progress / streaming is native. */
export function exportSessionUrl(sessionId: string): string {
  return `/api/frida/sessions/${encodeURIComponent(sessionId)}/export`;
}

// ---------------------------------------------------------------------------
// Introspection: hooks summary + scope snapshots (sub-step 4.6).
//
// Both are pure aggregations over the in-memory ring buffer; the
// frontend polls them every ``REFRESH_MS`` (2.5s) rather than running
// a parallel WS, because:
//  * the data is already in the ring (no extra Frida I/O),
//  * the panels are *summary* views (a 2.5s lag is invisible to a
//    human eye scanning the table), and
//  * polling means a paused trace WS doesn't also pause hooks/scope.

export type HookStatTopReturn = {
  value: string;
  count: number;
};

export type HookStat = {
  class: string;
  method: string;
  /** The session's template id at create-time. ``null`` only on
   *  legacy sessions created before sub-step 4.5 (none in v1, but the
   *  shape is permissive so a future template-less session shape
   *  doesn't break the panel). */
  template_id: string | null;
  hits: number;
  /** Unix epoch seconds (float). ``null`` until the first event lands. */
  last_seen_ts: number | null;
  top_returns: HookStatTopReturn[];
};

/** Last-known entry / exit snapshot for a single ``(class, method)``
 *  watched by a ``scope_inspector`` template. The ``last_entry`` and
 *  ``last_exit`` fields are independently updated, so a method that's
 *  mid-call (entered, not yet returned) can show entry data without
 *  exit data. */
export type ScopeSnapshotSide = {
  ts: number | null;
  this_fields: Record<string, string>;
};

export type ScopeSnapshotEntry = ScopeSnapshotSide & {
  args: string[] | null;
  this_class: string | null;
};

export type ScopeSnapshotExit = ScopeSnapshotSide & {
  return: string | null;
};

export type ScopeSnapshot = {
  class: string;
  method: string;
  last_entry: ScopeSnapshotEntry | null;
  last_exit: ScopeSnapshotExit | null;
};

export function getSessionHooks(sessionId: string) {
  return _read<{ session_id: string; hooks: HookStat[] }>(
    `/api/frida/sessions/${encodeURIComponent(sessionId)}/hooks`,
  );
}

export function getSessionScope(sessionId: string) {
  return _read<{ session_id: string; snapshots: ScopeSnapshot[] }>(
    `/api/frida/sessions/${encodeURIComponent(sessionId)}/scope`,
  );
}

// ---------------------------------------------------------------------------
// useFridaTrace — WS hook
//
// Wraps the connect/disconnect lifecycle of ``/ws/frida/sessions/:id/trace``.
// Owns three pieces of state the panel components need:
//
//   * ``events``        — ordered list of trace events (capped at
//                         ``maxBuffer``).
//   * ``connection``    — coarse status: ``connecting | open | closed | error``.
//   * ``dropCount``     — count of WS backpressure drops the server has
//                         signalled since this hook last reset.
//
// The hook does NOT auto-reconnect: the WS-server-side close on
// ``unknown_session`` is a hard signal we *want* to surface, and the
// session id is supposed to change rather than the same session
// reconnecting transparently. ``pause()`` stops *appending* new events
// to ``events`` but keeps the socket open and buffers internally so
// ``resume()`` can flush the missed window (capped at ``maxBuffer``).

export type FridaTraceConnection = "idle" | "connecting" | "open" | "closed" | "error";

export type UseFridaTraceOptions = {
  maxBuffer?: number;
  /** When set, the hook starts in the paused state — useful when the
   *  parent already has an export running and doesn't want fresh
   *  events flooding the list mid-download. */
  initialPaused?: boolean;
};

export type UseFridaTraceState = {
  events: TraceEvent[];
  connection: FridaTraceConnection;
  paused: boolean;
  dropCount: number;
  /** Last error message from the server side or the WebSocket itself. */
  errorMessage: string | null;
  pause: () => void;
  resume: () => void;
  clear: () => void;
};

const DEFAULT_MAX_BUFFER = 2000;

/** The WS server sends events as discrete JSON messages of two shapes:
 *  - ``TraceEvent`` (the normal case)
 *  - ``{type: 'drop' | 'error', session_id, ...}`` (server-side notice)
 *  We narrow on ``type`` so React state stays cleanly typed. */
type WsMessage = TraceEvent | { type: string; [k: string]: unknown };

function isTraceEvent(msg: WsMessage): msg is TraceEvent {
  return (
    msg != null &&
    typeof msg === "object" &&
    typeof (msg as TraceEvent).ts === "number" &&
    typeof (msg as TraceEvent).kind === "string" &&
    !("type" in msg)
  );
}

function buildWsUrl(sessionId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/frida/sessions/${encodeURIComponent(
    sessionId,
  )}/trace`;
}

export function useFridaTrace(
  sessionId: string | null,
  opts: UseFridaTraceOptions = {},
): UseFridaTraceState {
  const maxBuffer = opts.maxBuffer ?? DEFAULT_MAX_BUFFER;

  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [connection, setConnection] = useState<FridaTraceConnection>("idle");
  const [dropCount, setDropCount] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [paused, setPaused] = useState<boolean>(opts.initialPaused ?? false);

  // While paused, we keep the WS open and buffer messages here so a
  // subsequent ``resume()`` can flush the window. The buffer is bounded
  // by ``maxBuffer`` to avoid unbounded memory growth on a long pause.
  const pauseBufferRef = useRef<TraceEvent[]>([]);
  const pausedRef = useRef<boolean>(paused);
  pausedRef.current = paused;

  const wsRef = useRef<WebSocket | null>(null);

  // Reset state whenever the session changes. A new session id =
  // a new trace; we don't carry events across sessions.
  useEffect(() => {
    setEvents([]);
    setDropCount(0);
    setErrorMessage(null);
    pauseBufferRef.current = [];
    if (!sessionId) {
      setConnection("idle");
      return;
    }

    let closedByCleanup = false;
    setConnection("connecting");

    let ws: WebSocket;
    try {
      ws = new WebSocket(buildWsUrl(sessionId));
    } catch (e) {
      setConnection("error");
      setErrorMessage((e as Error).message ?? "WebSocket construction failed");
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      setConnection("open");
    };

    ws.onmessage = (raw: MessageEvent) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw.data as string);
      } catch {
        return;
      }
      const msg = parsed as WsMessage;

      if (!isTraceEvent(msg)) {
        const obj = msg as { type?: string; error?: unknown };
        if (obj.type === "drop") {
          setDropCount((n) => n + 1);
        } else if (obj.type === "error") {
          const errStr = typeof obj.error === "string" ? obj.error : "server error";
          setErrorMessage(errStr);
        }
        return;
      }

      if (pausedRef.current) {
        const buf = pauseBufferRef.current;
        buf.push(msg);
        if (buf.length > maxBuffer) buf.splice(0, buf.length - maxBuffer);
        return;
      }
      setEvents((prev) => {
        const next = prev.length >= maxBuffer ? prev.slice(prev.length - maxBuffer + 1) : prev.slice();
        next.push(msg);
        return next;
      });
    };

    ws.onerror = () => {
      // ``onerror`` fires before ``onclose``; we only flag the
      // status, the close handler will tear down state.
      setConnection("error");
    };

    ws.onclose = (ev: CloseEvent) => {
      if (!closedByCleanup) {
        if (ev.code === 1008) {
          setErrorMessage("unknown_session (server rejected)");
        } else if (ev.code !== 1000 && ev.code !== 1001) {
          setErrorMessage((prev) => prev ?? `WebSocket closed (code ${ev.code})`);
        }
      }
      setConnection("closed");
    };

    return () => {
      closedByCleanup = true;
      try {
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close(1000, "session change");
        }
      } catch {
        // ignored
      }
      wsRef.current = null;
    };
    // ``maxBuffer`` is a knob — changing it shouldn't tear down the
    // socket. We deliberately only re-run on session change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const pause = useCallback(() => {
    setPaused(true);
  }, []);

  const resume = useCallback(() => {
    setPaused(false);
    const queued = pauseBufferRef.current;
    pauseBufferRef.current = [];
    if (queued.length === 0) return;
    setEvents((prev) => {
      const merged = prev.concat(queued);
      return merged.length > maxBuffer ? merged.slice(merged.length - maxBuffer) : merged;
    });
  }, [maxBuffer]);

  const clear = useCallback(() => {
    setEvents([]);
    pauseBufferRef.current = [];
    setDropCount(0);
    setErrorMessage(null);
  }, []);

  return {
    events,
    connection,
    paused,
    dropCount,
    errorMessage,
    pause,
    resume,
    clear,
  };
}
