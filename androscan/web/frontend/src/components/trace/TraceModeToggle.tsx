/**
 * Behavior Trace v3 — Static / Dynamic / Both mode toggle
 * (Phase 13 sub-step 13.8 / DEC-029).
 *
 * Three-pill toggle row that sits above ``ExecutionFlow``. Drives
 * the flowchart's overlay rendering:
 *
 *   * **Static** — the Phase 13.6 default. Shows the static analysis
 *     verdict palette (allow / deny / neutral / unverdicted) on every
 *     edge with no fired-edge accent. The only mode that's useful
 *     before any dynamic trace has been run, so it's the default
 *     when ``hasDynamicData === false``.
 *   * **Dynamic** — only the *fired* edges are rendered with full
 *     emphasis (accent-blue solid per DEC-029); untaken edges are
 *     gray-dashed at 55% opacity. Fired nodes get the depth pill
 *     ("d:N · t:M" — depth-N call on thread M) overlay. Useful
 *     when the operator wants to focus on what the runtime actually
 *     hit and ignore the static analysis.
 *   * **Both** — overlay mode. Static verdicts on the edges (allow
 *     green / deny red / neutral gray) PLUS fired-edge accent
 *     (accent-blue solid stroke wins over the verdict color). The
 *     post-first-run default per the DEC-029 lock — once the
 *     operator has dynamic data, "Both" is the most informative
 *     mode (static plan + runtime confirmation in one view).
 *
 * Persistence: the active mode is stored in ``localStorage`` keyed
 * on the app id (``androscan/trace/mode/${appId}``) so the operator's
 * last choice survives reloads + cross-tab nav within the same app.
 * Different apps get independent state (a "Dynamic" preference on
 * app A doesn't bleed into app B's trace pane).
 *
 * Default mode rule (locked at DEC-029):
 *   1. ``localStorage`` hit for this app id → use that.
 *   2. ``hasDynamicData === true`` (the operator has run at least
 *      one dynamic trace this session) → "both".
 *   3. Otherwise → "static".
 *
 * The ``hasDynamicData`` signal is computed by the parent against
 * ``firedMethods.size > 0`` from :func:`useDynamicTrace` so the
 * toggle's default flips automatically the first time an event
 * lands. Subsequent runs honor the operator's last manual choice
 * (the ``localStorage`` hit always wins over the auto-default).
 *
 * Out of scope for v1:
 *   * Cross-anchor mode persistence (the localStorage key is
 *     per-app, NOT per-(app, anchor) — operator dogfood will tell
 *     us whether an "anchor changed → reset to static" UX is
 *     wanted; v1 keeps the per-app key for simplicity).
 *   * Keyboard navigation between pills (arrow keys); current
 *     surface is click + tab. v2 candidate.
 */

import { useCallback, useEffect, useState } from "react";

export type TraceMode = "static" | "dynamic" | "both";

const ALL_MODES: TraceMode[] = ["static", "dynamic", "both"];

const LS_KEY_PREFIX = "androscan/trace/mode/";

function readPersistedMode(appId: string): TraceMode | null {
  try {
    const raw = window.localStorage.getItem(`${LS_KEY_PREFIX}${appId}`);
    if (raw && (ALL_MODES as readonly string[]).includes(raw)) {
      return raw as TraceMode;
    }
  } catch {
    // ``localStorage.getItem`` can throw in privacy mode / when
    // the storage quota is full — fall back to the auto-default.
  }
  return null;
}

function persistMode(appId: string, mode: TraceMode): void {
  try {
    window.localStorage.setItem(`${LS_KEY_PREFIX}${appId}`, mode);
  } catch {
    /* ignore */
  }
}

/** Compute the initial mode for a given (appId, hasDynamicData)
 *  pair. Pure so the unit-test seam is trivial; consumers just
 *  call this in a ``useState`` initialiser. */
export function initialTraceMode(
  appId: string | null,
  hasDynamicData: boolean,
): TraceMode {
  if (appId) {
    const persisted = readPersistedMode(appId);
    if (persisted) return persisted;
  }
  return hasDynamicData ? "both" : "static";
}

type Props = {
  /** Active app id — drives the localStorage key. ``null`` disables
   *  persistence (the toggle still works, just doesn't write). */
  appId: string | null;
  /** ``true`` when at least one dynamic-trace event has landed in
   *  this session. Drives the auto-default flip from
   *  ``"static"`` → ``"both"`` on first event. */
  hasDynamicData: boolean;
  /** Operator's currently-selected mode. Controlled component —
   *  the parent owns the state so a mode change can drive
   *  ExecutionFlow's overlay rendering on the same render commit. */
  mode: TraceMode;
  /** Operator picked a different mode. */
  onModeChange: (mode: TraceMode) => void;
};

const PILL_LABEL: Record<TraceMode, string> = {
  static: "Static",
  dynamic: "Dynamic",
  both: "Both",
};

const PILL_TITLE: Record<TraceMode, string> = {
  static:
    "Show only the static analysis verdicts. Default before any dynamic trace has been run.",
  dynamic:
    "Show only what the runtime actually fired. Untaken edges fade out; fired methods get a depth pill overlay.",
  both:
    "Overlay the runtime fires on the static analysis. Default after the first dynamic trace — best operator-facing view in most cases.",
};

export function TraceModeToggle({
  appId,
  hasDynamicData,
  mode,
  onModeChange,
}: Props) {
  // Auto-default flip: when ``hasDynamicData`` flips from false to
  // true AND the operator hasn't manually picked a mode yet (no
  // localStorage hit), bump the mode to "both". We track the
  // "operator has manually picked" signal via a local flag rather
  // than re-reading localStorage each render — the flag flips on
  // the first ``onModeChange`` call below.
  const [operatorPicked, setOperatorPicked] = useState<boolean>(() =>
    appId ? readPersistedMode(appId) !== null : false,
  );

  useEffect(() => {
    if (!appId) return;
    setOperatorPicked(readPersistedMode(appId) !== null);
  }, [appId]);

  useEffect(() => {
    if (!hasDynamicData) return;
    if (operatorPicked) return;
    if (mode === "both") return;
    onModeChange("both");
  }, [hasDynamicData, operatorPicked, mode, onModeChange]);

  const handleClick = useCallback(
    (m: TraceMode) => {
      if (m === mode) return;
      setOperatorPicked(true);
      if (appId) persistMode(appId, m);
      onModeChange(m);
    },
    [mode, appId, onModeChange],
  );

  return (
    <div
      className="trace-mode-toggle"
      role="tablist"
      aria-label="Trace overlay mode"
    >
      {ALL_MODES.map((m) => {
        const isActive = m === mode;
        const isDisabled = (m === "dynamic" || m === "both") && !hasDynamicData;
        const titleSuffix =
          isDisabled && !hasDynamicData
            ? " — run a dynamic trace first to enable this mode."
            : "";
        return (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={isActive}
            disabled={isDisabled}
            className={[
              "trace-mode-toggle-pill",
              `trace-mode-toggle-pill-${m}`,
              isActive && "trace-mode-toggle-pill-active",
              isDisabled && "trace-mode-toggle-pill-disabled",
            ]
              .filter(Boolean)
              .join(" ")}
            onClick={() => handleClick(m)}
            title={`${PILL_TITLE[m]}${titleSuffix}`}
          >
            {PILL_LABEL[m]}
          </button>
        );
      })}
    </div>
  );
}
