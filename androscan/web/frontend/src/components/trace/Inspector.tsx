/**
 * Behavior Trace v3 — Inspector pane (Phase 13 sub-step 13.7 /
 * DEC-029).
 *
 * Right-side fixed-360px panel that opens when the operator clicks
 * a method node in the new ``ExecutionFlow`` flowchart (13.6).
 * Renders five operator-actionable sections for the selected
 * method:
 *
 *   1. **Signature** — Class.method + descriptor + overload count
 *      + ``Possibly inlined`` callout when applicable. Read from
 *      the ``ExecutionFlow``'s node data (passed in by the
 *      consumer so we don't re-derive the heuristic).
 *   2. **Summary** — LLM-generated method summary. v1 ships an
 *      empty-state placeholder ("Summary not yet generated. Run a
 *      dynamic trace to generate per-method summaries."); 13.8
 *      wires the live event consumer (``summary_pending`` /
 *      ``summary_ready`` / ``summary_failed`` from
 *      ``WS /ws/trace/{app_id}/{session_id}``). Cached summaries
 *      from prior dynamic-trace runs (persisted in
 *      ``skill_results_cache.json`` keyed under the
 *      ``summarise_method`` skill id) will surface here once
 *      13.8 wires up the bootstrap fetch.
 *   3. **Source line** — ``<class>.java:<line>``, clickable to
 *      open the file in Code Browser via the existing
 *      ``pendingCodeNav`` plumbing. v1 limitation: the existing
 *      ``PendingCodeNavInput`` shape doesn't carry a line number
 *      (only ``relPath`` + ``className`` + optional ``method``);
 *      the line number is shown in the Inspector for orientation
 *      but the cross-tab nav lands at the top of the file. v2
 *      candidate: extend ``PendingCodeNav`` with a ``line`` field
 *      so the consumer's existing ``setScrollTarget`` can scroll
 *      to it.
 *   4. **Predicate origin** — bold one-liner + secondary detail
 *      paragraph; consumes the existing ``PredicateOrigin`` data
 *      model from Phase 11 v2's slicer. Renders via the existing
 *      ``PredicateOriginView`` so the click-to-navigate behavior
 *      on ``method_call`` / ``field_read`` origins works for free.
 *   5. **Bypass plans** — per-risk pill + title; consumes the
 *      existing ``BypassPlan`` data model. v1 reuses
 *      ``BypassPlanCard`` verbatim; per the 13.7 spec note this
 *      may grow tall when the operator expands the
 *      Parameters / Notes ``<details>`` blocks. The 360px
 *      Inspector column scrolls to absorb the height. 13.9 may
 *      add a ``compact`` prop to ``BypassPlanCard`` if dogfooding
 *      shows the unfurled card too dense for the right-pane
 *      context.
 *
 * Action row at the bottom (three buttons, locked at DEC-029):
 *
 *   * ``[Hook this method]`` — writes ``pendingHookPrefill`` with
 *     the ``entry_exit_log`` template (mirrors
 *     ``BehaviorTrace.onVerify`` byte-for-byte) and flips
 *     ``labMode`` to ``"manual-hooks"``. The HookBuilder consumes
 *     the prefill on mount/change; the operator lands in the
 *     Manual Hooks form pre-populated and can hit Run immediately.
 *   * ``[Trace this gate]`` — writes ``pendingTraceEntry`` with
 *     the selected method's full Smali signature as the
 *     ``entryPrefix``. The ``LabTraceMode`` consumer auto-fires
 *     the cache GET (via ``useTraceAnchor``); cache miss surfaces
 *     the operator-clickable ``[Build trace]`` button. Idempotent
 *     in Trace mode (``setLabMode("trace")`` is a no-op when
 *     already there).
 *   * ``[Open source]`` — writes ``pendingCodeNav`` for the
 *     selected method's class + flips the active workbench tab
 *     to ``"inspect"``. The Inspect tab consumes the pending nav,
 *     loads the file in Code Browser, and clears the pending
 *     marker.
 *
 * Selection clearing: a ``[×]`` button at the top of the panel
 * fires ``onClear()`` which the consumer wires to
 * ``setSelectedFlowNodeId(null)``. The whole panel renders an
 * empty-state placeholder when ``selectedNodeId === null``.
 *
 * Out of scope for v1 (defer to later sub-steps):
 *   * Live observation panel (param values + return value from
 *     the dynamic-trace WebSocket) → 13.8.
 *   * Bootstrap fetch of cached summaries from
 *     ``skill_results_cache.json`` → 13.8.
 *   * Line-aware ``Open source`` (extend ``PendingCodeNav`` with
 *     a ``line`` field) → 13.9 candidate.
 */

