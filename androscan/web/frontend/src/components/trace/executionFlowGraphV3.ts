/**
 * Behavior Trace **v3 preview** — graph emission helper.
 *
 * **PRE-DEC PRODUCTION PREVIEW** — this module is wired only behind
 * the ``?flow=v3`` URL gate (see ``LabTraceMode.tsx``); no DECISIONS
 * / TASKS / STATE entries land until the operator confirms the
 * visual matches the target screenshot. The v2.0 ``executionFlowGraph``
 * remains the production module + default path.
 *
 * Why a parallel module instead of a v2 patch — the v3 emission rules
 * are categorically different:
 *
 *   1. **No synthetic ALLOW / DENY / neutral sink nodes.** v2 emitted
 *      one synthetic sink per (verdict-kind × source-method) (or
 *      global per-kind after the Q2=(a) coalescing), and every
 *      verdicted branch terminated at one. v3 drops these entirely
 *      because operator dogfood feedback ("why is ALLOW itself a
 *      box?") indicated that the synthetic sinks read as
 *      operator-actionable terminal methods rather than as the
 *      abstract verdict labels they actually were.
 *   2. **Verdict summary chip on the gate card.** v3 ships an
 *      always-on ``verdictSummary`` field on every gate node
 *      carrying the per-verdict counts (``{ allow, deny, neutral,
 *      unverdicted }``) for that method's decisions. The renderer
 *      surfaces this as a compact inline chip on the gate card
 *      (``2a · 1d · 8?``) so the operator can scan a method's full
 *      verdict distribution at a glance without traversing per-
 *      branch terminals.
 *   3. **Real-callee terminals via ``BypassPlan.target_method``.**
 *      When a ``BypassPlan`` cross-references a decision (matching
 *      ``source_decision_method`` + ``source_decision_instruction_index``)
 *      AND carries a ``target_method``, the corresponding branch
 *      edge routes into that real method node with a ``Ret: True``
 *      label. These callee edges are the only outgoing arrows the
 *      gate emits in default v3.1 mode — every other branch is
 *      summarised into the gate's chip, not emitted as a dead-end
 *      pill terminal.
 *   4. **Optional per-branch ``Ret: X`` terminal pills** (escape
 *      hatch via ``hideRetPills = false``). The pre-v3.1 default
 *      emitted one ``return_pill`` per (decision × verdict) — on
 *      WeakBank that produced ~60 horizontal pills under
 *      ``validatePin`` alone. v3.1 default flips this to ``true``
 *      (= summarise into the chip); the false-default path is kept
 *      for debug / drill-down comparison via
 *      ``?flow=v3&pills=show``.
 *   5. **Framework-package filter (default).** v3.1 defaults to
 *      ``gatesOnly = true`` which now drops methods whose
 *      ``class_name`` matches a known-framework prefix
 *      (``kotlin.*``, ``androidx.*``, ``java.*`` etc. per
 *      :data:`FRAMEWORK_CLASS_PREFIXES`). The original v3.1 attempt
 *      filtered to "candidate-gate methods only", but on anchors
 *      where every gate is neutral (e.g. WeakBank ``onCreate$
 *      lambda$1`` with 9 decisions / 0 plans / all-neutral
 *      verdicts), that rule swept the visual down to just the
 *      entry node — useless. The framework-prefix rule degrades
 *      gracefully: app-code methods with all-neutral verdicts
 *      survive (carrying a ``9 ?`` summary chip that surfaces the
 *      classifier-uncertainty), and framework noise still drops.
 *      The entry and resolved bypass-plan targets are kept
 *      unconditionally (operator opted into them via the trace
 *      input or the plan cross-ref). Escape hatch via
 *      ``?flow=v3&methods=all`` disables the framework filter
 *      entirely.
 *   6. **Synthesised entry-to-gate call edges.** v2 only emitted
 *      edges originating at ``DecisionPoint.method`` — the entry
 *      method was disconnected from the gate-containing methods
 *      unless the entry itself contained a gate. v3 fills in a
 *      ``call`` edge from the entry to every surviving gate-source
 *      method (those that pass the ``gatesOnly`` filter), so the
 *      resulting graph always renders as a connected tree rooted at
 *      the entry. Best-effort synthesis (the real call graph lives
 *      in ``call_graph.sqlite`` and isn't wired into
 *      ``BehaviorAnchor`` yet — see ISSUE-022 for the v2.1+ work
 *      that resolves this properly); the synthesised edges read as
 *      "the entry's closure transitively reaches this gate" which
 *      is the operator's intuition for "what does this entry do?".
 *
 * **Layout** — this module is layout-agnostic. The consumer
 * (``ExecutionFlowV3``) feeds the nodes + edges into Dagre via
 * ``@dagrejs/dagre`` with ``rankdir: 'TB'`` (top-down). Dagre
 * computes the actual ``(x, y)`` coordinates including the
 * indentation / hierarchical visuals the v2 column-grid couldn't
 * produce.
 *
 * **Opt-in toggles** controllable via URL params / future UI:
 *
 *   * ``hideRetPills``: drop the per-branch ``return_pill`` terminal
 *     nodes and instead accumulate the verdict counts into the gate
 *     node's ``verdictSummary``. Default ``true`` — the visual
 *     trade-off that closes the "horizontal sprawl" + "dead-end
 *     pill" feedback signals. Setting ``false`` brings the pills
 *     back for the drill-down case.
 *   * ``gatesOnly``: filter the method set to entry + candidate-
 *     gate methods + resolved plan targets only. Default ``true``
 *     — closes the "framework noise as rank-1 siblings" feedback
 *     signal. Setting ``false`` reverts to the v3.0 behaviour
 *     (include every ``DecisionPoint.method`` regardless of verdict
 *     confidence).
 */

