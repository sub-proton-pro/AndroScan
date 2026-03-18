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
    """list_exploit_skills returns only tier=exploit (e.g. app_env_check, build_exploit_command)."""
    skills = list_exploit_skills()
    assert isinstance(skills, list)
    assert any(m.name == "app_env_check" for m in skills)
    assert any(m.name == "build_exploit_command" for m in skills)
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


# --- build_exploit_command ---

_MINIMAL_DOSSIER = {
    "apk_info": {"package": "com.example.app"},
    "exported_activities": [{"name": "com.example.app.SecretActivity", "exported": True, "intent_filters": []}],
    "exported_services": [],
    "exported_receivers": [],
    "exported_providers": [{"name": "com.example.app.Provider", "exported": True, "authority": "com.example.app.provider"}],
    "deep_links": [
        {"component": "com.example.app.MainActivity", "scheme": "https", "host": "example.com", "path_prefix": "/open", "intent_filter_index": 0}
    ],
}


def test_build_exploit_command_missing_hypothesis(tmp_path):
    """build_exploit_command returns failure when hypothesis is missing."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict=_MINIMAL_DOSSIER)
    result = execute("build_exploit_command", {"dossier_dict": _MINIMAL_DOSSIER, "vuln_module": "exported_components"}, ctx)
    assert result.success is False
    assert "[build_exploit_command]" in result.text
    assert "hypothesis" in result.text.lower()


def test_build_exploit_command_missing_dossier(tmp_path):
    """build_exploit_command returns failure when dossier_dict is missing."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict=None)
    result = execute(
        "build_exploit_command",
        {"hypothesis": {"evidence_refs": ["exported_activities[0]"]}, "vuln_module": "exported_components"},
        ctx,
    )
    assert result.success is False
    assert "dossier" in result.text.lower()


def test_build_exploit_command_missing_vuln_module(tmp_path):
    """build_exploit_command returns failure when vuln_module is missing."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict=_MINIMAL_DOSSIER)
    result = execute(
        "build_exploit_command",
        {"hypothesis": {"evidence_refs": ["exported_activities[0]"]}, "dossier_dict": _MINIMAL_DOSSIER},
        ctx,
    )
    assert result.success is False
    assert "vuln_module" in result.text.lower()


def test_build_exploit_command_empty_evidence_refs(tmp_path):
    """build_exploit_command returns failure when evidence_refs is empty."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict=_MINIMAL_DOSSIER)
    result = execute(
        "build_exploit_command",
        {"hypothesis": {"evidence_refs": []}, "dossier_dict": _MINIMAL_DOSSIER, "vuln_module": "exported_components"},
        ctx,
    )
    assert result.success is False
    assert "evidence_refs" in result.text


def test_build_exploit_command_activity_success(tmp_path):
    """build_exploit_command builds launch_activity command for exported_activities[0]."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict=_MINIMAL_DOSSIER)
    result = execute(
        "build_exploit_command",
        {
            "hypothesis": {"evidence_refs": ["exported_activities[0]"], "component_type": "activity"},
            "dossier_dict": _MINIMAL_DOSSIER,
            "vuln_module": "exported_components",
        },
        ctx,
    )
    assert result.success is True
    assert result.data is not None
    assert result.data["template_id"] == "launch_activity"
    assert result.data["profile"] == "exported_activity"
    assert "am start -n com.example.app/com.example.app.SecretActivity" == result.data["command"]
    assert result.log_summary is not None
    assert "Exploit command built" in result.log_summary
    assert "launch_activity" in result.log_summary
    assert result.spinner_text == "Building exploit command..."


def test_build_exploit_command_uses_context_dossier(tmp_path):
    """build_exploit_command uses context.dossier_dict when dossier_dict not in params."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict=_MINIMAL_DOSSIER)
    result = execute(
        "build_exploit_command",
        {"hypothesis": {"evidence_refs": ["exported_activities[0]"]}, "vuln_module": "exported_components"},
        ctx,
    )
    assert result.success is True
    assert result.data["command"] == "am start -n com.example.app/com.example.app.SecretActivity"


def test_build_exploit_command_provider_success(tmp_path):
    """build_exploit_command builds query_provider command for exported_providers[0]."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict=_MINIMAL_DOSSIER)
    result = execute(
        "build_exploit_command",
        {"hypothesis": {"evidence_refs": ["exported_providers[0]"]}, "dossier_dict": _MINIMAL_DOSSIER, "vuln_module": "exported_components"},
        ctx,
    )
    assert result.success is True
    assert result.data["template_id"] == "query_provider"
    assert "content read --uri content://com.example.app.provider/" in result.data["command"]


def test_build_exploit_command_deep_link_success(tmp_path):
    """build_exploit_command builds open_deep_link command for deep_links[0]."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path, dossier_dict=_MINIMAL_DOSSIER)
    result = execute(
        "build_exploit_command",
        {"hypothesis": {"evidence_refs": ["deep_links[0]"]}, "dossier_dict": _MINIMAL_DOSSIER, "vuln_module": "exported_components"},
        ctx,
    )
    assert result.success is True
    assert result.data["template_id"] == "open_deep_link"
    assert "android.intent.action.VIEW" in result.data["command"]
    assert "https://example.com/open" in result.data["command"]