import { useMemo } from "react";

import type {
  BehaviorAnchor,
  BypassPlan,
  DecisionPoint,
  MethodRef,
  PredicateOrigin,
} from "../../api/trace";
import { useWorkbench } from "../../context/WorkbenchContext";
import { classNameToJavaRelPath } from "../../util/smaliClassToFile";
import { BypassPlanCard } from "./BypassPlanCard";
import { PredicateOriginView } from "./PredicateOriginView";
import type { ExecutionFlowNode } from "./executionFlowGraph";


// ---------------------------------------------------------------------------
// Lookup helpers (pure; unit-test seam if 13.x ever needs to move them out)


/** Smali signature shape — kept byte-equal to
 *  :mod:`executionFlowGraph`'s ``methodKey`` so the caller's
 *  ``selectedNodeId`` matches what we derive here on lookup. */
function methodSig(m: MethodRef): string {
  const className = (m.class_name || "").replace(/\./g, "/");
  return `L${className};->${m.method_name}(${(m.param_descriptors || []).join("")})${m.return_descriptor || "V"}`;
}

/** Overload-key (descriptor-stripped) — methods on the same class
 *  with the same name but different descriptors collapse onto the
 *  same node, so the Inspector lookup also collapses. */
function overloadKey(m: MethodRef): string {
  const className = (m.class_name || "").replace(/\./g, "/");
  return `L${className};->${m.method_name}`;
}

/** All ``MethodRef``s referenced anywhere in the anchor — flatten
 *  the same five sources :mod:`executionFlowGraph` flattens (entry
 *  + decisions + plan source + plan target + advanced plans). */
function allMethodRefs(anchor: BehaviorAnchor): MethodRef[] {
  const refs: MethodRef[] = [anchor.entry_method];
  for (const d of anchor.decisions) refs.push(d.method);
  for (const p of anchor.plans) {
    if (p.source_decision_method) refs.push(p.source_decision_method);
    if (p.target_method) refs.push(p.target_method);
  }
  for (const p of anchor.advanced_plans) {
    if (p.source_decision_method) refs.push(p.source_decision_method);
    if (p.target_method) refs.push(p.target_method);
  }
  return refs;
}

type ResolvedMethod = {
  /** Canonical ``MethodRef`` for the selected node — first occurrence
   *  in :func:`allMethodRefs`'s order. */
  canonical: MethodRef;
  /** Decisions whose enclosing method matches by overload-key.
   *  Multiple decisions can land here (each branch on the same
   *  method counts as a separate ``DecisionPoint``). */
  decisions: DecisionPoint[];
  /** Bypass plans whose ``target_method`` OR
   *  ``source_decision_method`` overload-key matches. */
  plans: BypassPlan[];
  /** First non-null source line across the matching decisions. */
  sourceLine: number | null;
  /** First predicate origin across the matching decisions —
   *  inspector-friendly view; v1 doesn't render multiple
   *  predicates per node (the methods that show up as multiple
   *  decisions are typically the same method's series of branches,
   *  which share the same predicate origin in practice). */
  primaryPredicate: PredicateOrigin | null;
};

/** Resolve the click target back into the anchor's data model.
 *  Returns ``null`` when the node id doesn't match anything in the
 *  anchor (defensive — synthetic sinks are filtered upstream by
 *  ``ExecutionFlow.onNodeClick`` so this should be unreachable in
 *  practice; v1 still guards). */
function resolveSelection(
  anchor: BehaviorAnchor,
  nodeId: string,
): ResolvedMethod | null {
  const refs = allMethodRefs(anchor);
  const target = refs.find((m) => methodSig(m) === nodeId);
  if (!target) return null;

  const tKey = overloadKey(target);
  const decisions = anchor.decisions.filter(
    (d) => overloadKey(d.method) === tKey,
  );
  const plans = [...anchor.plans, ...anchor.advanced_plans].filter(
    (p) =>
      (p.target_method && overloadKey(p.target_method) === tKey) ||
      (p.source_decision_method &&
        overloadKey(p.source_decision_method) === tKey),
  );
  const sourceLine =
    decisions.find((d) => d.source_line != null)?.source_line ?? null;
  const primaryPredicate =
    decisions.find((d) => d.predicate_origin != null)?.predicate_origin ??
    null;

  return {
    canonical: target,
    decisions,
    plans,
    sourceLine,
    primaryPredicate,
  };
}

