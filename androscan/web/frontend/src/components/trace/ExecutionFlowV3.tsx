/**
 * Behavior Trace **V3** — flowchart renderer (production).
 *
 * v3.X-next.2 promoted V3 from preview to production: the
 * ``?flow=v3`` URL gate + the v2-pair (``ExecutionFlow.tsx`` +
 * ``executionFlowGraph.ts``) were deleted in the same commit that
 * landed the CFG-position-aware ``method_invocations`` consumer.
 * V3 is now the sole flowchart renderer and the v2 path has been
 * archived per DEC-031 N2 = (i) (immediate-deletion of the v2
 * pair, no transition window).
 *
 * **What's different from v2.0**:
 *
 *   1. **Top-down (rankdir TB) Dagre layout.** Handles attach at the
 *      ``top`` (target) + ``bottom`` (source) of each card so the
 *      flow reads vertically — entry method on top, decision-source
 *      methods below, ``return_pill`` terminals at the bottom. The
 *      Dagre layout produces the true tree indentation operators
 *      asked for (the v2 column-grid couldn't, because all rank-0
 *      nodes collided horizontally regardless of their actual
 *      relationship in the call graph).
 *   2. **No synthetic ALLOW / DENY / neutral sink nodes.** Branches
 *      either route into real callee methods (resolved from
 *      ``BypassPlan.target_method``) or terminate at small
 *      ``return_pill`` chips carrying a ``Ret: True`` / ``Ret:
 *      False`` / ``Ret: ?`` concrete return-value label. The pills
 *      are visually subordinate to the method cards so the
 *      operator's eye still lands on the methods first.
 *   3. **Concrete ``Ret: <value>`` edge labels.** Each verdict edge
 *      carries a ``Ret: True`` / ``Ret: False`` / ``Ret: ?`` chip
 *      mid-path instead of the v2 ``allowed`` / ``denied`` /
 *      ``passes through`` verdict labels. Mirrors the target
 *      screenshot the operator shared.
 *   4. **Entry method accent.** The entry node renders with a
 *      thicker accent-blue border + ``ENTRY`` corner pill so it
 *      reads as the root of the flow at a glance.
 *   5. **Synthesised entry → gate ``call`` edges.** Renders the
 *      entry as the connected root of the tree (v2's column grid
 *      left it floating).
 *
 * **v3.1 patch** (this revision):
 *
 *   * **Default to gates-only.** The emitter's ``gatesOnly`` opt
 *     defaults ``true`` so framework noise drops out of the visual
 *     by default. Escape hatch via ``?flow=v3&methods=all``.
 *   * **Collapse the per-branch ret-pill row into a verdict-summary
 *     chip on the gate card.** ``hideRetPills`` defaults ``true``;
 *     gate cards carry a compact ``2 allow · 1 deny · 8 ?`` chip
 *     in the meta row instead of a horizontal row of 10× ``Ret: ?``
 *     pills below the gate. Escape hatch via
 *     ``?flow=v3&pills=show``.
 *   * **Restore the within-page fullscreen toggle** — ports the
 *     v2.0 button + ESC handler verbatim. The ``.execution-flow-
 *     container-fullscreen`` rule from v2.0.1's hotfix still pins
 *     ``width: 100vw; height: 100vh;`` so the v3 path inherits the
 *     same viewport-fill behaviour without a separate CSS rule.
 *
 * **v3.X-next.2 additions** (this revision):
 *
 *   * **``method_invocations`` consumer.** The renderer now ingests
 *     ``BehaviorAnchor.method_invocations`` (CallSite sequences
 *     keyed by caller signature) and emits ``invoke`` edges that
 *     carry the smali ``instruction_index`` + branch-arm identity.
 *     Dagre's top-down ranking then stacks callees vertically in
 *     true execution order rather than the v3.1 flat-fan layout.
 *     The pre-v3.X-next.2 synthesised ``call`` edges survive as
 *     the Q5=(a) gap-fallback for caches built before the backend
 *     emitted the field (legacy ``trace.sqlite`` rows or anchors
 *     that the slicer truncated before any invocation was
 *     captured).
 *   * **Dynamic-overlay surface ported from v2.** Fired-method
 *     accent + fired-edge emphasis + untaken-dynamic edge fade +
 *     depth pill on fired nodes + live-value chip on fired edges
 *     + runtime ``hook_failed`` "inlined (runtime)" decoration.
 *     Mode toggle (Static / Dynamic / Both) drives all four.
 *   * **N6 verdict-chip relabels.** Per DEC-031 N6 the terse
 *     ``9 ?`` / ``9 unv`` chip labels grew to plain-language
 *     ``9 neutral?`` / ``9 unverdicted?`` with per-sub-chip hover
 *     tooltips sourced from ``copy.ts``. The aggregate chip-level
 *     hover still spells out the per-bucket totals + the
 *     visibility caveat (the per-bucket counts ALSO include
 *     verdicts that the rendered next-method edge already
 *     reflects).
 *   * **N7 source-line pill clickable.** When the consumer wires
 *     an ``onSourceLineClick`` callback, the ``line N`` pill on a
 *     method card becomes a button that fires the callback with
 *     ``(className, methodName, sourceLine)`` — ``LabTraceMode``
 *     plugs it into ``setPendingCodeNav`` + ``setTab("inspect")``
 *     for one-click jump to the Code Browser.
 *   * **N8 title shape swap.** Primary line on the card is now the
 *     method name only (``validatePin``) with the fully-qualified
 *     ``MainActivity.validatePin`` as the secondary line. The old
 *     v3.1 pattern (``MainActivity.validatePin`` primary +
 *     ``MainActivity`` chip) was redundant — the chip's value was
 *     already encoded in the primary.
 *
 * **v3.X-next.4 additions** (this revision):
 *
 *   * **N3 + N4 + N5 hover-expand-card.** Hovering (or
 *     ``:focus-within``) a non-pill card scales it to 1.5× via a
 *     CSS-only ``transform: scale(1.5)`` + ``z-index: 50`` lift
 *     (matches the gate-count badge's 80ms ease-out so the
 *     affordance feels familiar). The expanded card reveals a
 *     ``.execution-flow-node-hover-detail`` block carrying the N5
 *     quick-look content set: decision-predicate count (sourced
 *     from the existing ``verdictSummary`` field), bypass-plan
 *     count (sourced from the new ``bypassPlanCount`` field added
 *     in the same commit), runtime depth + thread when the node
 *     is fired, and runtime-inlined reason when Frida's
 *     ``hook_failed`` event landed. The full content set N5
 *     describes (per-method LLM summary preview, predicate-origin
 *     breakdown, per-plan target/predicate/kind rendering)
 *     intentionally stays in the Inspector — this surface is a
 *     *quick-look preview*, not a duplicate, so operators can scan
 *     a flowchart of cards without opening the right pane for
 *     every node. Promoted from the v3.X-next.4 candidate stub
 *     after operator dogfood selection (no operator-visible
 *     density complaint, but the next-slot pick prioritised the
 *     UX-expansion path over the next mechanical fix).
 *
 * **Dagre integration notes**:
 *
 *   * We use ``@dagrejs/dagre`` v3 — API surface is stable enough
 *     to be drop-in compatible with older 1.x code. The library
 *     mutates the graph in-place; we read positions back via
 *     ``g.node(id).x``/``y`` after ``dagre.layout(g)``.
 *   * Node positions Dagre returns are CENTER points; React Flow
 *     wants TOP-LEFT positions. We translate by half the node's
 *     ``width``/``height`` before handing them to React Flow.
 *   * The layout is run on every ``buildGraph`` invocation; the
 *     graph is small (≤ ~30 nodes) so a fresh Dagre pass per
 *     render is fine. v2's ``useMemo`` pattern keeps the cost
 *     bounded across renders that share an anchor.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  getSmoothStepPath,
  type Edge,
  type EdgeProps,
  type EdgeTypes,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "@dagrejs/dagre";

import type {
  BehaviorAnchor,
  HookFailureRecord,
  LiveValueRecord,
} from "../../api/trace";
import {
  buildExecutionFlowV3Graph,
  graphV3Stats,
  overloadKeyFromNodeId,
  type ExecutionFlowV3Edge,
  type ExecutionFlowV3Node,
  type ExecutionFlowV3Options,
} from "./executionFlowGraphV3";
import type { TraceMode } from "./TraceModeToggle";
import { VERDICT_CHIP_LABELS, VERDICT_CHIP_TOOLTIPS } from "./copy";

// ---------------------------------------------------------------------------
// Live-value chip formatter — ported verbatim from v2's
// ``ExecutionFlow.tsx`` (DEC-031 N2=(i) immediate-deletion of the
// v2 pair means V3 owns this helper now). Pre-formats the per-edge
// "args → ret" chip so the renderer doesn't need to know about
// ``LiveValueRecord``'s shape.

const LIVE_LABEL_BUDGET_CHARS = 56;
const LIVE_LABEL_ARG_BUDGET = 24;

function composeLiveLabel(live: LiveValueRecord | null): string | null {
  if (!live) return null;
  const truncate = (s: string, n: number) =>
    s.length <= n ? s : s.slice(0, Math.max(0, n - 1)) + "…";
  const args = (live.args ?? [])
    .slice(0, 4)
    .map((a) => truncate(a, LIVE_LABEL_ARG_BUDGET));
  const argsPart = args.length > 0 ? args.join(", ") : "";
  const moreArgs =
    (live.args?.length ?? 0) > 4 ? `, +${(live.args?.length ?? 0) - 4} more` : "";
  const retPart =
    live.ret != null ? ` → ${truncate(live.ret, LIVE_LABEL_ARG_BUDGET)}` : "";
  const out = `${argsPart}${moreArgs}${retPart}`.trim();
  if (!out) return null;
  return truncate(out, LIVE_LABEL_BUDGET_CHARS);
}

// ---------------------------------------------------------------------------
// Layout constants — v3 is top-down (rankdir TB) so the pitch
// constants are vertical-leading rather than v2's horizontal-leading.

const NODE_WIDTH = 220;
const NODE_HEIGHT = 72;
const PILL_WIDTH = 96;
const PILL_HEIGHT = 32;
const RANK_GAP = 80; // vertical gap between rank layers
const NODE_GAP = 50; // horizontal gap between siblings within a rank

const ARROWHEAD_SIZE = 6;
const EDGE_STROKE_WIDTH = 1.5;

// ---------------------------------------------------------------------------
// Props

type Props = {
  anchor: BehaviorAnchor;
  selectedNodeId?: string | null;
  onNodeClick?: (node: ExecutionFlowV3Node) => void;
  /** v3.X-next.2 — opt-in toggle to drop the per-branch
   *  ``return_pill`` terminals and accumulate verdict counts into
   *  the gate card's chip. Default ``undefined`` lets the emitter's
   *  v3.1-baseline default (``true``) win. Operator override path
   *  is the legacy ``?pills=show`` URL param wired through
   *  ``LabTraceMode``. */
  hideRetPills?: boolean;
  /** v3.X-next.2 — opt-in toggle to disable the framework-package
   *  filter (``kotlin.*``, ``androidx.*``, ``java.*`` etc. per
   *  ``FRAMEWORK_CLASS_PREFIXES``). Default ``undefined`` lets the
   *  emitter's v3.1 default (``true``) win. Operator override path
   *  is the legacy ``?methods=all`` URL param. */
  gatesOnly?: boolean;
  /** Phase 13 sub-step 13.8 — overlay mode (Static / Dynamic /
   *  Both). Drives fired-edge / fired-node accent rendering plus
   *  the untaken-edge fade in ``"dynamic"`` mode. Default
   *  ``"static"`` so call sites that don't yet pass the prop keep
   *  their original rendering. */
  mode?: TraceMode;
  /** Phase 13 sub-step 13.8 — set of overload keys (descriptor-
   *  stripped Smali) that have fired during the current dynamic
   *  trace. Empty in ``"static"`` mode. */
  firedMethods?: ReadonlySet<string>;
  /** Phase 13 sub-step 13.8 — per-method live values (latest fire's
   *  args / return / thread + fire count) keyed by overload key.
   *  Used to populate the depth pill on fired nodes + the live-
   *  value chip on fired edges. Empty in ``"static"`` mode. */
  liveValues?: ReadonlyMap<string, LiveValueRecord>;
  /** Phase 13 sub-step 13.9 — runtime ``hook_failed`` confirmations
   *  keyed by overload key. Drives the warn-orange "inlined
   *  (runtime-confirmed)" decoration on the affected node. Empty
   *  in ``"static"`` mode. */
  hookFailed?: ReadonlyMap<string, HookFailureRecord>;
  /** v3.X-next.2 / DEC-031 N7 — source-line pill clickable. Called
   *  with the node's MethodRef-derived source line + class /
   *  method names so the consumer can open the Code Browser via
   *  the existing ``pendingCodeNav`` plumbing. Receives the same
   *  ``(className, methodName, sourceLine)`` triple that the
   *  Inspector's "Open source" button uses; ``LabTraceMode`` wires
   *  it through. Default no-op when omitted. */
  onSourceLineClick?: (target: {
    className: string;
    methodName: string;
    sourceLine: number;
  }) => void;
};

