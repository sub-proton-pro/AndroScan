export type DeviceStatus = {
  online: boolean;
  state: string;
  detail: string;
  /** Human-readable name resolved by the backend — the AVD name for
   *  emulators (``Pixel_2_API_28``) or the product model for physical
   *  devices (``Pixel 6``). ``null`` when adb is offline or neither
   *  lookup resolved (e.g. multi-device-attached error). The Mirror
   *  View status pill prefers this over the raw adb ``state`` token
   *  so an operator running ``Pixel_2_API_28`` sees that name back
   *  instead of the literal ``device``. */
  device_name?: string | null;
  /** ``getprop sys.boot_completed`` on the device — Android's canonical
   *  "system_server is up and the BOOT_COMPLETED broadcast has fired"
   *  signal. ``adb get-state`` returns ``device`` *much* earlier than
   *  this (as soon as ``adbd``'s socket is reachable), so anything that
   *  talks to ``ActivityManager`` / ``PackageManager`` (i.e. the
   *  install/launch steps in the Boot wizard) must additionally gate
   *  on ``boot_completed === true`` to avoid the ``monkey`` argv-dump
   *  failure mode the user reported. ``false`` while adbd is up but
   *  the OS is still booting, or whenever ``state !== "device"``. */
  boot_completed?: boolean;
};

export async function getDeviceStatus(): Promise<DeviceStatus> {
  try {
    const r = await fetch("/api/device/status");
    if (!r.ok) {
      return {
        online: false, state: `http_${r.status}`, detail: "",
        device_name: null, boot_completed: false,
      };
    }
    return (await r.json()) as DeviceStatus;
  } catch (e) {
    return {
      online: false, state: "fetch_error", detail: String(e),
      device_name: null, boot_completed: false,
    };
  }
}

// ---------------------------------------------------------------------------
// "Bring device online" wizard endpoints (see DeviceBoot.tsx).
//
// All three return raw JSON with an ``ok`` flag (or an HTTP error). We keep
// them tiny so the React component owns the orchestration and step UI.

export type AvdsResponse = {
  ok: boolean;
  emulator_path: string | null;
  avds: string[];
  error: string | null;
};

export async function listAvds(): Promise<AvdsResponse> {
  try {
    const r = await fetch("/api/device/avds");
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      return { ok: false, emulator_path: null, avds: [],
               error: body?.detail ?? `HTTP ${r.status}` };
    }
    return (await r.json()) as AvdsResponse;
  } catch (e) {
    return { ok: false, emulator_path: null, avds: [], error: String(e) };
  }
}

export type EmulatorStartResponse = {
  ok: boolean;
  emulator_path: string | null;
  avd: string;
  pid: number | null;
  error: string | null;
};

export async function startEmulator(avd?: string): Promise<EmulatorStartResponse> {
  try {
    const r = await fetch("/api/device/emulator/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ avd: avd ?? null }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      return { ok: false, emulator_path: null, avd: avd ?? "", pid: null,
               error: body?.detail ?? `HTTP ${r.status}` };
    }
    return (await r.json()) as EmulatorStartResponse;
  } catch (e) {
    return { ok: false, emulator_path: null, avd: avd ?? "", pid: null,
             error: String(e) };
  }
}

export type InstallLaunchStep = {
  key: "check_installed" | "install" | "launch";
  ok: boolean;
  skipped?: boolean;
  installed?: boolean;
  apk_path_on_device?: string | null;
  exit_code?: number | null;
  reason?: string;
  error?: string | null;
  // ----- Launch-only fields (populated only when key === "launch") -----
  /** Resolved launcher component, e.g. ``com.example.weakbank.low/.MainActivity``.
   *  Comes from ``cmd package resolve-activity --brief``; cached per-app. */
  activity?: string | null;
  /** ``LaunchState`` token from ``am start -W`` — ``COLD`` / ``WARM`` /
   *  ``HOT`` / ``RELAUNCH``. Useful operator-facing diagnostic for cold-
   *  start performance investigations. */
  launch_state?: string | null;
  /** ``TotalTime`` from ``am start -W`` (milliseconds, time from intent
   *  dispatch to first frame). ``null`` when the parser couldn't find
   *  the line (e.g. monkey fallback or am-start failure). */
  total_time_ms?: number | null;
  /** PID returned by ``pidof <package>`` after the launch attempt. The
   *  ground-truth verification — non-null means the app's process is
   *  alive regardless of what ``am start``/``monkey`` claimed. */
  pid?: number | null;
  /** True when ``pidof`` confirmed the process exists post-launch. The
   *  wizard treats this as the authoritative success signal. */
  verified_running?: boolean;
  /** True when activity resolution failed and we fell back to the
   *  legacy ``monkey`` launcher. Surfaced so the operator can spot
   *  unusual environments (older Android, PackageManager mid-rebuild). */
  used_monkey_fallback?: boolean;
};

