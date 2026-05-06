"""LLM response-envelope grammar / JSON-schema emitter (LCP.6 / DEC-027).

Closes ``ISSUE-016`` (JSON-validity drift on aggressive quants under the
v1 LCP local providers): v1's ``response_format: {"type": "json_object"}``
parity only enforces *syntactically valid* JSON, not *schema conformance*,
so a Q4_K_M / IQ4_XS quant that picks a wrong skill name or a malformed
``skill_requests[*]`` shape lands as parsed-but-empty data downstream.
Grammar enforcement at the model's logits-sampling level closes the drift.

Two emitters live here, one per local provider:

* :func:`build_response_json_schema` — JSON Schema dict suitable for
  Ollama's ``format: <schema>`` payload (supported since Ollama 0.5.0,
  Dec 2024; older builds reject it with HTTP 400, the client falls
  back to ``format: "json"`` on first failure and caches the fallback
  for the lifetime of the process).
* :func:`build_response_gbnf` — GBNF source string for llama.cpp's
  ``grammar`` field on ``/v1/chat/completions`` (llama.cpp-specific
  extension to the OpenAI-compat shim; documented in
  ``llama.cpp/grammars/README.md``). The grammar is sent ALONGSIDE the
  existing ``response_format: {"type": "json_object"}`` so a
  ``llama-server`` build that doesn't honour the grammar field still
  gets the JSON-mode fallback.

Both emitters share :func:`active_skill_names`, which reads the live
:mod:`androscan.skills` registry. The skill list is a discriminated
union: only ``tier == "llm"`` skills are LLM-callable (``pipeline`` /
``exploit`` skills are operator-driven), so the emitted enum / GBNF
alternative list is exactly the set the chat agentic loop will
actually accept.

Why hand-rolled GBNF rather than ``json-schema-to-grammar``: the v1 LCP
``llama-server`` builds (master @ 2026-05) ship the schema-to-grammar
converter behind ``response_format: {"type": "json_schema", ...}`` but
its support varies build-to-build (some operator's homebrew installs
predate the converter). Sending raw GBNF via the documented ``grammar``
field is the most-portable surface; the GBNF here is small enough
(~70 LOC) to maintain by hand, and the snapshot tests in
``tests/test_grammar.py`` catch regressions.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional


# Permissive hypothesis-item schema used by both emitters. Mirrors the
# fields :class:`androscan.llm.parser.Hypothesis` reads, with permissive
# defaults to match the parser's fail-soft posture: every field has a
# sensible default in :func:`androscan.llm.parser.parse_response`, so
# the schema marks NONE as ``required`` (the schema enforces SHAPE, not
# completeness — parsing a partial hypothesis still produces a valid
# Hypothesis with default-filled fields).
#
# ``additionalProperties: True`` because real LLM output frequently
# contains extras the parser ignores (e.g. ``"name"`` as a fallback
# alias for ``"title"``, free-form ``"notes"`` keys). Forbidding them
# would over-constrain the model and trigger schema-mode rejections
# on otherwise-correct output.
_HYPOTHESIS_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "component_type": {"type": "string"},
        "component_name": {"type": "string"},
        "title": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "evidence_refs": {
            "type": "array",
            "items": {"type": "string"},
        },
        "exploitability": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
        },
        "confidence": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
        },
        "remediation_hint": {"type": "string"},
        "exploit_params": {
            "type": ["object", "null"],
        },
    },
    "additionalProperties": True,
}


def active_skill_names(only_llm_tier: bool = True) -> list[str]:
    """Return the registered skill names, sorted, used by the emitters.

    ``only_llm_tier`` defaults to ``True`` because the LLM only ever
    requests ``tier == "llm"`` skills directly — pipeline + exploit
    skills are scheduler-driven and never appear in
    ``skill_requests``. Callers that need the full set (e.g. for
    UI introspection) can pass ``False``.

    Skill discovery is idempotent (handled by
    :mod:`androscan.skills.__init__`), so this can be called once per
    request without measurable cost.
    """
    from androscan.skills import _REGISTRY, discover, list_llm_skills

    if not _REGISTRY:
        discover()
    if only_llm_tier:
        return sorted(s.name for s in list_llm_skills())
    return sorted(_REGISTRY.keys())


def build_response_json_schema(
    skill_names: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Build the response-envelope JSON Schema sent to Ollama in
    ``format: <schema>`` mode.

    The shape mirrors :class:`androscan.llm.parser.LLMResponse`:

      * ``summary``: optional string (LLM's natural-language summary)
      * ``skill_requests``: optional list of
        ``{"skill": <enum>, "params": <object>}`` items. The
        ``skill`` enum is the discriminated union — operators pass an
        ``only_llm_tier=True`` filter so the enum exactly matches what
        the chat agentic loop will dispatch on.
      * ``hypotheses``: optional list of finding objects. The schema
        is permissive (``additionalProperties: True``, no ``required``
        fields) to match the parser's fail-soft default-filling
        posture; over-constraining hypothesis shape would trigger
        schema-mode rejections on otherwise-correct LLM output and
        defeat the purpose of LCP.6.

    The TOP-LEVEL ``additionalProperties`` is False — the only valid
    keys at the root are the three above. This is the strongest
    constraint we can apply without breaking real LLM output.

    ``skill_names`` defaults to :func:`active_skill_names` (registry
    snapshot) but can be overridden for test fixtures.
    """
    if skill_names is None:
        names = active_skill_names()
    else:
        names = sorted(skill_names)

    skill_request_item: dict[str, Any] = {
        "type": "object",
        "properties": {
            "skill": (
                {"type": "string", "enum": list(names)}
                if names
                else {"type": "string"}
            ),
            "params": {"type": "object"},
        },
        "required": ["skill"],
        "additionalProperties": False,
    }

    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "skill_requests": {
                "type": "array",
                "items": skill_request_item,
            },
            "hypotheses": {
                "type": "array",
                "items": _HYPOTHESIS_ITEM_SCHEMA,
            },
        },
        "additionalProperties": False,
    }


