"""Tests for LLM: client (Ollama), prompt builder, response parser."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from androscan.llm import build_prompt, complete, parse_response


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


def test_complete_calls_ollama_and_returns_response():
    """complete() POSTs to Ollama /api/chat and returns response text. No live Ollama."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"message": {"role": "assistant", "content": '{"hypotheses": []}'}}
    with patch("androscan.llm.client.requests.post", return_value=mock_resp) as post_mock:
        out = complete("test prompt", config=MagicMock(ollama_base_url="http://localhost:11434", ollama_timeout_sec=60, ollama_model="llama2"))
    assert out == '{"hypotheses": []}'
    mock_resp.raise_for_status.assert_called_once()
    call_args = post_mock.call_args
    assert call_args[0][0].endswith("/api/chat")
    call_kw = call_args.kwargs
    assert call_kw["json"]["messages"] == [{"role": "user", "content": "test prompt"}]
    assert call_kw["json"]["stream"] is False


def test_complete_raises_on_connection_error():
    """complete() raises RuntimeError when Ollama is unreachable."""
    with patch("androscan.llm.client.requests.post", side_effect=requests.ConnectionError):
        with pytest.raises(RuntimeError, match="Cannot connect to Ollama"):
            complete("test", config=MagicMock(ollama_base_url="http://localhost:11434", ollama_timeout_sec=5, ollama_model="x"))


def test_complete_raises_on_timeout():
    """complete() raises RuntimeError on request timeout."""
    with patch("androscan.llm.client.requests.post", side_effect=requests.Timeout):
        with pytest.raises(RuntimeError, match="timed out"):
            complete("test", config=MagicMock(ollama_base_url="http://localhost:11434", ollama_timeout_sec=10, ollama_model="x"))


def test_complete_raises_friendly_message_on_404():
    """complete() raises RuntimeError with user-friendly message on 404, not raw HTTP."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    http_err = requests.HTTPError("404 Not Found")
    http_err.response = mock_resp
    mock_resp.raise_for_status.side_effect = http_err
    with patch("androscan.llm.client.requests.post", return_value=mock_resp):
        with pytest.raises(RuntimeError) as exc_info:
            complete("test", config=MagicMock(ollama_base_url="http://localhost:11434", ollama_timeout_sec=10, ollama_model="x"))
    msg = str(exc_info.value)
    assert "endpoint not found" in msg.lower() or "not found" in msg.lower()
    assert "ollama.com" in msg or "Ensure Ollama" in msg


def test_is_ollama_available_true_when_200():
    """is_ollama_available returns True when GET /api/tags returns 200."""
    from androscan.llm.client import is_ollama_available

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("androscan.llm.client.requests.get", return_value=mock_resp):
        assert is_ollama_available("http://localhost:11434") is True


def test_is_ollama_available_false_on_connection_error():
    """is_ollama_available returns False on connection error."""
    from androscan.llm.client import is_ollama_available

    with patch("androscan.llm.client.requests.get", side_effect=requests.ConnectionError):
        assert is_ollama_available("http://localhost:11434") is False
