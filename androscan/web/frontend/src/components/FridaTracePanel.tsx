/**
 * Hook Lab — FridaTracePanel.
 *
 * Owns the live-trace UI for a single ``FridaSession``:
 *
 *   * subscribes to ``/ws/frida/sessions/:id/trace`` via
 *     :func:`useFridaTrace` (api/frida.ts);
 *   * renders the latest events in a vertically-virtualised list
 *     (browser-native ``contain: strict`` scroll container; we don't
 *     pull in react-virtual / react-window because the buffer is
 *     bounded at ``maxBuffer = 2000`` so a flat render is cheap);
 *   * filters by substring against ``label`` / ``phase`` / ``message``
 *     (the keys ``send`` payloads use — see
 *     ``frida_hooks/entry_exit_log.py``);
 *   * pause / resume buttons (uses the same hook's pause API);
 *   * Export button: anchor with ``download`` attribute pointing at
 *     ``/api/frida/sessions/:id/export`` so the browser streams the
 *     JSONL straight to disk.
 *
 * All wire shapes match ``_event_to_jsonable`` on the backend; we don't
 * re-derive any field names client-side.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { exportSessionUrl, useFridaTrace, type TraceEvent } from "../api/frida";

type Props = {
  sessionId: string | null;
  /** ``true`` when the session was created with ``persist=true``. The
   *  Export button is disabled when ``false`` because the backend
   *  returns 404 for sessions without a persistence path. */
  persistEnabled: boolean;
};

