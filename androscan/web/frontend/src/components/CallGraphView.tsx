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
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
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
import { appPackagePrefix, isAppPackage } from "../util/appPackage";
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
  /** Selected node — drives the in-tab CodeView in LabTab. The graph
   *  pane never reads this back; it only fires the callback on click. */
  onSelectNode: (sel: SelectedNode | null) => void;
  /** Dossier package (e.g. ``com.example.weakbank.low``) used by the
   *  default-on "App only" toggle to drop the bundled-library noise
   *  from the package overview. ``null`` (no dossier or no package
   *  field) disables the toggle and falls back to the unfiltered view
   *  — same behaviour as before this prop existed. */
  appPackage?: string | null;
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
// (``LabTab``) and the consumer side (this component). Inner-class
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

export function CallGraphView({
  appId,
  onSelectNode,
  appPackage = null,
  hitsByMethod,
}: Props) {
  const { setPendingCodeNav, setPendingTraceEntry, setLabMode, setTab } =
    useWorkbench();

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
  // Default ON whenever we have a known dossier package — the typical
  // case for any project that's been through ``--apk`` analysis. Falls
  // back to OFF (same as pre-toggle behaviour) when the dossier
  // doesn't carry a package field. Operators flip it off to see the
  // bundled-library jungle (Material / AndroidX / Kotlin / OkHttp …).
  const [appOnly, setAppOnly] = useState<boolean>(appPackage != null);
  // Whether the operator has manually touched the toggle since the
  // current ``appPackage`` resolved. Until then we keep auto-flipping
  // the default whenever the dossier transitions null → "com.example.…"
  // (dossier loads async, so the initial useState above sees ``null``
  // for the first render or two). Once the operator clicks, we never
  // override their choice.
  const userTouchedAppOnly = useRef(false);
  const [ctxMenu, setCtxMenu] = useState<ContextMenu | null>(null);

  // -------- Search dropdown state -----------------------------------------
  // ``searchOpen`` is the "popover visible" flag. We open it on first
  // keystroke (filter goes non-empty *and* input is focused), close on
  // Escape / blur-outside / click-outside / Enter on a result.
  // ``activeHitIdx`` is a flat index over the concatenated
  // packages+classes+methods array (see ``flattenHits``); ↑/↓ walk
  // it, Enter activates. Reset to 0 on every filter change so the
  // first match is always pre-selected.
  const [searchOpen, setSearchOpen] = useState(false);
  const [activeHitIdx, setActiveHitIdx] = useState(0);
  const searchAnchorRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const searchListRef = useRef<HTMLUListElement | null>(null);

  // -------- Browse-mode tree state ---------------------------------------
  // The dropdown shows a hierarchical Package → Class → Method tree (modeled
  // on Inspect tab's ``ClassMethodTree``) when the filter is empty. The
  // typeahead search hits take over the moment the user types. State lives
  // up here so expansion is preserved across open/close cycles — matches the
  // Inspect tab's behaviour and avoids the operator having to re-expand to
  // the same node every time they reopen the dropdown.
  const [openPkgs, setOpenPkgs] = useState<Record<string, boolean>>({});
  const [openCls, setOpenCls] = useState<Record<string, boolean>>({});
  const [showAppSection, setShowAppSection] = useState<boolean>(true);
  const [showLibSection, setShowLibSection] = useState<boolean>(false);

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

  // -------- App-only toggle: auto-default on dossier resolve ---------------
  // The dossier loads asynchronously after mount, so the initial
  // ``useState(appPackage != null)`` reads ``null`` for the first
  // render or two. Re-default the toggle whenever ``appPackage``
  // transitions to a non-null value, but stop the moment the operator
  // touches it themselves so we don't fight a manual choice. Also
  // reset the "user touched" sentinel when the project (``appId``)
  // changes so each project gets its own fresh default.
  useEffect(() => {
    userTouchedAppOnly.current = false;
    // Drop any per-(package, class) expansion state from the previous
    // project so the dropdown tree opens cleanly for the new app.
    setOpenPkgs({});
    setOpenCls({});
  }, [appId]);
  useEffect(() => {
    if (userTouchedAppOnly.current) return;
    setAppOnly(appPackage != null);
  }, [appPackage]);

  // -------- Graph data fetch (package mode) --------------------------------
  // ``appOnly`` translates to a server-side ``package_prefix`` filter
  // (``c.package LIKE 'com.example.weakbank%'`` in ``list_graph``).
  // That keeps the response well under the 5000-node frontend cap even
  // for apps that ship hundreds of bundled-library packages — the
  // pre-toggle behaviour was that ``com.example.weakbank``'s methods
  // (parsed last because their smali lives in ``smali_classes2/``)
  // landed in the tail and got truncated, so the filter input had
  // nothing to match. ``appPackagePrefix`` widens to the dossier
  // package's parent (drops one segment) so sibling-flavour apps
  // (``.low`` / ``.medium`` / ``.high`` builds of the same product)
  // share one overview when the operator switches between them.
  const packagePrefix =
    appOnly && appPackage ? appPackagePrefix(appPackage) : null;
  useEffect(() => {
    if (!appId || cgState !== "ready") return;
    if (viewMode !== "package") return;
    let cancelled = false;
    setGraphLoading(true);
    setGraphError(null);
    void fetchGraph(appId, {
      includeExternal: showExternal,
      packagePrefix: packagePrefix ?? undefined,
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
  }, [appId, cgState, viewMode, showExternal, packagePrefix]);

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

  // -------- Inverse-scale labels on zoom-in --------------------------------
  // Cytoscape's wheel-zoom is "magnifier" by default — every visual
  // element scales together, so a high zoom means a couple of huge
  // overlapping rectangles that don't help navigate dense library
  // packages (Material/AndroidX/Kotlin etc.). On zoom > 1 we inversely
  // scale font / padding / border so on-screen label size stays roughly
  // constant; the underlying layout positions don't change, but the
  // node footprint in graph-coordinate space shrinks, so dense clusters
  // visually spread out as you zoom in. Clamped at ``inv <= 1`` (only
  // zoom-in is affected) so zoom-out keeps today's behaviour — at low
  // zoom the whole graph is a smudge anyway and individual labels
  // weren't useful at that scale.
  //
  // Per-element ``cy.nodes(...).style({...})`` (inline) is preferred
  // over rebuilding ``cy.style()`` because the stylesheet API appends
  // selector entries on each call, growing unboundedly across zoom
  // events. ``cy.batch()`` coalesces the per-selector style writes into
  // one render. ``requestAnimationFrame`` debounces the burst of zoom
  // events that fire during a smooth wheel scroll. The handler is
  // idempotent and self-applies on mount, so the very first
  // ``cy.layout(...).run()`` (which calls ``fit:true`` and triggers a
  // zoom event) immediately picks up the right scaling.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    let frame = 0;
    const apply = () => {
      frame = 0;
      const c = cyRef.current;
      if (!c) return;
      const inv = 1 / Math.max(1, c.zoom());
      c.batch(() => {
        c.nodes(".pkgnode").style({
          "font-size": 11 * inv,
          padding: `${10 * inv}px`,
          "border-width": 1 * inv,
        });
        c.nodes(".pkgnode.reflective").style({ "border-width": 2 * inv });
        c.nodes(".pkgnode.has-hits").style({ "border-width": 3 * inv });
        c.nodes(".methodnode").style({
          "font-size": 10 * inv,
          padding: `${8 * inv}px`,
          "border-width": 1 * inv,
        });
        c.nodes(".methodnode.focusroot").style({ "border-width": 2 * inv });
        c.nodes(".methodnode.reflective").style({ "border-width": 2 * inv });
        c.nodes(".methodnode.hit").style({ "border-width": 3 * inv });
        c.edges(".edge").style({ width: 1.5 * inv, "font-size": 8 * inv });
        c.edges(".pkgedge").style({ "font-size": 9 * inv });
      });
    };
    const onZoom = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(apply);
    };
    cy.on("zoom", onZoom);
    apply();
    return () => {
      cy.off("zoom", onZoom);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [cgState]);

  // -------- Render Cytoscape elements when data changes --------------------
  // ``hitsByMethod`` is intentionally in the dep array: a fresh hooks
  // poll (every 2.5 s in LabTab's Manual Hooks mode) updates the same Map reference
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

  // Phase 11 sub-step 11.2 — "Trace this method" cross-tab handoff.
  // Reuses the 10.8 ``pendingTraceEntry`` plumbing established for the
  // Inspect → Trace handoff. ``node.smali_id`` is the pre-computed
  // canonical full signature (per ``call_graph.nodes.smali_id``) so
  // the consumer in ``LabTraceMode.tsx`` will recognise it as
  // complete via ``looksLikeCompleteSmaliSignature`` and auto-fire
  // the trace. External nodes are handled defensively here AND in
  // the menu UI (the menu button is rendered ``disabled`` with a
  // tooltip on external nodes — same surface as the spec's intent
  // for the "Open in Inspect" entry, even though the existing entry
  // bails silently rather than visibly disabling).
  const traceMethod = useCallback(
    (node: GraphNode, klass: GraphClass | undefined) => {
      if (!appId || node.is_external) return;
      const cls = klass?.class_name ?? `class#${node.class_id}`;
      setPendingTraceEntry({
        appId,
        entryPrefix: node.smali_id,
        sourceLabel: `Graph → ${cls}.${node.method_name}`,
      });
      setLabMode("trace");
      setTab("lab");
    },
    [appId, setPendingTraceEntry, setLabMode, setTab],
  );

  // -------- Search dropdown wiring -----------------------------------------
  // Recompute hits on every filter or graph change. ``searchGraph`` is
  // pure and bounded (capped at ``SEARCH_HIT_LIMIT`` per section), so
  // re-running it on each keystroke is cheap even on large graphs.
  const searchHits = useMemo(
    () => searchGraph(graph, filter),
    [graph, filter],
  );
  const flatHits = useMemo(() => flattenHits(searchHits), [searchHits]);
  const flatHitsLength = flatHits.length;

  // Browse-mode tree (Package → Class → Method), shown in the dropdown
  // when the filter is empty. Recomputed only when the underlying graph,
  // the App-only-vs-libraries split, or the External toggle change — *not*
  // on every keystroke (the typeahead path doesn't read this).
  const browseTree = useMemo(
    () => buildBrowseTree(graph, appPackage, showExternal),
    [graph, appPackage, showExternal],
  );

  // Open the dropdown the moment the filter goes non-empty *and* the
  // input is focused. We deliberately do NOT auto-close when the filter
  // clears — empty filter switches the dropdown into browse-tree mode
  // (Package → Class → Method), which mirrors Inspect tab's
  // ``ClassMethodTree``. The blur-outside / click-outside / Escape paths
  // close it independently below; the operator picks the close moment.
  useEffect(() => {
    setActiveHitIdx(0);
    if (!filter.trim()) return;
    if (document.activeElement === searchInputRef.current) {
      setSearchOpen(true);
    }
  }, [filter]);

  // Click-outside handler — only attached while the popover is open so
  // we don't pay the document-level listener cost during the typical
  // "operator isn't searching" idle state.
  useEffect(() => {
    if (!searchOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (!searchAnchorRef.current) return;
      if (!searchAnchorRef.current.contains(e.target as Node)) {
        setSearchOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [searchOpen]);

  // Keep the active row scrolled into view when keyboard nav crosses
  // a section break or pushes past the visible viewport.
  useEffect(() => {
    if (!searchOpen) return;
    const list = searchListRef.current;
    if (!list) return;
    const el = list.querySelector<HTMLLIElement>(
      `li[data-hit-idx="${activeHitIdx}"]`,
    );
    if (el) el.scrollIntoView({ block: "nearest" });
  }, [activeHitIdx, searchOpen]);

  // Activate a single hit — same control flow as clicking the
  // corresponding node in the graph: package / class drill into focus
  // mode at their first-method node; method also fires
  // ``onSelectNode`` so the right-pane decompile view opens. Filter is
  // cleared so the focus subgraph isn't immediately filtered down to
  // just the typed substring.
  const activateHit = useCallback(
    (hit: SearchHit) => {
      setSearchOpen(false);
      setFilter("");
      setViewMode("focus");
      setFocusNodeId(hit.firstNodeId);
      setCtxMenu(null);
      if (hit.kind === "method" && hit.node) {
        onSelectNode(toSelected(hit.node, hit.klass));
      }
    },
    [onSelectNode],
  );

  // Keyboard handler bound to the input. ↑/↓ walk active row,
  // Enter activates, Escape closes. Tab is left to the browser so
  // the operator can move focus out of the input without trapping.
  // Escape always closes regardless of mode (typeahead vs. browse tree)
  // so the operator has one consistent dismissal key.
  const onSearchKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        if (searchOpen) {
          setSearchOpen(false);
          e.preventDefault();
        }
        return;
      }
      if (!searchOpen || flatHitsLength === 0) {
        if (e.key === "ArrowDown" && filter.trim()) {
          // Re-open if user pressed down after a programmatic close.
          setSearchOpen(true);
          e.preventDefault();
        }
        return;
      }
      if (e.key === "ArrowDown") {
        setActiveHitIdx((i) => (i + 1) % flatHitsLength);
        e.preventDefault();
      } else if (e.key === "ArrowUp") {
        setActiveHitIdx((i) => (i - 1 + flatHitsLength) % flatHitsLength);
        e.preventDefault();
      } else if (e.key === "Enter") {
        const hit = flatHits[activeHitIdx];
        if (hit) {
          activateHit(hit);
          e.preventDefault();
        }
      }
    },
    [searchOpen, flatHits, flatHitsLength, activeHitIdx, filter, activateHit],
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
        <div ref={searchAnchorRef} style={searchAnchorStyle}>
          <input
            ref={searchInputRef}
            className="callgraph-filter"
            type="text"
            placeholder="Filter package, class, or method…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            onFocus={() => {
              if (filter.trim() && flatHitsLength > 0) setSearchOpen(true);
            }}
            onKeyDown={onSearchKeyDown}
            spellCheck={false}
            style={searchInputStyle}
            disabled={cgState !== "ready"}
            role="combobox"
            aria-expanded={searchOpen}
            aria-controls="callgraph-search-listbox"
            aria-autocomplete="list"
            aria-activedescendant={
              searchOpen && flatHitsLength > 0
                ? `callgraph-search-hit-${activeHitIdx}`
                : undefined
            }
          />
          <button
            type="button"
            className="callgraph-btn"
            style={searchToggleBtnStyle}
            onClick={() => {
              if (searchOpen) {
                setSearchOpen(false);
              } else {
                // Open in either typeahead (filter set) or browse-tree
                // (filter empty) mode. Either way, focus the input so the
                // operator can immediately start typing to refine.
                setSearchOpen(true);
                searchInputRef.current?.focus();
              }
            }}
            disabled={cgState !== "ready"}
            title={
              searchOpen
                ? "Hide list"
                : filter.trim()
                  ? "Show suggestions"
                  : "Browse packages, classes & methods"
            }
            aria-label={
              searchOpen
                ? "Hide list"
                : filter.trim()
                  ? "Show suggestions"
                  : "Browse packages, classes & methods"
            }
          >
            {searchOpen ? "▲" : "▼"}
          </button>
          {searchOpen && filter.trim() && flatHitsLength > 0 && (
            <SearchDropdown
              hits={searchHits}
              activeIdx={activeHitIdx}
              onHover={setActiveHitIdx}
              onActivate={activateHit}
              listRef={searchListRef}
            />
          )}
          {searchOpen && filter.trim() && flatHitsLength === 0 && graph && (
            <div style={searchEmptyStyle}>
              No matches in loaded graph data
              {graph.total_nodes > graph.nodes.length
                ? ` (truncated to ${graph.nodes.length} of ${graph.total_nodes} — try toggling "App only")`
                : ""}
            </div>
          )}
          {searchOpen && !filter.trim() && (
            <BrowseTreeDropdown
              tree={browseTree}
              graphLoaded={graph != null}
              openPkgs={openPkgs}
              setOpenPkgs={setOpenPkgs}
              openCls={openCls}
              setOpenCls={setOpenCls}
              showApp={showAppSection}
              setShowApp={setShowAppSection}
              showLib={showLibSection}
              setShowLib={setShowLibSection}
              onActivateMethod={(node, klass) => {
                // Reuse the typeahead activation path so a method click
                // here behaves identically to picking it from the search
                // results (focus mode + decompile-pane select + close).
                activateHit({
                  kind: "method",
                  label: `${klass.simple_name || klass.class_name}.${node.method_name}`,
                  secondary: klass.package || "(default)",
                  score: 0,
                  firstNodeId: node.id,
                  node,
                  klass,
                });
              }}
              listRef={searchListRef}
            />
          )}
        </div>
        <label style={toggleLabelStyle}>
          <input
            type="checkbox"
            checked={showExternal}
            onChange={(e) => setShowExternal(e.target.checked)}
            disabled={cgState !== "ready"}
          />{" "}
          External
        </label>
        <label
          style={toggleLabelStyle}
          title={
            appPackage
              ? `Hide bundled libraries (Material/AndroidX/Kotlin/etc.) — show only packages under ${appPackagePrefix(appPackage)}`
              : "Dossier has no package — load a project before toggling"
          }
        >
          <input
            type="checkbox"
            checked={appOnly}
            onChange={(e) => {
              userTouchedAppOnly.current = true;
              setAppOnly(e.target.checked);
            }}
            disabled={cgState !== "ready" || !appPackage}
          />{" "}
          App only
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
            onTraceMethod={() =>
              traceMethod(ctxMenu.node, ctxMenu.klass)
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

// ---------------------------------------------------------------------------
// Toolbar search dropdown — typeahead over loaded graph data
// ---------------------------------------------------------------------------

/** One suggestion row in the dropdown. ``firstNodeId`` is the click
 *  target — drilling into focus mode rooted there. ``node`` / ``klass``
 *  are populated for ``method`` rows so ``onSelectNode`` can fire. */
export type SearchHit = {
  kind: "package" | "class" | "method";
  /** Display label shown to the operator (the package name, class name,
   *  or method name in context like ``MainActivity.onCreate``). */
  label: string;
  /** Secondary line shown muted under the label (e.g. ``5 classes ·
   *  139 methods`` for a package, ``com.example.weakbank`` for a class
   *  or method). */
  secondary: string;
  /** Score for ranking — lower is better. ``0`` exact, ``100``
   *  prefix, ``200`` substring; ties broken by match position. */
  score: number;
  firstNodeId: number;
  /** Method-row only — used by ``onSelectNode`` to populate the
   *  right-pane decompile view; class / package rows leave it null
   *  and rely on ``firstNodeId`` for the drill. */
  node?: GraphNode;
  klass?: GraphClass;
};

export type SearchHitGroups = {
  packages: SearchHit[];
  classes: SearchHit[];
  methods: SearchHit[];
};

/** Cap per section. The dropdown shows three sections; each is sorted
 *  by score then alphabetically and truncated to this many entries.
 *  25 is a balance between "covers most relevant matches" and "fits
 *  on one screen without internal scrolling at typical viewport
 *  heights". */
const SEARCH_HIT_LIMIT = 25;

function scoreMatch(haystack: string, needle: string): number | null {
  if (!haystack) return null;
  const h = haystack.toLowerCase();
  if (h === needle) return 0;
  if (h.startsWith(needle)) return 100;
  const idx = h.indexOf(needle);
  if (idx < 0) return null;
  // Substring rank: 200 base + match offset, so earlier matches beat
  // later ones, and shorter haystacks tiebreak below in the comparator.
  return 200 + idx;
}

function compareHits(a: SearchHit, b: SearchHit): number {
  if (a.score !== b.score) return a.score - b.score;
  if (a.label.length !== b.label.length) return a.label.length - b.label.length;
  return a.label.localeCompare(b.label);
}

/** Pure-function ranker — pulls suggestion candidates out of the
 *  already-loaded ``graph`` data (the same dataset the package
 *  overview renders from). Empty / whitespace-only queries return
 *  three empty arrays (the dropdown closes itself in that case).
 *
 *  Three sections in priority order:
 *    1. Packages — matched on ``cls.package``
 *    2. Classes  — matched on the simple name OR the dotted FQN
 *    3. Methods  — matched on the bare ``method_name`` OR the
 *       compound ``ClassName.method_name`` (so typing
 *       ``MainActivity.onCreate`` finds the right one across
 *       overloads in different classes)
 *
 *  External nodes are skipped (the operator's filter input was for
 *  navigating in-app code; the existing **External** toggle covers
 *  rendering them). The ``classes`` array is iterated once to build
 *  the package + class hits and a class-id → first-node-id map so
 *  the method loop can reuse it. */
export function searchGraph(
  graph: GraphListResponse | null,
  query: string,
  limit: number = SEARCH_HIT_LIMIT,
): SearchHitGroups {
  const empty: SearchHitGroups = { packages: [], classes: [], methods: [] };
  if (!graph) return empty;
  const q = query.trim().toLowerCase();
  if (!q) return empty;

  // First pass: aggregate per-package + per-class metadata so package
  // and class rows render with operator-useful counts and so we can
  // resolve "click drills into focus mode at the first method node".
  const classMap = new Map<number, GraphClass>();
  for (const c of graph.classes) classMap.set(c.id, c);
  const firstNodeByClass = new Map<number, number>();
  const firstNodeByPkg = new Map<string, number>();
  const pkgClasses = new Map<string, Set<number>>();
  const pkgMethods = new Map<string, number>();
  for (const n of graph.nodes) {
    if (n.is_external) continue;
    const cls = classMap.get(n.class_id);
    if (!cls || cls.is_external) continue;
    if (!firstNodeByClass.has(cls.id)) firstNodeByClass.set(cls.id, n.id);
    const pkg = cls.package || "(default)";
    if (!firstNodeByPkg.has(pkg)) firstNodeByPkg.set(pkg, n.id);
    let cs = pkgClasses.get(pkg);
    if (!cs) {
      cs = new Set();
      pkgClasses.set(pkg, cs);
    }
    cs.add(cls.id);
    pkgMethods.set(pkg, (pkgMethods.get(pkg) ?? 0) + 1);
  }

  // Package hits — one row per unique package whose name matches.
  const packageHits: SearchHit[] = [];
  for (const [pkg, firstNodeId] of firstNodeByPkg) {
    const score = scoreMatch(pkg, q);
    if (score == null) continue;
    const ncls = pkgClasses.get(pkg)?.size ?? 0;
    const nmet = pkgMethods.get(pkg) ?? 0;
    packageHits.push({
      kind: "package",
      label: pkg,
      secondary: `${ncls} class${ncls === 1 ? "" : "es"} · ${nmet} method${nmet === 1 ? "" : "s"}`,
      score,
      firstNodeId,
    });
  }
  packageHits.sort(compareHits);

  // Class hits — matched on simple name OR dotted FQN. We score against
  // both and keep the better of the two so typing ``MainActivity``
  // catches both ``com.example.weakbank.MainActivity`` (FQN substring
  // 200+offset) and the bare simple-name (prefix=100), with the bare
  // form winning the tiebreak.
  const classHits: SearchHit[] = [];
  for (const cls of graph.classes) {
    if (cls.is_external) continue;
    const firstNodeId = firstNodeByClass.get(cls.id);
    if (firstNodeId == null) continue;
    const sSimple = scoreMatch(cls.simple_name, q);
    const sFqn = scoreMatch(cls.class_name, q);
    const score =
      sSimple == null ? sFqn : sFqn == null ? sSimple : Math.min(sSimple, sFqn);
    if (score == null) continue;
    classHits.push({
      kind: "class",
      label: cls.simple_name || cls.class_name,
      secondary: cls.package || "(default)",
      score,
      firstNodeId,
    });
  }
  classHits.sort(compareHits);

  // Method hits — matched on bare ``method_name`` OR
  // ``Class.method_name``. The compound form lets ``MainActivity.on``
  // narrow to the activity's lifecycle methods even when ``on`` is a
  // common substring across the whole graph.
  const methodHits: SearchHit[] = [];
  for (const n of graph.nodes) {
    if (n.is_external) continue;
    const cls = classMap.get(n.class_id);
    if (!cls || cls.is_external) continue;
    const compound = `${cls.simple_name}.${n.method_name}`;
    const sBare = scoreMatch(n.method_name, q);
    const sCompound = scoreMatch(compound, q);
    const score =
      sBare == null
        ? sCompound
        : sCompound == null
          ? sBare
          : Math.min(sBare, sCompound);
    if (score == null) continue;
    methodHits.push({
      kind: "method",
      label: compound,
      secondary: cls.package || "(default)",
      score,
      firstNodeId: n.id,
      node: n,
      klass: cls,
    });
  }
  methodHits.sort(compareHits);

  return {
    packages: packageHits.slice(0, limit),
    classes: classHits.slice(0, limit),
    methods: methodHits.slice(0, limit),
  };
}

/** Flattened ordering of all hits across the three sections — drives
 *  the keyboard-nav active-index walk. Packages first, then classes,
 *  then methods (matches visual order in the popover). */
export function flattenHits(g: SearchHitGroups): SearchHit[] {
  return [...g.packages, ...g.classes, ...g.methods];
}

// ---------------------------------------------------------------------------
// Browse-tree dropdown — Package → Class → Method hierarchy
// ---------------------------------------------------------------------------
//
// Shown in the toolbar dropdown when the filter input is empty (i.e. the
// operator clicked the chevron without typing). Mirrors Inspect tab's
// ``ClassMethodTree`` shape so the two surfaces feel like one tool: a
// top-level App vs. Library split (using the shared ``isAppPackage``
// heuristic), each section collapsible, packages collapsible, classes
// collapsible. Methods are leaves — clicking one drills the call graph
// into focus mode at that node and seeds the decompile pane (same control
// flow as activating a typeahead method hit).
//
// Why a tree here at all? When the call-graph package overview is dense
// (or in the user-reported single-package case) the operator has no way
// to discover *which* methods exist without first focusing on a package.
// The tree gives a flat browseable index of every loaded method without
// requiring the operator to know its name in advance.
//
// External / library nodes are filtered the same way as the package
// overview: the ``showExternal`` toggle gates ``is_external`` nodes;
// the ``appOnly`` toggle gates the backend ``package_prefix`` filter
// (which already drops library packages before we see them). We pass
// ``showExternal`` through so a future change to the toggle's semantics
// doesn't desync the tree from the rest of the pane.
// ---------------------------------------------------------------------------

export type BrowseTreePackage = {
  name: string;
  /** First method node in the package — drilled-into when the operator
   *  picks a package row directly. Currently unused (clicks expand
   *  rather than drill) but exposed so the row can grow a "focus here"
   *  affordance later without re-walking the data. */
  firstNodeId: number;
  classes: BrowseTreeClass[];
};

export type BrowseTreeClass = {
  classId: number;
  fqn: string;
  simpleName: string;
  pkg: GraphClass;
  firstNodeId: number;
  methods: BrowseTreeMethod[];
};

export type BrowseTreeMethod = {
  node: GraphNode;
  klass: GraphClass;
};

export type BrowseTree = {
  app: BrowseTreePackage[];
  lib: BrowseTreePackage[];
};

export function buildBrowseTree(
  graph: GraphListResponse | null,
  appPackage: string | null,
  showExternal: boolean,
): BrowseTree {
  const empty: BrowseTree = { app: [], lib: [] };
  if (!graph) return empty;
  const classMap = buildClassMap(graph.classes);

  // First pass: bucket nodes by class id so each class row has its
  // (sorted) method list. External nodes / classes obey the same toggle
  // the package overview uses.
  const nodesByClass = new Map<number, GraphNode[]>();
  for (const n of graph.nodes) {
    if (!showExternal && n.is_external) continue;
    const cls = classMap.get(n.class_id);
    if (!cls) continue;
    if (!showExternal && cls.is_external) continue;
    let arr = nodesByClass.get(n.class_id);
    if (!arr) {
      arr = [];
      nodesByClass.set(n.class_id, arr);
    }
    arr.push(n);
  }

  // Second pass: bucket classes by package, retaining only classes with
  // at least one node we'd render. Empty packages are silently dropped.
  const classesByPkg = new Map<string, GraphClass[]>();
  for (const cls of graph.classes) {
    if (!nodesByClass.has(cls.id)) continue;
    if (!showExternal && cls.is_external) continue;
    const pkg = cls.package || "(default)";
    let arr = classesByPkg.get(pkg);
    if (!arr) {
      arr = [];
      classesByPkg.set(pkg, arr);
    }
    arr.push(cls);
  }

  const allPkgs: BrowseTreePackage[] = [];
  for (const [pkgName, classes] of classesByPkg) {
    classes.sort((a, b) =>
      (a.simple_name || a.class_name).localeCompare(
        b.simple_name || b.class_name,
      ),
    );
    const treeClasses: BrowseTreeClass[] = [];
    for (const cls of classes) {
      const nodes = (nodesByClass.get(cls.id) ?? []).slice();
      nodes.sort((a, b) => a.method_name.localeCompare(b.method_name));
      if (nodes.length === 0) continue;
      treeClasses.push({
        classId: cls.id,
        fqn: cls.class_name,
        simpleName: cls.simple_name || cls.class_name,
        pkg: cls,
        firstNodeId: nodes[0].id,
        methods: nodes.map((node) => ({ node, klass: cls })),
      });
    }
    if (treeClasses.length === 0) continue;
    allPkgs.push({
      name: pkgName,
      firstNodeId: treeClasses[0].firstNodeId,
      classes: treeClasses,
    });
  }
  allPkgs.sort((a, b) => a.name.localeCompare(b.name));

  // App vs. Library split, identical heuristic to ClassMethodTree so the
  // two surfaces agree on what counts as the operator's own code.
  const app: BrowseTreePackage[] = [];
  const lib: BrowseTreePackage[] = [];
  for (const p of allPkgs) {
    (isAppPackage(p.name, appPackage) ? app : lib).push(p);
  }
  return { app, lib };
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

// -------- Toolbar search dropdown popover --------------------------------

/** Popover anchored under the filter input. Renders three sections
 *  (Packages / Classes / Methods) with section headers + per-row
 *  hover & active-row highlight. The active row tracking is owned by
 *  the parent so keyboard nav (↑/↓ on the input) and mouse hover
 *  share one source of truth.
 *
 *  ``data-hit-idx`` mirrors the flat index produced by ``flattenHits``
 *  so the parent's "scroll active into view" effect can find the
 *  right ``<li>`` cheaply. */
function SearchDropdown(props: {
  hits: SearchHitGroups;
  activeIdx: number;
  onHover: (idx: number) => void;
  onActivate: (hit: SearchHit) => void;
  listRef: RefObject<HTMLUListElement>;
}) {
  const { hits, activeIdx, onHover, onActivate, listRef } = props;
  const sections: Array<{
    label: string;
    rows: SearchHit[];
    base: number;
  }> = [
    { label: "Packages", rows: hits.packages, base: 0 },
    {
      label: "Classes",
      rows: hits.classes,
      base: hits.packages.length,
    },
    {
      label: "Methods",
      rows: hits.methods,
      base: hits.packages.length + hits.classes.length,
    },
  ];
  return (
    <ul
      ref={listRef}
      id="callgraph-search-listbox"
      role="listbox"
      style={searchDropdownStyle}
    >
      {sections.map((sec) => {
        if (sec.rows.length === 0) return null;
        return (
          <li key={sec.label} style={{ listStyle: "none" }}>
            <div style={searchSectionHeaderStyle}>
              {sec.label}{" "}
              <span className="muted small">({sec.rows.length})</span>
            </div>
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {sec.rows.map((hit, i) => {
                const idx = sec.base + i;
                const active = idx === activeIdx;
                return (
                  <li
                    key={`${hit.kind}-${hit.firstNodeId}-${i}`}
                    id={`callgraph-search-hit-${idx}`}
                    role="option"
                    aria-selected={active}
                    data-hit-idx={idx}
                    style={
                      active
                        ? { ...searchRowStyle, ...searchRowActiveStyle }
                        : searchRowStyle
                    }
                    onMouseEnter={() => onHover(idx)}
                    onMouseDown={(e) => {
                      // Use mousedown not click so the input doesn't blur
                      // before we get a chance to handle the activation.
                      e.preventDefault();
                      onActivate(hit);
                    }}
                  >
                    <div style={searchRowLabelStyle}>{hit.label}</div>
                    {hit.secondary && (
                      <div className="muted small" style={searchRowSecondaryStyle}>
                        {hit.secondary}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </li>
        );
      })}
      <li style={searchHintRowStyle}>
        <span className="muted small">↑↓ navigate · ↵ open · esc close</span>
      </li>
    </ul>
  );
}

/** Browse-tree dropdown — mirrors Inspect tab's ``ClassMethodTree``
 *  visually (App / Library top-level sections + per-package + per-class
 *  expand) but renders inside the call-graph search dropdown popover.
 *  Method clicks are the only "commit" action; package and class clicks
 *  toggle expansion so the operator can drill down without committing.
 *
 *  Reuses the global ``tree-*`` CSS classes so styling stays in sync
 *  with Inspect tab — change one and both panes update. */
function BrowseTreeDropdown(props: {
  tree: BrowseTree;
  graphLoaded: boolean;
  openPkgs: Record<string, boolean>;
  setOpenPkgs: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  openCls: Record<string, boolean>;
  setOpenCls: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  showApp: boolean;
  setShowApp: React.Dispatch<React.SetStateAction<boolean>>;
  showLib: boolean;
  setShowLib: React.Dispatch<React.SetStateAction<boolean>>;
  onActivateMethod: (node: GraphNode, klass: GraphClass) => void;
  listRef: RefObject<HTMLUListElement>;
}) {
  const {
    tree,
    graphLoaded,
    openPkgs,
    setOpenPkgs,
    openCls,
    setOpenCls,
    showApp,
    setShowApp,
    showLib,
    setShowLib,
    onActivateMethod,
    listRef,
  } = props;
  const isEmpty = tree.app.length === 0 && tree.lib.length === 0;

  if (!graphLoaded) {
    return (
      <div style={searchEmptyStyle}>Loading graph data…</div>
    );
  }
  if (isEmpty) {
    return (
      <div style={searchEmptyStyle}>
        No methods loaded yet. Try toggling "App only" or "External" to
        broaden the package filter.
      </div>
    );
  }

  const renderPackages = (pkgs: BrowseTreePackage[]) =>
    pkgs.map((p) => {
      const pkgKey = p.name;
      const isPkgOpen = openPkgs[pkgKey] ?? false;
      return (
        <li key={pkgKey} className="tree-pkg">
          <button
            type="button"
            className="tree-toggle"
            onClick={() =>
              setOpenPkgs((s) => ({ ...s, [pkgKey]: !isPkgOpen }))
            }
            title={p.name}
          >
            <span className="tree-caret">{isPkgOpen ? "▾" : "▸"}</span>
            <span className="tree-name">{p.name}</span>
            <span className="muted small"> ({p.classes.length})</span>
          </button>
          {isPkgOpen && (
            <ul className="tree-classes">
              {p.classes.map((c) => {
                const clsKey = `${pkgKey}.${c.simpleName}#${c.classId}`;
                const isClsOpen = openCls[clsKey] ?? false;
                return (
                  <li key={clsKey} className="tree-class">
                    <button
                      type="button"
                      className="tree-toggle"
                      onClick={() =>
                        setOpenCls((s) => ({ ...s, [clsKey]: !isClsOpen }))
                      }
                      title={c.fqn}
                    >
                      <span className="tree-caret">
                        {c.methods.length ? (isClsOpen ? "▾" : "▸") : " "}
                      </span>
                      <span className="tree-name">{c.simpleName}</span>
                      {c.methods.length > 0 && (
                        <span className="muted small">
                          {" "}
                          ({c.methods.length})
                        </span>
                      )}
                    </button>
                    {isClsOpen && c.methods.length > 0 && (
                      <ul className="tree-methods">
                        {c.methods.map((m) => (
                          <li key={`${m.node.id}`}>
                            <button
                              type="button"
                              className="tree-method"
                              // Use mousedown so the input doesn't blur
                              // before activation runs (matches the
                              // typeahead row pattern above).
                              onMouseDown={(e) => {
                                e.preventDefault();
                                onActivateMethod(m.node, m.klass);
                              }}
                              title={`${c.fqn}.${m.node.method_name}`}
                            >
                              {m.node.method_name}()
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </li>
      );
    });

  return (
    <ul
      ref={listRef}
      id="callgraph-search-listbox"
      role="listbox"
      style={browseTreeDropdownStyle}
    >
      <li style={{ listStyle: "none" }}>
        <button
          type="button"
          className="tree-section-head-btn"
          onClick={() => setShowApp((v) => !v)}
          aria-expanded={showApp}
        >
          <span className="tree-caret">{showApp ? "▾" : "▸"}</span>
          <span className="tree-section-title">App packages</span>
          <span className="muted small tree-section-count">
            {tree.app.length}
          </span>
        </button>
        {showApp &&
          (tree.app.length === 0 ? (
            <p className="muted small tree-empty">
              No app-owned packages — toggle "App only" off to see bundled
              libraries.
            </p>
          ) : (
            <ul className="tree-root">{renderPackages(tree.app)}</ul>
          ))}
      </li>
      <li style={{ listStyle: "none" }}>
        <button
          type="button"
          className="tree-section-head-btn"
          onClick={() => setShowLib((v) => !v)}
          aria-expanded={showLib}
        >
          <span className="tree-caret">{showLib ? "▾" : "▸"}</span>
          <span className="tree-section-title">
            Android / library packages
          </span>
          <span className="muted small tree-section-count">
            {tree.lib.length}
          </span>
        </button>
        {showLib &&
          (tree.lib.length === 0 ? (
            <p className="muted small tree-empty">No library packages.</p>
          ) : (
            <ul className="tree-root">{renderPackages(tree.lib)}</ul>
          ))}
      </li>
      <li style={searchHintRowStyle}>
        <span className="muted small">click a method to focus · esc close</span>
      </li>
    </ul>
  );
}

const browseTreeDropdownStyle: CSSProperties = {
  position: "absolute",
  top: "calc(100% + 4px)",
  left: 0,
  right: 0,
  maxHeight: "60vh",
  overflowY: "auto",
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "0.25rem 0",
  margin: 0,
  fontSize: "0.78rem",
  zIndex: 30,
  boxShadow: "0 4px 12px rgba(0, 0, 0, 0.3)",
  // Constrain width — when the toolbar is wide the input can stretch
  // past where a useful hierarchy is readable, so we cap at a sensible
  // browse width while still anchoring to the input.
  minWidth: "20em",
};

const searchDropdownStyle: CSSProperties = {
  position: "absolute",
  top: "calc(100% + 4px)",
  left: 0,
  right: 0,
  maxHeight: "60vh",
  overflowY: "auto",
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "0.25rem 0",
  margin: 0,
  fontSize: "0.78rem",
  zIndex: 30,
  boxShadow: "0 4px 12px rgba(0, 0, 0, 0.3)",
};

const searchSectionHeaderStyle: CSSProperties = {
  padding: "0.3rem 0.6rem 0.15rem 0.6rem",
  fontSize: "0.7rem",
  textTransform: "uppercase",
  letterSpacing: "0.04em",
  color: "var(--muted)",
  borderTop: "1px solid var(--border)",
};

const searchRowStyle: CSSProperties = {
  padding: "0.3rem 0.6rem",
  cursor: "pointer",
  borderLeft: "2px solid transparent",
};

const searchRowActiveStyle: CSSProperties = {
  background: "var(--panel-3, rgba(56, 139, 253, 0.15))",
  borderLeftColor: "var(--accent, #58a6ff)",
};

const searchRowLabelStyle: CSSProperties = {
  color: "var(--text)",
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

const searchRowSecondaryStyle: CSSProperties = {
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  marginTop: "0.05rem",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

const searchHintRowStyle: CSSProperties = {
  padding: "0.3rem 0.6rem 0.15rem 0.6rem",
  borderTop: "1px solid var(--border)",
  textAlign: "right",
  listStyle: "none",
};

// -------- Right-click context menu ---------------------------------------

function ContextMenuBox(props: {
  menu: ContextMenu;
  onClose: () => void;
  onFocus: (hops: number) => void;
  onOpenInInspect: () => void;
  onTraceMethod: () => void;
}) {
  const { menu, onClose, onFocus, onOpenInInspect, onTraceMethod } = props;
  const [hops, setHops] = useState(DEFAULT_HOPS);
  const cls = menu.klass?.class_name ?? "?";
  // External nodes have no source available — the trace skill needs
  // the smali body to slice predicates, so disable the menu item with
  // an operator-readable tooltip explaining why.
  const isExternal = menu.node.is_external;
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
        <button
          type="button"
          className={`callgraph-btn${isExternal ? " cy-context-menu-item-disabled" : ""}`}
          disabled={isExternal}
          onClick={() => {
            onTraceMethod();
            onClose();
          }}
          title={isExternal
            ? "External callee — no source available to trace"
            : "Open in Trace mode and run the trace_behavior skill on this method"}
        >
          Trace this method
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

// Wrapper around the search input + dropdown — needs ``position:
// relative`` so the popover anchors to it. ``flex: 1 1 12em`` keeps
// the same width behaviour the bare ``filterStyle`` had before the
// dropdown was introduced.
const searchAnchorStyle: CSSProperties = {
  position: "relative",
  display: "flex",
  alignItems: "stretch",
  flex: "1 1 12em",
  minWidth: "8em",
};

const searchInputStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  background: "var(--bg)",
  border: "1px solid var(--border)",
  borderRight: "none",
  color: "var(--text)",
  borderRadius: "4px 0 0 4px",
  padding: "0.25rem 0.5rem",
  fontSize: "0.8rem",
};

// Right-edge chevron button. Visually attached to the input via
// ``border-radius: 0 4px 4px 0`` and a single shared border line.
const searchToggleBtnStyle: CSSProperties = {
  flex: "0 0 auto",
  borderRadius: "0 4px 4px 0",
  padding: "0 0.55rem",
  fontSize: "0.7rem",
  lineHeight: 1,
};

// Empty-state pill rendered below the input when the operator's
// query genuinely matches nothing in the loaded graph data — flags
// the truncation case explicitly so they know to flip "App only"
// when they're hunting for app code that fell off the 5000-node page.
const searchEmptyStyle: CSSProperties = {
  position: "absolute",
  top: "calc(100% + 4px)",
  left: 0,
  right: 0,
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "0.4rem 0.6rem",
  fontSize: "0.78rem",
  color: "var(--muted)",
  zIndex: 30,
  boxShadow: "0 4px 12px rgba(0, 0, 0, 0.3)",
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
