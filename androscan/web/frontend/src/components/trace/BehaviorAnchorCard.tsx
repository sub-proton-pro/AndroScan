/**
 * Header card rendering the "what am I looking at" affordances for one
 * ``BehaviorAnchor`` (Phase 10 sub-step 10.7).
 *
 * Surfaces:
 *
 *   * The entry method's full signature (class.method(params)return)
 *     with descriptors rendered in a muted tone so the operator-
 *     facing class.method form pops while the smali descriptors stay
 *     scannable for verification.
 *   * "Open in Inspect" button — writes ``pendingCodeNav`` for the
 *     entry method's Java file and flips the active tab to Inspect.
 *     ``classNameToJavaRelPath`` handles the smali → java path
 *     mapping (inner classes collapse to the outer file per the
 *     same util shared by the call graph view).
 *   * Truncated / incomplete banners (see ``TraceBanners.tsx``).
 *   * LLM-authored ``rationale`` rendered as italic prose (only when
 *     non-empty — empty when the LLM call was skipped or failed).
 *   * Status footer — short summary like "12 decisions · 3 plans (+1
 *     advanced) · hops=3 · cache hit" so the operator can see the
 *     anchor's vital stats without scrolling.
 *
 * Pure presentational; no effects beyond the cross-tab nav fired by
 * the "Open in Inspect" button.
 */

import type { BehaviorAnchor } from "../../api/trace";
import { useWorkbench } from "../../context/WorkbenchContext";
import { classNameToJavaRelPath } from "../../util/smaliClassToFile";
import { TraceIncompleteBanner, TraceTruncatedBanner } from "./TraceBanners";

type Props = {
  anchor: BehaviorAnchor;
  /** Where the anchor came from on this render. ``"cache"`` means the
   *  GET /anchor returned it; ``"build"`` means the operator clicked
   *  Build (or Force re-trace) and the POST returned it. Surfaced as
   *  a small caption so the operator can tell at a glance whether
   *  they're looking at a refreshed result. */
  source: "cache" | "build";
};

export function BehaviorAnchorCard({ anchor, source }: Props) {
  const { appId, setPendingCodeNav, setTab } = useWorkbench();
  const m = anchor.entry_method;

  const onOpenInInspect = () => {
    if (!appId) return;
    setPendingCodeNav({
      appId,
      relPath: classNameToJavaRelPath(m.class_name),
      className: m.class_name,
      method: m.method_name,
    });
    setTab("inspect");
  };

  return (
    <section className="trace-anchor-card">
      <header className="trace-anchor-header">
        <div className="trace-anchor-title">
          <code className="trace-anchor-method">
            <span className="trace-anchor-class">{m.class_name}</span>
            <span className="trace-anchor-dot">.</span>
            <span className="trace-anchor-name">{m.method_name}</span>
            <span className="trace-anchor-descriptor">
              ({m.param_descriptors.join(", ") || ""}){m.return_descriptor}
            </span>
          </code>
          <button
            type="button"
            className="trace-anchor-inspect-btn"
            onClick={onOpenInInspect}
            disabled={!appId}
            title={appId
              ? `Open ${m.class_name} in the Inspect tab`
              : "No app selected"}
          >
            Open in Inspect
          </button>
        </div>
        <div className="trace-anchor-meta muted small">
          hops={anchor.hops} · {anchor.decisions.length} decision{anchor.decisions.length === 1 ? "" : "s"}
          {" · "}
          {anchor.plans.length} plan{anchor.plans.length === 1 ? "" : "s"}
          {anchor.advanced_plans.length > 0 && ` (+${anchor.advanced_plans.length} advanced)`}
          {" · "}
          {source === "cache" ? "from cache" : "freshly built"}
        </div>
      </header>

      {anchor.truncated && <TraceTruncatedBanner hops={anchor.hops} />}
      {anchor.incomplete && <TraceIncompleteBanner />}

      {anchor.rationale && (
        <p className="trace-anchor-rationale">
          <em>{anchor.rationale}</em>
        </p>
      )}
    </section>
  );
}
