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

export type TabId = "reports" | "inspect" | "hook" | "settings";

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
