/**
 * Hook Lab — left-pane Cytoscape call-graph viewer.
 *
 * Reads from the static call-graph routes shipped in sub-step 4.1
 * (``/api/graph/{app_id}/*``) and renders one of two views:
 *
 *   1. **Package overview** (default): aggregates ``GraphNode[]`` by
 *      class.package and lays out cross-package edges with cose-bilkent.
 *      Click a package node → drill into focus mode rooted there.
 *   2. **Focus subgraph**: triggered by right-click "Focus subgraph here"
 *      on any node, or by drilling from package mode. Calls
 *      ``/neighbors/{node_ref}`` and lays out the result with dagre LR.
 *      A small ``< N >`` hops stepper in the toolbar adjusts the radius.
 *
 * Edge styling honours ``DEC-023``: ``virtual_dispatch`` /
 * ``interface_dispatch`` rendered dashed, ``external`` dimmed; nodes
 * with ``may_have_unresolved_reflection`` get an ``[R]`` suffix and a
 * coloured outline. Tippy.js tooltips on hover; right-click opens a
 * context menu; left-click on a method node fires ``onSelectNode``.
 *
 * **Frida overlay (sub-step 4.8 — DEC-023's "graph hits = bold cyan,
 *  static = muted grey"):** when the parent passes ``hitsByMethod``
 *  (a map of ``"${class}::${method}" → hit_count`` derived from the
 *  active session's hooks aggregate), nodes whose ``(class, method)``
 *  matches a key are rendered in bold cyan and their hit count is
 *  shown in the tippy tooltip + label suffix. Non-matching nodes are
 *  dimmed so the overlay is visually obvious. ``null`` (no session
 *  pinned) reverts to the unaltered 4.2 styling — operators get the
 *  static graph back the moment they detach. Package-overview mode
 *  aggregates hit counts per package and dims packages with no hits.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import cytoscape, {
  type Core,
  type EdgeDefinition,
  type ElementDefinition,
  type EventObject,
  type LayoutOptions,
  type NodeDefinition,
} from "cytoscape";
import dagre from "cytoscape-dagre";
import coseBilkent from "cytoscape-cose-bilkent";
import popper from "cytoscape-popper";
import tippy, { type Instance as TippyInstance } from "tippy.js";
import "tippy.js/dist/tippy.css";

import {
  buildClassMap,
  fetchGraph,
  fetchGraphStatus,
  fetchNeighbors,
  formatMethodSignature,
  rebuildGraph,
  type ApiResult,
  type GraphClass,
  type GraphEdge,
  type GraphIndexStatus,
  type GraphListResponse,
  type GraphNeighborsResponse,
  type GraphNode,
  type GraphStatusResponse,
} from "../api/graph";
import { useWorkbench } from "../context/WorkbenchContext";
import { classNameToJavaRelPath } from "../util/smaliClassToFile";

// Register extensions exactly once. Idempotent with React strict-mode
// double-mount because cytoscape.use is itself idempotent.
let _extensionsRegistered = false;
function ensureExtensionsRegistered(): void {
  if (_extensionsRegistered) return;
  cytoscape.use(dagre);
  cytoscape.use(coseBilkent);
  cytoscape.use(popper);
  _extensionsRegistered = true;
}

const PENDING_POLL_MS = 4000;
const MIN_HOPS = 1;
const MAX_HOPS = 6;
const DEFAULT_HOPS = 2;

type Props = {
  appId: string | null;
  /** Selected node — drives the in-tab CodeView in HookLabTab. The graph
   *  pane never reads this back; it only fires the callback on click. */
  onSelectNode: (sel: SelectedNode | null) => void;
  /** Frida hit overlay (sub-step 4.8). ``null`` means no active Frida
   *  session is pinned — the graph renders in its plain 4.2 style.
   *  A ``Map`` (possibly empty) means overlay is active: keys are
   *  ``hitKey(className, methodName)``; values are hit counts. Empty
   *  map → every node is dimmed (overlay on, no hits yet). The map is
   *  intentionally typed read-only so the parent can hand us the same
   *  reference across renders without us mutating it; we recompute
   *  Cytoscape elements on identity change like every other input. */
  hitsByMethod?: ReadonlyMap<string, number> | null;
};

// Stable, dollar-aware join used by both the overlay-builder side
// (``HookLabTab``) and the consumer side (this component). Inner-class
// boundaries surface differently in Smali ("com.example.Foo$Inner") vs.
// some Java reflection paths (sometimes ".") — keeping the format
// explicit avoids silent miss-mapping when the key crosses tiers.
export function hitKey(className: string, methodName: string): string {
  return `${className}::${methodName}`;
}

export type SelectedNode = {
  nodeId: number;
  smaliId: string;
  className: string;
  methodName: string;
  package: string;
  javaRelPath: string;
};

type ViewMode = "package" | "focus";

