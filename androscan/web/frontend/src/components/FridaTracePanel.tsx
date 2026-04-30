/**
 * Hook Lab — FridaTracePanel.
 *
 * Owns the live-trace UI for a single ``FridaSession``:
 *
 *   * subscribes to ``/ws/frida/sessions/:id/trace`` via
 *     :func:`useFridaTrace` (api/frida.ts);
 *   * renders events newest-first (``tail -f`` mental model is
 *     intentionally inverted because operators eyeball the most
 *     recent ``forced`` / ``error`` events while older ``entry`` /
 *     ``exit`` chatter scrolls off below);
 *   * caps the rendered window at :data:`RENDER_CAP` events with a
 *     "Show older events" button at the bottom of the list — bounds
 *     the worst-case DOM size at ``RENDER_CAP * (1 + expanded-rows)``
 *     even when the client buffer is full (``maxBuffer = 2000`` per
 *     :func:`useFridaTrace`);
 *   * filters by substring against ``label`` / ``phase`` / ``message``
 *     (the keys ``send`` payloads use — see
 *     ``frida_hooks/entry_exit_log.py``); filtering applies to the
 *     full client buffer, then the cap is applied to the result so
 *     filter matches in older events still surface;
 *   * each row is a single grid row: chevron + ts + kind + phase +
 *     label + a compact unquoted-key JSON serialisation of the full
 *     payload (``{label:"x",phase:"entry",class:"...",method:"...",
 *     args:[...]}``). The chevron toggles the payload's wrap mode,
 *     not its content — collapsed = ``white-space: nowrap`` +
 *     ``text-overflow: ellipsis`` so the row is exactly one line and
 *     long payloads get a CSS-native ``…``; expanded = ``white-space:
 *     normal`` so the same JSON wraps onto as many lines as needed.
 *     One-row collapsed + N-row expanded comes from the same DOM
 *     (the JSON is always present in full); no separate ``<pre>``
 *     block, no per-template summariser. Operators see the entire
 *     payload's keys/values immediately on hover (``title={text}``
 *     surfaces the full JSON in the browser tooltip even when the
 *     row is clipped).
 *
 *     "Unquoted keys" matches the user's sketch (``{key:"v"}`` rather
 *     than ``{"key":"v"}``) and saves ~2 chars per key, which matters
 *     for the one-line collapsed preview where ellipsis-truncation is
 *     the only thing standing between a useful preview and a
 *     ``{"label"…`` cliff. Identifier-only keys are unquoted; any key
 *     with a non-identifier character (``"class.name"``, ``"foo bar"``,
 *     etc.) falls back to JSON-quoted form for unambiguity. The inline
 *     tokeniser handles both forms;
 *   * pause / resume / clear buttons (uses the same hook's APIs);
 *   * Export button: anchor with ``download`` attribute pointing at
 *     ``/api/frida/sessions/:id/export`` so the browser streams the
 *     JSONL straight to disk;
 *   * "Newest ↑" button in the toolbar — enabled when the user has
 *     scrolled away from the top, scrolls back to ``scrollTop = 0``
 *     where the freshest event sits.
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

// Render cap — max events kept in the DOM at once. The trace itself
// keeps growing (client-side ``maxBuffer`` is 2000); only the rendered
// window is capped. "Show older events" extends this in 500-event
// increments. 500 is a sweet spot:
//   * collapsed (default) row = ~1 line → ~500 nodes (negligible);
//   * fully expanded JSON row = ~10 lines → ~5,000 nodes worst case
//     (browser handles this cleanly even on slow machines);
//   * matches an operator's "scan recent activity" expectation —
//     anything older lives in the on-disk JSONL anyway, which the
//     Export button delivers in one click.
const RENDER_CAP = 500;

// Sticky-top scroll threshold. Anything within this distance of the
// very top counts as "the user is reading the newest events" and we
// auto-pin to ``scrollTop = 0`` when new events arrive (so the freshest
// event stays visible without being overrun). Past this threshold the
// user is reading older events and we don't disrupt their position
// (browser-native scroll anchoring keeps the visible content stable
// when new rows are prepended above).
const STICKY_TOP_PX = 16;

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

  // Render cap + "Show older" cursor. ``extraOlder`` is bumped in
  // ``RENDER_CAP``-sized chunks each time the operator clicks "Show
  // older events"; the visible window is the last
  // ``RENDER_CAP + extraOlder`` events of the filtered set.
  //
  // Reset to zero whenever the session changes (different trace) or
  // the filter changes (different event subset, no carry-over makes
  // sense — the operator's "I want to see older matches of THIS
  // search" intent restarts from the top of the new result set).
  const [extraOlder, setExtraOlder] = useState(0);
  useEffect(() => {
    setExtraOlder(0);
  }, [sessionId, filter]);

  // Per-row expanded state. Set of stable event keys (see
  // :func:`eventKey`). Default-collapsed: a row only appears in this
  // set if the operator has clicked its chevron. Reset when the
  // session changes — different trace, different events, stale keys
  // would just bloat the set forever.
  const [expandedSet, setExpandedSet] = useState<Set<string>>(new Set());
  useEffect(() => {
    setExpandedSet(new Set());
  }, [sessionId]);
  const toggleExpanded = useCallback((key: string) => {
    setExpandedSet((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  // Newest-first visible window. ``slice(-N)`` takes the last N
  // (chronologically newest) of the filtered set, then ``reverse()``
  // puts the freshest event at array index 0 — which renders at the
  // top of the list. ``toReversed()`` would be cleaner but we want
  // older browser support.
  const renderCap = RENDER_CAP + extraOlder;
  const visible = useMemo(() => {
    const tail = filtered.length > renderCap ? filtered.slice(-renderCap) : filtered;
    // ``slice().reverse()`` to avoid mutating the memoised filtered
    // array (the filter useMemo's identity depends on it being
    // stable across renders that don't change the inputs).
    return tail.slice().reverse();
  }, [filtered, renderCap]);
  const olderInBuffer = Math.max(0, filtered.length - renderCap);

  // Sticky-top scroll behaviour: when the user is at (or within
  // ``STICKY_TOP_PX`` of) the top, we auto-pin ``scrollTop = 0`` on
  // every visible-window change so the freshest event stays in view
  // — browser-native scroll anchoring would otherwise clamp the
  // viewport to the previous-newest row when a new row is prepended.
  // When the user has scrolled away the pin is disabled so they can
  // read older events without being yanked back. ``stickyTopRef`` is
  // a ref (not state) to avoid re-renders on every scrollbar nudge;
  // ``isScrolledAway`` IS state because it gates the visibility of
  // the "Newest ↑" toolbar button.
  const listRef = useRef<HTMLDivElement | null>(null);
  const stickyTopRef = useRef<boolean>(true);
  const [isScrolledAway, setIsScrolledAway] = useState(false);

  const onScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const atTop = el.scrollTop < STICKY_TOP_PX;
    stickyTopRef.current = atTop;
    setIsScrolledAway((prev) => (prev !== !atTop ? !atTop : prev));
  }, []);

  // Re-pin to ``scrollTop = 0`` whenever the visible window changes
  // AND the user is reading the top. Keyed on ``visible.length`` so
  // the effect fires on new events (length grows) and on filter
  // narrowing (length shrinks); also keyed on ``extraOlder`` so a
  // "Show older" click that doesn't change ``visible.length`` (e.g.
  // when filtered set is smaller than the cap) still triggers a
  // sanity re-pin.
  useEffect(() => {
    if (!stickyTopRef.current) return;
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = 0;
  }, [visible.length, extraOlder]);

  const jumpToNewest = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = 0;
    // Setting scrollTop synchronously fires the scroll handler which
    // updates stickyTopRef + isScrolledAway, but that handler is
    // debounced through React's state batching — set the flags
    // explicitly so the next render reflects "we're at top" without
    // waiting for the scroll event to round-trip.
    stickyTopRef.current = true;
    setIsScrolledAway(false);
  }, []);

  const showOlder = useCallback(() => {
    setExtraOlder((c) => c + RENDER_CAP);
  }, []);

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
            <button
              type="button"
              className="ghost-mini trace-jump-newest"
              onClick={jumpToNewest}
              disabled={!isScrolledAway}
              title={
                isScrolledAway
                  ? "Scroll back to the newest event at the top of the list"
                  : "Already at newest"
              }
            >
              Newest ↑
            </button>
            <span className="trace-counter muted small">
              {/* ``visible / filtered``: how many events the DOM is
               *  rendering vs. how many the filter currently matches
               *  in the full client buffer. The denominator no longer
               *  reflects the WHOLE buffer (``trace.events.length``)
               *  because with a filter applied + render cap that
               *  number was misleading — operators care about
               *  "rendered now" / "matches my filter", not the raw
               *  ring-buffer fill level. */}
              {visible.length} / {filtered.length}
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
            {visible.map((ev) => {
              const key = eventKey(ev);
              return (
                <TraceRow
                  key={key}
                  event={ev}
                  expanded={expandedSet.has(key)}
                  onToggle={() => toggleExpanded(key)}
                />
              );
            })}
            {olderInBuffer > 0 && (
              <button
                type="button"
                className="trace-show-older"
                onClick={showOlder}
                title={`Render the next ${RENDER_CAP} older events from the buffer (still ${olderInBuffer} below the cap)`}
              >
                Show {Math.min(RENDER_CAP, olderInBuffer)} older event
                {Math.min(RENDER_CAP, olderInBuffer) === 1 ? "" : "s"}
                {olderInBuffer > RENDER_CAP
                  ? ` (${olderInBuffer - RENDER_CAP} more in buffer after this)`
                  : ""}
                {" — fresher events stay at the top, older below"}
              </button>
            )}
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
//
// Single grid row: chevron + ts + kind + phase + label + compact JSON
// preview of the full payload. The JSON is always rendered in full;
// the chevron toggles a CSS modifier (``trace-row-expanded``) on the
// row that switches the payload column's wrap mode between
// ``nowrap+ellipsis`` (collapsed → one-line preview, browser shows ``…``
// when clipped) and ``normal+overflow-wrap:anywhere`` (expanded → wraps
// onto N lines). Same DOM both states, only CSS differs — re-renders
// cost a single boolean prop change per row, not a Set recomputation,
// which matters because the visible window can hold up to
// ``RENDER_CAP + extraOlder`` rows.
//
// ``title={compact}`` surfaces the full JSON in a browser tooltip on
// hover even when the row is collapsed-and-clipped, so the operator
// can preview a long payload without committing to expanding it.

