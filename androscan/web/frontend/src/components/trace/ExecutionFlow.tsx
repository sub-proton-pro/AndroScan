/**
 * Behavior Trace v3 — flowchart visual surface (Phase 13 sub-step
 * 13.6 / DEC-029).
 *
 * Renders the active ``BehaviorAnchor`` as a left-to-right
 * directed-acyclic flowchart using React Flow (``@xyflow/react``).
 * Replaces the linear top-down ``BehaviorTrace`` list as the
 * primary visual surface; the legacy list still renders below for
 * the 13.6 → 13.8 build-out window so operators can dogfood the
 * flowchart against the familiar list view.
 *
 * Locked design (DEC-029, mirrored from the canonical
 * ``phase-13-trace-mockup.canvas.tsx`` mockup):
 *
 *   * **Nodes** — 220×72px, 6px radius, three-line stacked-card
 *     visual. Title + class line + source-line / overload pill.
 *     Corner pill: ``GATE`` for candidate-gate methods,
 *     ``ALLOW`` / ``DENY`` for synthetic verdict sinks. Dashed
 *     border + ``Possibly inlined`` corner label for R8-suspected
 *     methods.
 *   * **Edges** — 1.5px stroke + 6×6 arrowhead. Color-only
 *     emphasis: allow = semantic-OK green, deny = semantic-error
 *     red, neutral = muted gray, unverdicted = dim accent gray.
 *     Same stroke and arrowhead size for ALL edges (three
 *     plan-mode iterations rejected thicker fired edges and a
 *     larger arrowhead).
 *   * **Layout** — coarse left-to-right column-grid based on each
 *     node's BFS distance from the entry (``layoutRank`` from
 *     :mod:`executionFlowGraph`). v1 ships without an external
 *     layout lib (dagre / ELK) — the column grid is enough for
 *     the locked ``MAX_TRACE_METHODS = 30`` ceiling, and the
 *     React Flow consumer's pan / zoom + minimap absorb any
 *     local-overlap artefacts. v2 may pull dagre in if real-app
 *     graphs exceed the ceiling.
 *
 * Click handler (``onNodeClick`` prop) lifts node selection up to
 * ``LabTraceMode`` so 13.7's ``Inspector`` pane can read it; v1
 * leaves the click handler optional so this sub-step ships with
 * a no-op default and 13.7 wires up the real consumer. Synthetic
 * sink nodes don't fire the click handler (they don't carry a
 * MethodRef).
 *
 * **Phase 13 sub-step 13.8 update:** the component now accepts
 * ``mode``, ``firedMethods``, and ``liveValues`` props that drive
 * the dynamic-overlay rendering. ``mode === "static"`` keeps the
 * 13.6 default (every edge in its verdict color). ``"dynamic"``
 * fades the verdict palette to gray-dashed at 55% opacity and
 * accents the fired edges with the locked accent-blue solid
 * stroke (DEC-029). ``"both"`` keeps the verdict colors but still
 * accents the fired edges in accent blue on top.
 *
 * Fired nodes (``firedMethods.has(overloadKey(node))``) get an
 * accent-blue border emphasis + a depth pill ("d:N · t:M") in the
 * top-right corner showing the most recent thread + depth from
 * ``liveValues``. Fired edges (whose source AND target are both
 * fired) carry a small live-value chip rendered as part of the
 * EdgeLabelRenderer — args / return values from ``liveValues`` so
 * the operator can see "what flowed through this edge" at a glance
 * (e.g. ``pin="1234" → false``).
 *
 * Out of scope for v1:
 *   * Per-thread depth visualization beyond the corner pill — full
 *     layout reshape into thread lanes deferred to 13.9.
 *   * Marching-ants animation on fired edges — DEC-029 explicitly
 *     locked the static stroke; the animation would need its own
 *     planning checkpoint.
 *   * Pan-to-fit on selection — v2 candidate.
 *
 * **Phase 13 v2.0 update (DEC-030).** Four operator-visible
 * flowchart fixes land in this revision, all driven by post-v1
 * operator feedback:
 *
 *   * **Q1 = (g) — hover-progressive gate-count badge + small
 *     de-emphasized neutral sinks.** Each method card carries a
 *     small ``N gates · M unclassified`` badge in the meta row,
 *     rendered always but CSS-hidden until the card is hovered or
 *     receives keyboard focus (``:focus-within``). The badge
 *     surfaces classifier-uncertainty at the source so operators
 *     can spot misclassifications without traversing the right-
 *     edge sinks. Per-source ``sink_neutral`` nodes render at
 *     110×36 with the de-emphasized variant (dashed border, 60%
 *     opacity, lowercase ``neutral`` label, no corner pill); their
 *     ``title`` attribute (browser-default tooltip) lists the
 *     feeding gates so hovering reveals which decisions converge
 *     into the sink.
 *   * **Q2 = (a) — global ALLOW / DENY sink coalescing.** All
 *     allow / deny verdict edges terminate at ONE shared node per
 *     kind (``GLOBAL_ALLOW_SINK_ID`` / ``GLOBAL_DENY_SINK_ID`` in
 *     :mod:`executionFlowGraph`). The visual reads as "all paths
 *     to allow / deny" without the per-source-method clutter of
 *     the v1 design. The classifier is confident on allow / deny
 *     so per-source anchors aren't worth their visual weight.
 *   * **Q3 = (a) — arrowhead fill via ``fill: context-stroke``.**
 *     The v1 ``fill: currentColor`` on SVG ``marker path`` failed
 *     because ``currentColor`` in a ``<marker>`` definition
 *     resolves against the marker's own color context, not the
 *     referencing path. The SVG2 ``context-stroke`` keyword
 *     correctly inherits the stroke color of the path that uses
 *     the marker. Lives entirely in ``App.css`` (Chrome 99+,
 *     Firefox 90+, Safari 16+). Fixed by 13.6's ``MARKERS``
 *     registry which was reserved exactly for this case.
 *   * **Q5 = (b) — within-column sort entry-first.** The
 *     ``layoutNodes`` sort now puts the entry node first within
 *     its rank column regardless of alphabetic order, then
 *     synthetic sinks last (preserved from v1), then real methods
 *     alphabetic in between (preserved from v1). Fixes the "boxes
 *     above Entry method" complaint at rank 0 when the entry has
 *     no own decision edges (real-app dominant case).
 *   * **Q7 = (a) — within-page fullscreen toggle.** New custom
 *     button (top-right via React Flow's ``<Panel>``) toggles a
 *     fullscreen state on the container; CSS flips the container
 *     to ``position: fixed; inset: 0; z-index: 1000;`` so the
 *     flowchart fills the viewport without going OS-level
 *     fullscreen (operator-locked posture — within-page only).
 *     ``ESC`` key exits via a ``window.keydown`` handler scoped to
 *     the fullscreen state. Button label flips to "Exit
 *     fullscreen" + active class indicator when toggled on.
 *
 * **Out of scope for v2.0** (deferred to v2.0-tests / v2.1 per
 * DEC-030's sub-step backlog):
 *   * Unit tests for the ``executionFlowGraph`` pure helper —
 *     deferred to v2.0-tests sub-step which lands the frontend
 *     test infrastructure (vitest) as its own focused decision
 *     surface.
 *   * Call-graph caller→callee edge injection for true call-
 *     hierarchy indentation — deferred to v2.1, dogfood-gated.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
  type NodeTypes,
  type EdgeTypes,
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type {
  BehaviorAnchor,
  HookFailureRecord,
  LiveValueRecord,
} from "../../api/trace";
import {
  buildExecutionFlowGraph,
  overloadKeyFromNodeId,
  type ExecutionFlowEdge,
  type ExecutionFlowNode,
} from "./executionFlowGraph";
import type { TraceMode } from "./TraceModeToggle";


// ---------------------------------------------------------------------------
// Locked layout constants — mirrors DEC-029 + the canonical mockup.

const NODE_WIDTH = 220;
const NODE_HEIGHT = 72;
const COLUMN_GAP = 80;
const ROW_GAP = 30;
const COLUMN_PITCH = NODE_WIDTH + COLUMN_GAP;
const ROW_PITCH = NODE_HEIGHT + ROW_GAP;

// Phase 13 v2.0 (DEC-030 Q1 = (g)) — small de-emphasized variant
// for per-source ``sink_neutral`` synthetic sinks. ~50% of the
// standard card dimensions so the sinks visually recede to
// "footnote" status without losing the per-gate anchor the
// operator needs for misclassification spotting.
const SMALL_SINK_WIDTH = 110;
const SMALL_SINK_HEIGHT = 36;

// React Flow's default arrowhead is 12.5×12.5; DEC-029 locks 6×6
// for ALL edges. ``markerEnd.width`` and ``markerEnd.height`` set
// the arrowhead's bounding box; the arrow itself fills it.
const ARROWHEAD_SIZE = 6;
const EDGE_STROKE_WIDTH = 1.5;

// 13.8 — live-value chip budget. The chip sits inside a 220px-
// wide column slot, so a ~56-char total budget keeps it readable
// at the locked font size (12px ui font) without wrapping.
const LIVE_LABEL_BUDGET_CHARS = 56;
const LIVE_LABEL_ARG_BUDGET = 24; // per-arg cap before ellipsis

/** Pre-format the per-edge live-value chip from the source method's
 *  latest LiveValueRecord. Returns ``null`` when nothing useful can
 *  be shown (no record yet, or both args + ret empty). The formatter
 *  is conservative: each arg is truncated to 24 chars + ellipsis,
 *  joined with ``, `` (and capped at 4 args), then a ``→ ret``
 *  suffix when the exit landed. The whole label is hard-truncated
 *  to ``LIVE_LABEL_BUDGET_CHARS`` as a safety net for pathological
 *  return values. */
