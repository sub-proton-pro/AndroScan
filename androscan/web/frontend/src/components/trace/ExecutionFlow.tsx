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

import type { BehaviorAnchor, LiveValueRecord } from "../../api/trace";
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
    isFiredEmphasis && "execution-flow-node-fired",
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
    const fired = firedMethods ?? null;
    const live = liveValues ?? null;

    const rfNodes: Node<NodeData>[] = graph.nodes.map((n) => {
      const oKey = n.isSynthetic ? "" : overloadKeyFromNodeId(n.id);
      const isFired = !n.isSynthetic && !!fired?.has(oKey);
      const liveRecord = !n.isSynthetic && live ? (live.get(oKey) ?? null) : null;
      return {
        id: n.id,
        type: "method",
        data: { ...n, mode, fired: isFired, live: liveRecord },
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
  }, [anchor, mode, firedMethods, liveValues]);

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
