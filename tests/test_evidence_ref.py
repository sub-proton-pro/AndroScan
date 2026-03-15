"""Tests for evidence_ref validation."""

import pytest

from androscan.internal.evidence_ref import validate_ref


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
