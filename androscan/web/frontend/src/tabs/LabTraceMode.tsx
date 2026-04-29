/**
 * Trace mode placeholder for the Lab tab (Phase 10 sub-step 10.6).
 *
 * The full Trace mode UI lands in 10.7 (BehaviorAnchorCard /
 * DecisionTimeline / BypassPlanCard). For 10.6 we ship a real
 * placeholder that:
 *
 *   1. Validates the new ``/api/trace`` endpoints are wired correctly
 *      end-to-end by hitting ``GET /status`` on mount + on app change.
 *   2. Surfaces the cache stats (``anchor_count`` / ``status``) so
 *      operators with a pre-built trace see something useful even
 *      before 10.7 ships the rich card UI.
 *   3. Renders a loud "10.7 will fill this in" banner so the operator
 *      isn't confused about an incomplete tab — Lab opens to this
 *      mode by default, so first-impression clarity matters.
 *
 * Deliberately tiny: no skill invocation, no per-anchor fetch, no
 * cross-tab wiring. Those land in the next sub-steps.
 */

import { useEffect, useState } from "react";
import {
  fetchTraceStatus,
  type TraceStatusPayload,
} from "../api/trace";

type Props = {
  appId: string | null;
};

export function LabTraceMode({ appId }: Props) {
  const [status, setStatus] = useState<TraceStatusPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setStatus(null);
    setError(null);
    if (!appId) return;
    let cancelled = false;
    setLoading(true);
    void (async () => {
      const r = await fetchTraceStatus(appId);
      if (cancelled) return;
      setLoading(false);
      if (r.ok) setStatus(r.data);
      else setError(`${r.status ? `${r.status} — ` : ""}${r.error}`);
    })();
    return () => {
      cancelled = true;
    };
  }, [appId]);

  return (
    <div className="lab-trace-mode pane-scroll">
      <header className="pane-head">
        <h2>Behavior Trace</h2>
        <span className="muted small">UI ➜ decision points ➜ bypass plans</span>
      </header>

      <div className="lab-trace-stub">
        <p>
          Trace mode will land in <strong>sub-step 10.7</strong>. This pane
          will host the <code>BehaviorAnchorCard</code> +{" "}
          <code>DecisionTimeline</code> + <code>BypassPlanCard</code> components
          driven by the <code>/api/trace</code> endpoints shipped in 10.6 and
          fed by the <code>trace_behavior</code> LLM-tier skill from 10.5.
        </p>
        <p className="muted small">
          For now: switch to <strong>Manual Hooks</strong> to use the existing
          Frida workflow, or <strong>Graph</strong> for the dedicated call-graph
          view.
        </p>
      </div>

      <section className="lab-trace-status">
        <h3>Cache status</h3>
        {!appId && (
          <p className="muted small">
            No app selected — pick a project in the Reports tab to see its
            trace cache.
          </p>
        )}
        {appId && loading && (
          <p className="muted small">Loading <code>/api/trace/{appId}/status</code>…</p>
        )}
        {appId && error && (
          <p className="muted small" style={{ color: "var(--accent)" }}>
            {error}
          </p>
        )}
        {appId && status && (
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
      </section>
    </div>
  );
}
