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

// Phase 11 v2.1 sub-step v2.1.5 — DEC-022 + DEC-025 v2.1 closing-note
// Q7. The bounded agentic-skill loop on the chat backend emits new
// ``skill_request`` / ``skill_result`` / ``skill_pending`` / ``widget``
// SSE events. Each call (skill invocation) is recorded inline on the
// assistant message that triggered it, alongside the streamed content
// and any LLM-emitted interactive widgets.
//
// ``ChatSkillCall.status``:
//   * ``"running"``  – emitted on ``skill_request`` while we wait for
//     the matching ``skill_result``.
//   * ``"success"``  – emitted on ``skill_result`` with success=true.
//   * ``"failed"``   – emitted on ``skill_result`` with success=false.
//   * ``"pending"``  – emitted on ``skill_pending`` (consent-class
//     skills; chat consent UI is still pending — ISSUE-009).
export type ChatSkillCallStatus = "running" | "success" | "failed" | "pending";

export type ChatSkillCall = {
  request_id: string;
  skill: string;
  params: Record<string, unknown>;
  status: ChatSkillCallStatus;
  text?: string;
  reason?: string;
};

// ``ChatWidget`` is a typed union mirroring the backend's
// ``SkillWidget``; future widget kinds add as new union members
// without breaking the renderer (the ``<ChatWidgetRenderer>``
// dispatcher gracefully handles unknown ``kind``s by rendering only
// the assistant text). v2.1.5 ships ONE kind:
// ``trace_entry_candidate`` — the first consumer of the chat-widget
// pattern (DEC-025 v2.1 closing-note Q7).
export type TraceEntryCandidateWidgetData = {
  kind: "trace_entry_candidate";
  smali_id: string;
  rationale: string;
  confidence: number;
};

export type ChatWidget = TraceEntryCandidateWidgetData;

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  ts: number;
  // Streaming-only fields (assistant messages):
  thinking?: string;
  isStreaming?: boolean;
  // v2.1.5 agentic-loop fields (assistant messages only — set when
  // the chat backend ran the bounded agentic loop and emitted skill
  // calls / widgets along the way).
  skill_calls?: ChatSkillCall[];
  widgets?: ChatWidget[];
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