function composeLiveLabel(live: LiveValueRecord | null): string | null {
  if (!live) return null;
  const truncate = (s: string, n: number) =>
    s.length <= n ? s : s.slice(0, Math.max(0, n - 1)) + "…";
  const args = (live.args ?? [])
    .slice(0, 4)
    .map((a) => truncate(a, LIVE_LABEL_ARG_BUDGET));
  const argsPart = args.length > 0 ? args.join(", ") : "";
  const moreArgs = (live.args?.length ?? 0) > 4 ? `, +${(live.args?.length ?? 0) - 4} more` : "";
  const retPart = live.ret != null ? ` → ${truncate(live.ret, LIVE_LABEL_ARG_BUDGET)}` : "";
  const out = `${argsPart}${moreArgs}${retPart}`.trim();
  if (!out) return null;
  return truncate(out, LIVE_LABEL_BUDGET_CHARS);
}


// ---------------------------------------------------------------------------
// Public props


type Props = {
  anchor: BehaviorAnchor;
  /** Active selection — ``null`` means no node selected. v1
   *  defaults to the entry node so the Inspector pane (13.7)
   *  always has SOMETHING to render on first paint. */
  selectedNodeId?: string | null;
  /** Click handler — fires on every NON-synthetic node click.
   *  13.7 wires this to ``setSelectedNodeId`` in ``LabTraceMode``.
   *  v1 default = no-op. */
  onNodeClick?: (node: ExecutionFlowNode) => void;
  /** Phase 13 sub-step 13.8 — overlay mode. Drives the fired-edge
   *  / fired-node accent rendering plus the untaken-edge fade in
   *  ``"dynamic"`` mode. Default ``"static"`` so the 13.6 / 13.7
   *  call sites that don't yet pass the prop keep their original
   *  rendering. */
  mode?: TraceMode;
  /** Phase 13 sub-step 13.8 — set of overload keys (descriptor-
   *  stripped Smali) that have fired during the current dynamic
   *  trace. Empty in ``"static"`` mode (or when no trace has run
   *  yet). */
  firedMethods?: ReadonlySet<string>;
  /** Phase 13 sub-step 13.8 — per-method live values (latest fire's
   *  args / return / thread + fire count) keyed by overload key.
   *  Used to populate the depth pill on fired nodes + the live-
   *  value chip on fired edges. Empty in ``"static"`` mode. */
  liveValues?: ReadonlyMap<string, LiveValueRecord>;
  /** Phase 13 sub-step 13.9 — runtime ``hook_failed`` confirmations
   *  keyed by overload key. Drives the warn-orange "inlined
   *  (runtime-confirmed)" decoration on the affected node — the
   *  static heuristic ``possiblyInlined`` cool-gray pill upgrades
   *  to a louder runtime-confirmed state when the dynamic trace
   *  proves Frida couldn't install the hook. Empty in ``"static"``
   *  mode (or when no trace has run yet). */
  hookFailed?: ReadonlyMap<string, HookFailureRecord>;
};


