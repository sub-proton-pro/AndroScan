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
 * **Phase 13 sub-step 13.8 update:** the Summary section now
 * consumes the live ``summary_pending`` / ``summary_ready`` /
 * ``summary_failed`` events from :func:`useDynamicTrace` (via the
 * ``summary`` prop). The four states the section renders:
 *
 *   * ``undefined`` — no summary event has landed for this method
 *     yet. v1 copy: "Summary not yet generated. Run a dynamic
 *     trace to generate per-method LLM summaries."
 *   * ``pending`` — summary in flight (LLM call started). Renders
 *     a small spinner + "Generating summary…" copy.
 *   * ``ready`` — summary text rendered as a paragraph. The
 *     ``cached: true`` flavour gets a small "(cached)" muted
 *     suffix so the operator knows whether the summary came from
 *     the warm cache or a fresh LLM call.
 *   * ``failed`` — error message rendered with a small ⚠ icon and
 *     a "Re-run dynamic trace to retry" hint.
 *
 * The Live observation panel (latest ``args`` / ``ret`` / thread
 * info from the runtime) lands in 13.8 too — appended below the
 * Source section as a small grid that's only visible when the
 * selected method has actually fired (``live`` is non-null) and
 * the operator's mode is ``"dynamic"`` / ``"both"``.
 *
 * Out of scope for v1 (defer to later sub-steps):
 *   * Bootstrap fetch of cached summaries from
 *     ``skill_results_cache.json`` for methods that fired in a
 *     prior session — operator currently sees the cached summary
 *     only on the first ``entry`` of the new session (the BE's
 *     replay-on-late-join handles that path). Pure-static
 *     inspection (no dynamic trace) doesn't surface the cache;
 *     13.9 may add a route for explicit lookups.
 *   * Line-aware ``Open source`` (extend ``PendingCodeNav`` with
 *     a ``line`` field) → 13.9 candidate.
 */

import { useMemo } from "react";

import type {
  BehaviorAnchor,
  BypassPlan,
  DecisionPoint,
  HookFailureRecord,
  LiveValueRecord,
  MethodRef,
  PredicateOrigin,
  SummaryState,
} from "../../api/trace";
import { useWorkbench } from "../../context/WorkbenchContext";
import { classNameToJavaRelPath } from "../../util/smaliClassToFile";
import { BypassPlanCard } from "./BypassPlanCard";
import { PredicateOriginView } from "./PredicateOriginView";
import {
  overloadKey as graphOverloadKey,
  type ExecutionFlowV3Node,
} from "./executionFlowGraphV3";
import type { TraceMode } from "./TraceModeToggle";


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
  selectedNodeData: ExecutionFlowV3Node | null;
  /** Active app id from context — passed down so child views
   *  (PredicateOriginView, action handlers) don't re-call
   *  ``useWorkbench()``. */
  appId: string | null;
  /** Operator-clicked Close (``×``); the consumer wires this to
   *  ``setSelectedFlowNodeId(null)``. */
  onClear: () => void;
  /** Phase 13 sub-step 13.8 — per-method LLM summary state, fed by
   *  the ``summary_pending`` / ``summary_ready`` / ``summary_failed``
   *  events from the dynamic-trace WebSocket. Keyed by overload key
   *  (descriptor-stripped Smali) so multiple overloads of the same
   *  method share one summary. ``undefined`` lookup → empty-state
   *  placeholder. */
  summaries?: ReadonlyMap<string, SummaryState>;
  /** Phase 13 sub-step 13.8 — latest fire's args + return + thread
   *  info for the selected method, fed by the ``entry`` / ``exit``
   *  events. ``undefined`` / not-found lookup → live-observation
   *  panel doesn't render. */
  liveValues?: ReadonlyMap<string, LiveValueRecord>;
  /** Phase 13 sub-step 13.8 — current trace overlay mode. The Live
   *  observation panel only renders in ``"dynamic"`` / ``"both"``
   *  to keep the Inspector compact when the operator has explicitly
   *  asked for the static-only view. */
  mode?: TraceMode;
  /** Phase 13 sub-step 13.9 — runtime ``hook_failed`` events from
   *  the dynamic-trace WebSocket, keyed by overload key. When the
   *  selected method's overload key is present in this map, the
   *  Inspector upgrades the (previously cool-gray heuristic)
   *  ``Possibly inlined`` callout to a runtime-confirmed warn-orange
   *  state — the operator now knows for certain Frida couldn't
   *  install the hook (R8 inlining is the most common cause). */
  hookFailed?: ReadonlyMap<string, HookFailureRecord>;
};