type ContextMenu = {
  x: number;
  y: number;
  node: GraphNode;
  klass: GraphClass | undefined;
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function CallGraphView({ appId, onSelectNode, hitsByMethod }: Props) {
  const { setPendingCodeNav, setTab } = useWorkbench();

  // Status / data state -----------------------------------------------------
  const [statusResp, setStatusResp] = useState<GraphStatusResponse | null>(
    null,
  );
  const [statusError, setStatusError] = useState<string | null>(null);
  const [graph, setGraph] = useState<GraphListResponse | null>(null);
  const [neighbors, setNeighbors] = useState<GraphNeighborsResponse | null>(
    null,
  );
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [rebuildBusy, setRebuildBusy] = useState(false);

  // View state --------------------------------------------------------------
  const [viewMode, setViewMode] = useState<ViewMode>("package");
  const [focusNodeId, setFocusNodeId] = useState<number | null>(null);
  const [focusHops, setFocusHops] = useState(DEFAULT_HOPS);
  const [filter, setFilter] = useState("");
  const [showExternal, setShowExternal] = useState(false);
  const [ctxMenu, setCtxMenu] = useState<ContextMenu | null>(null);

  // Cytoscape ---------------------------------------------------------------
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const tippiesRef = useRef<Map<string, TippyInstance>>(new Map());

  const cgStatus: GraphIndexStatus | null = statusResp?.call_graph ?? null;
  const cgState = cgStatus?.status ?? "missing";

  // -------- Status polling -------------------------------------------------
  useEffect(() => {
    if (!appId) {
      setStatusResp(null);
      setStatusError(null);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      const res = await fetchGraphStatus(appId);
      if (cancelled) return;
      if (res.ok) {
        setStatusResp(res.data);
        setStatusError(null);
      } else {
        setStatusError(res.error);
      }
    };
    void tick();
    return () => {
      cancelled = true;
    };
  }, [appId]);

  useEffect(() => {
    if (!appId) return;
    if (cgState !== "pending") return;
    const id = window.setInterval(async () => {
      const res = await fetchGraphStatus(appId);
      if (res.ok) setStatusResp(res.data);
    }, PENDING_POLL_MS);
    return () => window.clearInterval(id);
  }, [appId, cgState]);

  // -------- Graph data fetch (package mode) --------------------------------
  useEffect(() => {
    if (!appId || cgState !== "ready") return;
    if (viewMode !== "package") return;
    let cancelled = false;
    setGraphLoading(true);
    setGraphError(null);
    void fetchGraph(appId, {
      includeExternal: showExternal,
      limit: 5000,
    }).then((res: ApiResult<GraphListResponse>) => {
      if (cancelled) return;
      setGraphLoading(false);
      if (res.ok) {
        setGraph(res.data);
        setNeighbors(null);
      } else {
        setGraphError(res.error);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [appId, cgState, viewMode, showExternal]);

  // -------- Graph data fetch (focus mode) ----------------------------------
  useEffect(() => {
    if (!appId || cgState !== "ready") return;
    if (viewMode !== "focus" || focusNodeId == null) return;
    let cancelled = false;
    setGraphLoading(true);
    setGraphError(null);
    // ``fetchNeighbors`` uses the 1-hop endpoint; we expand to focusHops by
    // doing a BFS of subsequent calls. For v1 we trust limit_each on the
    // single backend hop and let the UI re-pivot for deeper dives — this
    // keeps focus mode responsive on large apps.
    void expandNeighborhood(appId, focusNodeId, focusHops).then(
      (res) => {
        if (cancelled) return;
        setGraphLoading(false);
        if (res.ok) {
          setNeighbors(res.data);
        } else {
          setGraphError(res.error);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [appId, cgState, viewMode, focusNodeId, focusHops]);

  // -------- Cytoscape init / teardown --------------------------------------
  useEffect(() => {
    if (!containerRef.current) return;
    if (cyRef.current) return;
    if (cgState !== "ready") return;
    ensureExtensionsRegistered();
    const cy = cytoscape({
      container: containerRef.current,
      style: GRAPH_STYLE,
      wheelSensitivity: 0.2,
      maxZoom: 4,
      minZoom: 0.05,
    });
    cyRef.current = cy;
    return () => {
      destroyAllTippies(tippiesRef.current);
      cy.destroy();
      cyRef.current = null;
    };
  }, [cgState]);

  // -------- Render Cytoscape elements when data changes --------------------
  // ``hitsByMethod`` is intentionally in the dep array: a fresh hooks
  // poll (every 2.5 s in HookLabTab) updates the same Map reference
  // identity-wise, so re-rendering on identity change keeps the overlay
  // live without re-laying out unless the underlying data actually
  // moved. We *do* recompute layout when overlay turns on/off because
  // that's a structural visual change, not just a count refresh.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const built = buildElementsForView({
      viewMode,
      graph,
      neighbors,
      filter,
      showExternal,
      focusNodeId,
      hitsByMethod: hitsByMethod ?? null,
    });
    destroyAllTippies(tippiesRef.current);
    cy.batch(() => {
      cy.elements().remove();
      cy.add(built.elements);
    });
    if (built.elements.length === 0) return;
    const layout: LayoutOptions =
      viewMode === "package"
        ? ({ name: "cose-bilkent", animate: false, fit: true, padding: 24 } as LayoutOptions)
        : ({ name: "dagre", rankDir: "LR", animate: false, fit: true, padding: 24 } as LayoutOptions);
    cy.layout(layout).run();
    attachTippies(cy, built.tooltipFor, tippiesRef.current);
  }, [graph, neighbors, viewMode, filter, showExternal, focusNodeId, hitsByMethod]);

  // -------- Cytoscape event wiring -----------------------------------------
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    const onTap = (ev: EventObject) => {
      const t = ev.target;
      if (t === cy) {
        // Background click clears selection + hides menu.
        setCtxMenu(null);
        onSelectNode(null);
        return;
      }
      if (typeof t.isNode !== "function" || !t.isNode()) return;
      const data = t.data();
      if (data?.kind === "package") {
        // Drill: package node → focus mode rooted at its first node.
        const firstNodeId = data.firstNodeId as number | undefined;
        if (firstNodeId != null) {
          setViewMode("focus");
          setFocusNodeId(firstNodeId);
        }
        return;
      }
      if (data?.kind === "method" && data?.node) {
        const node = data.node as GraphNode;
        const klass = data.class as GraphClass | undefined;
        onSelectNode(toSelected(node, klass));
        setCtxMenu(null);
      }
    };

    const onCxt = (ev: EventObject) => {
      const t = ev.target;
      if (t === cy) {
        setCtxMenu(null);
        return;
      }
      if (typeof t.isNode !== "function" || !t.isNode()) return;
      const data = t.data();
      if (data?.kind !== "method" || !data?.node) return;
      const orig = ev.originalEvent as MouseEvent | undefined;
      if (!orig) return;
      orig.preventDefault();
      const containerRect = (
        cy.container() as HTMLElement
      ).getBoundingClientRect();
      setCtxMenu({
        x: orig.clientX - containerRect.left,
        y: orig.clientY - containerRect.top,
        node: data.node as GraphNode,
        klass: data.class as GraphClass | undefined,
      });
    };

    cy.on("tap", onTap);
    cy.on("cxttap", onCxt);
    return () => {
      cy.off("tap", onTap);
      cy.off("cxttap", onCxt);
    };
  }, [onSelectNode]);

  // -------- Toolbar handlers -----------------------------------------------
  const handleRebuild = useCallback(async () => {
    if (!appId) return;
    setRebuildBusy(true);
    const res = await rebuildGraph(appId);
    setRebuildBusy(false);
    if (res.ok) {
      // Force re-poll on next status tick.
      const st = await fetchGraphStatus(appId);
      if (st.ok) setStatusResp(st.data);
    } else {
      setStatusError(res.error);
    }
  }, [appId]);

  const handleResetView = useCallback(() => {
    setViewMode("package");
    setFocusNodeId(null);
    setCtxMenu(null);
    onSelectNode(null);
  }, [onSelectNode]);

  // -------- Context-menu actions -------------------------------------------
  const focusOnContextNode = useCallback(
    (hops: number) => {
      if (!ctxMenu) return;
      setFocusHops(hops);
      setFocusNodeId(ctxMenu.node.id);
      setViewMode("focus");
      setCtxMenu(null);
    },
    [ctxMenu],
  );

  const openInInspect = useCallback(
    (node: GraphNode, klass: GraphClass | undefined) => {
      if (!appId || !klass) return;
      setPendingCodeNav({
        appId,
        className: klass.class_name,
        relPath: classNameToJavaRelPath(klass.class_name),
        method: node.method_name,
      });
      setTab("inspect");
    },
    [appId, setPendingCodeNav, setTab],
  );

  // -------- Render ---------------------------------------------------------
  const overlay = pickOverlay({
    appId,
    cgState,
    statusError,
    cgStatus,
    graphError,
    onRebuild: handleRebuild,
    rebuildBusy,
  });

  return (
    <div className="callgraph-pane" style={paneStyle}>
      <div className="callgraph-toolbar" style={toolbarStyle}>
        <input
          className="callgraph-filter"
          type="text"
          placeholder="Filter package, class, or method…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          spellCheck={false}
          style={filterStyle}
          disabled={cgState !== "ready"}
        />
        <label style={toggleLabelStyle}>
          <input
            type="checkbox"
            checked={showExternal}
            onChange={(e) => setShowExternal(e.target.checked)}
            disabled={cgState !== "ready"}
          />{" "}
          External
        </label>
        {viewMode === "focus" && (
          <span style={hopsStepperStyle}>
            <button
              type="button"
              className="callgraph-btn"
              onClick={() => setFocusHops((h) => Math.max(MIN_HOPS, h - 1))}
              disabled={focusHops <= MIN_HOPS}
              aria-label="Decrease hops"
            >
              −
            </button>
            <span style={{ minWidth: "1.5em", textAlign: "center" }}>
              {focusHops}
            </span>
            <button
              type="button"
              className="callgraph-btn"
              onClick={() => setFocusHops((h) => Math.min(MAX_HOPS, h + 1))}
              disabled={focusHops >= MAX_HOPS}
              aria-label="Increase hops"
            >
              +
            </button>
            <span className="muted small">hops</span>
          </span>
        )}
        <button
          type="button"
          className="callgraph-btn"
          onClick={handleResetView}
          disabled={cgState !== "ready" || viewMode === "package"}
        >
          Package overview
        </button>
        <button
          type="button"
          className="callgraph-btn"
          onClick={handleRebuild}
          disabled={!appId || rebuildBusy || cgState === "pending"}
        >
          {rebuildBusy ? "Rebuilding…" : "Rebuild"}
        </button>
      </div>

      <div className="callgraph-body" style={bodyStyle}>
        <div ref={containerRef} style={canvasStyle} />
        {graphLoading && cgState === "ready" && (
          <div style={spinnerOverlayStyle} className="muted small">
            loading graph…
          </div>
        )}
        {overlay}
        {ctxMenu && (
          <ContextMenuBox
            menu={ctxMenu}
            onClose={() => setCtxMenu(null)}
            onFocus={focusOnContextNode}
            onOpenInInspect={() =>
              openInInspect(ctxMenu.node, ctxMenu.klass)
            }
          />
        )}
      </div>

      {viewMode === "focus" && neighbors && (
        <div className="callgraph-footer" style={footerStyle}>
          <span className="muted small">
            Focus: <code>{shortMethodLabel(neighbors.node, lookupClass(neighbors.classes, neighbors.node.class_id))}</code>{" "}
            · {neighbors.callers.length} callers · {neighbors.callees.length} callees
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers below the component
// ---------------------------------------------------------------------------

function toSelected(node: GraphNode, klass: GraphClass | undefined): SelectedNode {
  const cls = klass?.class_name ?? `class#${node.class_id}`;
  return {
    nodeId: node.id,
    smaliId: node.smali_id,
    className: cls,
    methodName: node.method_name,
    package: klass?.package ?? "",
    javaRelPath: classNameToJavaRelPath(cls),
  };
}

function lookupClass(classes: GraphClass[], id: number): GraphClass | undefined {
  return classes.find((c) => c.id === id);
}

function shortMethodLabel(node: GraphNode, klass: GraphClass | undefined): string {
  const sn = klass?.simple_name ?? klass?.class_name?.split(".").pop() ?? "?";
  return `${sn}.${node.method_name}`;
}

// -------- Multi-hop expansion -------------------------------------------
//
// The backend's /neighbors endpoint returns 1 hop. To support a hops
// stepper we BFS up to ``hops`` rounds, deduping nodes/edges as we go.
// The resulting payload mimics the shape of GraphNeighborsResponse so the
// rest of the renderer doesn't care that it's a synthetic aggregate.
async function expandNeighborhood(
  appId: string,
  rootId: number,
  hops: number,
): Promise<ApiResult<GraphNeighborsResponse>> {
  const first = await fetchNeighbors(appId, rootId, { limitEach: 200 });
  if (!first.ok) return first;
  if (hops <= 1) return first;

  const root = first.data.node;
  const callerEdges = new Map<string, GraphNeighborsResponse["callers"][number]>();
  const calleeEdges = new Map<string, GraphNeighborsResponse["callees"][number]>();
  const classMap = new Map<number, GraphClass>();
  for (const c of first.data.classes) classMap.set(c.id, c);
  const seenNodes = new Map<number, GraphNode>([[root.id, root]]);
  for (const e of first.data.callers) {
    callerEdges.set(edgeKey(e.edge), e);
    seenNodes.set(e.node.id, e.node);
  }
  for (const e of first.data.callees) {
    calleeEdges.set(edgeKey(e.edge), e);
    seenNodes.set(e.node.id, e.node);
  }

  let frontier = new Set<number>([
    ...first.data.callers.map((e) => e.node.id),
    ...first.data.callees.map((e) => e.node.id),
  ]);
  for (let depth = 2; depth <= hops; depth++) {
    const next = new Set<number>();
    // Bound expansion so a hub like Log.d can't explode the request count.
    const PER_DEPTH_BUDGET = 50;
    let budgetLeft = PER_DEPTH_BUDGET;
    for (const id of frontier) {
      if (budgetLeft <= 0) break;
      budgetLeft--;
      const r = await fetchNeighbors(appId, id, { limitEach: 50 });
      if (!r.ok) continue;
      for (const c of r.data.classes) {
        if (!classMap.has(c.id)) classMap.set(c.id, c);
      }
      for (const e of r.data.callers) {
        if (!callerEdges.has(edgeKey(e.edge))) {
          callerEdges.set(edgeKey(e.edge), e);
          if (!seenNodes.has(e.node.id)) {
            seenNodes.set(e.node.id, e.node);
            next.add(e.node.id);
          }
        }
      }
      for (const e of r.data.callees) {
        if (!calleeEdges.has(edgeKey(e.edge))) {
          calleeEdges.set(edgeKey(e.edge), e);
          if (!seenNodes.has(e.node.id)) {
            seenNodes.set(e.node.id, e.node);
            next.add(e.node.id);
          }
        }
      }
    }
    frontier = next;
    if (frontier.size === 0) break;
  }

  return {
    ok: true,
    data: {
      app_id: appId,
      node: root,
      callers: Array.from(callerEdges.values()),
      callees: Array.from(calleeEdges.values()),
      classes: Array.from(classMap.values()),
    },
  };
}

function edgeKey(e: GraphEdge): string {
  return `${e.src_id}->${e.dst_id}:${e.kind}@${e.src_line ?? "?"}`;
}

// -------- Element building ----------------------------------------------

type BuildResult = {
  elements: ElementDefinition[];
  /** Per-element id → tooltip HTML. Keyed by ``id`` of the cy element. */
  tooltipFor: Map<string, string>;
};

function buildElementsForView(args: {
  viewMode: ViewMode;
  graph: GraphListResponse | null;
  neighbors: GraphNeighborsResponse | null;
  filter: string;
  showExternal: boolean;
  focusNodeId: number | null;
  /** ``null`` means "no overlay" — render plain 4.2 styling.
   *  An empty ``Map`` means "overlay on, but no hits yet" — every
   *  node renders as ``unhit`` so the operator sees that the session
   *  is wired but nothing has fired. */
  hitsByMethod: ReadonlyMap<string, number> | null;
}): BuildResult {
  if (args.viewMode === "package") {
    return buildPackageOverviewElements(
      args.graph,
      args.filter,
      args.showExternal,
      args.hitsByMethod,
    );
  }
  return buildFocusElements(
    args.neighbors,
    args.filter,
    args.focusNodeId,
    args.hitsByMethod,
  );
}

function buildPackageOverviewElements(
  graph: GraphListResponse | null,
  filter: string,
  showExternal: boolean,
  hitsByMethod: ReadonlyMap<string, number> | null,
): BuildResult {
  const tooltipFor = new Map<string, string>();
  if (!graph) return { elements: [], tooltipFor };
  const classMap = buildClassMap(graph.classes);
  const overlayActive = hitsByMethod != null;

  // Aggregate: package name → counts + first node id for drill-in.
  type PackageAgg = {
    pkg: string;
    classes: Set<number>;
    methods: number;
    reflection: number;
    firstNodeId: number;
    /** Number of methods in the package that matched a hit key. */
    hitMethods: number;
    /** Sum of hit counts across all matched methods in the package. */
    totalHits: number;
  };
  const aggs = new Map<string, PackageAgg>();
  const nodePkg = new Map<number, string>();
  for (const n of graph.nodes) {
    const cls = classMap.get(n.class_id);
    if (!cls) continue;
    if (!showExternal && (n.is_external || cls.is_external)) continue;
    const pkg = cls.package || "(default)";
    const matchesFilter =
      !filter ||
      pkg.toLowerCase().includes(filter.toLowerCase()) ||
      cls.class_name.toLowerCase().includes(filter.toLowerCase()) ||
      n.method_name.toLowerCase().includes(filter.toLowerCase());
    if (!matchesFilter) continue;
    let agg = aggs.get(pkg);
    if (!agg) {
      agg = {
        pkg,
        classes: new Set(),
        methods: 0,
        reflection: 0,
        firstNodeId: n.id,
        hitMethods: 0,
        totalHits: 0,
      };
      aggs.set(pkg, agg);
    }
    agg.classes.add(cls.id);
    agg.methods += 1;
    if (n.may_have_unresolved_reflection) agg.reflection += 1;
    nodePkg.set(n.id, pkg);
    if (overlayActive) {
      const hits = hitsByMethod.get(hitKey(cls.class_name, n.method_name));
      if (hits && hits > 0) {
        agg.hitMethods += 1;
        agg.totalHits += hits;
      }
    }
  }

  const nodes: NodeDefinition[] = [];
  for (const a of aggs.values()) {
    const id = `pkg:${a.pkg}`;
    const hasHits = a.totalHits > 0;
    const overlayLabel = overlayActive
      ? hasHits
        ? `\n• ${a.totalHits} hit${a.totalHits === 1 ? "" : "s"} in ${a.hitMethods} method${a.hitMethods === 1 ? "" : "s"}`
        : ""
      : "";
    const overlayClass = overlayActive
      ? hasHits
        ? "has-hits"
        : "no-hits"
      : "";
    nodes.push({
      data: {
        id,
        kind: "package",
        label:
          `${a.pkg}\n${a.classes.size} classes · ${a.methods} methods` +
          overlayLabel,
        firstNodeId: a.firstNodeId,
        hasReflection: a.reflection > 0,
        hitMethods: a.hitMethods,
        totalHits: a.totalHits,
      },
      classes:
        ["pkgnode", a.reflection > 0 ? "reflective" : "", overlayClass]
          .filter(Boolean)
          .join(" "),
    });
    const overlayTooltip =
      overlayActive && hasHits
        ? `<br/><b style='color:${HIT_CYAN}'>frida: ${a.totalHits} hit${
            a.totalHits === 1 ? "" : "s"
          } across ${a.hitMethods} method${a.hitMethods === 1 ? "" : "s"}</b>`
        : "";
    tooltipFor.set(
      id,
      `<b>${escapeHtml(a.pkg)}</b><br/>${a.classes.size} classes · ${a.methods} methods${
        a.reflection > 0 ? `<br/>${a.reflection} with reflection sentinels` : ""
      }${overlayTooltip}`,
    );
  }

  // Cross-package edges, with weights.
  type EdgeAgg = { src: string; dst: string; count: number; anyDashed: boolean };
  const edgeAggs = new Map<string, EdgeAgg>();
  for (const e of graph.edges) {
    const sp = nodePkg.get(e.src_id);
    const dp = nodePkg.get(e.dst_id);
    if (!sp || !dp || sp === dp) continue;
    const key = `${sp}=>${dp}`;
    let agg = edgeAggs.get(key);
    if (!agg) {
      agg = { src: sp, dst: dp, count: 0, anyDashed: false };
      edgeAggs.set(key, agg);
    }
    agg.count += 1;
    if (
      e.kind === "virtual_dispatch" ||
      e.kind === "interface_dispatch" ||
      e.kind === "external"
    ) {
      agg.anyDashed = true;
    }
  }

  const edges: EdgeDefinition[] = [];
  for (const a of edgeAggs.values()) {
    edges.push({
      data: {
        id: `pkgedge:${a.src}=>${a.dst}`,
        source: `pkg:${a.src}`,
        target: `pkg:${a.dst}`,
        weight: a.count,
        label: String(a.count),
      },
      classes: a.anyDashed ? "pkgedge dashed" : "pkgedge",
    });
  }

  return { elements: [...nodes, ...edges], tooltipFor };
}

function buildFocusElements(
  neighbors: GraphNeighborsResponse | null,
  filter: string,
  focusNodeId: number | null,
  hitsByMethod: ReadonlyMap<string, number> | null,
): BuildResult {
  const tooltipFor = new Map<string, string>();
  if (!neighbors) return { elements: [], tooltipFor };
  const classMap = buildClassMap(neighbors.classes);
  const overlayActive = hitsByMethod != null;

  const allNodes = new Map<number, GraphNode>();
  allNodes.set(neighbors.node.id, neighbors.node);
  for (const e of neighbors.callers) allNodes.set(e.node.id, e.node);
  for (const e of neighbors.callees) allNodes.set(e.node.id, e.node);

  const matchesFilter = (n: GraphNode, klass: GraphClass | undefined): boolean => {
    if (!filter) return true;
    const f = filter.toLowerCase();
    return (
      (klass?.package?.toLowerCase().includes(f) ?? false) ||
      (klass?.class_name?.toLowerCase().includes(f) ?? false) ||
      n.method_name.toLowerCase().includes(f)
    );
  };

  const lookupHits = (n: GraphNode, klass: GraphClass | undefined): number => {
    if (!overlayActive || !klass) return 0;
    return hitsByMethod.get(hitKey(klass.class_name, n.method_name)) ?? 0;
  };

  const nodeDefs: NodeDefinition[] = [];
  const visibleIds = new Set<number>();
  for (const n of allNodes.values()) {
    const cls = classMap.get(n.class_id);
    // Always include the focus root, even if filter would hide it.
    if (n.id !== focusNodeId && !matchesFilter(n, cls)) continue;
    visibleIds.add(n.id);
    const isRoot = n.id === focusNodeId;
    const hits = lookupHits(n, cls);
    const cssClasses = [
      "methodnode",
      isRoot ? "focusroot" : "",
      n.is_external ? "external" : "",
      n.may_have_unresolved_reflection ? "reflective" : "",
      overlayActive ? (hits > 0 ? "hit" : "unhit") : "",
    ]
      .filter(Boolean)
      .join(" ");
    const label = renderNodeLabel(n, cls, hits);
    nodeDefs.push({
      data: {
        id: `n:${n.id}`,
        kind: "method",
        label,
        node: n,
        class: cls,
        hits,
      },
      classes: cssClasses,
    });
    tooltipFor.set(`n:${n.id}`, renderNodeTooltipHtml(n, cls, hits));
  }

  const edgeDefs: EdgeDefinition[] = [];
  const seenEdgeKeys = new Set<string>();
  const pushEdge = (e: GraphEdge) => {
    if (!visibleIds.has(e.src_id) || !visibleIds.has(e.dst_id)) return;
    const key = edgeKey(e);
    if (seenEdgeKeys.has(key)) return;
    seenEdgeKeys.add(key);
    edgeDefs.push({
      data: {
        id: `e:${key}`,
        source: `n:${e.src_id}`,
        target: `n:${e.dst_id}`,
        kind: e.kind,
        invokeOp: e.invoke_op,
        srcLine: e.src_line,
      },
      classes: edgeCssClasses(e),
    });
  };
  for (const en of neighbors.callers) pushEdge(en.edge);
  for (const en of neighbors.callees) pushEdge(en.edge);

  return { elements: [...nodeDefs, ...edgeDefs], tooltipFor };
}

function edgeCssClasses(e: GraphEdge): string {
  const dashed =
    e.kind === "virtual_dispatch" ||
    e.kind === "interface_dispatch" ||
    e.kind === "external";
  const parts = ["edge", `kind-${e.kind}`];
  if (dashed) parts.push("dashed");
  if (e.kind === "external") parts.push("external");
  return parts.join(" ");
}

function renderNodeLabel(
  n: GraphNode,
  klass: GraphClass | undefined,
  hits: number,
): string {
  const sn = klass?.simple_name ?? klass?.class_name?.split(".").pop() ?? "?";
  const refl = n.may_have_unresolved_reflection ? "  [R]" : "";
  // Hit count is appended only when overlay is active *and* hits > 0;
  // ``unhit`` nodes keep their original two-token label so the dim
  // styling — not extra noise — communicates "no fires here yet".
  const hitSuffix = hits > 0 ? `  ×${hits}` : "";
  return `${sn}.${n.method_name}${refl}${hitSuffix}`;
}

function renderNodeTooltipHtml(
  n: GraphNode,
  klass: GraphClass | undefined,
  hits: number,
): string {
  const sig = formatMethodSignature(n, klass);
  const flags: string[] = [];
  if (n.is_static) flags.push("static");
  if (n.is_abstract) flags.push("abstract");
  if (n.is_native) flags.push("native");
  if (n.is_constructor) flags.push("ctor");
  if (n.is_external) flags.push("external");
  const flagLine = flags.length ? `<i>${flags.join(" ")}</i><br/>` : "";
  const reflLine = n.may_have_unresolved_reflection
    ? "<br/><span style='color:#f0883e'>uses reflection (Class.forName / Method.invoke)</span>"
    : "";
  // Hit count goes last so it visually dominates the tooltip — operators
  // skim for "did this fire?" before they read the signature.
  const hitLine =
    hits > 0
      ? `<br/><b style='color:${HIT_CYAN}'>frida: ${hits} hit${hits === 1 ? "" : "s"}</b>`
      : "";
  return `<b>${escapeHtml(sig)}</b><br/>${flagLine}returns ${escapeHtml(
    n.return_type,
  )}${reflLine}${hitLine}`;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// -------- Tippy tooltips bound to cytoscape-popper -----------------------

function attachTippies(
  cy: Core,
  tooltipFor: Map<string, string>,
  store: Map<string, TippyInstance>,
) {
  cy.nodes().forEach((node) => {
    const id = node.id();
    const html = tooltipFor.get(id);
    if (!html) return;
    type PopperHostNode = { popperRef: () => { getBoundingClientRect: () => DOMRect } };
    const popperHost = node as unknown as PopperHostNode;
    const ref = popperHost.popperRef();
    const dummy = document.createElement("div");
    const tip = tippy(dummy, {
      trigger: "manual",
      content: html,
      allowHTML: true,
      arrow: true,
      placement: "top",
      hideOnClick: false,
      theme: "androscan-graph",
      getReferenceClientRect: () => ref.getBoundingClientRect(),
    });
    store.set(id, tip);
    node.on("mouseover", () => tip.show());
    node.on("mouseout", () => tip.hide());
    node.on("position", () => {
      // Force tippy to re-measure on layout/zoom moves.
      if (tip.state.isVisible) tip.popperInstance?.update();
    });
  });
}

function destroyAllTippies(store: Map<string, TippyInstance>) {
  for (const t of store.values()) {
    try {
      t.destroy();
    } catch {
      /* noop */
    }
  }
  store.clear();
}

// -------- Status overlay -------------------------------------------------

function pickOverlay(args: {
  appId: string | null;
  cgState: GraphIndexStatus["status"] | "missing";
  statusError: string | null;
  cgStatus: GraphIndexStatus | null;
  graphError: string | null;
  onRebuild: () => void | Promise<void>;
  rebuildBusy: boolean;
}) {
  const { appId, cgState, statusError, cgStatus, graphError, onRebuild, rebuildBusy } =
    args;
  if (!appId) {
    return <Overlay primary="No app selected" />;
  }
  if (statusError) {
    return <Overlay primary="Failed to load status" secondary={statusError} />;
  }
  if (cgState === "missing") {
    return (
      <Overlay
        primary="Call graph not built yet"
        secondary="Build the decompile cache first; the call graph auto-builds on completion. You can also kick a manual rebuild now."
        action={
          <button
            type="button"
            className="callgraph-btn primary"
            onClick={() => void onRebuild()}
            disabled={rebuildBusy}
          >
            {rebuildBusy ? "Building…" : "Build now"}
          </button>
        }
      />
    );
  }
  if (cgState === "pending") {
    return (
      <Overlay
        primary="Building call graph…"
        secondary="apktool + parser worker is running. This usually takes 10–60s."
      />
    );
  }
  if (cgState === "failed") {
    return (
      <Overlay
        primary="Call-graph build failed"
        secondary={cgStatus?.error || "see server logs"}
        action={
          <button
            type="button"
            className="callgraph-btn primary"
            onClick={() => void onRebuild()}
            disabled={rebuildBusy}
          >
            {rebuildBusy ? "Retrying…" : "Retry"}
          </button>
        }
      />
    );
  }
  if (graphError) {
    return <Overlay primary="Failed to load graph" secondary={graphError} />;
  }
  return null;
}

function Overlay(props: {
  primary: string;
  secondary?: string;
  action?: React.ReactNode;
}) {
  return (
    <div style={overlayStyle}>
      <div style={{ fontWeight: 600 }}>{props.primary}</div>
      {props.secondary && (
        <div className="muted small" style={{ maxWidth: "32em", textAlign: "center" }}>
          {props.secondary}
        </div>
      )}
      {props.action}
    </div>
  );
}

// -------- Right-click context menu ---------------------------------------

function ContextMenuBox(props: {
  menu: ContextMenu;
  onClose: () => void;
  onFocus: (hops: number) => void;
  onOpenInInspect: () => void;
}) {
  const { menu, onClose, onFocus, onOpenInInspect } = props;
  const [hops, setHops] = useState(DEFAULT_HOPS);
  const cls = menu.klass?.class_name ?? "?";
  return (
    <>
      <div onClick={onClose} style={ctxBackdropStyle} />
      <div
        style={{ ...ctxMenuStyle, left: menu.x, top: menu.y }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="muted small" style={{ marginBottom: "0.3rem" }}>
          <code>
            {cls}.{menu.node.method_name}
          </code>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <button
            type="button"
            className="callgraph-btn"
            onClick={() => setHops((h) => Math.max(MIN_HOPS, h - 1))}
            disabled={hops <= MIN_HOPS}
          >
            −
          </button>
          <span style={{ minWidth: "1.5em", textAlign: "center" }}>{hops}</span>
          <button
            type="button"
            className="callgraph-btn"
            onClick={() => setHops((h) => Math.min(MAX_HOPS, h + 1))}
            disabled={hops >= MAX_HOPS}
          >
            +
          </button>
          <span className="muted small">hops</span>
        </div>
        <button
          type="button"
          className="callgraph-btn primary"
          onClick={() => onFocus(hops)}
          style={{ marginTop: "0.4rem" }}
        >
          Focus subgraph here
        </button>
        <button
          type="button"
          className="callgraph-btn"
          onClick={() => {
            onOpenInInspect();
            onClose();
          }}
        >
          Open in Inspect
        </button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Cytoscape stylesheet — kept in JS (not CSS) because cytoscape requires its
// own selector engine. Honours DEC-023's edge styling spec: dashed for
// virtual_dispatch / interface_dispatch / external; reflection nodes get an
// orange outline.
//
// **Frida overlay (sub-step 4.8).** DEC-023 calls for "graph hits = bold
// cyan, static = muted grey". The overlay layers four extra rules on top
// of the existing styling:
//   * ``node.methodnode.hit``         — bold cyan border + glow + brighter
//                                       background; method has fired in
//                                       the active session.
//   * ``node.methodnode.unhit``       — opacity ~0.45 so the eye is drawn
//                                       to ``.hit`` first; rendered only
//                                       when overlay is active.
//   * ``node.pkgnode.has-hits``       — same bold cyan treatment for the
//                                       package overview (one or more
//                                       methods inside fired).
//   * ``node.pkgnode.no-hits``        — dimmed package node, overlay-only.
// Reflection / focusroot styling still wins for border colour where they
// overlap by virtue of selector order (``.hit`` declared *after* the
// reflection / focusroot rules so its bold cyan border takes precedence
// when both apply — operators care about "did it fire?" more than "is it
// reflective?" when a hook has just landed).
// ---------------------------------------------------------------------------

/** Cytoscape stylesheets are JS-strings; this is the single colour token
 *  shared with the tooltip HTML so the overlay reads as one visual unit. */
const HIT_CYAN = "#56d4dd";

const GRAPH_STYLE: cytoscape.StylesheetStyle[] = [
  {
    selector: "node.pkgnode",
    style: {
      label: "data(label)",
      "text-wrap": "wrap",
      "text-valign": "center",
      "text-halign": "center",
      "background-color": "#1f6feb",
      color: "#f0f6fc",
      "font-size": 11,
      "border-width": 1,
      "border-color": "#30363d",
      shape: "round-rectangle",
      width: "label",
      height: "label",
      padding: "10px",
    } as cytoscape.Css.Node,
  },
  {
    selector: "node.pkgnode.reflective",
    style: {
      "border-color": "#f0883e",
      "border-width": 2,
    } as cytoscape.Css.Node,
  },
  {
    selector: "edge.pkgedge",
    style: {
      label: "data(label)",
      "font-size": 9,
      width: "mapData(weight, 1, 50, 1, 6)",
      "line-color": "#586069",
      "curve-style": "bezier",
      "target-arrow-shape": "triangle",
      "target-arrow-color": "#586069",
      "color": "#8b949e",
    } as cytoscape.Css.Edge,
  },
  {
    selector: "edge.pkgedge.dashed",
    style: {
      "line-style": "dashed",
    } as cytoscape.Css.Edge,
  },
  {
    selector: "node.methodnode",
    style: {
      label: "data(label)",
      "text-valign": "center",
      "text-halign": "center",
      "background-color": "#21262d",
      color: "#c9d1d9",
      "font-size": 10,
      "border-width": 1,
      "border-color": "#30363d",
      shape: "round-rectangle",
      width: "label",
      height: "label",
      padding: "8px",
    } as cytoscape.Css.Node,
  },
  {
    selector: "node.methodnode.focusroot",
    style: {
      "background-color": "#1f6feb",
      "border-color": "#58a6ff",
      "border-width": 2,
      color: "#f0f6fc",
    } as cytoscape.Css.Node,
  },
  {
    selector: "node.methodnode.external",
    style: {
      "background-color": "#161b22",
      "border-color": "#444c56",
      color: "#8b949e",
      "border-style": "dashed",
    } as cytoscape.Css.Node,
  },
  {
    selector: "node.methodnode.reflective",
    style: {
      "border-color": "#f0883e",
      "border-width": 2,
    } as cytoscape.Css.Node,
  },
  {
    selector: "edge.edge",
    style: {
      width: 1.5,
      "line-color": "#586069",
      "curve-style": "bezier",
      "target-arrow-shape": "triangle",
      "target-arrow-color": "#586069",
      "font-size": 8,
      color: "#8b949e",
    } as cytoscape.Css.Edge,
  },
  {
    selector: "edge.edge.dashed",
    style: {
      "line-style": "dashed",
    } as cytoscape.Css.Edge,
  },
  {
    selector: "edge.edge.external",
    style: {
      "line-color": "#444c56",
      "target-arrow-color": "#444c56",
      opacity: 0.6,
    } as cytoscape.Css.Edge,
  },
  // -------- Frida overlay (sub-step 4.8) ----------------------------------
  {
    selector: "node.methodnode.hit",
    style: {
      "background-color": "#0c3a3f",
      "border-color": HIT_CYAN,
      "border-width": 3,
      color: HIT_CYAN,
      "font-weight": "bold",
      "shadow-blur": 16,
      "shadow-color": HIT_CYAN,
      "shadow-opacity": 0.6,
    } as cytoscape.Css.Node,
  },
  {
    selector: "node.methodnode.unhit",
    style: {
      opacity: 0.45,
    } as cytoscape.Css.Node,
  },
  {
    selector: "node.pkgnode.has-hits",
    style: {
      "background-color": "#0c3a3f",
      "border-color": HIT_CYAN,
      "border-width": 3,
      color: HIT_CYAN,
      "font-weight": "bold",
    } as cytoscape.Css.Node,
  },
  {
    selector: "node.pkgnode.no-hits",
    style: {
      opacity: 0.5,
    } as cytoscape.Css.Node,
  },
];

// ---------------------------------------------------------------------------
// Inline styles — co-located with the component to avoid bloating App.css
// for a single tab. Variables use the workbench's existing CSS custom
// properties so the pane matches the rest of the UI.
// ---------------------------------------------------------------------------

const paneStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  width: "100%",
  background: "var(--panel)",
};

const toolbarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
  padding: "0.4rem 0.55rem",
  borderBottom: "1px solid var(--border)",
  background: "var(--panel-2)",
  flexWrap: "wrap",
};

const filterStyle: CSSProperties = {
  flex: "1 1 12em",
  minWidth: "8em",
  background: "var(--bg)",
  border: "1px solid var(--border)",
  color: "var(--text)",
  borderRadius: 4,
  padding: "0.25rem 0.5rem",
  fontSize: "0.8rem",
};

const toggleLabelStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "0.25rem",
  fontSize: "0.78rem",
  color: "var(--muted)",
  whiteSpace: "nowrap",
};

const hopsStepperStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "0.25rem",
};

const bodyStyle: CSSProperties = {
  position: "relative",
  flex: 1,
  minHeight: 0,
};

const canvasStyle: CSSProperties = {
  position: "absolute",
  inset: 0,
  background: "var(--bg)",
};

const overlayStyle: CSSProperties = {
  position: "absolute",
  inset: 0,
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: "0.6rem",
  background: "rgba(13, 17, 23, 0.85)",
  textAlign: "center",
  padding: "1rem",
};

const spinnerOverlayStyle: CSSProperties = {
  position: "absolute",
  top: "0.5rem",
  right: "0.5rem",
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "0.2rem 0.5rem",
  fontStyle: "italic",
};

const ctxBackdropStyle: CSSProperties = {
  position: "absolute",
  inset: 0,
  background: "transparent",
  zIndex: 5,
};

const ctxMenuStyle: CSSProperties = {
  position: "absolute",
  zIndex: 6,
  background: "var(--panel-2)",
  border: "1px solid var(--border-strong)",
  borderRadius: 4,
  padding: "0.5rem",
  display: "flex",
  flexDirection: "column",
  gap: "0.3rem",
  boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
  minWidth: "12em",
  fontSize: "0.78rem",
};

const footerStyle: CSSProperties = {
  borderTop: "1px solid var(--border)",
  background: "var(--panel-2)",
  padding: "0.3rem 0.55rem",
};
