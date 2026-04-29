import { useEffect, useMemo, useRef, useState } from "react";
import {
  ImperativePanelHandle,
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";
import {
  fetchSource,
  fetchTree,
  getDecompileStatus,
  type CodeTree,
  type DecompileStatus,
} from "../api/code";
import { mapTap, type MapResult } from "../api/inspect";
import { AdbShell } from "../components/AdbShell";
import { ChatDock } from "../components/ChatDock";
import { ClassMethodTree } from "../components/ClassMethodTree";
import { CodeView } from "../components/CodeView";
import { ElementMappingPanel } from "../components/ElementMappingPanel";
import {
  IconChevronLeft,
  IconChevronRight,
  IconChevronUp,
} from "../components/Icons";
import { MirrorView } from "../components/MirrorView";
import { ProjectsSidebar } from "../components/ProjectsSidebar";
import { ScopedLogcat } from "../components/ScopedLogcat";
import { useWorkbench } from "../context/WorkbenchContext";
import type { ChatAttachment } from "../types";
import {
  javaRelPathToSmaliMethodPrefix,
} from "../util/smaliClassToFile";
import type { ResolutionCandidate } from "../api/inspect";

const PENDING_POLL_MS = 4000;

export function InspectTab() {
  const {
    appId,
    dossier,
    pendingCodeNav,
    setPendingCodeNav,
    setPendingTraceEntry,
    setLabMode,
    setTab,
  } = useWorkbench();
  const packageName = useMemo<string | null>(() => {
    const apk = (dossier as Record<string, unknown> | null)?.apk_info as
      | { package?: string }
      | undefined;
    return apk?.package || null;
  }, [dossier]);

  const [decompile, setDecompile] = useState<DecompileStatus | null>(null);
  const [tree, setTree] = useState<CodeTree | null>(null);
  const [filter, setFilter] = useState("");
  const [openClassPath, setOpenClassPath] = useState<string | null>(null);
  const [openClassSource, setOpenClassSource] = useState<string | null>(null);
  const [openMethod, setOpenMethod] = useState<string | null>(null);
  const [logcatCollapsed, setLogcatCollapsed] = useState(true);
  const logcatRef = useRef<ImperativePanelHandle>(null);
  const [adbCollapsed, setAdbCollapsed] = useState(true);
  const adbRef = useRef<ImperativePanelHandle>(null);
  const [mapResult, setMapResult] = useState<MapResult | null>(null);
  const [mapBusy, setMapBusy] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);
  const sidebarRef = useRef<ImperativePanelHandle>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const treeColRef = useRef<ImperativePanelHandle>(null);
  const [treeColCollapsed, setTreeColCollapsed] = useState(false);
  const mirrorColRef = useRef<ImperativePanelHandle>(null);
  const [mirrorColCollapsed, setMirrorColCollapsed] = useState(false);
  const chatRef = useRef<ImperativePanelHandle>(null);
  const [chatCollapsed, setChatCollapsed] = useState(false);
  type CenterTab = "mapping" | "code";
  const [centerTab, setCenterTab] = useState<CenterTab>("mapping");
  // Anchor line for the Code Browser viewer; bumped each time the user
  // jumps to a candidate so the CodeView re-scrolls even when the file
  // is already loaded.
  const [scrollTarget, setScrollTarget] = useState<number | null>(null);
  // Persistent highlight covering the snippet range of whichever code
  // candidate the user last opened in the Code Browser tab. Cleared as
  // soon as the user picks a different class / method from the tree.
  const [highlightRange, setHighlightRange] = useState<
    [number, number] | null
  >(null);

  // Initial decompile-status load on app change.
  useEffect(() => {
    setDecompile(null);
    setTree(null);
    setOpenClassPath(null);
    setOpenClassSource(null);
    setOpenMethod(null);
    setMapResult(null);
    setScrollTarget(null);
    setHighlightRange(null);
    if (!appId) return;
    let cancelled = false;
    (async () => {
      const s = await getDecompileStatus(appId);
      if (cancelled) return;
      setDecompile(s);
      if (s.status === "ready") {
        const t = await fetchTree(appId);
        if (!cancelled) setTree(t);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [appId]);

  // Poll while pending.
  useEffect(() => {
    if (!appId || decompile?.status !== "pending") return;
    const id = window.setInterval(async () => {
      const s = await getDecompileStatus(appId);
      setDecompile(s);
      if (s.status === "ready") {
        const t = await fetchTree(appId);
        setTree(t);
        window.clearInterval(id);
      }
    }, PENDING_POLL_MS);
    return () => window.clearInterval(id);
  }, [appId, decompile?.status]);

  const handleSelect = async (sel: { rel_path: string; class_name: string; method?: string }) => {
    setOpenClassPath(sel.rel_path);
    setOpenMethod(sel.method ?? null);
    setScrollTarget(null);
    setHighlightRange(null);
    setCenterTab("code");
    if (appId) {
      const text = await fetchSource(appId, sel.rel_path);
      setOpenClassSource(text ?? "(failed to load)");
    }
  };

  // Cross-tab "Open in Inspect" — Hook Lab (CallGraphView's right-click menu)
  // writes ``pendingCodeNav`` and switches the tab; we consume it here and
  // clear it so the next click re-fires correctly. Keyed on ``ts`` to force
  // re-fire when the same node is opened twice in a row.
  useEffect(() => {
    if (!pendingCodeNav) return;
    if (!appId || pendingCodeNav.appId !== appId) return;
    void handleSelect({
      rel_path: pendingCodeNav.relPath,
      class_name: pendingCodeNav.className,
      method: pendingCodeNav.method,
    });
    setPendingCodeNav(null);
    // ``handleSelect`` is a stable closure over local setters; safe to
    // reference without listing it as a dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingCodeNav?.ts, appId]);

  // Open a code candidate (from the UI Mapping panel) in the Code Browser
  // tab, scroll to its first line and persistently highlight every line
  // covered by the snippet. Forces a value change on ``scrollTarget`` even
  // when the user re-clicks the same candidate, by nulling first.
  const handleOpenCandidateInBrowser = async (
    file: string,
    startLine: number,
    endLine: number,
  ) => {
    if (!appId) return;
    setOpenMethod(null);
    setScrollTarget(null);
    setHighlightRange(null);
    setCenterTab("code");
    if (openClassPath !== file || openClassSource === null) {
      setOpenClassPath(file);
      setOpenClassSource(null);
      const text = await fetchSource(appId, file);
      setOpenClassSource(text ?? "(failed to load)");
    }
    // Defer to next frame so a same-target re-click still flips the prop.
    requestAnimationFrame(() => {
      setScrollTarget(startLine);
      setHighlightRange([startLine, endLine]);
    });
  };

  // Cross-tab "Trace this behaviour" — the BestBanner button (Phase 10
  // sub-step 10.8) hands us the fuser's pick. We turn it into a Smali
  // entry-method *prefix* (the resolver doesn't carry per-overload
  // descriptors, so we cap at ``Lcom/example/Foo;->methodName(`` when a
  // method name is known, else ``Lcom/example/Foo;->``), seed the
  // pending-trace primitive, flip Lab to Trace mode, and switch tabs.
  // The Trace mode form prefills + surfaces a "Seeded from Inspect"
  // pill so the operator can complete the descriptor list and fire
  // Trace, all without losing the click context.
  const handleTraceBehaviour = (best: ResolutionCandidate) => {
    if (!appId) return;
    const prefix = javaRelPathToSmaliMethodPrefix(best.file, best.method_name);
    if (!prefix) return;
    const fileSimple = best.file.split("/").pop() || best.file;
    const sourceLabel =
      best.method_name
        ? `Inspect → ${fileSimple}#${best.method_name}`
        : `Inspect → ${fileSimple}:${best.line}`;
    setPendingTraceEntry({
      appId,
      entryPrefix: prefix,
      sourceLabel,
    });
    setLabMode("trace");
    setTab("lab");
  };

  const handleTap = async (x: number, y: number) => {
    if (!appId) {
      setMapError("select a project first");
      return;
    }
    setMapBusy(true);
    setMapError(null);
    setCenterTab("mapping");
    const r = await mapTap(appId, x, y);
    setMapBusy(false);
    if (!r) {
      setMapError("map request failed");
      return;
    }
    setMapResult(r);
    // Prefer the fuser's pick (resolution.best) over the raw first
    // deterministic candidate so the Code Browser jumps straight to the
    // most likely handler — including RAG hits when the deterministic
    // grep returned nothing useful.
    const best = r.resolution?.best;
    if (best) {
      const numLines = best.snippet
        ? Math.max(1, best.snippet.split("\n").length)
        : 4;
      const start = Math.max(1, best.line || 1);
      const end = start + numLines - 1;
      const text = await fetchSource(appId, best.file);
      setOpenClassPath(best.file);
      setOpenClassSource(text ?? "(failed to load)");
      setOpenMethod(best.method_name ?? null);
      requestAnimationFrame(() => {
        setScrollTarget(start);
        setHighlightRange([start, end]);
      });
    } else if (r.candidates[0]) {
      const text = await fetchSource(appId, r.candidates[0].file);
      setOpenClassPath(r.candidates[0].file);
      setOpenClassSource(text ?? "(failed to load)");
      setOpenMethod(null);
    }
  };

  const attachments = useMemo<ChatAttachment[]>(() => {
    const out: ChatAttachment[] = [];
    if (appId) out.push({ kind: "default", name: "selection", text: `app_id: ${appId}` });
    if (packageName) out.push({ kind: "default", name: "package", text: packageName });
    if (mapResult?.element) {
      out.push({
        kind: "default",
        name: "ui_element",
        text: JSON.stringify(
          {
            foreground_activity: mapResult.foreground_activity,
            element: mapResult.element,
            short_resource_id: mapResult.short_resource_id,
          },
          null,
          2,
        ),
      });
    }
    if (mapResult?.candidates?.length) {
      const top = mapResult.candidates.slice(0, 5);
      out.push({
        kind: "code",
        name: "candidates",
        text: top
          .map(
            (c) =>
              `# ${c.kind} — ${c.file}:${c.line}\n${c.snippet}`,
          )
          .join("\n\n"),
      });
    }
    // Surface the fuser's pick + reasons so the model can reference the
    // ranked best handler directly instead of re-deriving it from the raw
    // candidate list. Kept as a small ``default`` attachment so the
    // existing per-kind chat budget applies.
    const best = mapResult?.resolution?.best;
    if (best) {
      out.push({
        kind: "default",
        name: "best_handler",
        text: JSON.stringify(
          {
            file: best.file,
            line: best.line,
            class_name: best.class_name,
            method_name: best.method_name,
            source: best.source,
            kind: best.kind,
            score: best.score,
            reasons: best.reasons,
          },
          null,
          2,
        ),
      });
    }
    if (openClassPath && openClassSource) {
      out.push({
        kind: "code",
        name: openClassPath + (openMethod ? `::${openMethod}` : ""),
        text: openClassSource,
      });
    }
    return out;
  }, [appId, packageName, mapResult, openClassPath, openClassSource, openMethod]);

  return (
    <PanelGroup direction="horizontal" autoSaveId="inspect-h" className="tab-panels">
      <Panel
        ref={sidebarRef}
        defaultSize={22}
        minSize={14}
        collapsible
        collapsedSize={3}
        onCollapse={() => setSidebarCollapsed(true)}
        onExpand={() => setSidebarCollapsed(false)}
        className="panel sidebar"
      >
        {sidebarCollapsed ? (
          <button
            type="button"
            className="sidebar-rail"
            onClick={() => sidebarRef.current?.expand()}
            title="Expand projects sidebar"
            aria-label="Expand projects sidebar"
          >
            <span className="sidebar-rail-label">Projects</span>
            <span className="sidebar-rail-chevron" aria-hidden="true">
              <IconChevronRight />
            </span>
          </button>
        ) : (
          <ProjectsSidebar onCollapse={() => sidebarRef.current?.collapse()} />
        )}
      </Panel>
      <PanelResizeHandle className="resize-h" />
      <Panel
        ref={treeColRef}
        defaultSize={26}
        minSize={18}
        collapsible
        collapsedSize={3}
        onCollapse={() => setTreeColCollapsed(true)}
        onExpand={() => setTreeColCollapsed(false)}
        className="panel"
      >
        {treeColCollapsed ? (
          <button
            type="button"
            className="sidebar-rail"
            onClick={() => treeColRef.current?.expand()}
            title="Expand classes & methods"
            aria-label="Expand classes & methods"
          >
            <span className="sidebar-rail-label">Classes &amp; methods</span>
            <span className="sidebar-rail-chevron" aria-hidden="true">
              <IconChevronRight />
            </span>
          </button>
        ) : (
          <PanelGroup direction="vertical" autoSaveId="inspect-left-v">
            <Panel defaultSize={logcatCollapsed ? 95 : 62} minSize={20} className="panel">
              <div className="pane-scroll">
                <header className="pane-head">
                  <h2>Classes &amp; methods</h2>
                  <div className="pane-head-actions">
                    <span className="muted small">
                      {tree ? `${tree.packages.length} pkgs · cached` : "—"}
                    </span>
                    <button
                      type="button"
                      className="ghost-mini icon-btn"
                      onClick={() => treeColRef.current?.collapse()}
                      title="Collapse classes & methods"
                      aria-label="Collapse classes & methods"
                    >
                      <IconChevronLeft />
                    </button>
                  </div>
                </header>
                <ClassMethodTree
                  appId={appId}
                  status={decompile}
                  tree={tree}
                  filter={filter}
                  onFilterChange={setFilter}
                  onTreeLoaded={setTree}
                  onStatus={setDecompile}
                  onSelect={handleSelect}
                  appPackage={packageName}
                />
              </div>
            </Panel>
            <PanelResizeHandle className="resize-v" />
            <Panel
              ref={logcatRef}
              defaultSize={5}
              minSize={4}
              collapsible
              collapsedSize={4}
              className="panel logcat-panel"
            >
              <ScopedLogcat
                packageName={packageName}
                collapsed={logcatCollapsed}
                onToggle={() => {
                  setLogcatCollapsed((v) => {
                    const next = !v;
                    const handle = logcatRef.current;
                    if (handle) {
                      if (next) handle.resize(5);
                      else handle.resize(45);
                    }
                    return next;
                  });
                }}
              />
            </Panel>
          </PanelGroup>
        )}
      </Panel>
      <PanelResizeHandle className="resize-h" />
      <Panel defaultSize={32} minSize={20} className="panel">
        <PanelGroup direction="vertical" autoSaveId="inspect-center-v">
          <Panel defaultSize={62} minSize={20} className="panel">
            <div className="pane-scroll center-tabs">
              <header className="pane-head pane-head-tabs">
                <div className="center-tabbar" role="tablist">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={centerTab === "mapping"}
                    className={
                      centerTab === "mapping"
                        ? "center-tab active"
                        : "center-tab"
                    }
                    onClick={() => setCenterTab("mapping")}
                  >
                    UI mapping
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={centerTab === "code"}
                    className={
                      centerTab === "code"
                        ? "center-tab active"
                        : "center-tab"
                    }
                    onClick={() => setCenterTab("code")}
                  >
                    Code browser
                  </button>
                </div>
                <span className="muted small">
                  {openClassPath
                    ? openClassPath +
                      (openMethod ? ` :: ${openMethod}()` : "")
                    : "no selection"}
                </span>
              </header>

              {/* Both centre-tab panels stay mounted; only the inactive
                  one is hidden via CSS so its scroll position (and any
                  internal state — find query, gear prefs, scrollback) is
                  preserved across tab switches. */}
              <div
                className={
                  centerTab === "mapping"
                    ? "tab-content"
                    : "tab-content hidden-tab-content"
                }
              >
                <ElementMappingPanel
                  appId={appId}
                  result={mapResult}
                  busy={mapBusy}
                  error={mapError}
                  onOpenInBrowser={handleOpenCandidateInBrowser}
                  onTraceBehaviour={handleTraceBehaviour}
                />
              </div>

              <div
                className={
                  centerTab === "code"
                    ? "tab-content"
                    : "tab-content hidden-tab-content"
                }
              >
                <div className="code-browser">
                  {!openClassPath ? (
                    <p className="muted code-browser-empty">
                      Select a class from the <strong>Classes &amp; methods</strong>{" "}
                      panel to show its methods and source code here.
                    </p>
                  ) : openClassSource === null ? (
                    <p className="muted small">Loading source…</p>
                  ) : (
                    <>
                      <div className="code-browser-head">
                        <code className="code-browser-path">{openClassPath}</code>
                        {openMethod && (
                          <span className="muted small">
                            method emphasis: <strong>{openMethod}()</strong>
                          </span>
                        )}
                      </div>
                      <CodeView
                        source={openClassSource}
                        emphasizeMethod={openMethod}
                        scrollToLine={scrollTarget}
                        highlightRange={highlightRange}
                      />
                    </>
                  )}
                </div>
              </div>
            </div>
          </Panel>
          <PanelResizeHandle className="resize-v" />
          <Panel
            ref={chatRef}
            defaultSize={38}
            minSize={12}
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
                tab="inspect"
                attachments={attachments}
                onCollapse={() => chatRef.current?.collapse()}
              />
            )}
          </Panel>
        </PanelGroup>
      </Panel>
      <PanelResizeHandle className="resize-h" />
      <Panel
        ref={mirrorColRef}
        defaultSize={22}
        minSize={16}
        collapsible
        collapsedSize={3}
        onCollapse={() => setMirrorColCollapsed(true)}
        onExpand={() => setMirrorColCollapsed(false)}
        className="panel"
      >
        {mirrorColCollapsed ? (
          <button
            type="button"
            className="sidebar-rail rail-right"
            onClick={() => mirrorColRef.current?.expand()}
            title="Expand mirror panel"
            aria-label="Expand mirror panel"
          >
            <span className="sidebar-rail-chevron" aria-hidden="true">
              <IconChevronLeft />
            </span>
            <span className="sidebar-rail-label">Mirror</span>
          </button>
        ) : (
          <PanelGroup direction="vertical" autoSaveId="inspect-right-v">
            <Panel
              defaultSize={adbCollapsed ? 95 : 62}
              minSize={28}
              className="panel"
            >
              <MirrorView
                onTap={handleTap}
                onCollapse={() => mirrorColRef.current?.collapse()}
                appId={appId}
              />
            </Panel>
            <PanelResizeHandle className="resize-v" />
            <Panel
              ref={adbRef}
              defaultSize={5}
              minSize={4}
              collapsible
              collapsedSize={4}
              className="panel adb-panel"
            >
              <AdbShell
                collapsed={adbCollapsed}
                onToggle={() => {
                  setAdbCollapsed((v) => {
                    const next = !v;
                    const handle = adbRef.current;
                    if (handle) {
                      if (next) handle.resize(5);
                      else handle.resize(45);
                    }
                    return next;
                  });
                }}
              />
            </Panel>
          </PanelGroup>
        )}
      </Panel>
    </PanelGroup>
  );
}