// ---------------------------------------------------------------------------
// Layout — coarse column grid based on ``layoutRank``.
//
// Build a per-rank vertical stack: nodes at rank 0 (the entry) sit in
// the leftmost column, rank 1 nodes in the second column, etc. Within
// each column we stack vertically with a small row gap. Synthetic
// sinks land in the same column as their source's successors which
// is fine for the operator's mental model (the sink is "where the
// flow ends" — adjacent to the gate that emitted it).


type PositionedNodes = Map<string, { x: number; y: number }>;


function layoutNodes(nodes: ExecutionFlowNode[]): PositionedNodes {
  // Group by rank.
  const byRank: Map<number, ExecutionFlowNode[]> = new Map();
  for (const n of nodes) {
    if (!byRank.has(n.layoutRank)) byRank.set(n.layoutRank, []);
    byRank.get(n.layoutRank)!.push(n);
  }
  // Sort each rank's nodes per DEC-030 Q5 = (b): entry first → real
  // methods alphabetic → synthetic sinks last. The entry-first lock
  // fixes the v1 "boxes above Entry method" complaint at rank 0
  // when the entry has no own outgoing decision edges and a method
  // alphabetically prior to the entry's class name would otherwise
  // sort above it. Synthetic sinks still come last (cleaner visual:
  // real method nodes line up across ranks, sinks dangle off to the
  // right) — that rule is preserved verbatim from v1.
  for (const [, list] of byRank) {
    list.sort((a, b) => {
      if (a.kind === "entry" && b.kind !== "entry") return -1;
      if (b.kind === "entry" && a.kind !== "entry") return 1;
      if (a.isSynthetic !== b.isSynthetic) return a.isSynthetic ? 1 : -1;
      return a.title.localeCompare(b.title);
    });
  }
  const positioned: PositionedNodes = new Map();
  const ranks = [...byRank.keys()].sort((a, b) => a - b);
  for (const rank of ranks) {
    const list = byRank.get(rank)!;
    list.forEach((n, idx) => {
      positioned.set(n.id, {
        x: rank * COLUMN_PITCH,
        y: idx * ROW_PITCH,
      });
    });
  }
  return positioned;
}


