"""SSL/TLS certificate-pinning bypass — multi-strategy.

Patches the two pinning surfaces that cover the bulk of modern Android
apps: AOSP's ``com.android.org.conscrypt.TrustManagerImpl`` (system
default TLS) and ``okhttp3.CertificatePinner`` (the most common
networking-library pin). Strategies that don't resolve in the target
process are silently skipped — that's intentional, because the
operator's first signal is the ``ready`` event listing which
strategies actually attached.

This is **not** a control-plane no-op: once injected, the app no
longer enforces the patched pins until the script is detached. The
deterministic pentester summary makes this consequence explicit so the
operator sees it on the Inject button card, not buried in a tooltip.
"""

from __future__ import annotations

from androscan.adapters.frida_hooks import HookTemplate, HookTemplateParam


_JS = """\
// AndroScan Hook Lab — ssl_pinning_bypass
// Label: {event_label}
Java.perform(function () {{
  var disabled = [];

  // Strategy 1: AOSP TrustManagerImpl.verifyChainedSocket (Conscrypt / system TLS)
  try {{
    var TMI = Java.use("com.android.org.conscrypt.TrustManagerImpl");
    TMI.verifyChainedSocket.implementation = function (a, b, c, d) {{
      send({{ "label": "{event_label}", "strategy": "TrustManagerImpl",
              "result": "bypassed" }});
      return Java.use("java.util.ArrayList").$new();
    }};
    disabled.push("TrustManagerImpl");
  }} catch (e) {{ /* not present in this app's class loader */ }}

  // Strategy 2: OkHttp3 CertificatePinner.check (modern HTTP clients)
  try {{
    var CP = Java.use("okhttp3.CertificatePinner");
    CP.check.overload("java.lang.String", "java.util.List").implementation = function (h, l) {{
      send({{ "label": "{event_label}", "strategy": "OkHttp3",
              "host": String(h), "result": "bypassed" }});
      return;
    }};
    disabled.push("OkHttp3");
  }} catch (e) {{ /* OkHttp3 not on classpath */ }}

  // Strategy 3: javax.net.ssl.X509TrustManager.checkServerTrusted (custom TMs)
  try {{
    var X509TM = Java.use("javax.net.ssl.X509TrustManager");
    X509TM.checkServerTrusted.implementation = function (chain, authType) {{
      send({{ "label": "{event_label}", "strategy": "X509TrustManager",
              "result": "bypassed" }});
      return;
    }};
    disabled.push("X509TrustManager");
  }} catch (e) {{ /* should always be present, but guard anyway */ }}

  send({{ "label": "{event_label}", "phase": "ready", "strategies": disabled }});
}});
"""


_SUMMARY = """\
Disables common Android SSL / TLS certificate-pinning checks so a MITM proxy (Burp, mitmproxy,
Charles) can intercept HTTPS traffic from this app. Targets, in order:

  • Conscrypt `TrustManagerImpl.verifyChainedSocket`  — AOSP / system-default TLS path
  • OkHttp3 `CertificatePinner.check`                 — most modern Android networking libraries
  • Generic `javax.net.ssl.X509TrustManager.checkServerTrusted` — custom trust-manager fallback

Strategies that are not present in the target's class loader are silently skipped; the
`ready` event under label `{event_label}` lists which strategies actually patched. This is
a **control-plane modification** — once injected, the app no longer enforces the patched
pins until the script is detached. Use only against apps you are authorised to test, on
devices configured to trust your interception CA.
"""


TEMPLATE = HookTemplate(
    id="ssl_pinning_bypass",
    name="SSL/TLS pinning bypass",
    description=(
        "Multi-strategy bypass for Android certificate pinning (Conscrypt, OkHttp3, "
        "X509TrustManager). Skips strategies not present on the target."
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
        "com.android.org.conscrypt.TrustManagerImpl",
        "okhttp3.CertificatePinner",
        "javax.net.ssl.X509TrustManager",
    ),
)
