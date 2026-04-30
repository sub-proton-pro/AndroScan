import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  ImperativePanelGroupHandle,
  ImperativePanelHandle,
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";
import { ChatDock } from "../components/ChatDock";
import {
  CallGraphView,
  hitKey,
  type AnchoredMethodMeta,
  type SelectedNode,
} from "../components/CallGraphView";
import { CodeView } from "../components/CodeView";
import { FridaSessionsList } from "../components/FridaSessionsList";
import { FridaTracePanel } from "../components/FridaTracePanel";
import { HookBuilder } from "../components/HookBuilder";
import { HookStatsPanel } from "../components/HookStatsPanel";
import { IconChevronDown, IconChevronLeft, IconChevronUp } from "../components/Icons";
import { ScopeInspectorPanel } from "../components/ScopeInspectorPanel";
import { fetchSource } from "../api/code";
import {
  getSessionEvents,
  getSessionHooks,
  type CreateSessionResult,
  type FridaSessionInfo,
  type HookStat,
  type TraceEvent,
} from "../api/frida";
import type { ChatAttachment } from "../types";
import { useWorkbench, type LabMode } from "../context/WorkbenchContext";
import { LabTraceMode } from "./LabTraceMode";
import { listAnchoredMethods, type AnchoredMethod, type BehaviorAnchor } from "../api/trace";

/**
 * Lab tab (formerly "Hook Lab"; renamed in Phase 10 sub-step 10.6).
 *
 * Hosts three modes selectable via a thin left-edge rail:
 *
 *   * **Trace** (default, NEW) — UI element ➜ decision-point timeline
 *     ➜ bypass plans. Placeholder for 10.6 (pinned to ``LabTraceMode``);
 *     full ``BehaviorAnchorCard`` / ``DecisionTimeline`` / ``BypassPlanCard``
 *     UI lands in 10.7.
 *   * **Manual Hooks** — the legacy Hook Lab 3-column layout
 *     (CallGraph | CodeView+HookBuilder+Chat | Sessions+Trace/Hooks/Scope).
 *     Unchanged from the pre-10.6 surface; operators with established
 *     muscle memory get the same flow they had.
 *   * **Graph** — dedicated full-pane CallGraphView. Operators who want
 *     to deep-dive the call graph without the 32% width constraint of
 *     Manual Hooks mode pop into here.
 *
 * Mode selection persists in ``localStorage["androscan.lab.mode"]`` so
 * the operator's last choice survives reloads (state owned by
 * ``WorkbenchContext`` since 10.7 — see ``LabMode`` / ``setLabMode``
 * on the context). ``Trace`` is the cold-start default — that signals
 * the new feature without forcing operators who prefer the legacy
 * flow to re-pick every session.
 *
 * Cross-component wiring inside Manual Hooks mode (DEC-023, sub-steps
 * 4.5–4.8, unchanged from the original Hook Lab tab):
 *
 *   * Selecting a method in the call graph emits ``SelectedNode``;
 *     we (a) load its decompiled source into the inline CodeView,
 *     and (b) prefill the HookBuilder's ``class_name`` / ``method_name``
 *     params so the operator can Inject without retyping them.
 *   * Successful Inject creates a session; we capture its id +
 *     ``persist_path`` and pin the right pane's trace to it.
 *   * Picking a different session in the SessionsList swaps the
 *     trace / hooks / scope panes to that session's data.
 *   * **Frida overlay on the call graph (sub-step 4.8):** the same
 *     ``chatHooks`` polled for the chat ``frida_summary`` attachment
 *     is reduced into a ``hitsByMethod`` map and threaded into
 *     ``<CallGraphView />`` so methods that fired in the active
 *     session render in bold cyan (DEC-023) with their hit count in
 *     the label / tooltip; everything else dims. No new poll cadence —
 *     the chat-attachment loop already runs every 2.5s, so the
 *     overlay refreshes cohesively with hooks/scope/chat context.
 *
 * Right-pane bottom slot is a tab strip (sub-step 4.6): default
 * "Trace" preserves 4.5's behaviour; "Hooks" surfaces the per-
 * (class, method) hit counts + top return values; "Scope" shows
 * captured ``this_fields`` snapshots from the ``scope_inspector``
 * template. All three panels share the same ``activeSession``.
 */
type RightPaneTab = "trace" | "hooks" | "scope";

// ``LabMode`` itself + the localStorage round-trip lifted into
// ``WorkbenchContext`` in 10.7 so cross-tab actions (the Mirror →
// Trace integration in 10.8) and intra-tab actions (BypassPlanCard's
// "Stage in Manual Hooks" button) can flip the mode without drilling
// imperative refs through the LabTab tree. Re-exported here for the
// few legacy importers that already named ``LabTab.LabMode``.
export type { LabMode };

// Last-N tail of trace events folded into the chat ``frida_summary``
// attachment. Kept small so the per-kind ATTACHMENT_BUDGETS["frida_summary"]
// (4_000 chars on the backend) doesn't have to truncate aggressively.
const CHAT_TRACE_TAIL_LIMIT = 30;
// Poll cadence for the chat-attachment data. Aligned with ScopeInspectorPanel /
// HookStatsPanel (both 2.5s) so a refresh fans out cohesively. The chat dock
// re-renders on the next state tick; the model only sees the snapshot at
// ``Send`` time.
const CHAT_ATTACH_POLL_MS = 2500;

