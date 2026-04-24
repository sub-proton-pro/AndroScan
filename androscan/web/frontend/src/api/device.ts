export type DeviceStatus = {
  online: boolean;
  state: string;
  detail: string;
};

export async function getDeviceStatus(): Promise<DeviceStatus> {
  try {
    const r = await fetch("/api/device/status");
    if (!r.ok) return { online: false, state: `http_${r.status}`, detail: "" };
    return (await r.json()) as DeviceStatus;
  } catch (e) {
    return { online: false, state: "fetch_error", detail: String(e) };
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

/**
 * Poll ``/api/device/status`` until the device transitions to ``state ===
 * "device"`` or the timeout elapses. Resolves to ``true`` on success.
 *
 * Used by the wizard's "wait for device" step. Polling interval is fixed
 * at 2 s — emulator boot is slow enough that more frequent polls would
 * just hammer adb without speeding anything up.
 */
export async function waitForDeviceOnline(timeoutMs = 90_000): Promise<boolean> {
  const POLL_MS = 2000;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const s = await getDeviceStatus();
    if (s.online && s.state === "device") return true;
    await new Promise((res) => setTimeout(res, POLL_MS));
  }
  return false;
}