export function FridaTracePanel({ sessionId, persistEnabled }: Props) {
  const trace = useFridaTrace(sessionId, { maxBuffer: 2000 });
  const [filter, setFilter] = useState("");

  const filtered = useMemo(() => filterEvents(trace.events, filter), [trace.events, filter]);

  // Auto-scroll-to-tail unless the user has scrolled away. We track a
  // ``stickyTail`` flag on the scroll container so a deliberate scroll
  // up to inspect older events doesn't get fought by the auto-pin.
  const listRef = useRef<HTMLDivElement | null>(null);
  const stickyTailRef = useRef<boolean>(true);

  const onScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickyTailRef.current = distFromBottom < 16;
  }, []);

  // Re-pin to the bottom whenever the visible event count changes —
  // but only when the user hasn't scrolled away. ``stickyTailRef`` is
  // a ref (not state) on purpose: we don't want a render storm every
  // time the operator nudges the scrollbar.
  useEffect(() => {
    if (!stickyTailRef.current) return;
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [filtered.length]);

  return (
    <div className="pane-scroll trace-panel">
      <header className="pane-head">
        <h2>Trace</h2>
        <span className="pane-head-actions">
          <span className={`trace-status trace-status-${trace.connection}`} title={trace.connection}>
            {trace.connection}
          </span>
          {trace.dropCount > 0 && (
            <span className="trace-drop-pill" title="WebSocket backpressure drops">
              dropped {trace.dropCount}
            </span>
          )}
        </span>
      </header>

      {!sessionId && (
        <p className="muted small">No session — Inject a hook above to start a trace.</p>
      )}

      {sessionId && (
        <>
          <div className="trace-toolbar">
            <input
              className="trace-filter"
              placeholder="filter (label / phase / message substring)"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              spellCheck={false}
              autoComplete="off"
            />
            <button
              type="button"
              className="ghost-mini"
              onClick={trace.paused ? trace.resume : trace.pause}
              title={trace.paused ? "Resume live updates (flushes buffered events)" : "Pause live updates"}
            >
              {trace.paused ? "Resume" : "Pause"}
            </button>
            <button
              type="button"
              className="ghost-mini"
              onClick={trace.clear}
              title="Drop the local view (server-side ring is unaffected)"
            >
              Clear
            </button>
            <a
              className={`ghost-mini trace-export ${persistEnabled ? "" : "trace-export-disabled"}`}
              href={persistEnabled ? exportSessionUrl(sessionId) : undefined}
              download={persistEnabled ? `${sessionId}.jsonl` : undefined}
              aria-disabled={!persistEnabled}
              title={
                persistEnabled
                  ? "Download the on-disk JSONL trace for this session"
                  : "Export disabled (session created with persist=false)"
              }
              onClick={(e) => {
                if (!persistEnabled) e.preventDefault();
              }}
            >
              Export
            </a>
            <span className="trace-counter muted small">
              {filtered.length} / {trace.events.length}
            </span>
          </div>

          {trace.errorMessage && (
            <p className="hook-error" role="alert">
              {trace.errorMessage}
            </p>
          )}

          <div ref={listRef} onScroll={onScroll} className="trace-list">
            {filtered.length === 0 && (
              <p className="muted small" style={{ padding: "0.6rem" }}>
                {trace.events.length === 0
                  ? "Waiting for events…"
                  : "No events match the current filter."}
              </p>
            )}
            {filtered.map((ev, i) => (
              <TraceRow key={`${ev.ts}-${i}`} event={ev} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers

function filterEvents(events: TraceEvent[], q: string): TraceEvent[] {
  const needle = q.trim().toLowerCase();
  if (!needle) return events;
  return events.filter((ev) => matchesEvent(ev, needle));
}

function matchesEvent(ev: TraceEvent, needle: string): boolean {
  if (ev.kind.toLowerCase().includes(needle)) return true;
  const payload = ev.payload;
  if (typeof payload === "string") {
    return payload.toLowerCase().includes(needle);
  }
  if (payload && typeof payload === "object") {
    const p = payload as Record<string, unknown>;
    for (const key of ["label", "phase", "message", "method", "class", "error"]) {
      const v = p[key];
      if (typeof v === "string" && v.toLowerCase().includes(needle)) return true;
    }
    // Last-ditch: stringify the whole payload. Cheap because most
    // payloads are small dicts and we already short-circuit on the
    // hot keys above.
    try {
      return JSON.stringify(payload).toLowerCase().includes(needle);
    } catch {
      return false;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// TraceRow — one event in the list.

type TraceRowProps = { event: TraceEvent };

function TraceRow({ event }: TraceRowProps) {
  const ts = formatTs(event.ts);
  const label = extractLabel(event);
  const phase = extractPhase(event);
  return (
    <div className={`trace-row trace-row-${event.kind}`}>
      <span className="trace-ts" title={String(event.ts)}>
        {ts}
      </span>
      <span className={`trace-kind trace-kind-${event.kind}`}>{event.kind}</span>
      {phase && <span className={`trace-phase trace-phase-${phase}`}>{phase}</span>}
      {label && <span className="trace-label">{label}</span>}
      <span className="trace-payload">{summarisePayload(event.payload)}</span>
    </div>
  );
}

function formatTs(ts: number): string {
  // `ts` is a Unix timestamp in seconds (Python's ``time.time()``).
  const d = new Date(ts * 1000);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  const ms = String(d.getMilliseconds()).padStart(3, "0");
  return `${hh}:${mm}:${ss}.${ms}`;
}

function extractLabel(ev: TraceEvent): string | null {
  const p = ev.payload as Record<string, unknown> | null;
  if (p && typeof p === "object" && typeof p.label === "string") return p.label;
  return null;
}

function extractPhase(ev: TraceEvent): string | null {
  const p = ev.payload as Record<string, unknown> | null;
  if (p && typeof p === "object" && typeof p.phase === "string") return p.phase;
  return null;
}

function summarisePayload(payload: unknown): string {
  if (payload == null) return "";
  if (typeof payload === "string") return payload;
  if (typeof payload === "number" || typeof payload === "boolean") return String(payload);
  if (typeof payload === "object") {
    const p = payload as Record<string, unknown>;
    // Pull common pentester-relevant fields up front; the rest gets
    // a compact JSON tail.
    const head: string[] = [];
    if (typeof p.method === "string") head.push(`${p.class ?? "?"}.${p.method}`);
    if (Array.isArray(p.args)) head.push(`args=${stringifyArgs(p.args)}`);
    if (typeof p.return !== "undefined") head.push(`return=${stringifyValue(p.return)}`);
    if (typeof p.error === "string") head.push(`error=${p.error}`);
    if (typeof p.message === "string") head.push(p.message);
    if (typeof p.overloads === "number") head.push(`overloads=${p.overloads}`);
    if (head.length > 0) return head.join("  ");
    try {
      return JSON.stringify(p);
    } catch {
      return String(p);
    }
  }
  return String(payload);
}

function stringifyArgs(args: unknown[]): string {
  if (args.length === 0) return "()";
  return `(${args.map(stringifyValue).join(", ")})`;
}

function stringifyValue(v: unknown): string {
  if (typeof v === "string") {
    return v.length > 80 ? `${v.slice(0, 77)}…` : v;
  }
  if (v == null || typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    const s = JSON.stringify(v);
    return s.length > 80 ? `${s.slice(0, 77)}…` : s;
  } catch {
    return String(v);
  }
}
