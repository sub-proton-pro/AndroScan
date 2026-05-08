"""Multi-method dynamic tracer — Phase 13 / DEC-029, sub-step 13.1.

The first backend deliverable for Phase 13 (Behavior Trace v3): a
single Frida hook template that, given a closure of ``(class, method,
descriptor)`` triples, hooks every overload of every triple and emits
a structured trace event stream. The frontend consumes the stream to
render the Behavior Trace flowchart's *dynamic* and *both* modes (the
*static* mode uses only the existing static substrate; this template
is what makes the live overlay possible).

Wire shape (locked in 13.1's planning checkpoint, see
``docs/DECISIONS.md`` DEC-029)
------------------------------------------------------------------

Five ``phase`` values, all flat per-``send({...})`` payloads (matches
the ``entry_exit_log`` / ``scope_inspector`` precedent):

* ``hook_failed`` — emitted **eagerly per-failure during hook setup**.
  Carries ``{label, phase, class, method, descriptor, reason, error}``.
  ``reason`` is one of ``class_not_found`` / ``method_not_found`` /
  ``impl_set_failed`` (the canonical R8-inlining signal). The Inspector
  pane's ``Possibly inlined`` callout consumes the per-event ``reason``
  to pick reason-specific copy.

* ``ready`` — emitted **once** after every ``(class, method)`` in the
  closure has been hook-attempted. Aggregate counts only:
  ``{label, phase, methods_attempted, methods_hooked, methods_failed}``.
  No nested array — the frontend already collected per-failure events.

* ``entry`` — ``{label, phase, class, method, descriptor, seq,
  thread_id, thread_name, thread_depth, parent_call_seq, args}``.
  ``seq`` is monotonic-global (not per-thread). ``thread_id`` is the
  Java view (``Thread.currentThread().getId()``) — operator-meaningful
  even when threads are renamed at the OS level. ``thread_depth`` is
  the depth on this thread *before* this entry pushed (0 = top-level
  call). ``parent_call_seq`` is the ``seq`` of the nearest unmatched
  entry on the same thread; ``null`` if top-level.

* ``exit`` — ``{label, phase, class, method, descriptor, seq,
  entry_seq, thread_id, thread_depth, return}``. ``entry_seq`` links
  back to the matching entry's ``seq``; ``entry_seq: null`` for
  *dangling exits* (Frida attached mid-call — rare but possible).
  ``thread_depth`` is the depth on this thread *after* this exit
  popped.

* ``error`` — runtime invocation error (distinct from setup-time
  ``hook_failed``): ``{label, phase, class, method, seq, entry_seq,
  thread_id, error}``. The original exception is re-thrown after the
  event emission so the app's behaviour is unchanged.

Per-thread state machine (~25 LOC of JS)
----------------------------------------

A monotonic global ``seqCounter`` plus a ``Map<threadId, Frame[]>``
gives every event a unique ``seq`` and every entry an unambiguous
``parent_call_seq``. Locked option (b) from the planning checkpoint
— recursion + re-entrant hooks render correctly in 13.6's flowchart
without the frontend having to infer parent-child links from the
event timeline.

Tier-stringification serialiser (~150 LOC of JS)
-----------------------------------------------

Tier dispatch order (first match wins; per-step ``try/catch`` so one
tier failure falls through to the next, never aborts the event):

1. ``null`` / ``undefined`` -> literal ``"null"``
2. JS primitives (``number`` / ``boolean``) -> ``String(value)``
3. JS strings -> double-quoted, truncated at 256 chars
4. ``byte[]`` -> hex preview (full hex for length <= 32; length +
   first-16-bytes hex for longer arrays)
5. ``android.os.Bundle`` -> ``Bundle{k1=v1, k2=v2, ...}`` via
   ``keySet()`` + per-key ``get(k)``, recurse one level
6. ``android.content.Intent`` -> ``Intent(action="...", extras=...)``;
   extras stringified via the Bundle handler
7. ``java.util.List`` -> ``[a, b, c, ... (N total)]``, first 5
8. ``java.util.Map`` -> ``{k1=v1, ... (N total)}``, first 5
9. **User-defined class — locked option (a)**: try ``String(value)``
   first; if the result matches the default-toString fingerprint
   ``^[\\w$.]+@[0-9a-f]+$``, fall through to reflection. ``toString()``
   wins for app classes that override it (the common, operator-friendly
   case); reflection wins when the class doesn't.
10. Reflection: ``getDeclaredFields()`` + ``setAccessible(true)``,
    capped at 8 fields, recursion depth 1 (fields-of-fields are
    ``String(value)``'d, not reflected). Per-field ``try/catch``
    captures inline as ``<unreadable: ...>`` (mirrors
    ``scope_inspector.py``'s field-snapshot precedent).
11. Final fallback: ``String(value)`` unconditional.

Thresholds (256 chars / 32 bytes / 8 fields / depth 1 / 5 list-or-map
entries) are confirmed in DEC-029's locked design.

Parameters
----------

``methods_json`` is a JS-object-literal-formatted string (i.e. a JSON
array of ``{class, method, descriptor}`` objects, built via
``json.dumps(...)`` at the call site). The ``str.format`` renderer
substitutes the value verbatim into a JS expression context — Python's
substitution does NOT re-interpret braces inside the substituted
value, so the inner JSON braces survive intact (same trick
``custom.py`` uses for ``js_body``). 13.2's ``POST /api/trace/{app_id}/dynamic``
route is the production caller; it serialises the active
``BehaviorAnchor``'s closure into the JSON before render-time.

``event_label`` is a short tag attached to every emitted event, used
by the frontend to filter the WebSocket multiplex (Phase 13 sub-step
13.3 multiplexes ``summary_*`` events on the same channel; the label
disambiguates).

Out of scope for 13.1
---------------------

* HTTP route, WebSocket multiplexing, LLM summariser skill — sub-steps
  13.2 / 13.3 / 13.4.
* Frontend rename + ``ExecutionFlow`` + Inspector pane + mode toggle —
  sub-steps 13.5 -> 13.9.
* Live JVM smoke fixtures for the serialiser — deferred to 13.2's
  integration test once the route can wire the template through to a
  ``FridaSession``. The ``[frida]`` extra's ``pyjsparser`` is enough
  for a CI-time syntax check.
"""

