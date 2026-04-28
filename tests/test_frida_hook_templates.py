"""Unit tests for the Hook Lab template library (Phase 6 step 4 / DEC-023, sub-step 4.4).

This file owns three concerns:

1. **Renderer contract** — :class:`TestRenderer` exercises every public
   error path and happy path of :func:`render` /
   :func:`render_by_id` / :func:`get_template` / :func:`register` /
   :func:`extract_format_fields` against a small, hand-rolled
   :class:`HookTemplate` fixture that's independent of the shipped
   templates. This is what the LLM skill (4.7) and Inject UI (4.5)
   will actually call.

2. **Fail-closed registry walk** — :class:`TestRegistryFailClosed`
   walks every module in
   :data:`androscan.adapters.frida_hooks._TEMPLATE_MODULES` and asserts
   the per-template invariants from DEC-023: every template ships a
   ``TEMPLATE: HookTemplate``, both ``js_template`` and
   ``pentester_summary_template`` are non-empty, every ``str.format``
   placeholder is a declared parameter, and every required parameter
   appears in at least one of (JS, summary). This is the test that
   makes "summary template missing" a hard CI failure — adding a new
   template with a missing or stub summary will fail the suite here.

3. **Per-template smoke renders** — :class:`TestTemplates` renders
   each of the five v1 templates against a representative parameter
   set and asserts a few substring tokens to catch dumb regressions
   (forgotten brace, wrong placeholder name, summary that doesn't
   mention the right pentest concept). These assertions are
   intentionally loose — they're a sanity net, not a full functional
   spec for the JS bodies.
"""

from __future__ import annotations

import importlib

import pytest

from androscan.adapters import frida_hooks
from androscan.adapters.frida_hooks import (
    HookParamError,
    HookTemplate,
    HookTemplateError,
    HookTemplateNotFound,
    HookTemplateParam,
    RenderedHook,
    extract_format_fields,
    get_template,
    list_templates,
    register,
    render,
    render_by_id,
)


# ---------------------------------------------------------------------------
# Renderer contract
# ---------------------------------------------------------------------------


