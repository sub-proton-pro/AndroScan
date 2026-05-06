"""Tests for the LCP.6 grammar / JSON-schema emitters in
:mod:`androscan.llm.grammar`.

The emitters constrain the response envelope sent to the local LLM
providers (Ollama JSON-schema mode + llama.cpp grammar mode); the
tests here cover:

* Skill-name discovery (delegates to the live registry).
* JSON-schema shape — top-level keys, ``additionalProperties: False``
  posture, hypothesis-item permissiveness, ``skill_requests[*].skill``
  enum exactly matching the registered skill names.
* GBNF source — basic well-formedness (balanced braces, ``::=``
  separators, prelude included), per-skill alternative coverage.
* Parser invariance — a JSON document that satisfies the schema /
  GBNF still parses cleanly through
  :func:`androscan.llm.parser.parse_response` (the v1 LCP fail-soft
  default-fill semantics MUST still work in grammar mode).
* Feature-flag helper — ``Config.local_grammar_enabled`` resolves
  through :func:`is_grammar_enabled` with the documented defaults.
"""

import json
from unittest.mock import MagicMock

import pytest

from androscan.config import Config
from androscan.llm.grammar import (
    active_skill_names,
    build_response_gbnf,
    build_response_json_schema,
    is_grammar_enabled,
    json_schema_for_format_field,
    json_schema_to_wire_str,
)
from androscan.llm.parser import parse_response


# ---------------------------------------------------------------------------
# Skill-name discovery
# ---------------------------------------------------------------------------


class TestActiveSkillNames:
    """The emitters' discriminated-union enum is sourced from the live
    skill registry — these tests pin the contract."""

    def test_returns_only_llm_tier_skills_by_default(self) -> None:
        """Pipeline / exploit-tier skills are scheduler-driven (never
        appear in ``skill_requests``); they must NOT leak into the
        emitted enum."""
        from androscan.skills import _REGISTRY, discover, list_llm_skills

        if not _REGISTRY:
            discover()
        expected = sorted(s.name for s in list_llm_skills())
        assert active_skill_names() == expected

    def test_returns_full_registry_when_only_llm_tier_false(self) -> None:
        """Test-side / UI-side callers can ask for the full registry."""
        from androscan.skills import _REGISTRY, discover

        if not _REGISTRY:
            discover()
        expected = sorted(_REGISTRY.keys())
        assert active_skill_names(only_llm_tier=False) == expected

    def test_result_is_sorted(self) -> None:
        """Sort order must be stable so the emitted JSON-schema /
        GBNF output is deterministic across runs (matters for the
        snapshot tests below + for any operator diffing two
        Settings-tab "what gets sent" previews)."""
        names = active_skill_names()
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# JSON-schema emitter — shape + per-key contracts
# ---------------------------------------------------------------------------


