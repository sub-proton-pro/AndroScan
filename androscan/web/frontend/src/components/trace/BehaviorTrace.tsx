/**
 * Behavior Trace — linear list view of the active anchor's
 * ``DecisionPoint``s. Renamed from the historical ``DecisionTimeline``
 * component in Phase 13 sub-step 13.5 as part of the broader
 * "Decision Timeline → Behavior Trace" framing shift locked in
 * DEC-029; the one-release ``VITE_BEHAVIOR_TRACE_LEGACY`` rollback
 * flag and the legacy ``DecisionTimeline.tsx`` parallel file were
 * removed at sub-step 13.10's docs sweep (this is now the canonical
 * declaration).
 *
 * Renders BELOW the ``ExecutionFlow`` flowchart (13.6) + ``Inspector``
 * pane (13.7) inside ``LabTraceMode``; the flowchart is the primary
 * visual surface, this list view stays as the per-decision detail
 * companion (low-confidence / LLM-refined / candidate-gate framing
 * + "Verify with runtime trace" cross-tab handoff via
 * ``pendingHookPrefill`` + ``PredicateOriginView`` mounting).
 */

import { useState } from "react";
import type { DecisionPoint } from "../../api/trace";
import { useWorkbench } from "../../context/WorkbenchContext";
import { BranchOutcomeBadge } from "./BranchOutcomeBadge";
import { PredicateOriginView } from "./PredicateOriginView";

const LLM_RECLASSIFIED_CONFIDENCE = 0.75;
const LOW_CONFIDENCE_THRESHOLD = 0.6;
const HIGH_CONFIDENCE_THRESHOLD = 0.85;

type Props = {
  decisions: DecisionPoint[];
  /** Set of ``DecisionPoint.instruction_index`` values that the
   *  heuristic classifier flagged as low confidence (< 0.6) before
   *  any LLM re-classification. Used to dim the cards even when
   *  the LLM later refined the verdict to a higher confidence —
   *  operators can still see which gates needed AI assistance. */
  lowConfidenceIndices: ReadonlySet<number>;
  /** Active ``appId`` from context; passed down to
   *  ``PredicateOriginView`` so its "Open in Inspect" buttons can
   *  fire. */
  appId: string | null;
};

export function BehaviorTrace({
  decisions,
  lowConfidenceIndices,
  appId,
}: Props) {
  if (decisions.length === 0) {
    return (
      <p className="muted small">
        No decision points emitted in this closure. Either the entry
        method has no conditional gates within {/* eslint-disable-next-line react/no-unescaped-entities */}
        the configured hop count, or the smali parser couldn't find
        any (e.g. abstract method, native method).
      </p>
    );
  }
  return (
    <>
      <p className="behavior-trace-disclosure">
        Listed in static traversal order (BFS over the call graph, then
        source order within each method) — not runtime execution order.
      </p>
      <ol className="behavior-trace-list">
        {decisions.map((d, i) => (
          <BehaviorTraceCard
            key={`${d.method.class_name}.${d.method.method_name}#${d.instruction_index}#${i}`}
            decision={d}
            lowConfidence={lowConfidenceIndices.has(d.instruction_index)}
            appId={appId}
          />
        ))}
      </ol>
    </>
  );
}

// ---------------------------------------------------------------------------
// BehaviorTraceCard — one row in the trace. Defined inline so the
// expand/collapse state is local; lifting it would require keying by
// (method_signature, instruction_index) tuples which is over-
// engineering for v1.
// ---------------------------------------------------------------------------

type CardProps = {
  decision: DecisionPoint;
  lowConfidence: boolean;
  appId: string | null;
};

