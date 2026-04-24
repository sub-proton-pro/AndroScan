import type { ChatAttachment, ChatMessage, TabId } from "../types";

export type ChatSendArgs = {
  tab: TabId;
  prompt: string;
  history: ChatMessage[];
  attachments: ChatAttachment[];
  appId: string | null;
  runTs: string | null;
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

export type ChatStreamCallbacks = {
  onThinking?: (delta: string) => void;
  onContent?: (delta: string) => void;
  onDone?: (info: ChatStreamDone) => void;
  onError?: (err: ChatStreamError) => void;
};

export type ChatStreamDone = {
  trims: unknown[];
  elapsed_ms: number;
  transcript_path: string | null;
  done_reason: string | null;
  thinking_chars: number;
  content_chars: number;
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
        } else if (evt.event === "done") {
          sawTerminal = true;
          callbacks.onDone?.({
            trims: (obj.trims as unknown[]) ?? [],
            elapsed_ms: (obj.elapsed_ms as number) ?? 0,
            transcript_path: (obj.transcript_path as string | null) ?? null,
            done_reason: (obj.done_reason as string | null) ?? null,
            thinking_chars: (obj.thinking_chars as number) ?? 0,
            content_chars: (obj.content_chars as number) ?? 0,
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
