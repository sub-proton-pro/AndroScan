/**
 * Hook Lab — FridaSessionsList.
 *
 * Pulls ``GET /api/frida/sessions`` on mount + every ``REFRESH_MS``
 * and renders one row per active session with: template id, package,
 * pid, total event count, dropped count (ring + WS + persist), and a
 * Detach button (``DELETE /api/frida/sessions/:id``).
 *
 * Click-to-select calls back to the parent so it can wire the
 * matching trace into the right pane (one trace at a time — multi-
 * pane traces are explicitly out of v1's scope per DEC-023).
 */
import { useCallback, useEffect, useState } from "react";
import {
  deleteSession,
  listSessions,
  type FridaSessionInfo,
} from "../api/frida";
import { IconChevronRight } from "./Icons";

const REFRESH_MS = 2500;

type Props = {
  /** Currently selected session (highlighted). The parent owns this
   *  state because the trace panel needs to render it too. */
  selectedSessionId: string | null;
  onSelect: (info: FridaSessionInfo) => void;
  /** Bumped after Inject / Detach so the list re-fetches eagerly
   *  instead of waiting for the next polling tick. */
  refreshTick: number;
  onDetached: (sessionId: string) => void;
  /** Optional: render a collapse button in the header so the parent can
   *  shrink the right column to a vertical rail. */
  onCollapse?: () => void;
};

export function FridaSessionsList({
  selectedSessionId,
  onSelect,
  refreshTick,
  onDetached,
  onCollapse,
}: Props) {
  const [sessions, setSessions] = useState<FridaSessionInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const fetchOnce = useCallback(async () => {
    const r = await listSessions();
    if (r.ok) {
      setSessions(r.data.sessions);
      setError(null);
    } else {
      setError(r.error);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await fetchOnce();
      if (cancelled) return;
    })();
    const id = window.setInterval(fetchOnce, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [fetchOnce, refreshTick]);

  const onDetach = useCallback(
    async (sessionId: string) => {
      setBusy(sessionId);
      const r = await deleteSession(sessionId);
      setBusy(null);
      if (r.ok) {
        onDetached(sessionId);
        await fetchOnce();
      } else {
        setError(r.error);
      }
    },
    [onDetached, fetchOnce],
  );

  return (
    <div className="pane-scroll">
      <header className="pane-head">
        <h2>Sessions</h2>
        <span className="pane-head-actions">
          <span className="muted small">
            {sessions ? `${sessions.length} active` : "loading…"}
          </span>
          <button
            type="button"
            className="ghost-mini"
            onClick={fetchOnce}
            title="Refresh now"
          >
            ↻
          </button>
          {onCollapse && (
            <button
              type="button"
              className="ghost-mini icon-btn"
              onClick={onCollapse}
              title="Collapse sessions panel"
              aria-label="Collapse sessions panel"
            >
              <IconChevronRight />
            </button>
          )}
        </span>
      </header>

      {error && (
        <p className="hook-error" role="alert">
          {error}
        </p>
      )}

      {sessions && sessions.length === 0 && (
        <p className="muted small">
          No active sessions. Inject a hook from the builder to start one.
        </p>
      )}

      <ul className="frida-sessions-list">
        {sessions?.map((s) => {
          const isSelected = s.session_id === selectedSessionId;
          const isBusy = busy === s.session_id;
          return (
            <li
              key={s.session_id}
              className={`frida-session-row ${isSelected ? "frida-session-selected" : ""}`}
              onClick={() => onSelect(s)}
            >
              <div className="frida-session-head">
                <span className="frida-session-template">{s.template_id ?? "(no template id)"}</span>
                <span className="muted small">{s.package}</span>
              </div>
              <div className="frida-session-meta">
                {s.pid != null && <span>pid {s.pid}</span>}
                <span>events {s.total_events}</span>
                {(s.dropped > 0 || s.persist_dropped > 0) && (
                  <span className="frida-session-drop">
                    drop ring {s.dropped}
                    {s.persist_dropped > 0 && `, persist ${s.persist_dropped}`}
                  </span>
                )}
                {s.persist_path && <span title={s.persist_path}>persist on</span>}
                {!s.persist_path && <span className="muted">persist off</span>}
                {s.detached && <span className="frida-session-detached">detached</span>}
              </div>
              <div className="frida-session-actions">
                <button
                  type="button"
                  className="ghost-mini"
                  disabled={isBusy || s.detached}
                  onClick={(e) => {
                    e.stopPropagation();
                    void onDetach(s.session_id);
                  }}
                  title="Detach this session and flush its JSONL trace"
                >
                  {isBusy ? "Detaching…" : "Detach"}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