class TestBuildResponseJsonSchema:
    """The shape Ollama receives in ``format: <schema>`` mode."""

    def test_top_level_is_strict_object(self) -> None:
        schema = build_response_json_schema()
        assert schema["type"] == "object"
        # additionalProperties: False at the root forbids unknown keys
        # — the parser ignores them anyway, but the strict posture
        # gives the model a stronger nudge to NOT emit them.
        assert schema["additionalProperties"] is False

    def test_top_level_keys_match_parser_shape(self) -> None:
        """The three keys MUST mirror :class:`LLMResponse`'s fields."""
        schema = build_response_json_schema()
        assert set(schema["properties"].keys()) == {
            "summary",
            "skill_requests",
            "hypotheses",
        }

    def test_no_required_top_level_keys(self) -> None:
        """All three top-level keys are OPTIONAL — the parser
        tolerates partial responses (just summary / just hypotheses /
        empty object) and the schema must match that posture."""
        schema = build_response_json_schema()
        assert "required" not in schema

    def test_summary_is_string(self) -> None:
        schema = build_response_json_schema()
        assert schema["properties"]["summary"] == {"type": "string"}

    def test_skill_requests_is_array_of_objects(self) -> None:
        schema = build_response_json_schema()
        sr = schema["properties"]["skill_requests"]
        assert sr["type"] == "array"
        item = sr["items"]
        assert item["type"] == "object"
        assert item["required"] == ["skill"]
        assert item["additionalProperties"] is False

    def test_skill_enum_matches_registry(self) -> None:
        """The discriminated-union enum is the only place where the
        v1 LCP grammar disagrees with v0 ``response_format: {"type":
        "json_object"}`` — schema-mode rejection of e.g. a typo'd
        ``"query_calligraph"`` is the whole point of LCP.6."""
        schema = build_response_json_schema()
        enum = schema["properties"]["skill_requests"]["items"]["properties"]["skill"]["enum"]
        assert enum == active_skill_names()
        # Sanity: the registry includes at least the v2 LLM-tier skill set.
        assert "query_call_graph" in enum
        assert "trace_behavior" in enum
        assert "suggest_trace_entry" in enum

    def test_skill_enum_falls_back_to_string_when_no_skills_supplied(self) -> None:
        """Defensive: an empty ``skill_names`` argument falls through
        to a plain string type so the schema is still well-formed
        (matters for unit-tested fixtures, not real runtime)."""
        schema = build_response_json_schema(skill_names=[])
        skill_field = schema["properties"]["skill_requests"]["items"]["properties"]["skill"]
        assert skill_field == {"type": "string"}

    def test_skill_request_params_is_permissive_object(self) -> None:
        """Per-skill ``params_schema`` is operator-prose, not typed —
        the schema MUST allow any ``params`` object so the LLM isn't
        forced into a non-existent contract."""
        schema = build_response_json_schema()
        params = schema["properties"]["skill_requests"]["items"]["properties"]["params"]
        assert params == {"type": "object"}

    def test_hypotheses_is_array_of_permissive_objects(self) -> None:
        """The parser default-fills missing fields, so the schema
        MUST allow partial / extra-field hypotheses without
        rejection."""
        schema = build_response_json_schema()
        h = schema["properties"]["hypotheses"]
        assert h["type"] == "array"
        item = h["items"]
        assert item["type"] == "object"
        # No required keys — the parser fills defaults for everything.
        assert "required" not in item
        # Permissive posture — LLMs frequently emit extras the parser
        # tolerates (e.g. "name" as a fallback alias for "title").
        assert item["additionalProperties"] is True

    def test_hypothesis_int_fields_have_1_to_5_range(self) -> None:
        """``exploitability`` / ``confidence`` are 1-5 integers per
        :func:`androscan.llm.prompts.build_prompt`'s system message
        ("integers 1-5"); the schema enforces the same range so an
        LLM emitting ``exploitability: 7`` triggers a sampling
        rejection rather than a silent over-rated finding."""
        schema = build_response_json_schema()
        item_props = schema["properties"]["hypotheses"]["items"]["properties"]
        for f in ("exploitability", "confidence"):
            assert item_props[f]["type"] == "integer"
            assert item_props[f]["minimum"] == 1
            assert item_props[f]["maximum"] == 5

    def test_evidence_refs_is_array_of_strings(self) -> None:
        schema = build_response_json_schema()
        item_props = schema["properties"]["hypotheses"]["items"]["properties"]
        ev = item_props["evidence_refs"]
        assert ev == {"type": "array", "items": {"type": "string"}}

    def test_exploit_params_allows_object_or_null(self) -> None:
        """``Optional[dict]`` in the parser maps to ``["object", "null"]``
        in JSON-schema speak."""
        schema = build_response_json_schema()
        item_props = schema["properties"]["hypotheses"]["items"]["properties"]
        assert item_props["exploit_params"]["type"] == ["object", "null"]

    def test_serialises_to_valid_json(self) -> None:
        """The schema dict MUST round-trip through
        :func:`json.dumps` so it can be sent in the Ollama
        ``format`` field as either a dict or a serialised string."""
        schema = build_response_json_schema()
        wire = json.dumps(schema)
        round_tripped = json.loads(wire)
        assert round_tripped == schema

    def test_json_schema_for_format_field_is_alias(self) -> None:
        """The Ollama-specific wrapper is intentionally a thin alias
        today — kept as a separate name for future divergence
        without ripple to other callers."""
        assert json_schema_for_format_field() == build_response_json_schema()

    def test_json_schema_to_wire_str_is_compact(self) -> None:
        """The wire form uses the compact separators so the request
        body stays small (matters for an Ollama instance with a
        tight ``num_ctx`` budget)."""
        schema = {"type": "object"}
        out = json_schema_to_wire_str(schema)
        # Compact separators — no whitespace.
        assert ", " not in out
        assert ": " not in out
        assert json.loads(out) == schema


# ---------------------------------------------------------------------------
# GBNF emitter — well-formedness + skill-enum coverage
# ---------------------------------------------------------------------------


