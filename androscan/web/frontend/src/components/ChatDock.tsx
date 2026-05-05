import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { streamChat } from "../api/chat";
import { getLlmInfo, type LlmInfo } from "../api/llm";
import { useWorkbench } from "../context/WorkbenchContext";
import type { ChatAttachment, TabId } from "../types";
import { IconChevronDown } from "./Icons";

const MAX_PROMPT_CHARS = 8000;

type Props = {
  tab: TabId;
  attachments: ChatAttachment[];
  contextSummary?: string;
  /** Optional: render a collapse button in the chat header. Mirrors the
   *  Projects sidebar / Mirror panel pattern so the parent can shrink the
   *  chat dock to a horizontal rail at the bottom of its column. */
  onCollapse?: () => void;
};

const KIND_LABELS: Record<string, string> = {
  default: "Selection",
  dossier: "App dossier",
  finding: "Selected finding",
  triage: "Triage state",
  logcat: "Recent logcat",
  code: "Decompiled code",
  frida_summary: "Frida trace",
  trace: "Behavior trace",
};

function prettyKey(key: string): string {
  // snake_case / camelCase -> "Snake case"
  const withSpace = key.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
  return withSpace.charAt(0).toUpperCase() + withSpace.slice(1);
}

function renderValue(v: unknown, depth: number): string {
  const pad = "  ".repeat(depth);
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v.length > 240 ? `${v.slice(0, 240)}…` : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) {
    if (v.length === 0) return "(empty)";
    return v.map((item) => `\n${pad}- ${renderValue(item, depth + 1)}`).join("");
  }
  if (typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `\n${pad}${prettyKey(k)}: ${renderValue(val, depth + 1)}`)
      .join("");
  }
  return String(v);
}

function renderAttachment(a: ChatAttachment): string {
  const text = (a.text ?? "").trim();
  if (!text) return "(empty)";
  // Try to parse JSON-shaped payloads (findings, triage, ui_element, code lists)
  // and render them as key:value lines instead of a wall of JSON.
  const looksJson = (text.startsWith("{") || text.startsWith("[")) && text.length < 16_000;
  if (looksJson) {
    try {
      const parsed = JSON.parse(text);
      const rendered = renderValue(parsed, 0).replace(/^\n/, "");
      if (rendered) return rendered;
    } catch {
      /* fall through to raw */
    }
  }
  return text;
}

function buildPreview(attachments: ChatAttachment[]): string {
  if (attachments.length === 0) {
    return "Nothing will be attached for this turn.\n\nThe model will only see your prompt and the chat history above.";
  }
  const blocks: string[] = [
    `The following ${attachments.length} item(s) will be sent to the model along with your prompt:`,
    "",
  ];
  attachments.forEach((a, i) => {
    const heading = KIND_LABELS[a.kind] ?? prettyKey(a.kind);
    const tag = a.name && a.name !== a.kind ? ` — ${a.name}` : "";
    blocks.push(`${i + 1}. ${heading}${tag}`);
    blocks.push("─".repeat(Math.max(8, heading.length + tag.length + 4)));
    blocks.push(renderAttachment(a));
    blocks.push("");
  });
  return blocks.join("\n");
}

/**
 * Per-tab chat dock. Sends prompt + attachments + history to /api/chat which
 * applies guardrails (length cap, ANSI/secret redaction, prompt-injection
 * wrapping, allowlisted system prompts) and persists transcripts.
 */
