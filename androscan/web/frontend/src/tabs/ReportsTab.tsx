import { useEffect, useMemo, useRef, useState } from "react";
import {
  ImperativePanelHandle,
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";
import { ChatDock } from "../components/ChatDock";
import { FindingCard } from "../components/FindingCard";
import { IconChevronRight } from "../components/Icons";
import { ProjectsSidebar } from "../components/ProjectsSidebar";
import { useWorkbench } from "../context/WorkbenchContext";
import type { ChatAttachment, Hypothesis } from "../types";

function dossierSummary(d: unknown): string {
  if (!d || typeof d !== "object") return "(no dossier)";
  const dossier = d as Record<string, unknown>;
  const apk = (dossier.apk_info ?? {}) as Record<string, unknown>;
  const counts = ([
    "exported_activities",
    "exported_services",
    "exported_receivers",
    "exported_providers",
    "deep_links",
  ] as const).map((k) => `${k}=${Array.isArray(dossier[k]) ? (dossier[k] as unknown[]).length : 0}`);
  return [
    `package: ${apk.package ?? "?"}`,
    `version: ${apk.version_name ?? "?"}`,
    counts.join(", "),
  ].join("\n");
}

function findingId(h: Hypothesis, i: number): string {
  return h.id ?? `finding-${i}`;
}

export function ReportsTab() {
  const { appId, runTs, report, reportError, dossier, triage } = useWorkbench();
  const hypotheses: Hypothesis[] = report?.hypotheses ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const sidebarRef = useRef<ImperativePanelHandle>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // Reset / auto-pick selection when the run changes.
  useEffect(() => {
    if (hypotheses.length === 0) {
      setSelectedId(null);
      return;
    }
    setSelectedId((prev) => {
      const ids = hypotheses.map(findingId);
      if (prev && ids.includes(prev)) return prev;
      return ids[0] ?? null;
    });
  }, [appId, runTs, hypotheses]);

  const selected = useMemo(() => {
    if (!selectedId) return null;
    const idx = hypotheses.findIndex((h, i) => findingId(h, i) === selectedId);
    return idx >= 0 ? hypotheses[idx] : null;
  }, [hypotheses, selectedId]);

  const attachments = useMemo<ChatAttachment[]>(() => {
    const out: ChatAttachment[] = [];
    if (appId && runTs) {
      out.push({ kind: "default", name: "selection", text: `app_id: ${appId}\nrun_ts: ${runTs}` });
    }
    if (dossier) {
      out.push({ kind: "dossier", name: "dossier_summary", text: dossierSummary(dossier) });
    }
    if (selected) {
      out.push({
        kind: "finding",
        name: selectedId ?? "selected_finding",
        text: JSON.stringify(selected, null, 2),
      });
      const t = selectedId ? triage[selectedId] : undefined;
      if (t) {
        out.push({
          kind: "triage",
          name: `triage:${selectedId}`,
          text: JSON.stringify(t, null, 2),
        });
      }
    }
    return out;
  }, [appId, runTs, dossier, selected, selectedId, triage]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const h of hypotheses) {
      const sev = String(h.severity ?? "unknown").toLowerCase();
      c[sev] = (c[sev] ?? 0) + 1;
    }
    return c;
  }, [hypotheses]);

  const triageCount = useMemo(() => Object.keys(triage).length, [triage]);

  return (
    <PanelGroup direction="horizontal" autoSaveId="reports-h" className="tab-panels">
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
      <Panel defaultSize={56} minSize={30} className="panel">
        <PanelGroup direction="vertical" autoSaveId="reports-center-v">
          <Panel defaultSize={70} minSize={20} className="panel">
            <div className="pane-scroll">
              <header className="pane-head">
                <h2>Findings</h2>
                <span className="muted small">
                  {appId && runTs
                    ? `${hypotheses.length} hypothesis(es)${
                        Object.keys(counts).length
                          ? " — " +
                            Object.entries(counts)
                              .map(([k, v]) => `${k}=${v}`)
                              .join(", ")
                          : ""
                      }${triageCount ? ` — ${triageCount} triaged` : ""}`
                    : "Select a project + run on the left."}
                </span>
              </header>
              {reportError && (
                <p className="muted small err">Failed to load report: {reportError}</p>
              )}
              <ul className="findings-skeleton">
                {hypotheses.map((h, i) => {
                  const fid = findingId(h, i);
                  return (
                    <FindingCard
                      key={fid}
                      finding={h}
                      index={i}
                      selected={selectedId === fid}
                      onSelect={() => setSelectedId(fid)}
                      triageEntry={triage[fid]}
                    />
                  );
                })}
                {hypotheses.length === 0 && appId && runTs && !reportError && (
                  <li className="muted small">No findings in report.json for this run.</li>
                )}
              </ul>
            </div>
          </Panel>
          <PanelResizeHandle className="resize-v" />
          <Panel defaultSize={30} minSize={12} collapsible className="panel chat-panel">
            <ChatDock tab="reports" attachments={attachments} />
          </Panel>
        </PanelGroup>
      </Panel>
      <PanelResizeHandle className="resize-h" />
      {/* Right-most pane intentionally left empty for now (was raw report.json). */}
      <Panel defaultSize={22} minSize={6} className="panel placeholder-panel">
        <div className="pane-scroll" />
      </Panel>
    </PanelGroup>
  );
}
