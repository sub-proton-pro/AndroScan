/**
 * Linear top-down timeline of ``DecisionPoint``s in the
 * ``BehaviorAnchor.decisions`` order (Phase 10 sub-step 10.7).
 *
 * One ``DecisionCard`` per decision. Each card shows:
 *
 *   * Decision header — class.method, source line (if known), opcode
 *     kind badge, and the operator-readable instruction index.
 *   * Per-branch ``BranchOutcomeBadge`` row — joined to the parent's
 *     ``branch_outcome.verdicts`` by index (matches the Python
 *     classifier's ordering contract).
 *   * Expand/collapse for the predicate origin + source-line context
 *     (``PredicateOriginView`` rendered when expanded; collapsed by
 *     default to keep the timeline scannable).
 *
 * Low-confidence decisions (whose ``instruction_index`` appears in
 * ``low_confidence_decision_indices`` on the parent anchor) get an
 * outline treatment + a "low-confidence" pill on the header. When
 * the LLM bumped the verdict (detected by
 * ``BranchOutcome.confidence == LLM_RECLASSIFY_CONFIDENCE = 0.75``,
 * the value the skill stamps on LLM-reclassified outcomes per
 * ``trace_behavior.py``) we additionally show an "LLM-refined" pill
 * so the operator knows the verdict came from the LLM layer rather
 * than the heuristic catalog.
 */

import { useState } from "react";
import type { DecisionPoint } from "../../api/trace";
import { useWorkbench } from "../../context/WorkbenchContext";
import { BranchOutcomeBadge } from "./BranchOutcomeBadge";
import { PredicateOriginView } from "./PredicateOriginView";

const LLM_RECLASSIFIED_CONFIDENCE = 0.75;
const LOW_CONFIDENCE_THRESHOLD = 0.6;
// Phase 11 sub-step 11.1: gate the operator-action hint + the soft
// "candidate gate" framing on a confident, non-neutral classifier
// verdict. Threshold 0.85 mirrors the upper band of the heuristic
// classifier's confidence distribution per `trace_behavior.py` —
// well above the 0.75 LLM-reclassified stamp so we don't flag every
// LLM-touched gate as "candidate".
const HIGH_CONFIDENCE_THRESHOLD = 0.85;

type Props = {
  decisions: DecisionPoint[];
  /** Set of ``DecisionPoint.instruction_index`` values that the
   *  heuristic classifier flagged as low confidence (< 0.6) before
   *  any LLM re-classification. Used to dim the timeline cards even
   *  when the LLM later refined the verdict to a higher confidence —
   *  operators can still see which gates needed AI assistance. */
  lowConfidenceIndices: ReadonlySet<number>;
  /** Active ``appId`` from context; passed down to PredicateOriginView
   *  so its "Open in Inspect" buttons can fire. */
  appId: string | null;
};

