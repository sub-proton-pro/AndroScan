/**
 * Trace mode for the Lab tab — the headline UI for Phase 10
 * (sub-step 10.7). The 10.6 placeholder shipped a status-only view;
 * this rewrite is the real surface:
 *
 *   1. Top-of-pane form: smali entry signature input + hops stepper
 *      (1..6 clamped) + Build / Force-rebuild buttons.
 *   2. Result region: ``BehaviorAnchorCard`` header + ``DecisionTimeline``
 *      + the per-plan ``BypassPlanCard`` list (default plans visible,
 *      advanced plans behind an ``<details>`` expander per DEC-024).
 *   3. Cached anchors picker: a small list of previously-built
 *      anchors so the operator can flip between them without
 *      re-typing the smali signature.
 *   4. Status row: cache + decompile + call-graph readiness, surfaced
 *      via the same ``GET /status`` shape the 10.6 placeholder hit.
 *
 * Lifecycle owned by the ``useTraceAnchor`` hook in ``api/trace.ts``:
 * GET first (cache), surface "missing" when the entry isn't cached,
 * operator clicks Build to fire POST. ``Force re-trace`` always fires
 * POST with ``force=true``.
 *
 * The Trace pane intentionally has no chat dock of its own — operators
 * who want to talk to the LLM about a trace should switch to Manual
 * Hooks mode (which carries the ``ChatDock`` + the ``frida_summary``
 * attachment plumbing from sub-step 4.7). 10.8 will add a ``trace``
 * attachment kind so the Manual Hooks chat can pull the active
 * anchor.
 */

import { useEffect, useMemo, useState } from "react";
import { BehaviorAnchorCard } from "../components/trace/BehaviorAnchorCard";
import { BypassPlanCard } from "../components/trace/BypassPlanCard";
import { DecisionTimeline } from "../components/trace/DecisionTimeline";
import {
  deleteTraceAnchor,
  fetchTraceStatus,
  listTraceAnchors,
  useTraceAnchor,
  type TraceAnchorRow,
  type TraceStatusPayload,
} from "../api/trace";

type Props = {
  appId: string | null;
};

const DEFAULT_HOPS = 3;
const MIN_HOPS = 1;
const MAX_HOPS = 6;

