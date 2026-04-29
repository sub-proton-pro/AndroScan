// jadx in bulk-mode (``jadx -d <out>``) emits its own ``sources/`` and
// ``resources/`` subdirectories under whatever output target you give it.
// AndroScan's decompile cache passes ``apps/<app_id>/.decompiled/<sha>/sources/``
// as that target (see ``androscan.web.decompile_cache.SOURCES_SUBDIR``),
// so the on-disk Java files actually live at
// ``apps/<app_id>/.decompiled/<sha>/sources/sources/<package>/<Class>.java``
// — note the *double* ``sources/`` segment.
//
// Every rel-path producer in the backend (the Inspect-tab class tree
// from ``decompile_cache.build_tree``, the inspect-map deterministic
// grep from ``inspect_map.find_handlers``, and the RAG chunker in
// ``rag.chunking``) computes ``path.relative_to(sources_dir)``, so the
// rel-paths they emit carry the leading ``sources/`` segment from the
// nested jadx layout. Backend consumers (the ``GET /api/code/{app_id}/file``
// route via ``read_source_file``) round-trip those rel-paths back through
// the same ``sources_dir`` join, so they resolve correctly to the
// nested file.
//
// The helpers below bridge between the on-disk rel-path form (which
// has the prefix) and Java/Smali class-descriptor form (which does
// not — packages don't have a ``sources/`` prefix). They strip the
// segment going one way and re-add it going the other, so the
// round-trip ``className → relPath → className`` is the identity for
// every package shape this project ships.
const _JADX_SOURCES_PREFIX = "sources/";

/**
 * Map a Java class name (as stored in the call-graph ``classes.class_name``
 * column) to the relative path of the jadx-decompiled Java file for that
 * class — the form expected by ``/api/code/{app_id}/file?path=...`` and by
 * the ``CodeView`` opened from Lab on a graph-node click.
 *
 * Examples
 * --------
 *   ``com.example.Foo``        → ``sources/com/example/Foo.java``
 *   ``com.example.Foo$Inner``  → ``sources/com/example/Foo.java``
 *                                (inner classes live inside the outer
 *                                file; ``CodeView``'s ``emphasizeMethod``
 *                                prop carries the method-level location)
 *   ``MyClass`` (default pkg)  → ``sources/MyClass.java``
 *
 * The ``sources/`` prefix matches jadx's bulk-mode layout — see the
 * module-level ``_JADX_SOURCES_PREFIX`` comment. Without the prefix
 * the path resolves under ``<sha>/sources/`` directly and the file
 * isn't there, so ``fetchSource`` 404s and the Lab → Inspect
 * "Open in Inspect" handoff silently shows "(failed to load)".
 *
 * Pure function (one regex + one replace + one prepend) — kept in
 * its own module so future viewers (smali side-by-side, v2 reflection
 * traces) can reuse the same name-to-path contract.
 */
export function classNameToJavaRelPath(className: string): string {
  const trimmed = (className || "").trim();
  if (!trimmed) return "";
  const dollarIdx = trimmed.indexOf("$");
  const top = dollarIdx >= 0 ? trimmed.slice(0, dollarIdx) : trimmed;
  return `${_JADX_SOURCES_PREFIX}${top.replace(/\./g, "/")}.java`;
}

/**
 * Map a jadx-decompiled Java relative file path to the Smali type
 * descriptor for that class — the form expected by the call-graph
 * SQLite store and by the ``trace_behavior`` skill's ``entry_method``
 * argument (Phase 10 sub-step 10.8 cross-tab Inspect → Trace seed).
 *
 * Examples
 * --------
 *   ``sources/com/example/Foo.java`` → ``Lcom/example/Foo;``
 *   ``com/example/Foo.java``         → ``Lcom/example/Foo;``
 *                                      (caller-supplied path without
 *                                      the prefix — accepted because
 *                                      a ``sources`` package would be
 *                                      pathological)
 *   ``sources/MyClass.kt``           → ``LMyClass;``
 *   ``sources/com/Foo$Inner.java``   → ``Lcom/Foo$Inner;`` (inner-class
 *                                      disambiguation preserved when
 *                                      the caller passes a ``$``-
 *                                      suffixed path, even though jadx
 *                                      emits one ``.java`` per outer
 *                                      class — used when the caller
 *                                      already knows the inner from a
 *                                      class-tree pick rather than a
 *                                      file)
 *   empty / non-source path          → ``""``
 *
 * Strips the leading ``sources/`` segment if present (see the module
 * comment for why every backend rel-path producer carries it) and the
 * language extension (``.java`` or ``.kt``) before wrapping. Returns
 * the empty string on inputs that don't end in a recognised source
 * extension so callers can short-circuit cleanly.
 *
 * Pure / no I/O.
 */
export function javaRelPathToSmaliClass(relPath: string): string {
  const trimmed = (relPath || "").trim();
  if (!trimmed) return "";
  let stem = trimmed;
  if (stem.startsWith(_JADX_SOURCES_PREFIX)) {
    stem = stem.slice(_JADX_SOURCES_PREFIX.length);
  }
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
 *   ``sources/com/example/Foo.java`` + ``onClick`` → ``Lcom/example/Foo;->onClick(``
 *   ``sources/com/example/Foo.java`` + ``null``    → ``Lcom/example/Foo;->``
 *   ``com/example/Foo.java`` + ``onClick``         → ``Lcom/example/Foo;->onClick(``
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
