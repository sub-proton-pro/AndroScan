/**
 * Per-plan card rendering one ``BypassPlan`` from
 * ``BehaviorAnchor.plans`` or ``BehaviorAnchor.advanced_plans``
 * (Phase 10 sub-step 10.7).
 *
 * Layout:
 *
 *   * Header — risk pill (low/medium/high colour-coded), template id
 *     as a monospace badge, and the "Stage in Manual Hooks" button on
 *     the right edge.
 *   * Rationale — free-form prose from the planner / LLM.
 *   * Targets — small "Hooks: <method>" + "Targets: <gate>" captions
 *     so the operator can tell at a glance which method the hook will
 *     attach to (may differ from the gate method — e.g. a Plan A
 *     predicate-flip hooks ``isPremiumUser`` to flip the verdict on
 *     the gate ``onPaymentClicked``).
 *   * Params — 2-column dl grid of the template's parameter dict
 *     (operator-readable; the same dict gets shoved into the
 *     ``HookBuilder`` prefill on Stage).
 *   * Risks — bulleted list of secondary risk strings (per
 *     ``BypassPlan.risks``) when non-empty.
 *
 * The "Stage in Manual Hooks" button writes the plan into
 * ``WorkbenchContext.pendingHookPrefill`` and flips ``labMode`` to
 * ``"manual-hooks"`` so the operator lands in HookBuilder with the
 * form pre-populated. ``HookBuilder`` consumes the pending prefill on
 * mount/change and clears it.
 */

import type { BypassPlan } from "../../api/trace";
import { useWorkbench } from "../../context/WorkbenchContext";

type Props = {
  plan: BypassPlan;
};

export function BypassPlanCard({ plan }: Props) {
  const { appId, setPendingHookPrefill, setLabMode } = useWorkbench();
  const risk = (plan.risk || "medium").toLowerCase();
  const onStage = () => {
    if (!appId) return;
    const sourceMethod = plan.source_decision_method
      ? `${plan.source_decision_method.method_name}`
      : null;
    setPendingHookPrefill({
      appId,
      templateId: plan.template_id,
      params: plan.params,
      sourceLabel: sourceMethod
        ? `Trace plan: ${sourceMethod}`
        : `Trace plan: ${plan.template_id}`,
    });
    setLabMode("manual-hooks");
  };

  return (
    <article className="trace-bypass-plan-card">
      <header className="trace-bypass-plan-header">
        <span
          className={`trace-bypass-plan-risk trace-bypass-plan-risk-${risk}`}
          title={`risk: ${plan.risk}`}
        >
          {plan.risk}
        </span>
        <code className="trace-bypass-plan-template">{plan.template_id}</code>
        <div className="trace-bypass-plan-spacer" />
        <button
          type="button"
          className="trace-bypass-plan-stage"
          onClick={onStage}
          disabled={!appId}
          title={appId
            ? "Pre-fill the Manual Hooks builder with this plan and switch to Manual Hooks mode"
            : "No app selected"}
        >
          Stage in Manual Hooks
        </button>
      </header>

      {plan.rationale && (
        <p className="trace-bypass-plan-rationale">{plan.rationale}</p>
      )}

      {(plan.target_method || plan.source_decision_method) && (
        <div className="trace-bypass-plan-targets">
          {plan.target_method && (
            <div>
              <span className="muted small">Hooks: </span>
              <code>
                {plan.target_method.class_name}.{plan.target_method.method_name}
              </code>
            </div>
          )}
          {plan.source_decision_method && (
            <div>
              <span className="muted small">Bypasses: </span>
              <code>
                {plan.source_decision_method.class_name}.{plan.source_decision_method.method_name}
                {plan.source_decision_instruction_index != null &&
                  ` #${plan.source_decision_instruction_index}`}
              </code>
            </div>
          )}
        </div>
      )}

      {Object.keys(plan.params).length > 0 && (
        <details className="trace-bypass-plan-params">
          <summary>Parameters ({Object.keys(plan.params).length})</summary>
          <dl className="trace-bypass-plan-params-grid">
            {Object.entries(plan.params).map(([k, v]) => (
              <div key={k} className="trace-bypass-plan-param-row">
                <dt><code>{k}</code></dt>
                <dd><code>{v || <span className="muted small">(empty)</span>}</code></dd>
              </div>
            ))}
          </dl>
        </details>
      )}

      {plan.risks.length > 0 && (
        <details className="trace-bypass-plan-risks">
          <summary>Notes ({plan.risks.length})</summary>
          <ul>
            {plan.risks.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </details>
      )}
    </article>
  );
}