export function LabTraceMode({ appId }: Props) {
  // ----- form state ------------------------------------------------------
  const [entryDraft, setEntryDraft] = useState("");
  const [hopsDraft, setHopsDraft] = useState<number>(DEFAULT_HOPS);
  const [activeEntry, setActiveEntry] = useState<string | null>(null);
  const [activeHops, setActiveHops] = useState<number>(DEFAULT_HOPS);

  // ----- status + cached-anchors list ------------------------------------
  const [status, setStatus] = useState<TraceStatusPayload | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);

  const [cached, setCached] = useState<TraceAnchorRow[]>([]);
  const [cachedReloadTick, setCachedReloadTick] = useState(0);

  // ----- the anchor lifecycle --------------------------------------------
  const { state, build, clear } = useTraceAnchor(appId, activeEntry, activeHops);

  // Reset the form + active target whenever the operator changes app.
  useEffect(() => {
    setEntryDraft("");
    setHopsDraft(DEFAULT_HOPS);
    setActiveEntry(null);
    setActiveHops(DEFAULT_HOPS);
    clear();
  }, [appId, clear]);

  // Status fetch on app change + after Build (so the cache count
  // updates without an explicit reload).
  useEffect(() => {
    setStatus(null);
    setStatusError(null);
    if (!appId) return;
    let cancelled = false;
    setStatusLoading(true);
    void (async () => {
      const r = await fetchTraceStatus(appId);
      if (cancelled) return;
      setStatusLoading(false);
      if (r.ok) setStatus(r.data);
      else setStatusError(`${r.status ? `${r.status} — ` : ""}${r.error}`);
    })();
    return () => {
      cancelled = true;
    };
  }, [appId, cachedReloadTick]);

  // Cached anchors list — refresh on app change + after Build / Delete.
  useEffect(() => {
    setCached([]);
    if (!appId) return;
    let cancelled = false;
    void (async () => {
      const r = await listTraceAnchors(appId);
      if (cancelled) return;
      if (r.ok) setCached(r.data.anchors);
    })();
    return () => {
      cancelled = true;
    };
  }, [appId, cachedReloadTick]);

  // After a successful Build, bump the reload tick so the status +
  // cached list pick up the new row.
  useEffect(() => {
    if (state.kind === "loaded" && state.from === "build") {
      setCachedReloadTick((t) => t + 1);
    }
  }, [state]);

  const lowConfidenceSet = useMemo(() => {
    if (state.kind !== "loaded") return new Set<number>();
    return new Set(state.anchor.low_confidence_decision_indices);
  }, [state]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const entry = entryDraft.trim();
    if (!entry) return;
    const hops = Math.max(MIN_HOPS, Math.min(MAX_HOPS, hopsDraft || DEFAULT_HOPS));
    setActiveEntry(entry);
    setActiveHops(hops);
  };

  const onForceRebuild = () => {
    if (!activeEntry) return;
    void build(true);
  };

  const onPickCached = (row: TraceAnchorRow) => {
    setEntryDraft(row.entry_smali_id);
    setHopsDraft(row.hops);
    setActiveEntry(row.entry_smali_id);
    setActiveHops(row.hops);
  };

  const onDeleteCached = async (row: TraceAnchorRow) => {
    if (!appId) return;
    const r = await deleteTraceAnchor(appId, row.entry_smali_id, row.hops);
    if (r.ok) {
      // If the deleted row was the active one, clear the active state
      // so the result region returns to its empty state.
      if (activeEntry === row.entry_smali_id && activeHops === row.hops) {
        setActiveEntry(null);
        clear();
      }
      setCachedReloadTick((t) => t + 1);
    }
  };

  return (
    <div className="lab-trace-mode pane-scroll">
      <header className="pane-head">
        <h2>Behavior Trace</h2>
        <span className="muted small">
          UI element ➜ decision points ➜ bypass plans
        </span>
      </header>

      {!appId && (
        <p className="muted small">
          No app selected — pick a project in the Reports tab to start
          tracing behaviour.
        </p>
      )}

      {appId && (
        <>
          <form className="trace-form" onSubmit={onSubmit}>
            <label className="trace-form-field trace-form-entry">
              <span>Entry method (smali signature)</span>
              <input
                type="text"
                value={entryDraft}
                onChange={(e) => setEntryDraft(e.target.value)}
                placeholder="Lcom/example/Foo;->onClick(Landroid/view/View;)V"
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <label className="trace-form-field trace-form-hops">
              <span>Hops</span>
              <input
                type="number"
                min={MIN_HOPS}
                max={MAX_HOPS}
                step={1}
                value={hopsDraft}
                onChange={(e) => setHopsDraft(parseInt(e.target.value, 10) || DEFAULT_HOPS)}
              />
            </label>
            <div className="trace-form-buttons">
              <button type="submit" disabled={!entryDraft.trim()}>Trace</button>
              <button
                type="button"
                onClick={onForceRebuild}
                disabled={!activeEntry || state.kind === "building"}
                title="Re-run the trace_behavior skill from scratch (bypass cache)"
              >
                Force re-trace
              </button>
            </div>
          </form>

          <TraceResultRegion
            state={state}
            appId={appId}
            lowConfidenceSet={lowConfidenceSet}
            onBuild={() => void build(false)}
          />

          <CachedAnchorsList
            cached={cached}
            activeEntry={activeEntry}
            activeHops={activeHops}
            onPick={onPickCached}
            onDelete={(row) => void onDeleteCached(row)}
          />

          <TraceStatusFooter
            status={status}
            error={statusError}
            loading={statusLoading}
            appId={appId}
          />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TraceResultRegion — switches on the ``useTraceAnchor`` state and renders
// the right empty-state / loading / error / loaded UI. Lifted out of the
// main component so the JSX is scannable.
// ---------------------------------------------------------------------------

type ResultProps = {
  state: ReturnType<typeof useTraceAnchor>["state"];
  appId: string;
  lowConfidenceSet: ReadonlySet<number>;
  onBuild: () => void;
};

function TraceResultRegion({ state, appId, lowConfidenceSet, onBuild }: ResultProps) {
  if (state.kind === "idle") {
    return (
      <p className="trace-empty muted small">
        Type or paste a smali entry method above and click <strong>Trace</strong>.
        The cached anchors below show what's already been traced for this app.
      </p>
    );
  }
  if (state.kind === "loading") {
    return <p className="trace-empty muted small">Loading cached trace…</p>;
  }
  if (state.kind === "missing") {
    return (
      <div className="trace-empty trace-empty-missing">
        <p>
          This entry hasn't been traced yet. Click <strong>Build trace</strong> to
          run the <code>trace_behavior</code> skill — it walks the call-graph
          closure, classifies every gate, and asks the LLM to refine
          low-confidence verdicts.
        </p>
        <button type="button" onClick={onBuild}>Build trace</button>
      </div>
    );
  }
  if (state.kind === "building") {
    return (
      <p className="trace-empty muted small">
        Building trace — this fires one LLM call per anchor and may take
        a few seconds. The cache is updated atomically once the
        <code> trace_behavior</code> skill returns.
      </p>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="trace-empty trace-error">
        <p>
          <strong>{state.phase === "build" ? "Build failed" : "Cache lookup failed"}</strong>
          {" "}— {state.status ? `${state.status}: ` : ""}{state.error}
        </p>
        <button type="button" onClick={onBuild}>Retry as Build</button>
      </div>
    );
  }

  const { anchor, from } = state;
  return (
    <div className="trace-result">
      <BehaviorAnchorCard anchor={anchor} source={from} />

      <section className="trace-section">
        <h3>Decision timeline ({anchor.decisions.length})</h3>
        <DecisionTimeline
          decisions={anchor.decisions}
          lowConfidenceIndices={lowConfidenceSet}
          appId={appId}
        />
      </section>

      <section className="trace-section">
        <h3>Bypass plans ({anchor.plans.length})</h3>
        {anchor.plans.length === 0 ? (
          <p className="muted small">
            No deterministic plans synthesised at the configured risk
            threshold. Try the advanced plans below if any, or refine the
            heuristic verdicts manually via Manual Hooks mode.
          </p>
        ) : (
          <div className="trace-bypass-plan-list">
            {anchor.plans.map((p, i) => (
              <BypassPlanCard key={`${p.template_id}-${i}`} plan={p} />
            ))}
          </div>
        )}
        {anchor.advanced_plans.length > 0 && (
          <details className="trace-advanced-plans">
            <summary>
              Advanced ({anchor.advanced_plans.length} higher-risk plan
              {anchor.advanced_plans.length === 1 ? "" : "s"})
            </summary>
            <div className="trace-bypass-plan-list">
              {anchor.advanced_plans.map((p, i) => (
                <BypassPlanCard key={`adv-${p.template_id}-${i}`} plan={p} />
              ))}
            </div>
          </details>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CachedAnchorsList — small picker so the operator can flip between
// previously-traced anchors without re-typing the smali signature.
// ---------------------------------------------------------------------------

type CachedProps = {
  cached: TraceAnchorRow[];
  activeEntry: string | null;
  activeHops: number;
  onPick: (row: TraceAnchorRow) => void;
  onDelete: (row: TraceAnchorRow) => void;
};

function CachedAnchorsList({ cached, activeEntry, activeHops, onPick, onDelete }: CachedProps) {
  if (cached.length === 0) return null;
  return (
    <section className="trace-cached-anchors">
      <h3>Cached anchors ({cached.length})</h3>
      <ul>
        {cached.map((row) => {
          const isActive =
            row.entry_smali_id === activeEntry && row.hops === activeHops;
          return (
            <li key={`${row.entry_smali_id}#${row.hops}`} className={isActive ? "trace-cached-anchor-active" : ""}>
              <button
                type="button"
                className="trace-cached-anchor-pick"
                onClick={() => onPick(row)}
                title="Load this cached anchor"
              >
                <code>{row.entry_smali_id}</code>
                <span className="muted small">
                  hops={row.hops} · {new Date(row.created_at * 1000).toLocaleString()}
                </span>
              </button>
              <button
                type="button"
                className="trace-cached-anchor-delete"
                onClick={() => onDelete(row)}
                title="Delete this cached anchor"
                aria-label="Delete cached anchor"
              >
                ×
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------------------
// TraceStatusFooter — same status-card the 10.6 placeholder shipped,
// trimmed to a footer strip now that the headline UI sits above it.
// ---------------------------------------------------------------------------

type StatusProps = {
  status: TraceStatusPayload | null;
  error: string | null;
  loading: boolean;
  appId: string;
};

function TraceStatusFooter({ status, error, loading, appId }: StatusProps) {
  return (
    <footer className="lab-trace-status">
      <h3>Cache status</h3>
      {loading && (
        <p className="muted small">Loading <code>/api/trace/{appId}/status</code>…</p>
      )}
      {error && (
        <p className="muted small" style={{ color: "var(--accent)" }}>{error}</p>
      )}
      {status && (
        <dl className="lab-trace-status-grid">
          <dt>Decompile</dt>
          <dd>{status.decompile_status}</dd>
          <dt>Call graph</dt>
          <dd>{status.call_graph.status}</dd>
          <dt>Trace cache</dt>
          <dd>{status.trace_cache.status}</dd>
          <dt>Cached anchors</dt>
          <dd>{status.trace_cache.anchor_count ?? 0}</dd>
          {status.trace_cache.error && (
            <>
              <dt>Cache error</dt>
              <dd style={{ color: "var(--accent)" }}>{status.trace_cache.error}</dd>
            </>
          )}
        </dl>
      )}
    </footer>
  );
}
