/**
 * Shared renderer for the ``trace`` ``ChatAttachment`` payload — the
 * 6_000-char-capped, operator-readable summary of a built
 * :class:`BehaviorAnchor` that the chat dock attaches as context for
 * the LLM.
 *
 * **History.** Phase 10 sub-step 10.8 introduced the ``trace``
 * attachment kind and embedded the renderer inline in
 * :file:`tabs/LabTab.tsx` because Manual Hooks mode was the only
 * surface that owned a chat dock. Phase 11 v2.1.4 wired the "Ask AI"
 * rescue rope into Trace mode but deferred adding a Trace-mode chat
 * dock — the prefill targeted Manual Hooks's dock instead. v2.1.8
 * closes the resulting discoverability bug (clicking "Ask AI" in
 * Trace mode appeared to do nothing because the parent
 * ``ManualHooksMode`` wasn't mounted to receive the prefill) by
 * embedding a second :class:`ChatDock` directly into
 * ``LabTraceMode``. Both surfaces share the per-tab chat history
 * (``WorkbenchContext.chats["lab"]``) — they're never co-mounted, so
 * there's no rendering conflict, and the unified history feels right
 * (one Lab conversation rather than a per-mode scratch surface).
 *
 * Extracting the renderer here keeps the two consumers DRY and
 * preserves the existing 6_000-char budget contract that the backend
 * ``ATTACHMENT_BUDGETS["trace"]`` enforces — both modes ship the
 * same "Entry method / decisions / top plans" shape so the LLM sees
 * a consistent attachment format regardless of which mode triggered
 * the chat.
 */

import type { BehaviorAnchor } from "../api/trace";

/** Soft cap matching the backend ``ATTACHMENT_BUDGETS["trace"] == 6_000``.
 *  We trim client-side so the operator's "show context" preview matches
 *  what the model actually sees. */
export const CHAT_TRACE_BUDGET = 6_000;

/** We surface only the top-N ranked (default-tier) bypass plans in the
 *  chat attachment to keep the context budget honest. Operators who
 *  want the full ranked list (incl. advanced higher-risk plans) read
 *  the Trace mode UI directly. */
export const CHAT_TRACE_TOP_PLANS = 3;

/** Per-decision summary lines fold into the same 6_000-char budget
 *  alongside the entry header + plans; clipping further at this cap
 *  prevents a 200-decision closure from monopolising the attachment
 *  budget. Anything above this is replaced with a "+ N more
 *  decisions" trailer. */
export const CHAT_TRACE_MAX_DECISIONS = 40;

function _renderMethodRefForChat(m: { class_name: string; method_name: string }): string {
  return `${m.class_name}.${m.method_name}`;
}

/** Render a ``BehaviorAnchor`` as the operator-readable, budget-capped
 *  attachment text. Output shape: entry-method header → status flags →
 *  optional rationale → per-decision verdict list (capped) → top-N
 *  plans. Returns at most :data:`CHAT_TRACE_BUDGET` characters
 *  (with a trailing ``/* … truncated; full anchor is N chars *\/``
 *  marker when the cap is hit). */
export function renderTraceAttachment(anchor: BehaviorAnchor): string {
  const entry = anchor.entry_method;
  const parts: string[] = [];
  parts.push(
    `Entry method: ${_renderMethodRefForChat(entry)}` +
      `(${entry.param_descriptors.join(", ")})${entry.return_descriptor}`,
  );
  parts.push(
    `hops=${anchor.hops} · decisions=${anchor.decisions.length} · ` +
      `plans=${anchor.plans.length} (+${anchor.advanced_plans.length} advanced)` +
      (anchor.truncated ? " · TRUNCATED (cap hit)" : "") +
      (anchor.incomplete ? " · INCOMPLETE (unresolved predicate origins)" : ""),
  );
  if (anchor.rationale && anchor.rationale.trim()) {
    parts.push(`Rationale: ${anchor.rationale.trim()}`);
  }

  parts.push("");
  parts.push(`Decision timeline (${anchor.decisions.length}):`);
  const lowConf = new Set(anchor.low_confidence_decision_indices);
  const decisions = anchor.decisions.slice(0, CHAT_TRACE_MAX_DECISIONS);
  decisions.forEach((d, i) => {
    const verdicts =
      d.branch_outcome?.verdicts
        .map((v) => `${v.branch_label}=${v.verdict}(${v.score.toFixed(2)})`)
        .join(", ") ?? "(unclassified)";
    const origin =
      d.predicate_origin?.kind === "method_call"
        ? ` ← ${_renderMethodRefForChat(d.predicate_origin.method)}`
        : d.predicate_origin?.kind === "field_read"
        ? ` ← field ${d.predicate_origin.field.class_name}.${d.predicate_origin.field.field_name}`
        : d.predicate_origin?.kind === "const"
        ? ` ← const ${d.predicate_origin.value}`
        : d.predicate_origin?.kind === "param"
        ? ` ← param ${d.predicate_origin.register}`
        : d.predicate_origin?.kind === "composite"
        ? ` ← composite (${d.predicate_origin.reason})`
        : "";
    const flag = lowConf.has(i) ? " [LOW-CONF]" : "";
    parts.push(
      `  ${i + 1}. ${_renderMethodRefForChat(d.method)} @${d.instruction_index} ` +
        `[${d.kind}] ${verdicts}${origin}${flag}`,
    );
  });
  if (anchor.decisions.length > CHAT_TRACE_MAX_DECISIONS) {
    parts.push(
      `  + ${anchor.decisions.length - CHAT_TRACE_MAX_DECISIONS} more decision(s) ` +
        "(truncated for chat budget)",
    );
  }

  parts.push("");
  parts.push(`Top ${CHAT_TRACE_TOP_PLANS} bypass plan(s):`);
  if (anchor.plans.length === 0) {
    parts.push("  (none synthesised at the configured risk threshold)");
  } else {
    anchor.plans.slice(0, CHAT_TRACE_TOP_PLANS).forEach((p, i) => {
      const target = p.target_method
        ? _renderMethodRefForChat(p.target_method)
        : "(no target)";
      const params = Object.entries(p.params)
        .map(([k, v]) => `${k}=${v}`)
        .join(", ");
      parts.push(
        `  ${i + 1}. ${p.template_id} risk=${p.risk} → ${target}` +
          (params ? `\n     params: ${params}` : "") +
          (p.rationale ? `\n     rationale: ${p.rationale}` : ""),
      );
    });
  }

  let text = parts.join("\n");
  if (text.length > CHAT_TRACE_BUDGET) {
    text =
      text.slice(0, CHAT_TRACE_BUDGET) +
      `\n/* … truncated; full anchor is ${text.length} chars */`;
  }
  return text;
}