import type {
  BehaviorAnchor,
  BranchOutcome,
  BypassPlan,
  CallSite,
  DecisionPoint,
  MethodRef,
} from "../../api/trace";

// ---------------------------------------------------------------------------
// Shared smali-signature helpers — owned by the V3 module since
// v3.X-next.2 deleted the V2 ``executionFlowGraph.ts`` per DEC-031
// N2=(i). All FE consumers (Inspector, LabTraceMode, V3 emitter
// itself) import from here.

/** Smali signature shape — used as the stable node id + dedup key.
 *  Matches :attr:`MethodRef.smali_signature` on the Python side
 *  byte-equally (``Lcom/example/Foo;->bar(I)Z``). */
export function methodKey(m: MethodRef): string {
  const className = (m.class_name || "").replace(/\./g, "/");
  return `L${className};->${m.method_name}(${(m.param_descriptors || []).join("")})${m.return_descriptor || "V"}`;
}

/** Collapse-key for overload merging — drops the descriptor portion
 *  so methods with the same name on the same class but different
 *  param descriptors land on the same node (the node's
 *  ``overloadCount`` then carries the count). */
export function overloadKey(m: MethodRef): string {
  const className = (m.class_name || "").replace(/\./g, "/");
  return `L${className};->${m.method_name}`;
}

/** ``methodKey`` shape but starting from a node id — strips the
 *  trailing ``(...)return-descriptor`` to recover the overload key.
 *  Used by ``ExecutionFlowV3`` to look up firedMethods / liveValues
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
 *  attempt (the overload-key collapsing the ``ExecutionFlowV3`` does
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

// ---------------------------------------------------------------------------
// Public types

export type ExecutionFlowV3NodeKind =
  | "entry"
  | "gate"
  | "method"
  | "return_pill";

export type ExecutionFlowV3Node = {
  id: string;
  kind: ExecutionFlowV3NodeKind;
  /** Operator-facing ``Class.method`` label rendered in the card
   *  title. For ``return_pill`` terminals this carries the
   *  formatted ``Ret: <value>`` label so the renderer doesn't need
   *  to derive it. */
  title: string;
  /** Class portion (Java-form). Empty for ``return_pill``. */
  className: string;
  /** Method name portion. Empty for ``return_pill``. */
  methodName: string;
  /** Source line; ``null`` for terminals + when the slicer didn't
   *  resolve a source-line reference. */
  sourceLine: number | null;
  overloadCount: number;
  hasGateDecision: boolean;
  possiblyInlined: boolean;
  /** Lightweight synthetic flag — currently ``true`` only for
   *  ``return_pill`` terminals; preserved for parity with the v2
   *  shape so the renderer's selection / hit-test guards (sinks
   *  aren't operator-clickable in the Inspector) carry over. */
  isSynthetic: boolean;
  /** Decision count + unclassified count for this method (zero on
   *  terminals + on methods that aren't decision sources). Mirrors
   *  v2's gate-count badge data so the renderer can reuse the same
   *  meta-row pattern. */
  totalGates: number;
  unclassifiedGates: number;
  /** v3-only — for ``return_pill`` terminals, the verdict-derived
   *  return-value short string (``True`` / ``False`` / ``?``). The
   *  full label (``Ret: True``) lives in ``title``. */
  retValue: "True" | "False" | "?" | null;
  /** v3-only — for ``return_pill`` terminals, the source
   *  decision's source method title (``Class.method``) so the
   *  pill's hover-title can identify which gate produced this
   *  return. */
  retSourceTitle: string | null;
  /** v3.1 — per-verdict counts aggregated across every decision on
   *  this method. ``null`` for non-source methods (entry that
   *  isn't itself a decision source, plan-target callees, return
   *  pills). Drives the inline ``verdictSummary`` chip on the gate
   *  card so the operator sees the verdict distribution at a glance
   *  without expanding per-branch pills. The renderer suppresses
   *  the chip when ALL counts are zero (defensive — shouldn't
   *  happen since we only populate the field on methods with at
   *  least one decision).
   *
   *  Counts include verdicts that ALSO have a resolved bypass-plan
   *  target (so the visible callee edge AND the count overlap by
   *  design — the chip is the "total verdicts" view, the visible
   *  edges are the "resolved next-method" view). The two-line
   *  hover-title on the chip spells this out for the operator. */
  verdictSummary: {
    allow: number;
    deny: number;
    neutral: number;
    unverdicted: number;
  } | null;
};

