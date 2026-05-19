/**
 * Pure helper: convert a ``BehaviorAnchor`` into the
 * ``{ nodes, edges }`` graph that the
 * :mod:`ExecutionFlow` React Flow renderer consumes.
 *
 * Phase 13 sub-step 13.6 / DEC-029. The translation is deliberately
 * pure (no React, no React Flow types beyond a thin re-export) so
 * the node-kind / edge-kind / layout assignment is unit-testable
 * by isolation if 13.x or v2 ever wants to add tests at this seam
 * (current sub-step ships no tests at the route layer per the
 * 13.6 spec — the FE rendering surface is verified by ``tsc
 * --noEmit`` + ``vite build`` only).
 *
 * **Phase 13 v2.0 update (DEC-030).** Three pure-helper changes
 * land in this revision; behavior under the v1 lock (DEC-029) is
 * preserved for every code path not explicitly listed.
 *
 *   * **Q1 = (g) — neutral / unverdicted sink emission unchanged
 *     in shape but tagged for "small de-emphasized" rendering.**
 *     Per-source ``__sink_neutral__::<sourceId>`` ids stay (operator
 *     keeps a per-gate anchor for misclassification spotting per
 *     the false-negative concern that drove the lock). The renderer
 *     reads ``kind === "sink_neutral"`` to switch to the 110×36
 *     dashed-border de-emphasized variant.
 *
 *   * **Q2 = (a) — ALLOW / DENY sinks coalesce globally.** Source-
 *     keyed ids ``__sink_allow__::<sourceId>`` / ``__sink_deny__::
 *     <sourceId>`` flatten to module-level constants ``GLOBAL_ALLOW_
 *     SINK_ID`` / ``GLOBAL_DENY_SINK_ID``. All allow / deny verdict
 *     branches from every source in the anchor converge on ONE
 *     terminal per kind. The classifier is confident on allow / deny
 *     so per-source anchors aren't worth their visual weight.
 *
 *   * **Gate-count fields** ``totalGates`` + ``unclassifiedGates``
 *     are populated per ``ExecutionFlowNode`` from the anchor's
 *     decision list. Drives the per-method gate-count badge in the
 *     renderer's hover-progressive disclosure (Q1 = (g) again —
 *     badge is rendered always but CSS-hidden until ``:hover`` /
 *     ``:focus-within``). Both fields land at zero for entry-only
 *     methods + plan-target-only methods + synthetic sinks.
 *
 * The within-column sort (Q5 = (b) — entry first → real methods
 * alphabetic → synthetics last) lives in the consumer's
 * ``layoutNodes`` because that's where the rank-grouped sort
 * happens; this module just exposes ``isSynthetic`` + ``kind`` so
 * the consumer can implement the lock. The context-stroke
 * arrowhead fix (Q3 = (a)) lives entirely in ``App.css``. The
 * within-page fullscreen toggle (Q7 = (a)) lives entirely in
 * ``ExecutionFlow.tsx`` + ``App.css``. None of those three locks
 * touch this pure helper.
 *
 * Translation rules locked at DEC-029's canonical
 * ``phase-13-trace-mockup.canvas.tsx`` mockup:
 *
 *   * **Nodes** — one per ``MethodRef`` referenced anywhere in the
 *     anchor (entry method + per-decision enclosing methods +
 *     bypass-plan target methods + bypass-plan source methods).
 *     Deduped by Smali signature shape
 *     ``L<class>;-><method><descriptor>`` to handle overloads
 *     (overloaded methods stack into one card per the locked
 *     "stacked-card visual" — surfaced via the
 *     ``overload_count`` field that the custom node renderer
 *     reads to decide whether to draw the stacked-card chrome).
 *     Node ``kind`` is the operator-facing classification:
 *
 *       - ``entry`` — the anchor's ``entry_method``. Always exactly
 *         one entry node per graph (left-most in the layout).
 *       - ``gate`` — a method that contains at least one
 *         ``DecisionPoint`` with a candidate-gate verdict
 *         (heuristic confidence ≥ 0.85 + at least one
 *         ``allow``/``deny`` branch). The "GATE" corner pill in
 *         the mockup.
 *       - ``sink_allow`` / ``sink_deny`` — leaf methods (no
 *         outgoing branches) that are the target of an
 *         ``allow`` / ``deny`` verdict edge. The "ALLOW" /
 *         "DENY" corner pills in the mockup.
 *       - ``method`` — every other method. Plain card.
 *
 *     Locked v1: an entry method that ALSO has a gate-verdict
 *     decision keeps ``kind = "entry"`` (entry-ness wins; the
 *     custom renderer surfaces the gate-pill on the entry node
 *     when both apply via the ``has_gate_decision`` flag — but
 *     v1 lays out the entry as the root regardless).
 *
 *   * **Edges** — one per ``Branch`` of every ``DecisionPoint``,
 *     from the decision's enclosing method node to the branch's
 *     target method node. Edge ``kind`` mirrors the locked
 *     verdict palette:
 *
 *       - ``allow`` — ``BranchVerdict.verdict === "allow"``;
 *         renders in semantic-OK green.
 *       - ``deny`` — ``BranchVerdict.verdict === "deny"``;
 *         renders in semantic-error red.
 *       - ``neutral`` — ``BranchVerdict.verdict === "neutral"``;
 *         renders in muted gray.
 *       - ``unverdicted`` — no ``branch_outcome`` available
 *         (slicer hit max-walk on the predicate origin); renders
 *         in dim accent gray.
 *
 *     **Edge target resolution.** A branch's
 *     ``target_label`` is a Smali label like ``cond_0`` —
 *     operator-meaningless. We drop these labels from the
 *     primary UI per DEC-029 ("explicitly removed: Smali
 *     ``#instruction_index``, Smali labels"). For v1 we route
 *     branch edges into a synthetic ``allow`` / ``deny`` /
 *     ``neutral`` SINK NODE PER ENCLOSING METHOD when no
 *     ``MethodRef`` target is recoverable from the static
 *     analysis (which is the common case — the slicer doesn't
 *     resolve the post-branch flow into a method call). When
 *     a ``BypassPlan`` cross-references the same decision via
 *     ``source_decision_method`` + ``source_decision_instruction_index``
 *     AND has a ``target_method``, we route the edge into that
 *     target method's node instead — the operator gets the
 *     rich cross-method flowchart for the bypass-able gates,
 *     and the synthetic-sink fallback for the rest. This is
 *     the v1 behavior; 13.x or v2 may extend the slicer's
 *     post-branch flow resolution to make synthetic sinks
 *     unnecessary.
 *
 *   * **Layout** — left-to-right (``rankdir="LR"`` per the
 *     mockup), entry on the left, sinks on the right. We do
 *     NOT compute (x, y) coordinates here — the consumer pipes
 *     ``nodes`` + ``edges`` into ELK / dagre / Cytoscape's
 *     dagre layer at render time. v1 ships a tiny topological
 *     ranking (``layoutRank``) on each node so the renderer can
 *     fall back to a column-based grid layout if the layout lib
 *     isn't loaded yet.
 */

