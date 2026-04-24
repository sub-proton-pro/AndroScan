/**
 * "Bring device online" wizard.
 *
 * Rendered inside MirrorView when the device probe reports offline. Walks
 * the user through:
 *
 *   1. Pick / confirm an AVD             (synchronous, just the dropdown)
 *   2. Start the emulator                (POST /api/device/emulator/start)
 *   3. Wait for adb to see ``state=device`` (poll /api/device/status)
 *   4. Check installed + install if needed (POST /api/device/install_and_launch)
 *   5. Launch the app                    (same endpoint, second pass)
 *
 * Each step renders as a row with a coloured dot, a label, and (when
 * active) a shimmer animation defined in App.css. Errors surface inline
 * on the failed step so the user knows what to fix.
 *
 * Steps 4 and 5 are gated on having an ``appId`` selected — without one
 * we still help the user boot the emulator and stop there.
 */

import { useEffect, useState } from "react";
import {
  installAndLaunch,
  listAvds,
  startEmulator,
  waitForDeviceOnline,
  type InstallLaunchStep,
} from "../api/device";

export type StepKey =
  | "emulator"
  | "wait_online"
  | "check_installed"
  | "install"
  | "launch";

type StepStatus = "pending" | "active" | "done" | "failed" | "skipped";

type StepRow = {
  key: StepKey;
  label: string;
  status: StepStatus;
  detail?: string;
  error?: string;
};

const INITIAL_STEPS: StepRow[] = [
  { key: "emulator",        label: "Start emulator",       status: "pending" },
  { key: "wait_online",     label: "Wait for device",      status: "pending" },
  { key: "check_installed", label: "Check app installed",  status: "pending" },
  { key: "install",         label: "Install app (if needed)", status: "pending" },
  { key: "launch",          label: "Launch app",           status: "pending" },
];