export type ExecutionFlowV3Edge = {
  id: string;
  source: string;
  target: string;
  /** Edge classification:
   *   - ``allow`` / ``deny`` / ``neutral`` — verdict-derived branch
   *     edges from a decision source. Colored per the v2 palette
   *     so the operator's visual training carries over.
   *   - ``unverdicted`` — slicer hit max-walk on the predicate
   *     origin; rendered dim-dashed (same as v2).
   *   - ``call`` — synthesised entry-to-decision-source edge,
   *     emitted ONLY as the Q5=(a) gap-fallback when
   *     ``method_invocations`` is missing or empty on the entry
   *     (e.g. legacy ``trace.sqlite`` caches without the
   *     v3.X-next.1 field). No verdict semantics; rendered as a
   *     neutral arrow without an operator-readable label.
   *   - ``invoke`` — v3.X-next.2 CFG-position-aware invocation
   *     edge sourced from ``BehaviorAnchor.method_invocations``.
   *     Carries the smali ``instruction_index`` + branch-arm
   *     identity (``in_branch_of`` + ``branch_label``) so Dagre's
   *     top-down ranking stacks callees vertically in execution
   *     order rather than the v3.1 flat-fan layout. Multiple
   *     invocations within the same caller's body chain through
   *     ``prev_invoke → next_invoke`` per branch arm; the main
   *     (pre-branch / post-arm) segment chains from the caller
   *     itself. */
  kind: "allow" | "deny" | "neutral" | "unverdicted" | "call" | "invoke";
  /** Edge label rendered alongside the edge. v3 shape:
   *   - ``call`` / ``invoke`` edges: empty string (the arrow + the
   *     CFG-aware vertical stacking are enough; the operator reads
   *     execution order from the layout).
   *   - verdict edges: ``Ret: True`` / ``Ret: False`` / ``Ret: ?``. */
  label: string;
  isCandidateGate: boolean;
  /** v3.X-next.2 — for ``invoke`` edges sourced from
   *  ``method_invocations``, the smali ``instruction_index`` of the
   *  ``invoke-*`` op the edge represents. ``null`` for non-invoke
   *  edges. Surfaced so the Inspector can scroll the Code Browser
   *  to the originating line on hover / click. */
  instructionIndex: number | null;
  /** v3.X-next.2 — for ``invoke`` edges, the ``in_branch_of`` of
   *  the dominating decision (or ``null`` for pre-branch / post-arm
   *  calls). Surfaces the per-arm forking the operator needs to
   *  reason about "this call only happens in the true-arm". */
  inBranchOf: number | null;
  /** v3.X-next.2 — for ``invoke`` edges with a non-null
   *  ``inBranchOf``, the ``Branch.label`` of the arm containing the
   *  call. ``null`` for main-segment invokes + non-invoke edges. */
  branchLabel: string | null;
};

export type ExecutionFlowV3Graph = {
  nodes: ExecutionFlowV3Node[];
  edges: ExecutionFlowV3Edge[];
};

export type ExecutionFlowV3Options = {
  /** Drop the per-branch ``return_pill`` terminals and instead
   *  accumulate the verdict counts into the gate node's
   *  ``verdictSummary`` chip. **v3.1 default ``true``** (was
   *  ``false`` in v3.0) — closes the "horizontal pill sprawl"
   *  feedback signal. Setting ``false`` brings the pills back as a
   *  drill-down escape hatch (``?flow=v3&pills=show``); branches
   *  with a resolved bypass-plan target_method still emit the real-
   *  callee edge regardless of this flag. */
  hideRetPills?: boolean;
  /** Drop methods whose ``class_name`` matches a framework prefix
   *  (``kotlin.*``, ``androidx.*``, ``java.*``, etc. — full list at
   *  :data:`FRAMEWORK_CLASS_PREFIXES`). **v3.1 default ``true``** —
   *  closes the "framework noise as rank-1 siblings" feedback
   *  signal without depending on classifier confidence (the
   *  original v3.1 candidate-gate-only rule failed on all-neutral
   *  anchors). The entry method and resolved bypass-plan targets
   *  are kept unconditionally. Setting ``false`` disables the
   *  framework filter and is exposed via ``?flow=v3&methods=all``
   *  as a debug escape hatch. Name kept as ``gatesOnly`` for URL-
   *  param stability even though the semantics narrowed in
   *  v3.1-fix. */
  gatesOnly?: boolean;
};

// ---------------------------------------------------------------------------
// Locked thresholds — mirror v2's module-level constants.

const HIGH_CONFIDENCE_THRESHOLD = 0.85;

/** v3.1-fix — framework-class prefixes that the ``gatesOnly`` filter
 *  drops by default. The list catches the "framework noise" rank-1
 *  siblings the dogfood screenshots flagged (``Intrinsics.
 *  checkNotNullParam`` etc.) without needing the all-classifier-
 *  candidates path the original v3.1 attempted — that path failed on
 *  anchors where every gate is neutral (e.g. WeakBank ``onCreate$
 *  lambda$1`` with 9 decisions / 0 plans / all-neutral verdicts),
 *  because ``hasCandidateGate`` returned ``false`` for every method
 *  and the filter swept the visual down to just the entry node.
 *
 *  Denylist tier (prefix-matched against Java-dotted ``class_name``):
 *
 *   * Kotlin runtime — ``kotlin.*`` covers
 *     ``kotlin.jvm.internal.Intrinsics``,
 *     ``kotlin.coroutines.*``, ``kotlin.collections.*`` etc.
 *     ``kotlinx.*`` covers ``kotlinx.coroutines.*`` /
 *     ``kotlinx.serialization.*``.
 *   * Android SDK + Jetpack — ``android.*`` (platform),
 *     ``androidx.*`` (Jetpack), ``com.google.android.*``
 *     (GMS / Material).
 *   * JDK — ``java.*``, ``javax.*``, ``sun.*``, ``dalvik.*``.
 *
 *  The list is intentionally NOT exhaustive — it catches the ~95%
 *  of framework noise the operator wants gone without false-
 *  positiving app code. Operator can flip to the v3.0 permissive
 *  default via ``?flow=v3&methods=all`` if they want a noisy method
 *  in (rare; e.g. inspecting a third-party SDK gate). */
