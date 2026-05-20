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
      /**
       * Which probe layer confirmed reachability:
       *   * ``"pidof"`` — `adb shell pidof frida-server` matched (canonical install).
       *   * ``"ps"`` — `adb shell ps -A` found a `frida-server*` comm (versioned binary).
       *   * ``"frida-ps"`` — host-side `frida-ps -U` succeeded but the
       *     on-device process name didn't match (renamed/stealth binary
       *     or `frida-gadget` injected into the target app). ``pid`` is
       *     ``null`` in this case because no on-device PID was observed.
       *   * ``null`` — not running.
       *
       * The Settings card uses this to label host-confirmed reachability
       * as "running (host-confirmed via frida-ps)" rather than the
       * confusing "pid ?".
       */
      detection: "pidof" | "ps" | "frida-ps" | null;
      /**
       * Device-side username the server runs as
       * (``"root"`` | ``"shell"`` | ``"u0_a123"`` | ``...``);
       * ``null`` when we couldn't determine it (host-confirmed-only
       * detection where there's no on-device PID, or ``ps -A`` itself
       * failed).
       *
       * The Settings card surfaces a yellow warning AND amber-tints
       * the card dot when this is anything other than ``"root"``,
       * INCLUDING the ``null`` case (treated as "unverified, presumed
       * non-root" per v2.1.11): ``device.attach(<pid>)`` against an
       * app process needs CAP_SYS_PTRACE, which only root holds on
       * stock Android. A ``"shell"`` server lets ``frida-ps`` succeed
       * (process enumeration is unprivileged) but every Inject fails
       * with ``unable to connect to remote frida-server: closed`` once
       * the per-attach helper hits the ptrace barrier. ``uid == null``
       * with ``detection === "frida-ps"`` (host-side enumeration only)
       * can't prove root either, so we defensively assume the worst —
       * see ``FridaServerStatusCard``'s ``unverifiedRoot`` signal.
       *
       * Drives the visibility of the Start-as-root action button on
       * the Frida-server card AND a per-mode Diagnose playbook
       * (``FridaServerDiagnoseHint`` — surfaces the kill command for
       * the known-non-root case, and the ``ps -A | grep -iE
       * 'frida|gadget'`` discovery step for the unverified case).
       * Button + playbook show whenever ``running === false`` OR
       * (``running`` AND uid is not confirmed ``"root"``).
       */
      uid: string | null;
      /**
       * Whether ``re.frida.helper`` (the per-attach ptrace shim
       * frida-server forks) is currently observable in ``ps -A``.
       *
       * Present during an active attach, absent in steady state — so
       * ``false`` is the normal idle case and is NOT an error signal
       * by itself. Plumbed through for diagnostics; no UX wired off
       * of it directly today.
       */
      helper_running: boolean;
      host_version: string | null;
      device_version: string | null;
      version_skew: null | "minor" | "major";
      /**
       * Device CPU ABI from ``getprop ro.product.cpu.abi`` (e.g.
       * ``"arm64-v8a"``) and its mapping to the Frida release
       * filename arch suffix (``"android-arm64"``). Both ``null``
       * when no device is attached; ``frida_arch`` may also be
       * ``null`` for ABIs we don't have a mapping for (in which
       * case the install hint links to the releases page rather
       * than synthesising a download URL).
       */
      device_abi: string | null;
      frida_arch: string | null;
      /**
       * Device root-status fields used by the install playbook to
       * warn the operator before they paste ``adb root`` into a
       * production AVD that will refuse with *"adbd cannot run as
       * root in production builds"*. All three are ``null`` when no
       * device is attached.
       *
       * ``can_adb_root`` is the single boolean the UI gates on — it
       * rolls up build-type + debuggable + current-uid into the one
       * signal that matters: "will step 4 work?".
       *
       * ``device_rooted`` (default adb shell already runs as uid 0)
       * lets the playbook skip the ``adb root`` step entirely on
       * Magisk-rooted devices / eng builds.
       *
       * ``device_build_type`` (``"user"`` / ``"userdebug"`` / ``"eng"``)
       * is surfaced verbatim so the warning can name the actual
       * cause instead of a generic message.
       */
      device_rooted: boolean | null;
      can_adb_root: boolean | null;
      device_build_type: string | null;
    };
  };
  device: StatusCard & {
    connected: boolean;
    state: string | null;
    serial: string | null;
  };
  llm: StatusCard & {
    /** Discriminator for the LCP.3 / DEC-027 provider switch. The
     *  backend ``_gather_global`` runs exactly one local-LLM probe
     *  per request keyed on ``Config.provider_kind()``; this field
     *  lets the Settings tab render a "via Ollama" / "via llama.cpp"
     *  extras line without re-deriving the provider from the
     *  operator-typed ``llm_provider`` (which can drift). Cloud
     *  users keep getting ``"ollama"`` here in v1 — the cloud LLM
     *  status card is a future ship. */
    provider: "ollama" | "llamacpp";
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
  /**
   * Static call graph status (Hook Lab v1 / DEC-023). Mirrors the shape of
   * ``androscan.analysis.call_graph.IndexStatus.to_dict()``: ``status`` is
   * one of ``missing | pending | ready | failed`` plus build-time counts /
   * timestamps once the SQLite index is populated. Auto-builds in the
   * background after the decompile cache flips to ``ready``; the Settings
   * card exposes a manual rebuild knob (incl. drop-apktool re-decompile)
   * for the rare case where parser drift or APK swap demands a hard reset.
   */
  call_graph: StatusCard & {
    status: "missing" | "pending" | "ready" | "failed";
    sha?: string | null;
    fidelity_level?: string | null;
    parser_version?: string | null;
    built_at?: number | null;
    finished_at?: number | null;
    class_count?: number | null;
    external_class_count?: number | null;
    node_count?: number | null;
    edge_count?: number | null;
    db_path?: string | null;
  };
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
