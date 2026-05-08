"""Unit tests for the ``behavior_trace_multi`` Frida hook template.

Phase 13 / DEC-029, sub-step 13.1. The structural invariants (every
declared placeholder is a declared param, both bodies non-empty,
every required param used somewhere) are enforced by the registry
walk in :mod:`tests.test_frida_hook_templates`. This file owns the
template's *semantic* contract:

* The rendered JS body parses through ``pyjsparser`` (catches
  brace-doubling drift and authoring typos at CI time without a
  running Frida / JVM).
* The wire-shape vocabulary is intact — every locked phase
  (``hook_failed`` / ``ready`` / ``entry`` / ``exit`` / ``error``)
  appears in the JS, plus the three locked ``reason`` values
  (``class_not_found`` / ``method_not_found`` / ``impl_set_failed``).
* The per-thread state machine + monotonic ``seq`` counter exist by
  name (``pushFrame`` / ``popFrame`` / ``seqCounter`` /
  ``parent_call_seq``) — substring-shaped, so a future contributor
  refactoring those into helper modules will surface the rename here.
* The serialiser tier dispatchers (``tierByteArray`` / ``tierBundle``
  / ``tierIntent`` / ``tierList`` / ``tierMap`` / ``tierReflectFields``)
  are present and dispatched in the locked order.
* Parameter validation: missing ``methods_json`` /
  ``event_label`` raise :class:`HookParamError`. Empty arrays /
  malformed JS-object-literal values render successfully (the
  template's runtime guards handle the empty-array case; malformed
  JS is the caller's responsibility — same contract ``custom.py``
  established for ``js_body``).
* The pentester summary mentions every wire-shape phase and every
  serialiser tier in operator-readable language.
"""

from __future__ import annotations

import json

import pytest