export type InstallLaunchResponse = {
  ok: boolean;
  package: string;
  apk_path: string | null;
  steps: InstallLaunchStep[];
  error?: string;
};

export async function installAndLaunch(
  appId: string,
  opts: { install?: boolean; launch?: boolean } = {},
): Promise<InstallLaunchResponse> {
  const install = opts.install ?? true;
  const launch = opts.launch ?? true;
  try {
    const r = await fetch("/api/device/install_and_launch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ app_id: appId, install, launch }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      return {
        ok: false,
        package: "",
        apk_path: null,
        steps: [],
        error: body?.detail ?? `HTTP ${r.status}`,
      };
    }
    return (await r.json()) as InstallLaunchResponse;
  } catch (e) {
    return { ok: false, package: "", apk_path: null, steps: [],
             error: String(e) };
  }
}

/** Polling interval for both wait helpers below. Emulator boot is slow
 *  enough that more frequent polls would just hammer adb without
 *  speeding anything up. */
const DEVICE_POLL_MS = 2000;

/**
 * Poll ``/api/device/status`` until ``adbd`` is reachable (``state ===
 * "device"``) or the timeout elapses. Resolves to ``true`` on success.
 *
 * **This is the *first* boot gate, not the last.** ``adb get-state``
 * fires very early in the Android boot sequence — typically 10-20 s
 * into a cold boot — well before ``system_server`` is up. Steps that
 * talk to ``ActivityManager`` / ``PackageManager`` (install, launch)
 * MUST additionally wait on :func:`waitForBootCompleted`.
 */
export async function waitForAdbDevice(timeoutMs = 90_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const s = await getDeviceStatus();
    if (s.online && s.state === "device") return true;
    await new Promise((res) => setTimeout(res, DEVICE_POLL_MS));
  }
  return false;
}

/**
 * Poll ``/api/device/status`` until ``boot_completed === true`` or the
 * timeout elapses. Caller is expected to have already awaited
 * :func:`waitForAdbDevice` so the only gating signal here is the
 * ``getprop sys.boot_completed`` flip from ``0`` to ``1``.
 *
 * Default timeout is generous (3 minutes) because cold-boot of a fresh
 * AVD on a slow host can comfortably take 90-120 s between adbd-up and
 * ``sys.boot_completed=1``; under-sizing this window is exactly what
 * caused the original "Check app installed fired too early" bug.
 */
export async function waitForBootCompleted(timeoutMs = 180_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const s = await getDeviceStatus();
    if (s.online && s.state === "device" && s.boot_completed) return true;
    await new Promise((res) => setTimeout(res, DEVICE_POLL_MS));
  }
  return false;
}

/**
 * Convenience wrapper: waits for *both* adbd reachability AND
 * ``sys.boot_completed=1``, with separate timeouts so callers that
 * don't need progressive UI updates can use a single call. Most call
 * sites (the Boot wizard) want the two stages separately so they can
 * paint different rows — they should call :func:`waitForAdbDevice`
 * then :func:`waitForBootCompleted` directly.
 */
export async function waitForDeviceFullyBooted(
  adbTimeoutMs = 90_000,
  bootTimeoutMs = 180_000,
): Promise<boolean> {
  if (!(await waitForAdbDevice(adbTimeoutMs))) return false;
  return waitForBootCompleted(bootTimeoutMs);
}

/** @deprecated since the boot-completion split. Kept as a thin alias of
 *  :func:`waitForAdbDevice` so any external callers / scripts don't
 *  break — the *wizard* uses the split helpers above. New code should
 *  pick :func:`waitForAdbDevice` (just adbd) or
 *  :func:`waitForDeviceFullyBooted` (adbd + boot_completed). */
export const waitForDeviceOnline = waitForAdbDevice;
