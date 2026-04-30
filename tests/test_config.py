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
    # Phase 11 sub-step 11.6 / DEC-025 — bumped from 8192 → 12288 to
    # absorb the v2 inter-procedural slicer's ~1.5x response payload
    # growth.
    assert cfg.ollama_num_predict == 12288
    # Phase 11 sub-step 11.6 / DEC-025 — Ollama context window default
    # bumped above the Ollama-side 8192 default to absorb v2's ~2x
    # input prompt growth.
    assert cfg.ollama_num_ctx == 16384
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


# ---------------------------------------------------------------------------
# Phase 11 sub-step 11.6 / DEC-025 — ollama.num_ctx + trace.max_slice_depth
# ---------------------------------------------------------------------------


class TestOllamaNumCtx:
    """``ollama.num_ctx`` (Ollama context-window size) — new in 11.6."""

    def test_default_is_16384(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert load_config().ollama_num_ctx == 16384

    def test_parses_from_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_path = tmp_path / "global_config.yaml"
        cfg_path.write_text(
            yaml.safe_dump({"ollama": {"num_ctx": 32768}}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        assert load_config().ollama_num_ctx == 32768

    def test_env_override_wins_over_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_path = tmp_path / "global_config.yaml"
        cfg_path.write_text(
            yaml.safe_dump({"ollama": {"num_ctx": 8192}}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANDROSCAN_OLLAMA_NUM_CTX", "65536")
        assert load_config().ollama_num_ctx == 65536

    def test_env_invalid_falls_back_to_yaml(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cfg_path = tmp_path / "global_config.yaml"
        cfg_path.write_text(
            yaml.safe_dump({"ollama": {"num_ctx": 24576}}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANDROSCAN_OLLAMA_NUM_CTX", "not-a-number")
        cfg = load_config()
        assert cfg.ollama_num_ctx == 24576
        assert "ANDROSCAN_OLLAMA_NUM_CTX" in capsys.readouterr().err

    def test_field_is_live_reloadable(self) -> None:
        from androscan.config.loader import LIVE_RELOADABLE_FIELDS
        assert "ollama_num_ctx" in LIVE_RELOADABLE_FIELDS

    def test_field_is_in_field_map(self) -> None:
        from androscan.config.loader import CONFIG_FIELD_MAP
        section, key, env = CONFIG_FIELD_MAP["ollama_num_ctx"]
        assert (section, key, env) == ("ollama", "num_ctx", "ANDROSCAN_OLLAMA_NUM_CTX")


class TestTraceMaxSliceDepth:
    """``trace.max_slice_depth`` (bounded inter-procedural slicer
    descent depth) — new in 11.6."""

    def test_default_is_two(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert load_config().trace_max_slice_depth == 2

    def test_parses_from_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_path = tmp_path / "global_config.yaml"
        cfg_path.write_text(
            yaml.safe_dump({"trace": {"max_slice_depth": 3}}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        assert load_config().trace_max_slice_depth == 3

    def test_env_override_wins_over_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_path = tmp_path / "global_config.yaml"
        cfg_path.write_text(
            yaml.safe_dump({"trace": {"max_slice_depth": 1}}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANDROSCAN_TRACE_MAX_SLICE_DEPTH", "4")
        assert load_config().trace_max_slice_depth == 4

    def test_env_invalid_falls_back_to_yaml(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cfg_path = tmp_path / "global_config.yaml"
        cfg_path.write_text(
            yaml.safe_dump({"trace": {"max_slice_depth": 3}}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANDROSCAN_TRACE_MAX_SLICE_DEPTH", "abc")
        cfg = load_config()
        assert cfg.trace_max_slice_depth == 3
        assert "ANDROSCAN_TRACE_MAX_SLICE_DEPTH" in capsys.readouterr().err

    def test_zero_is_accepted_disables_descent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``trace.max_slice_depth: 0`` is a valid setting — the slicer's
        ``_DescentBudget.fresh(max_depth=0)`` produces a no-descent
        budget. Operators who want to lock the slicer to v1 semantics
        can do so this way without ripping out the kwargs at every
        call site."""
        cfg_path = tmp_path / "global_config.yaml"
        cfg_path.write_text(
            yaml.safe_dump({"trace": {"max_slice_depth": 0}}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        assert load_config().trace_max_slice_depth == 0

    def test_field_is_live_reloadable(self) -> None:
        from androscan.config.loader import LIVE_RELOADABLE_FIELDS
        assert "trace_max_slice_depth" in LIVE_RELOADABLE_FIELDS

    def test_field_is_in_field_map(self) -> None:
        from androscan.config.loader import CONFIG_FIELD_MAP
        section, key, env = CONFIG_FIELD_MAP["trace_max_slice_depth"]
        assert (section, key, env) == ("trace", "max_slice_depth", "ANDROSCAN_TRACE_MAX_SLICE_DEPTH")

    def test_slicer_budget_factory_clamps_to_hard_cap(self) -> None:
        """Even if YAML / env supplies a value above ``HARD_CAP_DEPTH``,
        ``_DescentBudget.fresh`` clamps it. Defensive: no Config path
        should be able to push descent past the in-code hard ceiling."""
        from androscan.analysis import slicing
        budget = slicing._DescentBudget.fresh(max_depth=999)
        assert budget.remaining_depth == slicing.HARD_CAP_DEPTH
