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
  // Settings is mounted persistently and hidden via CSS so the user's in-progress
  // edits (form draft, raw YAML buffer, selected sub-section, fetched data)
  // survive tab hops. The other tabs unmount/remount as before — keeps live
  // resources (mirror WS, logcat WS) tied to actual tab visibility.
  return (
    <>
      {tab === "reports" && <ReportsTab />}
      {tab === "inspect" && <InspectTab />}
      {tab === "lab" && <LabTab />}
      <div className="settings-tab-host" hidden={tab !== "settings"}>
        <SettingsTab />
      </div>
    </>
  );
}

function HeaderStatus() {
  const { status, setTab } = useWorkbench();
  // Layout: [AppPicker | HealthDot | status text], all pinned to the
  // right edge by ``.status``'s ``margin-left: auto``. AppPicker shares
  // ``appId`` with the Reports sidebar via WorkbenchContext, so picking
  // from either surface updates the other in lockstep.
  return (
    <span className="status">
      <AppPicker />
      <HealthDot onClick={() => setTab("settings")} />
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
