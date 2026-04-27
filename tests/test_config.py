"""Tests for config loading."""

from pathlib import Path

import pytest
import yaml

from androscan.config import Config, load_config


def test_default_config_has_expected_attributes():
    """Default config exposes ollama_base_url, ollama_timeout_sec, run_folder_root."""
    cfg = Config.default()
    assert hasattr(cfg, "ollama_base_url")
    assert hasattr(cfg, "ollama_timeout_sec")
    assert hasattr(cfg, "run_folder_root")
    assert cfg.ollama_base_url == "http://localhost:11434"
    assert cfg.ollama_timeout_sec == 150
    assert cfg.ollama_temperature == 0.2
    assert cfg.ollama_num_predict == 8192
    assert cfg.run_folder_root == "apps"
    assert cfg.web_host == "127.0.0.1"
    assert cfg.web_port == 8420
    assert cfg.web_screencap_interval_ms == 500
    # Hook Lab Frida adapter (DEC-023): default ring buffer cap.
    assert cfg.frida_trace_ring_buffer_size == 5000


def test_load_config_uses_env(monkeypatch):
    """load_config reads ANDROSCAN_OLLAMA_URL and ANDROSCAN_RUN_FOLDER."""
    monkeypatch.setenv("ANDROSCAN_OLLAMA_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("ANDROSCAN_RUN_FOLDER", "out")
    monkeypatch.setenv("ANDROSCAN_WEB_PORT", "9000")
    cfg = load_config()
    assert cfg.ollama_base_url == "http://127.0.0.1:8080"
    assert cfg.run_folder_root == "out"
    assert cfg.web_port == 9000


# ---------------------------------------------------------------------------
# Hook Lab: frida.trace_ring_buffer_size (DEC-023)


def test_frida_ring_buffer_default_when_yaml_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.frida_trace_ring_buffer_size == 5000


def test_frida_ring_buffer_parses_from_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = tmp_path / "global_config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"frida": {"trace_ring_buffer_size": 8000}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.frida_trace_ring_buffer_size == 8000


def test_frida_ring_buffer_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = tmp_path / "global_config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"frida": {"trace_ring_buffer_size": 8000}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANDROSCAN_FRIDA_TRACE_RING", "12000")
    cfg = load_config()
    # Env wins over YAML.
    assert cfg.frida_trace_ring_buffer_size == 12000


def test_frida_ring_buffer_env_invalid_falls_back_to_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    cfg_path = tmp_path / "global_config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"frida": {"trace_ring_buffer_size": 7777}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANDROSCAN_FRIDA_TRACE_RING", "not-a-number")
    cfg = load_config()
    # Bad env is logged + ignored; YAML still applies.
    assert cfg.frida_trace_ring_buffer_size == 7777
    err = capsys.readouterr().err
    assert "ANDROSCAN_FRIDA_TRACE_RING" in err


def test_frida_ring_buffer_clamp_min(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Values below 100 are clamped because a wraparound deque silently
    drops every event the moment it loops, which is a very unfun thing
    to debug in a Hook Lab session."""
    cfg_path = tmp_path / "global_config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"frida": {"trace_ring_buffer_size": 1}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.frida_trace_ring_buffer_size >= 100
