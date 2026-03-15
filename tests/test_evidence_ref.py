"""Tests for evidence_ref validation and resolution."""

import pytest

from androscan.internal.evidence_ref import resolve_ref, validate_ref


def test_validate_ref_valid_exported_activities():
    """Valid ref exported_activities[0] in dossier with one activity."""
    dossier = {"exported_activities": [{"name": "com.example.Main"}]}
    assert validate_ref(dossier, "exported_activities[0]") is True


def test_validate_ref_invalid_index():
    """Ref with out-of-range index is invalid."""
    dossier = {"exported_activities": [{"name": "com.example.Main"}]}
    assert validate_ref(dossier, "exported_activities[1]") is False
    assert validate_ref(dossier, "exported_activities[99]") is False


def test_validate_ref_unknown_key():
    """Ref with unknown list key is invalid."""
    dossier = {"exported_activities": []}
    assert validate_ref(dossier, "unknown_key[0]") is False


def test_validate_ref_malformed():
    """Malformed refs are invalid."""
    dossier = {"exported_activities": [{}]}
    assert validate_ref(dossier, "") is False
    assert validate_ref(dossier, "no_bracket") is False
    assert validate_ref(dossier, "exported_activities[") is False
    assert validate_ref(dossier, "exported_activities[abc]") is False


def test_validate_ref_strips_whitespace():
    """Refs with leading/trailing whitespace are accepted after normalization."""
    dossier = {"exported_activities": [{"name": "com.example.Main"}]}
    assert validate_ref(dossier, "  exported_activities[0]  ") is True
    assert validate_ref(dossier, "exported_activities[0]\n") is True


def test_resolve_ref_returns_valid_path_unchanged():
    """resolve_ref returns a valid dossier path as-is."""
    dossier = {"exported_activities": [{"name": "com.example.Main"}]}
    assert resolve_ref(dossier, "exported_activities[0]") == "exported_activities[0]"
    assert resolve_ref(dossier, "  exported_activities[0]  ") == "exported_activities[0]"


def test_resolve_ref_resolves_component_name_to_path():
    """resolve_ref resolves short or full component name to dossier path."""
    dossier = {
        "exported_activities": [{"name": "com.example.weakbank.SecretActivity"}],
        "exported_receivers": [{"name": "androidx.profileinstaller.ProfileInstallReceiver"}],
    }
    assert resolve_ref(dossier, "SecretActivity") == "exported_activities[0]"
    assert resolve_ref(dossier, "ProfileInstallReceiver") == "exported_receivers[0]"
    assert resolve_ref(dossier, "com.example.weakbank.SecretActivity") == "exported_activities[0]"
    assert resolve_ref(dossier, "nonexistent") is None
