/**
 * Phase 13 sub-step 13.9 / DEC-029 — chat-widget consumer for the
 * ``summarise_method`` skill's :class:`MethodSummaryWidget` payload.
 *
 * Renders one ``method_summary`` widget emitted by the agentic
 * loop's SSE ``widget`` event: a compact card carrying the method's
 * signature header + the LLM-generated summary paragraph + an
 * optional ``(cached)`` pill (when the summary came from
 * :mod:`skill_results_cache` rather than a fresh round-trip) + the
 * same three action affordances the Inspector pane's action row
 * exposes — ``[Hook this method]`` / ``[Trace this gate]`` /
 * ``[Open source]``. The action handlers reuse the existing cross-
 * surface pendings (``pendingHookPrefill`` /
 * ``pendingTraceEntry`` / ``pendingCodeNav``) so the operator
 * lands in the corresponding Lab / Inspect surface pre-populated
 * exactly the way the Inspector's buttons would land them — the
 * chat-widget pattern's Q7 (ii) lock-in (Phase 11 v2.1.5).
 *
 * Architectural pattern lock — additive-by-design (DEC-022 +
 * DEC-025 v2.1 closing-note):
 *   * The component is keyed on
 *     ``widget.kind === "method_summary"`` and registered in the
 *     ``<ChatWidgetRenderer>`` dispatcher (``./index.tsx``).
 *   * Action handlers no-op when ``appId`` is null — same posture
 *     as :mod:`TraceEntryCandidateWidget` (an unfocused workbench
 *     can't seed a per-app pending without picking the wrong app).
 *
 * v1 caps the rendered summary text at the natural width of the
 * card (no truncation) — the BE's
 * :data:`SOURCE_BODY_BUDGET_BYTES`-derived prompt budget already
 * keeps the LLM output to 3-5 sentences, which fits comfortably
 * in the chat dock's column. Pathological outputs (operator
 * explicitly asks for a longer paragraph) overflow the card with
 * the chat dock's natural scroll absorbing the height; v2 may add
 * a clamp + "more" toggle if real-app dogfooding shows
 * overflowing summaries are common.
 */

import type { MethodSummaryWidgetData } from "../../../types";
import { useWorkbench } from "../../../context/WorkbenchContext";
import { classNameToJavaRelPath } from "../../../util/smaliClassToFile";

type Props = {
  widget: MethodSummaryWidgetData;
  /** Optional source label — shows next to the action buttons so
   *  the operator can tell where the prefill seed came from when
   *  it lands in Lab / Inspect. Defaults to a deterministic
   *  ``Chat → summarise_method`` string. */
  sourceLabel?: string;
};

/** Compact ``Class.method(args)return`` shape for the card header.
 *  Mirrors :func:`Inspector.formatSignature` byte-equally so the
 *  two surfaces read consistently when the operator hops between
 *  them. */
function formatSignature(w: MethodSummaryWidgetData): string {
  const cls = w.class_name.split(".").pop() || w.class_name;
  return `${cls}.${w.method_name}${w.descriptor}`;
}

/** Build the full Smali signature for the Trace seed.
 *  ``Lcom/example/Foo;->onClick(Landroid/view/View;)V`` — same
 *  shape as :attr:`MethodRef.smali_signature` on the BE. */
function fullSmaliSig(w: MethodSummaryWidgetData): string {
  return `${w.class_smali}->${w.method_name}${w.descriptor}`;
}

export function MethodSummaryWidget({ widget, sourceLabel }: Props) {
  const {
    appId,
    setPendingHookPrefill,
    setPendingTraceEntry,
    setPendingCodeNav,
    setLabMode,
    setTab,
  } = useWorkbench();

  const sigDisplay = formatSignature(widget);
  const seedLabel =
    sourceLabel ?? `Chat → summarise_method (${widget.class_name}.${widget.method_name})`;
  const disabled = !appId;

  const onHookThisMethod = () => {
    if (!appId) return;
    const safeMethodName = widget.method_name.replace(/[<>/]/g, "");
    setPendingHookPrefill({
      appId,
      templateId: "entry_exit_log",
      params: {
        class_name: widget.class_name,
        method_name: widget.method_name,
        event_label: `${safeMethodName}_chat_summary`,
      },
      sourceLabel: seedLabel,
    });
    setLabMode("manual-hooks");
    setTab("lab");
  };

  const onTraceThisGate = () => {
    if (!appId) return;
    setPendingTraceEntry({
      appId,
      entryPrefix: fullSmaliSig(widget),
      sourceLabel: seedLabel,
    });
    setLabMode("trace");
    setTab("lab");
  };

  const onOpenSource = () => {
    if (!appId) return;
    setPendingCodeNav({
      appId,
      relPath: classNameToJavaRelPath(widget.class_name),
      className: widget.class_name,
      method: widget.method_name,
    });
    setTab("inspect");
  };

  return (
    <div className="chat-widget chat-widget-method-summary">
      <div className="chat-widget-method-summary-head">
        <code
          className="chat-widget-method-summary-sig"
          title={fullSmaliSig(widget)}
        >
          {sigDisplay}
        </code>
        {widget.cached && (
          <span
            className="chat-widget-method-summary-cached-pill"
            title="Loaded from skill_results_cache.json (no fresh LLM round-trip)"
          >
            cached
          </span>
        )}
      </div>
      <p className="chat-widget-method-summary-text">{widget.summary}</p>
      <div className="chat-widget-method-summary-actions">
        <button
          type="button"
          className="chat-widget-method-summary-button"
          onClick={onHookThisMethod}
          disabled={disabled}
          title={
            disabled
              ? "Select an app first"
              : "Pre-fill the Manual Hooks builder with an entry/exit log for this method"
          }
        >
          Hook this method
        </button>
        <button
          type="button"
          className="chat-widget-method-summary-button"
          onClick={onTraceThisGate}
          disabled={disabled}
          title={
            disabled
              ? "Select an app first"
              : "Seed Trace mode with this method as the entry"
          }
        >
          Trace this gate
        </button>
        <button
          type="button"
          className="chat-widget-method-summary-button"
          onClick={onOpenSource}
          disabled={disabled}
          title={
            disabled
              ? "Select an app first"
              : "Open the source file in Code Browser"
          }
        >
          Open source
        </button>
      </div>
    </div>
  );
}