class TestBuildResponseGbnf:
    """The grammar source llama.cpp receives in the ``grammar`` field."""

    def test_grammar_is_non_empty_string(self) -> None:
        gbnf = build_response_gbnf()
        assert isinstance(gbnf, str)
        assert len(gbnf) > 100

    def test_grammar_has_root_rule(self) -> None:
        """``root`` is GBNF's required entrypoint — without it the
        llama.cpp parser raises 'no root rule' and the grammar is
        unusable."""
        gbnf = build_response_gbnf()
        assert "\nroot ::=" in "\n" + gbnf
        # Specifically: root references response-object.
        assert "root ::= ws response-object ws" in gbnf

    def test_grammar_includes_json_value_prelude(self) -> None:
        """The standard JSON value productions (``string``, ``number``,
        ``object``, ``array``) MUST be present so non-discriminated
        fields like ``params`` / ``exploit_params`` accept any
        valid JSON object. The prelude uses right-padded alignment
        (``string     ::=``) so we look for the rule name at the
        start of a line rather than a fixed-whitespace prefix."""
        import re

        gbnf = build_response_gbnf()
        for rule in ("string", "number", "object", "array", "value", "member"):
            pattern = re.compile(rf"^{re.escape(rule)}\s*::=", re.MULTILINE)
            assert pattern.search(gbnf), f"missing prelude rule: {rule} ::="

    def test_grammar_skill_name_alternatives_match_registry(self) -> None:
        """Every registered LLM-tier skill MUST appear as a
        double-quoted string literal in the ``skill-name`` rule —
        anything else means the model can request a skill the chat
        agentic loop can't dispatch."""
        gbnf = build_response_gbnf()
        for name in active_skill_names():
            # The double-quote is escaped in the GBNF emission since
            # the rule wraps each skill name in literal quotes:
            #     skill-name ::= "\"foo\"" | "\"bar\""
            literal = f'"\\"{name}\\""'
            assert literal in gbnf, f"missing skill alternative: {name}"

    def test_grammar_skill_name_uses_pipe_alternation(self) -> None:
        """Discriminated-union via GBNF ``|`` alternation — the
        model can ONLY emit one of the registered skill names in
        the ``"skill"`` value."""
        gbnf = build_response_gbnf()
        assert "skill-name ::=" in gbnf
        # At least one alternation separator is present (we have
        # 9+ skills today).
        skill_line = next(
            line for line in gbnf.splitlines() if line.startswith("skill-name ::=")
        )
        assert "|" in skill_line

    def test_grammar_balanced_braces(self) -> None:
        """A grammar with mismatched braces / brackets is invalid
        GBNF and llama.cpp rejects it on first parse. Cheap
        well-formedness check that catches accidental edits."""
        gbnf = build_response_gbnf()
        assert gbnf.count("{") + gbnf.count("}") > 0  # sanity
        # Balanced parens + brackets.
        assert gbnf.count("(") == gbnf.count(")")
        assert gbnf.count("[") == gbnf.count("]")

    def test_grammar_falls_back_to_string_with_no_skills(self) -> None:
        """Defensive: empty registry produces a well-formed grammar
        whose ``skill-name`` rule accepts any string."""
        gbnf = build_response_gbnf(skill_names=[])
        assert "skill-name ::= string" in gbnf

    def test_grammar_alternative_count_matches_skill_count(self) -> None:
        """Snapshot-style — exactly ``len(skill_names)`` alternatives
        in the skill-name rule (catches accidental duplication or
        truncation)."""
        names = active_skill_names()
        gbnf = build_response_gbnf()
        skill_line = next(
            line for line in gbnf.splitlines() if line.startswith("skill-name ::=")
        )
        # ``"foo" | "bar"`` has one fewer ``|`` than alternatives.
        bar_count = skill_line.count(" | ")
        assert bar_count == len(names) - 1

    def test_grammar_includes_object_member_rule(self) -> None:
        """The ``object`` rule references ``member`` — needed so
        ``params`` / ``exploit_params`` accept arbitrary
        key:value pairs."""
        import re

        gbnf = build_response_gbnf()
        # Loose pattern — prelude uses right-padded alignment.
        assert re.search(r"^member\s*::=", gbnf, re.MULTILINE)
        assert re.search(r"^object\s*::=", gbnf, re.MULTILINE)


# ---------------------------------------------------------------------------
# Parser invariance — output matching the grammar still parses cleanly
# ---------------------------------------------------------------------------


