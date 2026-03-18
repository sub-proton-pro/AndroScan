"""Tests for skills layer: registry, execute, list_llm_skills."""

from pathlib import Path

import pytest

from androscan.config import Config
from androscan.skills import (
    SkillContext,
    SkillResult,
    execute,
    list_exploit_skills,
    list_llm_skills,
    list_skills_by_tier,
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


def test_execute_get_decompiled_class_accepts_class_name(tmp_path):
    """get_decompiled_class accepts full class name when component_ref is not a dossier path."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict={}, apk_path="/nonexistent.apk")
    result = execute("get_decompiled_class", {"component_ref": "com.example.weakbank.WeakBankLab"}, ctx)
    assert "[get_decompiled_class]" in result.text
    # Should not be "Invalid or unresolved" (we accepted the class name); failure is APK not found or jadx
    assert "Invalid or unresolved" not in result.text
    assert not result.success
    assert "not found" in result.text or "not available" in result.text.lower()


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


def test_list_skills_by_tier():
    """list_skills_by_tier returns only skills with the given tier."""
    llm = list_skills_by_tier("llm")
    assert all(m.tier == "llm" for m in llm)
    pipeline = list_skills_by_tier("pipeline")
    assert all(m.tier == "pipeline" for m in pipeline)
    exploit = list_skills_by_tier("exploit")
    assert all(m.tier == "exploit" for m in exploit)


def test_list_exploit_skills():
    """list_exploit_skills returns only tier=exploit (e.g. app_env_check)."""
    skills = list_exploit_skills()
    assert isinstance(skills, list)
    assert any(m.name == "app_env_check" for m in skills)
    for meta in skills:
        assert meta.tier == "exploit"


def test_run_skills_compat(tmp_path):
    """run_skills runs LLM-format requests and returns list of (skill_name, SkillResult)."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict={}, apk_path="/a.apk")
    requests = [
        {"skill": "get_decompiled_class", "params": {"component_ref": "exported_activities[0]"}},
    ]
    results = run_skills(requests, {}, tmp_path, ctx)
    assert len(results) == 1
    name, res = results[0]
    assert name == "get_decompiled_class"
    assert isinstance(res, SkillResult)
    assert len(res.text) > 0


def test_execute_get_decompiled_method_missing_params(tmp_path):
    """get_decompiled_method requires class_name and method_name; returns clear failure when missing."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, apk_path="/a.apk")
    r1 = execute("get_decompiled_method", {}, ctx)
    assert r1.success is False
    assert "[get_decompiled_method]" in r1.text
    assert "class_name" in r1.text
    r2 = execute("get_decompiled_method", {"class_name": "com.example.Foo"}, ctx)
    assert r2.success is False
    assert "method_name" in r2.text


def test_execute_get_decompiled_method_no_apk_or_jadx(tmp_path):
    """get_decompiled_method fails with clear message when APK missing or jadx not available."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, apk_path="/nonexistent.apk")
    result = execute(
        "get_decompiled_method",
        {"class_name": "com.example.Main", "method_name": "onCreate"},
        ctx,
    )
    assert "[get_decompiled_method]" in result.text
    assert result.success is False
    assert "not found" in result.text or "not available" in result.text.lower()


def test_app_env_check_requires_package(tmp_path):
    """app_env_check returns failure when package is missing."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, apk_path="/a.apk")
    result = execute("app_env_check", {}, ctx)
    assert result.success is False
    assert "[app_env_check]" in result.text
    assert "package" in result.text.lower()


def test_app_env_check_no_adb(tmp_path, monkeypatch):
    """app_env_check returns clear failure when adb is not on PATH."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, apk_path="/a.apk")
    result = execute("app_env_check", {"package": "com.example.app"}, ctx)
    assert result.success is False
    assert "[app_env_check]" in result.text
    assert "adb" in result.text.lower()


def test_app_env_check_no_devices(tmp_path, monkeypatch):
    """app_env_check returns failure with device list when no devices attached."""
    def fake_run(cmd, *args, **kwargs):
        class R:
            returncode = 0
            stdout = "List of devices attached\n\n"
            stderr = ""
        return R()
    monkeypatch.setattr("subprocess.run", fake_run)
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, apk_path="/a.apk")
    result = execute("app_env_check", {"package": "com.example.app"}, ctx)
    assert result.success is False
    assert "[app_env_check]" in result.text
    assert result.data is not None
    assert result.data.get("devices") == []
    assert result.data.get("reason") == "no_devices"


def test_extract_method_bodies():
    """_extract_method_bodies extracts method signature + body from Java-like source."""
    from androscan.skills.get_decompiled_method import _extract_method_bodies

    source = """
public class Main {
    public void onCreate(Bundle b) {
        setContentView(R.layout.main);
        return;
    }
    private void helper() { }
}
"""
    body = _extract_method_bodies(source, "onCreate")
    assert "onCreate" in body
    assert "setContentView" in body
    assert "return;" in body
    body2 = _extract_method_bodies(source, "helper")
    assert "helper" in body2
    assert _extract_method_bodies(source, "nonexistent") == ""
