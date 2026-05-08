/**
 * Pure presentational pill rendering one ``BranchVerdict`` from a
 * ``DecisionPoint.branch_outcome``. Used by ``BehaviorTrace`` (and the
 * legacy ``DecisionTimeline`` rendered behind ``VITE_BEHAVIOR_TRACE_LEGACY``) to
 * surface the per-branch verdict next to each ``Branch.label``.
 *
 * Visual contract (DEC-024 / 10.3):
 *
 *   * ``deny``    — red filled pill
 *   * ``allow``   — green filled pill
 *   * ``neutral`` — grey outline pill
 *
 * Low-confidence verdicts (whose parent ``BranchOutcome.confidence <
 * LLM_RECLASSIFY_THRESHOLD = 0.6``, before any LLM re-classification)
 * always render with a dashed-outline + warning hue regardless of the
 * verdict colour, with the verdict label dimmed — operators have to
 * read the underlying signal carefully when the deterministic layer
 * couldn't classify confidently.
 *
 * Tooltip surfaces the signed ``score`` + the human-readable
 * ``reasons`` list so the operator can audit *why* the classifier
 * picked the verdict.
 */

import type { BranchVerdict } from "../../api/trace";

type Props = {
  verdict: BranchVerdict;
  /** ``true`` when the parent ``BranchOutcome.confidence`` was below
   *  the heuristic-only threshold of 0.6; renders the dashed-outline
   *  + dimmed-label treatment so operators don't over-trust the
   *  heuristic verdict. */
  lowConfidence?: boolean;
};

export function BranchOutcomeBadge({ verdict, lowConfidence = false }: Props) {
  const v = verdict.verdict.toLowerCase();
  const cls =
    v === "deny" ? "deny"
    : v === "allow" ? "allow"
    : "neutral";
  const tooltip =
    `score=${verdict.score.toFixed(2)}` +
    (verdict.reasons.length > 0 ? `\n${verdict.reasons.join("\n")}` : "");
  return (
    <span
      className={`trace-branch-badge trace-branch-badge-${cls}${
        lowConfidence ? " trace-branch-badge-low-confidence" : ""
      }`}
      title={tooltip}
    >
      <span className="trace-branch-badge-label">{verdict.branch_label}</span>
      <span className="trace-branch-badge-verdict">{verdict.verdict}</span>
    </span>
  );
}
