"""Scope inspector — entry/exit hook with `this` field snapshot capture.

The workhorse template for "I want to see what this method *sees* when
it runs": for every overload of a fully-qualified Java method, this
hook captures the entry-time argument list, the post-call return value,
and a snapshot of `this`'s declared instance fields at *both* entry and
exit (post-call). The exit snapshot lets the operator diff against the
entry snapshot to spot field mutations the call performed.

Read-only by design (DEC-023 sub-step 4.6): the original return value
is forwarded verbatim, no mutation surface is exposed. v2 will add a
modify-return UI; this template's contract intentionally does not
foreclose that direction (the JS shape stays the same, only the
implementation hook would change).

Why this is a separate template, not an `entry_exit_log` extension:
field capture walks `this.getClass().getDeclaredFields()` per call,
which is materially more expensive than the existing logger and
produces a larger payload. Operators who only need entry/exit
visibility shouldn't pay that cost; making it a distinct template
keeps the trade-off explicit at the dropdown rather than hidden in a
parameter.

Why fields are stringified the same way as `entry_exit_log`'s args:
predictability. Java fields hold arbitrary instance graphs that
``JSON.stringify`` would silently drop or recurse infinitely on; we
``String(...)`` every value (which falls back to ``toString()`` on
custom types) so the output is always serialisable and consistently
shaped. Operators who need structured field capture should reach for a
hand-rolled script (a 4.5+ free-form editor surface, not v1's
template-only contract).

Output shape (consumed by 4.6's `_summarize_scope` aggregator):

    {label, phase: "ready",  overloads: <int>, fields_captured: <int>}
    {label, phase: "entry",  class, method, args: [...], this_class, this_fields: {f: str, ...}}
    {label, phase: "exit",   class, method, return: str, this_fields: {f: str, ...}}
    {label, phase: "error",  error: str}

The frontend's `ScopeInspectorPanel` filters on the presence of
`this_fields` to recognise scope-inspector events independently of
which session id they came from — i.e. the route layer doesn't need to
tag this template's events specially.
"""

from __future__ import annotations

from androscan.adapters.frida_hooks import HookTemplate, HookTemplateParam


# Frida + str.format double-brace dance: every literal ``{`` / ``}`` in
# the JS body is doubled (``{{`` / ``}}``); the registry-walk render
# test in ``tests/test_frida_hook_templates.py`` enforces this contract
# indirectly (any unescaped brace surfaces as a ``KeyError`` from
# ``str.format`` during the smoke render).
_JS = """\
// AndroScan Hook Lab — scope_inspector
// Class:  {class_name}
// Method: {method_name}
// Label:  {event_label}
//
// Captures `this` field snapshot at entry and exit, plus stringified
// args and return value. Read-only — the original return value is
// forwarded verbatim.
Java.perform(function () {{
  function snapshotFields(instance) {{
    var snap = {{}};
    if (instance == null) {{ return snap; }}
    try {{
      var klass = instance.getClass();
      var fields = klass.getDeclaredFields();
      for (var i = 0; i < fields.length; i++) {{
        var f = fields[i];
        try {{
          f.setAccessible(true);
          var v = f.get(instance);
          snap[f.getName()] = (v === null) ? "null" : String(v);
        }} catch (eField) {{
          // Field-level failures (e.g. SecurityManager rejection on
          // setAccessible, or a getter that throws) shouldn't void the
          // whole snapshot — capture the failure inline so the operator
          // sees *which* field couldn't be read.
          snap[f.getName()] = "<unreadable: " + String(eField) + ">";
        }}
      }}
    }} catch (eOuter) {{
      // Reflection itself failed (rare; usually means the class loader
      // was nuked). Surface as a single sentinel field so the consumer
      // can still tell the snapshot was attempted.
      snap["__error__"] = String(eOuter);
    }}
    return snap;
  }}
  try {{
    var Klass = Java.use("{class_name}");
    var overloads = Klass["{method_name}"].overloads;
    if (!overloads || overloads.length === 0) {{
      send({{ "label": "{event_label}", "phase": "error",
              "error": "method {class_name}.{method_name} not found" }});
      return;
    }}
    var fieldCount = 0;
    try {{
      // Best-effort field count for the ``ready`` signal — purely
      // informational; if it fails we just report 0.
      var probeKlass = Klass.class;
      fieldCount = probeKlass.getDeclaredFields().length;
    }} catch (eProbe) {{
      fieldCount = 0;
    }}
    overloads.forEach(function (overload) {{
      overload.implementation = function () {{
        var args = Array.prototype.slice.call(arguments);
        var thisClassName = "(unknown)";
        try {{ thisClassName = String(this.getClass().getName()); }} catch (eClass) {{ }}
        var entrySnap = snapshotFields(this);
        send({{ "label": "{event_label}", "phase": "entry",
                "class": "{class_name}", "method": "{method_name}",
                "args": args.map(function (a) {{ return String(a); }}),
                "this_class": thisClassName,
                "this_fields": entrySnap }});
        var rv = overload.apply(this, args);
        var exitSnap = snapshotFields(this);
        send({{ "label": "{event_label}", "phase": "exit",
                "class": "{class_name}", "method": "{method_name}",
                "return": String(rv),
                "this_fields": exitSnap }});
        return rv;
      }};
    }});
    send({{ "label": "{event_label}", "phase": "ready",
            "overloads": overloads.length,
            "fields_captured": fieldCount }});
  }} catch (e) {{
    send({{ "label": "{event_label}", "phase": "error", "error": String(e) }});
  }}
}});
"""


_SUMMARY = """\
Hooks every overload of `{class_name}.{method_name}` and emits a `{event_label}` event
for each call carrying a *scope snapshot*:

  • entry  — stringified arguments + a snapshot of every declared instance field on `this`
             (`this_fields: {{fieldName: stringified_value, ...}}`) plus the runtime class
             of `this` (helpful when the hooked class is a parent of the actual instance).
  • exit   — stringified return value + a fresh `this_fields` snapshot taken *after* the
             call returned, so the operator can diff against the entry snapshot to see
             which fields the call mutated.
  • ready  — number of overloads patched + best-effort declared-field count (sanity signal).
  • error  — any JS-side exception during hook setup or invocation.

This is read-only: the original return value is forwarded unchanged. Field-level read
failures (e.g. `setAccessible` rejection, getter throws) are captured inline as
`"<unreadable: ...>"` rather than aborting the snapshot. Captured args / fields / returns
may contain sensitive material (auth tokens, PII, crypto keys, plaintext credentials) —
review the event stream and the Scope Inspector pane before sharing or exporting it.
"""


TEMPLATE = HookTemplate(
    id="scope_inspector",
    name="Scope inspector (this fields + args + return)",
    description=(
        "Hook every overload of a Java method and capture a snapshot of `this`'s "
        "declared instance fields at entry and exit, plus stringified args and "
        "return value. Read-only."
    ),
    params=(
        HookTemplateParam(
            name="class_name",
            description="Fully-qualified Java class, e.g. com.example.LoginManager",
        ),
        HookTemplateParam(
            name="method_name",
            description="Method name (no signature; all overloads are hooked)",
        ),
        HookTemplateParam(
            name="event_label",
            description=(
                "Short tag attached to every emitted event "
                "(used for filtering in the trace + scope panes)"
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
    ),
)
