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

/**
 * Map a jadx-decompiled Java relative file path to the Smali type
 * descriptor for that class — the form expected by the call-graph
 * SQLite store and by the ``trace_behavior`` skill's ``entry_method``
 * argument (Phase 10 sub-step 10.8 cross-tab Inspect → Trace seed).
 *
 * Examples
 * --------
 *   ``com/example/Foo.java``   → ``Lcom/example/Foo;``
 *   ``MyClass.kt``             → ``LMyClass;``
 *   ``com/Foo$Inner.java``     → ``Lcom/Foo$Inner;`` (inner-class
 *                                disambiguation preserved when the
 *                                caller passes a ``$``-suffixed path,
 *                                even though jadx emits one .java per
 *                                outer class — used when the caller
 *                                already knows the inner from a class
 *                                tree pick rather than a file)
 *   empty / non-source path    → ``""``
 *
 * Pure / no I/O. Strips the language extension (``.java`` or ``.kt``)
 * before wrapping. Returns the empty string on inputs that don't end
 * in a recognised source extension so callers can short-circuit cleanly.
 */
export function javaRelPathToSmaliClass(relPath: string): string {
  const trimmed = (relPath || "").trim();
  if (!trimmed) return "";
  let stem = trimmed;
  if (stem.endsWith(".java")) stem = stem.slice(0, -5);
  else if (stem.endsWith(".kt")) stem = stem.slice(0, -3);
  else return "";
  return `L${stem};`;
}

/**
 * Build a Smali entry-method *prefix* from a Java rel path + optional
 * method name. The result is a string the operator can paste-in or
 * extend in the Trace mode form to fire ``trace_behavior``:
 *
 *   ``com/example/Foo.java`` + ``onClick`` → ``Lcom/example/Foo;->onClick(``
 *   ``com/example/Foo.java`` + ``null``    → ``Lcom/example/Foo;->``
 *
 * The descriptor list is intentionally left for the operator to fill
 * in — the deterministic resolver doesn't carry per-overload parameter
 * descriptors, so seeding a partial signature is the most we can do
 * honestly without guessing. Returns the empty string on an empty
 * ``relPath`` so callers can short-circuit cleanly.
 */
export function javaRelPathToSmaliMethodPrefix(
  relPath: string,
  methodName?: string | null,
): string {
  const klass = javaRelPathToSmaliClass(relPath);
  if (!klass) return "";
  const m = (methodName || "").trim();
  return m ? `${klass}->${m}(` : `${klass}->`;
}
