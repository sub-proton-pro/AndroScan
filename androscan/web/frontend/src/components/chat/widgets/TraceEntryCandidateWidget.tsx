/**
 * Phase 11 v2.1 sub-step v2.1.5 — Tier-3 chat-widget consumer
 * (DEC-025 v2.1 closing-note Q7 / Q8). Renders one
 * ``trace_entry_candidate`` widget emitted by the backend
 * ``suggest_trace_entry`` skill: compact card with the candidate's
 * Smali signature, a one-line LLM rationale, a confidence badge, and
 * a "Trace this" button.
 *
 * On click, the button writes ``pendingTraceEntry`` (re-using the
 * 10.8 / 11.2 plumbing — exact same shape as Inspect → Trace seeds),
 * flips ``labMode`` to ``"trace"``, and switches the active tab to
 * ``"lab"``. The Lab tab's ``LabTraceMode`` consumes the pending
 * entry and auto-fires the trace if the candidate's smali_id is a
 * complete return-descriptor signature (Q8: a — same auto-fire path
 * Inspect → Trace already uses).
 *
 * Architectural pattern lock — additive-by-design (DEC-022 +
 * DEC-025 v2.1 closing-note):
 *   * The component is keyed on ``widget.kind === "trace_entry_candidate"``.
 *     Future widget kinds add as new components in this folder; the
 *     ``<ChatWidgetRenderer>`` dispatcher (``./index.tsx``) routes
 *     by kind.
 *   * Confidence is rendered as a percent badge whose colour is a
 *     bucketed mapping of the [0.0, 1.0] interval. We deliberately
 *     don't show raw float values because the LLM's confidence is
 *     judgement-based, not calibrated probability — operators read
 *     it as a relative ranking, not a calibrated risk number.
 */
import type { TraceEntryCandidateWidgetData } from "../../../types";
import { useWorkbench } from "../../../context/WorkbenchContext";

type Props = {
  widget: TraceEntryCandidateWidgetData;
  /**
   * Optional source label (defaults to "Chat → suggest_trace_entry") —
   * surfaces in the Trace mode "seeded from <source>" pill so the
   * operator can tell where the entry method came from.
   */
  sourceLabel?: string;
};

/**
 * Map a [0, 1] confidence to one of three buckets — "high" / "medium"
 * / "low" — and the matching CSS class. Bucketing keeps the visual
 * cue readable at a glance and stops the operator from reading
 * micro-differences in raw float values that the LLM doesn't
 * actually calibrate to.
 */
function confidenceBucket(c: number): { label: string; cls: string } {
  if (c >= 0.75) return { label: "high", cls: "trace-entry-candidate-confidence-high" };
  if (c >= 0.5) return { label: "medium", cls: "trace-entry-candidate-confidence-medium" };
  return { label: "low", cls: "trace-entry-candidate-confidence-low" };
}

export function TraceEntryCandidateWidget({ widget, sourceLabel }: Props) {
  const {
    appId,
    setPendingTraceEntry,
    setLabMode,
    setTab,
  } = useWorkbench();

  const onTraceClick = () => {
    if (!appId) {
      return;
    }
    setPendingTraceEntry({
      appId,
      entryPrefix: widget.smali_id,
      sourceLabel: sourceLabel ?? "Chat → suggest_trace_entry",
    });
    setLabMode("trace");
    setTab("lab");
  };

  const bucket = confidenceBucket(widget.confidence);
  const percent = Math.round(widget.confidence * 100);
  const disabled = !appId;

  return (
    <div className="chat-widget chat-widget-trace-entry-candidate">
      <div className="chat-widget-trace-entry-candidate-head">
        <code
          className="chat-widget-trace-entry-candidate-smali"
          title={widget.smali_id}
        >
          {widget.smali_id}
        </code>
        <span
          className={`chat-widget-trace-entry-candidate-confidence ${bucket.cls}`}
          title={`Confidence: ${widget.confidence.toFixed(2)} (${bucket.label})`}
        >
          {percent}%
        </span>
      </div>
      {widget.rationale ? (
        <p className="chat-widget-trace-entry-candidate-rationale">
          {widget.rationale}
        </p>
      ) : null}
      <div className="chat-widget-trace-entry-candidate-actions">
        <button
          type="button"
          className="chat-widget-trace-entry-candidate-button"
          onClick={onTraceClick}
          disabled={disabled}
          title={
            disabled
              ? "Select an app first to seed Trace mode"
              : "Open Lab → Trace and seed the entry method"
          }
        >
          Trace this
        </button>
      </div>
    </div>
  );
}