// ---------------------------------------------------------------------------
// Layout — Dagre top-down.

type LayoutResult = {
  positions: Map<string, { x: number; y: number }>;
  width: number;
  height: number;
};

function layoutWithDagre(
  nodes: ExecutionFlowV3Node[],
  edges: ExecutionFlowV3Edge[],
): LayoutResult {
  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: "TB",
    nodesep: NODE_GAP,
    ranksep: RANK_GAP,
    marginx: 24,
    marginy: 24,
  });
  // ``setDefaultEdgeLabel`` is required by Dagre's API even when we
  // attach explicit edge labels; the default is the fallback for
  // any edge added without one.
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of nodes) {
    const isPill = n.kind === "return_pill";
    g.setNode(n.id, {
      width: isPill ? PILL_WIDTH : NODE_WIDTH,
      height: isPill ? PILL_HEIGHT : NODE_HEIGHT,
    });
  }
  for (const e of edges) {
    g.setEdge(e.source, e.target);
  }
  dagre.layout(g);

  const positions = new Map<string, { x: number; y: number }>();
  for (const n of nodes) {
    const dn = g.node(n.id);
    if (!dn) continue;
    const isPill = n.kind === "return_pill";
    const w = isPill ? PILL_WIDTH : NODE_WIDTH;
    const h = isPill ? PILL_HEIGHT : NODE_HEIGHT;
    // Dagre returns center coordinates; React Flow wants top-left.
    positions.set(n.id, { x: dn.x - w / 2, y: dn.y - h / 2 });
  }
  const graphLabel = g.graph();
  return {
    positions,
    width: graphLabel.width ?? 0,
    height: graphLabel.height ?? 0,
  };
}

