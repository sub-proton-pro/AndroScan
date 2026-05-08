"""Hook Lab template library (Phase 6 step 4 / DEC-023, sub-step 4.4).

This package ships the v1 set of parametrised Frida hook templates that
the Hook Lab UI (4.5) and the LLM-tier ``generate_frida_hook`` skill
(4.7) will consume. **Templates are the only source of hook JS in v1**
— the LLM fills parameters but never emits raw JS, per DEC-023's
hook-source policy.

Each template module under this package exports a single module-level
``TEMPLATE: HookTemplate`` that bundles three things:

* a parameter schema (``params: tuple[HookTemplateParam, ...]``) — the
  fields the LLM (or operator) must fill;
* a JS body (``js_template: str``) — Python ``str.format``-style with
  placeholders matching the schema's parameter names;
* a deterministic pentester summary (``pentester_summary_template: str``)
  — same ``str.format`` placeholder vocabulary; rendered alongside the
  JS so the operator sees what the script will *do* (in plain English,
  from a pentester's perspective) right next to the script itself. This
  fulfils DEC-023's Option-A confirmation UX (single Inject button + a
  deterministic, non-LLM-generated summary that's identical for the
  same inputs every time).

Adding a template is a strict two-deliverable change: a JS template
*and* a non-empty pentester summary template. ``tests/test_frida_hook_templates.py``
walks every module in this package and fails the suite if either is
missing or if the ``str.format`` placeholders drift outside the declared
parameter schema.

JS placeholder escaping: because we render via ``str.format``, every
literal ``{`` / ``}`` in the JS body must be doubled (``{{`` / ``}}``).
This is enforced indirectly by the per-template render tests: any
unescaped brace produces a malformed JS body that the corresponding
test's substring assertions will catch.

Sub-step 4.4 stops at the library + renderer + fail-closed registry
walk. There is no UI, no HTTP route, no JS pre-validation
(``pyjsparser`` is pinned by the ``[frida]`` extra but consumed only by
4.5's Inject button), and no LLM skill yet (4.7 owns
``generate_frida_hook``). The contract this module establishes — input
schema + ``RenderedHook`` shape — is what those layers will consume.
"""

from __future__ import annotations

import importlib
import logging
import string
from dataclasses import dataclass, field
from typing import Any, Optional


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class HookTemplateError(RuntimeError):
    """Base class for every error raised by this package."""


class HookTemplateNotFound(HookTemplateError):
    """Raised by :func:`get_template` / :func:`render_by_id` for unknown ids."""


class HookParamError(HookTemplateError):
    """Raised by :func:`render` when the supplied ``params`` don't satisfy the schema.

    Concrete sub-shapes are signalled via the message; callers (4.5's
    Inject UI, 4.7's ``generate_frida_hook`` skill) typically just
    surface the ``str(e)`` to the operator. Splitting into separate
    exception classes was considered and rejected — the consumers want
    one ``except HookParamError`` block plus a human-readable reason,
    not five.
    """


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookTemplateParam:
    """A single field in a template's parameter schema.

    ``required=True`` means the operator (or LLM) MUST supply a non-empty
    value at render time. ``required=False`` parameters fall back to
    ``default`` when omitted; ``default`` defaults to the empty string,
    which is the well-defined identity for the kinds of parameters we
    accept here (substring filters, event tags). All values are coerced
    to ``str`` at render time — Frida JS is ultimately textual, and
    typed validators (regex / int range / java-identifier) are deferred
    to v2 along with the LLM-side schema-aware fill in 4.7.
    """

    name: str
    description: str
    required: bool = True
    default: str = ""


@dataclass(frozen=True)
class HookTemplate:
    """A full hook template: schema + JS body + pentester summary body.

    ``id`` is the slug used in URLs and skill calls (e.g.
    ``"entry_exit_log"``). ``name`` is the human-readable title; the
    Hook Lab UI shows it in the template picker. ``description`` is the
    catalog blurb (one or two short sentences) — the LLM also sees it
    when picking a template.

    ``sensitive_apis`` is a free-form list of API names this template
    is known to interact with (``"javax.crypto.Cipher"``,
    ``"SharedPreferences.getString"``, etc.). It's purely informational
    in 4.4 — the Hook Lab UI will surface it on the Inject button card
    so the operator sees the blast-radius before clicking Inject.
    """

    id: str
    name: str
    description: str
    params: tuple[HookTemplateParam, ...]
    js_template: str
    pentester_summary_template: str
    sensitive_apis: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RenderedHook:
    """The output of :func:`render`.

    Both ``js`` and ``summary`` are post-substitution strings — ready to
    paste into a Monaco editor or the operator-facing summary card
    respectively. ``params_used`` is the merged dict (operator-supplied
    plus filled-in defaults) so callers can persist the exact inputs
    that produced this output. ``template_id`` lets callers carry one
    object around without keeping the source ``HookTemplate`` reference.
    """

    template_id: str
    js: str
    summary: str
    params_used: dict[str, str]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