class TestRenderer:
    """Cover the renderer's contract end-to-end with a hand-rolled fixture template.

    Using an ad-hoc :class:`HookTemplate` here (instead of one of the
    five shipped templates) keeps these tests stable when the JS / summary
    bodies of the production templates evolve — those have their own
    smoke assertions in :class:`TestTemplates`.
    """

    @pytest.fixture
    def fixture_template(self) -> HookTemplate:
        return HookTemplate(
            id="__test_fixture__",
            name="Test fixture",
            description="hand-rolled template for the renderer contract tests",
            params=(
                HookTemplateParam(name="alpha", description="required str"),
                HookTemplateParam(name="beta", description="required str"),
                HookTemplateParam(
                    name="gamma",
                    description="optional str with default",
                    required=False,
                    default="GAMMA-DEFAULT",
                ),
            ),
            js_template="JS({alpha}, {beta}, {gamma}) // literal braces: {{ and }}",
            pentester_summary_template="SUMMARY: alpha={alpha}, beta={beta}, gamma={gamma}",
        )

    def test_render_returns_rendered_hook_shape(self, fixture_template):
        result = render(fixture_template, {"alpha": "A", "beta": "B"})
        assert isinstance(result, RenderedHook)
        assert result.template_id == "__test_fixture__"
        assert isinstance(result.js, str)
        assert isinstance(result.summary, str)
        assert isinstance(result.params_used, dict)

    def test_render_substitutes_required_params(self, fixture_template):
        result = render(fixture_template, {"alpha": "ALPHA-VAL", "beta": "BETA-VAL"})
        assert "ALPHA-VAL" in result.js
        assert "BETA-VAL" in result.js
        assert "ALPHA-VAL" in result.summary
        assert "BETA-VAL" in result.summary

    def test_render_uses_default_for_omitted_optional(self, fixture_template):
        result = render(fixture_template, {"alpha": "A", "beta": "B"})
        assert result.params_used["gamma"] == "GAMMA-DEFAULT"
        assert "GAMMA-DEFAULT" in result.js
        assert "GAMMA-DEFAULT" in result.summary

    def test_render_uses_supplied_value_for_optional(self, fixture_template):
        result = render(
            fixture_template, {"alpha": "A", "beta": "B", "gamma": "OVERRIDE"}
        )
        assert result.params_used["gamma"] == "OVERRIDE"
        assert "OVERRIDE" in result.js

    def test_render_escapes_literal_braces(self, fixture_template):
        # The template has ``literal braces: {{ and }}`` which renders
        # as ``literal braces: { and }`` — proves operators can write
        # JS object syntax in templates as long as they double their
        # braces (the contract documented in the package docstring).
        result = render(fixture_template, {"alpha": "A", "beta": "B"})
        assert "literal braces: { and }" in result.js

    def test_render_missing_required_raises(self, fixture_template):
        with pytest.raises(HookParamError) as exc_info:
            render(fixture_template, {"alpha": "A"})
        msg = str(exc_info.value)
        assert "missing required parameter" in msg
        assert "beta" in msg

    def test_render_empty_required_raises(self, fixture_template):
        with pytest.raises(HookParamError) as exc_info:
            render(fixture_template, {"alpha": "A", "beta": ""})
        assert "beta" in str(exc_info.value)

    def test_render_none_required_raises(self, fixture_template):
        with pytest.raises(HookParamError) as exc_info:
            render(fixture_template, {"alpha": "A", "beta": None})
        assert "beta" in str(exc_info.value)

    def test_render_unknown_param_raises(self, fixture_template):
        with pytest.raises(HookParamError) as exc_info:
            render(
                fixture_template, {"alpha": "A", "beta": "B", "made_up_field": "x"}
            )
        msg = str(exc_info.value)
        assert "unknown parameter" in msg
        assert "made_up_field" in msg

    def test_render_coerces_non_string_to_str(self, fixture_template):
        result = render(fixture_template, {"alpha": 42, "beta": True})
        assert result.params_used["alpha"] == "42"
        assert result.params_used["beta"] == "True"
        assert "42" in result.js
        assert "True" in result.js

    def test_render_with_no_params_arg_uses_defaults(self):
        tmpl = HookTemplate(
            id="__only_optional__",
            name="optional-only",
            description="all optional",
            params=(
                HookTemplateParam(
                    name="x", description="opt", required=False, default="defx"
                ),
            ),
            js_template="x={x}",
            pentester_summary_template="x={x}",
        )
        result = render(tmpl, None)
        assert result.params_used == {"x": "defx"}
        assert result.js == "x=defx"

    def test_render_by_id_round_trip(self):
        result = render_by_id(
            "entry_exit_log",
            {
                "class_name": "com.example.Foo",
                "method_name": "bar",
                "event_label": "lbl1",
            },
        )
        assert result.template_id == "entry_exit_log"
        assert "com.example.Foo" in result.js

    def test_render_by_id_unknown_template_raises(self):
        with pytest.raises(HookTemplateNotFound) as exc_info:
            render_by_id("does_not_exist", {})
        # Error message lists the registered ids so the operator /
        # caller can see what's actually available.
        msg = str(exc_info.value)
        assert "does_not_exist" in msg
        assert "entry_exit_log" in msg

    def test_get_template_unknown_raises(self):
        with pytest.raises(HookTemplateNotFound):
            get_template("nope")

    def test_list_templates_returns_sorted(self):
        ids = [t.id for t in list_templates()]
        assert ids == sorted(ids)
        assert "entry_exit_log" in ids

    def test_register_collision_raises(self):
        existing = get_template("crypto")
        with pytest.raises(HookTemplateError) as exc_info:
            register(existing)
        assert "id collision" in str(exc_info.value)

    def test_extract_format_fields_named_only(self):
        assert extract_format_fields("a={a} b={b} a-again={a}") == {"a", "b"}

    def test_extract_format_fields_skips_positional(self):
        assert extract_format_fields("{} {0} {x}") == {"x"}

    def test_extract_format_fields_strips_attribute_and_index(self):
        # The renderer's params dict is keyed by *root* name, so
        # ``{x.attr}`` and ``{x[0]}`` both demand the param ``x``.
        assert extract_format_fields("{x.attr} {y[0]}") == {"x", "y"}


# ---------------------------------------------------------------------------
# Fail-closed registry walk
# ---------------------------------------------------------------------------