import type {
  BehaviorAnchor,
  BranchOutcome,
  BypassPlan,
  DecisionPoint,
  MethodRef,
} from "../../api/trace";

// ---------------------------------------------------------------------------
// Public types

/** One method node. ``id`` is the Smali signature shape used as the
 *  React Flow node id (stable across re-renders so React Flow
 *  preserves layout state on prop updates). */
export type ExecutionFlowNode = {
  id: string;
  kind:
    | "entry"
    | "gate"
    | "sink_allow"
    | "sink_deny"
    | "sink_neutral"
    | "method";
  /** Operator-facing class.method label rendered in the card title. */
  title: string;
  /** Class portion (Java-form, e.g. ``com.example.MainActivity``)
   *  for the secondary line. */
  className: string;
  /** Method name portion (e.g. ``onClick``). */
  methodName: string;
  /** Source line (1-based) when known; ``null`` for synthetic sinks
   *  + methods the slicer resolved without a source-line reference. */
  sourceLine: number | null;
  /** Number of overload entries that collapsed into this node;
   *  ``1`` for the common case. ``> 1`` triggers the
   *  stacked-card visual. */
  overloadCount: number;
  /** ``true`` when the method has at least one decision with a
   *  candidate-gate verdict. Drives the corner GATE pill on entry
   *  nodes that ALSO host a gate decision. */
  hasGateDecision: boolean;
  /** Suspected R8-inlined — set when the method shows up as a
   *  decision target / plan target but does NOT show up as a
   *  decision source. v1 leaves this conservative (false by
   *  default; flagged only when the missing-source signal is
   *  strong); 13.9 will refine this against runtime ``hook_failed``
   *  events from the dynamic-trace WebSocket. */
  possiblyInlined: boolean;
  /** Coarse topological rank — entry = 0, immediate
   *  successors = 1, etc. Used for the column-grid fallback
   *  layout when the layout lib is absent. */
  layoutRank: number;
  /** Synthetic sinks have no ``MethodRef`` — render lighter +
   *  no inspector click. */
  isSynthetic: boolean;
  /** Phase 13 v2.0 (DEC-030 Q1 = (g)) — total decision count for
   *  this method (= number of ``DecisionPoint`` entries on the
   *  anchor whose ``method`` overload-key matches this node).
   *  Drives the per-method gate-count badge in the renderer's
   *  hover-progressive disclosure (CSS-hidden by default, revealed
   *  on ``:hover`` / ``:focus-within``). Always ``0`` for synthetic
   *  sinks, plan-target-only methods, and any method that doesn't
   *  appear as a ``DecisionPoint.method`` source. */
  totalGates: number;
  /** Phase 13 v2.0 (DEC-030 Q1 = (g)) — count of this method's
   *  decisions whose verdict is ``neutral`` OR has no
   *  ``branch_outcome`` at all (slicer hit max-walk on the
   *  predicate origin). These are the misclassification-risk
   *  cases the operator wants to spot at a glance — the
   *  ``branch_classifier`` is keyword-based and known to false-
   *  negative on obfuscated strings / non-English deny copy /
   *  custom-predicate gates (see
   *  :mod:`androscan.analysis.branch_classifier`'s docstring "Out
   *  of scope for v1" section for the catalog). ``unclassifiedGates
   *  <= totalGates`` always. */
  unclassifiedGates: number;
  /** Phase 13 v2.0 (DEC-030 Q1 = (g)) — for synthetic
   *  ``sink_neutral`` nodes only, the list of (source method title,
   *  decision instruction index, edge kind) tuples that feed this
   *  sink. Drives the sink-hover tooltip so the operator can see
   *  which gates feed a given de-emphasized neutral sink without
   *  having to traverse every edge. Empty array for non-sink
   *  nodes and for global ALLOW / DENY sinks (those sinks are
   *  always operator-meaningful as "all paths to allow / deny"
   *  without per-gate breakdown). */
  feedingGates: Array<{ sourceTitle: string; instructionIndex: number; verdictKind: ExecutionFlowEdge["kind"] }>;
};

