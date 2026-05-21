/**
 * Phase 13 v3.X-next.2 / DEC-031 N6 — plain-language tooltip + chip
 * label constants for the Behavior Trace flowchart.
 *
 * Keeping these strings in a dedicated module (rather than inlined in
 * the ``MethodNode`` renderer) means future wording iterations don't
 * require touching the JSX. The constants are consumed by
 * ``executionFlowGraphV3.ts`` / ``ExecutionFlowV3.tsx`` for the gate-
 * card verdict-summary chip.
 *
 * **N6 lockset (v3.X-next.1.0 ratified):**
 *
 *   * Replace ``9 ?``  → ``9 neutral?``   on the per-method gate-card
 *   * Replace ``9 unv`` → ``9 unverdicted?``
 *   * Hover-tooltips on each sub-chip carry a plain-language
 *     explanation (no slicer / max-walk jargon — operator-facing
 *     vocabulary only).
 *   * The ``unverdicted`` tooltip ends with a CTA pointing the
 *     operator at ``Settings → Global → trace → max_slice_depth`` so
 *     they can act on the underlying cause (slicer terminated before
 *     resolving the predicate origin).
 *
 * The tooltips are deliberately verbose: each runs 3-4 lines so the
 * operator gets a "what does this mean" + "what should I do about it"
 * answer without leaving the flowchart.
 */

export const VERDICT_CHIP_LABELS = {
  allow: "allow",
  deny: "deny",
  neutral: "neutral?",
  unverdicted: "unverdicted?",
} as const;

export const VERDICT_CHIP_TOOLTIPS = {
  allow:
    "Allow — the predicate's outcome read as 'check passes'. " +
    "The static classifier matched a known allow-tier keyword in the " +
    "predicate's origin (e.g. constant true return, equality with a " +
    "safe sentinel, allow-list contains check). The corresponding " +
    "branch is what the operator would want their bypass to take.",
  deny:
    "Deny — the predicate's outcome read as 'check fails'. " +
    "The static classifier matched a known deny-tier keyword (e.g. " +
    "constant false return, throw of a security exception, deny-list " +
    "contains check). The bypass needs to flip the path away from " +
    "this branch.",
  neutral:
    "Neutral — the static classifier ran but couldn't decide " +
    "whether this branch reads as allow or deny. Common causes: " +
    "obfuscated predicate strings, non-English copy in error " +
    "messages, custom-predicate gates the keyword classifier wasn't " +
    "trained on. Review the predicate origin in the Inspector to " +
    "confirm; the classifier is known to false-negative on these " +
    "cases (see the branch_classifier 'Out of scope for v1' catalog).",
  unverdicted:
    "Unverdicted — the slicer terminated before resolving this " +
    "decision's predicate origin, so the classifier never even " +
    "ran. Most often caused by an unusually deep predicate chain " +
    "(method-call → field-read → method-call → ...) that hit the " +
    "per-app slice-depth ceiling. Bump Settings → Global → trace → " +
    "max_slice_depth (default 4; max 8) and rebuild the trace if " +
    "you want the slicer to descend further.",
} as const;