/** ``Class.method(I)Z`` style summary for the signature header.
 *  Uses the simple class-name + the descriptor-shaped argument
 *  list so the operator sees something close to what jadx prints. */
function formatSignature(m: MethodRef): string {
  const cls = (m.class_name || "").split(".").pop() || m.class_name;
  const args = (m.param_descriptors || []).join("");
  const ret = m.return_descriptor || "V";
  return `${cls}.${m.method_name}(${args})${ret}`;
}


// ---------------------------------------------------------------------------
// Public component


type Props = {
  /** Active anchor — re-derived selection details from this. */
  anchor: BehaviorAnchor;
  /** Active selection from ``ExecutionFlow.onNodeClick``; ``null``
   *  → empty-state placeholder. */
  selectedNodeId: string | null;
  /** ``ExecutionFlow``'s node data for the selected node — needed
   *  for ``overloadCount`` + ``possiblyInlined`` + ``isSynthetic``
   *  (the heuristics live on the graph layer; we don't re-derive). */
  selectedNodeData: ExecutionFlowNode | null;
  /** Active app id from context — passed down so child views
   *  (PredicateOriginView, action handlers) don't re-call
   *  ``useWorkbench()``. */
  appId: string | null;
  /** Operator-clicked Close (``×``); the consumer wires this to
   *  ``setSelectedFlowNodeId(null)``. */
  onClear: () => void;
};


