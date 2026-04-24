import { useMemo, useState } from "react";
import {
  fetchTree,
  getDecompileStatus,
  startDecompile,
  type CodeClass,
  type CodePackage,
  type CodeTree,
  type DecompileStatus,
} from "../api/code";

type Selection = { rel_path: string; class_name: string; method?: string };

type Props = {
  appId: string | null;
  status: DecompileStatus | null;
  tree: CodeTree | null;
  filter: string;
  onFilterChange: (s: string) => void;
  onTreeLoaded: (t: CodeTree | null) => void;
  onStatus: (s: DecompileStatus) => void;
  onSelect: (sel: Selection) => void;
  /** Target app's package (e.g. "com.example.weakbank") used to separate
   *  the user's own code from third-party / framework libraries. */
  appPackage: string | null;
};

/** Common Android, AndroidX, Kotlin, Google, JetBrains and popular library
 *  prefixes that almost always belong to the SDK / dependencies, not the
 *  app under test. */
const FRAMEWORK_PREFIXES = [
  "android.",
  "androidx.",
  "com.android.",
  "com.google.",
  "com.facebook.",
  "com.squareup.",
  "com.bumptech.",
  "kotlin",
  "kotlinx.",
  "org.jetbrains.",
  "org.intellij.",
  "org.json.",
  "org.apache.",
  "org.slf4j.",
  "io.reactivex.",
  "io.netty.",
  "io.grpc.",
  "io.opencensus.",
  "io.opentelemetry.",
  "rx.",
  "dagger.",
  "javax.",
  "java.",
  "junit.",
  "org.junit.",
  "org.hamcrest.",
  "okhttp3.",
  "okio.",
  "retrofit2.",
];

function isAppPackage(pkgName: string, appPackage: string | null): boolean {
  if (!pkgName) return false;
  if (appPackage) {
    if (pkgName === appPackage) return true;
    if (pkgName.startsWith(appPackage + ".")) return true;
    // Also recognise *ancestors* of the dossier package so the parent
    // namespace (e.g. ``com.example.weakbank`` when the dossier reports
    // ``com.example.weakbank.low``) lands in the App bucket too. We
    // require at least 3 segments to avoid catching bare ``com`` or
    // ``com.example`` roots that vendors and unrelated apps share.
    if (
      appPackage.startsWith(pkgName + ".") &&
      pkgName.split(".").length >= 3
    ) {
      return true;
    }
  }
  for (const p of FRAMEWORK_PREFIXES) {
    if (pkgName === p || pkgName.startsWith(p)) return false;
  }
  if (appPackage) return false;
  return true;
}

