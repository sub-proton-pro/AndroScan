"""Tests for extraction stub and dossier."""

import pytest

from androscan.extraction import extract_dossier
from androscan.internal import app_id_from_dossier


def test_extract_dossier_returns_expected_shape():
    """extract_dossier returns a dossier with expected keys and types."""
    dossier = extract_dossier("/any/path.apk")
    assert dossier.apk_info.package == "com.example.app"
    assert isinstance(dossier.permissions, list)
    assert len(dossier.exported_activities) == 1
    assert dossier.exported_activities[0].name == "com.example.app.MainActivity"
    assert dossier.exported_services == []
    assert dossier.exported_receivers == []
    assert dossier.exported_providers == []
    assert dossier.deep_links == []


def test_app_id_from_dossier():
    """app_id from stub dossier is sanitized package (com_example_app)."""
    dossier = extract_dossier("/dummy.apk")
    assert app_id_from_dossier(dossier) == "com_example_app"


def test_extract_dossier_rejects_empty_path():
    """extract_dossier raises on empty path."""
    with pytest.raises(ValueError, match="non-empty"):
        extract_dossier("")
