"""Custom hand-rolled JS template — operator-authored Frida JavaScript passthrough.

Phase 11 candidate (registered in v1's library so the manual-paste flow
works today; the LLM-driven half — chat → ``generate_frida_hook`` →
``template_id="custom"`` round-trip — lands once the chat agentic loop
ships per ISSUE-009 / DEC-022 follow-ups).

Workflow it serves
------------------

Operators arrive at this template along one of three paths:

1. **Manual authoring.** The seven structured templates
   (``entry_exit_log`` / ``scope_inspector`` / ``ssl_pinning_bypass`` /
   ``crypto`` / ``shared_preferences`` / ``intent``) plus the three
   override templates (``force_return_value`` / ``force_method_skip``
   / ``force_string_compare_equal``) cover the common Hook Lab
   targets, but a meaningful tail of real pentest hooks needs
   bespoke JS — multi-class instrumentation, hooks on
   ``Thread.sleep``-bounded retries, custom `recv()` plumbing for
   bidirectional control, etc. This template lets operators paste
   that JS directly into the Hook Builder without leaving the UI for
   a separate ``frida -l file.js`` invocation.

2. **Chat-suggested JS that doesn't fit any structured template.**
   Today the chat layer can suggest JS in prose; the operator
   copy-pastes into the Custom template body. Once the chat agentic
   loop ships (DEC-022's bounded ``while turn < max_turns`` +
   ``skill_pending`` SSE vocabulary), ``generate_frida_hook`` will
   surface a "Use this hook" affordance that stages the LLM's JS
   directly into the Custom template via the existing
   ``WorkbenchContext.pendingHookPrefill`` channel
   (same handoff ``BypassPlanCard.onStage`` already uses).

3. **CLI-developed scripts the operator wants persistence + WS
   replay for.** The Hook Lab session pane (JSONL trace +
   replay-then-stream WebSocket) is materially better than
   ``frida -U -l file.js > trace.txt`` for any hook the operator
   plans to keep around or share — pasting the file's contents into
   the Custom template gets all of that infrastructure for free.

JS contract
-----------

The template is a strict passthrough: ``js_template = "{js_body}\\n"``
and nothing else. The renderer's ``str.format`` substitution doesn't
re-interpret braces inside the substituted value, so the JS body's
``Java.perform(function () {{ ... }})`` curly braces survive intact
without any escape mangling. **The renderer does not analyse the JS
body** — there's no Java.perform wrapper auto-injected, no event_label
threaded through, no ready / error scaffolding. The body runs verbatim
inside Frida's script load.

Pre-Inject syntax validation still runs through ``pyjsparser`` (the
``[frida]`` extra's parse pre-flight) — same gate the structured
templates pass through — so a malformed JS body still surfaces as a
red Monaco marker before the operator can hit Inject. Beyond that,
the operator owns the JS's semantics: there's no event_label
guarantee, no hook-counter aggregation in the Hooks tab (the
aggregator pattern-matches on ``send({class, method, phase, args})``
shapes — custom hooks are free to send whatever they want and won't
appear in the per-method aggregate unless they happen to match), and
no scope-inspector field-mutation rendering.

**Risk** is intentionally unrated in the template metadata — risk
classification only makes sense when the template's behaviour is
known, and a Custom template's behaviour is whatever the operator
typed. The pentester summary calls this out explicitly so the
operator can't claim later that the workbench told them it was safe.

Why a separate template id rather than a free-form mode flag
------------------------------------------------------------

Adding a "Custom JS" toggle next to the template picker would have
fragmented the Hook Builder's contract — the template-change effect,
``pendingHookPrefill`` consumer, render debounce, parse marker
plumbing, and Monaco view all already key off ``selectedTemplateId``.
Surfacing "Custom" as just another template id keeps every existing
piece of the form working unchanged: the operator picks "Custom"
from the dropdown like any other template, the body field renders as
a tall textarea (the Hook Builder has a per-param-name special case
for ``js_body``), and Inject goes through ``POST /api/frida/sessions``
with ``template_id="custom"`` — the route's allowlist + render +
parse pipeline doesn't need a single change.

It also makes the eventual chat handoff trivial:
``generate_frida_hook`` just emits ``{template_id: "custom",
params: {js_body: <LLM JS>}}`` and the existing
``pendingHookPrefill`` consumer takes it from there — no new chat-
side affordance, no new staging-channel shape.
"""

