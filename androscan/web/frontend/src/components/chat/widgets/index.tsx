/**
 * Phase 11 v2.1 sub-step v2.1.5 — chat-widget dispatcher (DEC-022 +
 * DEC-025 v2.1 closing-note Q7).
 *
 * Routes a ``ChatWidget`` (typed union mirroring the backend's
 * ``SkillWidget``) to the matching React component by ``widget.kind``.
 *
 * Architectural pattern lock — additive-by-design:
 *   * Future widget kinds add a new ``case`` arm in the switch below
 *     and a corresponding component in ``./<Kind>Widget.tsx``.
 *   * Unknown widget kinds are GRACEFULLY IGNORED — we render
 *     ``null`` rather than throw. A server / client version skew (eg.
 *     backend ships a new widget kind before the frontend bundle is
 *     re-deployed) is non-fatal: the operator sees the assistant's
 *     ``text`` field as usual; the unknown widget just doesn't render.
 *     This keeps the schema additive at both ends.
 */
import type { ChatWidget } from "../../../types";
import { TraceEntryCandidateWidget } from "./TraceEntryCandidateWidget";

type Props = {
  widget: ChatWidget;
};

export function ChatWidgetRenderer({ widget }: Props) {
  switch (widget.kind) {
    case "trace_entry_candidate":
      return <TraceEntryCandidateWidget widget={widget} />;
    default:
      // Unknown kind — graceful fallback, no console noise (a soft
      // warn would spam the dev tools on every legacy chat replay
      // when a future widget kind is removed).
      return null;
  }
}