export function Inspector({
  anchor,
  selectedNodeId,
  selectedNodeData,
  appId,
  onClear,
}: Props) {
  const { setPendingHookPrefill, setPendingTraceEntry, setPendingCodeNav, setLabMode, setTab } =
    useWorkbench();

  const resolved = useMemo<ResolvedMethod | null>(
    () => (selectedNodeId ? resolveSelection(anchor, selectedNodeId) : null),
    [anchor, selectedNodeId],
  );

  // Empty-state placeholder — shown when no node is selected yet.
  if (!selectedNodeId || !resolved || !selectedNodeData) {
    return (
      <aside className="inspector inspector-empty" aria-label="Inspector">
        <div className="inspector-empty-body">
          <p className="muted small">
            Click a method node in the flowchart to inspect its signature,
            predicate origin, bypass plans, and source line.
          </p>
        </div>
      </aside>
    );
  }

  // Synthetic sinks shouldn't reach here (ExecutionFlow filters them at
  // click time) — render an explicit guard just in case.
  if (selectedNodeData.isSynthetic) {
    return (
      <aside className="inspector inspector-empty" aria-label="Inspector">
        <header className="inspector-head">
          <span className="inspector-head-title">Inspector</span>
          <button
            type="button"
            className="inspector-close"
            onClick={onClear}
            title="Clear selection"
            aria-label="Clear selection"
          >
            ×
          </button>
        </header>
        <p className="muted small">
          Synthetic sink nodes don't carry a method reference.
        </p>
      </aside>
    );
  }

  const { canonical, decisions, plans, sourceLine, primaryPredicate } = resolved;
  const { overloadCount, possiblyInlined } = selectedNodeData;
  const sigDisplay = formatSignature(canonical);
  const sourceFile = `${(canonical.class_name || "").split(".").pop() || canonical.class_name}.java`;

  const onHookThisMethod = () => {
    if (!appId) return;
    const safeMethodName = canonical.method_name.replace(/[<>/]/g, "");
    setPendingHookPrefill({
      appId,
      templateId: "entry_exit_log",
      params: {
        class_name: canonical.class_name,
        method_name: canonical.method_name,
        event_label: `${safeMethodName}_inspector`,
      },
      sourceLabel: `Inspector: ${canonical.class_name}.${canonical.method_name}`,
    });
    setLabMode("manual-hooks");
  };

  const onTraceThisGate = () => {
    if (!appId) return;
    setPendingTraceEntry({
      appId,
      entryPrefix: methodSig(canonical),
      sourceLabel: `Inspector → ${canonical.class_name}.${canonical.method_name}`,
    });
    // Idempotent — already in Trace mode in v1.
    setLabMode("trace");
  };

  const onOpenSource = () => {
    if (!appId) return;
    setPendingCodeNav({
      appId,
      relPath: classNameToJavaRelPath(canonical.class_name),
      className: canonical.class_name,
      method: canonical.method_name,
    });
    setTab("inspect");
  };

  return (
    <aside className="inspector" aria-label="Inspector">
      <header className="inspector-head">
        <span className="inspector-head-title">Inspector</span>
        <button
          type="button"
          className="inspector-close"
          onClick={onClear}
          title="Clear selection"
          aria-label="Clear selection"
        >
          ×
        </button>
      </header>

      <div className="inspector-body">
        {/* 1. Signature */}
        <section className="inspector-section">
          <h4 className="inspector-section-title">Signature</h4>
          <code className="inspector-signature">{sigDisplay}</code>
          <div className="inspector-signature-meta">
            <span className="muted small">{canonical.class_name}</span>
            {overloadCount > 1 && (
              <span className="inspector-overload-pill">
                ×{overloadCount} overloads
              </span>
            )}
            {possiblyInlined && (
              <span className="inspector-inlined-pill" title="Method appears as a target but not as a decision source — likely R8-inlined">
                possibly inlined
              </span>
            )}
          </div>
          {possiblyInlined && (
            <p className="inspector-inlined-callout muted small">
              This method couldn't be located as a decision source — R8
              may have inlined it. Hooking via Frida may fail; check
              the runtime trace's <code>hook_failed</code> events when
              the dynamic trace runs.
            </p>
          )}
        </section>

        {/* 2. Summary (placeholder; 13.8 wires the event consumer) */}
        <section className="inspector-section">
          <h4 className="inspector-section-title">Summary</h4>
          <p className="muted small">
            Summary not yet generated. Run a dynamic trace to generate
            per-method LLM summaries (cached for the active app SHA).
          </p>
        </section>

        {/* 3. Source line */}
        <section className="inspector-section">
          <h4 className="inspector-section-title">Source</h4>
          <button
            type="button"
            className="inspector-source-link"
            onClick={onOpenSource}
            disabled={!appId}
            title={
              appId
                ? `Open ${sourceFile} in Code Browser`
                : "No app selected"
            }
          >
            <code>{sourceFile}</code>
            {sourceLine != null && (
              <span className="muted small">:{sourceLine}</span>
            )}
          </button>
          {sourceLine == null && (
            <p className="muted small inspector-source-note">
              No source-line reference available for this method.
            </p>
          )}
        </section>

        {/* 4. Predicate origin */}
        <section className="inspector-section">
          <h4 className="inspector-section-title">Predicate origin</h4>
          {primaryPredicate ? (
            <div className="inspector-predicate-body">
              <PredicateOriginView origin={primaryPredicate} appId={appId} />
              {decisions.length > 1 && (
                <p className="muted small inspector-predicate-note">
                  ({decisions.length - 1} additional decision
                  {decisions.length - 1 === 1 ? "" : "s"} on this method
                  — see Behavior Trace list below for the full set.)
                </p>
              )}
            </div>
          ) : (
            <p className="muted small">
              {decisions.length === 0
                ? "This method is referenced as a target / sink only — no decisions originate here."
                : "Predicate origin couldn't be resolved by the slicer."}
            </p>
          )}
        </section>

        {/* 5. Bypass plans */}
        <section className="inspector-section">
          <h4 className="inspector-section-title">
            Bypass plans
            {plans.length > 0 && (
              <span className="muted small inspector-section-count">
                {" "}({plans.length})
              </span>
            )}
          </h4>
          {plans.length === 0 ? (
            <p className="muted small">
              No bypass plans cross-reference this method.
            </p>
          ) : (
            <div className="inspector-plans">
              {plans.map((p, i) => (
                <BypassPlanCard
                  key={`${p.template_id}-${i}`}
                  plan={p}
                />
              ))}
            </div>
          )}
        </section>
      </div>

      {/* Action row — sticks to the bottom of the panel. */}
      <footer className="inspector-actions">
        <button
          type="button"
          className="inspector-action inspector-action-hook"
          onClick={onHookThisMethod}
          disabled={!appId}
          title={
            appId
              ? "Pre-fill the Manual Hooks builder with an entry/exit log for this method"
              : "No app selected"
          }
        >
          Hook this method
        </button>
        <button
          type="button"
          className="inspector-action inspector-action-trace"
          onClick={onTraceThisGate}
          disabled={!appId}
          title={
            appId
              ? "Seed the Trace form with this method as the entry"
              : "No app selected"
          }
        >
          Trace this gate
        </button>
        <button
          type="button"
          className="inspector-action inspector-action-source"
          onClick={onOpenSource}
          disabled={!appId}
          title={
            appId
              ? `Open ${sourceFile} in Code Browser (Inspect tab)`
              : "No app selected"
          }
        >
          Open source
        </button>
      </footer>
    </aside>
  );
}