export function DeviceBoot({
  appId,
  detail,
  onRunStart,
  onComplete,
  onDismiss,
}: {
  appId: string | null;
  /** Free-text from /api/device/status to show below the title (e.g. "offline"). */
  detail?: string;
  /** Fired when the user clicks "Bring device online" and the wizard begins.
   *  Lets the parent lock the wizard open even after the device transitions
   *  to ``online`` mid-run so the install/launch steps remain visible. */
  onRunStart?: () => void;
  /** Called after the wizard finishes successfully (so the parent can dismiss). */
  onComplete?: () => void;
  /** Called when the user clicks the close button. The parent decides
   *  whether to actually unmount or just collapse the overlay. */
  onDismiss?: () => void;
}) {
  const [avds, setAvds] = useState<string[]>([]);
  const [avdsError, setAvdsError] = useState<string | null>(null);
  const [selectedAvd, setSelectedAvd] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<StepRow[]>(INITIAL_STEPS);
  const [finalMsg, setFinalMsg] = useState<string | null>(null);

  // Load AVD inventory once on mount so the dropdown is populated before
  // the user clicks "Start". Reload on demand via the refresh button.
  useEffect(() => {
    void refreshAvds();
  }, []);

  async function refreshAvds() {
    setAvdsError(null);
    const r = await listAvds();
    if (!r.ok) {
      setAvds([]);
      setAvdsError(r.error ?? "no AVDs");
      return;
    }
    setAvds(r.avds);
    setSelectedAvd((prev) => prev || r.avds[0] || "");
  }

  function patch(key: StepKey, change: Partial<StepRow>) {
    setSteps((prev) =>
      prev.map((s) => (s.key === key ? { ...s, ...change } : s)),
    );
  }

  function reset() {
    setSteps(INITIAL_STEPS);
    setFinalMsg(null);
  }

  async function run() {
    if (running) return;
    if (!selectedAvd) {
      setFinalMsg("Pick an AVD first.");
      return;
    }
    reset();
    setRunning(true);
    onRunStart?.();

    // Step 1: spawn emulator (detached).
    patch("emulator", { status: "active", detail: `avd: ${selectedAvd}` });
    const spawn = await startEmulator(selectedAvd);
    if (!spawn.ok) {
      patch("emulator", { status: "failed", error: spawn.error ?? "spawn failed" });
      setRunning(false);
      setFinalMsg("Could not start the emulator.");
      return;
    }
    patch("emulator", {
      status: "done",
      detail: spawn.pid ? `pid ${spawn.pid}` : undefined,
    });

    // Step 2: poll device status.
    patch("wait_online", {
      status: "active",
      detail: "this can take 30-90s on a cold boot",
    });
    const online = await waitForDeviceOnline(120_000);
    if (!online) {
      patch("wait_online", {
        status: "failed",
        error: "device did not come online within 2 minutes",
      });
      setRunning(false);
      setFinalMsg("Emulator started but adb still doesn't see it.");
      return;
    }
    patch("wait_online", { status: "done" });

    // Steps 3-5: install + launch (gated on app selection).
    if (!appId) {
      patch("check_installed", { status: "skipped", detail: "no app selected" });
      patch("install",         { status: "skipped", detail: "no app selected" });
      patch("launch",          { status: "skipped", detail: "no app selected" });
      setRunning(false);
      setFinalMsg("Device is online. Pick an app to install/launch it.");
      onComplete?.();
      return;
    }

    // We render the next three rows in their own active phases by issuing
    // one combined request and then mapping its per-step results back.
    patch("check_installed", { status: "active" });
    const r = await installAndLaunch(appId, { install: true, launch: true });

    const byKey: Record<string, InstallLaunchStep | undefined> = {};
    for (const s of r.steps) byKey[s.key] = s;

    // check_installed
    {
      const s = byKey.check_installed;
      if (!s) {
        patch("check_installed", { status: "failed", error: r.error ?? "no result" });
      } else if (s.error && !s.installed) {
        // pm path failed (e.g. transient adb issue); not fatal — install
        // step will reveal the real situation.
        patch("check_installed", { status: "done", detail: "not installed" });
      } else {
        patch("check_installed", {
          status: "done",
          detail: s.installed ? "installed" : "not installed",
        });
      }
    }

    // install
    {
      const s = byKey.install;
      if (!s) {
        patch("install", { status: "failed", error: r.error ?? "no result" });
        setRunning(false);
        setFinalMsg("Install step did not run.");
        return;
      }
      if (s.skipped) {
        patch("install", { status: "skipped", detail: s.reason });
      } else if (s.ok) {
        patch("install", { status: "done", detail: "installed via adb install -r" });
      } else {
        patch("install", { status: "failed", error: s.error ?? "install failed" });
        setRunning(false);
        setFinalMsg("App could not be installed.");
        return;
      }
    }

    // launch
    {
      const s = byKey.launch;
      patch("launch", { status: "active" });
      if (!s) {
        patch("launch", { status: "failed", error: r.error ?? "no result" });
      } else if (s.skipped) {
        patch("launch", { status: "skipped", detail: s.reason });
      } else if (s.ok) {
        patch("launch", { status: "done" });
      } else {
        patch("launch", { status: "failed", error: s.error ?? "launch failed" });
      }
    }

    setRunning(false);
    setFinalMsg(r.ok ? "Device online and app launched." : null);
    if (r.ok) onComplete?.();
  }

  const startDisabled =
    running || (avds.length === 0 && !selectedAvd) || !selectedAvd;

  return (
    <div className="device-boot" role="region" aria-label="Bring device online">
      <header className="device-boot-head">
        <strong>Device offline</strong>
        {detail && <span className="muted small">({detail})</span>}
        {onDismiss && (
          // The parent (MirrorView) overlays this card on top of the live
          // mirror once the device comes online. The close button lets the
          // user reclaim the full mirror view at any time, including after
          // a partial failure where the wizard is no longer auto-closing.
          <button
            type="button"
            className="ghost-mini device-boot-close"
            onClick={onDismiss}
            title="Hide wizard"
            aria-label="Hide wizard"
          >
            ×
          </button>
        )}
      </header>

      <div className="device-boot-controls">
        <label className="device-boot-label" htmlFor="avd-select">AVD:</label>
        <select
          id="avd-select"
          value={selectedAvd}
          onChange={(e) => setSelectedAvd(e.target.value)}
          disabled={running || avds.length === 0}
        >
          {avds.length === 0 && <option value="">(none)</option>}
          {avds.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <button
          type="button"
          className="ghost-mini"
          onClick={() => void refreshAvds()}
          disabled={running}
          title="Reload AVD list"
        >
          ↻
        </button>
        <button
          type="button"
          className="primary"
          onClick={() => void run()}
          disabled={startDisabled}
        >
          {running ? "Working…" : "Bring device online"}
        </button>
      </div>

      {avdsError && (
        <p className="muted small device-boot-warn">{avdsError}</p>
      )}

      <ol className="device-boot-steps" aria-label="Boot steps">
        {steps.map((s) => (
          <StepRowView key={s.key} step={s} />
        ))}
      </ol>

      {finalMsg && (
        <p className="device-boot-final muted small">{finalMsg}</p>
      )}
    </div>
  );
}

function StepRowView({ step }: { step: StepRow }) {
  const className = `step-row step-row-${step.status}${
    step.status === "active" ? " active" : ""
  }`;
  const icon =
    step.status === "done"    ? "✓"
    : step.status === "failed" ? "✕"
    : step.status === "skipped" ? "—"
    : step.status === "active"  ? "•"
    :                              "·";
  return (
    <li className={className}>
      <span className="step-row-icon" aria-hidden>{icon}</span>
      <span className="step-row-label">{step.label}</span>
      {step.detail && <span className="step-row-detail muted small">{step.detail}</span>}
      {step.error && <span className="step-row-error">{step.error}</span>}
    </li>
  );
}
