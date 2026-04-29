export type Project = { app_id: string };
export type Run = { run_timestamp: string };

export type Hypothesis = {
  id?: string;
  component_type?: string;
  component_name?: string;
  title?: string;
  description?: string;
  severity?: string;
  confidence?: string;
  exploitability?: string;
  evidence_refs?: string[];
  verified?: boolean;
  verification_reasoning?: string;
  verification_artifact_dir?: string;
  [k: string]: unknown;
};

export type Report = {
  summary?: string;
  hypotheses?: Hypothesis[];
  [k: string]: unknown;
};

// Phase 10 sub-step 10.6: ``"hook"`` was renamed to ``"lab"`` to reflect
// the broader scope (Trace mode + Manual Hooks mode + Graph mode under one
// umbrella). The chat backend still accepts ``"hook"`` as a back-compat
// alias for legacy transcripts; the frontend has dropped the literal so no
// new code is allowed to reintroduce the old id. Bookmarks of ``#/hook``
// are auto-redirected to ``#/lab`` in ``WorkbenchContext.tabFromHash``.
export type TabId = "reports" | "inspect" | "lab" | "settings";

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  ts: number;
  // Streaming-only fields (assistant messages):
  thinking?: string;
  isStreaming?: boolean;
};

export type ChatAttachment = {
  kind: string;
  name: string;
  text: string;
};

export type TriageStatus =
  | "confirmed"
  | "false_positive"
  | "suppressed"
  | "needs_review";

export type SeverityOverride =
  | "critical"
  | "high"
  | "medium"
  | "low"
  | "informational"
  | null;

export type TriageEntry = {
  finding_id: string;
  status?: TriageStatus;
  severity_override?: SeverityOverride;
  note?: string;
  actor?: string;
  ts?: string;
};

export type TriageMap = Record<string, TriageEntry>;

export type Dossier = {
  apk_info?: { package?: string; version_name?: string; [k: string]: unknown };
  exported_activities?: Array<{ name?: string; [k: string]: unknown }>;
  exported_services?: Array<{ name?: string; [k: string]: unknown }>;
  exported_receivers?: Array<{ name?: string; [k: string]: unknown }>;
  exported_providers?: Array<{ name?: string; [k: string]: unknown }>;
  deep_links?: unknown[];
  [k: string]: unknown;
};