from __future__ import annotations

from androscan.adapters.frida_hooks import HookTemplate, HookTemplateParam


# Frida + str.format double-brace dance: every literal ``{`` / ``}`` in
# the JS body is doubled (``{{`` / ``}}``); enforced indirectly by the
# fail-closed render walk in ``tests/test_frida_hook_templates.py``.
#
# The two ``str.format`` placeholders are ``{methods_json}`` (a JS
# object-literal-formatted string built by the caller via
# ``json.dumps``) and ``{event_label}`` (an opaque short tag). All
# other ``{`` / ``}`` characters are JS syntax and must be doubled.
_JS = """\
// AndroScan Hook Lab - behavior_trace_multi
// Label: {event_label}
//
// Multi-method Frida tracer with per-thread call-stack tracking and
// tiered argument / return-value stringification. Read-only - the
// original return value is forwarded verbatim. Re-thrown exceptions
// are re-thrown after event emission so the app's behaviour is
// unchanged.
//
// Wire shape:
//   hook_failed - one per failed hook setup (class_not_found /
//                 method_not_found / impl_set_failed).
//   ready       - one summary event after all hook attempts.
//   entry       - per Java method invocation (with seq, thread_id,
//                 parent_call_seq, args).
//   exit        - paired with entry by entry_seq; carries return.
//   error       - per runtime exception (mid-call); re-throws.
Java.perform(function () {{
  // ---- Tunable thresholds (locked in DEC-029 / 13.1 checkpoint) ----
  var STR_TRUNCATE_AT = 256;
  var BYTE_FULL_HEX_LIMIT = 32;
  var BYTE_PREVIEW_LIMIT = 16;
  var MAX_FIELDS_PER_OBJECT = 8;
  var MAX_LIST_ENTRIES = 5;
  var DEFAULT_TOSTRING_REGEX = /^[\\w$.]+@[0-9a-f]+$/;

  // ---- State machine: monotonic seq + per-thread call stacks -------
  var seqCounter = 0;
  var threadStacks = {{}};

  function nextSeq() {{
    var s = seqCounter;
    seqCounter = seqCounter + 1;
    return s;
  }}

  function pushFrame(threadId) {{
    var stack = threadStacks[threadId];
    if (!stack) {{
      stack = [];
      threadStacks[threadId] = stack;
    }}
    var parent = stack.length > 0 ? stack[stack.length - 1].seq : null;
    var seq = nextSeq();
    var depth = stack.length;
    stack.push({{ seq: seq }});
    return {{ seq: seq, depth: depth, parent_call_seq: parent }};
  }}

  function popFrame(threadId) {{
    var stack = threadStacks[threadId];
    if (!stack || stack.length === 0) {{
      // Dangling exit: Frida attached mid-call. Rare but possible.
      return {{ entry_seq: null, depth: 0 }};
    }}
    var frame = stack.pop();
    return {{ entry_seq: frame.seq, depth: stack.length }};
  }}

  // ---- Bespoke handler probes (resolved lazily, cached) ------------
  // ``Java.use`` is expensive; we resolve each framework class at most
  // once per session. ``null`` after a probe means the class wasn't
  // available (rare on stock Android, possible in stripped runtimes)
  // and the corresponding tier short-circuits to the next.
  var _Bundle = undefined, _Intent = undefined, _List = undefined, _Map = undefined;
  function probeKlass(name) {{
    try {{ return Java.use(name); }} catch (e) {{ return null; }}
  }}

  // ---- Tier helpers ------------------------------------------------
  function truncateString(s) {{
    if (s == null) return "null";
    if (s.length <= STR_TRUNCATE_AT) return s;
    return s.substring(0, STR_TRUNCATE_AT) + "... (truncated; " + s.length + " total)";
  }}

  function quoteString(s) {{
    var t = truncateString(s);
    return "\\"" + t.replace(/\\\\/g, "\\\\\\\\").replace(/"/g, "\\\\\\"") + "\\"";
  }}

  function bytesToHex(bytes, n) {{
    var hex = [];
    for (var i = 0; i < n; i++) {{
      var b = bytes[i] & 0xff;
      hex.push("0x" + (b < 16 ? "0" : "") + b.toString(16));
    }}
    return hex.join(" ");
  }}

  function tierByteArray(value) {{
    // JS-side ``Array.isArray`` matches Frida's auto-converted byte[].
    if (!Array.isArray(value)) return null;
    try {{
      var len = value.length;
      if (len <= BYTE_FULL_HEX_LIMIT) {{
        return "byte[" + len + "]=" + bytesToHex(value, len);
      }}
      var preview = bytesToHex(value, BYTE_PREVIEW_LIMIT);
      return "byte[" + len + "] preview=\\"" + preview + " ...\\"";
    }} catch (e) {{
      return "<byte[] unreadable: " + String(e) + ">";
    }}
  }}

  function tierBundle(value, depth) {{
    if (_Bundle === undefined) _Bundle = probeKlass("android.os.Bundle");
    if (_Bundle === null) return null;
    try {{
      if (!_Bundle.class.isInstance(value)) return null;
      var keysObj = value.keySet().toArray();
      var parts = [];
      var n = Math.min(keysObj.length, MAX_LIST_ENTRIES);
      for (var i = 0; i < n; i++) {{
        var k = String(keysObj[i]);
        var v;
        try {{ v = value.get(k); }} catch (eGet) {{ v = "<unreadable: " + String(eGet) + ">"; }}
        parts.push(k + "=" + summarise(v, depth + 1));
      }}
      if (keysObj.length > MAX_LIST_ENTRIES) {{
        parts.push("... (" + keysObj.length + " total)");
      }}
      return "Bundle{{" + parts.join(", ") + "}}";
    }} catch (e) {{
      return null;
    }}
  }}

  function tierIntent(value, depth) {{
    if (_Intent === undefined) _Intent = probeKlass("android.content.Intent");
    if (_Intent === null) return null;
    try {{
      if (!_Intent.class.isInstance(value)) return null;
      var action = value.getAction();
      var extras = value.getExtras();
      var actionStr = (action == null) ? "null" : quoteString(String(action));
      var extrasStr;
      if (extras == null) {{
        extrasStr = "null";
      }} else {{
        extrasStr = tierBundle(extras, depth + 1);
        if (extrasStr === null) extrasStr = "Bundle{{...}}";
      }}
      return "Intent(action=" + actionStr + ", extras=" + extrasStr + ")";
    }} catch (e) {{
      return null;
    }}
  }}

  function tierList(value, depth) {{
    if (_List === undefined) _List = probeKlass("java.util.List");
    if (_List === null) return null;
    try {{
      if (!_List.class.isInstance(value)) return null;
      var size = value.size();
      var parts = [];
      var n = Math.min(size, MAX_LIST_ENTRIES);
      for (var i = 0; i < n; i++) {{
        parts.push(summarise(value.get(i), depth + 1));
      }}
      if (size > MAX_LIST_ENTRIES) {{
        parts.push("... (" + size + " total)");
      }}
      return "[" + parts.join(", ") + "]";
    }} catch (e) {{
      return null;
    }}
  }}

  function tierMap(value, depth) {{
    if (_Map === undefined) _Map = probeKlass("java.util.Map");
    if (_Map === null) return null;
    try {{
      if (!_Map.class.isInstance(value)) return null;
      var entrySetArr = value.entrySet().toArray();
      var parts = [];
      var n = Math.min(entrySetArr.length, MAX_LIST_ENTRIES);
      for (var i = 0; i < n; i++) {{
        var entry = entrySetArr[i];
        var k, v;
        try {{ k = entry.getKey(); }} catch (eK) {{ k = "<unreadable>"; }}
        try {{ v = entry.getValue(); }} catch (eV) {{ v = "<unreadable>"; }}
        parts.push(summarise(k, depth + 1) + "=" + summarise(v, depth + 1));
      }}
      if (entrySetArr.length > MAX_LIST_ENTRIES) {{
        parts.push("... (" + entrySetArr.length + " total)");
      }}
      return "{{" + parts.join(", ") + "}}";
    }} catch (e) {{
      return null;
    }}
  }}

  function tierReflectFields(value, depth) {{
    try {{
      var klass = value.getClass();
      var fields = klass.getDeclaredFields();
      var parts = [];
      var n = Math.min(fields.length, MAX_FIELDS_PER_OBJECT);
      for (var i = 0; i < n; i++) {{
        var f = fields[i];
        var fname;
        try {{ fname = String(f.getName()); }} catch (eName) {{ fname = "<f" + i + ">"; }}
        try {{
          f.setAccessible(true);
          var fv = f.get(value);
          var rendered;
          if (depth >= 1) {{
            // Recursion cap: at depth >= 1, fields-of-fields are
            // String()'d directly (no further reflection).
            rendered = (fv == null) ? "null" : truncateString(String(fv));
          }} else {{
            rendered = summarise(fv, depth + 1);
          }}
          parts.push(fname + "=" + rendered);
        }} catch (eField) {{
          parts.push(fname + "=<unreadable: " + String(eField) + ">");
        }}
      }}
      if (fields.length > MAX_FIELDS_PER_OBJECT) {{
        parts.push("... (" + fields.length + " total)");
      }}
      var className;
      try {{ className = String(klass.getName()); }} catch (eC) {{ className = "?"; }}
      var simpleName = className.substring(className.lastIndexOf(".") + 1);
      return simpleName + "{{" + parts.join(", ") + "}}";
    }} catch (e) {{
      return null;
    }}
  }}

  // ---- Tier dispatcher --------------------------------------------
  function summarise(value, depth) {{
    if (depth === undefined) depth = 0;
    // Tier 1: null / undefined
    if (value === null || value === undefined) return "null";
    // Tier 2: JS primitives (number / boolean)
    var t = typeof value;
    if (t === "number" || t === "boolean") return String(value);
    // Tier 3: JS strings
    if (t === "string") return quoteString(value);
    try {{
      // Tier 4: byte[] (Frida exposes Java byte[] as a JS Array)
      var byteStr = tierByteArray(value);
      if (byteStr !== null) return byteStr;
      // Tier 5: android.os.Bundle
      var bundleStr = tierBundle(value, depth);
      if (bundleStr !== null) return bundleStr;
      // Tier 6: android.content.Intent
      var intentStr = tierIntent(value, depth);
      if (intentStr !== null) return intentStr;
      // Tier 7: java.util.List
      var listStr = tierList(value, depth);
      if (listStr !== null) return listStr;
      // Tier 8: java.util.Map
      var mapStr = tierMap(value, depth);
      if (mapStr !== null) return mapStr;
      // Tier 9: try toString() first; fall through to reflection if it
      // matches the default Object.toString() fingerprint.
      var stringified;
      try {{ stringified = String(value); }} catch (eStr) {{ stringified = null; }}
      if (stringified !== null && !DEFAULT_TOSTRING_REGEX.test(stringified)) {{
        return truncateString(stringified);
      }}
      // Tier 10: reflection over declared fields (depth-bounded)
      if (depth < 1) {{
        var reflected = tierReflectFields(value, depth);
        if (reflected !== null) return reflected;
      }}
      // Tier 11: final fallback
      return (stringified === null) ? "<unstringifiable>" : truncateString(stringified);
    }} catch (e) {{
      try {{ return truncateString(String(value)); }} catch (e2) {{ return "<unstringifiable>"; }}
    }}
  }}

  // ---- Hook setup loop ---------------------------------------------
  // ``methodsList`` is rendered by the Python ``str.format`` step from
  // the ``methods_json`` parameter (a JS-object-literal-formatted
  // string built via ``json.dumps``). The substituted value is a
  // syntactically-valid JS array literal; no JSON.parse is needed.
  var methodsList = {methods_json};

  if (!Array.isArray(methodsList)) {{
    send({{ "label": "{event_label}", "phase": "ready",
            "methods_attempted": 0, "methods_hooked": 0, "methods_failed": 0,
            "error": "methods_json did not render to a JS array" }});
    return;
  }}

  if (methodsList.length === 0) {{
    send({{ "label": "{event_label}", "phase": "ready",
            "methods_attempted": 0, "methods_hooked": 0, "methods_failed": 0 }});
    return;
  }}

  // Cache the Java.lang.Thread handle once; ``currentThread()`` is
  // cheap, ``Java.use`` is not.
  var _Thread = null;
  try {{ _Thread = Java.use("java.lang.Thread"); }} catch (eT) {{ _Thread = null; }}

  function readThread() {{
    if (_Thread === null) return {{ id: -1, name: "unknown" }};
    try {{
      var t = _Thread.currentThread();
      return {{ id: t.getId(), name: String(t.getName()) }};
    }} catch (eRT) {{
      return {{ id: -1, name: "unknown" }};
    }}
  }}

  var attempted = 0, hooked = 0, failed = 0;

  methodsList.forEach(function (spec) {{
    attempted = attempted + 1;
    var className = spec["class"];
    var methodName = spec["method"];
    var descriptor = spec["descriptor"] || "";

    var Klass;
    try {{
      Klass = Java.use(className);
    }} catch (eClass) {{
      send({{ "label": "{event_label}", "phase": "hook_failed",
              "class": className, "method": methodName, "descriptor": descriptor,
              "reason": "class_not_found", "error": String(eClass) }});
      failed = failed + 1;
      return;
    }}

    var overloads;
    try {{
      overloads = Klass[methodName].overloads;
    }} catch (eOver) {{
      send({{ "label": "{event_label}", "phase": "hook_failed",
              "class": className, "method": methodName, "descriptor": descriptor,
              "reason": "method_not_found", "error": String(eOver) }});
      failed = failed + 1;
      return;
    }}
    if (!overloads || overloads.length === 0) {{
      send({{ "label": "{event_label}", "phase": "hook_failed",
              "class": className, "method": methodName, "descriptor": descriptor,
              "reason": "method_not_found", "error": null }});
      failed = failed + 1;
      return;
    }}

    var hookedAny = false;
    overloads.forEach(function (overload) {{
      try {{
        overload.implementation = function () {{
          var args = Array.prototype.slice.call(arguments);
          var thr = readThread();
          var frame = pushFrame(thr.id);
          try {{
            send({{ "label": "{event_label}", "phase": "entry",
                    "class": className, "method": methodName, "descriptor": descriptor,
                    "seq": frame.seq, "thread_id": thr.id, "thread_name": thr.name,
                    "thread_depth": frame.depth, "parent_call_seq": frame.parent_call_seq,
                    "args": args.map(function (a) {{ return summarise(a, 0); }}) }});
          }} catch (eEnt) {{ /* event emit failure is non-fatal */ }}

          var rv;
          var threw = null;
          try {{
            rv = overload.apply(this, args);
          }} catch (eRun) {{
            threw = eRun;
          }}
          var popped = popFrame(thr.id);

          if (threw !== null) {{
            try {{
              send({{ "label": "{event_label}", "phase": "error",
                      "class": className, "method": methodName,
                      "seq": nextSeq(), "entry_seq": popped.entry_seq,
                      "thread_id": thr.id, "error": String(threw) }});
            }} catch (eErr) {{ /* swallow */ }}
            throw threw;
          }}

          try {{
            send({{ "label": "{event_label}", "phase": "exit",
                    "class": className, "method": methodName, "descriptor": descriptor,
                    "seq": nextSeq(), "entry_seq": popped.entry_seq,
                    "thread_id": thr.id, "thread_depth": popped.depth,
                    "return": summarise(rv, 0) }});
          }} catch (eEx) {{ /* swallow */ }}

          return rv;
        }};
        hookedAny = true;
      }} catch (eImpl) {{
        send({{ "label": "{event_label}", "phase": "hook_failed",
                "class": className, "method": methodName, "descriptor": descriptor,
                "reason": "impl_set_failed", "error": String(eImpl) }});
      }}
    }});

    if (hookedAny) {{
      hooked = hooked + 1;
    }} else {{
      failed = failed + 1;
    }}
  }});

  send({{ "label": "{event_label}", "phase": "ready",
          "methods_attempted": attempted,
          "methods_hooked": hooked,
          "methods_failed": failed }});
}});
"""


