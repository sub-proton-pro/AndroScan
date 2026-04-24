import { useEffect, useRef, useState } from "react";

type Props = {
  packageName: string | null; // null → device-wide
  collapsed: boolean;
  onToggle: () => void;
};

const MAX_LINES = 500;

/**
 * Connects to ``/ws/logcat?package=<pkg>`` (or the unscoped stream when
 * ``packageName`` is null). Re-opens when the package changes.
 */
export function ScopedLogcat({ packageName, collapsed, onToggle }: Props) {
  const [lines, setLines] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const tailRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setLines([]);
    setConnected(false);
    if (collapsed) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = packageName
      ? `${proto}//${window.location.host}/ws/logcat?package=${encodeURIComponent(packageName)}`
      : `${proto}//${window.location.host}/ws/logcat`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onmessage = (ev) => {
      const text = typeof ev.data === "string" ? ev.data : "";
      if (!text) return;
      setLines((prev) => {
        const next = [...prev, text];
        if (next.length > MAX_LINES) next.splice(0, next.length - MAX_LINES);
        return next;
      });
    };
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [packageName, collapsed]);

  useEffect(() => {
    const el = tailRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  const visible = filter
    ? lines.filter((l) => l.toLowerCase().includes(filter.toLowerCase()))
    : lines;

  return (
    <section className={collapsed ? "logcat collapsed" : "logcat"}>
      <header className="logcat-head">
        <button
          type="button"
          className="logcat-toggle-btn"
          onClick={onToggle}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand logcat" : "Collapse logcat"}
          title={collapsed ? "Expand logcat" : "Collapse logcat"}
        >
          {collapsed ? "+" : "−"}
        </button>
        <h3>logcat</h3>
        <span className="muted small">
          {packageName ? `pkg: ${packageName}` : "device-wide"}
        </span>
        <span
          className={connected ? "logcat-dot ok" : "logcat-dot err"}
          aria-hidden
          title={connected ? "stream connected" : "stream disconnected"}
        />
        {!collapsed && (
          <input
            type="search"
            placeholder="filter…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="filter-input small-input"
          />
        )}
        {!collapsed && (
          <button
            type="button"
            className="ghost-mini"
            onClick={() => setLines([])}
            disabled={lines.length === 0}
          >
            clear
          </button>
        )}
      </header>
      {!collapsed && (
        <div ref={tailRef} className="logcat-body">
          {visible.length === 0 && (
            <p className="muted small">
              {connected ? "(waiting for log lines…)" : "(disconnected)"}
            </p>
          )}
          {visible.map((line, i) => (
            <pre key={i} className={lineClass(line)}>{line}</pre>
          ))}
        </div>
      )}
    </section>
  );
}

function lineClass(line: string): string {
  if (line.startsWith("# androscan")) return "logcat-meta";
  // Heuristic: classic logcat -v time priority is " V/" / " D/" / " I/" etc.
  if (/\sE\//.test(line) || / E /.test(line)) return "logcat-line err";
  if (/\sW\//.test(line) || / W /.test(line)) return "logcat-line warn";
  if (/\sI\//.test(line) || / I /.test(line)) return "logcat-line info";
  return "logcat-line";
}
