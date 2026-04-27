import { useEffect, useState, type CSSProperties } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { ChatDock } from "../components/ChatDock";
import { CallGraphView, type SelectedNode } from "../components/CallGraphView";
import { CodeView } from "../components/CodeView";
import { fetchSource } from "../api/code";
import { useWorkbench } from "../context/WorkbenchContext";

export function HookLabTab() {
  const { appId } = useWorkbench();
  const [selected, setSelected] = useState<SelectedNode | null>(null);

  // Reset the selection whenever the operator switches apps so the
  // CodeView doesn't briefly render stale source from the previous app.
  useEffect(() => {
    setSelected(null);
  }, [appId]);

  return (
    <PanelGroup direction="horizontal" autoSaveId="hook-h" className="tab-panels">
      <Panel defaultSize={36} minSize={20} className="panel">
        <CallGraphView appId={appId} onSelectNode={setSelected} />
      </Panel>
      <PanelResizeHandle className="resize-h" />
      <Panel defaultSize={44} minSize={28} className="panel">
        <PanelGroup direction="vertical" autoSaveId="hook-center-v">
          <Panel defaultSize={55} minSize={20} className="panel">
            <HookLabCodeView appId={appId} selected={selected} />
          </Panel>
          <PanelResizeHandle className="resize-v" />
          <Panel defaultSize={20} minSize={10} className="panel">
            <div className="pane-scroll">
              <header className="pane-head">
                <h2>Frida script</h2>
                <span className="muted small">staged hooks</span>
              </header>
              <p className="muted small">
                Editor with explicit "Stage hook" → "Confirm &amp; run" flow per DEC-017.
              </p>
            </div>
          </Panel>
          <PanelResizeHandle className="resize-v" />
          <Panel defaultSize={25} minSize={12} collapsible className="panel chat-panel">
            <ChatDock
              tab="hook"
              attachments={[]}
              contextSummary={buildHookChatContextSummary(selected)}
            />
          </Panel>
        </PanelGroup>
      </Panel>
      <PanelResizeHandle className="resize-h" />
      <Panel defaultSize={20} minSize={12} className="panel">
        <PanelGroup direction="vertical" autoSaveId="hook-right-v">
          <Panel defaultSize={55} minSize={20} className="panel">
            <div className="pane-scroll">
              <header className="pane-head">
                <h2>Scope</h2>
                <span className="muted small">live vars</span>
              </header>
              <p className="muted small">
                Variable inspector: <code>this</code>, args, fields. Edits emit
                <code> Java.use(...).$instance.field = value</code>.
              </p>
            </div>
          </Panel>
          <PanelResizeHandle className="resize-v" />
          <Panel defaultSize={45} minSize={15} className="panel">
            <div className="pane-scroll">
              <header className="pane-head">
                <h2>Hooks &amp; trace stats</h2>
                <span className="muted small">step 4</span>
              </header>
              <p className="muted small">Active hooks, hit counts, top return values.</p>
            </div>
          </Panel>
        </PanelGroup>
      </Panel>
    </PanelGroup>
  );
}

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
