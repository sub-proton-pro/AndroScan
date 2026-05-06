/**
 * Settings tab — three areas:
 *
 *   1. Section nav on the left (Global / Per-app / Status / Diagnostics).
 *   2. Active panel on the right.
 *   3. A persistent "save bar" (only visible when the form is dirty).
 *
 * All field edits are local until the user explicitly saves, at which
 * point we PUT either the structured ``fields`` patch or the raw YAML
 * (depending on edit mode). After save we re-fetch the global view so
 * source pills and live_reloadable badges are consistent.
 */

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  fetchAppSettings,
  fetchGlobalSettings,
  reloadGlobalSettings,
  resetAppSettings,
  resetGlobalSettings,
  saveGlobalRawYaml,
  updateAppSettings,
  updateGlobalSettings,
  type AppSettingsResponse,
  type GlobalSettingsResponse,
} from "../api/settings";
import {
  fetchAppStatus,
  fetchGlobalStatus,
  type AppStatus,
  type GlobalStatus,
  type StatusCard,
} from "../api/status";
import { rebuildRagIndex } from "../api/rag";
import { rebuildGraph } from "../api/graph";
import { startFridaServer } from "../api/frida";
import { IconCheck, IconCopy } from "../components/Icons";
import { useWorkbench, type SettingsSection } from "../context/WorkbenchContext";

// Keep ``Section`` an alias of the canonical ``SettingsSection`` exported
// from WorkbenchContext so the deep-link plumbing stays type-safe at the
// boundary (HealthDot writes ``SettingsSection``, this tab reads it).
type Section = SettingsSection;

const SECTION_NAV: { id: Section; label: string; hint: string }[] = [
  { id: "global",      label: "Global settings", hint: "global_config.yaml" },
  { id: "perApp",      label: "App settings",    hint: "per-app overrides" },
  { id: "status",      label: "Status",          hint: "live health checks" },
  { id: "diagnostics", label: "Diagnostics",     hint: "raw payloads + reload" },
];

export function SettingsTab() {
  const { pendingSettingsSection, setPendingSettingsSection } = useWorkbench();
  // Initial section honours a pending deep-link if one is queued at
  // mount time (HealthDot click on a non-Settings tab → SettingsTab
  // mounts fresh with the seed), otherwise falls back to "global".
  const [section, setSection] = useState<Section>(
    () => pendingSettingsSection?.section ?? "global",
  );
  // Track which deep-link timestamp we've already consumed so we don't
  // fire the effect twice on the same seed (StrictMode double-mount in
  // dev would otherwise re-route the operator away from a sub-section
  // they navigated to themselves).
  const consumedTsRef = useRef<number | null>(
    pendingSettingsSection?.ts ?? null,
  );
  useEffect(() => {
    if (!pendingSettingsSection) return;
    if (pendingSettingsSection.ts === consumedTsRef.current) return;
    consumedTsRef.current = pendingSettingsSection.ts;
    setSection(pendingSettingsSection.section);
    setPendingSettingsSection(null);
  }, [pendingSettingsSection, setPendingSettingsSection]);
  return (
    <div className="settings-tab">
      <aside className="settings-nav" aria-label="Settings sections">
        {SECTION_NAV.map((s) => (
          <button
            key={s.id}
            type="button"
            className={section === s.id ? "settings-nav-item active" : "settings-nav-item"}
            onClick={() => setSection(s.id)}
          >
            <span className="settings-nav-label">{s.label}</span>
            <span className="settings-nav-hint">{s.hint}</span>
          </button>
        ))}
      </aside>
      <section className="settings-panel-host">
        {section === "global" && <GlobalSettingsPanel />}
        {section === "perApp" && <AppSettingsPanel />}
        {section === "status" && <StatusPanel />}
        {section === "diagnostics" && <DiagnosticsPanel />}
      </section>
    </div>
  );
}

// =============================================================================
// Global settings panel
// =============================================================================

type EditMode = "form" | "raw";

function GlobalSettingsPanel() {
  const [data, setData] = useState<GlobalSettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<EditMode>("form");

  // Form-mode dirty state (only the fields the user actually changed).
  const [formDirty, setFormDirty] = useState<Record<string, unknown>>({});
  // Raw-mode editor buffer.
  const [rawBuf, setRawBuf] = useState<string>("");
  const [savePending, setSavePending] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [restartHint, setRestartHint] = useState<string[] | null>(null);
  const [confirmingReset, setConfirmingReset] = useState(false);

  const reload = async () => {
    setLoading(true);
    const r = await fetchGlobalSettings();
    if (!r.ok) {
      setError(r.error);
      setData(null);
    } else {
      setError(null);
      setData(r.data);
      setRawBuf(r.data.raw_yaml);
      setFormDirty({});
    }
    setLoading(false);
  };

  useEffect(() => {
    reload();
  }, []);

  if (loading || !data) {
    return <div className="settings-loading">{error ? `Error: ${error}` : "Loading settings…"}</div>;
  }

  const isDirty =
    mode === "form" ? Object.keys(formDirty).length > 0 : rawBuf !== data.raw_yaml;

  const onFormChange = (field: string, value: unknown, original: unknown) => {
    setFormDirty((prev) => {
      const next = { ...prev };
      if (Object.is(value, original)) {
        delete next[field];
      } else {
        next[field] = value;
      }
      return next;
    });
  };

  const onSave = async () => {
    setSavePending(true);
    setSaveMsg(null);
    setRestartHint(null);
    const r =
      mode === "form"
        ? await updateGlobalSettings(formDirty)
        : await saveGlobalRawYaml(rawBuf);
    setSavePending(false);
    if (!r.ok) {
      setSaveMsg(`Save failed: ${r.error}`);
      return;
    }
    setSaveMsg("Saved.");
    setRestartHint(r.data.restart_required.length > 0 ? r.data.restart_required : null);
    setData(r.data.global);
    setRawBuf(r.data.global.raw_yaml);
    setFormDirty({});
  };

  const onReset = async () => {
    if (!confirmingReset) {
      setConfirmingReset(true);
      window.setTimeout(() => setConfirmingReset(false), 4000);
      return;
    }
    setConfirmingReset(false);
    setSavePending(true);
    const r = await resetGlobalSettings();
    setSavePending(false);
    if (!r.ok) {
      setSaveMsg(`Reset failed: ${r.error}`);
      return;
    }
    setSaveMsg("Restored defaults.");
    setRestartHint(r.data.restart_required.length > 0 ? r.data.restart_required : null);
    setData(r.data.global);
    setRawBuf(r.data.global.raw_yaml);
    setFormDirty({});
  };

  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <h2>Global settings</h2>
          <div className="settings-subtitle">{data.config_path}</div>
        </div>
        <div className="settings-mode-toggle" role="tablist" aria-label="Edit mode">
          <button
            type="button"
            className={mode === "form" ? "active" : ""}
            onClick={() => setMode("form")}
            disabled={isDirty && mode === "raw"}
            title={isDirty && mode === "raw" ? "Save or discard YAML edits first" : ""}
          >
            Form
          </button>
          <button
            type="button"
            className={mode === "raw" ? "active" : ""}
            onClick={() => setMode("raw")}
            disabled={isDirty && mode === "form"}
            title={isDirty && mode === "form" ? "Save or discard form edits first" : ""}
          >
            YAML
          </button>
        </div>
      </header>

      {Object.keys(data.env_locks).length > 0 && (
        <div className="settings-banner settings-banner-warn">
          <strong>Environment overrides active:</strong>{" "}
          {Object.entries(data.env_locks)
            .map(([k, v]) => `${k}=${v}`)
            .join(", ")}{" "}
          — these fields cannot be edited from the UI until the env vars are unset.
        </div>
      )}

      <div className="settings-panel-body">
        {mode === "form" ? (
          <FormGlobal
            data={data}
            dirty={formDirty}
            onChange={onFormChange}
          />
        ) : (
          <RawYamlEditor value={rawBuf} onChange={setRawBuf} />
        )}
      </div>

      <div className="settings-save-bar">
        <span className="settings-save-status">
          {savePending && "Saving…"}
          {!savePending && saveMsg && <span className="settings-save-msg">{saveMsg}</span>}
          {!savePending && restartHint && (
            <span className="settings-restart-hint">
              Restart required for: {restartHint.join(", ")}
            </span>
          )}
        </span>
        <div className="settings-save-actions">
          <button type="button" onClick={() => reload()} disabled={savePending}>
            Reload
          </button>
          <button
            type="button"
            className={confirmingReset ? "danger confirming" : "danger"}
            onClick={onReset}
            disabled={savePending}
          >
            {confirmingReset ? "Click again to confirm reset" : "Reset to defaults"}
          </button>
          <button
            type="button"
            className="primary"
            onClick={onSave}
            disabled={!isDirty || savePending}
          >
            Save changes
          </button>
        </div>
      </div>
    </div>
  );
}

function RawYamlEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="settings-yaml-editor">
      <p className="settings-help">
        Edit <code>global_config.yaml</code> directly. Validation runs on save —
        invalid YAML or unknown types will reject the change.
      </p>
      <textarea
        className="settings-yaml-textarea"
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={26}
      />
    </div>
  );
}

// LCP.4 / DEC-027 — "LLM provider" radio sits above the auto-grouped
// settings sections. The radio is the canonical chooser for
// ``llm_provider``; the bare text-input that ``FormGlobal`` would
// otherwise render (the field IS in ``field_map``) is suppressed.
//
// The three sections "ollama" / "llamacpp" / "llm.cloud_*" are
// rendered INSIDE the radio block, gated by the active radio
// value, so an operator picking "Local (Ollama)" never sees the
// llama.cpp knobs (and vice versa). All other sections (paths,
// workflow, output, web, rag, frida, trace) keep flowing through
// the existing auto-grouper untouched.

type ProviderRadio = "ollama" | "llamacpp" | "cloud";

const CLOUD_VENDORS: readonly string[] = [
  "openai",
  "gemini",
  "groq",
  "deepseek",
  "together",
  "mistral",
];

function classifyProvider(value: unknown): ProviderRadio {
  if (value === "ollama") return "ollama";
  if (value === "llamacpp") return "llamacpp";
  // Anything else (one of the six cloud vendors, or a typo'd value
  // the operator may have hand-edited in YAML) falls into the
  // Cloud bucket. The dropdown will surface the actual string so
  // the operator can spot the typo and pick a real vendor.
  return "cloud";
}

function FormGlobal({
  data,
  dirty,
  onChange,
}: {
  data: GlobalSettingsResponse;
  dirty: Record<string, unknown>;
  onChange: (field: string, value: unknown, original: unknown) => void;
}) {
  // Group fields by section using field_map.
  const grouped = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const [field, meta] of Object.entries(data.field_map)) {
      out[meta.section] ??= [];
      out[meta.section].push(field);
    }
    Object.values(out).forEach((arr) => arr.sort());
    return out;
  }, [data.field_map]);

  // LCP.4 — sections owned by the LlmProviderRadio block. The
  // auto-grouper below skips these so they render exactly once.
  const LLM_OWNED_SECTIONS = new Set(["ollama", "llamacpp", "llm"]);

  return (
    <div className="settings-form">
      <LlmProviderRadio
        data={data}
        dirty={dirty}
        grouped={grouped}
        onChange={onChange}
      />
      {Object.entries(grouped)
        .filter(([section]) => !LLM_OWNED_SECTIONS.has(section))
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([section, fields]) => (
          <fieldset key={section} className="settings-section">
            <legend>{section}</legend>
            {fields.map((f) => (
              <SettingsField
                key={f}
                field={f}
                meta={data.field_map[f]}
                value={dirty[f] ?? data.flat[f]}
                originalValue={data.flat[f]}
                source={data.sources[f]}
                envLock={
                  data.field_map[f].env_var
                    ? data.env_locks[data.field_map[f].env_var as string]
                    : undefined
                }
                liveReloadable={data.live_reloadable.includes(f)}
                isDirty={f in dirty}
                onChange={(v) => onChange(f, v, data.flat[f])}
              />
            ))}
          </fieldset>
        ))}
    </div>
  );
}

