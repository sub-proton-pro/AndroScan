"""SharedPreferences read/write watcher with key-prefix filter.

Hooks ``SharedPreferencesImpl.getString`` and the editor's
``putString`` so the operator can see what the app persists to disk —
auth tokens, session cookies, feature flags, PII — and read back later.
The ``key_prefix`` parameter narrows the trace volume; the empty
string (the default) matches every key.

Limited to ``getString`` / ``putString`` in v1 because those are the
overwhelming majority of "interesting from a pentest perspective"
preference accesses; ``getInt`` / ``getBoolean`` etc. are rarely the
bug. A future template can broaden the surface; this one stays
focused.
"""

from __future__ import annotations

from androscan.adapters.frida_hooks import HookTemplate, HookTemplateParam


_JS = """\
// AndroScan Hook Lab — shared_preferences
// Label:      {event_label}
// Key prefix: "{key_prefix}"  (empty string = log everything)
Java.perform(function () {{
  var keyPrefix = "{key_prefix}";
  function matches(k) {{
    return keyPrefix === "" || (k && String(k).indexOf(keyPrefix) === 0);
  }}

  try {{
    var SP = Java.use("android.app.SharedPreferencesImpl");

    SP.getString.overload("java.lang.String", "java.lang.String")
      .implementation = function (k, d) {{
        var v = this.getString(k, d);
        if (matches(k)) {{
          send({{ "label": "{event_label}", "op": "getString",
                  "key": String(k), "value": v === null ? null : String(v) }});
        }}
        return v;
      }};

    var Editor = Java.use("android.app.SharedPreferencesImpl$EditorImpl");
    Editor.putString.implementation = function (k, v) {{
      if (matches(k)) {{
        send({{ "label": "{event_label}", "op": "putString",
                "key": String(k), "value": v === null ? null : String(v) }});
      }}
      return this.putString(k, v);
    }};

    send({{ "label": "{event_label}", "phase": "ready",
            "key_prefix": keyPrefix }});
  }} catch (e) {{
    send({{ "label": "{event_label}", "phase": "error", "error": String(e) }});
  }}
}});
"""


_SUMMARY = """\
Watches `SharedPreferences` reads (`getString`) and writes (`putString`) on the target app,
filtered to keys that start with `{key_prefix}` (empty string = match every key). Emits one
event per matching read or write under label `{event_label}`.

Use this to find:

  • auth tokens / session cookies persisted in plaintext (look for keys like `auth_*`,
    `session_*`, `token_*`, `oauth_*`)
  • user PII written to disk
  • feature flags or app state that gates security-sensitive code paths

Captured values may contain credentials in plaintext — handle the trace data carefully and
prefer the in-memory ring buffer over disk export when investigating live sessions.
Read-only: preferences are observed, not modified.
"""


TEMPLATE = HookTemplate(
    id="shared_preferences",
    name="SharedPreferences read/write watcher",
    description=(
        "Hook SharedPreferences getString / putString with an optional key-prefix filter "
        "to surface tokens, PII, and feature flags persisted on-device."
    ),
    params=(
        HookTemplateParam(
            name="event_label",
            description="Short tag attached to every emitted event (used for filtering in the UI)",
        ),
        HookTemplateParam(
            name="key_prefix",
            description=(
                "Prefix filter for preference keys (empty string = log all keys). "
                "Useful when only auth_* / session_* / token_* keys are interesting."
            ),
            required=False,
            default="",
        ),
    ),
    js_template=_JS,
    pentester_summary_template=_SUMMARY,
    sensitive_apis=(
        "android.app.SharedPreferencesImpl",
        "SharedPreferences.getString",
        "SharedPreferences.putString",
    ),
)