const FRAMEWORK_CLASS_PREFIXES = [
  "kotlin.",
  "kotlinx.",
  "androidx.",
  "android.",
  "java.",
  "javax.",
  "com.google.android.",
  "com.google.gson.",
  "dalvik.",
  "sun.",
] as const;

function isFrameworkClass(className: string): boolean {
  return FRAMEWORK_CLASS_PREFIXES.some((p) => className.startsWith(p));
}

// ---------------------------------------------------------------------------
// Helpers — same shape semantics as v2.

/** Java-form ``class.method`` for the card title. */
function titleOf(m: MethodRef): string {
  const cls = (m.class_name || "").split(".").pop() || m.class_name;
  return `${cls}.${m.method_name}`;
}

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
      unclassified += 1;
      continue;
    }
    const hasActionable = o.verdicts.some(
      (v) => v.verdict === "allow" || v.verdict === "deny",
    );
    if (!hasActionable) {
      unclassified += 1;
    }
  }
  return { total, unclassified };
}

/** v3.1 — aggregate per-verdict counts for a single method,
 *  summed across every decision on that method. The renderer
 *  turns this into the inline ``verdictSummary`` chip on the gate
 *  card (e.g. ``2 allow · 1 deny · 8 ?``).
 *
 *  Bucket assignment:
 *   * ``allow`` / ``deny`` / ``neutral`` — one increment per
 *     ``BranchVerdict`` whose ``verdict`` matches. A decision can
 *     contribute multiple verdicts (mixed-outcome cases), all
 *     counted independently — matches v2's per-verdict edge
 *     emission accounting.
 *   * ``unverdicted`` — one increment per decision that has NO
 *     ``branch_outcome`` at all (slicer hit max-walk on the
 *     predicate origin). Surfaces the "classifier didn't even try"
 *     bucket so the operator can spot slicer-blind decisions
 *     separately from classifier-uncertain ``neutral`` verdicts.
 *
 *  Returns ``null`` when no decisions exist for this key (the
 *  caller uses this to decide whether to render the chip at all). */
function verdictSummaryFor(
  decisions: DecisionPoint[],
  key: string,
): { allow: number; deny: number; neutral: number; unverdicted: number } | null {
  let allow = 0;
  let deny = 0;
  let neutral = 0;
  let unverdicted = 0;
  let any = false;
  for (const d of decisions) {
    if (overloadKey(d.method) !== key) continue;
    any = true;
    const o: BranchOutcome | null = d.branch_outcome;
    if (!o || o.verdicts.length === 0) {
      unverdicted += 1;
      continue;
    }
    for (const v of o.verdicts) {
      if (v.verdict === "allow") allow += 1;
      else if (v.verdict === "deny") deny += 1;
      else neutral += 1;
    }
  }
  return any ? { allow, deny, neutral, unverdicted } : null;
}

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

/** Map a verdict (``allow`` / ``deny`` / ``neutral``) to the v3
 *  concrete return-value label. The mapping is:
 *
 *   * ``allow`` → ``True`` — the predicate returned a value the
 *     classifier read as check-passes.
 *   * ``deny`` → ``False`` — the predicate's value reads as
 *     check-fails.
 *   * anything else (including unverdicted / no outcome) → ``?``.
 *
 *  This is the operator-facing shape the target screenshot uses.
 *  The mapping is heuristic — branches that "allow" don't strictly
 *  imply the predicate returned the boolean ``true``; the v2
 *  classifier groups conceptually "passing" verdicts under
 *  ``allow``. v3 honors the operator's preferred presentation
 *  (``Ret: True`` vs the more abstract ``allowed``). */
function retValueForVerdict(
  verdict: string,
): "True" | "False" | "?" {
  if (verdict === "allow") return "True";
  if (verdict === "deny") return "False";
  return "?";
}

// ---------------------------------------------------------------------------
// Public entry point

