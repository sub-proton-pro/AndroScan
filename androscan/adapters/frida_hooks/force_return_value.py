"""Force-return-value override — replace a Java method's return with a fixed JS literal.

Phase 10 / DEC-024 sub-step 10.4. The first of the three *override*
templates (alongside ``force_method_skip`` and
``force_string_compare_equal``) emitted by the bypass planner.

Workflow it serves
------------------

The Behavior Trace flow identifies a gate decision whose predicate
register comes from a method call (``MethodCallOrigin``). The bypass
planner picks this template, fills in the called method's identity
(``class_name`` + ``method_name``) and a deterministic literal that
flips the gate toward the operator-classified ALLOW branch
(``return_value_expr``). The operator stages the rendered hook via
the existing Manual Hooks (formerly Hook Lab) flow and injects.

JS / summary contract
---------------------

Same shape as ``entry_exit_log`` / ``scope_inspector``: every
overload of the named method is replaced; entry / forced / error /
ready events are emitted on the operator-supplied ``event_label`` so
the Lab UI's session pane filters are uniform across templates.
``return_value_expr`` is rendered **as raw JS** — a planner-supplied
``"true"`` / ``"false"`` / ``"null"`` / ``"1"`` literal goes in
verbatim. Operators authoring by hand (Manual Hooks mode) can put
any expression in here that evaluates in the Java.perform context
(e.g. ``"Java.use('com.example.Result').$new(true)"``).

**Risk** is LOW per DEC-024's risk taxonomy: this template hijacks
exactly one named method, no app-wide interception. The
``pentester_summary_template`` calls out that the original method
body is *not run* — side effects (logs, network calls, state
mutations) the original method performed are skipped — so operators
can spot deny gates whose deny side itself has important side effects
(rare but not impossible).
"""

from __future__ import annotations

from androscan.adapters.frida_hooks import HookTemplate, HookTemplateParam


# Frida + str.format double-brace dance: every literal ``{`` / ``}`` in
# the JS body is doubled (``{{`` / ``}}``); enforced indirectly by the
# fail-closed render walk in ``tests/test_frida_hook_templates.py``.
_JS = """\
// AndroScan Hook Lab — force_return_value
// Class:        {class_name}
// Method:       {method_name}
// Forced value: {return_value_expr}
// Label:        {event_label}
//
// Replaces every overload of the named method with a stub that emits
// an event and returns the operator-supplied literal verbatim. The
// original method body is NOT executed — any side effects (logging,
// network calls, state mutations) it performed are skipped.
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
        var forced = ({return_value_expr});
        send({{ "label": "{event_label}", "phase": "forced",
                "class": "{class_name}", "method": "{method_name}",
                "args": args.map(function (a) {{ return String(a); }}),
                "return": String(forced) }});
        return forced;
      }};
    }});
    send({{ "label": "{event_label}", "phase": "ready",
            "overloads": overloads.length,
            "forced_value": "{return_value_expr}" }});
  }} catch (e) {{
    send({{ "label": "{event_label}", "phase": "error", "error": String(e) }});
  }}
}});
"""


_SUMMARY = """\
Hijacks every overload of `{class_name}.{method_name}` and forces it to return the literal
`{return_value_expr}` (rendered as raw JavaScript inside the Frida hook). Each call emits a
`{event_label}` event:

  • forced  — the args the caller passed + the value we returned in their place
  • ready   — number of overloads patched + the forced literal (sanity signal)
  • error   — any JS-side exception during hook setup or invocation

The original method body is NOT executed — any side effects it performed (logging,
network calls, state mutations) are skipped. This is the bypass-planner's preferred
move when a gate's predicate value comes from a method call: forcing the source
method's return flips the gate's verdict at the most precise possible point.
"""


TEMPLATE = HookTemplate(
    id="force_return_value",
    name="Force return value",
    description=(
        "Hijack every overload of a Java method and force its return to a fixed "
        "JS literal (true / false / 1 / 0 / null / hand-rolled instance). The "
        "original method body is NOT executed."
    ),
    params=(
        HookTemplateParam(
            name="class_name",
            description="Fully-qualified Java class, e.g. com.example.LicenseChecker",
        ),
        HookTemplateParam(
            name="method_name",
            description="Method name (no signature; all overloads are forced)",
        ),
        HookTemplateParam(
            name="return_value_expr",
            description=(
                "Raw JavaScript expression for the forced return value "
                "(e.g. \"true\", \"false\", \"1\", \"0\", \"null\", \"\\\"OK\\\"\")"
            ),
        ),
        HookTemplateParam(
            name="event_label",
            description="Short tag attached to every emitted event (used for filtering in the UI)",
        ),
    ),
    js_template=_JS,
    pentester_summary_template=_SUMMARY,
    sensitive_apis=("Java.use", "Java.perform", "overload.implementation"),
)