_SUMMARY = """\
Multi-method dynamic tracer. Hooks every overload of every `(class, method, descriptor)`
triple supplied in `methods_json` and emits `{event_label}` events on the trace stream:

  • hook_failed - one event per failed hook setup. `reason` is one of:
                    class_not_found    - Java.use(class) raised; class likely not loaded
                                          at hook time (try widening to a parent class,
                                          or trigger the path that loads it before re-running).
                    method_not_found   - method signature not present at runtime; likely
                                          renamed by R8, or the static analyzer recorded a
                                          stale signature.
                    impl_set_failed    - Java.use(class) succeeded but assigning
                                          `overload.implementation = ...` raised. This is
                                          the canonical R8-inlining signal: Frida cannot
                                          hook inlined code. The Inspector pane surfaces
                                          this as a `Possibly inlined` callout.
  • ready       - one summary event after all hook attempts complete:
                    {{ methods_attempted, methods_hooked, methods_failed }}.
  • entry       - per Java method invocation:
                    seq               - monotonic global event id;
                    thread_id         - Java view (Thread.currentThread().getId());
                    thread_name       - operator-friendly thread name;
                    thread_depth      - call depth on this thread BEFORE this entry;
                    parent_call_seq   - seq of the nearest unmatched entry on this
                                        thread; null if top-level;
                    args              - tier-stringified argument list (see below).
  • exit        - paired with the matching entry by `entry_seq`. Carries the
                  tier-stringified return value and the post-pop `thread_depth`.
                  `entry_seq: null` for dangling exits (Frida attached mid-call).
  • error       - runtime exception during method invocation; the original exception
                  is re-thrown after emission so the app's behaviour is unchanged.

Argument / return-value stringification tiers (first match wins; per-step try/catch so
one tier failure falls through to the next):

  1. null / undefined        -> "null"
  2. primitives (num / bool) -> direct
  3. strings                 -> quoted, truncated at 256 chars
  4. byte[]                  -> hex preview (full hex if length <= 32, else length +
                                first 16 bytes)
  5. android.os.Bundle       -> Bundle{{ k1=v1, ... }} via keySet() + per-key get(k)
  6. android.content.Intent  -> Intent(action="...", extras=...)
  7. java.util.List          -> [a, b, c, ... (N total)], first 5 entries
  8. java.util.Map           -> {{ k1=v1, ... (N total) }}, first 5 entries
  9. user-defined class      -> toString() if it overrides Object.toString();
                                otherwise reflect declared fields (capped at 8)
 10. final fallback          -> String(value) unconditional.

This is read-only: the original return value is forwarded verbatim. Captured args /
returns / fields may contain sensitive material (auth tokens, PII, crypto keys,
plaintext credentials) - review the event stream before sharing or exporting it.
The `methods_json` parameter is built by the calling layer (Phase 13's
`POST /api/trace/{{app_id}}/dynamic` route) from the active BehaviorAnchor's closure;
operators authoring by hand should pass a JSON array of `{{class, method, descriptor}}`
objects.
"""


TEMPLATE = HookTemplate(
    id="behavior_trace_multi",
    name="Behavior trace (multi-method dynamic tracer)",
    description=(
        "Hook every overload of every (class, method) pair in a BehaviorAnchor "
        "closure and emit per-thread, depth-aware entry / exit / hook_failed / "
        "ready / error events with tier-stringified args and return values. "
        "Read-only. Phase 13 / DEC-029 sub-step 13.1."
    ),
    params=(
        HookTemplateParam(
            name="methods_json",
            description=(
                "JS-object-literal-formatted JSON array of "
                "{class, method, descriptor} objects to hook. Built by the "
                "calling layer via json.dumps(closure)."
            ),
        ),
        HookTemplateParam(
            name="event_label",
            description=(
                "Short tag attached to every emitted event "
                "(used for filtering on the trace + summary WebSocket multiplex)"
            ),
        ),
    ),
    js_template=_JS,
    pentester_summary_template=_SUMMARY,
    sensitive_apis=(
        "Java.use",
        "Java.perform",
        "Class.getDeclaredFields",
        "Field.setAccessible",
        "overload.implementation",
    ),
)