// ---------------------------------------------------------------------------
// Custom node component
//
// React Flow expects a typed ``NodeProps<T>`` payload — we wrap the
// pure ``ExecutionFlowNode`` shape inside ``data`` so the component
// can read the operator-facing fields without type gymnastics.


/** Per-node overlay decoration baked at build time so the custom
 *  node renderer doesn't need a separate React context. ``mode`` is
 *  carried so the renderer can check whether to show the fired
 *  emphasis at all (``"static"`` suppresses); ``fired`` flips on
 *  per the consumer's ``firedMethods`` lookup; ``live`` is the
 *  latest ``LiveValueRecord`` for the depth pill. */
type NodeData = ExecutionFlowNode & {
  mode?: TraceMode;
  fired?: boolean;
  live?: LiveValueRecord | null;
  /** Phase 13 sub-step 13.9 — runtime ``hook_failed`` record for
   *  this overload key (or ``null`` when no failure landed). Drives
   *  the warn-orange runtime-confirmed inlined decoration that
   *  upgrades the ``possiblyInlined`` cool-gray heuristic state. */
  runtimeInlined?: HookFailureRecord | null;
};


function MethodNode({ data, selected }: NodeProps<Node<NodeData>>) {
  const n = data;
  const isFiredEmphasis =
    !!n.fired && (n.mode === "dynamic" || n.mode === "both");

  // Class chip (last segment of the FQCN). Synthetic sinks have an
  // empty class so we just render the title.
  const classChip = n.className
    ? n.className.split(".").pop() || n.className
    : null;

  // Phase 13 v2.0 (DEC-030 Q1 = (g)) — per-source ``sink_neutral``
  // sinks render at 110×36 with the de-emphasized variant, no
  // corner pill, lowercase ``neutral`` body label. Global ALLOW /
  // DENY sinks keep the standard size + uppercase pill (Q2 = (a)
  // global coalescing already collapses them to one per kind).
  const isSmallSink = n.kind === "sink_neutral";

  const cornerPill = (() => {
    if (n.kind === "sink_allow") return "ALLOW";
    if (n.kind === "sink_deny") return "DENY";
    if (n.kind === "sink_neutral") return null; // v2.0: no pill on small sinks
    if (n.kind === "entry" && n.hasGateDecision) return "GATE";
    if (n.kind === "gate") return "GATE";
    if (n.kind === "entry") return "ENTRY";
    return null;
  })();

  const cornerPillKind = (() => {
    if (n.kind === "sink_allow") return "execution-flow-pill-allow";
    if (n.kind === "sink_deny") return "execution-flow-pill-deny";
    if (cornerPill === "GATE") return "execution-flow-pill-gate";
    if (cornerPill === "ENTRY") return "execution-flow-pill-entry";
    return "";
  })();

  // 13.9 — runtime-confirmed inlined supersedes the static heuristic
  // class so the node renders with the warn-orange dashed-border
  // emphasis the operator can spot at a glance. Both classes can
  // coexist on the same DOM node (the ``-runtime`` variant overrides
  // the colour via specificity in App.css), but logically the
  // runtime confirmation is the louder signal.
  const isRuntimeInlined = !!n.runtimeInlined;
  const cardClass = [
    "execution-flow-node",
    `execution-flow-node-${n.kind}`,
    selected && "execution-flow-node-selected",
    n.possiblyInlined && "execution-flow-node-inlined",
    isRuntimeInlined && "execution-flow-node-inlined-runtime",
    n.overloadCount > 1 && "execution-flow-node-stacked",
    n.isSynthetic && "execution-flow-node-synthetic",
    isSmallSink && "execution-flow-node-sink-small",
    isFiredEmphasis && "execution-flow-node-fired",
  ]
    .filter(Boolean)
    .join(" ");

  // Phase 13 v2.0 (DEC-030 Q1 = (g)) — sink hover tooltip lists the
  // feeding gates so the operator can see which decisions converge
  // into a per-source ``sink_neutral`` without clicking through.
  // Browser-default ``title`` attribute keeps the implementation
  // dependency-free (no custom popover component); the
  // ``\n``-separated format renders as a multi-line tooltip in
  // every browser we care about. Global ALLOW / DENY sinks fall
  // through to the simple ``n.title`` because they're operator-
  // meaningful as "all paths to allow / deny" without per-gate
  // breakdown.
  const titleAttr = (() => {
    if (!n.isSynthetic) {
      return `${n.className}.${n.methodName}${
        n.sourceLine != null ? ` (line ${n.sourceLine})` : ""
      }`;
    }
    if (n.kind === "sink_neutral" && n.feedingGates.length > 0) {
      const lines = n.feedingGates.map(
        (g) =>
          `  • ${g.sourceTitle} @ instruction ${g.instructionIndex} (${g.verdictKind})`,
      );
      return `Fed by ${n.feedingGates.length} unclassified gate${
        n.feedingGates.length > 1 ? "s" : ""
      }:\n${lines.join("\n")}`;
    }
    return n.title;
  })();

  return (
    <div
      className={cardClass}
      style={{
        width: isSmallSink ? SMALL_SINK_WIDTH : NODE_WIDTH,
        height: isSmallSink ? SMALL_SINK_HEIGHT : NODE_HEIGHT,
      }}
      title={titleAttr}
    >
      {/* Source/target handles are required by React Flow for edges
          to attach. Style them as 0×0 invisible anchors so the
          locked card design isn't visually disrupted. */}
      <Handle
        type="target"
        position={Position.Left}
        className="execution-flow-handle"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="execution-flow-handle"
      />

      {cornerPill && (
        <span className={`execution-flow-pill ${cornerPillKind}`}>
          {cornerPill}
        </span>
      )}

      {/* Phase 13 sub-step 13.8 — depth pill on fired nodes (when
          mode allows the fired emphasis). Sits in the bottom-right
          corner so it doesn't collide with the top-right corner pill
          (GATE / ALLOW / etc.). Renders ``d:N · t:M`` where ``N`` is
          the most recent thread_depth and ``M`` is the thread_id of
          the latest fire; the ``× count`` suffix renders only when
          the method fired more than once. */}
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

      {n.isSynthetic ? (
        <div className="execution-flow-node-synthetic-body">{n.title}</div>
      ) : (
        <>
          <div className="execution-flow-node-title">{n.title}</div>
          {classChip && (
            <div className="execution-flow-node-class">{classChip}</div>
          )}
          <div className="execution-flow-node-meta">
            {n.sourceLine != null && (
              <span className="execution-flow-node-source-line">
                line {n.sourceLine}
              </span>
            )}
            {n.overloadCount > 1 && (
              <span className="execution-flow-node-overload-pill">
                ×{n.overloadCount} overloads
              </span>
            )}
            {/* Phase 13 v2.0 (DEC-030 Q1 = (g)) — gate-count badge.
                Rendered always when ``totalGates > 0`` so the DOM
                node stays stable; CSS hides it by default and
                reveals on ``.execution-flow-node:hover`` /
                ``:focus-within`` for keyboard navigation. The
                ``-warn`` modifier raises visual emphasis when at
                least one gate is unclassified — operator-spottable
                signal for potential branch_classifier false-
                negatives (see :mod:`androscan.analysis.
                branch_classifier`'s "Out of scope for v1" catalog
                of known false-negative classes). */}
            {n.totalGates > 0 && (
              <span
                className={[
                  "execution-flow-node-gate-badge",
                  n.unclassifiedGates > 0 &&
                    "execution-flow-node-gate-badge-warn",
                ]
                  .filter(Boolean)
                  .join(" ")}
                title={
                  n.unclassifiedGates > 0
                    ? `${n.totalGates} gate${n.totalGates > 1 ? "s" : ""} on this method · ${n.unclassifiedGates} unclassified by the heuristic classifier (potential false negative — review the predicate origin)`
                    : `${n.totalGates} gate${n.totalGates > 1 ? "s" : ""} on this method · all classified`
                }
              >
                {n.totalGates}g · {n.unclassifiedGates}?
              </span>
            )}
            {/* 13.9 — runtime-confirmed pill takes precedence over
                the static heuristic pill. ``runtimeInlined`` is
                truthy iff a ``hook_failed`` event landed for this
                method's overload key during the active dynamic
                trace. The ``-runtime`` modifier paints the pill in
                warn-orange; the title carries the operator-readable
                Frida reason for hover-context. */}
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
        </>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Custom edge component
//
// We use React Flow's ``getSmoothStepPath`` for the edge geometry
// (rounded right-angle bends, matches the mockup's visual). The
// custom edge component lets us:
//
//   1. Lock stroke-width to ``EDGE_STROKE_WIDTH = 1.5`` (React
//      Flow's default is 1.0, but we want the consistent 1.5 from
//      DEC-029).
//   2. Lock arrowhead size via ``markerEnd`` set on the edge data.
//   3. Render the operator-facing label (``allowed`` / ``denied`` /
//      etc.) inline along the edge mid-point with a colored chip
//      that matches the verdict palette.


/** Per-edge overlay decoration baked at build time. ``mode`` drives
 *  the untaken-edge fade ("dynamic"-only); ``fired`` flips on when
 *  both endpoints are in ``firedMethods``; ``liveLabel`` is the
 *  pre-formatted "args → ret" chip rendered alongside the verdict
 *  label on fired edges. */
type EdgeData = ExecutionFlowEdge & {
  mode?: TraceMode;
  fired?: boolean;
  liveLabel?: string | null;
};


function VerdictEdge({
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
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    borderRadius: 6,
  });
  const isDynamic = e.mode === "dynamic";
  const isBoth = e.mode === "both";
  const isFired = !!e.fired && (isDynamic || isBoth);
  // Untaken-edge fade: in "dynamic" mode every non-fired edge fades
  // to gray-dashed at 55% opacity (the static palette would compete
  // with the runtime emphasis); in "both" mode the verdict palette
  // stays visible at full opacity (operator wants to see the static
  // plan AND the runtime confirmation in one view), only the fade
  // suffix differs.
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
      {/* Phase 13 sub-step 13.8 — live-value chip on fired edges.
          Renders the latest fire's ``args → ret`` summary so the
          operator can see what flowed through the gate at a glance.
          Positioned slightly below the verdict label so the two
          don't collide; suppressed when ``liveLabel`` is empty
          (e.g. exit hadn't fired yet, or the args were empty). */}
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
// Node-types + edge-types registries (kept module-level so React
// Flow's internal stable-ref check doesn't trigger a console warning
// on every render — the registries are referentially stable).


const NODE_TYPES: NodeTypes = { method: MethodNode };
const EDGE_TYPES: EdgeTypes = { verdict: VerdictEdge };


// ---------------------------------------------------------------------------
// Marker registry — React Flow renders ``markerEnd`` references as
// SVG ``<marker>`` elements at the document level. We define one
// arrowhead per verdict-kind so the color matches the edge stroke.


const MARKERS: Record<ExecutionFlowEdge["kind"], string> = {
  allow: "execution-flow-arrow-allow",
  deny: "execution-flow-arrow-deny",
  neutral: "execution-flow-arrow-neutral",
  unverdicted: "execution-flow-arrow-unverdicted",
};


// ---------------------------------------------------------------------------
// Public component


export function ExecutionFlow({
  anchor,
  selectedNodeId = null,
  onNodeClick,
  mode = "static",
  firedMethods,
  liveValues,
  hookFailed,
}: Props) {
  const { rfNodes, rfEdges } = useMemo(() => {
    const graph = buildExecutionFlowGraph(anchor);
    const positions = layoutNodes(graph.nodes);
    // 13.8 dynamic-overlay decoration baked into ``data`` so the
    // custom MethodNode / VerdictEdge components don't need a
    // separate React context. ``firedMethods`` indexes by overload
    // key (descriptor-stripped); we strip the descriptor from the
    // node id via ``overloadKeyFromNodeId``. Synthetic sink nodes
    // never fire — guard explicitly so a stray ``allow`` sink id
    // matching a fired Smali key (impossible in practice; defensive)
    // doesn't accent.
    //
    // 13.9 — same indexing pattern for the ``hookFailed`` map; a
    // hit upgrades the static ``possiblyInlined`` heuristic to a
    // runtime-confirmed warn-orange pill. Synthetic sinks are
    // explicitly guarded out (they have no MethodRef so a hit is
    // impossible by construction; defensive nonetheless).
    const fired = firedMethods ?? null;
    const live = liveValues ?? null;
    const failed = hookFailed ?? null;

    const rfNodes: Node<NodeData>[] = graph.nodes.map((n) => {
      const oKey = n.isSynthetic ? "" : overloadKeyFromNodeId(n.id);
      const isFired = !n.isSynthetic && !!fired?.has(oKey);
      const liveRecord = !n.isSynthetic && live ? (live.get(oKey) ?? null) : null;
      const runtimeInlined =
        !n.isSynthetic && failed ? (failed.get(oKey) ?? null) : null;
      // Phase 13 v2.0 (DEC-030 Q1 = (g)) — per-source ``sink_neutral``
      // sinks render small; pass the smaller bounds to React Flow's
      // hit-testing so click + selection geometry matches the visual.
      // Global ALLOW / DENY sinks keep the standard bounds.
      const isSmallSink = n.kind === "sink_neutral";
      return {
        id: n.id,
        type: "method",
        data: {
          ...n,
          mode,
          fired: isFired,
          live: liveRecord,
          runtimeInlined,
        },
        position: positions.get(n.id) ?? { x: 0, y: 0 },
        width: isSmallSink ? SMALL_SINK_WIDTH : NODE_WIDTH,
        height: isSmallSink ? SMALL_SINK_HEIGHT : NODE_HEIGHT,
        // Synthetic sinks aren't operator-clickable.
        selectable: !n.isSynthetic,
        draggable: false,
        connectable: false,
        // ``selected`` is computed by React Flow against the
        // ``selectedNodeId`` consumer state; we pre-seed via a
        // controlled-component pattern below.
      };
    });

    // Edge fired-ness: an edge is "fired" iff BOTH endpoints fired.
    // The fired source means the gate executed; the fired target
    // means the path actually flowed through. Synthetic-sink targets
    // never fire on their own — a fired source + a synthetic sink
    // target reads as "this branch was taken" which is the operator
    // intuition we want; we treat synthetic sinks as fired-iff-source-
    // fired for the v1 emphasis (an entry that fired into a deny
    // sink should light up the path even though the sink has no
    // backing MethodRef).
    const rfEdges: Edge<EdgeData>[] = graph.edges.map((e) => {
      const sourceOKey = overloadKeyFromNodeId(e.source);
      const targetOKey = overloadKeyFromNodeId(e.target);
      const sourceFired = !!fired?.has(sourceOKey);
      const targetIsSynthetic = e.target.startsWith("__sink_");
      const targetFired = targetIsSynthetic
        ? sourceFired // synthetic sinks ride on the source's fired flag
        : !!fired?.has(targetOKey);
      const edgeFired = sourceFired && targetFired;
      // Compose the live-value chip from the source method's latest
      // args + (when ready) ret. Format: ``arg0, arg1 → ret`` with a
      // 56-char total budget so the chip stays readable on a 220px-
      // wide column. Empty when the source hasn't recorded an exit
      // yet (entry-only chip would just be ``args``).
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
          // The marker color is set via CSS (the SVG ``<marker>``
          // inherits ``currentColor`` from the parent path), so we
          // don't need to specify the color here.
        },
      };
    });

    return { rfNodes, rfEdges };
  }, [anchor, mode, firedMethods, liveValues, hookFailed]);

  // Honor the controlled selectedNodeId.
  const styledNodes = useMemo(() => {
    return rfNodes.map((n) => ({
      ...n,
      selected: n.id === selectedNodeId,
    }));
  }, [rfNodes, selectedNodeId]);

  const handleNodeClick = useCallback(
    (_evt: React.MouseEvent, node: Node<NodeData>) => {
      if (!onNodeClick) return;
      const data = node.data;
      if (data.isSynthetic) return;
      onNodeClick(data);
    },
    [onNodeClick],
  );

  // Touch the marker registry to avoid an unused-import lint —
  // the actual marker IDs are registered via ``markerEnd``'s
  // ``MarkerType.ArrowClosed`` above. ``MARKERS`` is reserved
  // for 13.8's per-verdict color overrides if React Flow's
  // ``currentColor`` inheritance proves insufficient.
  void MARKERS;

  // Phase 13 v2.0 (DEC-030 Q7 = (a)) — within-page fullscreen
  // toggle. ``isFullscreen`` flips a CSS class on the container so
  // it expands to ``position: fixed; inset: 0; z-index: 1000;`` and
  // fills the viewport without going OS-level fullscreen (operator-
  // locked posture: within-page only — full-OS fullscreen would
  // hide the workbench chrome the operator may want to switch back
  // to mid-trace). ``ESC`` exits via the ``window.keydown`` handler
  // scoped to the active state; effect cleans up its listener on
  // toggle-off + on unmount.
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
        // Subtle muted background grid.
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
        {/* Phase 13 v2.0 (DEC-030 Q7 = (a)) — within-page fullscreen
            toggle. Mounted via React Flow's <Panel> so the button
            stays inside the canvas chrome (top-right; bottom-left
            is reserved for <Controls>, bottom-right for <MiniMap>).
            Button label flips on toggle so the operator can find
            their way out; ESC also exits. */}
        <Panel position="top-right" className="execution-flow-fullscreen-panel">
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
      </ReactFlow>
    </div>
  );
}
