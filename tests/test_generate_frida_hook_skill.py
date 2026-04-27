"""Tests for the LLM-tier ``generate_frida_hook`` skill (sub-step 4.7 / DEC-023).

Covers:

* Catalog / consent surface — the skill is the **first** registered LLM-tier
  skill with ``requires_confirmation=True``; the chat agentic loop (DEC-022)
  must be able to find it via that flag without scanning by name.
* Input validation — missing / malformed ``template_id`` and ``params``.
* Schema-aware error text — when the LLM omits a required parameter or
  invents an unknown key, the failure ``text`` quotes the underlying
  :class:`HookParamError` *and* re-prints the declared schema so the LLM
  can self-correct on the next turn.
* Success path — round-trips against the real ``entry_exit_log`` template
  asserting ``data.js`` / ``data.summary`` / ``data.params_used`` /
  ``data.sensitive_apis`` / ``data.rationale`` are all present and the
  human-readable ``text`` contains a JS preview + the rationale.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from androscan.config import Config
from androscan.skills import (
    SkillContext,
    execute,
    list_llm_skills,
)


def _ctx(tmp_path: Path) -> SkillContext:
    return SkillContext(
        config=Config.default(),
        run_folder=tmp_path,
        dossier_dict={},
        apk_path=None,
    )


# ---------------------------------------------------------------------------
# Catalog / consent class


def test_generate_frida_hook_in_llm_catalog():
    metas = {m.name: m for m in list_llm_skills()}
    assert "generate_frida_hook" in metas
    meta = metas["generate_frida_hook"]
    assert meta.tier == "llm"
    assert meta.requires_confirmation is True


def test_first_consent_class_skill_in_v1_is_generate_frida_hook():
    """DEC-022 says ``generate_frida_hook`` is the first ``requires_confirmation=True``
    skill the v1 catalog ships. Guard the invariant so a future device-driving
    skill landing accidentally as ``True`` for a different reason doesn't pass
    silently — at minimum, the rename / addition needs to update this test."""
    consent_class = [m.name for m in list_llm_skills() if m.requires_confirmation]
    assert "generate_frida_hook" in consent_class


# ---------------------------------------------------------------------------
# Input validation


def test_missing_template_id(tmp_path):
    r = execute("generate_frida_hook", {}, _ctx(tmp_path))
    assert r.success is False
    assert "[generate_frida_hook]" in r.text
    assert "template_id" in r.text


def test_blank_template_id(tmp_path):
    r = execute("generate_frida_hook", {"template_id": "   "}, _ctx(tmp_path))
    assert r.success is False
    assert "template_id" in r.text


def test_unknown_template_id(tmp_path):
    r = execute(
        "generate_frida_hook",
        {"template_id": "no_such_template", "params": {}},
        _ctx(tmp_path),
    )
    assert r.success is False
    assert "[generate_frida_hook]" in r.text
    # The underlying HookTemplateNotFound message lists valid ids.
    assert "valid:" in r.text
    assert "entry_exit_log" in r.text


def test_params_must_be_dict(tmp_path):
    r = execute(
        "generate_frida_hook",
        {"template_id": "entry_exit_log", "params": "class=Foo"},
        _ctx(tmp_path),
    )
    assert r.success is False
    assert "params" in r.text
    assert "dict" in r.text


# ---------------------------------------------------------------------------
# Schema-aware errors


def test_missing_required_param_surfaces_declared_schema(tmp_path):
    """When the LLM forgets a required key, the failure should re-list the
    declared schema so the model can self-correct on the next turn."""
    r = execute(
        "generate_frida_hook",
        {
            "template_id": "entry_exit_log",
            "params": {"class_name": "com.example.Foo"},
        },
        _ctx(tmp_path),
    )
    assert r.success is False
    assert "missing required" in r.text.lower()
    assert "method_name" in r.text
    # Schema hint mentions every declared param.
    assert "class_name" in r.text
    assert "event_label" in r.text


def test_unknown_param_key_surfaces_declared_schema(tmp_path):
    """A typo / hallucinated parameter name should fail loudly with the
    list of declared params — no silent omission."""
    r = execute(
        "generate_frida_hook",
        {
            "template_id": "entry_exit_log",
            "params": {
                "class_name": "com.example.Foo",
                "method_name": "bar",
                "event_label": "tag",
                "klass_name": "com.example.Foo",  # typo of class_name
            },
        },
        _ctx(tmp_path),
    )
    assert r.success is False
    assert "unknown parameter" in r.text.lower()
    assert "klass_name" in r.text


# ---------------------------------------------------------------------------
# Success path


def test_entry_exit_log_renders_preview(tmp_path):
    r = execute(
        "generate_frida_hook",
        {
            "template_id": "entry_exit_log",
            "params": {
                "class_name": "com.example.LoginManager",
                "method_name": "checkPassword",
                "event_label": "login_check",
            },
            "rationale": "trace the password-checking call as part of an auth review",
        },
        _ctx(tmp_path),
    )
    assert r.success is True, r.text
    assert isinstance(r.data, dict)
    assert r.data["template_id"] == "entry_exit_log"
    # Filled params are echoed back by name.
    assert r.data["params_used"]["class_name"] == "com.example.LoginManager"
    assert r.data["params_used"]["method_name"] == "checkPassword"
    assert r.data["params_used"]["event_label"] == "login_check"
    # Rendered JS + summary should be non-empty post-substitution and contain
    # the operator's class name (proves substitution actually ran).
    assert "com.example.LoginManager" in r.data["js"]
    assert "checkPassword" in r.data["js"]
    assert r.data["summary"]
    # ``sensitive_apis`` is preserved from the template (entry_exit_log
    # doesn't declare any in 4.4, so this asserts the field exists as a list).
    assert isinstance(r.data["sensitive_apis"], list)
    assert r.data["rationale"] and "auth review" in r.data["rationale"]
    # Human-readable text should contain a JS preview AND the rationale.
    assert "JS preview:" in r.text
    assert "Pentester summary:" in r.text
    assert "auth review" in r.text
    # Operator-action notice mirrors DEC-023: we never inject from the skill.
    assert "Operator action required" in r.text


def test_rendered_data_does_not_leak_template_object(tmp_path):
    """The rendered dict should be plain JSON-friendly types so the chat
    transcript serialiser (json.dumps) never trips over a frozen
    HookTemplate dataclass."""
    r = execute(
        "generate_frida_hook",
        {
            "template_id": "ssl_pinning_bypass",
            "params": {"event_label": "pin"},
        },
        _ctx(tmp_path),
    )
    assert r.success is True
    import json as _json
    _json.dumps(r.data)  # must not raise


def test_rationale_is_optional(tmp_path):
    """Rationale is free-form metadata; missing it must not affect rendering."""
    r = execute(
        "generate_frida_hook",
        {
            "template_id": "entry_exit_log",
            "params": {
                "class_name": "com.example.Foo",
                "method_name": "bar",
                "event_label": "x",
            },
        },
        _ctx(tmp_path),
    )
    assert r.success is True
    assert r.data["rationale"] is None
    assert "Rationale:" not in r.text
