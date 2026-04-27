"""Cryptography observer — watch every ``javax.crypto.Cipher`` operation.

Captures ``init`` (mode + algorithm + key algorithm) and ``doFinal``
(input/output lengths only — the bytes themselves are NOT logged by
default; capturing plaintext requires editing the JS by hand and is a
deliberate consent gate). Reveals algorithm choices, key reuse across
operations, weak modes (ECB), and missing IV randomness — the classic
"crypto smell" findings that show up in mobile pentests.

Read-only: the cipher result is forwarded unchanged. The template
deliberately limits itself to the two most-used overloads
(``init(int, Key)`` and ``doFinal(byte[])``) for v1; broadening to the
full ``Cipher`` API surface is not free — every hooked overload
multiplies the trace volume — and is left for hand-rolled scripts.
"""

from __future__ import annotations

from androscan.adapters.frida_hooks import HookTemplate, HookTemplateParam


_JS = """\
// AndroScan Hook Lab — crypto
// Label: {event_label}
Java.perform(function () {{
  try {{
    var Cipher = Java.use("javax.crypto.Cipher");

    Cipher.init.overload("int", "java.security.Key").implementation = function (mode, key) {{
      send({{ "label": "{event_label}", "phase": "init",
              "mode": mode,
              "algo": String(this.getAlgorithm()),
              "key_algo": key ? String(key.getAlgorithm()) : null }});
      return this.init(mode, key);
    }};

    Cipher.doFinal.overload("[B").implementation = function (data) {{
      var out = this.doFinal(data);
      send({{ "label": "{event_label}", "phase": "doFinal",
              "algo": String(this.getAlgorithm()),
              "in_len":  data ? data.length : 0,
              "out_len": out  ? out.length  : 0 }});
      return out;
    }};

    send({{ "label": "{event_label}", "phase": "ready" }});
  }} catch (e) {{
    send({{ "label": "{event_label}", "phase": "error", "error": String(e) }});
  }}
}});
"""


_SUMMARY = """\
Observes every `javax.crypto.Cipher` operation in the target app. Per call, emits under
label `{event_label}`:

  • init     — cipher mode (1=ENCRYPT, 2=DECRYPT, 3=WRAP, 4=UNWRAP), algorithm name
               (e.g. `AES/CBC/PKCS5Padding`), key algorithm
  • doFinal  — algorithm + input/output byte lengths (the bytes themselves are NOT
               captured — extend the JS template if you need plaintext, that requires
               explicit consent)
  • ready    — sanity signal that hooks attached
  • error    — any JS-side exception during hook setup

This reveals which algorithms the app uses, whether weak modes (ECB, CBC without IV
randomness) are in play, and whether keys are reused across operations. Useful for finding
hardcoded keys, weak ciphers, and bad IV / nonce hygiene. Read-only — the cipher result is
not altered.
"""


TEMPLATE = HookTemplate(
    id="crypto",
    name="Crypto operations observer",
    description=(
        "Hook javax.crypto.Cipher.init / doFinal to log algorithm / mode / key-algo and "
        "in/out lengths (no plaintext capture by default)."
    ),
    params=(
        HookTemplateParam(
            name="event_label",
            description="Short tag attached to every emitted event (used for filtering in the UI)",
        ),
    ),
    js_template=_JS,
    pentester_summary_template=_SUMMARY,
    sensitive_apis=("javax.crypto.Cipher",),
)
