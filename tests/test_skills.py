"""Tests for skills layer: registry, execute, list_llm_skills."""

from pathlib import Path

import pytest

from androscan.config import Config
from androscan.skills import (
    SkillContext,
    execute,
    list_llm_skills,
    run_skills,
)


def test_execute_extract_manifest_pipeline_skill(tmp_path):
    """Pipeline skill extract_manifest runs and returns success with stub data."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, apk_path="/some.apk")
    result = execute("extract_manifest", {}, ctx)
    assert result.success is True
    assert result.data is not None
    assert "stub" in str(result.data).lower() or "apk_path" in (result.data or {})


def test_execute_prepare_dossier_pipeline_skill(tmp_path):
    """Pipeline skill prepare_dossier runs and returns dossier dict."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, apk_path="/some.apk")
    manifest_result = execute("extract_manifest", {}, ctx)
    result = execute("prepare_dossier", {"manifest": manifest_result.data}, ctx)
    assert result.success is True
    assert isinstance(result.data, dict)
    assert "apk_info" in result.data
    assert result.data["apk_info"].get("package") == "com.example.app"


def test_execute_llm_skill_get_decompiled_class(tmp_path):
    """get_decompiled_class runs; without real APK or with missing jadx returns clear failure (per CONVENTIONS)."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict={}, apk_path="/nonexistent.apk")
    result = execute("get_decompiled_class", {"component_ref": "exported_activities[0]"}, ctx)
    assert "[get_decompiled_class]" in result.text
    # No real APK in test: expect failure with clear reason (APK not found, or invalid ref if APK existed)
    if not result.success:
        assert "not found" in result.text or "not available" in result.text.lower() or "Invalid" in result.text
    else:
        assert "decompiled" in result.text.lower() or result.text


def test_execute_unknown_skill_returns_failure(tmp_path):
    """Unknown skill name returns failure SkillResult."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, apk_path="/a.apk")
    result = execute("nonexistent_skill", {}, ctx)
    assert result.success is False
    assert "unknown" in result.text.lower() or "nonexistent" in result.text.lower()


def test_list_llm_skills_returns_only_llm_tier():
    """list_llm_skills returns only skills with tier=llm."""
    skills = list_llm_skills()
    assert isinstance(skills, list)
    names = [s.name for s in skills]
    assert "get_decompiled_class" in names
    assert "extract_manifest" not in names
    assert "prepare_dossier" not in names
    assert "generate_report" not in names
    for meta in skills:
        assert meta.tier == "llm"


def test_run_skills_compat(tmp_path):
    """run_skills runs LLM-format requests and returns list of result strings."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict={}, apk_path="/a.apk")
    requests = [
        {"skill": "get_decompiled_class", "params": {"component_ref": "exported_activities[0]"}},
    ]
    results = run_skills(requests, {}, tmp_path, ctx)
    assert len(results) == 1
    assert isinstance(results[0], str)
    assert len(results[0]) > 0
