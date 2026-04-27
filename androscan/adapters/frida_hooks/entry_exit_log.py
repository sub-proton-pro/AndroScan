"""Entry/exit logger — hook every overload of a single Java method.

This is the workhorse template: paste in a fully-qualified class name
and a method name, and the script logs every entry (with stringified
arguments) and every exit (with the stringified return value) for every
overload. Read-only: the original return value is forwarded verbatim.

The script intentionally calls ``String(...)`` on args and the return
value rather than ``JSON.stringify`` — Java objects often don't have a
clean JSON representation, and Frida's ``send()`` would silently drop
fields with non-serialisable values. Stringification is lossy on
custom types (it falls back to ``Object.toString()``), but that is
fine for the "watch this method" workflow this template targets;
operators who need structured field capture should use a hand-rolled
script in 4.5's free-form editor (when that surface exists).
"""

from __future__ import annotations

from androscan.adapters.frida_hooks import HookTemplate, HookTemplateParam


_JS = """\
// AndroScan Hook Lab — entry_exit_log
// Class:  {class_name}
// Method: {method_name}
// Label:  {event_label}
Java.perform(function () {{
  try {{
    var Klass = Java.use("{class_name}");
    var overloads = Klass["{method_name}"].overloads;
    if (!overloads || overloads.length === 0) {{
      send({{ "label": "{event_label}", "phase": "error",
              "error": "method {class_name}.{method_name} not found" }});
      return;
    }}
    overloads.forEach(function (overload) {{
      overload.implementation = function () {{
        var args = Array.prototype.slice.call(arguments);
        send({{ "label": "{event_label}", "phase": "entry",
                "class": "{class_name}", "method": "{method_name}",
                "args": args.map(function (a) {{ return String(a); }}) }});
        var rv = overload.apply(this, args);
        send({{ "label": "{event_label}", "phase": "exit",
                "class": "{class_name}", "method": "{method_name}",
                "return": String(rv) }});
        return rv;
      }};
    }});
    send({{ "label": "{event_label}", "phase": "ready",
            "overloads": overloads.length }});
  }} catch (e) {{
    send({{ "label": "{event_label}", "phase": "error", "error": String(e) }});
  }}
}});
"""


_SUMMARY = """\
Hooks every overload of `{class_name}.{method_name}` and emits a `{event_label}` event for each call:

  • entry   — stringified arguments
  • exit    — stringified return value
  • error   — any JS-side exception during hook setup or invocation
  • ready   — number of overloads patched (sanity signal)

This is read-only: the original return value is forwarded unchanged. Captured args / returns
may contain sensitive material (auth tokens, PII, crypto keys, plaintext credentials)
depending on the method — review the event stream before sharing or exporting it.
"""


TEMPLATE = HookTemplate(
    id="entry_exit_log",
    name="Method entry/exit logger",
    description=(
        "Hook every overload of a Java method and stream entry / exit events with "
        "stringified args and return value. Read-only."
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
            description="Short tag attached to every emitted event (used for filtering in the UI)",
        ),
    ),
    js_template=_JS,
    pentester_summary_template=_SUMMARY,
    sensitive_apis=("Java.use", "Java.perform"),
)