/** One edge between two nodes. */
export type ExecutionFlowEdge = {
  id: string;
  source: string;
  target: string;
  kind: "allow" | "deny" | "neutral" | "unverdicted";
  /** Operator-facing label rendered next to the edge. v1 uses a
   *  short verdict-flavored phrase (``allowed`` / ``denied`` /
   *  ``passes through``); empty string for unverdicted. */
  label: string;
  /** ``true`` when the edge corresponds to a bypass-plannable gate
   *  (``confidence >= 0.85`` AND not already ``neutral``). The
   *  consumer uses this to render a faint dot decorator on the
   *  edge mid-point. v1 leaves the dot off (uncluttered); 13.9
   *  may flip this on. Field carried for future-proofing. */
  isCandidateGate: boolean;
};

export type ExecutionFlowGraph = {
  nodes: ExecutionFlowNode[];
  edges: ExecutionFlowEdge[];
};

// ---------------------------------------------------------------------------
// Locked classifier thresholds — duplicated here from
// :mod:`BehaviorTrace.tsx` so this module stays self-contained.
// Single source of truth lives in
// ``androscan/analysis/branch_classifier.py``;
// 13.10 may unify the FE constants into a shared module.

const HIGH_CONFIDENCE_THRESHOLD = 0.85;

// Phase 13 v2.0 (DEC-030 Q2 = (a)) — synthetic ALLOW / DENY sinks
// coalesce into ONE node per kind across the whole anchor.
// Module-level constants so the renderer can compare against them
// without re-deriving the id shape. ``sink_neutral`` stays per-source
// (DEC-030 Q1 = (g) — operator wants a per-gate anchor for
// misclassification spotting).
export const GLOBAL_ALLOW_SINK_ID = "__sink_allow__";
export const GLOBAL_DENY_SINK_ID = "__sink_deny__";

