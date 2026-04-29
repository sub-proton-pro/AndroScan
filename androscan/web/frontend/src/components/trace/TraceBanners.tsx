/**
 * Banner components for the Trace mode UI (Phase 10 sub-step 10.7).
 *
 * Two flavours, both rendered inline above the decision timeline by
 * ``BehaviorAnchorCard``:
 *
 *   * ``TraceTruncatedBanner`` — the closure walk hit the configured
 *     ``MAX_TRACE_HOPS`` (operator-supplied) or ``MAX_TRACE_METHODS``
 *     (hard cap of 30 in the skill). Surfaces the operator-typed
 *     ``hops`` so the message is concrete; explains that re-running
 *     with a higher hop count *may* surface additional decisions
 *     (subject to the hard cap).
 *   * ``TraceIncompleteBanner`` — at least one decision in the closure
 *     has ``predicate_origin === null`` (the slicer hit ``max_walk``
 *     or the predicate came from a path the v1 intra-procedural
 *     slicer doesn't follow — see DEC-024). Distinct from
 *     ``truncated``: the closure walk may have completed but still
 *     produced under-determined gates.
 *
 * Both render as a single yellow/orange notice line; the LabTraceMode
 * shell stacks them above the timeline if the anchor's flag is set.
 *
 * No state, no effects — pure presentational. The flags they react to
 * are computed by the 10.5 skill and surfaced verbatim in the wire
 * payload.
 */

type TruncatedProps = {
  /** The operator-supplied ``hops`` value (1..6 in the form). Echoed
   *  into the message so the operator knows which knob to bump. */
  hops: number;
  /** Hard upper bound applied by the skill (``MAX_TRACE_METHODS``).
   *  Hardcoded to 30 by ``trace_behavior.py``; surfaced here so the
   *  operator's mental model matches the skill's ceiling. */
  maxMethods?: number;
};

export function TraceTruncatedBanner({ hops, maxMethods = 30 }: TruncatedProps) {
  return (
    <div className="trace-banner trace-banner-truncated" role="status">
      <strong>Trace truncated.</strong>{" "}
      The closure walk hit a hard cap (hops={hops} or {maxMethods} methods).
      Bumping <code>hops</code> may reveal additional gates, subject to the
      6-hop ceiling and the {maxMethods}-method cap baked into the skill.
    </div>
  );
}

export function TraceIncompleteBanner() {
  return (
    <div className="trace-banner trace-banner-incomplete" role="status">
      <strong>Some predicate origins unresolved.</strong>{" "}
      The intra-procedural slicer couldn't trace at least one gate's
      predicate to a concrete source (method call, field read, constant,
      parameter, or composite). Treat the affected branch outcomes as
      heuristic; the LLM-refined verdict (if any) is your strongest
      signal.
    </div>
  );
}
