import { useEffect, useState, type CSSProperties } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { ChatDock } from "../components/ChatDock";
import { CallGraphView, type SelectedNode } from "../components/CallGraphView";
import { CodeView } from "../components/CodeView";
import { FridaSessionsList } from "../components/FridaSessionsList";
import { FridaTracePanel } from "../components/FridaTracePanel";
import { HookBuilder } from "../components/HookBuilder";
import { HookStatsPanel } from "../components/HookStatsPanel";
import { ScopeInspectorPanel } from "../components/ScopeInspectorPanel";
import { fetchSource } from "../api/code";
import type { CreateSessionResult, FridaSessionInfo } from "../api/frida";
import { useWorkbench } from "../context/WorkbenchContext";

/**
 * Hook Lab tab.
 *
 * Three-column layout (DEC-023, sub-steps 4.5 + 4.6):
 *
 *   ┌──────────┬──────────────────────────┬──────────────────────┐
 *   │          │  CodeView (top)          │  Sessions list       │
 *   │  Call    ├──────────────────────────┤                      │
 *   │  graph   │  HookBuilder (mid)       ├──────────────────────┤
 *   │          ├──────────────────────────┤  [ Trace | Hooks |   │
 *   │          │  ChatDock (bottom)       │    Scope ] panel     │
 *   └──────────┴──────────────────────────┴──────────────────────┘
 *
 * Cross-component wiring:
 *
 *   * Selecting a method in the call graph emits ``SelectedNode``;
 *     we (a) load its decompiled source into the inline CodeView,
 *     and (b) prefill the HookBuilder's ``class_name`` / ``method_name``
 *     params so the operator can Inject without retyping them.
 *   * Successful Inject creates a session; we capture its id +
 *     ``persist_path`` and pin the right pane's trace to it.
 *   * Picking a different session in the SessionsList swaps the
 *     trace / hooks / scope panes to that session's data.
 *
 * Right-pane bottom slot is a tab strip (sub-step 4.6): default
 * "Trace" preserves 4.5's behaviour; "Hooks" surfaces the per-
 * (class, method) hit counts + top return values; "Scope" shows
 * captured ``this_fields`` snapshots from the ``scope_inspector``
 * template. All three panels share the same ``activeSession``.
 */
type RightPaneTab = "trace" | "hooks" | "scope";
export function HookLabTab() {
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

  return (
    <PanelGroup direction="horizontal" autoSaveId="hook-h" className="tab-panels">
      <Panel defaultSize={32} minSize={20} className="panel">
        <CallGraphView appId={appId} onSelectNode={setSelected} />
      </Panel>
      <PanelResizeHandle className="resize-h" />
      <Panel defaultSize={46} minSize={30} className="panel">
        <PanelGroup direction="vertical" autoSaveId="hook-center-v">
          <Panel defaultSize={36} minSize={18} className="panel">
            <HookLabCodeView appId={appId} selected={selected} />
          </Panel>
          <PanelResizeHandle className="resize-v" />
          <Panel defaultSize={44} minSize={22} className="panel">
            <HookBuilder
              appId={appId}
              prefillClassName={selected?.className ?? null}
              prefillMethodName={selected?.methodName ?? null}
              defaultPackage={defaultPackage}
              onSessionCreated={onSessionCreated}
            />
          </Panel>
          <PanelResizeHandle className="resize-v" />
          <Panel defaultSize={20} minSize={10} collapsible className="panel chat-panel">
            <ChatDock
              tab="hook"
              attachments={[]}
              contextSummary={buildHookChatContextSummary(selected)}
            />
          </Panel>
        </PanelGroup>
      </Panel>
      <PanelResizeHandle className="resize-h" />
      <Panel defaultSize={22} minSize={14} className="panel">
        <PanelGroup direction="vertical" autoSaveId="hook-right-v">
          <Panel defaultSize={36} minSize={18} className="panel">
            <FridaSessionsList
              selectedSessionId={activeSession?.sessionId ?? null}
              onSelect={onSelectSession}
              refreshTick={sessionsRefreshTick}
              onDetached={onDetached}
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
      </Panel>
    </PanelGroup>
  );
}

type ActiveSession = {
  sessionId: string;
  persistEnabled: boolean;
};

// ---------------------------------------------------------------------------
// In-tab CodeView wrapper. Loads the Java source for the selected node via
// the existing /api/code/{app_id}/file endpoint and renders ``CodeView``
// with ``emphasizeMethod={method_name}`` so the body is tinted; we don't
// have a Java line number (the call graph stores Smali lines), but
// ``emphasizeMethod`` does the heavy lifting visually.
// ---------------------------------------------------------------------------

type HookLabCodeViewProps = {
  appId: string | null;
  selected: SelectedNode | null;
};

function HookLabCodeView({ appId, selected }: HookLabCodeViewProps) {
  const [source, setSource] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!appId || !selected) {
      setSource(null);
      setLoadError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setSource(null);
    void (async () => {
      const txt = await fetchSource(appId, selected.javaRelPath);
      if (cancelled) return;
      setLoading(false);
      if (txt == null) {
        setLoadError(
          `Could not load ${selected.javaRelPath}. The Java decompile may not include this class (e.g. a synthetic / smali-only type).`,
        );
        return;
      }
      setSource(txt);
    })();
    return () => {
      cancelled = true;
    };
  }, [appId, selected?.javaRelPath]);

  return (
    <div className="pane-scroll" style={hostStyle}>
      <header className="pane-head">
        <h2>Decompiled</h2>
        <span className="muted small">
          {selected
            ? `${selected.className}.${selected.methodName}`
            : "click a graph node to open its source"}
        </span>
      </header>
      {!selected && (
        <p className="muted small">
          Java file from the jadx decompile cache lands here when you click a
          method in the call-graph pane (left). Method body is tinted via
          ``emphasizeMethod``; use the find bar (Cmd/Ctrl + F) to scan within
          the file.
        </p>
      )}
      {selected && loading && <p className="muted small">loading {selected.javaRelPath}…</p>}
      {selected && loadError && (
        <p className="muted small" style={{ color: "var(--accent)" }}>
          {loadError}
        </p>
      )}
      {selected && source != null && (
        <div style={codeWrapStyle}>
          <CodeView source={source} emphasizeMethod={selected.methodName} />
        </div>
      )}
    </div>
  );
}

function buildHookChatContextSummary(selected: SelectedNode | null): string {
  if (!selected) {
    return (
      "(no method selected)\n" +
      "Click a method in the call-graph pane (left) to seed the chat with " +
      "the selected node's class + method as context."
    );
  }
  return (
    `Selected method: ${selected.className}.${selected.methodName}\n` +
    `Java file: ${selected.javaRelPath}\n` +
    `Smali id: ${selected.smaliId}\n` +
    "Will include: open file (decompiled), selected method, active hooks list, " +
    "frida-trace summary (counts + 30-event tail)."
  );
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