function LlmProviderRadio({
  data,
  dirty,
  grouped,
  onChange,
}: {
  data: GlobalSettingsResponse;
  dirty: Record<string, unknown>;
  grouped: Record<string, string[]>;
  onChange: (field: string, value: unknown, original: unknown) => void;
}) {
  const currentValue =
    (dirty["llm_provider"] as string | undefined) ??
    (data.flat["llm_provider"] as string | undefined) ??
    "ollama";
  const radio = classifyProvider(currentValue);
  const envLock = data.env_locks["ANDROSCAN_LLM_PROVIDER"];
  const disabled = Boolean(envLock);

  const setProvider = (next: string) => {
    onChange("llm_provider", next, data.flat["llm_provider"]);
  };

  const handleRadio = (kind: ProviderRadio) => {
    if (disabled) return;
    if (kind === "ollama") setProvider("ollama");
    else if (kind === "llamacpp") setProvider("llamacpp");
    else if (radio !== "cloud") setProvider("openai"); // sensible default
    // else current value already a cloud vendor — leave the dropdown alone.
  };

  // Filter the auto-grouped llm section's fields down to the
  // cloud_* ones (cloud_model / cloud_api_key / cloud_temperature).
  // The bare ``llm_provider`` field is intentionally not rendered
  // anywhere — the radio + dropdown is the only chooser.
  // LCP.6 — ``local_grammar_enabled`` lives under the ``llm`` YAML
  // section (cross-provider local knob) but only meaningfully applies
  // to local providers; rendered separately under the Ollama +
  // llama.cpp subsections, never under Cloud.
  const llmCloudFields = (grouped["llm"] ?? []).filter(
    (f) => f !== "llm_provider" && f !== "local_grammar_enabled",
  );
  const ollamaFields = grouped["ollama"] ?? [];
  const llamacppFields = grouped["llamacpp"] ?? [];
  const localGrammarField =
    "local_grammar_enabled" in (data.field_map ?? {})
      ? "local_grammar_enabled"
      : null;

  return (
    <fieldset className="settings-section settings-llm-provider">
      <legend>LLM provider</legend>
      <div
        className="settings-llm-radio"
        role="radiogroup"
        aria-label="LLM provider"
      >
        <RadioOption
          checked={radio === "ollama"}
          disabled={disabled}
          label="Local (Ollama)"
          onChange={() => handleRadio("ollama")}
        />
        <RadioOption
          checked={radio === "llamacpp"}
          disabled={disabled}
          label="Local (llama.cpp)"
          onChange={() => handleRadio("llamacpp")}
        />
        <RadioOption
          checked={radio === "cloud"}
          disabled={disabled}
          label="Cloud"
          onChange={() => handleRadio("cloud")}
        />
        {disabled && (
          <span
            className="env-lock-pill"
            title={`Locked by ANDROSCAN_LLM_PROVIDER=${envLock}`}
          >
            ANDROSCAN_LLM_PROVIDER
          </span>
        )}
      </div>
      {radio === "ollama" && (
        <div className="settings-llm-subsection">
          {ollamaFields.map((f) => (
            <SettingsField
              key={f}
              field={f}
              meta={data.field_map[f]}
              value={dirty[f] ?? data.flat[f]}
              originalValue={data.flat[f]}
              source={data.sources[f]}
              envLock={
                data.field_map[f].env_var
                  ? data.env_locks[data.field_map[f].env_var as string]
                  : undefined
              }
              liveReloadable={data.live_reloadable.includes(f)}
              isDirty={f in dirty}
              onChange={(v) => onChange(f, v, data.flat[f])}
            />
          ))}
          {localGrammarField && (
            <SettingsField
              key={localGrammarField}
              field={localGrammarField}
              meta={data.field_map[localGrammarField]}
              value={dirty[localGrammarField] ?? data.flat[localGrammarField]}
              originalValue={data.flat[localGrammarField]}
              source={data.sources[localGrammarField]}
              envLock={
                data.field_map[localGrammarField].env_var
                  ? data.env_locks[data.field_map[localGrammarField].env_var as string]
                  : undefined
              }
              liveReloadable={data.live_reloadable.includes(localGrammarField)}
              isDirty={localGrammarField in dirty}
              onChange={(v) =>
                onChange(localGrammarField, v, data.flat[localGrammarField])
              }
            />
          )}
        </div>
      )}
      {radio === "llamacpp" && (
        <div className="settings-llm-subsection">
          <p className="settings-help">
            Context size is set at <code>llama-server</code> start (e.g.
            <code> --ctx-size 16384</code>) and isn't a request-level
            parameter in OpenAI-compat mode. Restart the server to change it.
          </p>
          {llamacppFields.map((f) => (
            <SettingsField
              key={f}
              field={f}
              meta={data.field_map[f]}
              value={dirty[f] ?? data.flat[f]}
              originalValue={data.flat[f]}
              source={data.sources[f]}
              envLock={
                data.field_map[f].env_var
                  ? data.env_locks[data.field_map[f].env_var as string]
                  : undefined
              }
              liveReloadable={data.live_reloadable.includes(f)}
              isDirty={f in dirty}
              onChange={(v) => onChange(f, v, data.flat[f])}
            />
          ))}
          {localGrammarField && (
            <SettingsField
              key={localGrammarField}
              field={localGrammarField}
              meta={data.field_map[localGrammarField]}
              value={dirty[localGrammarField] ?? data.flat[localGrammarField]}
              originalValue={data.flat[localGrammarField]}
              source={data.sources[localGrammarField]}
              envLock={
                data.field_map[localGrammarField].env_var
                  ? data.env_locks[data.field_map[localGrammarField].env_var as string]
                  : undefined
              }
              liveReloadable={data.live_reloadable.includes(localGrammarField)}
              isDirty={localGrammarField in dirty}
              onChange={(v) =>
                onChange(localGrammarField, v, data.flat[localGrammarField])
              }
            />
          )}
        </div>
      )}
      {radio === "cloud" && (
        <div className="settings-llm-subsection">
          <div className="settings-field">
            <label className="settings-field-label">
              <span className="settings-field-name">Cloud vendor</span>
              <span className="settings-field-id">llm.provider</span>
            </label>
            <div className="settings-field-input">
              <select
                value={currentValue}
                disabled={disabled}
                onChange={(e) => setProvider(e.target.value)}
              >
                {/* Off-vocab values (typos / future vendors) keep
                    rendering so the operator can see + fix them. */}
                {!CLOUD_VENDORS.includes(currentValue) && (
                  <option value={currentValue}>{currentValue} (unknown)</option>
                )}
                {CLOUD_VENDORS.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
            </div>
            <div className="settings-field-meta">
              <span className={`source-pill source-${data.sources["llm_provider"] ?? "default"}`}>
                {data.sources["llm_provider"] ?? "default"}
              </span>
            </div>
          </div>
          {llmCloudFields.map((f) => (
            <SettingsField
              key={f}
              field={f}
              meta={data.field_map[f]}
              value={dirty[f] ?? data.flat[f]}
              originalValue={data.flat[f]}
              source={data.sources[f]}
              envLock={
                data.field_map[f].env_var
                  ? data.env_locks[data.field_map[f].env_var as string]
                  : undefined
              }
              liveReloadable={data.live_reloadable.includes(f)}
              isDirty={f in dirty}
              onChange={(v) => onChange(f, v, data.flat[f])}
            />
          ))}
        </div>
      )}
    </fieldset>
  );
}

function RadioOption({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  label: string;
  onChange: () => void;
}) {
  return (
    <label
      className={
        checked
          ? "settings-llm-radio-option active"
          : "settings-llm-radio-option"
      }
    >
      <input
        type="radio"
        name="llm-provider-radio"
        checked={checked}
        disabled={disabled}
        onChange={onChange}
      />
      <span>{label}</span>
    </label>
  );
}

function SettingsField({
  field,
  meta,
  value,
  originalValue,
  source,
  envLock,
  liveReloadable,
  isDirty,
  onChange,
}: {
  field: string;
  meta: { section: string; key: string; env_var: string | null };
  value: unknown;
  originalValue: unknown;
  source: "yaml" | "env" | "default";
  envLock: string | undefined;
  liveReloadable: boolean;
  isDirty: boolean;
  onChange: (v: unknown) => void;
}) {
  const disabled = Boolean(envLock);

  // Pick input shape based on the original value's type.
  const isBool = typeof originalValue === "boolean";
  const isNum = typeof originalValue === "number";

  return (
    <div className={isDirty ? "settings-field dirty" : "settings-field"}>
      <label className="settings-field-label">
        <span className="settings-field-name">{meta.key}</span>
        <span className="settings-field-id">{field}</span>
      </label>
      <div className="settings-field-input">
        {isBool ? (
          <input
            type="checkbox"
            checked={Boolean(value)}
            disabled={disabled}
            onChange={(e) => onChange(e.target.checked)}
          />
        ) : isNum ? (
          <input
            type="number"
            value={String(value ?? "")}
            disabled={disabled}
            onChange={(e) => {
              const n = Number(e.target.value);
              onChange(Number.isFinite(n) ? n : 0);
            }}
          />
        ) : (
          <input
            type="text"
            value={String(value ?? "")}
            disabled={disabled}
            onChange={(e) => onChange(e.target.value)}
          />
        )}
      </div>
      <div className="settings-field-meta">
        <span className={`source-pill source-${source}`}>{source}</span>
        {!liveReloadable && (
          <span className="restart-pill" title="Requires uvicorn restart">restart</span>
        )}
        {disabled && envLock && (
          <span className="env-lock-pill" title={`Locked by ${meta.env_var}=${envLock}`}>
            {meta.env_var}
          </span>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// App settings panel
// =============================================================================

function AppSettingsPanel() {
  const { projects, appId, setAppId } = useWorkbench();
  const [data, setData] = useState<AppSettingsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [savePending, setSavePending] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const reload = async (id: string) => {
    setLoading(true);
    const r = await fetchAppSettings(id);
    if (!r.ok) {
      setError(r.error);
      setData(null);
    } else {
      setError(null);
      setData(r.data);
      setDraft(JSON.parse(JSON.stringify(r.data.per_app)));
    }
    setLoading(false);
  };

  useEffect(() => {
    if (appId) reload(appId);
    else {
      setData(null);
      setDraft({});
    }
  }, [appId]);

  const isDirty = JSON.stringify(draft) !== JSON.stringify(data?.per_app ?? {});

  const setSectionField = (section: string, key: string, value: unknown) => {
    setDraft((prev) => {
      const next = { ...prev };
      const sec = { ...((next[section] as Record<string, unknown>) ?? {}) };
      if (value === "" || value === null || value === undefined) {
        delete sec[key];
      } else {
        sec[key] = value;
      }
      next[section] = sec;
      return next;
    });
  };

  const onSave = async () => {
    if (!appId) return;
    setSavePending(true);
    setSaveMsg(null);
    const r = await updateAppSettings(appId, draft);
    setSavePending(false);
    if (!r.ok) {
      setSaveMsg(`Save failed: ${r.error}`);
      return;
    }
    setSaveMsg("Saved.");
    await reload(appId);
  };

  const onReset = async () => {
    if (!appId) return;
    setSavePending(true);
    const r = await resetAppSettings(appId);
    setSavePending(false);
    if (!r.ok) {
      setSaveMsg(`Reset failed: ${r.error}`);
      return;
    }
    setSaveMsg("Cleared.");
    await reload(appId);
  };

  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <h2>Per-app settings</h2>
          <div className="settings-subtitle">overrides written to apps/&lt;id&gt;/app_settings.json</div>
        </div>
        <select
          className="settings-app-picker"
          value={appId ?? ""}
          onChange={(e) => setAppId(e.target.value || null)}
        >
          <option value="">— select app —</option>
          {projects.map((p) => (
            <option key={p.app_id} value={p.app_id}>{p.app_id}</option>
          ))}
        </select>
      </header>

      <div className="settings-panel-body">
        {!appId && <div className="settings-empty">Pick an app to view its settings.</div>}
        {appId && loading && <div className="settings-loading">Loading…</div>}
        {appId && error && <div className="settings-error">Error: {error}</div>}
        {appId && data && !loading && (
          <>
            <p className="settings-help">
              Per-app values override the global ones. Leave any field blank to inherit
              the global value (you'll see a <span className="source-pill source-yaml">global</span> pill).
            </p>
            <div className="settings-form">
              {(["rag", "decompile", "inspect", "exploit", "chat"] as const).map((sec) => (
                <fieldset key={sec} className="settings-section">
                  <legend>{sec}</legend>
                  {Object.entries(data.effective[sec] ?? {}).map(([key, cell]) => {
                    const draftSec = (draft[sec] as Record<string, unknown>) ?? {};
                    const overridden = key in draftSec;
                    const display = overridden ? draftSec[key] : "";
                    return (
                      <div className={overridden ? "settings-field dirty" : "settings-field"} key={`${sec}.${key}`}>
                        <label className="settings-field-label">
                          <span className="settings-field-name">{key}</span>
                          <span className="settings-field-id">{sec}.{key}</span>
                        </label>
                        <div className="settings-field-input">
                          <input
                            type="text"
                            placeholder={cell.value === null ? "" : String(cell.value ?? "")}
                            value={String(display ?? "")}
                            onChange={(e) => setSectionField(sec, key, e.target.value)}
                          />
                        </div>
                        <div className="settings-field-meta">
                          <span className={`source-pill source-${cell.source}`}>{cell.source}</span>
                        </div>
                      </div>
                    );
                  })}
                </fieldset>
              ))}
              <fieldset className="settings-section">
                <legend>tags + notes</legend>
                <div className="settings-field">
                  <label className="settings-field-label">
                    <span className="settings-field-name">tags</span>
                    <span className="settings-field-id">comma-separated</span>
                  </label>
                  <div className="settings-field-input">
                    <input
                      type="text"
                      value={(draft.tags as string[] | undefined)?.join(", ") ?? ""}
                      onChange={(e) =>
                        setDraft((p) => ({
                          ...p,
                          tags: e.target.value
                            .split(",")
                            .map((s) => s.trim())
                            .filter(Boolean),
                        }))
                      }
                    />
                  </div>
                </div>
                <div className="settings-field">
                  <label className="settings-field-label">
                    <span className="settings-field-name">notes</span>
                    <span className="settings-field-id">free-form, &lt;= 8000 chars</span>
                  </label>
                  <div className="settings-field-input">
                    <textarea
                      rows={4}
                      value={String(draft.notes ?? "")}
                      onChange={(e) => setDraft((p) => ({ ...p, notes: e.target.value }))}
                    />
                  </div>
                </div>
              </fieldset>
            </div>
          </>
        )}
      </div>
      {appId && data && !loading && (
        <div className="settings-save-bar">
          <span className="settings-save-status">
            {savePending && "Saving…"}
            {!savePending && saveMsg && <span className="settings-save-msg">{saveMsg}</span>}
          </span>
          <div className="settings-save-actions">
            <button type="button" onClick={() => reload(appId)} disabled={savePending}>Reload</button>
            <button type="button" className="danger" onClick={onReset} disabled={savePending}>
              Clear all overrides
            </button>
            <button
              type="button"
              className="primary"
              onClick={onSave}
              disabled={!isDirty || savePending}
            >
              Save overrides
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Status panel (Global + per-App rolled into one tab)
// =============================================================================

function StatusPanel() {
  const { projects, appId, setAppId } = useWorkbench();
  const [globalStatus, setGlobalStatus] = useState<GlobalStatus | null>(null);
  const [appStatus, setAppStatus] = useState<AppStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = async () => {
    setLoading(true);
    const [g, a] = await Promise.all([
      fetchGlobalStatus(),
      appId ? fetchAppStatus(appId) : Promise.resolve({ ok: false, error: "no app", status: 0 } as const),
    ]);
    if (!g.ok) {
      setError(g.error);
      setGlobalStatus(null);
    } else {
      setError(null);
      setGlobalStatus(g.data);
    }
    if (a.ok) setAppStatus(a.data);
    else setAppStatus(null);
    setLoading(false);
  };

  useEffect(() => {
    reload();
    const t = window.setInterval(reload, 15_000);
    return () => window.clearInterval(t);
  }, [appId]);

  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <h2>Status</h2>
          <div className="settings-subtitle">Live health checks (auto-refresh 15s)</div>
        </div>
        <div className="settings-status-toolbar">
          <select
            value={appId ?? ""}
            onChange={(e) => setAppId(e.target.value || null)}
          >
            <option value="">— pick app for per-app status —</option>
            {projects.map((p) => (
              <option key={p.app_id} value={p.app_id}>{p.app_id}</option>
            ))}
          </select>
          <button type="button" onClick={reload} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      </header>

      <div className="settings-panel-body">
      {error && <div className="settings-error">Error: {error}</div>}

      <h3 className="settings-status-section-title">Global</h3>
      <div className="status-grid">
        {globalStatus && (
          <>
            <StatusCardView card={globalStatus.process} extras={[
              `pid ${globalStatus.process.pid}`,
              `${globalStatus.process.host}:${globalStatus.process.port}`,
              `python ${globalStatus.process.python.python_version}`,
            ]}/>
            <StatusCardView
              card={globalStatus.llm}
              extras={[
                globalStatus.llm.model,
                /* LCP.3 / DEC-027 — the LLM card can be sourced from
                 * either the Ollama probe or the llama.cpp probe
                 * depending on Config.provider_kind(). The backend
                 * sets `provider`; we surface it as a small "via X"
                 * extras line so operators can tell at a glance
                 * which local LLM is being probed without opening
                 * the Settings sub-section. */
                globalStatus.llm.provider === "llamacpp"
                  ? "via llama.cpp"
                  : "via Ollama",
                globalStatus.llm.ping_ms !== null ? `${globalStatus.llm.ping_ms}ms` : "",
                `${globalStatus.llm.models_available.length} model(s) available`,
              ]}
              extraClassName={
                globalStatus.llm.provider === "llamacpp"
                  ? "llamacpp-status"
                  : "ollama-status"
              }
            />
            <StatusCardView card={globalStatus.rag_provider} />
            <StatusCardView card={globalStatus.tools.adb} />
            <StatusCardView card={globalStatus.tools.jadx} />
            <StatusCardView card={globalStatus.tools.apktool} />
            <StatusCardView card={globalStatus.tools.frida} />
            <FridaServerStatusCard
              card={globalStatus.tools.frida_server}
              onRefresh={reload}
            />
            <StatusCardView card={globalStatus.device} extras={[
              globalStatus.device.connected
                ? `state: ${globalStatus.device.state ?? "unknown"}`
                : "no device attached",
              globalStatus.device.serial ? `serial: ${globalStatus.device.serial}` : "",
            ]}/>
            <StatusCardView card={globalStatus.filesystem.apps_root} extras={[
              typeof globalStatus.filesystem.apps_root.free_gb === "number"
                ? `${globalStatus.filesystem.apps_root.free_gb} GB free`
                : "",
            ]}/>
          </>
        )}
      </div>

      <h3 className="settings-status-section-title">
        Per-app {appId ? `· ${appId}` : "(pick an app)"}
      </h3>
      {appStatus && !appStatus.device.connected && (
        <div className="settings-banner settings-banner-warn">
          <strong>No device attached.</strong>{" "}
          Device-side checks (installed, running, UID, foreground activity,
          uiautomator dump) were skipped. Start an emulator or connect a
          device to populate them.
        </div>
      )}
      <div className="status-grid">
        {appStatus && (
          <>
            <StatusCardView card={appStatus.meta} extras={[
              appStatus.meta.package,
              appStatus.meta.apk_sha256?.slice(0, 12) ?? "",
            ]}/>
            <StatusCardView card={appStatus.decompile} extras={[
              appStatus.decompile.status,
              appStatus.decompile.file_count !== undefined ? `${appStatus.decompile.file_count} files` : "",
            ]}/>
            <RagStatusCard
              appId={appStatus.app_id}
              ragCard={appStatus.rag}
              decompileReady={appStatus.decompile.status === "ready"}
              onChanged={reload}
            />
            <CallGraphStatusCard
              appId={appStatus.app_id}
              graphCard={appStatus.call_graph}
              decompileReady={appStatus.decompile.status === "ready"}
              onChanged={reload}
            />
            <StatusCardView card={appStatus.device.package_installed} />
            <StatusCardView card={appStatus.device.package_running} />
            <StatusCardView card={appStatus.device.package_uid} />
            <StatusCardView card={appStatus.device.foreground} extras={[
              appStatus.device.foreground.activity != null ? String(appStatus.device.foreground.activity) : "",
            ]}/>
            <StatusCardView card={appStatus.device.uiautomator_dump} />
            <StatusCardView card={appStatus.overrides} extras={[
              `${appStatus.overrides.active_count} active`,
              ...appStatus.overrides.lines,
            ]}/>
          </>
        )}
      </div>
      </div>
    </div>
  );
}

function StatusCardView({
  card,
  extras,
  actions,
  extraClassName,
}: {
  card: StatusCard;
  extras?: (string | undefined)[];
  actions?: ReactNode;
  /** Optional CSS class appended to the outer status-card div. LCP.3 /
   *  DEC-027 uses this to tag the LLM card with ``ollama-status`` /
   *  ``llamacpp-status`` so the App.css namespace can render
   *  per-provider badges or accent colors without touching the
   *  generic StatusCardView shape. */
  extraClassName?: string;
}) {
  // ``skipped`` cards (e.g. per-app device probes when no device is
  // attached) are not real failures — render them as warn (yellow) so the
  // panel doesn't scream red the moment the emulator isn't booted.
  const skipped = Boolean((card as { skipped?: boolean }).skipped);
  const dot = card.ok ? "ok" : skipped ? "warn" : card.error ? "fail" : "warn";
  const classNames = ["status-card", `status-${dot}`];
  if (extraClassName) classNames.push(extraClassName);
  return (
    <div className={classNames.join(" ")}>
      <div className="status-card-header">
        <span className={`status-card-dot status-card-dot-${dot}`} />
        <strong>{card.label}</strong>
      </div>
      {extras && extras.filter(Boolean).length > 0 && (
        <ul className="status-card-extras">
          {extras.filter(Boolean).map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
      {card.hint && <div className="status-card-hint">{card.hint}</div>}
      {card.error && !skipped && (
        <div className="status-card-error">{String(card.error)}</div>
      )}
      {actions && <div className="status-card-actions">{actions}</div>}
    </div>
  );
}

/**
 * RAG status card with an inline build/rebuild button.
 *
 * The button is gated on the *decompile* cache being ready — the rebuild
 * endpoint returns 409 otherwise, so we'd rather disable than fail.
 *
 * Status → button label/intent:
 *   missing  → "Build now"   (primary)
 *   failed   → "Retry build" (warn)
 *   pending  → "Building…"   (disabled, shows progress)
 *   ready    → "Rebuild"     (subtle; useful after embed-model swap)
 */
function RagStatusCard({
  appId,
  ragCard,
  decompileReady,
  onChanged,
}: {
  appId: string;
  ragCard: AppStatus["rag"];
  decompileReady: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const status = ragCard.status;

  const config: { label: string; className: string; disabled: boolean; title?: string } =
    !decompileReady
      ? {
          label: "Build now",
          className: "primary",
          disabled: true,
          title: "Build the decompile cache first (Decompile cache card → POST /api/decompile)",
        }
      : status === "missing"
      ? { label: "Build now", className: "primary", disabled: false }
      : status === "failed"
      ? { label: "Retry build", className: "warn", disabled: false }
      : status === "pending"
      ? { label: "Building…", className: "", disabled: true }
      : { label: "Rebuild", className: "", disabled: false };

  const onClick = async () => {
    setBusy(true);
    setMsg(null);
    const r = await rebuildRagIndex(appId);
    setBusy(false);
    if (!r.ok) {
      setMsg(`Rebuild failed: ${r.error}`);
      return;
    }
    setMsg(r.data.kicked ? "Build kicked off…" : "Build already in progress.");
    onChanged();
  };

  return (
    <StatusCardView
      card={ragCard}
      extras={[ragCard.status]}
      actions={
        <>
          <button
            type="button"
            className={`status-card-btn ${config.className}`}
            onClick={onClick}
            disabled={config.disabled || busy}
            title={config.title}
          >
            {busy ? "Kicking off…" : config.label}
          </button>
          {msg && <span className="status-card-msg">{msg}</span>}
        </>
      }
    />
  );
}

/**
 * Static call-graph status card with build / rebuild + drop-apktool action.
 *
 * Parallels :func:`RagStatusCard` — same "wait for decompile, then offer
 * a manual rebuild knob" contract — but with a second secondary action
 * (``Rebuild + re-decompile``) that maps to ``POST
 * /api/graph/{app_id}/rebuild?drop_apktool=true``. The auto-builder
 * already kicks in after the decompile cache flips to ready (see
 * ``schedule_call_graph_build_after_decompile`` in
 * ``androscan/web/graph_routes.py``); this button is mostly for the
 * "I changed apktool / parser / Smali" cases where the operator wants
 * to force a re-parse from scratch.
 *
 * Status → button label/intent:
 *   missing  → "Build now"     (primary; auto-build usually beats us to it)
 *   failed   → "Retry build"   (warn)
 *   pending  → "Building…"     (disabled, shows progress)
 *   ready    → "Rebuild"       (subtle; secondary "Re-decompile" wipes apktool)
 */
function CallGraphStatusCard({
  appId,
  graphCard,
  decompileReady,
  onChanged,
}: {
  appId: string;
  graphCard: AppStatus["call_graph"];
  decompileReady: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const status = graphCard.status;

  const config: { label: string; className: string; disabled: boolean; title?: string } =
    !decompileReady
      ? {
          label: "Build now",
          className: "primary",
          disabled: true,
          title: "Build the decompile cache first (Decompile cache card → POST /api/decompile)",
        }
      : status === "missing"
      ? { label: "Build now", className: "primary", disabled: false }
      : status === "failed"
      ? { label: "Retry build", className: "warn", disabled: false }
      : status === "pending"
      ? { label: "Building…", className: "", disabled: true }
      : { label: "Rebuild", className: "", disabled: false };

  const kick = async (dropApktool: boolean) => {
    setBusy(true);
    setMsg(null);
    const r = await rebuildGraph(appId, { dropApktool });
    setBusy(false);
    if (!r.ok) {
      setMsg(`Rebuild failed: ${r.error}`);
      return;
    }
    setMsg(
      r.data.kicked
        ? dropApktool
          ? "Re-decompile + rebuild kicked off…"
          : "Build kicked off…"
        : "Build already in progress.",
    );
    onChanged();
  };

  const extras: (string | undefined)[] = [graphCard.status];
  if (status === "ready") {
    if (typeof graphCard.node_count === "number" && typeof graphCard.edge_count === "number") {
      extras.push(`${graphCard.node_count} nodes / ${graphCard.edge_count} edges`);
    }
    if (typeof graphCard.class_count === "number") {
      const ext = graphCard.external_class_count ?? 0;
      extras.push(`${graphCard.class_count} classes (${ext} external)`);
    }
    if (graphCard.fidelity_level) {
      extras.push(`fidelity: ${graphCard.fidelity_level}`);
    }
  }

  return (
    <StatusCardView
      card={graphCard}
      extras={extras}
      actions={
        <>
          <button
            type="button"
            className={`status-card-btn ${config.className}`}
            onClick={() => kick(false)}
            disabled={config.disabled || busy}
            title={config.title}
          >
            {busy ? "Kicking off…" : config.label}
          </button>
          {decompileReady && status === "ready" && (
            <button
              type="button"
              className="status-card-btn"
              onClick={() => kick(true)}
              disabled={busy}
              title="Drops the apktool/Smali cache and rebuilds the call graph from scratch. Use after replacing the APK or upgrading apktool."
            >
              Re-decompile
            </button>
          )}
          {msg && <span className="status-card-msg">{msg}</span>}
        </>
      }
    />
  );
}

/**
 * Hook Lab readiness card — wraps the generic ``StatusCardView`` with an
 * ABI-aware install playbook for ``frida-server`` (DEC-023 follow-up).
 *
 * When the on-device server isn't running, the card adds a collapsible
 * `<details>` block with the exact download URL + push / install / verify
 * commands the operator needs. The download URL is synthesised from
 * (a) the host frida CLI version returned by the backend (so the device
 * binary will match the host wire-protocol — same major.minor avoids the
 * "version skew: major" failure mode the card already warns about) and
 * (b) the device ABI from ``getprop ro.product.cpu.abi`` mapped to the
 * Frida release filename arch suffix (``arm64-v8a → android-arm64``,
 * etc.). When the ABI is unmapped or no device is attached, we degrade
 * gracefully: link the operator to the releases page rather than build
 * a broken URL.
 *
 * Each command is rendered with a tiny "copy" button next to it
 * (``navigator.clipboard.writeText``) so operators can paste straight
 * into a terminal — preferred over per-line install scripts because the
 * commands need to interleave with their own checks (e.g. ``adb root``
 * succeeded before ``chmod``).
 *
 * Card stays green when the server is running — the playbook is hidden.
 */
function FridaServerStatusCard({
  card,
  onRefresh,
}: {
  card: GlobalStatus["tools"]["frida_server"];
  onRefresh?: () => void | Promise<void>;
}) {
  // ``detection`` tells us how the probe found frida — when it's
  // "frida-ps" we know reachability was confirmed via the host-side
  // wire-protocol enumeration, NOT a device-side process scan, so
  // there's no on-device PID to display. Show a label that makes the
  // detection method explicit instead of the misleading "pid ?".
  const runStatus =
    card.running
      ? card.pid != null
        ? `pid ${card.pid}`
        : card.detection === "frida-ps"
          ? "running (host-confirmed via frida-ps — renamed binary or frida-gadget)"
          : "running"
      : "not running";
  // ``uid`` warning: server is up but as a non-root user (typically
  // ``shell``, when the operator forgot ``adb root`` or ran the binary
  // directly without ``su 0``). Process listing works (frida-ps is
  // unprivileged) but ``device.attach(<pid>)`` against an app fails
  // with ``unable to connect to remote frida-server: closed`` once
  // the per-attach helper hits the ptrace barrier on a non-root server.
  // The Start-as-root button below uses the same signal to decide
  // whether to show itself.
  const uidWarning =
    card.running && card.uid != null && card.uid !== "root"
      ? `running as ${card.uid} — app attaches will fail unless restarted as root`
      : "";
  const extras: (string | undefined)[] = [
    runStatus,
    card.uid ? `uid ${card.uid}` : "",
    uidWarning,
    card.device_version ? `device ${card.device_version}` : "",
    card.host_version ? `host ${card.host_version}` : "",
    card.device_abi ? `abi ${card.device_abi}` : "",
    card.device_build_type ? `build ${card.device_build_type}` : "",
    card.version_skew === "major"
      ? "version skew: major (incompatible)"
      : card.version_skew === "minor"
      ? "version skew: minor"
      : "",
  ];

  // The Start-as-root button shows for two distinct failure modes
  // that share the same fix (``adb shell "su 0 frida-server -D"``):
  //   1. Server is down entirely (``!running``) — most common after
  //      an emulator reboot or a version-skew handshake crashed it.
  //   2. Server is up but as non-root (``uid !== "root"``) — operator
  //      forgot the privilege escalation step.
  // Both states leave Inject broken in identical ways.
  const needsStart = !card.running || (card.uid != null && card.uid !== "root");

  // Only surface the install hint when the device half is down. When
  // running we keep the card terse — the existing version-skew badge
  // already handles the "running but mismatched" case.
  const installHint = !card.running ? <FridaServerInstallHint card={card} /> : null;

  const actions = (
    <>
      {needsStart && <FridaServerStartButton onRefresh={onRefresh} />}
      {installHint}
    </>
  );

  return <StatusCardView card={card} extras={extras} actions={actions} />;
}

/** "Start frida-server (as root)" action button + transient
 *  in-flight / error state. Lives next to the install-hint disclosure
 *  on the Frida-server card.
 *
 *  Refreshes the parent's status payload on success so the operator
 *  sees the card flip from red (or yellow uid-warning) to green
 *  without having to wait for the 15s auto-poll.
 */
function FridaServerStartButton({
  onRefresh,
}: {
  onRefresh?: () => void | Promise<void>;
}) {
  const [inFlight, setInFlight] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const [lastSuccess, setLastSuccess] = useState<string | null>(null);

  const click = async () => {
    setInFlight(true);
    setLastError(null);
    setLastSuccess(null);
    const r = await startFridaServer();
    setInFlight(false);
    if (r.ok) {
      // ``already_running`` distinguishes the no-op idempotent path
      // (button hit twice, second hit is a no-op) from an actual
      // start — both render the same green confirmation.
      setLastSuccess(
        r.data.already_running
          ? "Already running as root."
          : `Started as root (pid ${r.data.pid ?? "?"}).`,
      );
    } else {
      // The route returns a structured detail string for every
      // failure path (see ``frida_routes.start_frida_server``); we
      // surface it verbatim so the operator can read the actionable
      // hint (kill command, install playbook pointer, manual fallback
      // command) inline without opening DevTools.
      setLastError(r.error || `HTTP ${r.status}`);
    }
    // Refresh the card after both success and failure: success so
    // the green state shows; failure so a transient probe state
    // (e.g. server-now-up-but-still-shell after a partial start)
    // re-renders the warning extras correctly.
    if (onRefresh) await onRefresh();
  };

  return (
    <div className="frida-server-start">
      <button
        type="button"
        className="frida-server-start-btn"
        onClick={click}
        disabled={inFlight}
        title="Run `adb shell su 0 /data/local/tmp/frida-server -D` and confirm via re-probe"
      >
        {inFlight ? "Starting…" : "Start frida-server (as root)"}
      </button>
      {lastSuccess && (
        <div className="frida-server-start-ok">{lastSuccess}</div>
      )}
      {lastError && (
        <div className="frida-server-start-err">{lastError}</div>
      )}
    </div>
  );
}

/**
 * Install playbook details — only rendered when ``frida-server`` isn't
 * running. Synthesises the download URL when we have both the host
 * version and a known device ABI; otherwise points at the releases
 * page. Push path follows Frida's documented convention
 * (``/data/local/tmp/frida-server``); ``adb root`` is included because
 * the standard AOSP / Android Studio emulator images need it for the
 * background-fork to actually keep the server alive.
 */
function FridaServerInstallHint({
  card,
}: {
  card: GlobalStatus["tools"]["frida_server"];
}) {
  const version = card.host_version ?? null;
  const fridaArch = card.frida_arch ?? null;
  const abi = card.device_abi ?? null;

  // When we have both halves, build the canonical filename + URL the
  // operator would paste into curl / their browser. The URL pattern is
  // the one Frida advertises on
  // https://github.com/frida/frida/releases — releases are uploaded
  // there with the exact ``frida-server-X.Y.Z-android-<arch>.xz`` shape.
  const filename = version && fridaArch
    ? `frida-server-${version}-android-${fridaArch.replace(/^android-/, "")}.xz`
    : null;
  const downloadUrl = version && fridaArch
    ? `https://github.com/frida/frida/releases/download/${version}/${filename}`
    : null;
  const releaseTagUrl = version
    ? `https://github.com/frida/frida/releases/tag/${version}`
    : "https://github.com/frida/frida/releases/latest";

  // Strip the trailing ``.xz`` for the push step — operators decompress
  // first. Falls back to a placeholder when the version/arch isn't
  // known, but the placeholder is still copy-pasteable as a template.
  const decompressed = filename ? filename.replace(/\.xz$/, "") : "frida-server-<version>-android-<arch>";

  // Root-status branching: the install playbook needs to behave
  // differently depending on whether the device can be rooted.
  //   * can_adb_root === false → operator pasted ``adb root`` will
  //     fail; we hoist a warning banner above the steps explaining
  //     why and what to do instead (recreate the AVD as
  //     AOSP / Google APIs userdebug, or use Magisk).
  //   * device_rooted === true → adbd already runs as uid 0; we
  //     skip the ``adb root`` line in step 4 (it's a no-op-with-noise
  //     on Magisk / eng builds and confuses operators who think they
  //     need to run it).
  //   * Both null → no device attached; we render the playbook as a
  //     template so the operator can see what to expect, but the
  //     "no device" line in the detect blurb already explains the
  //     situation.
  const canAdbRoot = card.can_adb_root;
  const alreadyRooted = card.device_rooted === true;
  const buildType = card.device_build_type;

  return (
    <details className="frida-install-hint">
      <summary>How to install <code>frida-server</code> on the device</summary>
      <div className="frida-install-body">
        <p className="frida-install-detect">
          {abi ? <>Detected device ABI: <code>{abi}</code>{fridaArch ? <> → Frida release arch <code>{fridaArch}</code></> : null}.</> : <>No device detected — start an emulator (or attach a device) and refresh, then come back here for an ABI-aware playbook.</>}
          {version ? <> Match host CLI: <code>frida {version}</code> (same major.minor avoids the "version skew" warning).</> : null}
        </p>
        {!fridaArch && abi && (
          <p className="frida-install-detect frida-install-warn">
            We don't have a Frida arch mapping for <code>{abi}</code> — pick the closest match by hand from the <a href={releaseTagUrl} target="_blank" rel="noopener noreferrer">releases page</a>.
          </p>
        )}
        {canAdbRoot === false && (
          <div className="frida-install-banner frida-install-banner-warn">
            <strong>This device can't be rooted via <code>adb root</code>.</strong>
            <p>
              {buildType === "user"
                ? <>The attached AVD is a production build (<code>ro.build.type=user</code>) — almost certainly a <em>Google Play</em> system image. Step 4 below will fail with <code>adbd cannot run as root in production builds</code>, and Frida needs root to inject.</>
                : <>The attached device's build profile (<code>ro.build.type={buildType ?? "?"}</code> / <code>ro.debuggable=0</code>) refuses the <code>adb root</code> upgrade. Step 4 below will fail and Frida needs root to inject.</>
              }
            </p>
            <p>
              To enable Hook Lab on this device, do <em>one</em> of the following first:
            </p>
            <ul>
              <li>Recreate the AVD in Android Studio's Device Manager picking the <strong>AOSP</strong> or <strong>Google APIs</strong> system image variant (not <em>Google Play</em>) — these ship as <code>userdebug</code> and accept <code>adb root</code>.</li>
              <li>Boot the existing AVD with <code>emulator -avd &lt;name&gt; -writable-system</code>, then push <a href="https://github.com/topjohnwu/Magisk/releases/latest" target="_blank" rel="noopener noreferrer">Magisk</a> or another <code>su</code> provider.</li>
              <li>Use a physical device that's already rooted (Magisk / eng ROM).</li>
            </ul>
          </div>
        )}
        {alreadyRooted && (
          <div className="frida-install-banner frida-install-banner-ok">
            <strong>Device already runs as root</strong> (<code>uid=0</code>) — step 4 below skips the <code>adb root</code> upgrade.
          </div>
        )}
        <ol className="frida-install-steps">
          <li>
            <span className="frida-install-step-label">Download the matching release binary</span>
            {downloadUrl ? (
              <FridaInstallCmd cmd={`curl -L -o ${filename} ${downloadUrl}`} />
            ) : (
              <p className="frida-install-detect">
                Browse <a href={releaseTagUrl} target="_blank" rel="noopener noreferrer">{releaseTagUrl}</a> and download the asset whose filename starts with <code>frida-server-</code> and ends with <code>-android-&lt;arch&gt;.xz</code>.
              </p>
            )}
          </li>
          <li>
            <span className="frida-install-step-label">Decompress (host-side, one-time)</span>
            <FridaInstallCmd cmd={`xz -d ${filename ?? decompressed + ".xz"}`} />
          </li>
          <li>
            <span className="frida-install-step-label">Push to the device</span>
            <FridaInstallCmd cmd={`adb push ${decompressed} /data/local/tmp/frida-server`} />
          </li>
          <li>
            <span className="frida-install-step-label">
              {alreadyRooted
                ? "Make it executable & start it (device already runs as root)"
                : "Make it executable & start it (root needed; emulators run as root)"}
            </span>
            {!alreadyRooted && <FridaInstallCmd cmd={`adb root`} />}
            <FridaInstallCmd cmd={`adb shell "chmod 755 /data/local/tmp/frida-server"`} />
            <FridaInstallCmd cmd={`adb shell "/data/local/tmp/frida-server &" >/dev/null 2>&1 &`} />
          </li>
          <li>
            <span className="frida-install-step-label">Verify it's running</span>
            <FridaInstallCmd cmd={`adb shell pidof frida-server`} />
            <p className="frida-install-detect">
              A non-empty PID means the device half is up — refresh this page and the card should turn green.
            </p>
          </li>
        </ol>
      </div>
    </details>
  );
}

/**
 * One install command rendered with a click-to-copy affordance. Keeps
 * the playbook readable (each command is its own line, monospaced)
 * without forcing operators to triple-click-select.
 */
function FridaInstallCmd({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard API may be unavailable (insecure context, sandboxed
      // browser, etc.) — fall back to a no-op + brief flash so the
      // operator can still triple-click-select the rendered <code>.
      setCopied(false);
    }
  };
  return (
    <div className="frida-install-cmd">
      <code>{cmd}</code>
      <button
        type="button"
        className={copied ? "frida-install-copy-btn copied" : "frida-install-copy-btn"}
        onClick={onCopy}
        title={copied ? "Copied" : "Copy command to clipboard"}
        aria-label={copied ? "Copied" : "Copy command to clipboard"}
      >
        {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
      </button>
    </div>
  );
}

// =============================================================================
// Diagnostics panel (raw JSON for debugging)
// =============================================================================

function DiagnosticsPanel() {
  const [globalSettings, setGlobalSettings] = useState<GlobalSettingsResponse | null>(null);
  const [globalStatus, setGlobalStatus] = useState<GlobalStatus | null>(null);
  const [reloading, setReloading] = useState(false);
  const [reloadMsg, setReloadMsg] = useState<string | null>(null);

  useEffect(() => {
    fetchGlobalSettings().then((r) => r.ok && setGlobalSettings(r.data));
    fetchGlobalStatus().then((r) => r.ok && setGlobalStatus(r.data));
  }, []);

  const onReload = async () => {
    setReloading(true);
    const r = await reloadGlobalSettings();
    setReloading(false);
    if (!r.ok) {
      setReloadMsg(`Reload failed: ${r.error}`);
    } else {
      setReloadMsg("Config re-read from disk.");
      setGlobalSettings(r.data.global);
    }
  };

  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <h2>Diagnostics</h2>
          <div className="settings-subtitle">raw API payloads + uvicorn reload</div>
        </div>
        <button type="button" onClick={onReload} disabled={reloading}>
          {reloading ? "Reloading…" : "Reload config from disk"}
        </button>
      </header>
      {reloadMsg && <div className="settings-banner">{reloadMsg}</div>}
      <div className="settings-panel-body">
        <details open>
          <summary>GET /api/settings/global</summary>
          <pre className="diag-json">{JSON.stringify(globalSettings, null, 2)}</pre>
        </details>
        <details>
          <summary>GET /api/status/global</summary>
          <pre className="diag-json">{JSON.stringify(globalStatus, null, 2)}</pre>
        </details>
      </div>
    </div>
  );
}
