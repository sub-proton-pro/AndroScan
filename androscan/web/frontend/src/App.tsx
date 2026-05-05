import { AppPicker } from "./components/AppPicker";
import { HealthDot } from "./components/HealthDot";
import { TabBar } from "./components/TabBar";
import { WorkbenchProvider, useWorkbench } from "./context/WorkbenchContext";
import { InspectTab } from "./tabs/InspectTab";
import { LabTab } from "./tabs/LabTab";
import { ReportsTab } from "./tabs/ReportsTab";
import { SettingsTab } from "./tabs/SettingsTab";

function ActiveTab() {
  const { tab } = useWorkbench();
  // Persistent-mount tabs: Settings + Lab.
  //
  //   * Settings — preserves in-progress edits (form draft, raw YAML
  //     buffer, selected sub-section, fetched data) across tab hops.
  //   * Lab — preserves Trace mode form + active anchor, Manual Hooks
  //     selected-node + active Frida session + right-pane tab,
  //     hook-builder collapse state, etc. Keeping it mounted also
  //     keeps the Frida session polling alive so the operator's
  //     in-flight trace doesn't go silent while they look at Reports
  //     / Inspect (the polling only runs when ``activeSession`` is
  //     set, so there's no idle-tab cost).
  //
  // The Inspect + Reports tabs still unmount/remount on switch —
  // intentional, to release Inspect's mirror WS + logcat WS the moment
  // the operator looks elsewhere. UI Mapping state survives anyway
  // because it's lifted into ``WorkbenchContext`` (``mapResult`` /
  // ``runMapTap``).
  return (
    <>
      {tab === "reports" && <ReportsTab />}
      {tab === "inspect" && <InspectTab />}
      <div className="lab-tab-host" hidden={tab !== "lab"}>
        <LabTab />
      </div>
      <div className="settings-tab-host" hidden={tab !== "settings"}>
        <SettingsTab />
      </div>
    </>
  );
}

function HeaderStatus() {
  const { status, setTab, setPendingSettingsSection } = useWorkbench();
  // Layout: [AppPicker | HealthDot | status text], all pinned to the
  // right edge by ``.status``'s ``margin-left: auto``. AppPicker shares
  // ``appId`` with the Reports sidebar via WorkbenchContext, so picking
  // from either surface updates the other in lockstep. Clicking the
  // dot writes ``pendingSettingsSection: "status"`` *before* flipping
  // the tab so SettingsTab picks up the deep-link signal on its first
  // render and lands the operator on the live-probe panel directly
  // (rather than the default Global settings panel — the operator
  // wants the unhappy probe, not the YAML form).
  const onPillClick = () => {
    setPendingSettingsSection("status");
    setTab("settings");
  };
  return (
    <span className="status">
      <AppPicker />
      <HealthDot onClick={onPillClick} />
      {status}
    </span>
  );
}

export default function App() {
  return (
    <WorkbenchProvider>
      <div className="layout">
        <header className="app-header">
          <h1>AndroScan RE Workbench</h1>
          <TabBar />
          <HeaderStatus />
        </header>
        <main className="app-main">
          <ActiveTab />
        </main>
      </div>
    </WorkbenchProvider>
  );
}
