/**
 * Map a Java class name (as stored in the call-graph ``classes.class_name``
 * column) to the relative path of the jadx-decompiled Java file for that
 * class — the form expected by ``/api/code/{app_id}/file?path=...`` and by
 * the ``CodeView`` opened from Hook Lab on a graph-node click.
 *
 * Examples
 * --------
 *   ``com.example.Foo``        → ``com/example/Foo.java``
 *   ``com.example.Foo$Inner``  → ``com/example/Foo.java`` (inner classes
 *                                live inside the outer file; ``CodeView``'s
 *                                ``emphasizeMethod`` prop carries the
 *                                method-level location)
 *   ``MyClass`` (default pkg)  → ``MyClass.java``
 *
 * This is a pure function (one regex + one replace) — kept in its own
 * module so future viewers (smali side-by-side, ``v2`` reflection traces)
 * can reuse the same name-to-path contract.
 */
export function classNameToJavaRelPath(className: string): string {
  const trimmed = (className || "").trim();
  if (!trimmed) return "";
  // Drop the inner-class suffix; jadx emits one .java per top-level class.
  const dollarIdx = trimmed.indexOf("$");
  const top = dollarIdx >= 0 ? trimmed.slice(0, dollarIdx) : trimmed;
  return `${top.replace(/\./g, "/")}.java`;
}
