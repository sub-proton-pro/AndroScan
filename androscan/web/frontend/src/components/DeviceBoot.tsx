/**
 * "Bring device online" wizard.
 *
 * Rendered inside MirrorView when the device probe reports offline. Walks
 * the user through:
 *
 *   1. Pick / confirm an AVD             (synchronous, just the dropdown)
 *   2. Start the emulator                (POST /api/device/emulator/start)
 *   3. Wait for adb to see ``state=device`` (poll /api/device/status)
 *   4. Wait for the OS to finish booting   (poll sys.boot_completed=1)
 *   5. Check installed + install if needed (POST /api/device/install_and_launch)
 *   6. Launch the app                    (same endpoint, second pass)
 *
 * Each step renders as a row with a coloured dot, a label, and (when
 * active) a shimmer animation defined in App.css. Errors surface inline
 * on the failed step so the user knows what to fix.
 *
 * The split between steps 3 and 4 is deliberate. ``adb get-state`` flips
 * to ``device`` very early (10-20 s into a cold boot) — long before
 * ``system_server`` is up. Firing ``adb install`` or ``monkey -p`` in
 * that window deterministically races the binder transport and the user
 * sees ``monkey`` dump its argv as the only "error" (the original
 * "Check app installed fires too early" symptom). Step 4 gates everything
 * downstream on Android's canonical boot signal (``getprop sys.boot_completed
 * == 1``) so the install/launch only run once the OS is actually ready.
 *
 * Steps 5 and 6 are gated on having an ``appId`` selected — without one
 * we still help the user boot the emulator and stop there.
 */

import { useEffect, useState } from "react";
import {
  installAndLaunch,
  listAvds,
  startEmulator,
  waitForAdbDevice,
  waitForBootCompleted,
  type InstallLaunchStep,
} from "../api/device";

export type StepKey =
  | "emulator"
  | "wait_adb"
  | "wait_boot"
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
  { key: "emulator",        label: "Start emulator",          status: "pending" },
  { key: "wait_adb",        label: "Wait for adb",            status: "pending" },
  { key: "wait_boot",       label: "Wait for system boot",    status: "pending" },
  { key: "check_installed", label: "Check app installed",     status: "pending" },
  { key: "install",         label: "Install app (if needed)", status: "pending" },
  { key: "launch",          label: "Launch app",              status: "pending" },
];

