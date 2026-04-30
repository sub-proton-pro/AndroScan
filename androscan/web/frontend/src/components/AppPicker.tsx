/**
 * Global app selector rendered in the top-right of the header, just to
 * the left of the ``HealthDot``.
 *
 * Reads + writes ``appId`` from ``WorkbenchContext``, so it shares state
 * with the Reports tab's ``ProjectsSidebar`` — picking from either
 * surface updates the other immediately, and whatever is currently
 * selected in Reports is what the dropdown shows on first paint.
 *
 * Intentionally minimal:
 *   * No refresh button — Reports tab's sidebar already has one, and
 *     the global picker is meant to be a "jump to project" shortcut,
 *     not a project-management surface.
 *   * No "(none)" option — once an operator has picked a project the
 *     workbench keeps it selected until they explicitly switch. If
 *     ``projects`` is empty (cold start before scanning anything) the
 *     ``<select>`` is disabled and shows a placeholder.
 */

import { useWorkbench } from "../context/WorkbenchContext";

const PLACEHOLDER_VALUE = "__none__";

export function AppPicker() {
  const { projects, appId, setAppId } = useWorkbench();

  const empty = projects.length === 0;
  const value = appId ?? PLACEHOLDER_VALUE;

  return (
    <select
      className="app-picker"
      value={value}
      disabled={empty}
      onChange={(e) => {
        const v = e.target.value;
        setAppId(v === PLACEHOLDER_VALUE ? null : v);
      }}
      title={empty ? "No projects yet — scan an APK to get started" : "Select project"}
      aria-label="Select project"
    >
      <option value={PLACEHOLDER_VALUE} disabled>
        {empty ? "No projects" : "Select project…"}
      </option>
      {projects.map((p) => (
        <option key={p.app_id} value={p.app_id}>
          {p.app_id}
        </option>
      ))}
    </select>
  );
}
