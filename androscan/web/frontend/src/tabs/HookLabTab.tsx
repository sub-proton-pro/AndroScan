import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { ChatDock } from "../components/ChatDock";
import {
  CallGraphView,
  hitKey,
  type SelectedNode,
} from "../components/CallGraphView";
import { CodeView } from "../components/CodeView";
import { FridaSessionsList } from "../components/FridaSessionsList";
import { FridaTracePanel } from "../components/FridaTracePanel";
import { HookBuilder } from "../components/HookBuilder";
import { HookStatsPanel } from "../components/HookStatsPanel";
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
      }),
    [appId, selected, selectedSource, activeSession, chatHooks, chatTraceTail],
  );

  // -------------------------------------------------------------------------
  // Frida → Cytoscape overlay map (sub-step 4.8).
  //
  // The overlay is "on" exactly when an active session is pinned. While
  // ``chatHooks`` is still loading (``null``) we deliberately pass an empty
  // Map rather than ``null`` so the overlay turns on immediately — the
  // graph renders dimmed and the hits flow in on the next 2.5s poll. That
  // matches the rest of the right pane (HookStatsPanel / FridaTracePanel
  // both surface "session active, no events yet" affordances rather than
  // hiding their UI). When there's no session, we pass ``null`` so the
  // graph reverts to its plain 4.2 styling — operators get the static
  // graph back the moment they detach.
  //
  // Keying by class.class_name + method_name matches the call_graph DB's
  // dotted form (Smali → ".") to the Frida runtime's ``Java.use(name).$className``
  // (also dotted, with ``$`` for inner-class boundaries on both sides). If
  // method overloads ever caused an ambiguous hit (same name, different
  // signatures), the count attached to the node would *under*-count rather
  // than mis-attribute — hooks attribute by class+method, not by full
  // descriptor, in 4.6's aggregator. Tracked as an overlay-precision
  // follow-up in KNOWN_ISSUES (ISSUE-012 in 4.8 docs sweep).
  // -------------------------------------------------------------------------
  const hitsByMethod = useMemo<ReadonlyMap<string, number> | null>(() => {
    if (!activeSession) return null;
    const out = new Map<string, number>();
    for (const h of chatHooks ?? []) {
      out.set(hitKey(h.class, h.method), h.hits);
    }
    return out;
  }, [activeSession, chatHooks]);

  return (
    <PanelGroup direction="horizontal" autoSaveId="hook-h" className="tab-panels">
      <Panel defaultSize={32} minSize={20} className="panel">
        <CallGraphView
          appId={appId}
          onSelectNode={setSelected}
          appPackage={defaultPackage}
          hitsByMethod={hitsByMethod}
        />
      </Panel>
      <PanelResizeHandle className="resize-h" />
      <Panel defaultSize={46} minSize={30} className="panel">
        <PanelGroup direction="vertical" autoSaveId="hook-center-v">
          <Panel defaultSize={36} minSize={18} className="panel">
            <HookLabCodeView
              appId={appId}
              selected={selected}
              onSourceLoaded={onSourceLoaded}
            />
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
              attachments={chatAttachments}
              contextSummary={buildHookChatContextSummary({
                selected,
                hasSource: selectedSource != null,
                activeSession,
                hooks: chatHooks,
                traceTail: chatTraceTail,
              })}
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
  /** Notifies the parent when the loaded Java source changes so it can
   *  forward the same text to the chat ``code`` attachment without
   *  re-fetching. ``null`` means "no source available" (no selection,
   *  load error, or path missing in the cache). */
  onSourceLoaded?: (source: string | null) => void;
};

function HookLabCodeView({ appId, selected, onSourceLoaded }: HookLabCodeViewProps) {
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

// ---------------------------------------------------------------------------
// Chat-attachment plumbing (sub-step 4.7).
//
// The Hook Lab chat dock is the LLM's eye into the operator's current
// pentest state. We forward four kinds of context as ChatAttachment[] so
// the model can answer questions like "which hook should I add next?" or
// "did the bypass actually fire?":
//
//   1. ``default`` — selected method header (class + method + smali id).
//      Tiny, always-on, free in any per-kind budget.
//   2. ``code`` — the decompiled Java file for the selected method, soft-
//      capped at CHAT_CODE_BUDGET so the operator's "show context" preview
//      mirrors what the model sees after backend truncation
//      (``ATTACHMENT_BUDGETS["code"]`` == 6_000).
//   3. ``frida_summary`` — JSON document combining the active session's
//      ``hooks`` aggregate (per-(class, method) hits + top return values)
//      and the last-N trace events tail. Backend ATTACHMENT_BUDGETS already
//      has a 4_000-char slot for this kind (see DEC-022 / ChatDock).
//   4. ``default`` — meta footer (active session id / template id / app
//      id) so the model can disambiguate which app it's analysing without
//      relying on chat history.
//
// Empty attachments are omitted entirely so the operator's "show context"
// dot accurately reflects whether the model will see Hook Lab data.
// ---------------------------------------------------------------------------

type BuildAttachmentsArgs = {
  appId: string | null;
  selected: SelectedNode | null;
  selectedSource: string | null;
  activeSession: ActiveSession | null;
  hooks: HookStat[] | null;
  traceTail: TraceEvent[] | null;
};

function buildHookChatAttachments({
  appId,
  selected,
  selectedSource,
  activeSession,
  hooks,
  traceTail,
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
};

function buildHookChatContextSummary({
  selected,
  hasSource,
  activeSession,
  hooks,
  traceTail,
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