type TraceRowProps = {
  event: TraceEvent;
  expanded: boolean;
  onToggle: () => void;
};

function TraceRow({ event, expanded, onToggle }: TraceRowProps) {
  const ts = formatTs(event.ts);
  const label = extractLabel(event);
  const phase = extractPhase(event);
  const compact = useMemo(() => safeCompactPayload(event.payload), [event.payload]);
  return (
    <div
      className={`trace-row trace-row-${event.kind}${expanded ? " trace-row-expanded" : ""}`}
    >
      <div className="trace-row-meta">
        <button
          type="button"
          className="trace-payload-toggle"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-label={expanded ? "Collapse payload" : "Expand payload"}
          title={expanded ? "Wrap to one line" : "Wrap to multiple lines"}
        >
          {/* Plain unicode triangles instead of an Icon component:
           *  these only ever appear inside the trace pane and the
           *  shapes match the ``<details>`` element disclosure
           *  triangle conventions operators see in browser devtools,
           *  so muscle memory carries over. */}
          {expanded ? "▾" : "▸"}
        </button>
        <span className="trace-ts" title={String(event.ts)}>
          {ts}
        </span>
        <span className={`trace-kind trace-kind-${event.kind}`}>{event.kind}</span>
        {phase && <span className={`trace-phase trace-phase-${phase}`}>{phase}</span>}
        {label && <span className="trace-label">{label}</span>}
        <span className="trace-payload" title={compact}>
          <JsonView text={compact} />
        </span>
      </div>
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

// Compact unquoted-key JSON serialiser used by the trace-pane preview.
//
// Output shape: ``{label:"x",phase:"entry",class:"...",method:"...",
// args:["..."],return:1234}`` — identifier-only keys are left
// unquoted (saves ~2 chars per key vs. strict JSON, which matters for
// the one-line collapsed preview that gets ellipsis-clipped); keys
// with a non-identifier character (``"class.name"``, keys with
// spaces, etc.) fall back to JSON-quoted form for unambiguity.
//
// String values stay quoted (they're the only token where quotes are
// load-bearing — without them ``key:value`` would be ambiguous between
// a string-valued key and a bool/number/null-valued one). Numbers,
// booleans, and ``null`` use their native string form. Arrays render
// the same way they would in JSON (``[v,v,v]``). Nested objects
// recurse.
//
// ``undefined`` is rendered as the literal string ``undefined`` —
// shouldn't reach us from the backend (Python's ``json.dumps`` doesn't
// produce it) but we handle it defensively in case a future client-side
// payload-fixup step introduces it.
function compactJsonText(v: unknown): string {
  if (v === null) return "null";
  if (v === undefined) return "undefined";
  if (typeof v === "string") return JSON.stringify(v);
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return `[${v.map(compactJsonText).join(",")}]`;
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    return `{${Object.entries(o)
      .map(([k, x]) => `${jsonKey(k)}:${compactJsonText(x)}`)
      .join(",")}}`;
  }
  // Symbol / function / etc. — JSON.stringify would return undefined
  // for these. Fall back to the value's String() form so the row
  // renders SOMETHING instead of a confusing empty payload column.
  return JSON.stringify(v) ?? String(v);
}

// Identifier-safe keys (matches what's normally allowed for unquoted
// object literal property names in JS — letters, digits, ``_``, ``$``;
// can't start with a digit) get rendered without quotes. Anything else
// — including the empty string, keys containing dots, spaces, dashes,
// or any non-ASCII character — falls back to ``JSON.stringify(k)``
// which gives us the standard ``"..."`` form with proper escaping.
function jsonKey(k: string): string {
  return /^[A-Za-z_$][\w$]*$/.test(k) ? k : JSON.stringify(k);
}

function safeCompactPayload(value: unknown): string {
  // Two layers of defence: the first try block catches anything the
  // recursive walker chokes on (cycles → ``InternalError: too much
  // recursion``, exotic objects whose property access throws, etc.);
  // the second falls back to ``JSON.stringify`` which has its own
  // cycle detection. Both failing simultaneously would be a very
  // sick payload — coerce to ``String`` so the row at least renders
  // an opaque marker instead of throwing during render.
  try {
    return compactJsonText(value);
  } catch {
    try {
      return JSON.stringify(value) ?? String(value);
    } catch {
      return String(value);
    }
  }
}

// ---------------------------------------------------------------------------
// eventKey — stable React key + expanded-set key for one TraceEvent.
//
// The full client buffer is append-only at the new end and evict-only
// at the old end (ring semantics), so for an event currently in the
// buffer its position can shift but its CONTENT is stable. We need a
// key that survives those position shifts so the operator's per-row
// expand state doesn't drift to a different event when new events
// arrive.
//
// ``ts`` is from Python's ``time.time()`` (sub-millisecond precision),
// so collisions between distinct events are rare. We add ``kind`` and
// (for object payloads) ``phase`` to disambiguate the rare same-ts
// case (e.g. an entry/exit pair on a method that returns inside a
// single tick of the system clock). Two genuinely identical events
// would still collide, but that's harmless for the React-key use
// (``key`` only needs to be unique within siblings; the worst case
// is React reusing one DOM node which is fine for our render).
function eventKey(ev: TraceEvent): string {
  const p = ev.payload as Record<string, unknown> | null;
  const phase =
    p && typeof p === "object" && typeof p.phase === "string" ? p.phase : "";
  return `${ev.ts}-${ev.kind}-${phase}`;
}

// ---------------------------------------------------------------------------
// JsonView — syntax-highlight a JSON-ish text string as colored spans.
//
// Takes pre-serialised text (not a raw value) because the trace panel
// already needs the same string for the parent ``<span>``'s ``title``
// attribute (tooltip-on-hover for clipped rows) and recomputing the
// serialisation here would mean two ``compactJsonText`` calls per row
// for nothing — better to serialise once in ``TraceRow`` and pass the
// string through.
//
// Why this lives inline (no dependency): JSON is regular enough to
// tokenise with one regex pass; the alternative (Prism, Highlight.js,
// react-syntax-highlighter) would have added 100+ KB of bundle for
// one use case. Reusing Monaco was rejected because spawning a Monaco
// editor instance per row spawns Web Workers per row — catastrophic
// memory cost for the 500 rows the trace pane can hold.
//
// Tokens emitted: ``key`` (object property name, quoted OR unquoted
// identifier), ``string`` (non-key string value), ``number``,
// ``bool``, ``null``, ``punct`` (braces / brackets / colons / commas),
// ``ws`` (whitespace). Colors live in App.css (``.json-tok-*``).

type JsonViewProps = { text: string };

function JsonView({ text }: JsonViewProps) {
  const tokens = useMemo(() => tokenizeJson(text), [text]);
  return (
    <>
      {tokens.map((t, i) => (
        <span key={i} className={`json-tok json-tok-${t.kind}`}>
          {t.text}
        </span>
      ))}
    </>
  );
}

type JsonTokKind =
  | "key"
  | "string"
  | "number"
  | "bool"
  | "null"
  | "punct"
  | "ws";
type JsonTok = { kind: JsonTokKind; text: string };

// Single-pass tokeniser. Two key alternatives at the front — quoted
// string-followed-by-colon (strict-JSON style) and bare-identifier-
// followed-by-colon (our compact serialiser's output style) — both
// gated on a ``(?=\s*:)`` lookahead so we discriminate keys from
// equivalent-shaped value tokens (string value vs. unquoted true/
// false/null which share the identifier shape but never appear before
// a colon in well-formed payloads).
//
// Ordering inside the alternation matters: longer / more-specific
// patterns first so JavaScript's regex engine doesn't match a generic
// string or identifier before the key-with-lookahead can fire.
//
// Anchored to the output of ``compactJsonText`` (no whitespace, no
// indentation), but tolerant of pretty-printed JSON if a future caller
// passes that — the ``\s+`` alternative consumes whitespace, and the
// quoted-key alternative still lights up keys in ``{"k": "v"}`` form.
const JSON_TOKEN_RE =
  /"(?:[^"\\]|\\.)*"(?=\s*:)|[A-Za-z_$][\w$]*(?=\s*:)|"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\btrue\b|\bfalse\b|\bnull\b|[{}\[\],:]|\s+/g;

