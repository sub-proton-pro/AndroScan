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
 * Out of scope for v1:
 *   * Live-value chips on edges (e.g. ``pin="1234" → false``) —
 *     wired up in 13.8 from the dynamic-trace WebSocket.
 *   * Mode toggle between ``Static`` / ``Dynamic`` / ``Both`` —
 *     13.8.
 *   * Pan-to-fit on selection — v2 candidate.
 */

import { useCallback, useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
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

import type { BehaviorAnchor } from "../../api/trace";
import {
  buildExecutionFlowGraph,
  type ExecutionFlowEdge,
  type ExecutionFlowNode,
} from "./executionFlowGraph";


// ---------------------------------------------------------------------------
// Locked layout constants — mirrors DEC-029 + the canonical mockup.

const NODE_WIDTH = 220;
const NODE_HEIGHT = 72;
const COLUMN_GAP = 80;
const ROW_GAP = 30;
const COLUMN_PITCH = NODE_WIDTH + COLUMN_GAP;
const ROW_PITCH = NODE_HEIGHT + ROW_GAP;

// React Flow's default arrowhead is 12.5×12.5; DEC-029 locks 6×6
// for ALL edges. ``markerEnd.width`` and ``markerEnd.height`` set
// the arrowhead's bounding box; the arrow itself fills it.
const ARROWHEAD_SIZE = 6;
const EDGE_STROKE_WIDTH = 1.5;


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
  // Sort each rank's nodes so synthetic sinks come last (cleaner
  // visual: real method nodes line up across ranks, sinks dangle
  // off to the right).
  for (const [, list] of byRank) {
    list.sort((a, b) => {
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


type NodeData = ExecutionFlowNode;


function MethodNode({ data, selected }: NodeProps<Node<NodeData>>) {
  const n = data;

  // Class chip (last segment of the FQCN). Synthetic sinks have an
  // empty class so we just render the title.
  const classChip = n.className
    ? n.className.split(".").pop() || n.className
    : null;

  const cornerPill = (() => {
    if (n.kind === "sink_allow") return "ALLOW";
    if (n.kind === "sink_deny") return "DENY";
    if (n.kind === "sink_neutral") return "NEUTRAL";
    if (n.kind === "entry" && n.hasGateDecision) return "GATE";
    if (n.kind === "gate") return "GATE";
    if (n.kind === "entry") return "ENTRY";
    return null;
  })();

  const cornerPillKind = (() => {
    if (n.kind === "sink_allow") return "execution-flow-pill-allow";
    if (n.kind === "sink_deny") return "execution-flow-pill-deny";
    if (n.kind === "sink_neutral") return "execution-flow-pill-neutral";
    if (cornerPill === "GATE") return "execution-flow-pill-gate";
    if (cornerPill === "ENTRY") return "execution-flow-pill-entry";
    return "";
  })();

  const cardClass = [
    "execution-flow-node",
    `execution-flow-node-${n.kind}`,
    selected && "execution-flow-node-selected",
    n.possiblyInlined && "execution-flow-node-inlined",
    n.overloadCount > 1 && "execution-flow-node-stacked",
    n.isSynthetic && "execution-flow-node-synthetic",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={cardClass}
      style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
      title={
        n.isSynthetic
          ? n.title
          : `${n.className}.${n.methodName}${
              n.sourceLine != null ? ` (line ${n.sourceLine})` : ""
            }`
      }
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


type EdgeData = ExecutionFlowEdge;


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
            className={`execution-flow-edge-label execution-flow-edge-label-${e.kind}`}
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
}: Props) {
  const { rfNodes, rfEdges } = useMemo(() => {
    const graph = buildExecutionFlowGraph(anchor);
    const positions = layoutNodes(graph.nodes);

    const rfNodes: Node<NodeData>[] = graph.nodes.map((n) => ({
      id: n.id,
      type: "method",
      data: n,
      position: positions.get(n.id) ?? { x: 0, y: 0 },
      // 220×72 fixed; React Flow uses these for hit-testing.
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      // Synthetic sinks aren't operator-clickable.
      selectable: !n.isSynthetic,
      draggable: false,
      connectable: false,
      // ``selected`` is computed by React Flow against the
      // ``selectedNodeId`` consumer state; we pre-seed via a
      // controlled-component pattern below.
    }));

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
        // The marker color is set via CSS (the SVG ``<marker>``
        // inherits ``currentColor`` from the parent path), so we
        // don't need to specify the color here.
      },
    }));

    return { rfNodes, rfEdges };
  }, [anchor]);

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

  return (
    <div className="execution-flow-container">
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
      </ReactFlow>
    </div>
  );
}
