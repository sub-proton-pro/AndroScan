/**
 * Shared "is this package the app's own code?" heuristic used by every
 * Workbench pane that visualises packages — Inspect tab's
 * ``ClassMethodTree`` and Hook Lab's ``CallGraphView`` (call-graph
 * package overview).
 *
 * Why this lives here
 * -------------------
 * The Hook Lab call-graph backend doesn't distinguish "the app's
 * compiled code" from "bundled libraries shipped inside the APK"
 * (Material, AndroidX, Kotlin stdlib, OkHttp, …). They're all
 * ``is_external=0`` because they all live under the apktool ``smali``
 * trees on disk.
 * That's correct from the parser's point of view but wrong for the
 * default operator workflow — a typical APK has 200+ library packages
 * burying the 2-3 packages the operator actually wrote, and the
 * frontend's ``limit: 5000`` page on ``fetchGraph`` strands the user's
 * own code in the long tail when the parser walks libraries first
 * (alphabetically ``androidx.`` < ``com.example.…``).
 *
 * The Inspect tab solved this earlier by splitting packages into App vs
 * Libs buckets via the dossier's package_name + a ``FRAMEWORK_PREFIXES``
 * denylist. Lifting that helper here lets Hook Lab reuse exactly the
 * same heuristic so both panes agree on "what's the app".
 *
 * Returned semantics (mirrors the original ``ClassMethodTree`` impl
 * verbatim — no behaviour change):
 *
 * 1. Empty/missing ``pkgName`` → ``false`` (treat as not-app).
 * 2. ``appPackage`` known and ``pkgName`` is the app's package, a
 *    descendant of it (``com.example.weakbank.low.*``), or a 3+-segment
 *    ancestor (``com.example.weakbank`` when dossier reports
 *    ``com.example.weakbank.low``) → ``true``. The 3-segment ancestor
 *    floor avoids catching bare ``com`` / ``com.example`` that vendors
 *    and unrelated apps share.
 * 3. ``pkgName`` matches a known framework prefix → ``false``.
 * 4. Fallback: ``false`` if ``appPackage`` is known (we couldn't
 *    confirm), ``true`` if not (best-effort — anything that's not
 *    obviously a framework is presumed app code).
 */

/** Common Android, AndroidX, Kotlin, Google, JetBrains and popular library
 *  prefixes that almost always belong to the SDK / dependencies, not the
 *  app under test. */
export const FRAMEWORK_PREFIXES: readonly string[] = [
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

export function isAppPackage(
  pkgName: string,
  appPackage: string | null,
): boolean {
  if (!pkgName) return false;
  if (appPackage) {
    if (pkgName === appPackage) return true;
    if (pkgName.startsWith(appPackage + ".")) return true;
    // Recognise *ancestors* of the dossier package so the parent
    // namespace (e.g. ``com.example.weakbank`` when the dossier reports
    // ``com.example.weakbank.low``) lands in the App bucket too. The
    // 3-segment floor avoids catching bare ``com`` or ``com.example``
    // roots that vendors and unrelated apps share.
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

/**
 * Best-effort "ancestor prefix" used as a SQL ``LIKE`` argument for
 * ``/api/graph/{app_id}?package_prefix=...``. Given a dossier package
 * like ``com.example.weakbank.low``, returns ``com.example.weakbank``
 * so a sibling-flavour app (``.medium`` / ``.high``) under the same
 * parent is still included. Falls back to the input package when the
 * 3-segment floor can't be cleared, and to ``null`` when no app
 * package is known (caller treats this as "no prefix filter").
 */
export function appPackagePrefix(appPackage: string | null): string | null {
  if (!appPackage) return null;
  const segs = appPackage.split(".");
  if (segs.length >= 4) {
    // Drop the last segment so siblings of the dossier package are
    // included. Floor at 3 segments to keep the prefix meaningful
    // (``com.example`` is too broad).
    return segs.slice(0, -1).join(".");
  }
  return appPackage;
}
