/**
 * Frontend client for ``/api/status/*`` (Settings tab status panels and the
 * tiny health dot in the global header).
 *
 * Cards are intentionally permissive: every card has at minimum
 * ``ok: boolean`` and a human-readable ``label``; the rest is probe-specific
 * and rendered with a generic key/value renderer.
 */

export type StatusCard = {
  ok: boolean;
  label: string;
  hint?: string | null;
  error?: string | null;
  [k: string]: unknown;
};

export type GlobalStatus = {
  ts: number;
  took_ms: number;
  process: StatusCard & {
    pid: number;
    host: string;
    port: number;
    cwd: string;
    config_path: string;
    config_path_exists: boolean;
    env_locked_keys: string[];
    python: { python_version: string; modules: Record<string, boolean> };
  };
  tools: {
    adb: StatusCard;
    jadx: StatusCard;
    apktool: StatusCard;
    /** Host-side `frida` CLI (Python bindings entry point). */
    frida: StatusCard;
    /**
     * Hook Lab device-side readiness card (Phase 6 step 4 / DEC-023):
     * combines `pidof frida-server` reachability with the host↔device
     * version-skew check. `version_skew` is `null` when versions match
     * exactly, `"minor"` for differing minors (works but flag), or
     * `"major"` for incompatible majors (card goes red).
     */
    frida_server: StatusCard & {
      running: boolean;
      pid: number | null;
      host_version: string | null;
      device_version: string | null;
      version_skew: null | "minor" | "major";
    };
  };
  device: StatusCard & {
    connected: boolean;
    state: string | null;
    serial: string | null;
  };
  llm: StatusCard & {
    model: string;
    base_url: string | null;
    ping_ms: number | null;
    models_available: string[];
    model_present: boolean;
  };
  rag_provider: StatusCard;
  filesystem: { apps_root: StatusCard };
  config_sources: Record<string, "yaml" | "env" | "default">;
};

export type AppStatus = {
  ts: number;
  took_ms: number;
  app_id: string;
  app_dir: string;
  meta: StatusCard & { package: string; apk_path: string | null; apk_sha256: string | null };
  decompile: StatusCard & { status: string; sha: string | null; file_count?: number };
  rag: StatusCard & { status: string };
  device: {
    /** True when adb get-state returned a usable device state. */
    connected: boolean;
    /** Raw adb get-state value (`device`, `unauthorized`, `offline`, …). */
    state: string | null;
    /** Serial (when exactly one device is attached). */
    serial: string | null;
    package_installed: StatusCard & { skipped?: boolean };
    package_running: StatusCard & { skipped?: boolean };
    package_uid: StatusCard & { skipped?: boolean };
    foreground: StatusCard & { matches_app?: boolean; skipped?: boolean };
    uiautomator_dump: StatusCard & { skipped?: boolean };
  };
  overrides: StatusCard & { active_count: number; lines: string[]; tags: string[] };
};

export type StatusResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status: number };

async function _get<T>(url: string): Promise<StatusResult<T>> {
  try {
    const r = await fetch(url);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      return { ok: false, error: body?.detail ?? `HTTP ${r.status}`, status: r.status };
    }
    return { ok: true, data: body as T };
  } catch (e) {
    return { ok: false, error: (e as Error).message, status: 0 };
  }
}

export function fetchGlobalStatus() {
  return _get<GlobalStatus>("/api/status/global");
}

export function fetchAppStatus(appId: string) {
  return _get<AppStatus>(`/api/status/apps/${encodeURIComponent(appId)}`);
}

/**
 * Quick rollup used by the header health dot. Returns one of:
 *   "green"   — everything ok
 *   "yellow"  — non-critical issues (e.g. frida missing, RAG provider degraded,
 *               no device attached — offline workflows still work)
 *   "red"     — at least one critical (LLM down, adb missing, disk full)
 */
export function rollupGlobal(g: GlobalStatus): "green" | "yellow" | "red" {
  if (!g.llm.ok) return "red";
  if (!g.tools.adb.ok) return "red";
  const fs = g.filesystem.apps_root;
  if (!fs.ok) return "red";
  if (typeof fs.low_space === "boolean" && fs.low_space) return "red";
  if (!g.tools.jadx.ok || !g.rag_provider.ok) return "yellow";
  if (!g.tools.apktool.ok || !g.tools.frida.ok) return "yellow";
  // Hook Lab readiness is non-critical for the offline / static workflows
  // (Reports / Inspect): yellow it instead of red. Major version skew is
  // surfaced as the card error message; the dot simply tracks "ok".
  if (!g.tools.frida_server.ok) return "yellow";
  if (!g.device.ok) return "yellow";
  return "green";
}
