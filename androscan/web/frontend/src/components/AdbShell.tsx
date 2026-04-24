import { useEffect, useRef, useState } from "react";
import { getDeviceStatus, type DeviceStatus } from "../api/device";

type Entry = {
  cmd: string;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  truncated: boolean;
  ts: number;
  err?: string;
};

type Props = {
  collapsed?: boolean;
  onToggle?: () => void;
};

const STATUS_POLL_MS = 4000;
const HISTORY_LIMIT = 50;

/**
 * Minimal ``adb shell`` runner. Single-line input, scrollback buffer,
 * disabled when the device is offline. Server enforces argv parsing,
 * timeout and an irreversible-command denylist.
 *
 * Renders header-only when ``collapsed`` so the parent ``Panel`` can be
 * shrunk to a thin bar (mirrors the logcat collapse pattern).
 */
export function AdbShell({ collapsed = false, onToggle }: Props) {
  const [device, setDevice] = useState<DeviceStatus>({
    online: false,
    state: "…",
    detail: "",
  });
  const [draft, setDraft] = useState("");
  const [history, setHistory] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);
  const tailRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const s = await getDeviceStatus();
      if (!cancelled) setDevice(s);
    };
    tick();
    const id = window.setInterval(tick, STATUS_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    const el = tailRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history.length, busy]);

  const run = async () => {
    const cmd = draft.trim();
    if (!cmd || busy) return;
    setBusy(true);
    try {
      const r = await fetch("/api/adb/shell", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: cmd }),
      });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try {
          const j = await r.json();
          if (typeof j?.detail === "string") detail = j.detail;
        } catch {
          /* ignore */
        }
        appendEntry({
          cmd,
          exit_code: null,
          stdout: "",
          stderr: "",
          truncated: false,
          ts: Date.now(),
          err: detail,
        });
      } else {
        const j = await r.json();
        appendEntry({
          cmd,
          exit_code: j.exit_code ?? null,
          stdout: typeof j.stdout === "string" ? j.stdout : "",
          stderr: typeof j.stderr === "string" ? j.stderr : "",
          truncated: !!j.truncated,
          ts: Date.now(),
        });
      }
      setDraft("");
    } catch (e) {
      appendEntry({
        cmd,
        exit_code: null,
        stdout: "",
        stderr: "",
        truncated: false,
        ts: Date.now(),
        err: e instanceof Error ? e.message : "network error",
      });
    } finally {
      setBusy(false);
    }
  };

  const appendEntry = (e: Entry) => {
    setHistory((prev) => {
      const next = [...prev, e];
      if (next.length > HISTORY_LIMIT) next.splice(0, next.length - HISTORY_LIMIT);
      return next;
    });
  };

  return (
    <section className={collapsed ? "adb-shell collapsed" : "adb-shell"}>
      <header className="adb-head">
        {onToggle && (
          <button
            type="button"
            className="logcat-toggle-btn"
            onClick={onToggle}
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand adb shell" : "Collapse adb shell"}
            title={collapsed ? "Expand adb shell" : "Collapse adb shell"}
          >
            {collapsed ? "+" : "−"}
          </button>
        )}
        <h3>adb shell</h3>
        <span
          className={`status-dot ${device.online ? "ok" : "err"}`}
          aria-hidden
          title={
            device.online
              ? "device online"
              : `device offline (${device.state})`
          }
        />
        {!collapsed && (
          <button
            type="button"
            className="ghost-mini"
            onClick={() => setHistory([])}
            disabled={history.length === 0}
          >
            clear
          </button>
        )}
      </header>

      {!collapsed && (
        <>
          <div ref={tailRef} className="adb-body">
            {history.length === 0 ? (
              <p className="muted small adb-hint">
                Type any <code>adb shell</code> argv (e.g.{" "}
                <code>pm list packages -3</code>,{" "}
                <code>dumpsys activity top</code>). Pipes / redirects are not
                invoked. Irreversible commands (reboot, wipe, remount,
                fastboot) are blocked.
              </p>
            ) : (
              history.map((h, i) => (
                <div key={i} className="adb-entry">
                  <div className="adb-cmd">
                    <span className="adb-prompt">$</span>{" "}
                    <code>adb shell {h.cmd}</code>
                    {h.exit_code !== null && h.exit_code !== 0 && (
                      <span className="adb-rc err"> exit {h.exit_code}</span>
                    )}
                    {h.truncated && (
                      <span className="muted small"> (output truncated)</span>
                    )}
                  </div>
                  {h.err && <pre className="adb-err">{h.err}</pre>}
                  {h.stdout && <pre className="adb-out">{h.stdout}</pre>}
                  {h.stderr && <pre className="adb-err">{h.stderr}</pre>}
                </div>
              ))
            )}
          </div>

          <form
            className="adb-input-row"
            onSubmit={(e) => {
              e.preventDefault();
              void run();
            }}
          >
            <span className="adb-prompt" aria-hidden>
              $
            </span>
            <input
              type="text"
              className="adb-input"
              placeholder={
                device.online
                  ? "adb shell <argv>… (Enter to run)"
                  : "device offline"
              }
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={busy || !device.online}
              spellCheck={false}
              autoComplete="off"
            />
            <button
              type="submit"
              className="ghost-mini"
              disabled={
                busy || !device.online || draft.trim().length === 0
              }
            >
              {busy ? "…" : "run"}
            </button>
          </form>
        </>
      )}
    </section>
  );
}