function tokenizeJson(s: string): JsonTok[] {
  const out: JsonTok[] = [];
  // ``g`` flag means we maintain ``lastIndex`` across iterations;
  // reset on entry so we don't carry state between calls (the
  // module-level constant is fine because we reset explicitly).
  JSON_TOKEN_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  let last = 0;
  while ((m = JSON_TOKEN_RE.exec(s)) !== null) {
    if (m.index > last) {
      // Anything between matches shouldn't happen for our serialiser's
      // output, but if it ever does (exotic edge case, future caller
      // passes free-form text) surface it as plain punctuation so the
      // row renders intact instead of swallowing characters.
      out.push({ kind: "punct", text: s.slice(last, m.index) });
    }
    const tok = m[0];
    // Both key alternatives (quoted or bare identifier) require the
    // ``(?=\s*:)`` lookahead to have matched. We re-test it explicitly
    // here to label the token — cheaper than threading capture groups
    // through the regex and clearer than reading ``m`` indices.
    const followedByColon = /^\s*:/.test(s.slice(m.index + tok.length));
    if (tok.startsWith('"')) {
      out.push({ kind: followedByColon ? "key" : "string", text: tok });
    } else if (tok === "true" || tok === "false") {
      out.push({ kind: "bool", text: tok });
    } else if (tok === "null") {
      out.push({ kind: "null", text: tok });
    } else if (/^-?\d/.test(tok)) {
      out.push({ kind: "number", text: tok });
    } else if (/^[A-Za-z_$]/.test(tok)) {
      // Bare identifier — the lookahead must have fired (otherwise the
      // alternative wouldn't have matched at all), so this is always a
      // key in our compact format.
      out.push({ kind: "key", text: tok });
    } else if (/^\s+$/.test(tok)) {
      out.push({ kind: "ws", text: tok });
    } else {
      out.push({ kind: "punct", text: tok });
    }
    last = JSON_TOKEN_RE.lastIndex;
  }
  if (last < s.length) {
    out.push({ kind: "punct", text: s.slice(last) });
  }
  return out;
}