# Hand-rolled GBNF for the response envelope. The structure mirrors the
# JSON Schema above but expressed in the GBNF dialect llama.cpp's
# grammar engine accepts (documented in ``llama.cpp/grammars/README.md``).
#
# Design notes:
#   * ``ws`` = inline whitespace (space + tab + newline) — JSON's
#     standard insignificant-whitespace.
#   * The "any-order, any-subset" outer object is expressed as a
#     repetition of ``response-pair`` separated by commas, which is
#     the simplest way to allow the LLM to emit summary / skill_requests
#     / hypotheses in any order without combinatorial expansion.
#   * The ``params`` field's value is the standard ``object`` rule
#     (free-form JSON object) because the per-skill ``params_schema``
#     in ``SkillMeta`` is operator-prose, not typed JSON Schema, so
#     there's no per-skill discriminated-union to emit at the GBNF
#     level. The ``skill`` enum is the only discriminating constraint.
#   * ``hypothesis-item`` is the standard ``object`` rule (permissive)
#     for the same reason :data:`_HYPOTHESIS_ITEM_SCHEMA` uses
#     ``additionalProperties: True`` — the parser is fail-soft and
#     over-constraining the grammar triggers spurious sampling
#     rejections.
#   * The standard JSON value productions (``string``, ``number``,
#     ``boolean``, ``null-val``, ``object``, ``array``, ``value``) are
#     lifted from llama.cpp's ``grammars/json.gbnf`` template; the
#     emitter builds the response-specific rules ON TOP of those.
_GBNF_JSON_PRELUDE = r"""
ws         ::= [ \t\n\r]*
boolean    ::= "true" | "false"
null-val   ::= "null"
number     ::= ("-"? ([0-9] | [1-9] [0-9]+)) ("." [0-9]+)? ([eE] [-+]? [0-9]+)?
hex        ::= [0-9a-fA-F]
unicode-esc ::= "u" hex hex hex hex
escape     ::= "\\" (["\\/bfnrt] | unicode-esc)
char       ::= [^"\\] | escape
string     ::= "\"" char* "\""
value      ::= object | array | string | number | boolean | null-val
member     ::= ws string ws ":" ws value
object     ::= "{" ws (member (ws "," ws member)*)? ws "}"
array      ::= "[" ws (value (ws "," ws value)*)? ws "]"
""".lstrip()