# Explicit module list (matches the ``androscan.skills`` discover pattern).
# A new template = one new module + one new line here. Keeping the list
# explicit makes "what ships" auditable in a single place; auto-globbing
# would mean a stray ``frida_hooks/scratch.py`` becomes a registered
# template by accident.
_TEMPLATE_MODULES: tuple[str, ...] = (
    "androscan.adapters.frida_hooks.entry_exit_log",
    "androscan.adapters.frida_hooks.scope_inspector",
    "androscan.adapters.frida_hooks.ssl_pinning_bypass",
    "androscan.adapters.frida_hooks.crypto",
    "androscan.adapters.frida_hooks.shared_preferences",
    "androscan.adapters.frida_hooks.intent",
    # Phase 10 / DEC-024 sub-step 10.4 — override templates emitted by
    # the bypass planner (``androscan.analysis.bypass_planner``). Risk
    # taxonomy:
    #   force_return_value         — LOW    (one named method, one literal)
    #   force_method_skip          — MEDIUM (one named method, side effects skipped)
    #   force_string_compare_equal — MEDIUM (app-wide String.equals, literal-gated)
    "androscan.adapters.frida_hooks.force_return_value",
    "androscan.adapters.frida_hooks.force_method_skip",
    "androscan.adapters.frida_hooks.force_string_compare_equal",
    # Phase 11 candidate — operator-authored JS passthrough. Lives in
    # the v1 library so the manual-paste flow + (eventually) the
    # chat-suggested-JS handoff via ``generate_frida_hook`` can reuse
    # the existing render / parse / Inject pipeline without a parallel
    # "untemplated JS" code path. Risk is intentionally unrated — the
    # renderer doesn't analyse the body, so any classification would
    # be a guess (the template's pentester summary calls this out).
    "androscan.adapters.frida_hooks.custom",
    # Phase 13 / DEC-029 sub-step 13.1 — multi-method dynamic tracer.
    # Hooks every overload of every (class, method, descriptor) triple
    # in a BehaviorAnchor closure, emits per-thread depth-aware
    # entry / exit / hook_failed / ready / error events with
    # tier-stringified args + return values. Backend deliverable for
    # the Behavior Trace v3 dynamic + both modes. Read-only.
    "androscan.adapters.frida_hooks.behavior_trace_multi",
)


# Registry keyed by template id. Populated lazily by :func:`discover` on
# first import; callers should treat it as read-only.
_REGISTRY: dict[str, HookTemplate] = {}


def discover() -> None:
    """Import every module in :data:`_TEMPLATE_MODULES` and register its ``TEMPLATE``.

    Mirrors :func:`androscan.skills.discover`. Failures during a single
    template's import are logged but do not block other templates from
    registering — the fail-closed test in
    ``tests/test_frida_hook_templates.py`` catches the case where a
    template module is broken or missing its ``TEMPLATE`` symbol.
    """

    for mod_name in _TEMPLATE_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("frida_hooks: failed to import %s: %s", mod_name, exc)
            continue
        tmpl = getattr(mod, "TEMPLATE", None)
        if not isinstance(tmpl, HookTemplate):
            logger.warning(
                "frida_hooks: module %s does not export a TEMPLATE: HookTemplate", mod_name
            )
            continue
        register(tmpl)


def register(template: HookTemplate) -> None:
    """Add a :class:`HookTemplate` to the registry.

    Raises :class:`HookTemplateError` if a template with the same ``id``
    is already registered (catches accidental id collisions during
    development; in steady-state ``discover()`` is the only caller).
    """

    if template.id in _REGISTRY:
        raise HookTemplateError(f"hook template id collision: {template.id!r}")
    _REGISTRY[template.id] = template


def get_template(template_id: str) -> HookTemplate:
    """Return the :class:`HookTemplate` for ``template_id``.

    Raises :class:`HookTemplateNotFound` if the id isn't registered. The
    Hook Lab UI / the ``generate_frida_hook`` skill should surface
    ``str(e)`` to the operator with the list of valid ids alongside.
    """

    try:
        return _REGISTRY[template_id]
    except KeyError as exc:
        valid = ", ".join(sorted(_REGISTRY)) or "<empty>"
        raise HookTemplateNotFound(
            f"unknown hook template id: {template_id!r} (valid: {valid})"
        ) from exc


