import { HealthDot } from "./components/HealthDot";
import { TabBar } from "./components/TabBar";
import { WorkbenchProvider, useWorkbench } from "./context/WorkbenchContext";
import { HookLabTab } from "./tabs/HookLabTab";
import { InspectTab } from "./tabs/InspectTab";
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
      {tab === "hook" && <HookLabTab />}
      <div className="settings-tab-host" hidden={tab !== "settings"}>
        <SettingsTab />
      </div>
    </>
  );
}

function HeaderStatus() {
  const { status, setTab } = useWorkbench();
  return (
    <span className="status">
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
