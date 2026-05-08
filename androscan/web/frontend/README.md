# AndroScan RE Workbench (frontend)

Vite + React + TypeScript with `react-resizable-panels`. Build output goes to `../static/` for FastAPI to serve.

## Layout

- `src/App.tsx` — top-level shell (header + `<TabBar>` + active tab).
- `src/context/WorkbenchContext.tsx` — shared state (selected `app_id` / `run_ts`, projects, runs, report, per-tab chat history) + URL hash routing (`#/reports`, `#/inspect`, `#/lab`, `#/settings`). Bookmarks of `#/hook` (the pre-rename id) are silently rewritten to `#/lab` so legacy links still land in the right place.
- `src/components/`
  - `TabBar.tsx` — switches between the four top-level tabs.
  - `ProjectsSidebar.tsx` — Projects + Runs picker (used by `Reports` today; reusable later).
  - `ChatDock.tsx` — per-tab chat dock skeleton (history + input + context preview + guardrails footer); back-end wiring lands in step 2 of the Phase 6→9 UX rollout.
- `src/tabs/`
  - `ReportsTab.tsx` — projects sidebar | findings (cards) + chat | raw `report.json`.
  - `InspectTab.tsx` — click-to-code (UI element ↔ component mapping) for the Mirror flow.
  - `LabTab.tsx` — three-mode workspace (renamed from `HookLabTab.tsx` in Phase 10 sub-step 10.6). A thin left rail switches between **Trace** (UI ➜ Behavior Trace ➜ `ExecutionFlow` flowchart + `Inspector` pane + bypass plans, default; the placeholder lives in `LabTraceMode.tsx` with the full `BehaviorAnchorCard` / `BehaviorTrace` / `ExecutionFlow` / `Inspector` / `BypassPlanCard` UI; Phase 13 sub-step 13.5 renamed "Decision Timeline" → "Behavior Trace"; 13.6 → 13.8 added the flowchart + Inspector + Static / Dynamic / Both mode toggle on top; the one-release `VITE_BEHAVIOR_TRACE_LEGACY` rollback flag + the legacy `DecisionTimeline` component were removed at sub-step 13.10's docs sweep), **Manual Hooks** (the legacy three-column Hook Lab layout with CallGraph + CodeView + HookBuilder + Sessions), and **Graph** (a dedicated full-pane CallGraphView). Mode selection persists in `localStorage["androscan.lab.mode"]`.
  - `LabTraceMode.tsx` — Trace mode placeholder (10.6); validates the new `/api/trace` endpoints end-to-end and surfaces the per-app trace cache stats.
- `src/api/trace.ts` — client for the per-app Behavior Trace cache (`/api/trace/{app_id}/...`); locked wire shape matches `androscan.internal.trace_cache.anchor_to_json`.

Every left/center/right column is horizontally resizable; sub-panes (e.g. logcat under the class tree, chat under the center pane) are vertically resizable. Sizes persist per pane group via `autoSaveId` (localStorage).

```bash
npm install
npm run dev
```

`npm run dev` proxies `/api` and `/ws` to `http://127.0.0.1:8420` — start the Python server first (`python androscan.py --serve` from repo root).

Production build:

```bash
npm run build
```

Then open the app via `python androscan.py --serve` (serves `static/index.html` and `/assets/*`).
