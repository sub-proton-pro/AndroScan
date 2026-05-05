import type { ChatAttachment, ChatMessage, ChatWidget, TabId } from "../types";

export type ChatSendArgs = {
  tab: TabId;
  prompt: string;
  history: ChatMessage[];
  attachments: ChatAttachment[];
  appId: string | null;
  runTs: string | null;
  // Phase 11 v2.1 sub-step v2.1.5 — DEC-022 + DEC-025 v2.1 closing-note
  // Q7. Opt into the bounded agentic-skill loop. v2.1.5 sets this for
  // the lab tab (where the chat-widget pattern is most useful);
  // other tabs default to the legacy single-pass path so existing
  // behaviour is unchanged.
  agenticLoop?: boolean;
};

export type ChatResponse =
  | { ok: true; reply: string; trims: unknown[]; elapsed_ms: number; transcript_path: string | null }
  | { ok: false; error: string; retry_after_seconds?: number };

function buildPayload(args: ChatSendArgs) {
  return {
    tab: args.tab,
    prompt: args.prompt,
    history: args.history.map((m) => ({ role: m.role, text: m.text })),
    attachments: args.attachments,
    app_id: args.appId,
    run_ts: args.runTs,
    agentic_loop: args.agenticLoop ?? false,
  };
}

export async function sendChat(args: ChatSendArgs): Promise<ChatResponse> {
  const r = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildPayload(args)),
  });
  const body = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }));
  return body as ChatResponse;
}

// ---------------------------------------------------------------------------
// SSE-based streaming chat
//
// We can't use the browser's built-in EventSource because it only supports
// GET. Instead we POST and parse the SSE byte stream by hand: split on the
// double-newline frame separator, then split each frame's "event:" /
// "data:" lines. Cancellation is handled via AbortSignal.

// v2.1.5 — agentic-loop SSE event payloads. The frontend renders
// skill calls inline on the assistant message that triggered them
// (per ChatSkillCall in types.ts) and dispatches widgets through
// <ChatWidgetRenderer> by widget.kind.
export type ChatStreamSkillRequest = {
  request_id: string;
  skill: string;
  params: Record<string, unknown>;
};

export type ChatStreamSkillResult = {
  request_id: string;
  skill: string;
  success: boolean;
  text: string;
};

export type ChatStreamSkillPending = {
  request_id: string;
  skill: string;
  params: Record<string, unknown>;
  reason: string;
};

export type ChatStreamWidget = {
  request_id: string;
  skill: string;
  widget: ChatWidget;
};

export type ChatStreamCallbacks = {
  onThinking?: (delta: string) => void;
  onContent?: (delta: string) => void;
  onDone?: (info: ChatStreamDone) => void;
  onError?: (err: ChatStreamError) => void;
  // v2.1.5 agentic-loop callbacks. Optional — single-pass chat
  // requests never trigger these so existing callers (Reports / Inspect
  // tabs that haven't migrated) don't need to handle them.
  onSkillRequest?: (info: ChatStreamSkillRequest) => void;
  onSkillResult?: (info: ChatStreamSkillResult) => void;
  onSkillPending?: (info: ChatStreamSkillPending) => void;
  onWidget?: (info: ChatStreamWidget) => void;
};

export type ChatStreamDone = {
  trims: unknown[];
  elapsed_ms: number;
  transcript_path: string | null;
  done_reason: string | null;
  thinking_chars: number;
  content_chars: number;
  agentic?: boolean;
  skill_calls?: number;
  widgets?: number;
};

export type ChatStreamError = {
  error: string;
  retry_after_seconds?: number;
};

type SseEvent = { event: string; data: string };

