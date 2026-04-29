import { useWorkbench } from "../context/WorkbenchContext";
import type { TabId } from "../types";

const TABS: { id: TabId; label: string }[] = [
  { id: "reports", label: "Reports" },
  { id: "inspect", label: "Inspect" },
  // Phase 10 sub-step 10.6: "Hook Lab" → "Lab" — the tab now hosts
  // Trace + Manual Hooks + Graph modes, so the narrower "Hook Lab"
  // label no longer fits.
  { id: "lab", label: "Lab" },
  { id: "settings", label: "Settings" },
];

export function TabBar() {
  const { tab, setTab } = useWorkbench();
  return (
    <nav className="tab-bar" role="tablist" aria-label="Workbench tabs">
      {TABS.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={tab === t.id}
          className={tab === t.id ? "tab tab-active" : "tab"}
          onClick={() => setTab(t.id)}
        >
          {t.label}
        </button>
      ))}
    </nav>
  );
}
