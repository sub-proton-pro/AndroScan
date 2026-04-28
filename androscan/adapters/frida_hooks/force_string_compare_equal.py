"""Force `String.equals` / `String.equalsIgnoreCase` to return true for a target literal.

Phase 10 / DEC-024 sub-step 10.4. The third of the three *override*
templates emitted by the bypass planner.

Workflow it serves
------------------

The textbook secret-comparison gate:

.. code-block:: java

    String input = getUserInput();
    if (input.equals("LICENSE_VALID_42")) {  // ← matches the secret
        grantAccess();
    } else {
        denyAccess();
    }

The Behavior Trace flow identifies the gate; the slicer (10.2)
records the predicate origin as
``MethodCallOrigin(method=Ljava/lang/String;->equals(Ljava/lang/Object;)Z)``;
the classifier (10.3) typically scores the deny branch with the
"forbidden / denied / unauthorised" string-keyword regex when the
deny side has a user-facing message; the bypass planner picks this
template and pre-fills ``target_literal`` with the secret string
discovered in the gate's basic block (e.g. ``"LICENSE_VALID_42"``).

Override semantics
------------------

The hook intercepts every call to ``java.lang.String.equals(Object)``
*and* ``java.lang.String.equalsIgnoreCase(String)`` (both are common
in license-style checks; equalsIgnoreCase covers case-folded
comparisons against UPPERCASE constants). For each call, the hook
checks whether *either side* of the comparison stringifies to
``target_literal`` — covering both ``input.equals(SECRET)`` and
``SECRET.equals(input)`` orderings — and force-returns ``true`` when
either side matches. Calls whose strings don't involve the literal
are forwarded to the original ``equals`` implementation unchanged.

**Risk** is MEDIUM per DEC-024's risk taxonomy. The hook is
*app-wide* (every ``String.equals`` call goes through it), but it
only *acts* on calls involving ``target_literal`` — calls comparing
unrelated strings still get the original semantics. False positives
are limited to other gates in the same app that happen to compare
against the exact same literal (rare for non-trivial tokens). The
summary surfaces this trade-off so operators understand the
blast-radius before injecting.

Why this isn't `force_return_value` on `String.equals`
------------------------------------------------------

A plain ``force_return_value`` on ``String.equals`` would force
*every* equals call to return the same value, which would break the
app catastrophically (the JVM uses ``String.equals`` internally for
class loading, hash table lookups, etc.). This template's literal-
gated check is what makes the override survivable.
"""

from __future__ import annotations

from androscan.adapters.frida_hooks import HookTemplate, HookTemplateParam


# Frida + str.format double-brace dance: every literal ``{`` / ``}`` in
# the JS body is doubled (``{{`` / ``}}``); enforced indirectly by the
# fail-closed render walk in ``tests/test_frida_hook_templates.py``.
_JS = """\
// AndroScan Hook Lab — force_string_compare_equal
// Target literal: {target_literal}
// Label:          {event_label}
//
// Hooks java.lang.String.equals(Object) AND
// java.lang.String.equalsIgnoreCase(String) app-wide. For each call,
// if EITHER the receiver OR the argument stringifies to the
// operator-supplied target literal, the hook force-returns true.
// Calls whose strings don't involve the literal are forwarded to the
// original equals implementation unchanged.
Java.perform(function () {{
  var TARGET = "{target_literal}";
  var hooked = 0;
  try {{
    var Str = Java.use("java.lang.String");
    Str.equals.overload("java.lang.Object").implementation = function (other) {{
      var receiver = String(this);
      var argStr = (other === null) ? "null" : String(other);
      if (receiver === TARGET || argStr === TARGET) {{
        send({{ "label": "{event_label}", "phase": "forced",
                "method": "String.equals",
                "receiver": receiver, "arg": argStr,
                "matched": "true",
                "return": "true" }});
        return true;
      }}
      return this.equals(other);
    }};
    hooked++;
    Str.equalsIgnoreCase.overload("java.lang.String").implementation = function (other) {{
      var receiver = String(this);
      var argStr = (other === null) ? "null" : String(other);
      // equalsIgnoreCase is case-folded — match either side against
      // the target by normalising both. Operators typically supply a
      // mixed-case literal here; this hook fires when a case-folded
      // receiver / arg matches it.
      var t = TARGET.toLowerCase();
      if (receiver.toLowerCase() === t || argStr.toLowerCase() === t) {{
        send({{ "label": "{event_label}", "phase": "forced",
                "method": "String.equalsIgnoreCase",
                "receiver": receiver, "arg": argStr,
                "matched": "true",
                "return": "true" }});
        return true;
      }}
      return this.equalsIgnoreCase(other);
    }};
    hooked++;
    send({{ "label": "{event_label}", "phase": "ready",
            "overloads_hooked": hooked,
            "target_literal": TARGET }});
  }} catch (e) {{
    send({{ "label": "{event_label}", "phase": "error", "error": String(e) }});
  }}
}});
"""


_SUMMARY = """\
Hooks `java.lang.String.equals(Object)` AND `java.lang.String.equalsIgnoreCase(String)`
app-wide and force-returns `true` whenever EITHER the receiver OR the argument
stringifies to the literal `{target_literal}`. Calls comparing strings that
don't involve the literal are forwarded to the original `equals` implementation
unchanged — the override is *literal-gated*, not blanket. Each match emits a
`{event_label}` event:

  • forced  — the receiver, the argument, and which equals variant fired
  • ready   — overload count (sanity signal)
  • error   — any JS-side exception during hook setup or invocation

This is the bypass-planner's preferred move when a gate's predicate value comes
from a `String.equals` call whose target literal is recoverable from the gate's
basic block (the canonical "if (input.equals(SECRET))" license-check shape).

The hook is app-wide but literal-gated — the blast-radius is limited to other
gates in the same app that happen to compare against the exact same literal
(rare for non-trivial tokens). Always start a Frida session with this hook in
isolation rather than stacked with unrelated overrides so the event stream is
attributable.
"""


TEMPLATE = HookTemplate(
    id="force_string_compare_equal",
    name="Force String.equals to true for a literal",
    description=(
        "Hook java.lang.String.equals AND String.equalsIgnoreCase app-wide and "
        "force-true when either side of the comparison stringifies to the supplied "
        "literal. Use to bypass secret-string gates."
    ),
    params=(
        HookTemplateParam(
            name="target_literal",
            description=(
                "The literal string that should make every equals comparison return true "
                "(e.g. \"LICENSE_VALID_42\" — recover from the gate's const-string)"
            ),
        ),
        HookTemplateParam(
            name="event_label",
            description="Short tag attached to every emitted event (used for filtering in the UI)",
        ),
    ),
    js_template=_JS,
    pentester_summary_template=_SUMMARY,
    sensitive_apis=(
        "Java.use",
        "Java.perform",
        "String.equals",
        "String.equalsIgnoreCase",
    ),
)
