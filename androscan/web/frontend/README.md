# AndroScan RE Workbench (frontend)

Vite + React + TypeScript with `react-resizable-panels`. Build output goes to `../static/` for FastAPI to serve.

## Layout

- `src/App.tsx` — top-level shell (header + `<TabBar>` + active tab).
- `src/context/WorkbenchContext.tsx` — shared state (selected `app_id` / `run_ts`, projects, runs, report, per-tab chat history) + URL hash routing (`#/reports`, `#/inspect`, `#/hook`).
- `src/components/`
  - `TabBar.tsx` — switches between the three tabs.
  - `ProjectsSidebar.tsx` — Projects + Runs picker (used by `Reports` today; reusable later).
  - `ChatDock.tsx` — per-tab chat dock skeleton (history + input + context preview + guardrails footer); back-end wiring lands in step 2 of the Phase 6→9 UX rollout.
- `src/tabs/`
  - `ReportsTab.tsx` — projects sidebar | findings (cards) + chat | raw `report.json`.
  - `InspectTab.tsx` — placeholder layout for click-to-code (step 3).
  - `HookLabTab.tsx` — placeholder layout for code graph + Frida (step 4).

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