from __future__ import annotations

from androscan.adapters.frida_hooks import HookTemplate, HookTemplateParam


# Strict passthrough: the operator's body is rendered verbatim. The
# renderer's ``str.format`` substitution does NOT re-interpret braces
# inside the substituted value, so ``Java.perform(function () {{ }})``
# in the body survives intact (the doubled-brace dance only applies
# to the *template string*, which here is just a single ``{js_body}``
# placeholder).
#
# Trailing newline so the rendered output is well-formed if the
# operator forgot one — Frida's parser tolerates either way, but the
# Monaco view + JSONL trace look a hair cleaner with the explicit
# terminator.
_JS = "{js_body}\n"


# Pentester summary deliberately doesn't reference ``{js_body}`` — the
# rendered Monaco view is right next to the summary, so re-inlining
# the (potentially huge) JS body into the summary would just be
# visual noise. The summary's job is to remind the operator what
# safety contract they HAVE and HAVEN'T agreed to by injecting a
# Custom hook.
_SUMMARY = """\
Custom hook — operator-authored JavaScript.

The renderer passes the body through verbatim; no Java.perform wrapper, event-label
threading, ready/error scaffolding, or hook-aggregator pattern is injected for you.
The only safety net before Inject is the pyjsparser syntax check (same gate the
structured templates pass through) — semantic correctness, blast radius, and
side-effect surface are entirely the operator's responsibility.

Notes the structured templates would normally surface:

  • No event_label is enforced. The Lab session pane will still receive whatever
    the body calls send() with, but the Hooks aggregator + Scope inspector
    pattern-match on the structured-template payload shape ({{class, method,
    phase, args, return}}) — custom hooks are free to send any shape and won't
    show up in those aggregations unless their payloads happen to match.

  • No sensitive-API metadata is computed. The "Touches:" row stays empty because
    the renderer doesn't parse the JS to find Java.use / overload.implementation
    / SecretKeySpec / ... call sites. Operators using Custom hooks for high-
    blast-radius work (e.g. global java.lang.String.equals override) should
    document the radius in their own notes.

  • Risk is not rated. The template doesn't know what the body does, so it can't
    classify it as low / medium / high. The bypass-planner's risk taxonomy
    deliberately doesn't apply.

Use Custom when none of the structured templates match the target shape. For
common cases — entry/exit logging, SSL pinning bypass, force_return_value /
force_method_skip on a single method, SharedPreferences observation — the
structured templates are strictly better (deterministic summaries, aggregator
participation, deterministic risk rating).
"""


TEMPLATE = HookTemplate(
    id="custom",
    name="Custom (hand-rolled JS)",
    description=(
        "Hand-write or paste arbitrary Frida JavaScript. The renderer passes "
        "the body through unchanged; pyjsparser still validates syntax before "
        "Inject. No event-label threading, no aggregator participation, no "
        "deterministic risk rating — operator owns the body's semantics."
    ),
    params=(
        HookTemplateParam(
            name="js_body",
            description=(
                "Frida JavaScript body — runs verbatim inside the script load. "
                "Wrap your hooks in Java.perform(function () { ... }) yourself; "
                "call send({...}) for events you want to see in the Trace pane."
            ),
        ),
    ),
    js_template=_JS,
    pentester_summary_template=_SUMMARY,
    # Sensitive APIs intentionally empty: the renderer doesn't analyse the body,
    # so any value here would be a guess. The summary tells the operator the
    # "Touches:" row is intentionally blank.
    sensitive_apis=(),
)
