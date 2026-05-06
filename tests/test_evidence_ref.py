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


def test_resolve_ref_strips_kotlin_java_extensions():
    """resolve_ref accepts component names with .kt/.java/.smali/.dex/.xml suffixes
    (Gemma 4 thinking-mode models sometimes attach them during consolidation)."""
    dossier = {
        "exported_activities": [{"name": "com.example.weakbank.SecretActivity"}],
        "exported_providers": [{"name": "com.example.weakbank.WeakBankContentProvider"}],
    }
    # Each supported extension on a known component
    assert resolve_ref(dossier, "SecretActivity.kt") == "exported_activities[0]"
    assert resolve_ref(dossier, "SecretActivity.java") == "exported_activities[0]"
    assert resolve_ref(dossier, "SecretActivity.smali") == "exported_activities[0]"
    # Provider name with extension (the actual Gemma 4 failure case)
    assert resolve_ref(dossier, "WeakBankContentProvider.kt") == "exported_providers[0]"
    # Fully qualified name with extension
    assert resolve_ref(dossier, "com.example.weakbank.SecretActivity.kt") == "exported_activities[0]"
    # Case-insensitive on the extension
    assert resolve_ref(dossier, "SecretActivity.KT") == "exported_activities[0]"
    # Helper class name with extension is still rejected (not a component)
    assert resolve_ref(dossier, "WeakBankLab.kt") is None
    # Free-form prose is still rejected
    assert resolve_ref(dossier, "classes3.dex") is None


def test_resolve_ref_does_not_strip_extension_from_dossier_paths():
    """A valid dossier path that happens to contain a dot is unaffected."""
    dossier = {"exported_activities": [{"name": "com.example.Main"}]}
    # The valid-path fast path returns before the extension-strip logic is reached.
    assert resolve_ref(dossier, "exported_activities[0]") == "exported_activities[0]"


def test_resolve_ref_does_not_swallow_short_strings():
    """A bare extension-only string (e.g. ".kt") must not be treated as resolvable."""
    dossier = {"exported_activities": [{"name": "com.example.Main"}]}
    assert resolve_ref(dossier, ".kt") is None
    assert resolve_ref(dossier, ".java") is None


def test_lookup_component_meta_returns_type_and_name():
    """lookup_component_meta turns a canonical ref into (component_type, component_name)."""
    from androscan.internal.evidence_ref import lookup_component_meta

    dossier = {
        "exported_activities": [{"name": "com.example.MainActivity"}],
        "exported_services": [{"name": "com.example.PaymentService"}],
        "exported_receivers": [{"name": "com.example.ResetReceiver"}],
        "exported_providers": [{"name": "com.example.AppProvider"}],
        "deep_links": [{"component": "com.example.RouterActivity"}],
    }
    assert lookup_component_meta(dossier, "exported_activities[0]") == ("activity", "com.example.MainActivity")
    assert lookup_component_meta(dossier, "exported_services[0]") == ("service", "com.example.PaymentService")
    assert lookup_component_meta(dossier, "exported_receivers[0]") == ("receiver", "com.example.ResetReceiver")
    assert lookup_component_meta(dossier, "exported_providers[0]") == ("content_provider", "com.example.AppProvider")
    assert lookup_component_meta(dossier, "deep_links[0]") == ("deep_link", "com.example.RouterActivity")


def test_lookup_component_meta_rejects_invalid_refs():
    """Non-canonical or out-of-range refs return None instead of raising."""
    from androscan.internal.evidence_ref import lookup_component_meta

    dossier = {"exported_activities": [{"name": "com.example.Main"}]}
    assert lookup_component_meta(dossier, "exported_activities[99]") is None
    assert lookup_component_meta(dossier, "WeakBankLab.kt") is None
    assert lookup_component_meta(dossier, "") is None
    assert lookup_component_meta(dossier, "exported_activities[0]") == ("activity", "com.example.Main")