export function buildExecutionFlowV3Graph(
  anchor: BehaviorAnchor,
  opts: ExecutionFlowV3Options = {},
): ExecutionFlowV3Graph {
  // v3.1 defaults — both ``true``. Closes the two big dogfood
  // signals from v3.0: horizontal pill-row sprawl + framework
  // noise as rank-1 siblings. URL escape hatches (``pills=show``,
  // ``methods=all``) can flip these back.
  const hideRetPills = opts.hideRetPills ?? true;
  const gatesOnly = opts.gatesOnly ?? true;

  // Phase 1 — collect MethodRef sources. Same dedup-on-overloadKey
  // shape as v2 but tracks a narrower set of provenance flags
  // because v3 doesn't need the inlined inference for synthetic-sink
  // edge routing.
  const methods = new Map<string, CallerCollector>();

  const ingest = (
    m: MethodRef,
    flags: {
      isDecisionSource?: boolean;
      isPlanTarget?: boolean;
      isInvokeTarget?: boolean;
    } = {},
  ) => {
    const k = overloadKey(m);
    const sig = methodKey(m);
    const existing = methods.get(k);
    if (existing) {
      existing.descriptors.add(sig);
      existing.overloadCount = existing.descriptors.size;
      existing.isDecisionSource ||= !!flags.isDecisionSource;
      existing.isPlanTarget ||= !!flags.isPlanTarget;
      existing.isInvokeTarget ||= !!flags.isInvokeTarget;
    } else {
      methods.set(k, {
        canonical: m,
        overloadCount: 1,
        descriptors: new Set([sig]),
        isDecisionSource: !!flags.isDecisionSource,
        isPlanTarget: !!flags.isPlanTarget,
        isInvokeTarget: !!flags.isInvokeTarget,
      });
    }
  };

  ingest(anchor.entry_method);
  const entryKey = overloadKey(anchor.entry_method);
  const entryId = methodKey(
    methods.get(entryKey)!.canonical,
  );

  for (const d of anchor.decisions) {
    ingest(d.method, { isDecisionSource: true });
  }
  const allPlans: BypassPlan[] = [
    ...anchor.plans,
    ...anchor.advanced_plans,
  ];
  for (const p of allPlans) {
    if (p.source_decision_method) {
      ingest(p.source_decision_method, { isDecisionSource: true });
    }
    if (p.target_method) {
      ingest(p.target_method, { isPlanTarget: true });
    }
  }

  // v3.X-next.2 / DEC-031 — ingest ``method_invocations`` callers
  // + callees so the v3.X-next.2 invoke-chain emission has all the
  // surviving callee nodes available. Callers are typically already
  // ingested (they're decision sources / entry / plan targets), but
  // we re-ingest defensively in case a future slicer surfaces an
  // invocation map for a method that's none of those (cheap; the
  // ``ingest`` helper dedupes on overloadKey). Callees may be
  // novel — e.g. ``validate_pin`` / ``create_session`` in WeakBank
  // dogfood are called from entry but are not themselves decision
  // sources or plan targets, so the v3.1 emitter missed them
  // entirely.
  //
  // Callees are tagged ``isInvokeTarget`` (NOT ``isPlanTarget``)
  // so the provenance splits correctly:
  //   * The ``gatesOnly`` framework filter's bypass stays scoped
  //     to operator-opted-in entries (entry / plan target); an
  //     invoke-only framework callee — defensive only since the
  //     slicer denylists those — still gets filtered out, matching
  //     the backstop's stated intent.
  //   * The ``possiblyInlined`` heuristic stays
  //     (``!isDecisionSource && isPlanTarget``); being called via
  //     an ``invoke-*`` op no longer false-flags a method as
  //     possibly-inlined (the very opposite signal).
  const methodInvocations = anchor.method_invocations ?? {};
  for (const [callerSig, callSites] of Object.entries(methodInvocations)) {
    if (!callSites || callSites.length === 0) continue;
    // The caller key is the smali signature, not a MethodRef. The
    // first call site's ``caller`` field carries the MethodRef
    // (all sites in a list share the same caller per slicer
    // contract). Defensive guard: if the key shape doesn't match
    // the first caller's methodKey, log + skip.
    const firstCaller = callSites[0]?.caller;
    if (!firstCaller || methodKey(firstCaller) !== callerSig) continue;
    ingest(firstCaller);
    for (const cs of callSites) {
      ingest(cs.callee, { isInvokeTarget: true });
    }
  }

  // Phase 2 — assemble nodes. Filter logic depends on ``gatesOnly``:
  //
  //   * ``gatesOnly = true`` (v3.1 default): drop framework-package
  //     methods (``kotlin.*``, ``androidx.*``, ``java.*`` etc. per
  //     ``FRAMEWORK_CLASS_PREFIXES``). The entry method and resolved
  //     bypass-plan targets stay visible even when their class is
  //     in a framework package (operator opted in by either
  //     supplying the entry or having the plan layer cross-reference
  //     the method). v3.1's previous "candidate-gate-only" rule was
  //     replaced because it collapsed all-neutral anchors to just
  //     the entry node — useless visual on anchors with low signal.
  //   * ``gatesOnly = false`` (escape hatch via
  //     ``?flow=v3&methods=all``): no framework filtering; every
  //     decision source + plan target survives. Matches v3.0 default
  //     behaviour for the rare case the operator wants to inspect a
  //     framework method.
  //
  // ``verdictSummary`` populates on every surviving method that has
  // at least one decision; the renderer turns it into the inline
  // ``2 allow · 1 deny · 8 ?`` chip on the gate card so the operator
  // sees the verdict distribution at-a-glance even when every
  // decision is neutral (where the chip reads e.g. ``9 ?`` — louder
  // than the v3.0 silent treatment).
  const nodes: ExecutionFlowV3Node[] = [];
  for (const [key, c] of methods.entries()) {
    const isEntry = key === entryKey;
    const hasGate = hasCandidateGate(anchor.decisions, key);
    if (
      !isEntry &&
      !c.isDecisionSource &&
      !c.isPlanTarget &&
      !c.isInvokeTarget
    ) {
      // Defensive — shouldn't happen with the collection passes above
      // but cheap to guard against future ingestion paths.
      continue;
    }
    // Framework-class filter bypass is intentionally scoped to
    // ``isEntry`` + ``isPlanTarget`` (operator-opted-in sources).
    // ``isInvokeTarget`` is NOT a bypass channel: the slicer
    // already denylists framework callees, so an invoke-only
    // framework callee should never appear in production —
    // dropping it here is the defensive backstop.
    if (
      gatesOnly &&
      !isEntry &&
      !c.isPlanTarget &&
      isFrameworkClass(c.canonical.class_name)
    ) {
      continue;
    }
    const gateCounts = gateCountsFor(anchor.decisions, key);
    const summary = verdictSummaryFor(anchor.decisions, key);
    nodes.push({
      id: methodKey(c.canonical),
      kind: isEntry ? "entry" : (hasGate ? "gate" : "method"),
      title: titleOf(c.canonical),
      className: c.canonical.class_name,
      methodName: c.canonical.method_name,
      sourceLine: sourceLineFor(anchor.decisions, key),
      overloadCount: c.overloadCount,
      hasGateDecision: hasGate,
      possiblyInlined: !c.isDecisionSource && c.isPlanTarget,
      isSynthetic: false,
      totalGates: gateCounts.total,
      unclassifiedGates: gateCounts.unclassified,
      retValue: null,
      retSourceTitle: null,
      verdictSummary: summary,
    });
  }

  const nodeIds = new Set(nodes.map((n) => n.id));

  // Phase 3 — emit edges + return-pill terminals.
  const edges: ExecutionFlowV3Edge[] = [];
  const returnPills: ExecutionFlowV3Node[] = [];

  // v3.X-next.2 / DEC-031 Q5=(a) — emit CFG-position-aware invoke
  // edges from ``method_invocations`` first (the primary path).
  // The function below populates ``edges`` with one ``invoke`` edge
  // per ``CallSite`` whose endpoints both survive the
  // ``gatesOnly`` filter, chained per-branch-arm so Dagre's
  // top-down ranking stacks callees vertically in execution order.
  // Returns the set of ``methodKey``s that contributed at least
  // one outgoing invoke edge — used below to decide which methods
  // need the v3.1 synthesised-call fallback.
  const callersWithInvokes = emitInvokeEdges(
    anchor.method_invocations ?? {},
    methods,
    nodeIds,
    edges,
  );

  // Q5=(a) gap-fallback — v3.1 synthesised entry → decision-source
  // ``call`` edges, emitted ONLY when the entry's
  // ``method_invocations`` entry is missing or empty (legacy
  // ``trace.sqlite`` caches without the v3.X-next.1 field, or live
  // anchors where the slicer didn't surface any non-framework
  // invokes in entry's body — e.g. entry is a pure dispatcher
  // calling only framework methods that the slicer filtered).
  // Preserves the empty-dict-renders-as-v3.1 invariant ratified at
  // v3.X-next.2.0 (TASKS.md § Phase 13 v3.X-next sub-step backlog).
  // Q5=(b)/(c) call-graph recovery deferred to v3.X-next.3
  // candidate stub per the diminishing-returns analysis at
  // v3.X-next.2.0 (see DEC-031 / STATE.md).
  if (!callersWithInvokes.has(entryId)) {
    const calledFromEntry = new Set<string>();
    for (const d of anchor.decisions) {
      const k = overloadKey(d.method);
      if (k === entryKey) continue;
      const target = methods.get(k);
      if (!target) continue;
      const tgtId = methodKey(target.canonical);
      if (!nodeIds.has(tgtId)) continue;
      if (calledFromEntry.has(tgtId)) continue;
      calledFromEntry.add(tgtId);
      edges.push({
        id: `${entryId}->${tgtId}#call`,
        source: entryId,
        target: tgtId,
        kind: "call",
        label: "",
        isCandidateGate: false,
        instructionIndex: null,
        inBranchOf: null,
        branchLabel: null,
      });
    }
  }

  // Verdict edges — one per (decision × verdict).
  for (const d of anchor.decisions) {
    const sourceCollector = methods.get(overloadKey(d.method));
    if (!sourceCollector) continue;
    const sourceId = methodKey(sourceCollector.canonical);
    if (!nodeIds.has(sourceId)) continue;
    const sourceTitle = titleOf(sourceCollector.canonical);
    const planTarget = planTargetFor(d, allPlans);

    const verdicts = d.branch_outcome?.verdicts ?? [];
    const isCandidate =
      d.branch_outcome != null &&
      d.branch_outcome.confidence >= HIGH_CONFIDENCE_THRESHOLD;

    if (verdicts.length === 0) {
      // No outcome — emit a single unverdicted dangling pill (or
      // skip when ``hideRetPills``).
      if (!hideRetPills) {
        const pillId = `__retpill__::${sourceId}#${d.instruction_index}#unv`;
        returnPills.push({
          id: pillId,
          kind: "return_pill",
          title: "Ret: ?",
          className: "",
          methodName: "",
          sourceLine: null,
          overloadCount: 1,
          hasGateDecision: false,
          possiblyInlined: false,
          isSynthetic: true,
          totalGates: 0,
          unclassifiedGates: 0,
          retValue: "?",
          retSourceTitle: sourceTitle,
          verdictSummary: null,
        });
        edges.push({
          id: `${sourceId}->${pillId}#${d.instruction_index}`,
          source: sourceId,
          target: pillId,
          kind: "unverdicted",
          label: "Ret: ?",
          isCandidateGate: false,
          instructionIndex: null,
          inBranchOf: null,
          branchLabel: null,
        });
      }
      continue;
    }

    for (let i = 0; i < verdicts.length; i++) {
      const v = verdicts[i];
      const verdictKind: ExecutionFlowV3Edge["kind"] =
        v.verdict === "allow"
          ? "allow"
          : v.verdict === "deny"
            ? "deny"
            : "neutral";
      const retLabel = retValueForVerdict(v.verdict);

      // Route to plan target when one is resolved AND the verdict
      // is ``allow`` (the bypass plan's target_method is what the
      // operator wants to see flowing from the passing branch).
      // Otherwise emit a return_pill terminal.
      let targetId: string;
      const planMatch =
        planTarget && v.verdict === "allow"
          ? methods.get(overloadKey(planTarget))
          : null;

      if (planMatch) {
        const planTargetId = methodKey(planMatch.canonical);
        if (nodeIds.has(planTargetId)) {
          targetId = planTargetId;
        } else if (!hideRetPills) {
          // Plan target was filtered out by ``gatesOnly`` (or some
          // future filter) — fall back to a return pill so the
          // branch still has a visible terminator.
          const pillId = `__retpill__::${sourceId}#${d.instruction_index}#${i}`;
          returnPills.push({
            id: pillId,
            kind: "return_pill",
            title: `Ret: ${retLabel}`,
            className: "",
            methodName: "",
            sourceLine: null,
            overloadCount: 1,
            hasGateDecision: false,
            possiblyInlined: false,
            isSynthetic: true,
            totalGates: 0,
            unclassifiedGates: 0,
            retValue: retLabel,
            retSourceTitle: sourceTitle,
            verdictSummary: null,
          });
          targetId = pillId;
        } else {
          continue;
        }
      } else if (!hideRetPills) {
        const pillId = `__retpill__::${sourceId}#${d.instruction_index}#${i}`;
        returnPills.push({
          id: pillId,
          kind: "return_pill",
          title: `Ret: ${retLabel}`,
          className: "",
          methodName: "",
          sourceLine: null,
          overloadCount: 1,
          hasGateDecision: false,
          possiblyInlined: false,
          isSynthetic: true,
          totalGates: 0,
          unclassifiedGates: 0,
          retValue: retLabel,
          retSourceTitle: sourceTitle,
          verdictSummary: null,
        });
        targetId = pillId;
      } else {
        continue;
      }

      edges.push({
        id: `${sourceId}->${targetId}#${d.instruction_index}#${i}`,
        source: sourceId,
        target: targetId,
        kind: verdictKind,
        label: `Ret: ${retLabel}`,
        isCandidateGate: isCandidate,
        instructionIndex: null,
        inBranchOf: null,
        branchLabel: null,
      });
    }
  }

  nodes.push(...returnPills);
  return { nodes, edges };
}

