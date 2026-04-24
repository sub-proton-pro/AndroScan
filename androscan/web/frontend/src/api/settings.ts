/**
 * Frontend client for ``/api/settings/*`` (the Settings tab).
 *
 * Contract mirrors ``androscan/web/settings_routes.py``. The shapes are kept
 * intentionally permissive (``Record<string, unknown>``) for the YAML-shaped
 * payloads since the user can write any keys in the raw editor; type-safe
 * accessors live on the React side once we know we're looking at a known
 * field.
 */

export type SettingsSource = "yaml" | "env" | "default";

/** Per-app setting cell as returned by the effective-merge endpoint. */
export type EffectiveCell = {
  /** Resolved value (string | number | boolean | null). */
  value: unknown;
  /** Where the value came from. */
  source: "global" | "app" | "default";
};

/** Effective per-app view: section -> key -> EffectiveCell. */
export type EffectiveSettings = {
  rag: Record<string, EffectiveCell>;
  decompile: Record<string, EffectiveCell>;
  inspect: Record<string, EffectiveCell>;
  exploit: Record<string, EffectiveCell>;
  chat: Record<string, EffectiveCell>;
  tags: string[];
  notes: string;
  schema_version: number;
};

export type GlobalSettingsResponse = {
  ts: number;
  config_path: string;
  config_path_exists: boolean;
  /** YAML-shaped (section -> {key: value}). Mirrors ``global_config.yaml``. */
  global: Record<string, Record<string, unknown>>;
  /** Flat ``Config`` field map. */
  flat: Record<string, unknown>;
  /** Raw text of ``global_config.yaml`` (empty string if missing). */
  raw_yaml: string;
  /** field -> "yaml" | "env" | "default" */
  sources: Record<string, SettingsSource>;
  /** Currently-set ``ANDROSCAN_*`` env vars (NAME -> value). */
  env_locks: Record<string, string>;
  /** Fields that don't require a uvicorn restart to take effect. */
  live_reloadable: string[];
  /** field -> {section, key, env_var}. Useful for UI grouping/labels. */
  field_map: Record<string, { section: string; key: string; env_var: string | null }>;
};

export type AppSettingsResponse = {
  ts: number;
  app_id: string;
  app_dir: string;
  /** Raw on-disk per-app overrides. */
  per_app: Record<string, unknown>;
  /** Merged effective view (per_app overlaid on global_view). */
  effective: EffectiveSettings;
  /** Snapshot of the global view used in the merge. */
  global_view: Record<string, Record<string, unknown>>;
};

export type WriteResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status: number };

async function _read<T>(url: string): Promise<WriteResult<T>> {
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

async function _send<T>(
  url: string,
  method: "PUT" | "POST",
  body: unknown,
): Promise<WriteResult<T>> {
  try {
    const r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      return { ok: false, error: j?.detail ?? `HTTP ${r.status}`, status: r.status };
    }
    return { ok: true, data: j as T };
  } catch (e) {
    return { ok: false, error: (e as Error).message, status: 0 };
  }
}

// ---- Global -----------------------------------------------------------------

export function fetchGlobalSettings() {
  return _read<GlobalSettingsResponse>("/api/settings/global");
}

export type GlobalUpdateResponse = {
  ok: true;
  updated_fields: string[];
  restart_required: string[];
  global: GlobalSettingsResponse;
};

export function updateGlobalSettings(fields: Record<string, unknown>) {
  return _send<GlobalUpdateResponse>("/api/settings/global", "PUT", { fields });
}

export function saveGlobalRawYaml(raw_yaml: string) {
  return _send<GlobalUpdateResponse>("/api/settings/global/raw", "PUT", { raw_yaml });
}

export function resetGlobalSettings() {
  return _send<GlobalUpdateResponse>("/api/settings/global/reset", "POST", undefined);
}

export function reloadGlobalSettings() {
  return _send<GlobalUpdateResponse>("/api/settings/reload", "POST", undefined);
}

// ---- Per-app ----------------------------------------------------------------

export function fetchAppSettings(appId: string) {
  return _read<AppSettingsResponse>(
    `/api/settings/apps/${encodeURIComponent(appId)}`,
  );
}

export function updateAppSettings(appId: string, patch: Record<string, unknown>) {
  return _send<{ ok: true; per_app: Record<string, unknown>; effective: EffectiveSettings }>(
    `/api/settings/apps/${encodeURIComponent(appId)}`,
    "PUT",
    { patch },
  );
}

export function resetAppSettings(appId: string) {
  return _send<{ ok: true; per_app: Record<string, unknown>; effective: EffectiveSettings }>(
    `/api/settings/apps/${encodeURIComponent(appId)}/reset`,
    "POST",
    undefined,
  );
}