def list_templates() -> list[HookTemplate]:
    """Return all registered templates sorted by id (deterministic for tests / UI)."""

    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def extract_format_fields(template_str: str) -> set[str]:
    """Return the set of named placeholders ``str.format`` would substitute.

    Skips positional placeholders (``{0}``, ``{}``) and "literal-only"
    parses produced by ``string.Formatter().parse``. Used by both
    :func:`render` (to guard against schema/template drift) and by the
    fail-closed test in ``tests/test_frida_hook_templates.py``.

    ``str.format`` and ``string.Formatter().parse`` agree on what
    constitutes a placeholder, so this function is the single source of
    truth for "which fields does this template reference?".
    """

    fields: set[str] = set()
    for _literal, field_name, _spec, _conv in string.Formatter().parse(template_str):
        if field_name is None:
            continue
        # ``field_name`` may include a dotted attribute or indexer
        # (``{foo.bar}`` / ``{foo[0]}``). We strip those: the *root*
        # name is what the params dict needs to provide.
        root = field_name.split(".", 1)[0].split("[", 1)[0]
        # Positional placeholders (``{}`` and ``{0}``) are skipped: v1
        # schemas only allow named parameters, and the fail-closed test
        # would reject any template that referenced an integer-named
        # field anyway. ``str.isdigit()`` returns False for ``""``,
        # so we also need the explicit empty check.
        if root == "" or root.isdigit():
            continue
        fields.add(root)
    return fields


def render(template: HookTemplate, params: Optional[dict[str, Any]] = None) -> RenderedHook:
    """Render ``template`` against the supplied ``params`` dict.

    Steps (intentionally unsurprising):

    1. Validate that every supplied param key is declared in
       ``template.params`` (no unknown keys — drops typo'd LLM output
       early instead of letting it silently render with a missing
       substitution).
    2. Validate that every ``required=True`` param is supplied with a
       non-empty value.
    3. Fill in defaults for any non-required params not supplied.
    4. Coerce all values to ``str`` (Frida JS is text; the schema
       semantics are stringly-typed in v1 — see
       :class:`HookTemplateParam`).
    5. Render both ``js_template`` and ``pentester_summary_template``
       via ``str.format(**values)``.

    Any KeyError surfaced by ``str.format`` is wrapped in
    :class:`HookParamError` with the offending placeholder name — that
    means a template author drifted the JS / summary against the
    declared schema. The fail-closed registry test catches this in CI;
    surfacing it cleanly here matters for hand-rolled callers.
    """

    supplied: dict[str, Any] = dict(params or {})
    declared = {p.name: p for p in template.params}

    # (1) Reject unknown keys early. The LLM in 4.7 will sometimes
    # invent a plausible-sounding parameter name; if it slipped through
    # to ``str.format`` it would just be ignored, and the operator
    # would see the *correct-looking* JS without realising their input
    # was discarded.
    unknown = sorted(set(supplied) - set(declared))
    if unknown:
        raise HookParamError(
            f"unknown parameter(s) for template {template.id!r}: {', '.join(unknown)} "
            f"(declared: {', '.join(sorted(declared)) or '<none>'})"
        )

    # (2) + (3) Required-vs-default merge.
    filled: dict[str, str] = {}
    missing_required: list[str] = []
    for name, spec in declared.items():
        if name in supplied:
            value = supplied[name]
            if spec.required and (value is None or str(value) == ""):
                missing_required.append(name)
                continue
            filled[name] = "" if value is None else str(value)
        else:
            if spec.required:
                missing_required.append(name)
                continue
            filled[name] = spec.default
    if missing_required:
        raise HookParamError(
            f"missing required parameter(s) for template {template.id!r}: "
            f"{', '.join(missing_required)}"
        )

    # (4) Render. We catch KeyError here (template author drift)
    # separately from the schema validation above so the operator-facing
    # error message points at the right culprit.
    try:
        rendered_js = template.js_template.format(**filled)
        rendered_summary = template.pentester_summary_template.format(**filled)
    except KeyError as exc:  # pragma: no cover - guarded by registry test
        raise HookParamError(
            f"template {template.id!r} references undeclared placeholder {exc.args[0]!r} "
            "(schema/template drift; please report)"
        ) from exc

    return RenderedHook(
        template_id=template.id,
        js=rendered_js,
        summary=rendered_summary,
        params_used=filled,
    )


def render_by_id(template_id: str, params: Optional[dict[str, Any]] = None) -> RenderedHook:
    """Convenience: ``render(get_template(template_id), params)``.

    The two-step form is preferred when callers already hold a
    :class:`HookTemplate` reference (e.g. the Hook Lab UI's template
    picker); ``render_by_id`` is for lookup-by-string callers (LLM
    skill, REST routes in 4.5+).
    """

    return render(get_template(template_id), params)


# Auto-discover on first import (matches androscan.skills behaviour).
if not _REGISTRY:
    discover()


__all__ = [
    "HookParamError",
    "HookTemplate",
    "HookTemplateError",
    "HookTemplateNotFound",
    "HookTemplateParam",
    "RenderedHook",
    "discover",
    "extract_format_fields",
    "get_template",
    "list_templates",
    "register",
    "render",
    "render_by_id",
]