export function ChatDock({ tab, attachments, contextSummary, onCollapse }: Props) {
  const {
    chats,
    appendChat,
    updateChat,
    clearChat,
    appId,
    runTs,
    pendingChatPrefill,
    setPendingChatPrefill,
  } = useWorkbench();
  const history = chats[tab];
  const [draft, setDraft] = useState("");
  const [showContext, setShowContext] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [llm, setLlm] = useState<LlmInfo | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  // Cancel any in-flight stream when the user switches tabs / unmounts.
  const abortRef = useRef<AbortController | null>(null);
  // Phase 11 v2.1 sub-step v2.1.4 — textarea ref for focusing on
  // ``pendingChatPrefill`` consumption. Without focus, the operator
  // would need to click the textarea before they could edit the
  // prefilled prompt — adds friction the "Ask AI" button is trying
  // to remove.
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let mounted = true;
    getLlmInfo().then((info) => {
      if (mounted) setLlm(info);
    });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history, busy]);

  // Phase 11 v2.1 sub-step v2.1.4 — consume ``pendingChatPrefill`` for
  // this tab. Writes the prefill ``message`` into ``draft``, focuses the
  // textarea, and clears the pending state so a downstream observer
  // (LabTab's chatRef.expand()) doesn't keep re-firing on every render.
  //
  // Two-consumer pattern: the parent (LabTab) ALSO observes
  // ``pendingChatPrefill`` to expand the chat panel. Both effects run
  // in the same commit cycle and close over the same pre-clear value,
  // so the order of clear-vs-expand doesn't matter — LabTab's effect
  // sees the original value via its own closure even after this
  // effect calls ``setPendingChatPrefill(null)``.
  //
  // Re-fire semantics: the dep is ``pendingChatPrefill?.ts``, so the
  // operator clicking "Ask AI" twice in a row with the same entry
  // (each click stamps a fresh ``ts``) reliably re-fires the prefill.
  // The ``if (!pendingChatPrefill) return;`` short-circuit handles
  // the post-clear render where ``ts`` flips to ``undefined``.
  useEffect(() => {
    if (!pendingChatPrefill) return;
    if (pendingChatPrefill.tab !== tab) return;
    setDraft(pendingChatPrefill.message);
    setError(null);
    // Focus + place caret at end so the operator can immediately edit
    // / extend the prefilled prompt without having to click first.
    const ta = textareaRef.current;
    if (ta) {
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }
    setPendingChatPrefill(null);
    // ``setPendingChatPrefill`` is a stable callback; intentional
    // partial deps to avoid re-firing on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingChatPrefill?.ts, tab]);

  const tooLong = draft.length > MAX_PROMPT_CHARS;
  const atLimit = draft.length >= MAX_PROMPT_CHARS;
  const hasContext = attachments.some((a) => (a.text ?? "").trim().length > 0);

  const onSend = async () => {
    const trimmed = draft.trim();
    if (!trimmed || tooLong || busy) return;

    const now = Date.now();
    const userMsg = {
      id: `${now}-u`,
      role: "user" as const,
      text: trimmed,
      ts: now,
    };
    appendChat(tab, userMsg);

    // Insert a placeholder assistant bubble immediately so streamed
    // tokens can be appended to it without waiting for the first token
    // to land. ``isStreaming`` controls the pulsing-dot indicator and
    // the auto-open state of the thinking block.
    const assistantId = `${now}-a`;
    appendChat(tab, {
      id: assistantId,
      role: "assistant",
      text: "",
      ts: now,
      thinking: "",
      isStreaming: true,
    });

    setDraft("");
    setBusy(true);
    setError(null);

    const ctrl = new AbortController();
    abortRef.current?.abort();
    abortRef.current = ctrl;

    let firstContentSeen = false;
    let terminated = false;

    await streamChat(
      { tab, prompt: trimmed, history, attachments, appId, runTs },
      {
        onThinking: (delta) => {
          updateChat(tab, assistantId, (m) => ({
            thinking: (m.thinking ?? "") + delta,
          }));
        },
        onContent: (delta) => {
          if (!firstContentSeen) {
            firstContentSeen = true;
          }
          updateChat(tab, assistantId, (m) => ({
            text: (m.text ?? "") + delta,
          }));
        },
        onDone: () => {
          terminated = true;
          updateChat(tab, assistantId, { isStreaming: false });
        },
        onError: (err) => {
          terminated = true;
          const msg = err.retry_after_seconds
            ? `${err.error} — retry in ${err.retry_after_seconds}s`
            : err.error;
          setError(msg);
          // Replace the empty placeholder with a system-style failure
          // line; keep any partial text/thinking we already received.
          updateChat(tab, assistantId, (m) => ({
            isStreaming: false,
            text: m.text
              ? `${m.text}\n\n_[stream failed: ${msg}]_`
              : `_[chat failed: ${msg}]_`,
          }));
        },
      },
      ctrl.signal,
    );

    if (!terminated) {
      // Stream was aborted (tab change / unmount). Clear the streaming
      // flag so the bubble doesn't keep the spinner forever.
      updateChat(tab, assistantId, { isStreaming: false });
    }
    setBusy(false);
    abortRef.current = null;
  };

  const previewText = contextSummary ?? buildPreview(attachments);

  return (
    <section className="chat-dock" aria-label={`Chat (${tab})`}>
      <header className="chat-head">
        {onCollapse && (
          <button
            type="button"
            className="logcat-toggle-btn chat-collapse-btn"
            onClick={onCollapse}
            aria-expanded="true"
            aria-label="Collapse chat dock"
            title="Collapse chat dock"
          >
            <IconChevronDown size={10} />
          </button>
        )}
        <h3>Chat</h3>
        <span
          className="chat-info-icon"
          tabIndex={0}
          aria-label="About this chat"
        >
          <span aria-hidden="true">i</span>
          <span className="chat-info-tooltip" role="tooltip">
            <strong>About this chat</strong>
            <span>This chat uses an LLM model preconfigured for use with AndroScan.</span>
            <span className="chat-info-kv">
              <span className="k">Provider</span>
              <span className="v">{llm?.provider ?? "—"}</span>
            </span>
            <span className="chat-info-kv">
              <span className="k">Model</span>
              <span className="v">{llm?.model ?? "loading…"}</span>
            </span>
            <span className="chat-info-kv">
              <span className="k">Endpoint</span>
              <span className="v">{llm?.base_url ?? "—"}</span>
            </span>
          </span>
        </span>
        <span className={hasContext ? "chat-ctx-dot ok" : "chat-ctx-dot muted"}>
          {hasContext ? "● context attached" : "○ no context"}
        </span>
        <button
          type="button"
          className="ghost-mini"
          onClick={() => setShowContext((s) => !s)}
          title="Show what context will be sent"
        >
          {showContext ? "hide context" : "show context"}
        </button>
        <button
          type="button"
          className="ghost-mini"
          onClick={() => clearChat(tab)}
          disabled={history.length === 0 || busy}
          title="Clear conversation"
        >
          clear
        </button>
      </header>
      {showContext && (
        <pre className="chat-context" aria-label="Context preview">
          {previewText}
        </pre>
      )}
      <div ref={listRef} className="chat-history">
        {history.map((m) => (
          <div key={m.id} className={`chat-msg chat-${m.role}`}>
            <span className="chat-role">{m.role}</span>
            <div className="chat-text">
              {m.role === "assistant" ? (
                <>
                  {m.thinking && m.thinking.length > 0 && (
                    <details
                      className="chat-thinking"
                      // Auto-open while the model is still thinking, then
                      // collapse the moment a content delta lands. After
                      // that the user fully owns the open/closed state.
                      open={Boolean(m.isStreaming) && (m.text ?? "").length === 0}
                    >
                      <summary className="chat-thinking-toggle">
                        {m.isStreaming && (m.text ?? "").length === 0
                          ? "Thinking"
                          : "Thoughts"}
                        {m.isStreaming && (m.text ?? "").length === 0 && (
                          <span className="chat-thinking-dot" aria-hidden="true">●</span>
                        )}
                      </summary>
                      <div className="chat-thinking-body">{m.thinking}</div>
                    </details>
                  )}
                  {(m.text ?? "").length > 0 ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown>
                  ) : (
                    !m.thinking &&
                    m.isStreaming && (
                      <span className="chat-stream-pending">
                        waiting for model<span className="chat-thinking-dot" aria-hidden="true">●</span>
                      </span>
                    )
                  )}
                </>
              ) : (
                <span className="chat-plain">{m.text}</span>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="chat-input-row">
        <textarea
          ref={textareaRef}
          className="chat-input"
          placeholder="Ask about the current selection… (Enter to send, Shift+Enter for newline)"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
              e.preventDefault();
              onSend();
            }
          }}
          rows={2}
          maxLength={MAX_PROMPT_CHARS + 256}
          disabled={busy}
        />
        <div className="chat-input-meta">
          <span className={`chat-counter${atLimit ? " at-limit" : ""}${tooLong ? " over" : ""}`}>
            {draft.length}/{MAX_PROMPT_CHARS}
          </span>
          {error && <span className="muted err small">{error}</span>}
          <button
            type="button"
            className="ghost"
            onClick={onSend}
            disabled={!draft.trim() || tooLong || busy}
          >
            {busy ? "…" : "Send"}
          </button>
        </div>
      </div>
    </section>
  );
}
