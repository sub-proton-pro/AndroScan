"""Tests for skill results cache (lookup/store)."""

from pathlib import Path

import pytest

from androscan.internal.skill_results_cache import CACHE_FILENAME, lookup, store


def test_lookup_missing_returns_none(tmp_path):
    """Lookup when cache file does not exist returns None."""
    assert lookup(tmp_path, "com.example.app", "get_decompiled_class", {"class_name": "Foo"}) is None


def test_store_creates_file(tmp_path):
    """Store creates app_id/skill_results_cache.json with entry."""
    store(tmp_path, "com.example.app", "15-mar-26_21-22-57", "get_decompiled_class", {"class_name": "Foo"}, "decompiled content")
    path = tmp_path / "com.example.app" / CACHE_FILENAME
    assert path.exists()
    data = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert "by_key" in data
    assert data["next_serial"] == 1


def test_lookup_after_store_returns_entry(tmp_path):
    """After store, lookup returns run_folder and result_text."""
    store(tmp_path, "com.example.app", "15-mar-26_21-22-57", "get_decompiled_class", {"class_name": "Foo"}, "decompiled content")
    entry = lookup(tmp_path, "com.example.app", "get_decompiled_class", {"class_name": "Foo"})
    assert entry is not None
    assert entry["run_folder"] == "15-mar-26_21-22-57"
    assert entry["result_text"] == "decompiled content"


def test_lookup_different_params_miss(tmp_path):
    """Lookup with different params returns None."""
    store(tmp_path, "com.example.app", "15-mar-26_21-22-57", "get_decompiled_class", {"class_name": "Foo"}, "content")
    assert lookup(tmp_path, "com.example.app", "get_decompiled_class", {"class_name": "Bar"}) is None


def test_store_overwrites_same_key(tmp_path):
    """Store for same (skill, params) overwrites; next_serial still increments."""
    store(tmp_path, "com.example.app", "run1", "skill_a", {"x": 1}, "first")
    store(tmp_path, "com.example.app", "run2", "skill_a", {"x": 1}, "second")
    entry = lookup(tmp_path, "com.example.app", "skill_a", {"x": 1})
    assert entry is not None
    assert entry["result_text"] == "second"
    assert entry["run_folder"] == "run2"
