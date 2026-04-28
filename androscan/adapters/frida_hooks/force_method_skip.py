"""Force-method-skip override — replace a Java method with a no-op for its declared return type.

Phase 10 / DEC-024 sub-step 10.4. The second of the three *override*
templates (alongside ``force_return_value`` and
``force_string_compare_equal``) emitted by the bypass planner.

Workflow it serves
------------------

When a gate decision's enclosing method returns ``void`` (e.g. the
canonical ``void enforceLicense() throws SecurityException`` shape),
there's no return value to force — but skipping the method *entirely*
short-circuits any deny-side throws / process-exits / activity-finishes
that live further inside the method body. The planner picks this
template, fills in the gate method's identity and its declared return
descriptor (``return_descriptor``: ``"V"`` / ``"Z"`` / ``"I"`` /
``"L...;"`` / ...), and the rendered JS returns the type's natural
zero value (``false`` / ``0`` / ``null`` / ``0.0``) — or simply
returns nothing for ``void``.

Why not just always use force_return_value
------------------------------------------

Two reasons:

1. ``void`` methods can't accept a return value at all — Frida
   refuses to attach an ``implementation`` that returns a value to a
   void method. ``force_method_skip`` is the only template that
   handles void.
2. For non-void methods where the operator wants the type's *zero*
   value (e.g. ``return 0;`` for ``int isPremium()``), this template
   is more honest about intent: the operator is saying "skip this
   method's logic and report the empty / negative answer". The
   rendered JS is also slightly simpler than the equivalent
   ``force_return_value`` invocation since the return literal is
   derived from the descriptor rather than supplied by the operator.

**Risk** is MEDIUM per DEC-024's risk taxonomy: hijacking exactly one
named method is precise (low), but skipping its body wholesale
discards any side effects the method performed legitimately. For an
``enforceLicense`` gate that's the goal; for an ``initialiseEngine``
method it's a foot-gun. The summary calls this out so the operator
can verify the gate semantics before injecting.
"""

from __future__ import annotations

from androscan.adapters.frida_hooks import HookTemplate, HookTemplateParam


# Frida + str.format double-brace dance: every literal ``{`` / ``}`` in
# the JS body is doubled (``{{`` / ``}}``); enforced indirectly by the
# fail-closed render walk in ``tests/test_frida_hook_templates.py``.
_JS = """\
// AndroScan Hook Lab — force_method_skip
// Class:               {class_name}
// Method:              {method_name}
// Return descriptor:   {return_descriptor}
// Label:               {event_label}
//
// Replaces every overload of the named method with a stub that
// returns the type's natural zero value (false / 0 / null / 0.0)
// or nothing at all for void. The original method body is NOT
// executed — any side effects (logging, network calls, state
// mutations, throws) it performed are skipped.
Java.perform(function () {{
  // Compute the zero / null / 0.0 / "" stub return for the declared
  // descriptor here in the JS so the operator can read off what we'll
  // be returning right next to the descriptor itself in the script
  // header. The mapping mirrors the JNI primitive rules:
  //   Z       -> false
  //   B/S/C/I -> 0
  //   J       -> 0  (Frida marshals Java long via Number for small values)
  //   F/D     -> 0.0
  //   V       -> nothing (handled below by an early return inside the impl)
  //   L...;   -> null  (reference)
  //   [...    -> null  (array)
  function _skipReturnFor(desc) {{
    if (desc === "V") {{ return undefined; }}
    if (desc === "Z") {{ return false; }}
    if (desc === "B" || desc === "S" || desc === "C" ||
        desc === "I" || desc === "J") {{ return 0; }}
    if (desc === "F" || desc === "D") {{ return 0.0; }}
    return null;
  }}
  try {{
    var Klass = Java.use("{class_name}");
    var overloads = Klass["{method_name}"].overloads;
    if (!overloads || overloads.length === 0) {{
      send({{ "label": "{event_label}", "phase": "error",
              "error": "method {class_name}.{method_name} not found" }});
      return;
    }}
    var stubReturn = _skipReturnFor("{return_descriptor}");
    var isVoid = ("{return_descriptor}" === "V");
    overloads.forEach(function (overload) {{
      overload.implementation = function () {{
        var args = Array.prototype.slice.call(arguments);
        send({{ "label": "{event_label}", "phase": "skipped",
                "class": "{class_name}", "method": "{method_name}",
                "args": args.map(function (a) {{ return String(a); }}),
                "return": isVoid ? "(void)" : String(stubReturn) }});
        if (isVoid) {{ return; }}
        return stubReturn;
      }};
    }});
    send({{ "label": "{event_label}", "phase": "ready",
            "overloads": overloads.length,
            "stub_return": isVoid ? "(void)" : String(stubReturn),
            "return_descriptor": "{return_descriptor}" }});
  }} catch (e) {{
    send({{ "label": "{event_label}", "phase": "error", "error": String(e) }});
  }}
}});
"""


_SUMMARY = """\
Replaces every overload of `{class_name}.{method_name}` with a no-op stub that returns
the type's natural zero value (`return_descriptor={return_descriptor}` → false / 0 /
null / 0.0 / nothing-for-void). Each call emits a `{event_label}` event:

  • skipped — the args the caller passed + the stub return we sent back
  • ready   — number of overloads patched + the stub return + the descriptor (sanity)
  • error   — any JS-side exception during hook setup or invocation

The original method body is NOT executed. Any side effects it performed (logging,
network calls, state mutations, exceptions thrown) are skipped. This is the
bypass-planner's preferred move when a gate's enclosing method returns void
(e.g. `void enforceLicense() throws SecurityException`): skipping the method
short-circuits the deny-side throw and lets execution continue past the gate.
"""


TEMPLATE = HookTemplate(
    id="force_method_skip",
    name="Force method skip (no-op stub)",
    description=(
        "Replace every overload of a Java method with a no-op stub that returns "
        "the declared type's zero value (false / 0 / null / 0.0 / nothing for "
        "void). Use to short-circuit gate methods whose deny side throws or has "
        "other terminal side effects."
    ),
    params=(
        HookTemplateParam(
            name="class_name",
            description="Fully-qualified Java class, e.g. com.example.LicenseGate",
        ),
        HookTemplateParam(
            name="method_name",
            description="Method name (no signature; all overloads are stubbed)",
        ),
        HookTemplateParam(
            name="return_descriptor",
            description=(
                "Smali-style JNI return descriptor (V / Z / B / S / C / I / J / F / D / "
                "L...; / [...). Drives the stub return value."
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
