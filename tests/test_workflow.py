"""Integration test: workflow with mock LLM."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from androscan.config import Config
from androscan.extraction import extract_dossier
from androscan.llm.client import CompleteResult
from androscan.internal.run_log import RunLogger
from androscan.internal.workflow import run_workflow


def _single_shot_config() -> Config:
    """Config with per_component_analysis=False so workflow uses single-shot path (for tests that expect fixed complete() count)."""
    c = Config.default()
    return c  # default has per_component_analysis=False


def test_workflow_creates_report_file(tmp_path):
    """Run workflow with mock LLM (no live Ollama); run folder contains report.json with expected structure."""
    stub_json = '{"summary": "Stub analysis.", "hypotheses": [{"id": "H1", "component_type": "activity", "component_name": "com.example.app.MainActivity", "title": "Stub finding", "description": "Stub.", "evidence_refs": ["exported_activities[0]"], "exploitability": 3, "confidence": 2, "remediation_hint": "N/A"}]}'
    stub_result = CompleteResult(content=stub_json, thinking="", metadata={})
    config = _single_shot_config()
    with patch("androscan.internal.workflow.complete", return_value=stub_result):
        run_workflow("/dummy.apk", ["exported_components"], tmp_path, config=config)
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

    config = _single_shot_config()
    with patch("androscan.internal.workflow.complete", side_effect=mock_complete):
        run_workflow("/dummy.apk", ["exported_components"], tmp_path, config=config)

    assert call_count == 2
    report_file = tmp_path / "report.json"
    assert report_file.exists()
    data = json.loads(report_file.read_text())
    assert len(data["hypotheses"]) == 1
    assert data["hypotheses"][0]["id"] == "H2"


def test_workflow_drops_hypothesis_with_invalid_evidence_ref(tmp_path):
    """Hypotheses with invalid evidence_ref are dropped; only valid refs appear in report; [WARNING] in run.log."""
    stub_json = '{"summary": "Ok.", "hypotheses": ['
    stub_json += '{"id": "H1", "component_type": "activity", "component_name": "com.example.Main", "title": "Valid", "description": "D", "evidence_refs": ["exported_activities[0]"], "exploitability": 3, "confidence": 2, "remediation_hint": ""},'
    stub_json += '{"id": "H2", "component_type": "activity", "component_name": "com.example.Ghost", "title": "Invalid ref", "description": "D", "evidence_refs": ["exported_activities[99]"], "exploitability": 4, "confidence": 1, "remediation_hint": ""}'
    stub_json += ']}'
    stub_result = CompleteResult(content=stub_json, thinking="", metadata={})
    run_logger = RunLogger(tmp_path)
    config = _single_shot_config()
    with patch("androscan.internal.workflow.complete", return_value=stub_result):
        run_workflow("/dummy.apk", ["exported_components"], tmp_path, config=config, run_logger=run_logger)
    data = json.loads((tmp_path / "report.json").read_text())
    assert len(data["hypotheses"]) == 1
    assert data["hypotheses"][0]["id"] == "H1"
    assert "[WARNING]" in (tmp_path / "run.log").read_text()


def test_workflow_writes_run_meta_and_run_log(tmp_path):
    """Workflow writes report.json, run_meta.json, and run.log when run_logger is provided."""
    stub_json = '{"summary": "Ok.", "hypotheses": [{"id": "H1", "component_type": "activity", "component_name": "com.example.Main", "title": "T", "description": "D", "evidence_refs": ["exported_activities[0]"], "exploitability": 3, "confidence": 2, "remediation_hint": ""}]}'
    run_logger = RunLogger(tmp_path)
    config = _single_shot_config()
    with patch("androscan.internal.workflow.complete", return_value=CompleteResult(content=stub_json, thinking="", metadata={})):
        run_workflow("/dummy.apk", ["exported_components"], tmp_path, config=config, run_logger=run_logger)
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "run_meta.json").exists()
    assert (tmp_path / "run.log").exists()
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert meta.get("apk_path") == "/dummy.apk"
    assert "run_timestamp" in meta
    assert meta.get("hypotheses_count") == 1
    log_content = (tmp_path / "run.log").read_text()
    assert "[task]" in log_content
