"""Integration test: workflow with mock LLM."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from androscan.extraction import extract_dossier
from androscan.internal.workflow import run_workflow


def test_workflow_creates_report_file(tmp_path):
    """Run workflow with stub (no live Ollama); run folder contains report.json with expected structure."""
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
            return '{"skill_requests": [{"skill": "get_decompiled_class", "params": {"component_ref": "exported_activities[0]"}}]}'
        return '{"hypotheses": [{"id": "H2", "component_type": "activity", "component_name": "com.example.app.MainActivity", "title": "Mock", "description": "D", "evidence_refs": ["exported_activities[0]"], "exploitability": 2, "confidence": 3, "remediation_hint": ""}]}'

    with patch("androscan.internal.workflow.complete", side_effect=mock_complete):
        run_workflow("/dummy.apk", ["exported_components"], tmp_path)

    assert call_count == 2
    report_file = tmp_path / "report.json"
    assert report_file.exists()
    data = json.loads(report_file.read_text())
    assert len(data["hypotheses"]) == 1
    assert data["hypotheses"][0]["id"] == "H2"