export function DeviceBoot({
  appId,
  detail,
  deviceOnline = false,
  bootCompleted = false,
  onRunStart,
  onComplete,
  onDismiss,
}: {
  appId: string | null;
  /** Free-text from /api/device/status to show below the title (e.g. "offline"). */
  detail?: string;
  /** Live ``online`` flag from /api/device/status (i.e. ``adb get-state ==
   *  device``). Means ``adbd`` is reachable but says nothing about whether
   *  the OS itself has finished booting — see ``bootCompleted`` below. */
  deviceOnline?: boolean;
  /** Live ``boot_completed`` flag from /api/device/status (i.e.
   *  ``getprop sys.boot_completed == 1``). Both ``deviceOnline`` AND
   *  ``bootCompleted`` must be true for the wizard to switch into
   *  "install only" mode — otherwise the install/launch path would
   *  race the binder transport during the BOOT_COMPLETED → AM-up gap
   *  and the user would see the ``monkey`` argv-dump failure mode.
   *  When ``deviceOnline`` is true but ``bootCompleted`` is false, the
   *  wizard stays in boot mode and the install button stays disabled
   *  with a "system still booting" hint. */
  bootCompleted?: boolean;
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

  /**
   * Run the install + launch sub-steps (3-5) for ``currentAppId``. Shared
   * by both the full ``run()`` boot path (called after wait_online turns
   * green) and the targeted ``runInstallLaunch()`` path (called when the
   * device is already online and the user picks an app *after* the
   * initial wizard run finished with these steps marked "skipped").
   *
   * Returns the final user-facing message — caller handles ``setRunning``
   * + ``setFinalMsg`` + ``onComplete``. Splitting the side effects out
   * keeps the helper purely about translating ``installAndLaunch`` API
   * results into per-row patches.
   */
  async function processInstallLaunchSteps(
    currentAppId: string,
  ): Promise<{ ok: boolean; finalMsg: string | null }> {
    patch("check_installed", { status: "active", detail: undefined, error: undefined });
    const r = await installAndLaunch(currentAppId, { install: true, launch: true });

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
        return { ok: false, finalMsg: "Install step did not run." };
      }
      if (s.skipped) {
        patch("install", { status: "skipped", detail: s.reason });
      } else if (s.ok) {
        patch("install", { status: "done", detail: "installed via adb install -r" });
      } else {
        patch("install", { status: "failed", error: s.error ?? "install failed" });
        return { ok: false, finalMsg: "App could not be installed." };
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
        // Build a descriptive detail line out of the post-launch
        // diagnostics: "COLD start in 678 ms (com.example/.MainActivity)"
        // is far more useful than a bare green check, and the activity
        // is the same string the operator would put in a bug report.
        // ``via monkey`` is appended when activity resolution failed and
        // we fell back to the legacy launcher — surfacing this lets the
        // operator notice unusual environments without us hiding it.
        patch("launch", { status: "done", detail: launchDetail(s) });
      } else {
        patch("launch", { status: "failed", error: s.error ?? "launch failed" });
      }
    }

    return {
      ok: r.ok,
      finalMsg: r.ok ? "Device online and app launched." : null,
    };
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

    // Step 2: wait for adbd. Only ~10-20 s on a cold boot — adbd comes
    // up almost immediately and fires ``state=device`` long before the
    // OS itself is ready, so this is the *first* of two boot gates.
    patch("wait_adb", {
      status: "active",
      detail: "waiting for adb to see the device",
    });
    const adbReady = await waitForAdbDevice(120_000);
    if (!adbReady) {
      patch("wait_adb", {
        status: "failed",
        error: "device did not come online within 2 minutes",
      });
      setRunning(false);
      setFinalMsg("Emulator started but adb still doesn't see it.");
      return;
    }
    patch("wait_adb", { status: "done" });

    // Step 3: wait for ``sys.boot_completed=1`` — Android's canonical
    // "system_server is up and BOOT_COMPLETED has fired" signal. This
    // is the gate the original wizard was missing: without it the
    // install/launch steps below could fire into a half-up OS and
    // ``monkey`` would dump its argv as the only "error" output.
    patch("wait_boot", {
      status: "active",
      detail: "Android is finishing boot (this can take 30-90s)",
    });
    const booted = await waitForBootCompleted(180_000);
    if (!booted) {
      patch("wait_boot", {
        status: "failed",
        error: "sys.boot_completed never reached 1 within 3 minutes",
      });
      setRunning(false);
      setFinalMsg("Emulator booted on adb but Android never finished starting.");
      return;
    }
    patch("wait_boot", { status: "done" });

    // Steps 4-6: install + launch (gated on app selection).
    if (!appId) {
      patch("check_installed", { status: "skipped", detail: "no app selected" });
      patch("install",         { status: "skipped", detail: "no app selected" });
      patch("launch",          { status: "skipped", detail: "no app selected" });
      setRunning(false);
      setFinalMsg(
        "Device is online. Pick an app — then click \u201cInstall & launch app\u201d here to finish.",
      );
      onComplete?.();
      return;
    }

    const result = await processInstallLaunchSteps(appId);
    setRunning(false);
    setFinalMsg(result.finalMsg);
    if (result.ok) onComplete?.();
  }

  /**
   * Targeted install + launch run for the case where the device is
   * *already* online (so steps 1-2 don't need to re-run) and the user
   * picked an app after the wizard's initial pass marked steps 3-5 as
   * "skipped — no app selected". Without this path the wizard rows
   * stay frozen as ✓ ✓ — — — and the only way to actually get the app
   * onto the device is to manually re-boot the emulator, which is what
   * the user reported as "Check app installed permanently stuck".
   */
  async function runInstallLaunch() {
    if (running) return;
    if (!appId) return;
    setRunning(true);
    onRunStart?.();
    setFinalMsg(null);
    // Mark the boot phase as already complete so the operator can see
    // the wizard's full picture (✓ ✓ ✓ • · · → ✓ ✓ ✓ ✓ · ·) without
    // us pretending the emulator just spawned. ``detail: "already
    // running"`` is the breadcrumb that distinguishes this path from a
    // fresh boot for anyone reading the wizard mid-run. Both wait_adb
    // and wait_boot are flipped to ✓ because /api/device/status said
    // ``online`` (which now also implies ``boot_completed`` — see the
    // ``deviceOnline`` prop definition).
    patch("emulator", { status: "done", detail: "already running", error: undefined });
    patch("wait_adb", { status: "done", detail: undefined, error: undefined });
    patch("wait_boot", { status: "done", detail: undefined, error: undefined });
    patch("install", { status: "pending", detail: undefined, error: undefined });
    patch("launch", { status: "pending", detail: undefined, error: undefined });

    const result = await processInstallLaunchSteps(appId);
    setRunning(false);
    setFinalMsg(result.finalMsg);
    if (result.ok) onComplete?.();
  }

  // Mode selection — drives the primary button label and what clicking
  // it does. ``install_only`` mode kicks in once the device is online
  // *and* the OS has finished booting *and* the operator has an app
  // selected, so the wizard can act on exactly the gap that's left
  // (install/launch) without re-running the emulator boot every time.
  //
  // Critically: we require ``bootCompleted`` here, not just
  // ``deviceOnline``. During the BOOT_COMPLETED → AM-up window adbd is
  // reachable but ``ActivityManager`` isn't, and switching to
  // install-only mode prematurely would let the user click "Install &
  // launch app" right into the failure window the user originally
  // reported. Staying in boot mode with the button disabled is the
  // safer default until ``sys.boot_completed=1``.
  const mode: "boot" | "install_only" =
    deviceOnline && bootCompleted && appId ? "install_only" : "boot";

  // While the device is online but Android is still booting, the wizard
  // is between modes — it can't usefully boot (already running) and
  // can't safely install (system_server isn't up). We surface that as a
  // disabled primary with a clear tooltip rather than letting the user
  // click into a known-flaky window.
  const onlineButStillBooting = deviceOnline && !bootCompleted;

  const primaryLabel =
    running ? "Working\u2026"
    : onlineButStillBooting ? "Waiting for boot\u2026"
    : mode === "install_only" ? "Install & launch app"
    : "Bring device online";

  const primaryDisabled =
    running ||
    onlineButStillBooting ||
    (mode === "boot" && (avds.length === 0 || !selectedAvd));

  const primaryHandler = mode === "install_only" ? runInstallLaunch : run;

  // Title adapts to mode so the popover doesn't keep claiming "Device
  // offline" once the boot phase has finished and only install/launch
  // remains. The bracketed (detail) — AVD name or adb state — still
  // surfaces underneath either way for context.
  const headerTitle =
    onlineButStillBooting
      ? "Device booting"
      : mode === "install_only"
        ? "Install app"
        : deviceOnline && !appId
          ? "Device online"
          : "Device offline";

  // The AVD picker is irrelevant in install-only mode (the AVD is
  // already running) and during the boot-completion wait (same — the
  // emulator is up, just not finished booting). Keeping it mounted
  // lets the user still swap AVD if they want to *re*-boot a different
  // one — they'd cancel install-only mode by toggling the device
  // offline first.
  const avdPickerDisabled =
    running || avds.length === 0 || deviceOnline;

  return (
    <div className="device-boot" role="region" aria-label="Bring device online">
      <header className="device-boot-head">
        <strong>{headerTitle}</strong>
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
          disabled={avdPickerDisabled}
          title={deviceOnline ? "AVD already running" : undefined}
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
          disabled={running || deviceOnline}
          title="Reload AVD list"
        >
          ↻
        </button>
        <button
          type="button"
          className="primary"
          onClick={() => void primaryHandler()}
          disabled={primaryDisabled}
          title={
            onlineButStillBooting
              ? "Android is still booting (waiting for sys.boot_completed=1)"
              : mode === "install_only"
                ? "Run install + launch using the already-booted emulator"
                : !selectedAvd && avds.length > 0
                  ? "Pick an AVD first"
                  : undefined
          }
        >
          {primaryLabel}
        </button>
      </div>

      {avdsError && (
        <p className="muted small device-boot-warn">{avdsError}</p>
      )}

      {/* Hint while Android is mid-boot. We *could* let the user pick an
          app in this window but the install-only path would race the
          binder transport, so disabling the primary + telling them
          explicitly is the safer UX. */}
      {onlineButStillBooting && (
        <p className="muted small device-boot-warn">
          adb sees the device — waiting for Android to finish booting before
          install/launch will run.
        </p>
      )}

      {/* Hint when device is fully booted but no app is selected — the
          install-only path needs an appId, so without one there's
          literally nothing the wizard can do. Surfacing this inline
          is friendlier than disabling the button with no explanation. */}
      {deviceOnline && bootCompleted && !appId && (
        <p className="muted small device-boot-warn">
          Device is online — pick an app in the sidebar to install/launch it.
        </p>
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

/**
 * Render-friendly detail string for the launch step. Combines
 * ``LaunchState`` + ``TotalTime`` from ``am start -W`` with the resolved
 * activity component name so the operator gets a one-line summary of
 * what just happened. Also flags the legacy ``monkey`` fallback when it
 * was used, since that's a hint about the environment (older Android or
 * PackageManager mid-rebuild). Falls back to "running (pid …)" when the
 * timing diagnostics are missing — typical for monkey fallback paths
 * where ``am start -W``'s structured output is unavailable.
 */
function launchDetail(s: InstallLaunchStep): string {
  const parts: string[] = [];
  if (s.launch_state && s.total_time_ms != null) {
    parts.push(`${s.launch_state} start in ${s.total_time_ms} ms`);
  } else if (s.total_time_ms != null) {
    parts.push(`started in ${s.total_time_ms} ms`);
  } else if (s.pid != null) {
    parts.push(`running (pid ${s.pid})`);
  } else {
    parts.push("started");
  }
  if (s.activity) parts.push(`(${s.activity})`);
  if (s.used_monkey_fallback) parts.push("via monkey");
  return parts.join(" ");
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
