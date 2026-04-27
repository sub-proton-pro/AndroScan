/**
 * Hook Lab — ScopeInspectorPanel (sub-step 4.6).
 *
 * Polls ``/api/frida/sessions/:id/scope`` every ``REFRESH_MS`` (2.5s)
 * and renders one collapsible card per ``(class, method)`` watched by
 * a ``scope_inspector`` template. Each card shows:
 *
 *   * Last entry  — args + ``this_class`` + ``this_fields`` snapshot.
 *   * Last exit   — return value + ``this_fields`` post-call snapshot.
 *
 * The "diff" between entry and exit ``this_fields`` is the actual
 * pentest signal — we surface both side-by-side and highlight the
 * keys that changed.
 *
 * Why no Monaco: the data we render is shallow (string-keyed maps of
 * stringified field values), and Monaco's CDN dependency
 * (``KNOWN_ISSUES.md`` ISSUE-010) is enough air-gap friction that we
 * deliberately keep this panel native. ``<details>``/``<summary>``
 * gives us free disclosure semantics + keyboard support.
 *
 * The panel filters at the *aggregator* level (see
 * ``_summarize_scope_events`` in ``frida_routes.py``): only events
 * carrying a ``this_fields`` block — i.e. emitted by the
 * ``scope_inspector`` template — show up here. A session running
 * ``entry_exit_log`` (which doesn't capture fields) renders as an
 * empty state with copy that points the operator at the right
 * template.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getSessionScope,
  type ScopeSnapshot,
  type ScopeSnapshotEntry,
  type ScopeSnapshotExit,
} from "../api/frida";

const REFRESH_MS = 2500;

type Props = {
  sessionId: string | null;
};

export function ScopeInspectorPanel({ sessionId }: Props) {
  const [snapshots, setSnapshots] = useState<ScopeSnapshot[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchOnce = useCallback(async () => {
    if (!sessionId) {
      setSnapshots(null);
      setError(null);
      return;
    }
    const r = await getSessionScope(sessionId);
    if (r.ok) {
      setSnapshots(r.data.snapshots);
      setError(null);
    } else if (r.status === 404) {
      setSnapshots([]);
      setError(null);
    } else {
      setError(r.error);
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
    <div className="pane-scroll scope-panel">
      <header className="pane-head">
        <h2>Scope</h2>
        <span className="pane-head-actions">
          <span className="muted small">
            {sessionId == null
              ? "no active session"
              : snapshots == null
                ? "loading…"
                : `${snapshots.length} captured`}
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
          Select an active session in the list above to see captured field
          snapshots.
        </p>
      )}

      {sessionId != null && snapshots != null && snapshots.length === 0 && (
        <p className="muted small">
          No scope snapshots yet. The Scope Inspector reads <code>this_fields</code>
          payloads — Inject a hook with the <strong>Scope inspector</strong> template
          (rather than <em>entry/exit log</em>) to capture instance-field state.
        </p>
      )}

      {snapshots != null && snapshots.length > 0 && (
        <ul className="scope-list">
          {snapshots.map((snap, idx) => (
            <ScopeCard key={`${snap.class}::${snap.method}`} snap={snap} defaultOpen={idx === 0} />
          ))}
        </ul>
      )}
    </div>
  );
}

type CardProps = {
  snap: ScopeSnapshot;
  defaultOpen: boolean;
};

function ScopeCard({ snap, defaultOpen }: CardProps) {
  const changedKeys = useMemo(
    () => diffChangedKeys(snap.last_entry?.this_fields, snap.last_exit?.this_fields),
    [snap.last_entry, snap.last_exit],
  );
  return (
    <li className="scope-card">
      <details open={defaultOpen}>
        <summary className="scope-summary">
          <span className="scope-method" title={`${snap.class}.${snap.method}`}>
            <span className="scope-class">{snap.class}</span>
            <span className="scope-dot">.</span>
            <span className="scope-method-name">{snap.method}</span>
          </span>
          <span className="scope-meta">
            {changedKeys.length > 0 && (
              <span className="scope-mutated" title="Fields whose value changed between entry and exit">
                {changedKeys.length} mutated
              </span>
            )}
            <span className="muted small">{formatScopeTs(snap)}</span>
          </span>
        </summary>
        <div className="scope-body">
          {snap.last_entry && (
            <ScopeSide title="entry" entry={snap.last_entry} changedKeys={changedKeys} />
          )}
          {snap.last_exit && (
            <ScopeExitSide title="exit" exit={snap.last_exit} changedKeys={changedKeys} />
          )}
          {snap.last_entry == null && snap.last_exit == null && (
            <p className="muted small">No entry/exit captured for this method yet.</p>
          )}
        </div>
      </details>
    </li>
  );
}

function ScopeSide({
  title,
  entry,
  changedKeys,
}: {
  title: string;
  entry: ScopeSnapshotEntry;
  changedKeys: string[];
}) {
  return (
    <section className="scope-side">
      <h4 className="scope-side-title">
        {title}
        <span className="muted small">
          {entry.ts != null ? new Date(entry.ts * 1000).toLocaleTimeString() : ""}
        </span>
      </h4>
      {entry.this_class && (
        <div className="scope-line">
          <span className="scope-key">this</span>
          <span className="scope-value">{entry.this_class}</span>
        </div>
      )}
      {entry.args != null && (
        <div className="scope-line">
          <span className="scope-key">args</span>
          <span className="scope-value">
            {entry.args.length === 0 ? "(none)" : `[${entry.args.length}]`}
          </span>
        </div>
      )}
      {entry.args != null && entry.args.length > 0 && (
        <ol className="scope-args">
          {entry.args.map((a, i) => (
            <li key={i} className="scope-arg">
              <span className="scope-key">#{i}</span>
              <span className="scope-value">{a}</span>
            </li>
          ))}
        </ol>
      )}
      <FieldsTable fields={entry.this_fields} changedKeys={changedKeys} side="entry" />
    </section>
  );
}

function ScopeExitSide({
  title,
  exit: exitSide,
  changedKeys,
}: {
  title: string;
  exit: ScopeSnapshotExit;
  changedKeys: string[];
}) {
  return (
    <section className="scope-side">
      <h4 className="scope-side-title">
        {title}
        <span className="muted small">
          {exitSide.ts != null ? new Date(exitSide.ts * 1000).toLocaleTimeString() : ""}
        </span>
      </h4>
      {exitSide.return != null && (
        <div className="scope-line">
          <span className="scope-key">return</span>
          <span className="scope-value">{exitSide.return}</span>
        </div>
      )}
      <FieldsTable fields={exitSide.this_fields} changedKeys={changedKeys} side="exit" />
    </section>
  );
}

function FieldsTable({
  fields,
  changedKeys,
  side,
}: {
  fields: Record<string, string>;
  changedKeys: string[];
  side: "entry" | "exit";
}) {
  const entries = useMemo(
    () => Object.entries(fields).sort((a, b) => a[0].localeCompare(b[0])),
    [fields],
  );
  if (entries.length === 0) {
    return (
      <p className="muted small scope-empty">
        no instance fields captured
      </p>
    );
  }
  const changedSet = new Set(changedKeys);
  return (
    <ul className="scope-fields">
      {entries.map(([k, v]) => {
        const changed = changedSet.has(k);
        return (
          <li
            key={k}
            className={`scope-field ${changed ? "scope-field-changed" : ""}`}
            title={
              changed
                ? `value differs between entry and exit (${side} side shown)`
                : undefined
            }
          >
            <span className="scope-key">{k}</span>
            <span className="scope-value">{v}</span>
          </li>
        );
      })}
    </ul>
  );
}

function diffChangedKeys(
  before: Record<string, string> | undefined,
  after: Record<string, string> | undefined,
): string[] {
  if (!before || !after) return [];
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  const out: string[] = [];
  keys.forEach((k) => {
    if ((before[k] ?? null) !== (after[k] ?? null)) out.push(k);
  });
  return out.sort();
}

function formatScopeTs(snap: ScopeSnapshot): string {
  const tsCandidates: number[] = [];
  if (snap.last_entry?.ts != null) tsCandidates.push(snap.last_entry.ts);
  if (snap.last_exit?.ts != null) tsCandidates.push(snap.last_exit.ts);
  if (tsCandidates.length === 0) return "never";
  const ts = Math.max(...tsCandidates);
  const delta = Math.max(0, Date.now() / 1000 - ts);
  if (delta < 1) return "just now";
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  return `${Math.floor(delta / 3600)}h ago`;
}
