/**
 * Hook Lab — HookStatsPanel (sub-step 4.6).
 *
 * Polls ``/api/frida/sessions/:id/hooks`` every ``REFRESH_MS`` (2.5s)
 * and renders one row per ``(class, method)`` the trace ring has
 * observed: hits, last seen, and the top-N return values seen.
 *
 * This is a *summary* view — the canonical event stream stays in the
 * Trace panel; this panel is for "is the hook firing? what does it
 * return on average?" at a glance. Polling (rather than a parallel
 * WS) is a deliberate choice: the backend aggregator is pure over
 * ``session.events()``, so a 2.5s refresh keeps the panel cheap and
 * lets it survive a paused trace without going stale (a paused trace
 * only pauses appending to the visible event list — the ring keeps
 * receiving events).
 *
 * Empty / disabled / error states each get their own copy so the
 * operator never sees a blank panel without a hint why.
 */
import { useCallback, useEffect, useState } from "react";
import { getSessionHooks, type HookStat } from "../api/frida";

const REFRESH_MS = 2500;
const TOP_RETURNS_VISIBLE = 3;

type Props = {
  /** ``null`` = no active session: the panel renders an empty state
   *  rather than throwing. */
  sessionId: string | null;
};

export function HookStatsPanel({ sessionId }: Props) {
  const [hooks, setHooks] = useState<HookStat[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchOnce = useCallback(async () => {
    if (!sessionId) {
      setHooks(null);
      setError(null);
      return;
    }
    const r = await getSessionHooks(sessionId);
    if (r.ok) {
      setHooks(r.data.hooks);
      setError(null);
    } else {
      // 404 here means the session was just detached out from under us;
      // surface gracefully rather than as a red error.
      if (r.status === 404) {
        setHooks([]);
        setError(null);
      } else {
        setError(r.error);
      }
    }
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await fetchOnce();
      if (cancelled) return;
    })();
    if (!sessionId) return undefined;
    const id = window.setInterval(fetchOnce, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [fetchOnce, sessionId]);

  return (
    <div className="pane-scroll hookstats-panel">
      <header className="pane-head">
        <h2>Hooks</h2>
        <span className="pane-head-actions">
          <span className="muted small">
            {sessionId == null
              ? "no active session"
              : hooks == null
                ? "loading…"
                : `${hooks.length} hooked`}
          </span>
          <button
            type="button"
            className="ghost-mini"
            onClick={() => void fetchOnce()}
            title="Refresh now"
            disabled={!sessionId}
          >
            ↻
          </button>
        </span>
      </header>

      {error && (
        <p className="hook-error" role="alert">
          {error}
        </p>
      )}

      {sessionId == null && (
        <p className="muted small">
          Select an active session in the list above (or Inject a new hook) to
          see per-(class.method) hit counts and recent return values.
        </p>
      )}

      {sessionId != null && hooks != null && hooks.length === 0 && (
        <p className="muted small">
          No hook activity yet. Wait for the target app to call the hooked
          method, or check the Trace panel for setup errors.
        </p>
      )}

      {hooks != null && hooks.length > 0 && (
        <ul className="hookstats-list">
          {hooks.map((h) => (
            <li key={`${h.class}::${h.method}`} className="hookstats-row">
              <div className="hookstats-head">
                <span className="hookstats-method" title={`${h.class}.${h.method}`}>
                  <span className="hookstats-class">{h.class}</span>
                  <span className="hookstats-dot">.</span>
                  <span className="hookstats-method-name">{h.method}</span>
                </span>
                <span className="hookstats-hits">{h.hits} hits</span>
              </div>
              <div className="hookstats-meta">
                {h.template_id && (
                  <span className="hookstats-template" title={`template: ${h.template_id}`}>
                    {h.template_id}
                  </span>
                )}
                <span className="hookstats-lastseen">
                  {formatLastSeen(h.last_seen_ts)}
                </span>
              </div>
              {h.top_returns.length > 0 && (
                <ul className="hookstats-returns">
                  {h.top_returns.slice(0, TOP_RETURNS_VISIBLE).map((r, i) => (
                    <li key={i} className="hookstats-return">
                      <span className="hookstats-return-count">×{r.count}</span>
                      <span className="hookstats-return-value" title={r.value}>
                        {r.value}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function formatLastSeen(ts: number | null): string {
  if (ts == null) return "never";
  const now = Date.now() / 1000;
  const delta = Math.max(0, now - ts);
  if (delta < 1) return "just now";
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  return `${Math.floor(delta / 3600)}h ago`;
}