function BehaviorTraceCard({ decision, lowConfidence, appId }: CardProps) {
  const [expanded, setExpanded] = useState(false);
  const { setPendingHookPrefill, setLabMode } = useWorkbench();

  const outcome = decision.branch_outcome;
  const llmRefined = !!outcome &&
    Math.abs(outcome.confidence - LLM_RECLASSIFIED_CONFIDENCE) < 1e-6;
  const currentlyLowConfidence = !!outcome &&
    outcome.confidence < LOW_CONFIDENCE_THRESHOLD;

  const isCandidateGate = !!outcome &&
    outcome.confidence >= HIGH_CONFIDENCE_THRESHOLD &&
    outcome.verdicts.some((v) => v.verdict === "deny" || v.verdict === "allow");

  const onVerify = () => {
    if (!appId) return;
    const safeMethodName = decision.method.method_name.replace(/[<>]/g, "");
    const eventLabel = `${safeMethodName}_verify`;
    const sourceLabel =
      `Trace verify: ${decision.method.class_name}.${decision.method.method_name}`;
    setPendingHookPrefill({
      appId,
      templateId: "entry_exit_log",
      params: {
        class_name: decision.method.class_name,
        method_name: decision.method.method_name,
        event_label: eventLabel,
      },
      sourceLabel,
    });
    setLabMode("manual-hooks");
  };

  return (
    <li
      className={`behavior-trace-card${lowConfidence ? " behavior-trace-card-flagged" : ""}`}
    >
      <header className="behavior-trace-header">
        <div className="behavior-trace-header-line">
          <code className="behavior-trace-method">
            {decision.method.class_name}.{decision.method.method_name}
          </code>
          <span className="behavior-trace-kind">{decision.kind}</span>
          <span className="muted small">
            #{decision.instruction_index}
            {decision.source_line != null && ` · line ${decision.source_line}`}
          </span>
          {lowConfidence && (
            <span
              className="behavior-trace-pill behavior-trace-pill-flagged"
              title="Heuristic classifier reported confidence < 0.6 — LLM was asked to re-classify"
            >
              flagged
            </span>
          )}
          {llmRefined && (
            <span
              className="behavior-trace-pill behavior-trace-pill-llm"
              title="Branch verdict refined by the LLM (confidence stamped at 0.75)"
            >
              LLM-refined
            </span>
          )}
          {currentlyLowConfidence && !llmRefined && (
            <span
              className="behavior-trace-pill behavior-trace-pill-warning"
              title="Verdict confidence still below 0.6 after analysis — treat as heuristic"
            >
              low confidence
            </span>
          )}
        </div>

        {outcome && outcome.verdicts.length > 0 && (
          <div className="behavior-trace-verdicts">
            {outcome.verdicts.map((v, i) => (
              <BranchOutcomeBadge
                key={`${v.branch_label}#${i}`}
                verdict={v}
                lowConfidence={currentlyLowConfidence}
              />
            ))}
          </div>
        )}
      </header>

      {isCandidateGate && (
        <p className="behavior-trace-action-hint">
          This is a candidate gate — review the bypass plans below or
          stage <code>force_return_value</code> to flip the verdict.
        </p>
      )}

      <div className="behavior-trace-actions">
        <button
          type="button"
          className="behavior-trace-expand"
          onClick={() => setExpanded((e) => !e)}
          aria-expanded={expanded}
        >
          {expanded ? "Hide details" : "Show predicate origin"}
        </button>
        <button
          type="button"
          className="behavior-trace-verify-btn"
          onClick={onVerify}
          disabled={!appId}
          title={appId
            ? "Pre-fill Manual Hooks with entry_exit_log on this method and switch to Manual Hooks mode"
            : "No app selected"}
        >
          Verify with runtime trace
        </button>
      </div>

      {expanded && (
        <div className="behavior-trace-body">
          <dl className="behavior-trace-details">
            <dt>Predicate registers</dt>
            <dd>
              {decision.predicate_registers.length > 0
                ? decision.predicate_registers.map((r) => (
                    <code key={r} className="behavior-trace-register">{r}</code>
                  ))
                : <span className="muted small">—</span>}
            </dd>
            <dt>Branches</dt>
            <dd>
              <ul className="behavior-trace-branch-list">
                {decision.branches.map((b, i) => (
                  <li key={`${b.label}#${i}`}>
                    <code>{b.label}</code>
                    {" → "}
                    <code>{b.target_label ?? "(fall-through)"}</code>
                  </li>
                ))}
              </ul>
            </dd>
            <dt>Predicate origin</dt>
            <dd>
              {decision.predicate_origin
                ? <PredicateOriginView origin={decision.predicate_origin} appId={appId} />
                : <span className="muted small">unresolved (slicer hit max-walk or unsupported path)</span>}
            </dd>
            {outcome && outcome.reasons.length > 0 && (
              <>
                <dt>Cross-branch reasons</dt>
                <dd>
                  <ul className="behavior-trace-reasons">
                    {outcome.reasons.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </dd>
              </>
            )}
          </dl>
        </div>
      )}
    </li>
  );
}
