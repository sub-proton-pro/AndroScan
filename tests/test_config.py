"""Tests for config loading."""

import os

import pytest

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


def test_load_config_uses_env(monkeypatch):
    """load_config reads ANDROSCAN_OLLAMA_URL and ANDROSCAN_RUN_FOLDER."""
    monkeypatch.setenv("ANDROSCAN_OLLAMA_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("ANDROSCAN_RUN_FOLDER", "out")
    cfg = load_config()
    assert cfg.ollama_base_url == "http://127.0.0.1:8080"
    assert cfg.run_folder_root == "out"