def _escape_gbnf_string_literal(s: str) -> str:
    """Escape a Python string for use as a GBNF double-quoted string literal.

    GBNF string literals follow JSON-string escaping conventions: the
    backslash and double-quote are the only chars that MUST be escaped
    inside ``"..."``. Skill names are validated against
    ``[a-zA-Z_][a-zA-Z0-9_]*`` upstream (Python identifier rules) so
    in practice no escaping is needed — but we apply it defensively
    so a future skill name with a punctuation char doesn't break the
    grammar silently.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_response_gbnf(
    skill_names: Optional[Iterable[str]] = None,
) -> str:
    """Build the GBNF source string sent to llama.cpp in the
    ``grammar`` field on ``/v1/chat/completions``.

    The returned grammar is a single GBNF document (multiple
    production rules separated by newlines). The ``root`` rule is the
    response-envelope object; the standard JSON value productions
    (string / number / object / array / etc.) are appended verbatim
    from the prelude at module level.

    ``skill_names`` defaults to :func:`active_skill_names`. If the
    registry is empty (no skills loaded — should never happen at
    runtime, but possible in isolated tests), ``skill-name`` falls
    back to the standard ``string`` rule so the grammar is still
    well-formed.
    """
    if skill_names is None:
        names = active_skill_names()
    else:
        names = sorted(skill_names)

    if names:
        # Each alternative is a fully-quoted JSON string literal.
        # GBNF's ``"..."`` syntax inserts the literal characters,
        # including the surrounding quotes.
        alternatives = " | ".join(
            f'"\\"{_escape_gbnf_string_literal(n)}\\""' for n in names
        )
        skill_name_rule = f"skill-name ::= {alternatives}"
    else:
        skill_name_rule = "skill-name ::= string"

    response_specific = f"""
root ::= ws response-object ws

response-object ::= "{{" ws (response-pair (ws "," ws response-pair)*)? ws "}}"

response-pair ::= summary-pair
                | skill-requests-pair
                | hypotheses-pair

summary-pair        ::= "\\"summary\\"" ws ":" ws string
skill-requests-pair ::= "\\"skill_requests\\"" ws ":" ws skill-requests-array
hypotheses-pair     ::= "\\"hypotheses\\"" ws ":" ws hypotheses-array

skill-requests-array ::= "[" ws (skill-request-item (ws "," ws skill-request-item)*)? ws "]"

skill-request-item ::= "{{" ws skill-request-pair (ws "," ws skill-request-pair)* ws "}}"

skill-request-pair ::= ("\\"skill\\"" ws ":" ws skill-name)
                     | ("\\"params\\"" ws ":" ws object)

hypotheses-array ::= "[" ws (hypothesis-item (ws "," ws hypothesis-item)*)? ws "]"

hypothesis-item ::= object

{skill_name_rule}
""".strip()

    return response_specific + "\n\n" + _GBNF_JSON_PRELUDE


def is_grammar_enabled(config: Any) -> bool:
    """Return ``True`` iff the configured grammar-mode feature flag is on.

    Reads ``config.local_grammar_enabled`` defensively (falling back to
    ``True`` if the attribute is missing) so a ``MagicMock``-based
    test that pre-dates LCP.6 keeps the grammar path active by default.

    This helper also serves as the integration point for any future
    "kill switch" plumbing — operators can disable grammar enforcement
    via ``llm.local_grammar_enabled: false`` in ``global_config.yaml``
    or ``ANDROSCAN_LOCAL_GRAMMAR_ENABLED=false`` in the env if their
    runtime / quant pairing produces sampling failures (the
    opportunistic 400-fallback in the LLM client also handles
    runtimes that reject the new payload shape outright).
    """
    val = getattr(config, "local_grammar_enabled", True)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def json_schema_for_format_field(
    skill_names: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Convenience wrapper for the Ollama ``format`` payload.

    Identical to :func:`build_response_json_schema` today; kept as a
    separate name so a future divergence (e.g. Ollama-specific schema
    annotations like ``$defs`` indirection) doesn't ripple to other
    callers. The serialised JSON shape is what Ollama 0.5.0+ accepts
    in the ``format`` field of ``/api/chat``.
    """
    return build_response_json_schema(skill_names)


def json_schema_to_wire_str(schema: dict[str, Any]) -> str:
    """Serialise a JSON-schema dict to its on-the-wire form.

    Ollama accepts the ``format`` payload as either a dict (sent in
    the JSON body) or a serialised JSON string (some older clients
    re-encode it). We use the dict-in-body form, but expose this
    helper for diagnostic logging + the workbench Settings UI's
    "what gets sent" preview.
    """
    return json.dumps(schema, separators=(",", ":"), sort_keys=True)
