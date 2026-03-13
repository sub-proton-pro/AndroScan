"""Tests for LLM stub: prompt builder and response parser."""

import pytest

from androscan.llm import build_prompt, parse_response


def test_build_prompt_contains_dossier():
    """Prompt builder returns a string containing dossier data."""
    dossier = {"apk_info": {"package": "com.test.app"}, "permissions": []}
    prompt = build_prompt(dossier)
    assert "com.test.app" in prompt
    assert "Dossier" in prompt or "dossier" in prompt


def test_parse_response_valid_hypotheses():
    """Parser correctly parses valid JSON with hypotheses."""
    raw = '{"hypotheses": [{"id": "H1", "component_type": "activity", "component_name": "com.a.B", "title": "T", "description": "D", "evidence_refs": ["exported_activities[0]"], "exploitability": 4, "confidence": 3, "remediation_hint": "Fix"}]}'
    resp = parse_response(raw)
    assert len(resp.hypotheses) == 1
    assert resp.hypotheses[0].id == "H1"
    assert resp.hypotheses[0].exploitability == 4
    assert resp.hypotheses[0].evidence_refs == ["exported_activities[0]"]


def test_parse_response_valid_skill_requests():
    """Parser correctly parses valid JSON with skill_requests."""
    raw = '{"skill_requests": [{"skill": "get_decompiled_class", "params": {"component_ref": "exported_activities[0]"}}]}'
    resp = parse_response(raw)
    assert len(resp.skill_requests) == 1
    assert resp.skill_requests[0].skill == "get_decompiled_class"
    assert resp.skill_requests[0].params.get("component_ref") == "exported_activities[0]"


def test_parse_response_invalid_json():
    """Parser handles invalid JSON without crashing; returns empty response."""
    resp = parse_response("not json at all")
    assert resp.hypotheses == []
    assert resp.skill_requests == []