export function ClassMethodTree({
  appId,
  status,
  tree,
  filter,
  onFilterChange,
  onTreeLoaded,
  onStatus,
  onSelect,
  appPackage,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [openPkgs, setOpenPkgs] = useState<Record<string, boolean>>({});
  const [openCls, setOpenCls] = useState<Record<string, boolean>>({});
  const [showApp, setShowApp] = useState(true);
  const [showLibs, setShowLibs] = useState(false);

  const { appPackages, libPackages } = useMemo(() => {
    if (!tree) return { appPackages: [] as CodePackage[], libPackages: [] as CodePackage[] };
    const q = filter.trim().toLowerCase();
    const filterOne = (p: CodePackage): CodePackage | null => {
      if (!q) return p;
      const matchPkg = p.name.toLowerCase().includes(q);
      const classes = p.classes.filter(
        (c) =>
          matchPkg ||
          c.name.toLowerCase().includes(q) ||
          c.methods.some((m) => m.toLowerCase().includes(q)),
      );
      return classes.length ? { name: p.name, classes } : null;
    };
    const app: CodePackage[] = [];
    const lib: CodePackage[] = [];
    for (const p of tree.packages) {
      const kept = filterOne(p);
      if (!kept) continue;
      (isAppPackage(kept.name, appPackage) ? app : lib).push(kept);
    }
    app.sort((a, b) => a.name.localeCompare(b.name));
    lib.sort((a, b) => a.name.localeCompare(b.name));
    return { appPackages: app, libPackages: lib };
  }, [tree, filter, appPackage]);

  if (!appId) {
    return <p className="muted small">Select a project on the left first.</p>;
  }

  if (!status || status.status === "missing" || status.status === "unknown") {
    return (
      <div className="decompile-prompt">
        <p className="small">
          {status?.status === "unknown"
            ? "No app_meta.json yet — run a full analysis from the CLI first."
            : "Decompile cache not built yet. The Inspect tab needs the full source tree to map taps to handlers."}
        </p>
        {status?.status === "missing" && (
          <button
            type="button"
            className="ghost"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              const s = await startDecompile(appId);
              onStatus(s);
              setBusy(false);
              if (s.status === "ready") {
                const t = await fetchTree(appId);
                onTreeLoaded(t);
              }
            }}
          >
            {busy ? "Starting…" : "Run jadx now"}
          </button>
        )}
      </div>
    );
  }

  if (status.status === "pending") {
    return (
      <div className="decompile-prompt">
        <p className="small">Decompiling APK with jadx (this can take a few minutes for large apps)…</p>
        <button
          type="button"
          className="ghost"
          onClick={async () => {
            const s = await getDecompileStatus(appId);
            onStatus(s);
            if (s.status === "ready") {
              const t = await fetchTree(appId);
              onTreeLoaded(t);
            }
          }}
        >
          Refresh status
        </button>
      </div>
    );
  }

  if (status.status === "failed") {
    return (
      <div className="decompile-prompt">
        <p className="small err">jadx failed: {status.error || "(no detail)"}</p>
        <button
          type="button"
          className="ghost"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            const s = await startDecompile(appId);
            onStatus(s);
            setBusy(false);
          }}
        >
          {busy ? "Retrying…" : "Retry"}
        </button>
      </div>
    );
  }

  const renderPackages = (pkgs: CodePackage[]) =>
    pkgs.map((p) => {
      const pkgKey = p.name;
      const isOpen = openPkgs[pkgKey] ?? filter.trim().length > 0;
      return (
        <li key={pkgKey} className="tree-pkg">
          <button
            type="button"
            className="tree-toggle"
            onClick={() => setOpenPkgs((s) => ({ ...s, [pkgKey]: !isOpen }))}
          >
            <span className="tree-caret">{isOpen ? "▾" : "▸"}</span>
            <span className="tree-name">{p.name}</span>
            <span className="muted small"> ({p.classes.length})</span>
          </button>
          {isOpen && (
            <ul className="tree-classes">
              {p.classes.map((c) => renderClass(c, pkgKey, openCls, setOpenCls, onSelect, filter))}
            </ul>
          )}
        </li>
      );
    });

  const empty = appPackages.length === 0 && libPackages.length === 0;

  return (
    <div className="class-tree">
      <div className="class-tree-toolbar">
        <input
          type="search"
          placeholder="filter packages / classes / methods"
          value={filter}
          onChange={(e) => onFilterChange(e.target.value)}
          className="filter-input"
        />
        <span className="muted small">
          {tree ? `${appPackages.length} app · ${libPackages.length} lib` : ""}
        </span>
      </div>

      <div className="tree-section">
        <button
          type="button"
          className="tree-section-head tree-section-head-btn"
          onClick={() => setShowApp((v) => !v)}
          aria-expanded={showApp}
          title={appPackage ?? "no package known"}
        >
          <span className="tree-caret">{showApp ? "▾" : "▸"}</span>
          <span className="tree-section-title">App packages</span>
          <span className="muted small tree-section-count">
            {appPackages.length}
          </span>
        </button>
        {showApp &&
          (appPackages.length === 0 ? (
            <p className="muted small tree-empty">
              No app-owned packages matched. Adjust the filter or check that the
              APK was decompiled.
            </p>
          ) : (
            <ul className="tree-root">{renderPackages(appPackages)}</ul>
          ))}
      </div>

      <div className="tree-section">
        <button
          type="button"
          className="tree-section-head tree-section-head-btn"
          onClick={() => setShowLibs((v) => !v)}
          aria-expanded={showLibs}
        >
          <span className="tree-caret">{showLibs ? "▾" : "▸"}</span>
          <span className="tree-section-title">Android / library packages</span>
          <span className="muted small tree-section-count">{libPackages.length}</span>
        </button>
        {showLibs &&
          (libPackages.length === 0 ? (
            <p className="muted small tree-empty">No library packages.</p>
          ) : (
            <ul className="tree-root">{renderPackages(libPackages)}</ul>
          ))}
      </div>

      {empty && <p className="muted small tree-empty">no matches</p>}
    </div>
  );
}

function renderClass(
  c: CodeClass,
  pkg: string,
  openCls: Record<string, boolean>,
  setOpenCls: (f: (s: Record<string, boolean>) => Record<string, boolean>) => void,
  onSelect: (sel: Selection) => void,
  filter: string,
) {
  const key = `${pkg}.${c.name}`;
  const isOpen = openCls[key] ?? filter.trim().length > 0;
  return (
    <li key={key} className="tree-class">
      <button
        type="button"
        className="tree-toggle"
        onClick={() => {
          setOpenCls((s) => ({ ...s, [key]: !isOpen }));
          onSelect({ rel_path: c.rel_path, class_name: c.name });
        }}
      >
        <span className="tree-caret">{c.methods.length ? (isOpen ? "▾" : "▸") : " "}</span>
        <span className="tree-name">{c.name}</span>
        {c.methods.length > 0 && <span className="muted small"> ({c.methods.length})</span>}
      </button>
      {isOpen && c.methods.length > 0 && (
        <ul className="tree-methods">
          {c.methods.map((m) => (
            <li key={m}>
              <button
                type="button"
                className="tree-method"
                onClick={() =>
                  onSelect({ rel_path: c.rel_path, class_name: c.name, method: m })
                }
              >
                {m}()
              </button>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

