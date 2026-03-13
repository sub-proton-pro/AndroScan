"""Tests for extraction and dossier."""

from pathlib import Path

import pytest

from androscan.extraction import extract_dossier
from androscan.extraction.manifest_parser import build_dossier_dict_from_parsed, parse_manifest_xml
from androscan.internal import app_id_from_dossier
from androscan.internal.dossier import Dossier

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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


def test_parse_manifest_xml_fixture():
    """Parsing fixture AndroidManifest.xml yields package, permissions, exported activity and provider."""
    manifest_path = FIXTURES_DIR / "AndroidManifest.xml"
    if not manifest_path.exists():
        pytest.skip("fixtures/AndroidManifest.xml not found")
    parsed = parse_manifest_xml(manifest_path)
    assert parsed.get("stub") is not True
    assert parsed.get("package") == "com.fixture.testapp"
    assert "android.permission.INTERNET" in (parsed.get("permissions") or [])
    activities = parsed.get("activities") or []
    assert len(activities) >= 1
    assert any(".MainActivity" in a.get("name", "") for a in activities)
    providers = parsed.get("providers") or []
    assert any("FileProvider" in p.get("name", "") for p in providers)


def test_build_dossier_dict_from_parsed_fixture():
    """Build dossier dict from parsed fixture manifest; assert shape and exported components."""
    manifest_path = FIXTURES_DIR / "AndroidManifest.xml"
    if not manifest_path.exists():
        pytest.skip("fixtures/AndroidManifest.xml not found")
    parsed = parse_manifest_xml(manifest_path)
    dossier_dict = build_dossier_dict_from_parsed(parsed)
    assert dossier_dict
    assert dossier_dict.get("apk_info", {}).get("package") == "com.fixture.testapp"
    assert len(dossier_dict.get("permissions") or []) >= 1
    assert len(dossier_dict.get("exported_activities") or []) >= 1
    assert len(dossier_dict.get("exported_providers") or []) >= 1
    dossier = Dossier.from_dict(dossier_dict)
    assert app_id_from_dossier(dossier) == "com_fixture_testapp"