// ---------------------------------------------------------------------------
// Custom node component
//
// v3.X-next.2 — extended with dynamic-overlay decoration (fired
// emphasis + depth pill + runtime-confirmed inlined pill) + N6 chip
// relabels (``9 ?`` → ``9 neutral?``, ``9 unv`` → ``9 unverdicted?``)
// + N7 source-line pill clickable + N8 title shape swap (method
// name primary, fully-qualified secondary).

type NodeData = ExecutionFlowV3Node & {
  mode?: TraceMode;
  fired?: boolean;
  live?: LiveValueRecord | null;
  runtimeInlined?: HookFailureRecord | null;
  onSourceLineClick?: (target: {
    className: string;
    methodName: string;
    sourceLine: number;
  }) => void;
};

function MethodNodeV3({ data, selected }: NodeProps<Node<NodeData>>) {
  const n = data;
  const isPill = n.kind === "return_pill";
  const isFiredEmphasis =
    !!n.fired && (n.mode === "dynamic" || n.mode === "both");
  const isRuntimeInlined = !!n.runtimeInlined;

  const cardClass = [
    "execution-flow-node",
    `execution-flow-node-${n.kind === "return_pill" ? "retpill" : n.kind}`,
    n.kind === "entry" && "execution-flow-node-v3-entry",
    n.kind === "gate" && "execution-flow-node-v3-gate",
    selected && "execution-flow-node-selected",
    isPill && "execution-flow-node-retpill",
    isFiredEmphasis && "execution-flow-node-fired",
    n.possiblyInlined && "execution-flow-node-inlined",
    isRuntimeInlined && "execution-flow-node-inlined-runtime",
  ]
    .filter(Boolean)
    .join(" ");

  const cornerPill = (() => {
    if (n.kind === "entry") return "ENTRY";
    if (n.kind === "gate" || n.hasGateDecision) return "GATE";
    return null;
  })();

  const cornerPillKind = (() => {
    if (cornerPill === "ENTRY") return "execution-flow-pill-entry";
    if (cornerPill === "GATE") return "execution-flow-pill-gate";
    return "";
  })();

  // N8 — title shape swap. Primary line = method name only (e.g.
  // ``validatePin``); secondary line = fully-qualified
  // ``Class.method`` (e.g. ``MainActivity.validatePin``). The
  // operator's eye lands on the method name first; the secondary
  // line carries the class context for disambiguation across the
  // graph. Replaces the v3.1 ``MainActivity.validatePin`` primary
  // + ``MainActivity`` chip pattern (the class chip's value was
  // already encoded in the primary so the chip was redundant).
  const primary = isPill ? n.title : (n.methodName || n.title);
  const secondary = isPill
    ? n.retSourceTitle ?? null
    : n.className
      ? `${n.className.split(".").pop() || n.className}.${n.methodName}`
      : null;

  const titleAttr = isPill
    ? n.retSourceTitle
      ? `${n.title} — from ${n.retSourceTitle}`
      : n.title
    : `${n.className}.${n.methodName}${
        n.sourceLine != null ? ` (line ${n.sourceLine})` : ""
      }`;

  return (
    <div
      className={cardClass}
      style={{
        width: isPill ? PILL_WIDTH : NODE_WIDTH,
        height: isPill ? PILL_HEIGHT : NODE_HEIGHT,
      }}
      title={titleAttr}
    >
      {/* Top-down layout: target handle on top, source on bottom. */}
      <Handle
        type="target"
        position={Position.Top}
        className="execution-flow-handle"
      />
      <Handle
        type="source"
        position={Position.Bottom}
        className="execution-flow-handle"
      />

      {cornerPill && (
        <span className={`execution-flow-pill ${cornerPillKind}`}>
          {cornerPill}
        </span>
      )}

      {/* Depth pill on fired nodes (when mode allows the emphasis).
          Bottom-right corner so it doesn't collide with the top-right
          GATE / ENTRY pill. Format: ``d:N · t:M``; ``× count`` suffix
          when the method fired more than once. */}
      {isFiredEmphasis && n.live && (
        <span
          className="execution-flow-node-depth-pill"
          title={`Latest fire: depth ${n.live.threadDepth} on thread ${n.live.threadId}${
            n.live.fireCount > 1
              ? ` · fired ${n.live.fireCount} times this session`
              : ""
          }`}
        >
          d:{n.live.threadDepth} · t:{n.live.threadId}
          {n.live.fireCount > 1 && (
            <span className="execution-flow-node-depth-pill-count">
              {" "}
              ×{n.live.fireCount}
            </span>
          )}
        </span>
      )}

      {isPill ? (
        <div className="execution-flow-node-retpill-body">{n.title}</div>
      ) : (
        <>
          <div className="execution-flow-node-title">{primary}</div>
          {secondary && (
            <div className="execution-flow-node-class">{secondary}</div>
          )}
          <div className="execution-flow-node-meta">
            {n.sourceLine != null && (
              // N7 — source-line pill is now clickable. Falls back
              // to the static ``span`` rendering when no callback is
              // wired (e.g. the renderer is hosted outside
              // ``LabTraceMode``); when a callback is present the
              // pill becomes a ``button`` so keyboard navigation +
              // screen readers treat it as an interactive control.
              n.onSourceLineClick ? (
                <button
                  type="button"
                  className="execution-flow-node-source-line execution-flow-node-source-line-clickable"
                  title={`Open ${n.className}.${n.methodName} at line ${n.sourceLine} in the Code Browser`}
                  onClick={(evt) => {
                    evt.stopPropagation();
                    n.onSourceLineClick!({
                      className: n.className,
                      methodName: n.methodName,
                      sourceLine: n.sourceLine!,
                    });
                  }}
                >
                  line {n.sourceLine}
                </button>
              ) : (
                <span className="execution-flow-node-source-line">
                  line {n.sourceLine}
                </span>
              )
            )}
            {n.overloadCount > 1 && (
              <span className="execution-flow-node-overload-pill">
                ×{n.overloadCount} overloads
              </span>
            )}
            {n.verdictSummary && (() => {
              // v3.1 — inline verdict-summary chip on the gate card.
              // Per-verdict-kind breakdown so the operator sees the
              // full distribution at a glance. Sub-spans suppressed
              // when their count is ``0`` (de-clutters skewed
              // distributions).
              //
              // v3.X-next.2 N6 — chip labels grew from terse
              // ``9 ?`` / ``9 unv`` to plain-language
              // ``9 neutral?`` / ``9 unverdicted?`` with per-sub-
              // chip hover tooltips sourced from ``copy.ts``. The
              // aggregate hover (on the outer chip) still spells
              // out the per-bucket totals + the visibility caveat.
              const s = n.verdictSummary;
              const total = s.allow + s.deny + s.neutral + s.unverdicted;
              if (total === 0) return null;
              const titleLines = [
                `${total} verdict${total > 1 ? "s" : ""} across this method's decisions:`,
                s.allow > 0 ? `  • ${s.allow} allow (Ret: True)` : null,
                s.deny > 0 ? `  • ${s.deny} deny (Ret: False)` : null,
                s.neutral > 0
                  ? `  • ${s.neutral} neutral (classifier unsure)`
                  : null,
                s.unverdicted > 0
                  ? `  • ${s.unverdicted} unverdicted (slicer max-walk)`
                  : null,
                "",
                "Visible outgoing arrows show only the verdicts whose",
                "BypassPlan.target_method resolved into a callee method.",
              ].filter(Boolean);
              return (
                <span
                  className="execution-flow-node-v3-summary"
                  title={titleLines.join("\n")}
                >
                  {s.allow > 0 && (
                    <span
                      className="execution-flow-node-v3-summary-allow"
                      title={VERDICT_CHIP_TOOLTIPS.allow}
                    >
                      {s.allow} {VERDICT_CHIP_LABELS.allow}
                    </span>
                  )}
                  {s.deny > 0 && (
                    <span
                      className="execution-flow-node-v3-summary-deny"
                      title={VERDICT_CHIP_TOOLTIPS.deny}
                    >
                      {s.deny} {VERDICT_CHIP_LABELS.deny}
                    </span>
                  )}
                  {s.neutral > 0 && (
                    <span
                      className="execution-flow-node-v3-summary-neutral"
                      title={VERDICT_CHIP_TOOLTIPS.neutral}
                    >
                      {s.neutral} {VERDICT_CHIP_LABELS.neutral}
                    </span>
                  )}
                  {s.unverdicted > 0 && (
                    <span
                      className="execution-flow-node-v3-summary-unv"
                      title={VERDICT_CHIP_TOOLTIPS.unverdicted}
                    >
                      {s.unverdicted} {VERDICT_CHIP_LABELS.unverdicted}
                    </span>
                  )}
                </span>
              );
            })()}
            {/* Runtime-confirmed inlined pill takes precedence over
                the static heuristic pill. ``runtimeInlined`` is
                truthy iff a Frida ``hook_failed`` event landed for
                this method's overload key during the active dynamic
                trace. */}
            {isRuntimeInlined && n.runtimeInlined ? (
              <span
                className="execution-flow-node-inlined-pill execution-flow-node-inlined-pill-runtime"
                title={`Frida hook_failed: ${n.runtimeInlined.reason}`}
              >
                inlined (runtime)
              </span>
            ) : (
              n.possiblyInlined && (
                <span className="execution-flow-node-inlined-pill">
                  possibly inlined
                </span>
              )
            )}
          </div>
          {/* v3.X-next.4 — N5 hover-expand-card content set.
              Surfaces a preview of the same signals the Inspector
              owns in full (top decision-predicate count, bypass-
              plan count, live runtime status) so the operator can
              quickly scan a flowchart of cards without opening the
              right pane for every node. Each row reads as
              "<label>: <value>" with a per-row title-attribute
              that mirrors the longer-form copy the Inspector
              shows. CSS-only visibility — the block stays in DOM
              for keyboard tab-through (``:focus-within``) and
              hover-flicker stability, gated by ``opacity`` +
              ``visibility`` flips on the parent ``.execution-
              flow-node:hover``. N5's full-content items (per-
              method LLM summary preview, predicate-origin
              breakdown, per-plan target/predicate/kind
              rendering) intentionally stay in the Inspector —
              this surface is the quick-look preview, not a
              duplicate. */}
          {renderHoverDetailV3(n, isFiredEmphasis, isRuntimeInlined)}
        </>
      )}
    </div>
  );
}