// ---------------------------------------------------------------------------
// v3.X-next.2 — invoke-edge emission helper
//
// Walks ``method_invocations`` (the v3.X-next.1 slicer extension)
// and emits one ``invoke`` edge per ``CallSite`` whose caller +
// callee both have surviving nodes. Chains consecutive calls within
// the same ``(in_branch_of, branch_label)`` arm so the resulting
// graph stacks callees vertically in execution order under their
// caller — closing the v3.1 "horizontal-fan flatness" friction
// DEC-031 was diagnosed to fix.
//
// Algorithm (per caller, sorted by instruction_index):
//   * Track a per-arm "last node" frame keyed on the
//     (in_branch_of, branch_label) tuple. Main-segment calls
//     (in_branch_of == null) live in their own frame anchored at
//     the caller.
//   * On encountering a CallSite:
//       1. Compute the frame key.
//       2. Look up the frame's current ``lastNodeId``; if absent
//          (first call in this arm), anchor at the caller itself.
//       3. Skip if the callee's node id isn't in ``nodeIds`` (it
//          was filtered out by the ``gatesOnly`` framework filter
//          or never registered as a method — defensive).
//       4. Push an ``invoke`` edge ``lastNodeId → calleeId`` with
//          the per-edge ``instruction_index`` + branch-arm
//          metadata.
//       5. Update the frame's ``lastNodeId`` to the callee so the
//          next call in the same arm chains onto it.
//
// Returns the set of caller node ids that emitted at least one
// invoke edge — the caller uses this to decide which entries need
// the Q5=(a) gap-fallback synthesised-call edges (v3.1 behaviour
// kicks in when entry has no surviving invokes).