export function DecisionTimeline({
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
      {/* Phase 11 sub-step 11.1: inline disclosure block clarifying the
          ordering contract. Operators reading the timeline in v1
          frequently mistook the visual ordering for runtime execution
          order; the static-traversal-order framing matches the actual
          BFS traversal performed by the slicer. */}
      <p className="trace-decision-timeline-disclosure">
        Listed in static traversal order (BFS over the call graph, then
        source order within each method) — not runtime execution order.
      </p>
      <ol className="trace-decision-timeline">
        {decisions.map((d, i) => (
          <DecisionCard
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
// DecisionCard — one row in the timeline. Defined inline so the
// expand/collapse state is local; lifting it would require keying by
// (method_signature, instruction_index) tuples which is over-engineering
// for v1.
// ---------------------------------------------------------------------------

type CardProps = {
  decision: DecisionPoint;
  lowConfidence: boolean;
  appId: string | null;
};

function DecisionCard({ decision, lowConfidence, appId }: CardProps) {
  const [expanded, setExpanded] = useState(false);
  const { setPendingHookPrefill, setLabMode } = useWorkbench();

  const outcome = decision.branch_outcome;
  const llmRefined = !!outcome &&
    Math.abs(outcome.confidence - LLM_RECLASSIFIED_CONFIDENCE) < 1e-6;
  // The "currently low confidence" label reflects the *post-LLM*
  // confidence (operator-actionable). The card's outline treatment
  // reflects the *pre-LLM* heuristic confidence (signals "needed AI
  // help"). They can disagree: a pre-LLM 0.0 + LLM-refined to 0.75
  // dims the card but the per-branch badges render normally.
  const currentlyLowConfidence = !!outcome &&
    outcome.confidence < LOW_CONFIDENCE_THRESHOLD;

  // Phase 11 sub-step 11.1: a "candidate gate" is a confident,
  // operator-actionable verdict — confidence ≥ 0.85 AND at least one
  // branch decided allow/deny (not just neutral). Neutral-only
  // outcomes get no hint because there's nothing to "flip" — the
  // operator should investigate the predicate, not stage a bypass.
  const isCandidateGate = !!outcome &&
    outcome.confidence >= HIGH_CONFIDENCE_THRESHOLD &&
    outcome.verdicts.some((v) => v.verdict === "deny" || v.verdict === "allow");

  // "Verify with runtime trace" handler — same `pendingHookPrefill`
  // plumbing 10.7 established for `BypassPlanCard.onStage`. Stages
  // the universal `entry_exit_log` template against the decision's
  // enclosing method so the operator can confirm the gate actually
  // executes at runtime (and inspect the receiver / args / return
  // value via the hook's emitted events).
  //
  // NOTE: the 11.1 spec text in TASKS.md uses `label` as shorthand for
  // the template's parameter; the actual `entry_exit_log` template
  // declares its parameter as `event_label` (per
  // `androscan/adapters/frida_hooks/entry_exit_log.py`). We use the
  // real param name so the prefilled HookBuilder form is valid on
  // arrival — otherwise the operator would land on a form with a
  // missing-required-field error.
  const onVerify = () => {
    if (!appId) return;
    // Sanitise label for grep-friendliness: Java constructors stringify
    // as `<init>` / `<clinit>` and the angle brackets are awkward in
    // the trace pane's filter UX.
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
      className={`trace-decision-card${lowConfidence ? " trace-decision-card-flagged" : ""}`}
    >
      <header className="trace-decision-header">
        <div className="trace-decision-header-line">
          <code className="trace-decision-method">
            {decision.method.class_name}.{decision.method.method_name}
          </code>
          <span className="trace-decision-kind">{decision.kind}</span>
          <span className="muted small">
            #{decision.instruction_index}
            {decision.source_line != null && ` · line ${decision.source_line}`}
          </span>
          {lowConfidence && (
            <span
              className="trace-decision-pill trace-decision-pill-flagged"
              title="Heuristic classifier reported confidence < 0.6 — LLM was asked to re-classify"
            >
              flagged
            </span>
          )}
          {llmRefined && (
            <span
              className="trace-decision-pill trace-decision-pill-llm"
              title="Branch verdict refined by the LLM (confidence stamped at 0.75)"
            >
              LLM-refined
            </span>
          )}
          {currentlyLowConfidence && !llmRefined && (
            <span
              className="trace-decision-pill trace-decision-pill-warning"
              title="Verdict confidence still below 0.6 after analysis — treat as heuristic"
            >
              low confidence
            </span>
          )}
        </div>

        {outcome && outcome.verdicts.length > 0 && (
          <div className="trace-decision-verdicts">
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
        <p className="trace-decision-action-hint">
          This is a candidate gate — review the bypass plans below or
          stage <code>force_return_value</code> to flip the verdict.
        </p>
      )}

      <div className="trace-decision-actions">
        <button
          type="button"
          className="trace-decision-expand"
          onClick={() => setExpanded((e) => !e)}
          aria-expanded={expanded}
        >
          {expanded ? "Hide details" : "Show predicate origin"}
        </button>
        <button
          type="button"
          className="trace-decision-verify-btn"
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
        <div className="trace-decision-body">
          <dl className="trace-decision-details">
            <dt>Predicate registers</dt>
            <dd>
              {decision.predicate_registers.length > 0
                ? decision.predicate_registers.map((r) => (
                    <code key={r} className="trace-decision-register">{r}</code>
                  ))
                : <span className="muted small">—</span>}
            </dd>
            <dt>Branches</dt>
            <dd>
              <ul className="trace-decision-branch-list">
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
                  <ul className="trace-decision-reasons">
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