from androscan.adapters.frida_hooks import (
    HookParamError,
    RenderedHook,
    _jsparse,
    get_template,
    render,
    render_by_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _methods_json(specs: list[dict[str, str]]) -> str:
    """Return the JS-object-literal-formatted string the template expects."""

    return json.dumps(specs)


@pytest.fixture
def weakbank_methods() -> str:
    """A representative 3-method closure for WeakBank's login flow."""

    return _methods_json(
        [
            {
                "class": "com.example.weakbank.MainActivity",
                "method": "login",
                "descriptor": "(Ljava/lang/String;Ljava/lang/String;)Z",
            },
            {
                "class": "com.example.weakbank.LoginManager",
                "method": "validatePin",
                "descriptor": "(Ljava/lang/String;)Z",
            },
            {
                "class": "com.example.weakbank.SessionStore",
                "method": "createSession",
                "descriptor": "(Ljava/lang/String;)V",
            },
        ]
    )


# ---------------------------------------------------------------------------
# Render contract
# ---------------------------------------------------------------------------


class TestRender:
    def test_smoke_render_returns_rendered_hook(self, weakbank_methods: str) -> None:
        result = render_by_id(
            "behavior_trace_multi",
            {"methods_json": weakbank_methods, "event_label": "weakbank-login"},
        )
        assert isinstance(result, RenderedHook)
        assert result.template_id == "behavior_trace_multi"
        assert result.js
        assert result.summary
        assert set(result.params_used) == {"methods_json", "event_label"}

    def test_event_label_threaded_through_js(self, weakbank_methods: str) -> None:
        # Operator picks a label, every emitted event must carry it so the
        # frontend WebSocket multiplex can filter cleanly.
        result = render_by_id(
            "behavior_trace_multi",
            {"methods_json": weakbank_methods, "event_label": "trace-42"},
        )
        # Substring count: the label appears on every send() call site
        # (hook_failed * 3 reasons, entry, exit, error, ready * 2 + the
        # comment header). A floor of 6 guards against accidentally
        # threading the label through only a subset of the call sites.
        assert result.js.count('"trace-42"') >= 6, (
            "event_label must appear on every send() payload; "
            "found "
            f"{result.js.count('\"trace-42\"')} occurrences"
        )

    def test_methods_json_substituted_as_js_array_literal(self) -> None:
        # The renderer substitutes ``methods_json`` verbatim into a JS
        # expression context. ``json.dumps`` produces a syntactically-
        # valid JS array literal, so the rendered JS contains the
        # method spec inline (no JSON.parse round-trip needed).
        methods = _methods_json(
            [{"class": "com.example.Foo", "method": "bar", "descriptor": "()V"}]
        )
        result = render_by_id(
            "behavior_trace_multi",
            {"methods_json": methods, "event_label": "smoke"},
        )
        assert "var methodsList = [" in result.js
        assert '"class": "com.example.Foo"' in result.js
        assert '"method": "bar"' in result.js
        assert '"descriptor": "()V"' in result.js


class TestParamValidation:
    def test_missing_methods_json_raises(self) -> None:
        with pytest.raises(HookParamError, match="methods_json"):
            render_by_id("behavior_trace_multi", {"event_label": "x"})

    def test_missing_event_label_raises(self, weakbank_methods: str) -> None:
        with pytest.raises(HookParamError, match="event_label"):
            render_by_id("behavior_trace_multi", {"methods_json": weakbank_methods})

    def test_empty_methods_json_renders(self) -> None:
        # An empty array is a legal closure (the template's runtime
        # guard short-circuits to the ``ready`` event with all-zero
        # counts). The renderer should NOT reject it.
        result = render_by_id(
            "behavior_trace_multi",
            {"methods_json": _methods_json([]), "event_label": "empty"},
        )
        assert "var methodsList = [];" in result.js

    def test_unknown_param_raises(self, weakbank_methods: str) -> None:
        with pytest.raises(HookParamError, match="bogus"):
            render_by_id(
                "behavior_trace_multi",
                {
                    "methods_json": weakbank_methods,
                    "event_label": "x",
                    "bogus": "extra",
                },
            )


# ---------------------------------------------------------------------------
# Wire-shape vocabulary
# ---------------------------------------------------------------------------


class TestWireShape:
    """Every phase + reason locked in DEC-029 / 13.1 must appear in the JS.

    These are deliberately substring-shaped (not regex) so the failure
    output is greppable. A failure here means a wire-shape regression —
    the frontend reconstruction (13.6) and the JSONL persistence
    (13.2) consume these strings directly.
    """

    @pytest.fixture
    def js(self, weakbank_methods: str) -> str:
        return render_by_id(
            "behavior_trace_multi",
            {"methods_json": weakbank_methods, "event_label": "wire"},
        ).js

    @pytest.mark.parametrize(
        "phase",
        ["hook_failed", "ready", "entry", "exit", "error"],
    )
    def test_phase_present(self, js: str, phase: str) -> None:
        assert f'"phase": "{phase}"' in js, (
            f"locked wire-shape phase {phase!r} missing from rendered JS"
        )

    @pytest.mark.parametrize(
        "reason",
        ["class_not_found", "method_not_found", "impl_set_failed"],
    )
    def test_hook_failure_reason_present(self, js: str, reason: str) -> None:
        assert f'"reason": "{reason}"' in js, (
            f"locked hook_failed reason {reason!r} missing from rendered JS"
        )

    @pytest.mark.parametrize(
        "field",
        [
            "seq",
            "thread_id",
            "thread_name",
            "thread_depth",
            "parent_call_seq",
            "entry_seq",
        ],
    )
    def test_event_payload_field_present(self, js: str, field: str) -> None:
        assert f'"{field}":' in js, (
            f"locked event payload field {field!r} missing from rendered JS"
        )

    def test_state_machine_helpers_present(self, js: str) -> None:
        # The per-thread call-stack tracking is the load-bearing logic
        # for 13.6's flowchart parent-child reconstruction. Renaming
        # these without updating the frontend wire-shape readme would
        # break the contract; this test makes the rename a forced
        # touch-point.
        for symbol in ("seqCounter", "pushFrame", "popFrame", "threadStacks", "nextSeq"):
            assert symbol in js, (
                f"state-machine symbol {symbol!r} missing; "
                "wire-shape helpers must remain greppable"
            )


# ---------------------------------------------------------------------------
# Serialiser tier dispatch
# ---------------------------------------------------------------------------


class TestSerialiserTiers:
    """The eleven tiers are dispatched in a fixed order (see DEC-029).

    The substring assertions catch reorderings that would shift
    operator-visible output (e.g. a class with a useful ``toString()``
    suddenly rendering via reflection because the tiers got swapped)
    without surfacing a hard syntax error. Each handler's *name* is
    the canonical anchor.
    """

    @pytest.fixture
    def js(self, weakbank_methods: str) -> str:
        return render_by_id(
            "behavior_trace_multi",
            {"methods_json": weakbank_methods, "event_label": "tiers"},
        ).js

    @pytest.mark.parametrize(
        "tier_handler",
        [
            "tierByteArray",
            "tierBundle",
            "tierIntent",
            "tierList",
            "tierMap",
            "tierReflectFields",
        ],
    )
    def test_tier_handler_present(self, js: str, tier_handler: str) -> None:
        assert tier_handler in js, (
            f"tier handler {tier_handler!r} missing from rendered JS"
        )

    def test_tier_dispatch_order(self, js: str) -> None:
        # Within ``summarise(value, depth)``, the tier handlers must be
        # dispatched in the locked order: byte[] -> Bundle -> Intent ->
        # List -> Map -> toString-fingerprint -> reflection -> fallback.
        # A reorder would silently change the operator-visible output
        # for ambiguous values (e.g. a ``LinkedHashMap`` that's both a
        # Map and has a useful ``toString()``).
        #
        # Use ``rfind`` because each tier handler appears twice in the
        # rendered JS — once at its function definition, once at its
        # dispatch call inside ``summarise``. The dispatch call comes
        # later, so ``rfind`` is what isolates the dispatch order.
        positions = [
            ("tierByteArray", js.rfind("tierByteArray(value)")),
            ("tierBundle", js.rfind("tierBundle(value, depth)")),
            ("tierIntent", js.rfind("tierIntent(value, depth)")),
            ("tierList", js.rfind("tierList(value, depth)")),
            ("tierMap", js.rfind("tierMap(value, depth)")),
            (
                "DEFAULT_TOSTRING_REGEX",
                js.rfind("DEFAULT_TOSTRING_REGEX.test(stringified)"),
            ),
            ("tierReflectFields", js.rfind("tierReflectFields(value, depth)")),
        ]
        # All call sites must be found.
        for name, pos in positions:
            assert pos > 0, f"tier dispatch site for {name!r} not found in JS"
        # And they must appear in the locked order.
        for (name_a, pos_a), (name_b, pos_b) in zip(positions, positions[1:]):
            assert pos_a < pos_b, (
                f"tier order regression: {name_a!r} (at {pos_a}) "
                f"must dispatch before {name_b!r} (at {pos_b})"
            )

    def test_locked_thresholds_present(self, js: str) -> None:
        # DEC-029 locks these defaults; surfacing them here means a
        # silent edit (e.g. someone bumps STR_TRUNCATE_AT to 1024) is a
        # forced touch-point on this test, not a quiet behavioural
        # change in dogfood.
        assert "STR_TRUNCATE_AT = 256" in js
        assert "BYTE_FULL_HEX_LIMIT = 32" in js
        assert "BYTE_PREVIEW_LIMIT = 16" in js
        assert "MAX_FIELDS_PER_OBJECT = 8" in js
        assert "MAX_LIST_ENTRIES = 5" in js


# ---------------------------------------------------------------------------
# JS syntax validity
# ---------------------------------------------------------------------------


class TestJsSyntax:
    """Catch brace-doubling drift / authoring typos at CI time.

    The ``[frida]`` extra ships ``pyjsparser``; the wrapper at
    :mod:`androscan.adapters.frida_hooks._jsparse` degrades gracefully
    when it's not installed (returns ``available=False``). When the
    parser is present, this test gates the template's JS on a clean
    parse — same gate the Hook Lab Inject button uses.
    """

    def test_rendered_js_parses_cleanly(self, weakbank_methods: str) -> None:
        result = render_by_id(
            "behavior_trace_multi",
            {"methods_json": weakbank_methods, "event_label": "parse-check"},
        )
        parse = _jsparse.parse_frida_js(result.js)
        if not parse.available:
            pytest.skip("pyjsparser not installed; skipping syntax check")
        assert parse.ok, (
            f"rendered JS failed to parse: {parse.error} "
            f"at line {parse.line}, column {parse.column}"
        )

    def test_empty_methods_renders_to_parseable_js(self) -> None:
        # The empty-array fast path is its own JS code path (early
        # return after the ``ready`` event); guard it separately so a
        # break there can't hide behind the multi-method case.
        result = render_by_id(
            "behavior_trace_multi",
            {"methods_json": _methods_json([]), "event_label": "empty-parse"},
        )
        parse = _jsparse.parse_frida_js(result.js)
        if not parse.available:
            pytest.skip("pyjsparser not installed; skipping syntax check")
        assert parse.ok, (
            f"empty-methods render failed to parse: {parse.error} "
            f"at line {parse.line}, column {parse.column}"
        )


# ---------------------------------------------------------------------------
# Pentester summary content
# ---------------------------------------------------------------------------


class TestSummary:
    @pytest.fixture
    def summary(self, weakbank_methods: str) -> str:
        return render_by_id(
            "behavior_trace_multi",
            {"methods_json": weakbank_methods, "event_label": "summary"},
        ).summary

    @pytest.mark.parametrize(
        "phase",
        ["hook_failed", "ready", "entry", "exit", "error"],
    )
    def test_summary_mentions_phase(self, summary: str, phase: str) -> None:
        assert phase in summary, (
            f"pentester summary should mention wire-shape phase {phase!r}"
        )

    @pytest.mark.parametrize(
        "reason",
        ["class_not_found", "method_not_found", "impl_set_failed"],
    )
    def test_summary_mentions_failure_reason(self, summary: str, reason: str) -> None:
        assert reason in summary, (
            f"pentester summary should mention hook_failed reason {reason!r}"
        )

    def test_summary_mentions_inlining_callout(self, summary: str) -> None:
        # 13.7's Inspector pane consumes the summary as the canonical
        # operator-readable explainer. The R8-inlining story is the
        # single most-asked operator question (per planning checkpoint
        # transcript), so the summary must surface it explicitly.
        assert "inlin" in summary.lower(), (
            "pentester summary should explain the impl_set_failed -> R8 inlining link"
        )

    def test_summary_mentions_event_label(self, summary: str) -> None:
        assert "summary" in summary  # the event_label value rendered into the prose

    def test_summary_mentions_serialiser_tiers(self, summary: str) -> None:
        # Every tier the operator can see in event payloads should be
        # documented in the summary (so reading the summary alone tells
        # them what shape ``args`` / ``return`` will be in).
        for tier_name in ("Bundle", "Intent", "byte[]", "List", "Map", "toString"):
            assert tier_name in summary, (
                f"pentester summary should document serialiser tier {tier_name!r}"
            )


# ---------------------------------------------------------------------------
# Sensitive APIs metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_template_advertises_sensitive_apis(self) -> None:
        tmpl = get_template("behavior_trace_multi")
        # Every API listed must be a string the renderer would care
        # about (the Hook Lab UI surfaces this list verbatim under
        # "Touches:" — see ``HookBuilder.tsx``).
        assert "Java.use" in tmpl.sensitive_apis
        assert "Java.perform" in tmpl.sensitive_apis
        assert "Field.setAccessible" in tmpl.sensitive_apis
        assert "overload.implementation" in tmpl.sensitive_apis

    def test_template_id_and_name_stable(self) -> None:
        # The template id is part of the public API — 13.2's HTTP
        # route, 13.4's LLM skill summariser, 13.6's frontend all
        # reference the literal string "behavior_trace_multi". A rename
        # would need a ratcheted migration; this test makes the rename
        # a forced touch-point.
        tmpl = get_template("behavior_trace_multi")
        assert tmpl.id == "behavior_trace_multi"
        assert "Behavior trace" in tmpl.name