type CallerCollector = {
  canonical: MethodRef;
  overloadCount: number;
  descriptors: Set<string>;
  isDecisionSource: boolean;
  isPlanTarget: boolean;
  /** v3.X-next.2 — method appears at least once as a CallSite
   *  callee in ``BehaviorAnchor.method_invocations``. Splits the
   *  invoke-only provenance off ``isPlanTarget`` so:
   *    * The ``gatesOnly`` framework-filter bypass stays scoped to
   *      operator-opted-in plan targets (an invoke-only framework
   *      callee — defensive only since the slicer denylists those
   *      — is filtered out, matching the backstop's stated intent).
   *    * The ``possiblyInlined`` heuristic
   *      (``!isDecisionSource && isPlanTarget``) no longer false-
   *      flags every invoke callee as "possibly inlined" — being
   *      called via an ``invoke-*`` op is the very opposite of
   *      being inlined.
   *  Still acts as a node-survival signal (an invoke-only callee
   *  IS reachable from the closure and SHOULD render). */
  isInvokeTarget: boolean;
};

function emitInvokeEdges(
  methodInvocations: Record<string, CallSite[]>,
  // ``methods`` is reserved for future use (e.g. surfacing
  // ``possiblyInlined`` flags on invoke targets that lack a
  // smali body); not consumed in v3.X-next.2's first cut since
  // node-ingestion already happened in the caller's Phase 1.
  _methods: Map<string, CallerCollector>,
  nodeIds: Set<string>,
  edges: ExecutionFlowV3Edge[],
): Set<string> {
  const callersWithInvokes = new Set<string>();
  for (const [callerSig, callSites] of Object.entries(methodInvocations)) {
    if (!callSites || callSites.length === 0) continue;
    if (!nodeIds.has(callerSig)) continue;
    // Defensive sort — slicer guarantees instruction_index order
    // but a future caller could pass a tuple in any order. Cheap.
    const sorted = [...callSites].sort(
      (a, b) => a.instruction_index - b.instruction_index,
    );
    const arms = new Map<string, string>();
    for (const cs of sorted) {
      const calleeId = methodKey(cs.callee);
      if (!nodeIds.has(calleeId)) continue;
      const armKey =
        cs.in_branch_of == null
          ? "__main__"
          : `${cs.in_branch_of}:${cs.branch_label ?? ""}`;
      const lastNodeId = arms.get(armKey) ?? callerSig;
      // Don't emit self-loops (caller == callee on a recursive
      // call would otherwise produce a node-onto-itself edge that
      // Dagre handles weirdly).
      if (lastNodeId === calleeId) {
        // Still advance the chain — subsequent calls in this arm
        // should chain off the callee, not the caller. (e.g.
        // ``A() { foo(); foo(); bar(); }`` — bar should chain
        // after the second foo, not back to A.)
        arms.set(armKey, calleeId);
        continue;
      }
      edges.push({
        id: `${lastNodeId}->${calleeId}#invoke#${callerSig}#${cs.instruction_index}`,
        source: lastNodeId,
        target: calleeId,
        kind: "invoke",
        label: "",
        isCandidateGate: false,
        instructionIndex: cs.instruction_index,
        inBranchOf: cs.in_branch_of,
        branchLabel: cs.branch_label,
      });
      arms.set(armKey, calleeId);
      callersWithInvokes.add(callerSig);
    }
  }
  return callersWithInvokes;
}