class TestRegistryFailClosed:
    """The DEC-023 contract: adding a template is a strict two-deliverable change.

    These tests walk every module listed in
    :data:`frida_hooks._TEMPLATE_MODULES` and assert the structural
    invariants. A template missing its summary, drifting placeholders
    away from the schema, or declaring a required parameter that no
    template body references will fail the suite here.
    """

    def test_module_list_is_non_empty(self):
        # Sanity: if someone accidentally empties the list, the loop
        # below would silently pass — guard against that. Lower bound
        # bumped from 5 → 9 in Phase 10 sub-step 10.4 (DEC-024) when
        # the bypass-planner override templates landed
        # (force_return_value / force_method_skip /
        # force_string_compare_equal alongside the original 6 v1
        # observation templates).
        assert len(frida_hooks._TEMPLATE_MODULES) >= 9

    def test_registered_count_matches_module_list(self):
        # Every module in the list contributed exactly one template.
        assert len(list_templates()) == len(frida_hooks._TEMPLATE_MODULES)

    @pytest.mark.parametrize("module_path", frida_hooks._TEMPLATE_MODULES)
    def test_module_exports_template(self, module_path):
        mod = importlib.import_module(module_path)
        tmpl = getattr(mod, "TEMPLATE", None)
        assert isinstance(tmpl, HookTemplate), (
            f"{module_path} must export TEMPLATE: HookTemplate"
        )

    @pytest.mark.parametrize("module_path", frida_hooks._TEMPLATE_MODULES)
    def test_template_id_matches_module_basename(self, module_path):
        # Discovery and reverse-lookup get a lot easier if these stay in sync.
        mod = importlib.import_module(module_path)
        expected_id = module_path.rsplit(".", 1)[-1]
        assert mod.TEMPLATE.id == expected_id, (
            f"{module_path}: TEMPLATE.id={mod.TEMPLATE.id!r} "
            f"should match module basename {expected_id!r}"
        )

    @pytest.mark.parametrize("module_path", frida_hooks._TEMPLATE_MODULES)
    def test_js_template_non_empty(self, module_path):
        mod = importlib.import_module(module_path)
        assert mod.TEMPLATE.js_template.strip(), (
            f"{module_path}: js_template must be non-empty"
        )

    @pytest.mark.parametrize("module_path", frida_hooks._TEMPLATE_MODULES)
    def test_pentester_summary_template_non_empty(self, module_path):
        # This is the DEC-023 fail-closed promise: a stub or empty
        # summary fails CI before the operator ever sees it.
        mod = importlib.import_module(module_path)
        assert mod.TEMPLATE.pentester_summary_template.strip(), (
            f"{module_path}: pentester_summary_template must be non-empty "
            "(DEC-023: every template must ship a real summary)"
        )

    @pytest.mark.parametrize("module_path", frida_hooks._TEMPLATE_MODULES)
    def test_js_placeholders_subset_of_declared_params(self, module_path):
        mod = importlib.import_module(module_path)
        tmpl: HookTemplate = mod.TEMPLATE
        declared = {p.name for p in tmpl.params}
        used = extract_format_fields(tmpl.js_template)
        drift = used - declared
        assert not drift, (
            f"{module_path}: js_template references undeclared param(s) {sorted(drift)} "
            f"(declared: {sorted(declared)})"
        )

    @pytest.mark.parametrize("module_path", frida_hooks._TEMPLATE_MODULES)
    def test_summary_placeholders_subset_of_declared_params(self, module_path):
        mod = importlib.import_module(module_path)
        tmpl: HookTemplate = mod.TEMPLATE
        declared = {p.name for p in tmpl.params}
        used = extract_format_fields(tmpl.pentester_summary_template)
        drift = used - declared
        assert not drift, (
            f"{module_path}: pentester_summary_template references undeclared param(s) "
            f"{sorted(drift)} (declared: {sorted(declared)})"
        )

    @pytest.mark.parametrize("module_path", frida_hooks._TEMPLATE_MODULES)
    def test_every_required_param_appears_in_js_or_summary(self, module_path):
        # If a required param isn't substituted anywhere, it's a dead
        # field in the schema — either the JS / summary need to use
        # it or it shouldn't be required (or shouldn't exist).
        mod = importlib.import_module(module_path)
        tmpl: HookTemplate = mod.TEMPLATE
        required = {p.name for p in tmpl.params if p.required}
        used = extract_format_fields(tmpl.js_template) | extract_format_fields(
            tmpl.pentester_summary_template
        )
        unused_required = required - used
        assert not unused_required, (
            f"{module_path}: required param(s) {sorted(unused_required)} are not "
            "referenced in either the JS body or the pentester summary"
        )

    @pytest.mark.parametrize("module_path", frida_hooks._TEMPLATE_MODULES)
    def test_template_renders_with_placeholder_inputs(self, module_path):
        # Every shipped template must render cleanly when supplied a
        # legal-shaped param dict. This is the integration-shaped
        # check: catches forgotten ``{{`` escapes (which surface as a
        # ``KeyError`` from ``str.format``) and any other authoring
        # bug that would crash render() at runtime.
        mod = importlib.import_module(module_path)
        tmpl: HookTemplate = mod.TEMPLATE
        params = {p.name: f"<{p.name}-stub>" for p in tmpl.params}
        result = render(tmpl, params)
        assert isinstance(result, RenderedHook)
        assert result.js
        assert result.summary


