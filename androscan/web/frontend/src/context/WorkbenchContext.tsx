import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { fetchTriage, postTriage, type TriageUpdate } from "../api/triage";
import type {
  ChatMessage,
  Dossier,
  Project,
  Report,
  Run,
  TabId,
  TriageEntry,
  TriageMap,
} from "../types";

const TABS: TabId[] = ["reports", "inspect", "hook", "settings"];

function tabFromHash(): TabId {
  const raw = window.location.hash.replace(/^#\/?/, "");
  return (TABS as string[]).includes(raw) ? (raw as TabId) : "reports";
}

type WorkbenchState = {
  // navigation
  tab: TabId;
  setTab: (t: TabId) => void;

  // selection (shared across tabs)
  appId: string | null;
  setAppId: (a: string | null) => void;
  runTs: string | null;
  setRunTs: (r: string | null) => void;

  // cached data
  projects: Project[];
  refreshProjects: () => Promise<void>;
  runs: Run[];
  report: Report | null;
  reportError: string | null;
  dossier: Dossier | null;
  triage: TriageMap;
  updateTriage: (
    findingId: string,
    update: TriageUpdate,
  ) => Promise<{ ok: true; entry: TriageEntry } | { ok: false; error: string }>;

  // global UI bits
  status: string;
  setStatus: (s: string) => void;

  // Cross-tab "open this file in the Code Browser" intent. The Hook Lab
  // graph pane writes here when the operator picks "Open in Inspect" on a
  // node tooltip; the Inspect tab consumes it on mount/change and clears
  // it. ``ts`` forces re-fire when the same target is requested twice in
  // a row.
  pendingCodeNav: PendingCodeNav | null;
  setPendingCodeNav: (n: PendingCodeNavInput | null) => void;

  // per-tab chat history (persisted client-side)
  chats: Record<TabId, ChatMessage[]>;
  appendChat: (tab: TabId, msg: ChatMessage) => void;
  updateChat: (
    tab: TabId,
    id: string,
    patch: Partial<ChatMessage> | ((m: ChatMessage) => Partial<ChatMessage>),
  ) => void;
  clearChat: (tab: TabId) => void;
};

export type PendingCodeNavInput = {
  appId: string;
  relPath: string;
  className: string;
  method?: string;
};

export type PendingCodeNav = PendingCodeNavInput & { ts: number };

const WorkbenchContext = createContext<WorkbenchState | null>(null);

export function WorkbenchProvider({ children }: { children: ReactNode }) {
  const [tab, setTabState] = useState<TabId>(() => tabFromHash());
  const [appId, setAppId] = useState<string | null>(null);
  const [runTs, setRunTs] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [triage, setTriage] = useState<TriageMap>({});
  const [status, setStatus] = useState("");
  const [pendingCodeNav, setPendingCodeNavState] =
    useState<PendingCodeNav | null>(null);
  const [chats, setChats] = useState<Record<TabId, ChatMessage[]>>({
    reports: [],
    inspect: [],
    hook: [],
    settings: [],
  });

  const setTab = useCallback((t: TabId) => {
    setTabState(t);
    if (window.location.hash !== `#/${t}`) {
      window.history.replaceState(null, "", `#/${t}`);
    }
  }, []);

  useEffect(() => {
    const onHash = () => setTabState(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const refreshProjects = useCallback(async () => {
    try {
      const r = await fetch("/api/projects");
      const d = await r.json();
      setProjects(d.projects || []);
    } catch {
      setStatus("Failed to load /api/projects");
    }
  }, []);

  useEffect(() => {
    refreshProjects();
  }, [refreshProjects]);

  useEffect(() => {
    if (!appId) {
      setRuns([]);
      setRunTs(null);
      return;
    }
    fetch(`/api/projects/${encodeURIComponent(appId)}/runs`)
      .then((r) => r.json())
      .then((d) => {
        const list: Run[] = d.runs || [];
        setRuns(list);
        setRunTs(list[0]?.run_timestamp ?? null);
      })
      .catch(() => setRuns([]));
  }, [appId]);

  // Load report + dossier + triage whenever (appId, runTs) changes.
  useEffect(() => {
    if (!appId || !runTs) {
      setReport(null);
      setReportError(null);
      setDossier(null);
      setTriage({});
      return;
    }
    let cancelled = false;
    fetch(`/api/findings/${encodeURIComponent(appId)}/${encodeURIComponent(runTs)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => {
        if (cancelled) return;
        setReport(d.report ?? null);
        setReportError(null);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setReport(null);
        setReportError(e.message || "load failed");
      });

    fetch(`/api/dossier/${encodeURIComponent(appId)}/${encodeURIComponent(runTs)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled) return;
        setDossier(d?.dossier ?? null);
      })
      .catch(() => {
        if (cancelled) return;
        setDossier(null);
      });

    fetchTriage(appId, runTs).then((m) => {
      if (!cancelled) setTriage(m);
    });

    return () => {
      cancelled = true;
    };
  }, [appId, runTs]);

  const updateTriage = useCallback<WorkbenchState["updateTriage"]>(
    async (findingId, update) => {
      if (!appId || !runTs) {
        return { ok: false, error: "no run selected" };
      }
      const safeId =
        typeof findingId === "string" ? findingId.trim() : "";
      if (!safeId) {
        return { ok: false, error: "missing finding id" };
      }
      const res = await postTriage(appId, runTs, safeId, update);
      if (res.ok) {
        setTriage((prev) => ({ ...prev, [safeId]: res.entry }));
      }
      return res;
    },
    [appId, runTs],
  );

  const appendChat = useCallback((t: TabId, msg: ChatMessage) => {
    setChats((prev) => ({ ...prev, [t]: [...prev[t], msg] }));
  }, []);
  const updateChat = useCallback<WorkbenchState["updateChat"]>((t, id, patch) => {
    setChats((prev) => ({
      ...prev,
      [t]: prev[t].map((m) => {
        if (m.id !== id) return m;
        const delta = typeof patch === "function" ? patch(m) : patch;
        return { ...m, ...delta };
      }),
    }));
  }, []);
  const clearChat = useCallback((t: TabId) => {
    setChats((prev) => ({ ...prev, [t]: [] }));
  }, []);

  const setPendingCodeNav = useCallback(
    (n: PendingCodeNavInput | null) => {
      setPendingCodeNavState(n ? { ...n, ts: Date.now() } : null);
    },
    [],
  );

  const value = useMemo<WorkbenchState>(
    () => ({
      tab,
      setTab,
      appId,
      setAppId,
      runTs,
      setRunTs,
      projects,
      refreshProjects,
      runs,
      report,
      reportError,
      dossier,
      triage,
      updateTriage,
      status,
      setStatus,
      pendingCodeNav,
      setPendingCodeNav,
      chats,
      appendChat,
      updateChat,
      clearChat,
    }),
    [
      tab,
      setTab,
      appId,
      runTs,
      projects,
      refreshProjects,
      runs,
      report,
      reportError,
      dossier,
      triage,
      updateTriage,
      status,
      pendingCodeNav,
      setPendingCodeNav,
      chats,
      appendChat,
      updateChat,
      clearChat,
    ],
  );

  return <WorkbenchContext.Provider value={value}>{children}</WorkbenchContext.Provider>;
}

export function useWorkbench(): WorkbenchState {
  const ctx = useContext(WorkbenchContext);
  if (!ctx) throw new Error("useWorkbench must be used inside <WorkbenchProvider>");
  return ctx;
}
