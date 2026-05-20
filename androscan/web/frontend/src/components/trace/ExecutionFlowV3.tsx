/**
 * Behavior Trace **v3 preview** — flowchart renderer.
 *
 * **PRE-DEC PRODUCTION PREVIEW** — mounted only behind the
 * ``?flow=v3`` URL gate in ``LabTraceMode``; the v2.0
 * ``ExecutionFlow`` remains the default production renderer. The
 * file is deliberately a parallel module rather than a v2 patch so
 * the old + new visuals can be compared side-by-side during the
 * preview window. Once the operator signs off on the visual, the v3
 * path replaces the v2 path (Q5 = (b) hard-cut per the pre-preview
 * plan) and v2.0's ``ExecutionFlow.tsx`` + ``executionFlowGraph.ts``
 * get archived.
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
 * **Out of scope for the preview**:
 *
 *   * Dynamic-overlay rendering (``firedMethods`` / ``liveValues``
 *     / ``hookFailed``). The v2 component carries these; the v3
 *     preview defers them so the static visual gets confirmed
 *     first. Once v3 lands as production, the dynamic-overlay
 *     paths will port over.
 *   * Inspector wiring beyond the click handler. Click works,
 *     selection halo works; richer Inspector consumption stays on
 *     the v2 component until v3 is promoted.
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

import type { BehaviorAnchor } from "../../api/trace";
import {
  buildExecutionFlowV3Graph,
  graphV3Stats,
  type ExecutionFlowV3Edge,
  type ExecutionFlowV3Node,
  type ExecutionFlowV3Options,
} from "./executionFlowGraphV3";

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
  /** v3 preview toggles — wired into ``?flow=v3&...`` URL params by
   *  the consumer. Both default ``undefined`` so the underlying
   *  graph builder falls back to its v3.1 defaults
   *  (``hideRetPills=true``, ``gatesOnly=true``). */
  hideRetPills?: boolean;
  gatesOnly?: boolean;
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

type NodeData = ExecutionFlowV3Node;

function MethodNodeV3({ data, selected }: NodeProps<Node<NodeData>>) {
  const n = data;
  const isPill = n.kind === "return_pill";

  const cardClass = [
    "execution-flow-node",
    `execution-flow-node-${n.kind === "return_pill" ? "retpill" : n.kind}`,
    n.kind === "entry" && "execution-flow-node-v3-entry",
    n.kind === "gate" && "execution-flow-node-v3-gate",
    selected && "execution-flow-node-selected",
    isPill && "execution-flow-node-retpill",
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

  const classChip = n.className
    ? n.className.split(".").pop() || n.className
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

      {isPill ? (
        <div className="execution-flow-node-retpill-body">{n.title}</div>
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
            {n.verdictSummary && (() => {
              // v3.1 — inline verdict-summary chip on the gate card.
              // Replaces the v2 ``Ng · M?`` gate-count badge with a
              // per-verdict-kind breakdown so the operator sees the
              // full distribution at a glance. Sub-spans are
              // suppressed when their count is ``0`` (de-clutters
              // the chip on gates with skewed distributions —
              // ``2 allow`` reads cleaner than ``2 allow · 0 deny ·
              // 0 ? · 0 unv`` on a low-decision gate).
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
                    <span className="execution-flow-node-v3-summary-allow">
                      {s.allow} allow
                    </span>
                  )}
                  {s.deny > 0 && (
                    <span className="execution-flow-node-v3-summary-deny">
                      {s.deny} deny
                    </span>
                  )}
                  {s.neutral > 0 && (
                    <span className="execution-flow-node-v3-summary-neutral">
                      {s.neutral} ?
                    </span>
                  )}
                  {s.unverdicted > 0 && (
                    <span className="execution-flow-node-v3-summary-unv">
                      {s.unverdicted} unv
                    </span>
                  )}
                </span>
              );
            })()}
            {n.possiblyInlined && (
              <span className="execution-flow-node-inlined-pill">
                possibly inlined
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Custom edge component

type EdgeData = ExecutionFlowV3Edge;

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

  const className = [
    "execution-flow-edge",
    `execution-flow-edge-${e.kind}`,
  ].join(" ");

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
            ].join(" ")}
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            }}
          >
            {e.label}
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
}: Props) {
  // v3 preview's dev-overlay state — operator can toggle the stats
  // bar via the panel button or via the URL ``?flow=v3&stats=0``;
  // default ``true`` because the preview is for inspecting the
  // visual + the underlying graph metrics together.
  const [showStats, setShowStats] = useState(true);

  const { rfNodes, rfEdges, stats } = useMemo(() => {
    const buildOpts: ExecutionFlowV3Options = {
      // ``undefined`` here lets the emitter fall back to its v3.1
      // defaults (``hideRetPills=true``, ``gatesOnly=true``); the
      // URL gate in ``LabTraceMode`` flips these to ``false`` when
      // the operator passes ``?flow=v3&pills=show`` or
      // ``?flow=v3&methods=all`` respectively.
      hideRetPills,
      gatesOnly,
    };
    const graph = buildExecutionFlowV3Graph(anchor, buildOpts);
    const layout = layoutWithDagre(graph.nodes, graph.edges);
    const stats = graphV3Stats(graph);

    const rfNodes: Node<NodeData>[] = graph.nodes.map((n) => {
      const isPill = n.kind === "return_pill";
      return {
        id: n.id,
        type: "method",
        data: n,
        position: layout.positions.get(n.id) ?? { x: 0, y: 0 },
        width: isPill ? PILL_WIDTH : NODE_WIDTH,
        height: isPill ? PILL_HEIGHT : NODE_HEIGHT,
        selectable: !n.isSynthetic,
        draggable: false,
        connectable: false,
      };
    });

    const rfEdges: Edge<EdgeData>[] = graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: "verdict",
      data: e,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: ARROWHEAD_SIZE,
        height: ARROWHEAD_SIZE,
      },
    }));

    return { rfNodes, rfEdges, stats };
  }, [anchor, hideRetPills, gatesOnly]);

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
        <Panel position="top-left" className="execution-flow-v3-preview-panel">
          <span className="execution-flow-v3-preview-badge">v3 preview</span>
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
              {stats.edges} edges ({stats.callEdges} call, {stats.verdictEdges} verdict){stats.dangling > 0 && ` · ${stats.dangling} DANGLING`}
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