# ---------------------------------------------------------------------------
# Per-template smoke renders
# ---------------------------------------------------------------------------


class TestTemplates:
    """Loose substring assertions per shipped template.

    Catches the most common authoring regressions: a forgotten
    placeholder, a swapped param name, a summary that no longer
    mentions the pentest concept the template targets. The
    structural guarantees (no undeclared placeholders, every required
    param used somewhere) live in :class:`TestRegistryFailClosed`.
    """

    def test_entry_exit_log_renders(self):
        result = render_by_id(
            "entry_exit_log",
            {
                "class_name": "com.example.LoginManager",
                "method_name": "authenticate",
                "event_label": "login-trace",
            },
        )
        assert "com.example.LoginManager" in result.js
        assert "authenticate" in result.js
        assert "login-trace" in result.js
        # Summary should call out the read-only nature + the fact that
        # captured args/returns may be sensitive.
        assert "com.example.LoginManager" in result.summary
        assert "authenticate" in result.summary
        assert "read-only" in result.summary.lower()
        assert "auth tokens" in result.summary.lower() or "sensitive" in result.summary.lower()

    def test_ssl_pinning_bypass_renders(self):
        result = render_by_id("ssl_pinning_bypass", {"event_label": "mitm-1"})
        assert "mitm-1" in result.js
        # Summary should mention pinning + MITM-tooling context.
        s = result.summary.lower()
        assert "pinning" in s
        assert "mitm" in s or "burp" in s or "intercept" in s
        # Should call out the control-plane modification.
        assert "control-plane" in s or "no longer enforces" in s

    def test_crypto_renders(self):
        result = render_by_id("crypto", {"event_label": "crypto-watch"})
        assert "crypto-watch" in result.js
        assert "javax.crypto.Cipher" in result.js
        s = result.summary.lower()
        assert "cipher" in s
        # Summary should mention plaintext is NOT captured by default
        # (this is the consent gate).
        assert "not captured" in s or "not log" in s or "plaintext" in s

    def test_shared_preferences_renders(self):
        result = render_by_id(
            "shared_preferences",
            {"event_label": "prefs-trace", "key_prefix": "auth_"},
        )
        assert "prefs-trace" in result.js
        assert "auth_" in result.js
        # Default-empty case still works (covered structurally
        # elsewhere; here we just check the supplied prefix lands in
        # the summary too).
        assert "auth_" in result.summary
        s = result.summary.lower()
        assert "token" in s or "session" in s or "credentials" in s

    def test_shared_preferences_default_key_prefix(self):
        # Optional param should fall back to the empty-string default.
        result = render_by_id(
            "shared_preferences", {"event_label": "prefs-default"}
        )
        assert result.params_used["key_prefix"] == ""

    def test_intent_renders(self):
        result = render_by_id("intent", {"event_label": "intent-map"})
        assert "intent-map" in result.js
        assert "startActivity" in result.js
        assert "startService" in result.js
        assert "sendBroadcast" in result.js
        s = result.summary.lower()
        # Should call out the deep-link / navigation framing AND the
        # extras-not-captured caveat.
        assert "deep-link" in s or "navigation" in s
        assert "extras" in s

    def test_force_return_value_renders(self):
        """Phase 10 sub-step 10.4 — bypass-planner override template."""
        result = render_by_id(
            "force_return_value",
            {
                "class_name": "com.example.LicenseChecker",
                "method_name": "isPremiumUser",
                "return_value_expr": "true",
                "event_label": "force-rv-1",
            },
        )
        # Every parameter substituted into the JS body.
        assert "com.example.LicenseChecker" in result.js
        assert "isPremiumUser" in result.js
        assert "force-rv-1" in result.js
        assert "(true)" in result.js  # the forced literal as raw JS
        # Phase markers used by the Lab session pane.
        assert '"phase": "forced"' in result.js
        assert '"phase": "ready"' in result.js
        assert '"phase": "error"' in result.js
        # Summary surfaces the contract the operator needs to see
        # before injecting: the original method body is NOT executed.
        s = result.summary.lower()
        assert "force" in s
        assert "not executed" in s or "side effects" in s
        assert "true" in result.summary  # forced literal mentioned
        # Sensitive-APIs metadata flags the implementation hijack.
        from androscan.adapters.frida_hooks import get_template

        tmpl = get_template("force_return_value")
        assert "overload.implementation" in tmpl.sensitive_apis

    def test_force_method_skip_renders(self):
        """Phase 10 sub-step 10.4 — bypass-planner void-gate template."""
        result = render_by_id(
            "force_method_skip",
            {
                "class_name": "com.example.LicenseGate",
                "method_name": "enforceLicense",
                "return_descriptor": "V",
                "event_label": "force-skip-1",
            },
        )
        assert "com.example.LicenseGate" in result.js
        assert "enforceLicense" in result.js
        assert "force-skip-1" in result.js
        assert '"V"' in result.js  # descriptor used in the JS
        # Phase markers — ``skipped`` instead of ``forced`` to
        # distinguish from force_return_value events on the
        # operator-facing trace stream.
        assert '"phase": "skipped"' in result.js
        assert '"phase": "ready"' in result.js
        s = result.summary.lower()
        assert "skip" in s or "no-op" in s or "stub" in s
        assert "side effects" in s or "not executed" in s
        # The descriptor → zero-value mapping for void / Z / I / L is
        # the contract operators rely on. Spot-check non-void rendering
        # too — the descriptor flows into the JS, not the descriptor
        # text in the summary.
        result_z = render_by_id(
            "force_method_skip",
            {
                "class_name": "com.example.Foo",
                "method_name": "isOk",
                "return_descriptor": "Z",
                "event_label": "skip-z",
            },
        )
        assert '"Z"' in result_z.js

    def test_force_string_compare_equal_renders(self):
        """Phase 10 sub-step 10.4 — license-check / secret-string bypass."""
        result = render_by_id(
            "force_string_compare_equal",
            {
                "target_literal": "LICENSE_VALID_42",
                "event_label": "string-eq-1",
            },
        )
        assert "LICENSE_VALID_42" in result.js
        assert "string-eq-1" in result.js
        # Both String.equals overloads must be hooked — that's the
        # template's whole-point (covers both equals + equalsIgnoreCase
        # patterns operators see in the wild).
        assert "String.equals" in result.js or "Str.equals" in result.js
        assert "equalsIgnoreCase" in result.js
        # Phase marker.
        assert '"phase": "forced"' in result.js
        # Summary calls out the literal-gated nature so operators
        # understand the blast-radius before injecting.
        s = result.summary.lower()
        assert "literal" in s
        assert "app-wide" in s or "blast-radius" in s
        # Sensitive-APIs metadata flags the String hooks specifically.
        from androscan.adapters.frida_hooks import get_template

        tmpl = get_template("force_string_compare_equal")
        assert "String.equals" in tmpl.sensitive_apis
        assert "String.equalsIgnoreCase" in tmpl.sensitive_apis

    def test_scope_inspector_renders(self):
        result = render_by_id(
            "scope_inspector",
            {
                "class_name": "com.example.SessionManager",
                "method_name": "refreshToken",
                "event_label": "scope-1",
            },
        )
        # JS body — every parameter substituted, plus the structural
        # markers the aggregator + UI rely on.
        assert "com.example.SessionManager" in result.js
        assert "refreshToken" in result.js
        assert "scope-1" in result.js
        # Field-snapshot machinery must be present (this is the bit
        # that makes scope_inspector different from entry_exit_log).
        assert "getDeclaredFields" in result.js
        assert "setAccessible" in result.js
        assert "this_fields" in result.js
        assert "snapshotFields" in result.js
        # Both phases emit the discriminator the aggregator pattern-
        # matches on.
        assert '"phase": "entry"' in result.js
        assert '"phase": "exit"' in result.js
        assert '"phase": "ready"' in result.js
        # Summary surfaces the read-only contract + the fields concept
        # so an operator picking from the dropdown understands what
        # this template captures.
        s = result.summary.lower()
        assert "read-only" in s
        assert "field" in s
        assert "this_fields" in s
        assert "scope-1" in result.summary
        # Sensitive-APIs metadata is the operator-facing audit hint.
        from androscan.adapters.frida_hooks import get_template

        tmpl = get_template("scope_inspector")
        assert "Class.getDeclaredFields" in tmpl.sensitive_apis
        assert "Field.setAccessible" in tmpl.sensitive_apis
