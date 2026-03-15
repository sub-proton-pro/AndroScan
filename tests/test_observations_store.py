"""Tests for persistent observations store."""

import json
from pathlib import Path

import pytest

from androscan.internal.observations_store import (
    OBSERVATIONS_FILENAME,
    append_observations,
    load_observations,
)


def test_load_observations_missing_returns_empty(tmp_path):
    """Missing file returns empty list."""
    assert load_observations(tmp_path, "com_example_app") == []


def test_append_observations_creates_file(tmp_path):
    """Append creates app_id/observations.json with entries."""
    append_observations(tmp_path, "com_example_app", [{"run_ts": "12-mar-26_10-00-00", "source": "run", "text": "Done."}])
    path = tmp_path / "com_example_app" / OBSERVATIONS_FILENAME
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["observations"] == [{"run_ts": "12-mar-26_10-00-00", "source": "run", "text": "Done."}]


def test_append_observations_appends_to_existing(tmp_path):
    """Append adds to existing observations."""
    append_observations(tmp_path, "com_example_app", [{"source": "run", "text": "First."}])
    append_observations(tmp_path, "com_example_app", [{"source": "run", "text": "Second."}])
    obs = load_observations(tmp_path, "com_example_app")
    assert len(obs) == 2
    assert obs[0]["text"] == "First."
    assert obs[1]["text"] == "Second."