/** v3.X-next.4 — derive the hover-expand-card's quick-look content
 *  from signals already on the node's ``data`` prop. No new BE /
 *  emitter plumbing beyond the ``bypassPlanCount`` field added in
 *  the same commit (operator-visible only inside the expanded
 *  surface; the base card stays at the v3.X-next.2 baseline
 *  density). Returns ``null`` when no quick-look row would render
 *  (e.g. a plain ``method`` node with no gate, no plans, no fire,
 *  no inline status) so the dashed-border separator stays hidden
 *  in that degenerate case. */
function renderHoverDetailV3(
  n: NodeData,
  isFiredEmphasis: boolean,
  isRuntimeInlined: boolean,
): JSX.Element | null {
  const totalDecisions = n.verdictSummary
    ? n.verdictSummary.allow +
      n.verdictSummary.deny +
      n.verdictSummary.neutral +
      n.verdictSummary.unverdicted
    : 0;
  const hasDecisions = n.hasGateDecision && totalDecisions > 0;
  const hasPlans = n.bypassPlanCount > 0;
  const hasFiredLive = isFiredEmphasis && !!n.live;
  const hasRuntimeInlined = isRuntimeInlined && !!n.runtimeInlined;
  if (!hasDecisions && !hasPlans && !hasFiredLive && !hasRuntimeInlined) {
    return null;
  }
  return (
    <div className="execution-flow-node-hover-detail">
      {hasDecisions && (
        <div
          className="execution-flow-node-hover-detail-row"
          title={`${totalDecisions} decision predicate${
            totalDecisions > 1 ? "s" : ""
          } on this method. Open the Inspector for the per-predicate origin trace.`}
        >
          <span className="execution-flow-node-hover-detail-label">
            decisions
          </span>
          <span className="execution-flow-node-hover-detail-value">
            {totalDecisions}
          </span>
        </div>
      )}
      {hasPlans && (
        <div
          className="execution-flow-node-hover-detail-row"
          title={`${n.bypassPlanCount} BypassPlan${
            n.bypassPlanCount > 1 ? "s" : ""
          } authored on this gate. Open the Inspector for target method, predicate, kind + confidence.`}
        >
          <span className="execution-flow-node-hover-detail-label">
            bypass plans
          </span>
          <span className="execution-flow-node-hover-detail-value">
            {n.bypassPlanCount}
          </span>
        </div>
      )}
      {hasFiredLive && n.live && (
        <div
          className="execution-flow-node-hover-detail-row"
          title={`Latest fire on thread ${n.live.threadId} at depth ${
            n.live.threadDepth
          }${
            n.live.fireCount > 1
              ? ` · fired ${n.live.fireCount} times this session`
              : ""
          }`}
        >
          <span className="execution-flow-node-hover-detail-label">
            runtime
          </span>
          <span className="execution-flow-node-hover-detail-value execution-flow-node-hover-detail-value-runtime">
            depth {n.live.threadDepth} · thread {n.live.threadId}
            {n.live.fireCount > 1 ? ` · ×${n.live.fireCount}` : ""}
          </span>
        </div>
      )}
      {hasRuntimeInlined && n.runtimeInlined && (
        <div
          className="execution-flow-node-hover-detail-row"
          title={`Frida hook_failed: ${n.runtimeInlined.reason}`}
        >
          <span className="execution-flow-node-hover-detail-label">
            inlined
          </span>
          <span className="execution-flow-node-hover-detail-value execution-flow-node-hover-detail-value-inlined">
            {n.runtimeInlined.reason}
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Custom edge component
//
// v3.X-next.2 — extended with dynamic-overlay decoration (fired
// emphasis + untaken-dynamic fade + live-value chip) ported from
// the v2 ``ExecutionFlow.tsx`` VerdictEdge. ``invoke`` (the new
// v3.X-next.2 CFG-position-aware edge kind) renders with a neutral
// stroke + no label (the vertical-stack layout carries the
// execution-order signal; an explicit label would just be noise).

type EdgeData = ExecutionFlowV3Edge & {
  mode?: TraceMode;
  fired?: boolean;
  liveLabel?: string | null;
};

function VerdictEdgeV3({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
  markerEnd,
}: EdgeProps<Edge<EdgeData>>) {
  const e = data!;
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition: Position.Bottom,
    targetPosition: Position.Top,
    borderRadius: 6,
  });

  const isDynamic = e.mode === "dynamic";
  const isBoth = e.mode === "both";
  const isFired = !!e.fired && (isDynamic || isBoth);
  const isFadedUntaken = isDynamic && !isFired;

  const className = [
    "execution-flow-edge",
    `execution-flow-edge-${e.kind}`,
    isFired && "execution-flow-edge-fired",
    isFadedUntaken && "execution-flow-edge-untaken-dynamic",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        className={className}
        style={{ strokeWidth: EDGE_STROKE_WIDTH }}
      />
      {e.label && (
        <EdgeLabelRenderer>
          <div
            className={[
              "execution-flow-edge-label",
              `execution-flow-edge-label-${e.kind}`,
              isFired && "execution-flow-edge-label-fired",
              isFadedUntaken && "execution-flow-edge-label-untaken-dynamic",
            ]
              .filter(Boolean)
              .join(" ")}
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            }}
          >
            {e.label}
          </div>
        </EdgeLabelRenderer>
      )}
      {/* Live-value chip on fired edges — same pattern as v2's
          ``execution-flow-edge-live-chip``. Positioned slightly
          below the verdict label so the two don't collide. */}
      {isFired && e.liveLabel && (
        <EdgeLabelRenderer>
          <div
            className="execution-flow-edge-live-chip"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY + 18}px)`,
            }}
            title={e.liveLabel}
          >
            {e.liveLabel}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// React Flow type registries.

const NODE_TYPES: NodeTypes = { method: MethodNodeV3 };
const EDGE_TYPES: EdgeTypes = { verdict: VerdictEdgeV3 };

// ---------------------------------------------------------------------------
// Public component

export function ExecutionFlowV3({
  anchor,
  selectedNodeId = null,
  onNodeClick,
  hideRetPills,
  gatesOnly,
  mode = "static",
  firedMethods,
  liveValues,
  hookFailed,
  onSourceLineClick,
}: Props) {
  // Dev-overlay state. Default ``false`` post-v3-promotion so the
  // production rendering is uncluttered; operators can still toggle
  // via the top-left panel button when inspecting graph internals.
  const [showStats, setShowStats] = useState(false);

  const { rfNodes, rfEdges, stats } = useMemo(() => {
    const buildOpts: ExecutionFlowV3Options = {
      hideRetPills,
      gatesOnly,
    };
    const graph = buildExecutionFlowV3Graph(anchor, buildOpts);
    const layout = layoutWithDagre(graph.nodes, graph.edges);
    const stats = graphV3Stats(graph);

    // Index the dynamic-overlay payloads by overload key (descriptor-
    // stripped) — same indexing pattern as v2's ``ExecutionFlow``.
    // Return pill nodes never fire (no MethodRef backing); guard
    // explicitly so a stray live-value hit on an empty overload key
    // doesn't accent.
    const fired = firedMethods ?? null;
    const live = liveValues ?? null;
    const failed = hookFailed ?? null;

    const rfNodes: Node<NodeData>[] = graph.nodes.map((n) => {
      const isPill = n.kind === "return_pill";
      const oKey = isPill ? "" : overloadKeyFromNodeId(n.id);
      const isFired = !isPill && !!fired?.has(oKey);
      const liveRecord = !isPill && live ? (live.get(oKey) ?? null) : null;
      const runtimeInlined =
        !isPill && failed ? (failed.get(oKey) ?? null) : null;
      return {
        id: n.id,
        type: "method",
        data: {
          ...n,
          mode,
          fired: isFired,
          live: liveRecord,
          runtimeInlined,
          onSourceLineClick,
        },
        position: layout.positions.get(n.id) ?? { x: 0, y: 0 },
        width: isPill ? PILL_WIDTH : NODE_WIDTH,
        height: isPill ? PILL_HEIGHT : NODE_HEIGHT,
        selectable: !n.isSynthetic,
        draggable: false,
        connectable: false,
      };
    });

    // Edge fired-ness: an edge is "fired" iff BOTH endpoints fired.
    // The fired source means the gate / caller executed; the fired
    // target means the path actually flowed through. Synthetic
    // return-pill targets ride on the source's fired flag (a fired
    // gate routing into a pill reads as "this branch was taken").
    const rfEdges: Edge<EdgeData>[] = graph.edges.map((e) => {
      const sourceOKey = overloadKeyFromNodeId(e.source);
      const targetOKey = overloadKeyFromNodeId(e.target);
      const sourceFired = !!fired?.has(sourceOKey);
      const targetIsPill = e.target.startsWith("__retpill__::");
      const targetFired = targetIsPill
        ? sourceFired
        : !!fired?.has(targetOKey);
      const edgeFired = sourceFired && targetFired;
      const sourceLive = live?.get(sourceOKey) ?? null;
      const liveLabel = composeLiveLabel(sourceLive);
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: "verdict",
        data: {
          ...e,
          mode,
          fired: edgeFired,
          liveLabel,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: ARROWHEAD_SIZE,
          height: ARROWHEAD_SIZE,
        },
      };
    });

    return { rfNodes, rfEdges, stats };
  }, [
    anchor,
    hideRetPills,
    gatesOnly,
    mode,
    firedMethods,
    liveValues,
    hookFailed,
    onSourceLineClick,
  ]);

  const styledNodes = useMemo(
    () =>
      rfNodes.map((n) => ({ ...n, selected: n.id === selectedNodeId })),
    [rfNodes, selectedNodeId],
  );

  const handleNodeClick = useCallback(
    (_evt: React.MouseEvent, node: Node<NodeData>) => {
      if (!onNodeClick) return;
      const data = node.data;
      if (data.isSynthetic) return;
      onNodeClick(data);
    },
    [onNodeClick],
  );

  // v3.1 — within-page fullscreen toggle (ported verbatim from v2.0
  // ``ExecutionFlow`` per DEC-030 Q7 = (a)). The
  // ``.execution-flow-container-fullscreen`` rule from v2.0.1's
  // hotfix already pins ``width: 100vw; height: 100vh;`` + ``position:
  // fixed; inset: 0;`` so flipping the class on the v3 container
  // inherits the same viewport-fill behaviour. ``ESC`` exits via a
  // window-scoped keydown handler.
  const [isFullscreen, setIsFullscreen] = useState(false);
  useEffect(() => {
    if (!isFullscreen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsFullscreen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isFullscreen]);

  const containerClass = [
    "execution-flow-container",
    "execution-flow-v3-container",
    isFullscreen && "execution-flow-container-fullscreen",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={containerClass}>
      <ReactFlow
        nodes={styledNodes}
        edges={rfEdges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1.0 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        onNodeClick={handleNodeClick}
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ type: "verdict" }}
      >
        <Background gap={16} size={1} className="execution-flow-bg" />
        <Controls
          showInteractive={false}
          className="execution-flow-controls"
        />
        <MiniMap
          className="execution-flow-minimap"
          nodeStrokeWidth={1}
          pannable
          zoomable
        />
        {/* v3.X-next.2 — dropped the "v3 preview" badge (v3 is now
            the production renderer). Stats toggle survives for
            graph-internals inspection during dogfood; collapsed by
            default per the post-promotion uncluttered default. */}
        <Panel position="top-left" className="execution-flow-v3-preview-panel">
          <button
            type="button"
            className="execution-flow-v3-preview-toggle"
            onClick={() => setShowStats((v) => !v)}
            title={showStats ? "Hide debug stats" : "Show debug stats"}
            aria-pressed={showStats}
          >
            {showStats ? "stats: on" : "stats: off"}
          </button>
        </Panel>
        {/* v3.1 — within-page fullscreen toggle (top-right). ``ESC``
            also exits via the window-scoped keydown handler. */}
        <Panel
          position="top-right"
          className="execution-flow-fullscreen-panel"
        >
          <button
            type="button"
            className={[
              "execution-flow-fullscreen-toggle",
              isFullscreen && "execution-flow-fullscreen-toggle-active",
            ]
              .filter(Boolean)
              .join(" ")}
            onClick={() => setIsFullscreen((v) => !v)}
            title={isFullscreen ? "Exit fullscreen (ESC)" : "Fullscreen"}
            aria-label={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
            aria-pressed={isFullscreen}
          >
            {isFullscreen ? "Exit fullscreen" : "Fullscreen"}
          </button>
        </Panel>
        {showStats && (
          <Panel position="bottom-right" className="execution-flow-v3-stats">
            <div>
              {stats.entry}× entry · {stats.gate}× gate · {stats.method}× method · {stats.returnPills}× ret-pill
            </div>
            <div>
              {stats.edges} edges ({stats.invokeEdges} invoke, {stats.callEdges} call-fallback, {stats.verdictEdges} verdict){stats.dangling > 0 && ` · ${stats.dangling} DANGLING`}
            </div>
            <div>
              verdicts: {stats.summary.allow}a · {stats.summary.deny}d · {stats.summary.neutral}? · {stats.summary.unverdicted}unv
            </div>
            <div>
              opts: pills={hideRetPills === false ? "shown" : "hidden"} · gates-only={gatesOnly === false ? "no" : "yes"}
            </div>
          </Panel>
        )}
      </ReactFlow>
    </div>
  );
}