# --- capture_signals ---

def test_capture_signals_requires_params(tmp_path):
    """capture_signals returns failure when required params are missing."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path)
    r = execute("capture_signals", {}, ctx)
    assert r.success is False
    assert "[capture_signals]" in r.text
    assert "device_serial" in r.text or "package" in r.text or "vuln_module" in r.text or "profile" in r.text


def test_capture_signals_no_adb(tmp_path, monkeypatch):
    """capture_signals returns clear failure when adb is not on PATH."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path)
    result = execute(
        "capture_signals",
        {"device_serial": "emulator-5554", "package": "com.example.app", "vuln_module": "exported_components", "profile": "exported_activity"},
        ctx,
    )
    assert result.success is False
    assert "adb" in result.text.lower()


def test_capture_signals_unknown_profile(tmp_path):
    """capture_signals returns failure for unknown profile."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path)
    result = execute(
        "capture_signals",
        {"device_serial": "emulator-5554", "package": "com.example.app", "vuln_module": "exported_components", "profile": "unknown_profile"},
        ctx,
    )
    assert result.success is False
    assert "Unknown profile" in result.text or "unknown_profile" in result.text


def test_capture_signals_success_volatile_then_non_volatile(tmp_path, monkeypatch):
    """capture_signals runs volatile first then non-volatile; returns log_summary and spinner_text."""
    def fake_run(cmd, *args, capture_output=True, text=True, timeout=15, **kwargs):
        class R:
            returncode = 0
            stdout = "fake logcat or dumpsys output\n"
            stderr = ""
        if "exec-out" in cmd or "screencap" in str(cmd):
            class RBinary:
                returncode = 0
                stdout = b"\x89PNG\r\n\x1a\n"
                stderr = b""
            return RBinary()
        return R()
    monkeypatch.setattr("subprocess.run", fake_run)
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path)
    result = execute(
        "capture_signals",
        {"device_serial": "emulator-5554", "package": "com.example.app", "vuln_module": "exported_components", "profile": "exported_activity"},
        ctx,
    )
    assert result.success is True
    assert result.data is not None
    assert "signals" in result.data
    assert "volatile_captured" in result.data
    assert "non_volatile_captured" in result.data
    assert result.data["volatile_captured"] == ["screenshot"]
    assert "logcat" in result.data["non_volatile_captured"] or "logcat" in result.data["signals"]
    assert result.log_summary is not None
    assert "Captured" in result.log_summary
    assert result.spinner_text == "Capturing signals..."


def test_capture_signals_list_exploit_includes_capture_signals():
    """list_exploit_skills includes capture_signals."""
    skills = list_exploit_skills()
    names = [m.name for m in skills]
    assert "capture_signals" in names


# --- run_exploit_command ---

def test_run_exploit_command_requires_params(tmp_path):
    """run_exploit_command returns failure when device_serial or command missing."""
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path)
    r = execute("run_exploit_command", {}, ctx)
    assert r.success is False
    assert "[run_exploit_command]" in r.text
    r2 = execute("run_exploit_command", {"device_serial": "emulator-5554"}, ctx)
    assert r2.success is False
    assert "command" in r2.text.lower()


def test_run_exploit_command_no_adb(tmp_path, monkeypatch):
    """run_exploit_command returns clear failure when adb not on PATH."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path)
    result = execute(
        "run_exploit_command",
        {"device_serial": "emulator-5554", "command": "am start -n pkg/.Main"},
        ctx,
    )
    assert result.success is False
    assert "adb" in result.text.lower()


def test_run_exploit_command_success(tmp_path, monkeypatch):
    """run_exploit_command runs adb shell and returns success, stdout, stderr."""
    def fake_run(cmd, *args, capture_output=True, text=True, timeout=30, **kwargs):
        class R:
            returncode = 0
            stdout = "Starting: Intent { pkg=pkg }\n"
            stderr = ""
        return R()
    monkeypatch.setattr("subprocess.run", fake_run)
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path)
    result = execute(
        "run_exploit_command",
        {"device_serial": "emulator-5554", "command": "am start -n com.example.app/.SecretActivity"},
        ctx,
    )
    assert result.success is True
    assert result.data["success"] is True
    assert result.data["returncode"] == 0
    assert "Starting" in result.data["stdout"]
    assert result.log_summary is not None
    assert "Ran exploit command" in result.log_summary
    assert result.spinner_text == "Running exploit command..."


def test_run_exploit_command_exit_nonzero(tmp_path, monkeypatch):
    """run_exploit_command returns success=False when shell command exits non-zero."""
    def fake_run(cmd, *args, capture_output=True, text=True, timeout=30, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = "Error: Activity not found"
        return R()
    monkeypatch.setattr("subprocess.run", fake_run)
    config = Config.default()
    ctx = SkillContext(config=config, run_folder=tmp_path)
    result = execute(
        "run_exploit_command",
        {"device_serial": "emulator-5554", "command": "am start -n bad/.Missing"},
        ctx,
    )
    assert result.success is False
    assert result.data["success"] is False
    assert result.data["returncode"] == 1
    assert "Activity not found" in result.data["stderr"]