function parseFrame(frame: string): SseEvent | null {
  // A frame is one or more lines; we only care about ``event:`` and ``data:``.
  // Multi-line ``data:`` is concatenated with newlines per the SSE spec, but
  // our backend always emits a single JSON line, so simple concatenation is
  // safe.
  let event = "message";
  const dataLines: string[] = [];
  for (const raw of frame.split("\n")) {
    if (!raw || raw.startsWith(":")) continue;
    const idx = raw.indexOf(":");
    const field = idx === -1 ? raw : raw.slice(0, idx);
    let value = idx === -1 ? "" : raw.slice(idx + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

/** Stream a chat turn. Resolves once the server closes the stream. */
export async function streamChat(
  args: ChatSendArgs,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  let r: Response;
  try {
    r = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(buildPayload(args)),
      signal,
    });
  } catch (e) {
    if ((e as DOMException)?.name === "AbortError") return;
    callbacks.onError?.({ error: (e as Error).message || "network error" });
    return;
  }

  if (!r.ok || !r.body) {
    callbacks.onError?.({ error: `HTTP ${r.status}` });
    return;
  }

  const reader = r.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let sawTerminal = false;

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line. Process all complete frames
      // currently in the buffer; keep the trailing partial for next read.
      let sepIdx: number;
      while ((sepIdx = buffer.indexOf("\n\n")) !== -1) {
        const rawFrame = buffer.slice(0, sepIdx);
        buffer = buffer.slice(sepIdx + 2);
        const evt = parseFrame(rawFrame);
        if (!evt) continue;

        let parsed: unknown;
        try {
          parsed = JSON.parse(evt.data);
        } catch {
          continue;
        }
        const obj = parsed as Record<string, unknown>;

        if (evt.event === "thinking") {
          const delta = typeof obj.delta === "string" ? obj.delta : "";
          if (delta) callbacks.onThinking?.(delta);
        } else if (evt.event === "content") {
          const delta = typeof obj.delta === "string" ? obj.delta : "";
          if (delta) callbacks.onContent?.(delta);
        } else if (evt.event === "skill_request") {
          callbacks.onSkillRequest?.({
            request_id: (obj.request_id as string) ?? "",
            skill: (obj.skill as string) ?? "",
            params: (obj.params as Record<string, unknown>) ?? {},
          });
        } else if (evt.event === "skill_result") {
          callbacks.onSkillResult?.({
            request_id: (obj.request_id as string) ?? "",
            skill: (obj.skill as string) ?? "",
            success: Boolean(obj.success),
            text: (obj.text as string) ?? "",
          });
        } else if (evt.event === "skill_pending") {
          callbacks.onSkillPending?.({
            request_id: (obj.request_id as string) ?? "",
            skill: (obj.skill as string) ?? "",
            params: (obj.params as Record<string, unknown>) ?? {},
            reason: (obj.reason as string) ?? "",
          });
        } else if (evt.event === "widget") {
          // The widget payload mirrors the backend's ``SkillWidget``
          // dataclass shape (kind + per-kind fields). The dispatcher
          // <ChatWidgetRenderer> ignores unknown kinds so a server /
          // client version skew is non-fatal — operator never sees a
          // broken render, just no widget for that one event.
          const widget = obj.widget as ChatWidget | undefined;
          if (widget && typeof widget.kind === "string") {
            callbacks.onWidget?.({
              request_id: (obj.request_id as string) ?? "",
              skill: (obj.skill as string) ?? "",
              widget,
            });
          }
        } else if (evt.event === "done") {
          sawTerminal = true;
          callbacks.onDone?.({
            trims: (obj.trims as unknown[]) ?? [],
            elapsed_ms: (obj.elapsed_ms as number) ?? 0,
            transcript_path: (obj.transcript_path as string | null) ?? null,
            done_reason: (obj.done_reason as string | null) ?? null,
            thinking_chars: (obj.thinking_chars as number) ?? 0,
            content_chars: (obj.content_chars as number) ?? 0,
            agentic: (obj.agentic as boolean | undefined),
            skill_calls: (obj.skill_calls as number | undefined),
            widgets: (obj.widgets as number | undefined),
          });
        } else if (evt.event === "error") {
          sawTerminal = true;
          callbacks.onError?.({
            error: (obj.error as string) ?? "unknown error",
            retry_after_seconds: obj.retry_after_seconds as number | undefined,
          });
        }
      }
    }
  } catch (e) {
    if ((e as DOMException)?.name !== "AbortError" && !sawTerminal) {
      callbacks.onError?.({ error: (e as Error).message || "stream error" });
    }
  }

  if (!sawTerminal) {
    // Server closed without a terminal frame — surface a generic error so
    // the UI doesn't keep showing the "Thinking…" indicator forever.
    callbacks.onError?.({ error: "stream ended without completion" });
  }
}
