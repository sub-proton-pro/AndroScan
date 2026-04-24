import { useState } from "react";
import { useWorkbench } from "../context/WorkbenchContext";
import { IconChevronLeft, IconRefresh } from "./Icons";

type Props = {
  onCollapse?: () => void;
};

export function ProjectsSidebar({ onCollapse }: Props) {
  const {
    projects,
    appId,
    setAppId,
    runs,
    runTs,
    setRunTs,
    refreshProjects,
  } = useWorkbench();
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      await refreshProjects();
    } finally {
      // Hold the spinner visible for a brief moment so even instant local
      // refreshes register as a click for the user.
      setTimeout(() => setRefreshing(false), 250);
    }
  };

  return (
    <div className="sidebar-inner">
      <div className="sidebar-section">
        <div className="sidebar-section-head">
          <h2>Projects</h2>
          <div className="sidebar-section-actions">
            <button
              type="button"
              className={`ghost-mini icon-btn${refreshing ? " spinning" : ""}`}
              onClick={handleRefresh}
              disabled={refreshing}
              title={refreshing ? "Refreshing…" : "Refresh projects"}
              aria-label="Refresh projects"
            >
              <IconRefresh />
            </button>
            {onCollapse && (
              <button
                type="button"
                className="ghost-mini icon-btn"
                onClick={onCollapse}
                title="Collapse sidebar"
                aria-label="Collapse sidebar"
              >
                <IconChevronLeft />
              </button>
            )}
          </div>
        </div>
        <ul>
          {projects.length === 0 && <li className="muted">No projects yet</li>}
          {projects.map((p) => (
            <li key={p.app_id}>
              <button
                type="button"
                className={p.app_id === appId ? "active" : ""}
                onClick={() => setAppId(p.app_id)}
              >
                {p.app_id}
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="sidebar-section">
        <h2>Runs</h2>
        <ul>
          {!appId && <li className="muted">Select a project</li>}
          {appId && runs.length === 0 && <li className="muted">No runs</li>}
          {runs.map((r) => (
            <li key={r.run_timestamp}>
              <button
                type="button"
                className={r.run_timestamp === runTs ? "active" : ""}
                onClick={() => setRunTs(r.run_timestamp)}
              >
                {r.run_timestamp}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