// ---------------------------------------------------------------------------
// Diagnostics — exported for the preview UI's debug overlay.

/** Quick stats on the emitted graph — used by the v3 preview's
 *  bottom-bar dev overlay so the operator can see, at a glance,
 *  how many of each node-kind landed and whether any branches
 *  dangled. ``dangling`` counts edges whose target ids don't
 *  resolve in ``nodes`` — should always be ``0`` for the v3
 *  emitter (every emitted edge points at a known node id), but the
 *  field is checked anyway so a regression in the emitter is
 *  visible in the preview without a console open. */
export function graphV3Stats(g: ExecutionFlowV3Graph): {
  total: number;
  entry: number;
  gate: number;
  method: number;
  returnPills: number;
  edges: number;
  dangling: number;
  callEdges: number;
  /** v3.X-next.2 — count of ``invoke`` edges sourced from
   *  ``method_invocations``. When ``invokeEdges > 0`` the
   *  CFG-position-aware ranking is active for this anchor; when
   *  ``invokeEdges === 0 && callEdges > 0`` the Q5=(a) gap-fallback
   *  to v3.1 synthesised entry→gate edges is active (legacy
   *  ``trace.sqlite`` caches, or anchors where the slicer didn't
   *  surface any non-framework invokes). The dev overlay can use
   *  this discrimination to surface "CFG-aware" vs "v3.1 fallback"
   *  state for operator inspection without re-walking the anchor. */
  invokeEdges: number;
  verdictEdges: number;
  /** v3.1 — aggregate verdict counts across every surviving gate
   *  node's ``verdictSummary``. Mirrors the per-card chip totals so
   *  the dev overlay can confirm the chip arithmetic matches the
   *  underlying anchor data. */
  summary: {
    allow: number;
    deny: number;
    neutral: number;
    unverdicted: number;
  };
} {
  const ids = new Set(g.nodes.map((n) => n.id));
  let entry = 0;
  let gate = 0;
  let method = 0;
  let pills = 0;
  let allow = 0;
  let deny = 0;
  let neutral = 0;
  let unverdicted = 0;
  for (const n of g.nodes) {
    if (n.kind === "entry") entry += 1;
    else if (n.kind === "gate") gate += 1;
    else if (n.kind === "method") method += 1;
    else if (n.kind === "return_pill") pills += 1;
    if (n.verdictSummary) {
      allow += n.verdictSummary.allow;
      deny += n.verdictSummary.deny;
      neutral += n.verdictSummary.neutral;
      unverdicted += n.verdictSummary.unverdicted;
    }
  }
  let dangling = 0;
  let callEdges = 0;
  let invokeEdges = 0;
  let verdictEdges = 0;
  for (const e of g.edges) {
    if (!ids.has(e.target) || !ids.has(e.source)) dangling += 1;
    if (e.kind === "call") callEdges += 1;
    else if (e.kind === "invoke") invokeEdges += 1;
    else verdictEdges += 1;
  }
  return {
    total: g.nodes.length,
    entry,
    gate,
    method,
    returnPills: pills,
    edges: g.edges.length,
    dangling,
    callEdges,
    invokeEdges,
    verdictEdges,
    summary: { allow, deny, neutral, unverdicted },
  };
}