class TestParserInvariance:
    """A JSON document that the schema would accept MUST still parse
    cleanly through :func:`parse_response` — over-strictness in the
    schema would silently break the v1 LCP fail-soft default-fill
    semantics."""

    def test_empty_object_parses(self) -> None:
        """The empty object is the most common LLM "I'm done" signal;
        the schema marks all three top-level keys optional."""
        out = parse_response("{}")
        assert out.summary is None
        assert out.skill_requests == []
        assert out.hypotheses == []

    def test_summary_only_parses(self) -> None:
        out = parse_response('{"summary": "no findings"}')
        assert out.summary == "no findings"

    def test_skill_requests_only_parses(self) -> None:
        names = active_skill_names()
        if not names:
            pytest.skip("registry empty; can't pick a real skill name")
        body = json.dumps({
            "skill_requests": [
                {"skill": names[0], "params": {"foo": "bar"}},
            ],
        })
        out = parse_response(body)
        assert len(out.skill_requests) == 1
        assert out.skill_requests[0].skill == names[0]
        assert out.skill_requests[0].params == {"foo": "bar"}

    def test_hypotheses_with_partial_fields_parses(self) -> None:
        """Partial hypothesis (missing description / evidence_refs etc.)
        — the parser fills defaults; the schema's
        ``additionalProperties: True`` + no ``required`` ensures the
        LLM can still emit this shape."""
        body = json.dumps({
            "hypotheses": [
                {"id": "f1", "title": "x", "exploitability": 3, "confidence": 4},
            ],
        })
        out = parse_response(body)
        assert len(out.hypotheses) == 1
        h = out.hypotheses[0]
        assert h.id == "f1"
        assert h.title == "x"
        assert h.exploitability == 3
        assert h.confidence == 4
        # Default-filled.
        assert h.description == ""
        assert h.evidence_refs == []

    def test_full_response_with_all_three_keys_parses(self) -> None:
        names = active_skill_names()
        if not names:
            pytest.skip("registry empty; can't pick a real skill name")
        body = json.dumps({
            "summary": "round-trip test",
            "skill_requests": [{"skill": names[0], "params": {}}],
            "hypotheses": [
                {
                    "id": "h1",
                    "component_type": "activity",
                    "component_name": "MainActivity",
                    "title": "test",
                    "description": "desc",
                    "evidence_refs": ["evidence/1"],
                    "exploitability": 3,
                    "confidence": 4,
                    "remediation_hint": "fix it",
                    "exploit_params": {"foo": 1},
                }
            ],
        })
        out = parse_response(body)
        assert out.summary == "round-trip test"
        assert len(out.skill_requests) == 1
        assert len(out.hypotheses) == 1
        assert out.hypotheses[0].exploit_params == {"foo": 1}

    def test_hypothesis_with_extra_keys_parses(self) -> None:
        """``additionalProperties: True`` on hypothesis-item allows
        common LLM extras (``name`` alias, free-form ``notes``);
        parser ignores them but accepts the response."""
        body = json.dumps({
            "hypotheses": [
                {
                    "name": "fallback for title",  # parser falls back to ``name``
                    "description": "x",
                    "exploitability": 2,
                    "confidence": 3,
                    "notes": "ignored extra",
                }
            ],
        })
        out = parse_response(body)
        assert len(out.hypotheses) == 1
        assert out.hypotheses[0].title == "fallback for title"


# ---------------------------------------------------------------------------
# Feature-flag helper
# ---------------------------------------------------------------------------


class TestIsGrammarEnabled:
    """The single kill-switch helper read by both local-provider
    branches in ``androscan.llm.client``."""

    def test_default_config_is_enabled(self) -> None:
        """LCP.6 / DEC-027 Q2 (a) committed follow-up — ships ON by
        default."""
        cfg = Config.default()
        assert is_grammar_enabled(cfg) is True

    def test_explicit_false_disables(self) -> None:
        cfg = MagicMock(local_grammar_enabled=False)
        assert is_grammar_enabled(cfg) is False

    def test_missing_attribute_defaults_to_true(self) -> None:
        """MagicMock-based test configs that pre-date LCP.6 keep
        the grammar path active — important so
        ``tests/test_llm_client.py`` fixtures don't all need a
        ``local_grammar_enabled`` override."""

        # A real object without the attr (rather than MagicMock,
        # which auto-spawns a truthy mock for missing attrs).
        class _Bare:
            pass

        assert is_grammar_enabled(_Bare()) is True

    def test_string_truthy_values_resolve_to_true(self) -> None:
        for val in ("true", "True", "1", "yes", "on", " on "):
            assert is_grammar_enabled(MagicMock(local_grammar_enabled=val)) is True

    def test_string_falsy_values_resolve_to_false(self) -> None:
        for val in ("false", "0", "no", "off", "False"):
            assert is_grammar_enabled(MagicMock(local_grammar_enabled=val)) is False