// ---------------------------------------------------------------------------
// Helpers

/** Smali signature shape — used as the stable node id + dedup key.
 *  Matches :attr:`MethodRef.smali_signature` on the Python side
 *  byte-equally (``Lcom/example/Foo;->bar(I)Z``). Exported in 13.8
 *  for consumers that need to derive the same key from a
 *  ``MethodRef`` (e.g. :func:`Inspector.resolveSelection`). */
export function methodKey(m: MethodRef): string {
  const className = (m.class_name || "").replace(/\./g, "/");
  const sig = `L${className};->${m.method_name}(${(m.param_descriptors || []).join("")})${m.return_descriptor || "V"}`;
  return sig;
}

/** Collapse-key for overload merging — drops the descriptor portion
 *  so methods with the same name on the same class but different
 *  param descriptors land on the same node (the node's
 *  ``overloadCount`` then carries the count). Exported in 13.8 so
 *  consumers (``ExecutionFlow``'s fired-edge styling, ``Inspector``'s
 *  summary lookup) can derive the same key from a ``MethodRef`` /
 *  node id without redefining the function. */
export function overloadKey(m: MethodRef): string {
  const className = (m.class_name || "").replace(/\./g, "/");
  return `L${className};->${m.method_name}`;
}

/** ``methodKey`` shape but starting from a node id — strips the
 *  trailing ``(...)return-descriptor`` to recover the overload key.
 *  Used by ``ExecutionFlow`` to look up firedMethods / liveValues
 *  by overload key without knowing the underlying ``MethodRef``. */
export function overloadKeyFromNodeId(nodeId: string): string {
  const parenIdx = nodeId.indexOf("(");
  return parenIdx > 0 ? nodeId.slice(0, parenIdx) : nodeId;
}

/** Phase 13 sub-step 13.9 — count the unique full Smali signatures
 *  Frida will hook for ``anchor``. Mirrors
 *  :func:`androscan.web.trace_dynamic.extract_closure_methods`'s
 *  dedup-by-``smali_signature`` pass exactly so the FE's pre-run
 *  threshold-color decision matches what the BE will actually
 *  attempt (the overload-key collapsing the ``ExecutionFlow`` does
 *  for visual stacking is a presentation concern; Frida hooks
 *  EVERY overload, so the hook count is keyed on the full
 *  signature). The five-source flatten:
 *
 *    1. ``anchor.entry_method``
 *    2. each ``DecisionPoint.method``
 *    3. each ``BypassPlan.target_method`` + ``source_decision_method``
 *       (both ``plans`` and ``advanced_plans``)
 *
 *  Returns the unique-signature count; the caller bands it against
 *  DEC-029's threshold colour ladder to decorate the "Run dynamic
 *  trace" button. */
export function closureMethodCount(anchor: BehaviorAnchor): number {
  const seen = new Set<string>();
  const add = (m: MethodRef | null | undefined) => {
    if (!m) return;
    seen.add(methodKey(m));
  };
  add(anchor.entry_method);
  for (const d of anchor.decisions) add(d.method);
  for (const p of [...anchor.plans, ...anchor.advanced_plans]) {
    add(p.target_method);
    add(p.source_decision_method);
  }
  return seen.size;
}

/** Java-form ``class.method`` for the card title. */
function titleOf(m: MethodRef): string {
  const cls = (m.class_name || "").split(".").pop() || m.class_name;
  return `${cls}.${m.method_name}`;
}

/** Best-effort source-line resolution — picks the first non-null
 *  ``DecisionPoint.source_line`` on a method's decisions. Returns
 *  ``null`` when no decision on this method has a source line. */
function sourceLineFor(
  decisions: DecisionPoint[],
  key: string,
): number | null {
  for (const d of decisions) {
    if (overloadKey(d.method) === key && d.source_line != null) {
      return d.source_line;
    }
  }
  return null;
}

/** Quick lookup: does this method have at least one decision with a
 *  candidate-gate verdict? */
function hasCandidateGate(
  decisions: DecisionPoint[],
  key: string,
): boolean {
  for (const d of decisions) {
    if (overloadKey(d.method) !== key) continue;
    const o: BranchOutcome | null = d.branch_outcome;
    if (
      o &&
      o.confidence >= HIGH_CONFIDENCE_THRESHOLD &&
      o.verdicts.some((v) => v.verdict === "allow" || v.verdict === "deny")
    ) {
      return true;
    }
  }
  return false;
}

