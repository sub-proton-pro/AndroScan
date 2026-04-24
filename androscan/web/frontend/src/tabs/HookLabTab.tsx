import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { ChatDock } from "../components/ChatDock";

export function HookLabTab() {
  return (
    <PanelGroup direction="horizontal" autoSaveId="hook-h" className="tab-panels">
      <Panel defaultSize={30} minSize={18} className="panel">
        <div className="pane-scroll">
          <header className="pane-head">
            <h2>Code graph</h2>
            <span className="muted small">static + frida overlay</span>
          </header>
          <p className="muted small">
            Cytoscape graph: static smali edges in muted grey, frida-runtime edges in bold cyan;
            hot paths warm. Lands in step 4.
          </p>
        </div>
      </Panel>
      <PanelResizeHandle className="resize-h" />
      <Panel defaultSize={50} minSize={30} className="panel">
        <PanelGroup direction="vertical" autoSaveId="hook-center-v">
          <Panel defaultSize={50} minSize={20} className="panel">
            <div className="pane-scroll">
              <header className="pane-head">
                <h2>Decompiled</h2>
                <span className="muted small">Monaco — step 4</span>
              </header>
              <p className="muted small">
                Java + smali side-by-side, with hook overlay annotations.
              </p>
            </div>
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
          <Panel defaultSize={30} minSize={12} collapsible className="panel chat-panel">
            <ChatDock
              tab="hook"
              attachments={[]}
              contextSummary={
                "(no method selected)\nWill include: open file (decompiled), selected method, active hooks list, frida-trace summary (counts + 30-event tail)."
              }
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