// Cap for the ``code`` attachment carrying the decompiled file. Mirrors the
// soft budget on the backend (ATTACHMENT_BUDGETS["code"] == 6_000); we trim
// here so the operator's "show context" preview matches what the model sees.
const CHAT_CODE_BUDGET = 6_000;

const LAB_MODES: { id: LabMode; label: string; hint: string }[] = [
  { id: "trace", label: "Trace", hint: "UI → decision points → bypass plans (NEW)" },
  { id: "manual-hooks", label: "Manual Hooks", hint: "Legacy Frida hook builder + sessions" },
  { id: "graph", label: "Graph", hint: "Dedicated call-graph explorer" },
];

export function LabTab() {
  const { appId, labMode, setLabMode } = useWorkbench();

  // 10.8: track the active ``BehaviorAnchor`` from Trace mode so the
  // Manual Hooks chat dock can fold it into a ``trace`` ``ChatAttachment``
  // when the operator hops between modes inside a single anchor's
  // investigation. Cleared on app change (handled inside ``LabTraceMode``)
  // and on mode hop away from Trace + Manual Hooks; ``GraphMode`` keeps
  // the value untouched so a quick Graph-mode side-trip doesn't lose
  // the chat context the operator just built.
  const [activeAnchor, setActiveAnchor] = useState<BehaviorAnchor | null>(null);
  // ``LabTraceMode`` calls ``onActiveAnchorChange`` on a stable callback
  // identity to avoid re-firing the effect on every state tick.
  const handleActiveAnchorChange = useCallback(
    (anchor: BehaviorAnchor | null) => setActiveAnchor(anchor),
    [],
  );
  // Reset the cross-mode anchor reference on app change so a second
  // project's chat dock never inherits the first one's trace context.
  useEffect(() => {
    setActiveAnchor(null);
  }, [appId]);

  return (
    <div className="lab-tab-shell">
      <nav className="lab-mode-rail" role="tablist" aria-label="Lab mode">
        {LAB_MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            role="tab"
            aria-selected={labMode === m.id}
            className={`lab-mode-button ${labMode === m.id ? "lab-mode-button-active" : ""}`}
            onClick={() => setLabMode(m.id)}
            title={m.hint}
          >
            {m.label}
          </button>
        ))}
      </nav>
      <div className="lab-mode-content">
        {labMode === "trace" && (
          <LabTraceMode
            appId={appId}
            onActiveAnchorChange={handleActiveAnchorChange}
          />
        )}
        {labMode === "manual-hooks" && (
          <ManualHooksMode activeAnchor={activeAnchor} />
        )}
        {labMode === "graph" && <GraphMode />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Graph mode — dedicated full-pane CallGraphView. Same component the Manual
// Hooks mode uses on the left, but here it gets the full tab width so
// operators can deep-dive without the 32% column constraint.
// ---------------------------------------------------------------------------

function GraphMode() {
  const { appId, dossier } = useWorkbench();
  const [, setSelected] = useState<SelectedNode | null>(null);
  const defaultPackage =
    typeof dossier?.apk_info?.package === "string" ? dossier.apk_info.package : null;
  return (
    <div className="lab-graph-mode">
      <CallGraphView
        appId={appId}
        onSelectNode={setSelected}
        appPackage={defaultPackage}
        hitsByMethod={null}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Manual Hooks mode — the legacy Hook Lab 3-column layout, untouched from the
// pre-10.6 surface. Lifted into its own component so the mode switch is a
// clean conditional render (avoids re-running ManualHooksMode's effects on
// every mode hop).
// ---------------------------------------------------------------------------

function ManualHooksMode({
  activeAnchor,
}: {
  /** Phase 10 sub-step 10.8: the active ``BehaviorAnchor`` from Trace
   *  mode (lifted into ``LabTab``). When non-null, the chat-attachment
   *  builder folds it into a ``trace`` attachment so the operator can
   *  ask the LLM about the gates the Trace pipeline classified
   *  without leaving Manual Hooks mode. */
  activeAnchor: BehaviorAnchor | null;
}) {
  const { appId, dossier } = useWorkbench();
  const [selected, setSelected] = useState<SelectedNode | null>(null);

  // Active trace target. Either the most-recently-Injected session, or
  // a session the operator picked from the SessionsList.
  const [activeSession, setActiveSession] = useState<ActiveSession | null>(null);

  // Bumped after Inject / Detach so the SessionsList re-fetches eagerly.
  const [sessionsRefreshTick, setSessionsRefreshTick] = useState(0);

  // Right-pane bottom tab. Defaults to "trace" so existing operators
  // see the same surface they had in 4.5.
  const [rightTab, setRightTab] = useState<RightPaneTab>("trace");

  // Source for the centre CodeView, hoisted here so the chat-attachment
  // builder can include it as a ``code`` block without re-fetching.
  const [selectedSource, setSelectedSource] = useState<string | null>(null);

  // Polled snapshots of the active session's hooks summary + last-N trace
  // events, used to build the ``frida_summary`` attachment. We poll in this
  // tab (rather than in <ChatDock>) because the right-pane HookStatsPanel /
  // FridaTracePanel already render the same data — they're authoritative;
  // we just snapshot for chat. Both reset the moment the session changes.
  const [chatHooks, setChatHooks] = useState<HookStat[] | null>(null);
  const [chatTraceTail, setChatTraceTail] = useState<TraceEvent[] | null>(null);

  // Imperative handles + collapsed flags for the chat dock (collapses to a
  // bottom rail) and the right column (collapses to a right rail). Mirrors
  // the InspectTab pattern so the same operator muscle memory carries over.
  const chatRef = useRef<ImperativePanelHandle>(null);
  const [chatCollapsed, setChatCollapsed] = useState(false);
  const rightColRef = useRef<ImperativePanelHandle>(null);
  const [rightColCollapsed, setRightColCollapsed] = useState(false);

  // Hook Builder lives in the middle of the centre vertical PanelGroup, so
  // it can't fold to an edge rail; instead we mirror the AdbShell / logcat
  // pattern — header stays visible, body hides, and the panel is shrunk to
  // a thin strip.
  //
  // Layout intent: the collapsed Hook Builder strip should *drop down* and
  // sit flush against the top edge of the Chat section. By default
  // ``react-resizable-panels`` redistributes the freed space across all
  // siblings, which leaves the strip floating somewhere in the middle of
  // the column. To make the strip land just above Chat, we drive the
  // resize through ``PanelGroup.setLayout`` so the freed pixels go
  // exclusively to CodeView (the top neighbour) — Chat keeps its size,
  // and the strip ends up pinned to Chat's top edge.
  //
  // Pre-collapse sizes are remembered so expanding restores the operator's
  // own resize-bar customisations rather than the hard-coded defaults.
  const centerGroupRef = useRef<ImperativePanelGroupHandle>(null);
  const lastExpandedSizesRef = useRef<number[] | null>(null);
  const [hookBuilderCollapsed, setHookBuilderCollapsed] = useState(false);
  const HOOK_BUILDER_COLLAPSED_PCT = 4;
  const HOOK_BUILDER_DEFAULT_LAYOUT = [36, 44, 20];
  const toggleHookBuilder = () => {
    const grp = centerGroupRef.current;
    if (!grp) return;
    if (hookBuilderCollapsed) {
      grp.setLayout(lastExpandedSizesRef.current ?? HOOK_BUILDER_DEFAULT_LAYOUT);
    } else {
      const sizes = grp.getLayout();
      lastExpandedSizesRef.current = [...sizes];
      const [code, hb, chat] = sizes.length === 3 ? sizes : HOOK_BUILDER_DEFAULT_LAYOUT;
      const delta = hb - HOOK_BUILDER_COLLAPSED_PCT;
      grp.setLayout([code + delta, HOOK_BUILDER_COLLAPSED_PCT, chat]);
    }
  };

  // Decompiled pane (top of the centre vertical PanelGroup) is the
  // third foldable surface in this column. Unlike Hook Builder it can
  // use ``react-resizable-panels``' built-in collapsible/collapsedSize
  // pair directly — there's no neighbour-pinning intent here (Hook
  // Builder needed the manual ``setLayout`` dance to land the strip
  // flush against Chat's top edge; the Decompiled pane sits at the
  // top of the column already, so the freed pixels can land wherever
  // ``react-resizable-panels`` chooses to put them).
  const decompiledRef = useRef<ImperativePanelHandle>(null);
  const [decompiledCollapsed, setDecompiledCollapsed] = useState(false);
  const toggleDecompiled = () => {
    const p = decompiledRef.current;
    if (!p) return;
    if (decompiledCollapsed) p.expand();
    else p.collapse();
  };

  useEffect(() => {
    setSelected(null);
    setActiveSession(null);
  }, [appId]);

  const defaultPackage =
    typeof dossier?.apk_info?.package === "string" ? dossier.apk_info.package : null;

  const onSessionCreated = (result: CreateSessionResult) => {
    setActiveSession({
      sessionId: result.session_id,
      persistEnabled: !!result.persist_path,
    });
    setSessionsRefreshTick((n) => n + 1);
  };

  const onSelectSession = (info: FridaSessionInfo) => {
    setActiveSession({
      sessionId: info.session_id,
      persistEnabled: !!info.persist_path,
    });
  };

  const onDetached = (sessionId: string) => {
    setSessionsRefreshTick((n) => n + 1);
    if (activeSession?.sessionId === sessionId) {
      setActiveSession(null);
    }
  };

  const onSourceLoaded = useCallback((source: string | null) => {
    setSelectedSource(source);
  }, []);

  // Poll hooks summary + last-N events for the chat attachment. The
  // active-session check + early-return short-circuits the fetch loop the
  // moment the session is detached or swapped.
  useEffect(() => {
    setChatHooks(null);
    setChatTraceTail(null);
    const sessionId = activeSession?.sessionId ?? null;
    if (!sessionId) return;
    let cancelled = false;
    let timer: number | null = null;
    const tick = async () => {
      const [hooksRes, eventsRes] = await Promise.all([
        getSessionHooks(sessionId),
        getSessionEvents(sessionId, CHAT_TRACE_TAIL_LIMIT),
      ]);
      if (cancelled) return;
      if (hooksRes.ok) setChatHooks(hooksRes.data.hooks);
      if (eventsRes.ok) setChatTraceTail(eventsRes.data.events);
      timer = window.setTimeout(tick, CHAT_ATTACH_POLL_MS);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [activeSession?.sessionId]);

  const chatAttachments = useMemo<ChatAttachment[]>(
    () =>
      buildHookChatAttachments({
        appId,
        selected,
        selectedSource,
        activeSession,
        hooks: chatHooks,
        traceTail: chatTraceTail,
        activeAnchor,
      }),
    [
      appId,
      selected,
      selectedSource,
      activeSession,
      chatHooks,
      chatTraceTail,
      activeAnchor,
    ],
  );

  // -------------------------------------------------------------------------
  // Frida → Cytoscape overlay map (sub-step 4.8). See LabTab docstring for
  // the full rationale; the keying contract (class.dotted + method) matches
  // the call_graph DB ↔ Frida runtime symbol shape.
  // -------------------------------------------------------------------------
  const hitsByMethod = useMemo<ReadonlyMap<string, number> | null>(() => {
    if (!activeSession) return null;
    const out = new Map<string, number>();
    for (const h of chatHooks ?? []) {
      out.set(hitKey(h.class, h.method), h.hits);
    }
    return out;
  }, [activeSession, chatHooks]);

  // -------------------------------------------------------------------------
  // BehaviorAnchor → Cytoscape overlay map (Phase 11 sub-step 11.3).
  //
  // Fetched once per (appId, mount) — ``ManualHooksMode`` is unmounted
  // when ``labMode !== "manual-hooks"`` (LabTab uses conditional
  // rendering at line 165), so any trace build / delete in Trace mode
  // is naturally visible the next time the operator switches back: the
  // mode-switch re-mounts this component and fires the effect fresh.
  //
  // The fetch returns 404 on "no traces ever built" and 200+empty on
  // "built then cleared" — the consumer treats both as "no overlay,
  // no glyphs", so we collapse them to ``null`` (treat-as-overlay-off)
  // and a single non-null ``Map`` (overlay active) respectively.
  // The non-200/non-404 case (transient backend hiccup) also collapses
  // to ``null`` rather than surfacing a banner; the call-graph view
  // is the operator's primary surface here, not the trace cache.
  // -------------------------------------------------------------------------
  const [anchoredMethodRows, setAnchoredMethodRows] = useState<readonly AnchoredMethod[] | null>(null);
  useEffect(() => {
    setAnchoredMethodRows(null);
    if (!appId) return;
    let cancelled = false;
    void (async () => {
      const r = await listAnchoredMethods(appId);
      if (cancelled) return;
      if (r.ok) {
        setAnchoredMethodRows(r.data.methods);
      } else if (r.status === 404) {
        // 404 = unbuilt trace cache; treat as overlay off.
        setAnchoredMethodRows(null);
      } else {
        // Transient error; leave overlay off rather than flicker.
        setAnchoredMethodRows(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [appId]);

  const anchoredMethods = useMemo<ReadonlyMap<string, AnchoredMethodMeta> | null>(() => {
    if (anchoredMethodRows == null) return null;
    const out = new Map<string, AnchoredMethodMeta>();
    for (const m of anchoredMethodRows) {
      // ``class_smali`` is ``Lcom/example/Foo;``; the call-graph
      // ``GraphClass.class_name`` is ``com.example.Foo``. Convert
      // here so the consumer-side ``hitKey`` join matches.
      const javaClass = m.class_smali.startsWith("L") && m.class_smali.endsWith(";")
        ? m.class_smali.slice(1, -1).replace(/\//g, ".")
        : m.class_smali;
      out.set(hitKey(javaClass, m.method_name), {
        hops: m.hops,
        created_at: m.created_at,
      });
    }
    return out;
  }, [anchoredMethodRows]);

  return (
    <PanelGroup direction="horizontal" autoSaveId="lab-manual-h" className="tab-panels">
      <Panel defaultSize={32} minSize={20} className="panel">
        <CallGraphView
          appId={appId}
          onSelectNode={setSelected}
          appPackage={defaultPackage}
          hitsByMethod={hitsByMethod}
          anchoredMethods={anchoredMethods}
        />
      </Panel>
      <PanelResizeHandle className="resize-h" />
      <Panel defaultSize={46} minSize={30} className="panel">
        <PanelGroup
          ref={centerGroupRef}
          direction="vertical"
          autoSaveId="lab-manual-center-v"
        >
          <Panel
            ref={decompiledRef}
            defaultSize={36}
            minSize={4}
            collapsible
            collapsedSize={4}
            onCollapse={() => setDecompiledCollapsed(true)}
            onExpand={() => setDecompiledCollapsed(false)}
            className="panel"
          >
            <LabCodeView
              appId={appId}
              selected={selected}
              onSourceLoaded={onSourceLoaded}
              collapsed={decompiledCollapsed}
              onToggle={toggleDecompiled}
            />
          </Panel>
          <PanelResizeHandle className="resize-v" />
          <Panel
            defaultSize={44}
            minSize={4}
            collapsible
            collapsedSize={4}
            onCollapse={() => setHookBuilderCollapsed(true)}
            onExpand={() => setHookBuilderCollapsed(false)}
            className="panel"
          >
            <HookBuilder
              appId={appId}
              prefillClassName={selected?.className ?? null}
              prefillMethodName={selected?.methodName ?? null}
              defaultPackage={defaultPackage}
              onSessionCreated={onSessionCreated}
              collapsed={hookBuilderCollapsed}
              onToggle={toggleHookBuilder}
            />
          </Panel>
          <PanelResizeHandle className="resize-v" />
          <Panel
            ref={chatRef}
            defaultSize={20}
            minSize={10}
            collapsible
            collapsedSize={3}
            onCollapse={() => setChatCollapsed(true)}
            onExpand={() => setChatCollapsed(false)}
            className="panel chat-panel"
          >
            {chatCollapsed ? (
              <button
                type="button"
                className="sidebar-rail rail-bottom"
                onClick={() => chatRef.current?.expand()}
                title="Expand chat dock"
                aria-label="Expand chat dock"
              >
                <span className="sidebar-rail-chevron" aria-hidden="true">
                  <IconChevronUp />
                </span>
                <span className="sidebar-rail-label">Chat</span>
              </button>
            ) : (
              <ChatDock
                tab="lab"
                attachments={chatAttachments}
                contextSummary={buildHookChatContextSummary({
                  selected,
                  hasSource: selectedSource != null,
                  activeSession,
                  hooks: chatHooks,
                  traceTail: chatTraceTail,
                  activeAnchor,
                })}
                onCollapse={() => chatRef.current?.collapse()}
              />
            )}
          </Panel>
        </PanelGroup>
      </Panel>
      <PanelResizeHandle className="resize-h" />
      <Panel
        ref={rightColRef}
        defaultSize={22}
        minSize={14}
        collapsible
        collapsedSize={3}
        onCollapse={() => setRightColCollapsed(true)}
        onExpand={() => setRightColCollapsed(false)}
        className="panel"
      >
        {rightColCollapsed ? (
          <button
            type="button"
            className="sidebar-rail rail-right"
            onClick={() => rightColRef.current?.expand()}
            title="Expand sessions panel"
            aria-label="Expand sessions panel"
          >
            <span className="sidebar-rail-chevron" aria-hidden="true">
              <IconChevronLeft />
            </span>
            <span className="sidebar-rail-label">Sessions</span>
          </button>
        ) : (
          <PanelGroup direction="vertical" autoSaveId="lab-manual-right-v">
            <Panel defaultSize={36} minSize={18} className="panel">
              <FridaSessionsList
                selectedSessionId={activeSession?.sessionId ?? null}
                onSelect={onSelectSession}
                refreshTick={sessionsRefreshTick}
                onDetached={onDetached}
                onCollapse={() => rightColRef.current?.collapse()}
              />
            </Panel>
            <PanelResizeHandle className="resize-v" />
            <Panel defaultSize={64} minSize={20} className="panel">
              <div className="right-pane-tabs">
                <nav className="right-pane-tabs-nav" role="tablist" aria-label="Trace / Hooks / Scope">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={rightTab === "trace"}
                    className={`right-pane-tab ${rightTab === "trace" ? "right-pane-tab-active" : ""}`}
                    onClick={() => setRightTab("trace")}
                  >
                    Trace
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={rightTab === "hooks"}
                    className={`right-pane-tab ${rightTab === "hooks" ? "right-pane-tab-active" : ""}`}
                    onClick={() => setRightTab("hooks")}
                    disabled={!activeSession}
                    title={activeSession ? undefined : "Inject a hook to see per-method stats"}
                  >
                    Hooks
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={rightTab === "scope"}
                    className={`right-pane-tab ${rightTab === "scope" ? "right-pane-tab-active" : ""}`}
                    onClick={() => setRightTab("scope")}
                    disabled={!activeSession}
                    title={activeSession ? undefined : "Inject a scope_inspector hook to see field snapshots"}
                  >
                    Scope
                  </button>
                </nav>
                <div className="right-pane-tab-body">
                  {rightTab === "trace" && (
                    <FridaTracePanel
                      sessionId={activeSession?.sessionId ?? null}
                      persistEnabled={activeSession?.persistEnabled ?? false}
                    />
                  )}
                  {rightTab === "hooks" && (
                    <HookStatsPanel sessionId={activeSession?.sessionId ?? null} />
                  )}
                  {rightTab === "scope" && (
                    <ScopeInspectorPanel sessionId={activeSession?.sessionId ?? null} />
                  )}
                </div>
              </div>
            </Panel>
          </PanelGroup>
        )}
      </Panel>
    </PanelGroup>
  );
}

type ActiveSession = {
  sessionId: string;
  persistEnabled: boolean;
};

// ---------------------------------------------------------------------------
// In-tab CodeView wrapper (renamed from HookLabCodeView in 10.6 — same
// implementation, new name to match the Lab umbrella). Loads the Java
// source for the selected node via the existing /api/code/{app_id}/file
// endpoint and renders ``CodeView`` with ``emphasizeMethod={method_name}``
// so the body is tinted; we don't have a Java line number (the call graph
// stores Smali lines), but ``emphasizeMethod`` does the heavy lifting
// visually.
// ---------------------------------------------------------------------------

type LabCodeViewProps = {
  appId: string | null;
  selected: SelectedNode | null;
  /** Notifies the parent when the loaded Java source changes so it can
   *  forward the same text to the chat ``code`` attachment without
   *  re-fetching. ``null`` means "no source available" (no selection,
   *  load error, or path missing in the cache). */
  onSourceLoaded?: (source: string | null) => void;
  /** Optional collapse state. When ``collapsed`` is true the header
   *  strip stays visible (with the chevron + title), but the body
   *  (subtitle, empty-state hint, loading / error message, and the
   *  ``CodeView`` itself) is hidden. The parent panel
   *  (``react-resizable-panels`` Panel with ``collapsible``) handles
   *  the actual size shrinking — this prop just controls what the
   *  pane *renders* in its strip. */
  collapsed?: boolean;
  /** Click handler for the chevron in the header. Driven by the
   *  parent (which owns the ``ImperativePanelHandle`` ref it needs
   *  to call ``.expand()`` / ``.collapse()`` on). When omitted the
   *  chevron button isn't rendered, leaving the pane permanently
   *  expanded. */
  onToggle?: () => void;
};

function LabCodeView({
  appId,
  selected,
  onSourceLoaded,
  collapsed = false,
  onToggle,
}: LabCodeViewProps) {
  const [source, setSource] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!appId || !selected) {
      setSource(null);
      setLoadError(null);
      onSourceLoaded?.(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setSource(null);
    onSourceLoaded?.(null);
    void (async () => {
      const txt = await fetchSource(appId, selected.javaRelPath);
      if (cancelled) return;
      setLoading(false);
      if (txt == null) {
        setLoadError(
          `Could not load ${selected.javaRelPath}. The Java decompile may not include this class (e.g. a synthetic / smali-only type).`,
        );
        onSourceLoaded?.(null);
        return;
      }
      setSource(txt);
      onSourceLoaded?.(txt);
    })();
    return () => {
      cancelled = true;
    };
    // ``onSourceLoaded`` is intentionally omitted from the dep array — we
    // want the load effect keyed only on (appId, javaRelPath); the parent
    // wraps the callback in useCallback so its identity is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId, selected?.javaRelPath]);

  return (
    <div
      className={
        collapsed
          ? "pane-scroll hooklab-decompiled collapsed"
          : "pane-scroll hooklab-decompiled"
      }
      style={hostStyle}
    >
      <header className={`pane-head ${collapsed ? "pane-head-collapsed" : ""}`.trim()}>
        {onToggle && (
          <button
            type="button"
            className="logcat-toggle-btn"
            onClick={onToggle}
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand decompiled" : "Collapse decompiled"}
            title={collapsed ? "Expand decompiled" : "Collapse decompiled"}
          >
            {collapsed ? <IconChevronUp size={10} /> : <IconChevronDown size={10} />}
          </button>
        )}
        <h2>Decompiled</h2>
        {!collapsed && (
          <span className="muted small">
            {selected
              ? `${selected.className}.${selected.methodName}`
              : "click a graph node to open its source"}
          </span>
        )}
      </header>
      {!collapsed && !selected && (
        <p className="muted small">
          Java file from the jadx decompile cache lands here when you click a
          method in the call-graph pane (left). Method body is tinted via
          ``emphasizeMethod``; use the find bar (Cmd/Ctrl + F) to scan within
          the file.
        </p>
      )}
      {!collapsed && selected && loading && (
        <p className="muted small">loading {selected.javaRelPath}…</p>
      )}
      {!collapsed && selected && loadError && (
        <p className="muted small" style={{ color: "var(--accent)" }}>
          {loadError}
        </p>
      )}
      {!collapsed && selected && source != null && (
        <div style={codeWrapStyle}>
          <CodeView source={source} emphasizeMethod={selected.methodName} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat-attachment plumbing (sub-step 4.7). Unchanged from the original
// HookLabTab; lifted here verbatim so Manual Hooks mode behaves identically
// to the pre-10.6 tab.
// ---------------------------------------------------------------------------

type BuildAttachmentsArgs = {
  appId: string | null;
  selected: SelectedNode | null;
  selectedSource: string | null;
  activeSession: ActiveSession | null;
  hooks: HookStat[] | null;
  traceTail: TraceEvent[] | null;
  /** 10.8: when non-null the attachment builder emits a ``trace``
   *  ``ChatAttachment`` (entry method header + per-decision verdict
   *  list + top-3 ranked bypass plans) capped at the same 6,000 chars
   *  as the ``code`` attachment so the model can reason about gates
   *  the operator just identified without leaving Manual Hooks mode. */
  activeAnchor: BehaviorAnchor | null;
};

// Soft cap matching the backend ``ATTACHMENT_BUDGETS["trace"] == 6_000``.
// Mirrors ``CHAT_CODE_BUDGET``; we trim client-side so the operator's
// "show context" preview matches what the model actually sees.
const CHAT_TRACE_BUDGET = 6_000;
// We surface only the top-3 ranked (default-tier) bypass plans in the
// chat attachment to keep the context budget honest. Operators who want
// the full ranked list (incl. advanced higher-risk plans) read the
// Trace mode UI directly.
const CHAT_TRACE_TOP_PLANS = 3;
// Per-decision summary lines fold into the same 6_000-char budget
// alongside the entry header + plans; clipping further at this cap
// prevents a 200-decision closure from monopolising the attachment
// budget. Anything above this is replaced with a "+ N more decisions"
// trailer.
const CHAT_TRACE_MAX_DECISIONS = 40;

function _renderMethodRefForChat(m: { class_name: string; method_name: string }): string {
  return `${m.class_name}.${m.method_name}`;
}

function _renderTraceAttachment(anchor: BehaviorAnchor): string {
  const entry = anchor.entry_method;
  const parts: string[] = [];
  parts.push(
    `Entry method: ${_renderMethodRefForChat(entry)}` +
      `(${entry.param_descriptors.join(", ")})${entry.return_descriptor}`,
  );
  parts.push(
    `hops=${anchor.hops} · decisions=${anchor.decisions.length} · ` +
      `plans=${anchor.plans.length} (+${anchor.advanced_plans.length} advanced)` +
      (anchor.truncated ? " · TRUNCATED (cap hit)" : "") +
      (anchor.incomplete ? " · INCOMPLETE (unresolved predicate origins)" : ""),
  );
  if (anchor.rationale && anchor.rationale.trim()) {
    parts.push(`Rationale: ${anchor.rationale.trim()}`);
  }

  // Per-decision verdict list. One line per gate so the model can
  // reference them by index without us shipping the full nested
  // verdict / branch / origin structure.
  parts.push("");
  parts.push(`Decision timeline (${anchor.decisions.length}):`);
  const lowConf = new Set(anchor.low_confidence_decision_indices);
  const decisions = anchor.decisions.slice(0, CHAT_TRACE_MAX_DECISIONS);
  decisions.forEach((d, i) => {
    const verdicts =
      d.branch_outcome?.verdicts
        .map((v) => `${v.branch_label}=${v.verdict}(${v.score.toFixed(2)})`)
        .join(", ") ?? "(unclassified)";
    const origin =
      d.predicate_origin?.kind === "method_call"
        ? ` ← ${_renderMethodRefForChat(d.predicate_origin.method)}`
        : d.predicate_origin?.kind === "field_read"
        ? ` ← field ${d.predicate_origin.field.class_name}.${d.predicate_origin.field.field_name}`
        : d.predicate_origin?.kind === "const"
        ? ` ← const ${d.predicate_origin.value}`
        : d.predicate_origin?.kind === "param"
        ? ` ← param ${d.predicate_origin.register}`
        : d.predicate_origin?.kind === "composite"
        ? ` ← composite (${d.predicate_origin.reason})`
        : "";
    const flag = lowConf.has(i) ? " [LOW-CONF]" : "";
    parts.push(
      `  ${i + 1}. ${_renderMethodRefForChat(d.method)} @${d.instruction_index} ` +
        `[${d.kind}] ${verdicts}${origin}${flag}`,
    );
  });
  if (anchor.decisions.length > CHAT_TRACE_MAX_DECISIONS) {
    parts.push(
      `  + ${anchor.decisions.length - CHAT_TRACE_MAX_DECISIONS} more decision(s) ` +
        "(truncated for chat budget)",
    );
  }

  // Top-N default-tier plans only. Risk taxonomy is locked to
  // {low, medium, high}; the operator-configurable threshold lives on
  // the server (DEC-024 / 10.4) and decides the plans/advanced_plans
  // split — we just take the first N from the default tier.
  parts.push("");
  parts.push(`Top ${CHAT_TRACE_TOP_PLANS} bypass plan(s):`);
  if (anchor.plans.length === 0) {
    parts.push("  (none synthesised at the configured risk threshold)");
  } else {
    anchor.plans.slice(0, CHAT_TRACE_TOP_PLANS).forEach((p, i) => {
      const target = p.target_method
        ? _renderMethodRefForChat(p.target_method)
        : "(no target)";
      const params = Object.entries(p.params)
        .map(([k, v]) => `${k}=${v}`)
        .join(", ");
      parts.push(
        `  ${i + 1}. ${p.template_id} risk=${p.risk} → ${target}` +
          (params ? `\n     params: ${params}` : "") +
          (p.rationale ? `\n     rationale: ${p.rationale}` : ""),
      );
    });
  }

  let text = parts.join("\n");
  if (text.length > CHAT_TRACE_BUDGET) {
    text =
      text.slice(0, CHAT_TRACE_BUDGET) +
      `\n/* … truncated; full anchor is ${text.length} chars */`;
  }
  return text;
}

function buildHookChatAttachments({
  appId,
  selected,
  selectedSource,
  activeSession,
  hooks,
  traceTail,
  activeAnchor,
}: BuildAttachmentsArgs): ChatAttachment[] {
  const out: ChatAttachment[] = [];

  if (appId) {
    out.push({ kind: "default", name: "selection", text: `app_id: ${appId}` });
  }

  if (selected) {
    out.push({
      kind: "default",
      name: "selected_method",
      text:
        `class: ${selected.className}\n` +
        `method: ${selected.methodName}\n` +
        `java_rel_path: ${selected.javaRelPath}\n` +
        `smali_id: ${selected.smaliId}`,
    });
  }

  if (selected && selectedSource) {
    const trimmed =
      selectedSource.length > CHAT_CODE_BUDGET
        ? selectedSource.slice(0, CHAT_CODE_BUDGET) +
          `\n/* … truncated; full source is ${selectedSource.length} chars */`
        : selectedSource;
    out.push({
      kind: "code",
      name: selected.javaRelPath,
      text: trimmed,
    });
  }

  if (activeAnchor) {
    out.push({
      kind: "trace",
      name:
        activeAnchor.entry_method.class_name +
        "." +
        activeAnchor.entry_method.method_name,
      text: _renderTraceAttachment(activeAnchor),
    });
  }

  if (activeSession && (hooks?.length || traceTail?.length)) {
    out.push({
      kind: "frida_summary",
      name: activeSession.sessionId,
      text: JSON.stringify(
        {
          session_id: activeSession.sessionId,
          persist_enabled: activeSession.persistEnabled,
          hooks_summary: (hooks ?? []).map((h) => ({
            class: h.class,
            method: h.method,
            template_id: h.template_id,
            hits: h.hits,
            last_seen_ts: h.last_seen_ts,
            top_returns: h.top_returns,
          })),
          last_events: (traceTail ?? []).map((ev) => ({
            ts: ev.ts,
            kind: ev.kind,
            payload: ev.payload,
          })),
        },
        null,
        2,
      ),
    });
  }

  return out;
}

type SummaryArgs = {
  selected: SelectedNode | null;
  hasSource: boolean;
  activeSession: ActiveSession | null;
  hooks: HookStat[] | null;
  traceTail: TraceEvent[] | null;
  /** 10.8: surfaces the active ``BehaviorAnchor`` from Trace mode in
   *  the "show context" preview so the operator can tell at a glance
   *  the chat will see the trace summary. ``null`` when no anchor is
   *  loaded or the operator hasn't visited Trace mode yet. */
  activeAnchor: BehaviorAnchor | null;
};

function buildHookChatContextSummary({
  selected,
  hasSource,
  activeSession,
  hooks,
  traceTail,
  activeAnchor,
}: SummaryArgs): string {
  const lines: string[] = [];
  if (selected) {
    lines.push(`Selected method: ${selected.className}.${selected.methodName}`);
    lines.push(`Java file: ${selected.javaRelPath}`);
    lines.push(`Smali id: ${selected.smaliId}`);
    lines.push(
      hasSource
        ? `Decompiled source: included (capped at ${CHAT_CODE_BUDGET} chars)`
        : "Decompiled source: not loaded — open the file in the centre pane to attach it.",
    );
  } else {
    lines.push("Selected method: — (click a node in the call graph to seed the chat)");
    lines.push("Decompiled source: —");
  }

  lines.push("");
  if (activeAnchor) {
    const entry = activeAnchor.entry_method;
    lines.push(
      `Active behaviour trace: ${entry.class_name}.${entry.method_name} ` +
        `· hops=${activeAnchor.hops} · ${activeAnchor.decisions.length} decision(s) · ` +
        `${activeAnchor.plans.length} plan(s) (+${activeAnchor.advanced_plans.length} advanced)`,
    );
    lines.push(
      `Trace attachment: included (capped at ${CHAT_TRACE_BUDGET} chars; ` +
        `top ${CHAT_TRACE_TOP_PLANS} plans + first ${CHAT_TRACE_MAX_DECISIONS} decisions).`,
    );
  } else {
    lines.push(
      "Active behaviour trace: — (build a trace in Lab → Trace mode to attach it here).",
    );
  }

  if (activeSession) {
    const hookCount = hooks?.length ?? 0;
    const eventCount = traceTail?.length ?? 0;
    lines.push("");
    lines.push(`Active session: ${activeSession.sessionId}`);
    lines.push(
      `Frida trace summary: ${hookCount} hook row(s), last ${eventCount} event(s) ` +
        `(refreshed every ${Math.round(CHAT_ATTACH_POLL_MS / 1000)}s).`,
    );
  } else {
    lines.push("");
    lines.push("Active session: — (Inject a hook to attach trace context).");
  }

  return lines.join("\n");
}

const hostStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "0.5rem",
  height: "100%",
};

const codeWrapStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  border: "1px solid var(--border)",
  borderRadius: 4,
  overflow: "hidden",
};