/** Phase 13 v2.0 (DEC-030 Q1 = (g)) — count this method's decisions
 *  and the subset that are unclassified (verdict === ``neutral`` OR
 *  no ``branch_outcome`` at all). Drives the per-method gate-count
 *  badge in the renderer. A decision counts as "unclassified" when
 *  the branch_classifier (heuristic / keyword-based per Phase 10
 *  sub-step 10.3) failed to confidently land an ``allow`` or
 *  ``deny`` verdict — the misclassification-risk surface the badge
 *  surfaces to the operator. */
function gateCountsFor(
  decisions: DecisionPoint[],
  key: string,
): { total: number; unclassified: number } {
  let total = 0;
  let unclassified = 0;
  for (const d of decisions) {
    if (overloadKey(d.method) !== key) continue;
    total += 1;
    const o: BranchOutcome | null = d.branch_outcome;
    if (!o || o.verdicts.length === 0) {
      // No outcome at all — slicer hit max-walk on the predicate
      // origin. Same operator-uncertainty signal as a neutral
      // classification.
      unclassified += 1;
      continue;
    }
    // A decision is "classified" iff at least one of its branches
    // earned an allow / deny verdict. Mixed verdicts (e.g. one
    // branch allow + one branch neutral) count as classified
    // because the classifier landed actionable signal somewhere.
    const hasActionable = o.verdicts.some(
      (v) => v.verdict === "allow" || v.verdict === "deny",
    );
    if (!hasActionable) {
      unclassified += 1;
    }
  }
  return { total, unclassified };
}

/** Walk the bypass-plan list to find a target method whose
 *  ``source_decision_method`` + ``source_decision_instruction_index``
 *  pair points at the given decision. Returns the plan's target
 *  method when one is found. Used to route a decision's branches
 *  into a real method node when the plan layer cross-references
 *  the gate. */
