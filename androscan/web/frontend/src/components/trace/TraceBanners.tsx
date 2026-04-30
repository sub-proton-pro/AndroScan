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
 *     has ``predicate_origin === null`` (the slicer hit ``max_walk``,
 *     the slicer's bounded inter-procedural descent budget exhausted,
 *     or the predicate came from a path the slicer doesn't follow —
 *     see DEC-024 + DEC-025). Distinct from ``truncated``: the
 *     closure walk may have completed but still produced under-
 *     determined gates. Phase 11 sub-step 11.6 — the v2 slicer's
 *     bounded inter-procedural descent (sub-steps 11.4 + 11.5)
 *     reduces the incidence of this banner; the copy now reflects
 *     v2's improved coverage and points operators at the depth pill
 *     on resolved cards as the visible signal that descent did fire.
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
      The slicer couldn't trace at least one gate's predicate to a
      concrete source (method call, field read, constant, parameter,
      or composite) — its bounded inter-procedural descent (helper-
      method walk + same-class field-write walk) hit a depth cap, a
      stateful / external / reflective callee, or a path the slicer
      doesn't follow. Cards that <em>did</em> resolve via descent show
      a "via N helper method(s)" / "via N field write(s)" pill next
      to the origin tag. Treat the affected branch outcomes as
      heuristic; the LLM-refined verdict (if any) is your strongest
      signal.
    </div>
  );
}
