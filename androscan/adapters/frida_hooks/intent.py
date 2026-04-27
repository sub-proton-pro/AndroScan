"""Intent watcher — log every outgoing Activity / Service / broadcast Intent.

Hooks the three ``ContextWrapper`` entry points the framework
funnels every ``Intent``-driven launch through (``startActivity``,
``startService``, ``sendBroadcast``) and emits a structured
description of each Intent: action, data URI, target component, target
package. This is the "navigation map" trace — useful for understanding
the in-app routing surface, finding deep-link parsing bugs, and
spotting accidental implicit-intent leaks to other apps.

Extras-bundle contents are NOT logged in v1. Bundles can be very large
(serialised Parcelables, file URIs with grant flags, OAuth tokens),
and exposing them by default would torpedo the ring buffer's signal-
to-noise. Operators who need extras can extend the JS template by
hand.
"""

from __future__ import annotations

from androscan.adapters.frida_hooks import HookTemplate, HookTemplateParam


_JS = """\
// AndroScan Hook Lab — intent
// Label: {event_label}
Java.perform(function () {{
  function describe(intent) {{
    if (!intent) return null;
    try {{
      return {{
        "action":    intent.getAction()       ? String(intent.getAction())                      : null,
        "data":      intent.getDataString()   ? String(intent.getDataString())                  : null,
        "component": intent.getComponent()    ? String(intent.getComponent().flattenToShortString()) : null,
        "package":   intent.getPackage()      ? String(intent.getPackage())                     : null
      }};
    }} catch (e) {{
      return {{ "_describe_error": String(e) }};
    }}
  }}

  try {{
    var ContextWrapper = Java.use("android.content.ContextWrapper");

    ContextWrapper.startActivity.overload("android.content.Intent")
      .implementation = function (i) {{
        send({{ "label": "{event_label}", "op": "startActivity", "intent": describe(i) }});
        return this.startActivity(i);
      }};

    ContextWrapper.startService.overload("android.content.Intent")
      .implementation = function (i) {{
        send({{ "label": "{event_label}", "op": "startService", "intent": describe(i) }});
        return this.startService(i);
      }};

    ContextWrapper.sendBroadcast.overload("android.content.Intent")
      .implementation = function (i) {{
        send({{ "label": "{event_label}", "op": "sendBroadcast", "intent": describe(i) }});
        return this.sendBroadcast(i);
      }};

    send({{ "label": "{event_label}", "phase": "ready" }});
  }} catch (e) {{
    send({{ "label": "{event_label}", "phase": "error", "error": String(e) }});
  }}
}});
"""


_SUMMARY = """\
Logs every outgoing `Intent` from the target app via the three `ContextWrapper` entry
points the framework funnels everything through:

  • startActivity   — UI navigation / cross-app launches
  • startService    — background work / IPC
  • sendBroadcast   — broadcasts (explicit and implicit)

Per Intent, emits the action, data URI, target component (if explicit), and target
package under label `{event_label}`. Useful for:

  • mapping the in-app navigation surface and the cross-app launch surface
  • spotting deep-link parsing bugs (action / data combinations that reach unexpected components)
  • catching attempts to send data to other apps via implicit Intents

The Intent's extras bundle is **not** captured by default — extras can contain large
Parcelables, file URIs with grant flags, or OAuth tokens, and capturing them would
overwhelm the ring buffer. Extend the JS template if you need extras for a specific
investigation. Read-only — Intents are not modified.
"""


TEMPLATE = HookTemplate(
    id="intent",
    name="Intent watcher",
    description=(
        "Hook ContextWrapper.startActivity / startService / sendBroadcast and log Intent "
        "action / data URI / component / package (extras NOT captured)."
    ),
    params=(
        HookTemplateParam(
            name="event_label",
            description="Short tag attached to every emitted event (used for filtering in the UI)",
        ),
    ),
    js_template=_JS,
    pentester_summary_template=_SUMMARY,
    sensitive_apis=(
        "android.content.ContextWrapper",
        "android.content.Intent",
    ),
)
