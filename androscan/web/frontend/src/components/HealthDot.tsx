/**
 * Tiny coloured dot rendered in the global header.
 *
 * Polls ``/api/status/global`` every 30s and reduces the response to a
 * single tri-state (``green | yellow | red``) via ``rollupGlobal``.
 * Clicking the dot deep-links into the Settings tab so the user can see
 * exactly which probe is unhappy.
 */

import { useEffect, useState } from "react";
import { fetchGlobalStatus, rollupGlobal } from "../api/status";

const POLL_MS = 30_000;

export function HealthDot({ onClick }: { onClick?: () => void }) {
  const [color, setColor] = useState<"green" | "yellow" | "red" | "gray">("gray");
  const [tip, setTip] = useState<string>("checking…");

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const tick = async () => {
      const r = await fetchGlobalStatus();
      if (cancelled) return;
      if (!r.ok) {
        setColor("red");
        setTip(`status fetch failed: ${r.error}`);
      } else {
        const c = rollupGlobal(r.data);
        setColor(c);
        const issues: string[] = [];
        if (!r.data.llm.ok) issues.push(`LLM: ${r.data.llm.error ?? "down"}`);
        if (!r.data.tools.adb.ok) issues.push("adb missing");
        if (!r.data.tools.jadx.ok) issues.push("jadx missing");
        if (!r.data.tools.apktool.ok) issues.push("apktool missing");
        if (!r.data.tools.frida.ok) issues.push("frida missing (Hook Lab)");
        if (!r.data.device.ok) {
          issues.push(
            r.data.device.connected
              ? `device state: ${r.data.device.state ?? "unknown"}`
              : "no device attached (start an emulator)"
          );
        }
        if (!r.data.rag_provider.ok) issues.push("RAG provider degraded");
        const fs = r.data.filesystem.apps_root;
        if (!fs.ok) issues.push("apps/ not writable");
        if (typeof fs.low_space === "boolean" && fs.low_space) issues.push("low disk");
        setTip(issues.length === 0 ? "All systems nominal" : issues.join("\n"));
      }
      if (!cancelled) {
        timer = window.setTimeout(tick, POLL_MS);
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  return (
    <button
      type="button"
      className={`health-dot health-dot-${color}`}
      title={tip}
      aria-label={`System health: ${color}`}
      onClick={onClick}
    >
      <span className="health-dot-inner" />
    </button>
  );
}
