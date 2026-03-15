"""Integration test: workflow with mock LLM."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from androscan.extraction import extract_dossier
from androscan.llm.client import CompleteResult
from androscan.internal.workflow import run_workflow


def test_workflow_creates_report_file(tmp_path):
    """Run workflow with mock LLM (no live Ollama); run folder contains report.json with expected structure."""
    stub_json = '{"summary": "Stub analysis.", "hypotheses": [{"id": "H1", "component_type": "activity", "component_name": "com.example.app.MainActivity", "title": "Stub finding", "description": "Stub.", "evidence_refs": ["exported_activities[0]"], "exploitability": 3, "confidence": 2, "remediation_hint": "N/A"}]}'
    stub_result = CompleteResult(content=stub_json, thinking="", metadata={})
    with patch("androscan.internal.workflow.complete", return_value=stub_result):
        run_workflow("/dummy.apk", ["exported_components"], tmp_path)
    report_file = tmp_path / "report.json"
    assert report_file.exists()
    data = json.loads(report_file.read_text())
    assert "hypotheses" in data
    assert isinstance(data["hypotheses"], list)
    # Stub LLM returns one hypothesis
    assert len(data["hypotheses"]) >= 1
    h = data["hypotheses"][0]
    assert "id" in h and "evidence_refs" in h and "exploitability" in h


def test_workflow_multi_turn_with_mock_skill_request(tmp_path):
    """When mock LLM returns skill_requests then hypotheses, workflow runs two turns and writes report."""
    call_count = 0

    def mock_complete(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            content = '{"skill_requests": [{"skill": "get_decompiled_class", "params": {"component_ref": "exported_activities[0]"}}]}'
        else:
            content = '{"hypotheses": [{"id": "H2", "component_type": "activity", "component_name": "com.example.app.MainActivity", "title": "Mock", "description": "D", "evidence_refs": ["exported_activities[0]"], "exploitability": 2, "confidence": 3, "remediation_hint": ""}]}'
        return CompleteResult(content=content, thinking="", metadata={})

    with patch("androscan.internal.workflow.complete", side_effect=mock_complete):
        run_workflow("/dummy.apk", ["exported_components"], tmp_path)

    assert call_count == 2
    report_file = tmp_path / "report.json"
    assert report_file.exists()
    data = json.loads(report_file.read_text())
    assert len(data["hypotheses"]) == 1
    assert data["hypotheses"][0]["id"] == "H2"


def test_workflow_drops_hypothesis_with_invalid_evidence_ref(tmp_path):
    """Hypotheses with invalid evidence_ref are dropped; only valid refs appear in report."""
    stub_json = '{"summary": "Ok.", "hypotheses": ['
    stub_json += '{"id": "H1", "component_type": "activity", "component_name": "com.example.Main", "title": "Valid", "description": "D", "evidence_refs": ["exported_activities[0]"], "exploitability": 3, "confidence": 2, "remediation_hint": ""},'
    stub_json += '{"id": "H2", "component_type": "activity", "component_name": "com.example.Ghost", "title": "Invalid ref", "description": "D", "evidence_refs": ["exported_activities[99]"], "exploitability": 4, "confidence": 1, "remediation_hint": ""}'
    stub_json += ']}'
    stub_result = CompleteResult(content=stub_json, thinking="", metadata={})
    with patch("androscan.internal.workflow.complete", return_value=stub_result):
        run_workflow("/dummy.apk", ["exported_components"], tmp_path)
    data = json.loads((tmp_path / "report.json").read_text())
    assert len(data["hypotheses"]) == 1
    assert data["hypotheses"][0]["id"] == "H1"