export function Inspector({
  anchor,
  selectedNodeId,
  selectedNodeData,
  appId,
  onClear,
  summaries,
  liveValues,
  mode = "static",
  hookFailed,
}: Props) {
  const {
    setPendingHookPrefill,
    setPendingTraceEntry,
    setPendingCodeNav,
    setPendingChatPrefill,
    setLabMode,
    setTab,
  } = useWorkbench();

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
  // 13.8 — per-method summary + live-value lookup. Both keyed on
  // overload key (descriptor-stripped Smali) so multiple overloads
  // collapse onto the same Inspector view.
  const oKey = graphOverloadKey(canonical);
  const summary = summaries?.get(oKey);
  const live = liveValues?.get(oKey) ?? null;
  const showLiveObservation = (mode === "dynamic" || mode === "both") && live != null;
  // 13.9 — runtime ``hook_failed`` confirmation. If the active
  // dynamic trace fired a ``hook_failed`` event for any overload of
  // this method, surface the runtime-confirmed inlined state. The
  // ``hookFailed`` map is keyed by overload key, so a single match
  // is enough to confirm the whole stack. ``null`` when no runtime
  // confirmation has landed (Inspector falls back to the static
  // ``possiblyInlined`` heuristic).
  const runtimeInlined = hookFailed?.get(oKey) ?? null;

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

  // 13.9 — "Discuss in chat" affordance on the Summary section.
  // Writes ``pendingChatPrefill`` with a method-specific prompt
  // template that pre-arms the agentic loop with the four
  // identifying fields the ``summarise_method`` skill needs;
  // operator reviews / sends, the LLM calls the skill, and the
  // skill emits a ``MethodSummaryWidget`` (13.9 BE extension)
  // alongside its text — so the chat dock surfaces the same
  // summary as an interactive card. When ``app_id`` is missing the
  // affordance is disabled (matches the Trace / Hook this method
  // posture).
  const onDiscussInChat = () => {
    if (!appId) return;
    // Prompt template — descriptive enough that the LLM picks the
    // right skill but free-form enough that the operator can edit
    // it before sending. The four fields are pre-baked verbatim so
    // the LLM doesn't have to guess at the Smali shape (the chat
    // dock's auto-complete on Smali signatures is operator-aided,
    // not LLM-aided, so a deterministic prefill is the path of
    // least friction).
    const params = (canonical.param_descriptors || []).join("");
    const ret = canonical.return_descriptor || "V";
    const descriptor = `(${params})${ret}`;
    const message = [
      `Tell me more about \`${canonical.class_name}.${canonical.method_name}${descriptor}\`.`,
      "",
      "Use the `summarise_method` skill to fetch / generate the summary, then explain what this method does and how a security tester should think about it. Skill params:",
      `  class_smali = \"L${(canonical.class_name || "").replace(/\./g, "/")};\"`,
      `  method_name = \"${canonical.method_name}\"`,
      `  descriptor  = \"${descriptor}\"`,
      `  app_id      = \"${appId}\"`,
    ].join("\n");
    setPendingChatPrefill({
      tab: "lab",
      message,
      sourceLabel: `Inspector → summarise_method (${canonical.class_name}.${canonical.method_name})`,
    });
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
            {/* 13.9 — pill state machine. ``runtimeInlined`` (a
                ``hook_failed`` event for this overload key)
                upgrades the cool-gray static heuristic pill to a
                warn-orange runtime-confirmed pill. ``runtimeInlined``
                takes precedence; we render either the runtime pill
                OR the static pill, never both. */}
            {runtimeInlined ? (
              <span
                className="inspector-inlined-pill inspector-inlined-pill-runtime"
                title={`Frida couldn't install this hook at runtime (reason: ${runtimeInlined.reason}) — R8 likely inlined the method`}
              >
                inlined (runtime-confirmed)
              </span>
            ) : (
              possiblyInlined && (
                <span
                  className="inspector-inlined-pill"
                  title="Method appears as a target but not as a decision source — likely R8-inlined"
                >
                  possibly inlined
                </span>
              )
            )}
          </div>
          {/* 13.9 — callout state machine. Runtime-confirmed
              callout supersedes the static heuristic copy with a
              clearer, action-oriented message. */}
          {runtimeInlined && (
            <p className="inspector-inlined-callout inspector-inlined-callout-runtime small">
              <span aria-hidden="true">⚠</span>{" "}Runtime-confirmed:
              Frida raised{" "}
              <code>{runtimeInlined.reason}</code>
              {" "}for this method during the active trace
              {runtimeInlined.error ? (
                <>
                  {" "}(<span className="muted">{runtimeInlined.error}</span>)
                </>
              ) : null}
              . R8 most likely inlined the method into its callers; try
              hooking the caller method instead.
            </p>
          )}
          {possiblyInlined && !runtimeInlined && (
            <p className="inspector-inlined-callout muted small">
              This method couldn't be located as a decision source — R8
              may have inlined it. Hooking via Frida may fail; check
              the runtime trace's <code>hook_failed</code> events when
              the dynamic trace runs.
            </p>
          )}
        </section>

        {/* 2. Summary — 13.8 consumes the live ``summary_*`` events
            from useDynamicTrace via the ``summary`` lookup. Four
            states: undefined (no event yet) / pending (in flight) /
            ready (text rendered) / failed (error + retry hint). */}
        <section className="inspector-section">
          <h4 className="inspector-section-title">Summary</h4>
          {!summary && (
            <p className="muted small">
              Summary not yet generated. Run a dynamic trace to generate
              per-method LLM summaries (cached for the active app SHA).
            </p>
          )}
          {summary?.state === "pending" && (
            <div className="inspector-summary inspector-summary-pending" role="status" aria-live="polite">
              <span className="trace-entry-spinner" aria-hidden="true" />
              <span className="muted small">Generating summary…</span>
            </div>
          )}
          {summary?.state === "ready" && (
            <div className="inspector-summary inspector-summary-ready">
              <p className="inspector-summary-text">{summary.text}</p>
              {summary.cached && (
                <span className="inspector-summary-cached-pill" title="Loaded from skill_results_cache.json (no fresh LLM call)">
                  cached
                </span>
              )}
            </div>
          )}
          {summary?.state === "failed" && (
            <div className="inspector-summary inspector-summary-failed">
              <p className="inspector-summary-error" role="alert">
                <span aria-hidden="true">⚠</span>
                {" "}Summary generation failed — {summary.error}.
              </p>
              <p className="muted small">
                Re-run the dynamic trace to retry. Failed summaries are
                not cached.
              </p>
            </div>
          )}
          {/* 13.9 — "Discuss in chat" affordance. Always visible on
              non-synthetic methods (the operator may want to ask
              about a method even before its summary lands or after
              one fails); writes a method-specific
              ``pendingChatPrefill`` that pre-arms the agentic loop
              with the four ``summarise_method`` params. The chat
              dock auto-opens via the existing
              ``pendingChatPrefill`` consumer in
              :mod:`LabTraceMode`. */}
          <button
            type="button"
            className="inspector-summary-discuss-button"
            onClick={onDiscussInChat}
            disabled={!appId}
            title={
              appId
                ? "Open the Lab chat dock with a prompt that runs summarise_method for this method"
                : "Select an app first to discuss this method in chat"
            }
          >
            Discuss in chat
          </button>
        </section>

        {/* 13.8 — Live observation panel. Only renders when the
            operator's mode includes the dynamic overlay AND the
            method has actually fired (entry recorded → liveValues
            populated). Shows the latest fire's args, return value
            (when exit landed), thread + depth context, and fire
            count. The grid is sized for the 360px column; long
            arg / ret values are truncated by the chip CSS rather
            than wrapped, with a hover-tooltip carrying the full
            value. */}
        {showLiveObservation && live && (
          <section className="inspector-section inspector-live">
            <h4 className="inspector-section-title">
              Live observation
              {live.fireCount > 1 && (
                <span className="muted small inspector-section-count">
                  {" "}(latest of {live.fireCount} fires)
                </span>
              )}
            </h4>
            <dl className="inspector-live-grid">
              <dt>Args</dt>
              <dd>
                {live.args.length === 0 ? (
                  <span className="muted small">(no args)</span>
                ) : (
                  <ul className="inspector-live-args">
                    {live.args.map((a, i) => (
                      <li key={i} title={a}>
                        <code>{a}</code>
                      </li>
                    ))}
                  </ul>
                )}
              </dd>
              <dt>Return</dt>
              <dd>
                {live.ret == null ? (
                  <span className="muted small">(exit not yet recorded)</span>
                ) : (
                  <code className="inspector-live-ret" title={live.ret}>
                    {live.ret}
                  </code>
                )}
              </dd>
              <dt>Thread</dt>
              <dd>
                <code>tid {live.threadId}</code>
                <span className="muted small">
                  {" "}· depth {live.threadDepth}
                </span>
              </dd>
            </dl>
          </section>
        )}

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