function planTargetFor(
  decision: DecisionPoint,
  plans: BypassPlan[],
): MethodRef | null {
  const decKey = overloadKey(decision.method);
  for (const p of plans) {
    if (
      p.source_decision_method &&
      overloadKey(p.source_decision_method) === decKey &&
      p.source_decision_instruction_index === decision.instruction_index &&
      p.target_method
    ) {
      return p.target_method;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Public entry point

export function buildExecutionFlowGraph(
  anchor: BehaviorAnchor,
): ExecutionFlowGraph {
  // Phase 1 — collect every MethodRef the anchor mentions, deduping
  // on overloadKey. Tracks per-key:
  //   - the canonical MethodRef (first seen)
  //   - the count of distinct descriptors (overloadCount)
  //   - whether this method appears as a decision SOURCE (we'll
  //     use this for the possibly-inlined inference)
  type MethodCollector = {
    canonical: MethodRef;
    overloadCount: number;
    descriptors: Set<string>;
    isDecisionSource: boolean;
    isDecisionTarget: boolean;
    isPlanTarget: boolean;
  };
  const methods = new Map<string, MethodCollector>();

  const ingest = (
    m: MethodRef,
    flags: { isDecisionSource?: boolean; isDecisionTarget?: boolean; isPlanTarget?: boolean } = {},
  ) => {
    const k = overloadKey(m);
    const existing = methods.get(k);
    const fullSig = methodKey(m);
    if (existing) {
      existing.descriptors.add(fullSig);
      existing.overloadCount = existing.descriptors.size;
      existing.isDecisionSource ||= !!flags.isDecisionSource;
      existing.isDecisionTarget ||= !!flags.isDecisionTarget;
      existing.isPlanTarget ||= !!flags.isPlanTarget;
    } else {
      methods.set(k, {
        canonical: m,
        overloadCount: 1,
        descriptors: new Set([fullSig]),
        isDecisionSource: !!flags.isDecisionSource,
        isDecisionTarget: !!flags.isDecisionTarget,
        isPlanTarget: !!flags.isPlanTarget,
      });
    }
  };

  // Entry method.
  ingest(anchor.entry_method);
  const entryKey = overloadKey(anchor.entry_method);

  // Decision-source methods.
  for (const d of anchor.decisions) {
    ingest(d.method, { isDecisionSource: true });
  }

  // Plan source + target methods.
  for (const p of anchor.plans) {
    if (p.source_decision_method) {
      ingest(p.source_decision_method, { isDecisionSource: true });
    }
    if (p.target_method) {
      ingest(p.target_method, { isPlanTarget: true });
    }
  }
  for (const p of anchor.advanced_plans) {
    if (p.source_decision_method) {
      ingest(p.source_decision_method, { isDecisionSource: true });
    }
    if (p.target_method) {
      ingest(p.target_method, { isPlanTarget: true });
    }
  }

  // Phase 2 — assemble ExecutionFlowNode entries with kind +
  // overload-count + has-gate flag + possibly-inlined inference.
  // Topological rank assignment is a quick BFS from the entry; the
  // graph is small (≤ MAX_TRACE_METHODS=30) so we don't bother with
  // a fancy layered algorithm.
  const nodes: ExecutionFlowNode[] = [];
  const synthSinks: Map<string, ExecutionFlowNode> = new Map();
  const edges: ExecutionFlowEdge[] = [];

  for (const [key, c] of methods.entries()) {
    const isEntry = key === entryKey;
    const hasGate = hasCandidateGate(anchor.decisions, key);
    const possiblyInlined =
      !c.isDecisionSource && (c.isPlanTarget || c.isDecisionTarget);
    const gateCounts = gateCountsFor(anchor.decisions, key);

    nodes.push({
      id: methodKey(c.canonical),
      kind: isEntry ? "entry" : (hasGate ? "gate" : "method"),
      title: titleOf(c.canonical),
      className: c.canonical.class_name,
      methodName: c.canonical.method_name,
      sourceLine: sourceLineFor(anchor.decisions, key),
      overloadCount: c.overloadCount,
      hasGateDecision: hasGate,
      possiblyInlined,
      layoutRank: -1, // assigned below
      isSynthetic: false,
      totalGates: gateCounts.total,
      unclassifiedGates: gateCounts.unclassified,
      feedingGates: [],
    });
  }

  // Phase 3 — emit edges. For each decision, walk every
  // (branch, verdict) pair and route to either a plan-resolved
  // target node or a synthetic verdict-flavored sink.
  //
  // Phase 13 v2.0 (DEC-030): ALLOW / DENY sinks coalesce to ONE
  // global node per kind across the whole anchor (Q2 = (a)); NEUTRAL
  // sinks stay per-source so the operator keeps a per-gate anchor
  // for misclassification spotting (Q1 = (g)); per-source NEUTRAL
  // sinks carry ``feedingGates`` so the renderer can populate a
  // hover tooltip listing the upstream gates.
  const allPlans: BypassPlan[] = [...anchor.plans, ...anchor.advanced_plans];

  /** Ensure a synthetic sink node exists in ``synthSinks`` and
   *  return it. Handles the per-kind dispatch (global ALLOW / DENY,
   *  per-source NEUTRAL) and seeds the operator-facing title at
   *  creation time. */
  const ensureSink = (
    sinkKind: "sink_allow" | "sink_deny" | "sink_neutral",
    sourceId: string,
  ): ExecutionFlowNode => {
    const id =
      sinkKind === "sink_allow"
        ? GLOBAL_ALLOW_SINK_ID
        : sinkKind === "sink_deny"
          ? GLOBAL_DENY_SINK_ID
          : `__sink_neutral__::${sourceId}`;
    const existing = synthSinks.get(id);
    if (existing) return existing;
    const title =
      sinkKind === "sink_allow"
        ? "ALLOW"
        : sinkKind === "sink_deny"
          ? "DENY"
          : "neutral";
    const created: ExecutionFlowNode = {
      id,
      kind: sinkKind,
      title,
      className: "",
      methodName: "",
      sourceLine: null,
      overloadCount: 1,
      hasGateDecision: false,
      possiblyInlined: false,
      layoutRank: -1,
      isSynthetic: true,
      totalGates: 0,
      unclassifiedGates: 0,
      feedingGates: [],
    };
    synthSinks.set(id, created);
    return created;
  };

  for (const d of anchor.decisions) {
    const sourceCanonical = methods.get(overloadKey(d.method))!.canonical;
    const sourceId = methodKey(sourceCanonical);
    const sourceTitle = titleOf(sourceCanonical);

    const planTarget = planTargetFor(d, allPlans);

    const verdicts = d.branch_outcome?.verdicts ?? [];
    const isCandidate =
      d.branch_outcome != null &&
      d.branch_outcome.confidence >= HIGH_CONFIDENCE_THRESHOLD;

    if (verdicts.length === 0) {
      // No verdict — emit a single unverdicted edge to a per-source
      // ``sink_neutral`` so the operator keeps a visual anchor for
      // the unclassified gate (Q1 = (g) misclassification-spotting
      // affordance). Edge kind stays ``unverdicted`` so the renderer
      // distinguishes "classifier was unsure" (neutral) from
      // "slicer hit max-walk" (unverdicted) via the dim-dashed
      // stroke variant locked at DEC-029.
      const sink = ensureSink("sink_neutral", sourceId);
      sink.feedingGates.push({
        sourceTitle,
        instructionIndex: d.instruction_index,
        verdictKind: "unverdicted",
      });
      edges.push({
        id: `${sourceId}->${sink.id}#${d.instruction_index}`,
        source: sourceId,
        target: sink.id,
        kind: "unverdicted",
        label: "",
        isCandidateGate: false,
      });
      continue;
    }

    for (let i = 0; i < verdicts.length; i++) {
      const v = verdicts[i];
      const verdictKind: ExecutionFlowEdge["kind"] =
        v.verdict === "allow"
          ? "allow"
          : v.verdict === "deny"
            ? "deny"
            : "neutral";

      // Resolve target. Plan target wins when the verdict is
      // ``allow`` (the bypass plan's target_method is what the
      // operator wants to see flowing from a denied gate). For
      // every other case we route to the appropriate synthetic
      // sink — global per-kind for ALLOW / DENY (Q2 = (a)), per-
      // source for NEUTRAL (Q1 = (g)).
      let targetId: string;
      if (planTarget && v.verdict === "allow") {
        targetId = methodKey(
          methods.get(overloadKey(planTarget))!.canonical,
        );
      } else {
        const sinkKind: "sink_allow" | "sink_deny" | "sink_neutral" =
          v.verdict === "allow"
            ? "sink_allow"
            : v.verdict === "deny"
              ? "sink_deny"
              : "sink_neutral";
        const sink = ensureSink(sinkKind, sourceId);
        if (sinkKind === "sink_neutral") {
          // Only neutral sinks carry the hover-tooltip feed list —
          // global ALLOW / DENY sinks are operator-meaningful as
          // "all paths to allow / deny" without per-gate breakdown.
          sink.feedingGates.push({
            sourceTitle,
            instructionIndex: d.instruction_index,
            verdictKind,
          });
        }
        targetId = sink.id;
      }

      const label =
        v.verdict === "allow"
          ? "allowed"
          : v.verdict === "deny"
            ? "denied"
            : "passes through";

      edges.push({
        id: `${sourceId}->${targetId}#${d.instruction_index}#${i}`,
        source: sourceId,
        target: targetId,
        kind: verdictKind,
        label,
        isCandidateGate: isCandidate,
      });
    }
  }

  // Append synthetic sinks to the node list.
  nodes.push(...synthSinks.values());

  // Phase 4 — coarse topological rank via BFS from the entry. The
  // graph is small (≤ ~50 nodes counting synthetic sinks); the
  // BFS doubles as a cycle guard.
  const adj: Map<string, string[]> = new Map();
  for (const e of edges) {
    if (!adj.has(e.source)) adj.set(e.source, []);
    adj.get(e.source)!.push(e.target);
  }
  const ranks: Map<string, number> = new Map();
  const queue: Array<{ id: string; rank: number }> = [];
  const entryNodeId = methodKey(anchor.entry_method);
  ranks.set(entryNodeId, 0);
  queue.push({ id: entryNodeId, rank: 0 });
  while (queue.length > 0) {
    const cur = queue.shift()!;
    const successors = adj.get(cur.id) || [];
    for (const succ of successors) {
      const existing = ranks.get(succ);
      const newRank = cur.rank + 1;
      if (existing == null || existing < newRank) {
        // Take the deepest BFS distance — gives a stable
        // left-to-right column ordering even on diamond-shaped
        // sub-graphs.
        ranks.set(succ, newRank);
        queue.push({ id: succ, rank: newRank });
      }
    }
  }
  for (const n of nodes) {
    n.layoutRank = ranks.get(n.id) ?? 0;
  }

  return { nodes, edges };
}
