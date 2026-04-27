"""Unit tests for the per-app settings module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from androscan.web import per_app_settings as pas


def test_load_returns_defaults_when_missing(tmp_path: Path) -> None:
    out = pas.load_app_settings(tmp_path)
    assert out["schema_version"] == pas.SCHEMA_VERSION
    assert out["rag"] == {}
    assert out["tags"] == []


def test_load_handles_corrupt(tmp_path: Path) -> None:
    pas.app_settings_path(tmp_path).write_text("not json", encoding="utf-8")
    out = pas.load_app_settings(tmp_path)
    assert out == pas.default_app_settings()


def test_save_atomic_persists(tmp_path: Path) -> None:
    data = pas.default_app_settings()
    data["rag"]["embed_provider"] = "ollama"
    data["tags"] = ["alpha", "beta"]
    pas.save_app_settings(tmp_path, data)
    p = pas.app_settings_path(tmp_path)
    assert p.is_file()
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["rag"]["embed_provider"] == "ollama"
    assert on_disk["tags"] == ["alpha", "beta"]


def test_save_normalises_bad_section_types(tmp_path: Path) -> None:
    weird = {"rag": None, "tags": ["", "  ", "x"], "notes": 123}
    persisted = pas.save_app_settings(tmp_path, weird)
    assert persisted["rag"] == {}
    assert persisted["tags"] == ["x"]
    # notes was an int → it gets stringified-via-coerce path? Actually no:
    # save_app_settings preserves whatever it gets as long as it survives
    # the type cleanup. It explicitly resets notes to "" if not str.
    assert persisted["notes"] == ""


def test_reset_app_settings(tmp_path: Path) -> None:
    pas.save_app_settings(tmp_path, {"rag": {"embed_provider": "ollama"}})
    cleared = pas.reset_app_settings(tmp_path)
    assert cleared["rag"] == {}


def test_effective_settings_inherits(tmp_path: Path) -> None:
    gv = {"rag": {"embed_provider": "fastembed", "embed_model": ""}}
    eff = pas.effective_settings(global_view=gv, per_app=pas.default_app_settings())
    assert eff["rag"]["embed_provider"]["value"] == "fastembed"
    assert eff["rag"]["embed_provider"]["source"] == "global"


def test_effective_settings_app_override() -> None:
    gv = {"rag": {"embed_provider": "fastembed"}}
    per_app = pas.default_app_settings()
    per_app["rag"]["embed_provider"] = "ollama"
    eff = pas.effective_settings(global_view=gv, per_app=per_app)
    assert eff["rag"]["embed_provider"]["value"] == "ollama"
    assert eff["rag"]["embed_provider"]["source"] == "app"


def test_effective_settings_inherit_keyword_clears_override() -> None:
    gv = {"rag": {"embed_provider": "fastembed"}}
    per_app = pas.default_app_settings()
    per_app["rag"]["embed_provider"] = "  inherit  "
    eff = pas.effective_settings(global_view=gv, per_app=per_app)
    assert eff["rag"]["embed_provider"]["source"] == "global"


def test_overrides_for_runtime_subset() -> None:
    per_app = pas.default_app_settings()
    per_app["rag"]["embed_provider"] = "ollama"
    per_app["rag"]["top_k_default"] = "12"
    per_app["chat"]["model_override"] = "qwen2:7b"
    out = pas.overrides_for_runtime(per_app)
    assert out["rag_embed_provider"] == "ollama"
    assert out["rag_top_k_default"] == 12
    assert out["ollama_model"] == "qwen2:7b"


def test_merge_for_app_combines_global_and_overrides() -> None:
    glob = {"rag_embed_provider": "fastembed", "rag_top_k_default": 8, "ollama_model": "qwen3.5:35b"}
    per_app = pas.default_app_settings()
    per_app["rag"]["embed_provider"] = "ollama"
    merged = pas.merge_for_app(glob, per_app)
    assert merged["rag_embed_provider"] == "ollama"
    assert merged["rag_top_k_default"] == 8


def test_apk_overrides_summary_lists_active_keys() -> None:
    per_app = pas.default_app_settings()
    per_app["rag"]["embed_provider"] = "ollama"
    per_app["chat"]["model_override"] = "qwen2:7b"
    per_app["tags"] = ["banking"]
    lines = pas.apk_overrides_summary(per_app)
    assert any("rag.embed_provider" in line for line in lines)
    assert any("chat.model" in line for line in lines)
    assert any("tags" in line for line in lines)


def test_coerce_partial_update_unknown_key_rejected() -> None:
    base = pas.default_app_settings()
    new, err = pas.coerce_partial_update(base, {"oops": 1})
    assert err is not None
    assert "unknown" in err


def test_coerce_partial_update_section_type_check() -> None:
    base = pas.default_app_settings()
    _, err = pas.coerce_partial_update(base, {"rag": "not a dict"})
    assert err is not None


def test_coerce_partial_update_merges_into_existing_section() -> None:
    base = pas.default_app_settings()
    base["rag"]["embed_provider"] = "ollama"
    new, err = pas.coerce_partial_update(base, {"rag": {"embed_model": "nomic-embed-text"}})
    assert err is None
    assert new["rag"]["embed_provider"] == "ollama"  # preserved
    assert new["rag"]["embed_model"] == "nomic-embed-text"


def test_coerce_partial_update_notes_too_long() -> None:
    base = pas.default_app_settings()
    _, err = pas.coerce_partial_update(base, {"notes": "x" * 9000})
    assert err is not None
    assert "too long" in err


def test_coerce_partial_update_null_clears_section() -> None:
    base = pas.default_app_settings()
    base["rag"]["embed_provider"] = "ollama"
    new, err = pas.coerce_partial_update(base, {"rag": None})
    assert err is None
    assert new["rag"] == {}


# ---------------------------------------------------------------------------
# Hook section (sub-step 4.5)
#
# The Hook Lab adds a small section gating the Inject route's allowlist.
# Defaults need to be stable (operators reading the file should see an
# empty section, not invented globals) and partial-update validation
# must be strict on types so a typo can't silently allow a wider
# package prefix than intended.


def test_default_app_settings_has_empty_hook_section() -> None:
    out = pas.default_app_settings()
    assert out["hook"] == {}


def test_known_top_level_keys_includes_hook() -> None:
    assert "hook" in pas._KNOWN_TOP_LEVEL_KEYS


def test_load_normalises_null_hook_section(tmp_path: Path) -> None:
    pas.app_settings_path(tmp_path).write_text(
        json.dumps({"hook": None}), encoding="utf-8",
    )
    out = pas.load_app_settings(tmp_path)
    assert out["hook"] == {}


def test_save_persists_hook_section(tmp_path: Path) -> None:
    data = pas.default_app_settings()
    data["hook"]["hook_target_package_prefix"] = "com.example"
    data["hook"]["auto_attach_on_session_start"] = True
    pas.save_app_settings(tmp_path, data)
    on_disk = json.loads(pas.app_settings_path(tmp_path).read_text(encoding="utf-8"))
    assert on_disk["hook"]["hook_target_package_prefix"] == "com.example"
    assert on_disk["hook"]["auto_attach_on_session_start"] is True


def test_effective_settings_hook_falls_back_to_app_package() -> None:
    """Without a per-app override the prefix should default to the app's
    own manifest package id (passed in via the new ``app_package`` kwarg)."""
    gv: dict = {}
    per_app = pas.default_app_settings()
    eff = pas.effective_settings(global_view=gv, per_app=per_app, app_package="com.example.target")
    assert eff["hook"]["hook_target_package_prefix"]["value"] == "com.example.target"
    assert eff["hook"]["hook_target_package_prefix"]["source"] == "default"
    assert eff["hook"]["auto_attach_on_session_start"]["value"] is False
    assert eff["hook"]["auto_attach_on_session_start"]["source"] == "default"


def test_effective_settings_hook_no_app_package_yields_none() -> None:
    """When the route layer can't resolve a package (e.g. a half-imported
    app with no ``app_meta.json``), the prefix surfaces as ``None`` so
    the route's 403 path triggers."""
    gv: dict = {}
    per_app = pas.default_app_settings()
    eff = pas.effective_settings(global_view=gv, per_app=per_app)
    assert eff["hook"]["hook_target_package_prefix"]["value"] is None


def test_effective_settings_hook_per_app_override_wins() -> None:
    gv: dict = {}
    per_app = pas.default_app_settings()
    per_app["hook"]["hook_target_package_prefix"] = "com.widened"
    per_app["hook"]["auto_attach_on_session_start"] = True
    eff = pas.effective_settings(global_view=gv, per_app=per_app, app_package="com.example")
    assert eff["hook"]["hook_target_package_prefix"]["value"] == "com.widened"
    assert eff["hook"]["hook_target_package_prefix"]["source"] == "app"
    assert eff["hook"]["auto_attach_on_session_start"]["value"] is True
    assert eff["hook"]["auto_attach_on_session_start"]["source"] == "app"


def test_apk_overrides_summary_picks_up_hook_overrides() -> None:
    per_app = pas.default_app_settings()
    per_app["hook"]["hook_target_package_prefix"] = "com.widened"
    per_app["hook"]["auto_attach_on_session_start"] = True
    lines = pas.apk_overrides_summary(per_app)
    assert any("hook.target_prefix" in line for line in lines)
    assert any("auto_attach_on_session_start" in line for line in lines)


def test_apk_overrides_summary_skips_default_auto_attach() -> None:
    """``auto_attach=False`` is the default — it shouldn't pollute the summary."""
    per_app = pas.default_app_settings()
    per_app["hook"]["hook_target_package_prefix"] = "com.widened"
    per_app["hook"]["auto_attach_on_session_start"] = False
    lines = pas.apk_overrides_summary(per_app)
    assert not any("auto_attach_on_session_start" in line for line in lines)


def test_coerce_partial_update_hook_prefix_must_be_string() -> None:
    base = pas.default_app_settings()
    _, err = pas.coerce_partial_update(base, {"hook": {"hook_target_package_prefix": 42}})
    assert err is not None
    assert "hook_target_package_prefix" in err


def test_coerce_partial_update_hook_prefix_blank_rejected() -> None:
    base = pas.default_app_settings()
    _, err = pas.coerce_partial_update(base, {"hook": {"hook_target_package_prefix": "   "}})
    assert err is not None
    assert "non-empty" in err


def test_coerce_partial_update_hook_prefix_null_clears_override() -> None:
    base = pas.default_app_settings()
    base["hook"]["hook_target_package_prefix"] = "com.example"
    new, err = pas.coerce_partial_update(base, {"hook": {"hook_target_package_prefix": None}})
    assert err is None
    # After merge, the key is preserved as None — the effective merger
    # treats None as "no override" and falls back to the app's package.
    assert new["hook"]["hook_target_package_prefix"] is None


def test_coerce_partial_update_hook_auto_attach_must_be_bool() -> None:
    base = pas.default_app_settings()
    _, err = pas.coerce_partial_update(base, {"hook": {"auto_attach_on_session_start": "yes"}})
    assert err is not None
    assert "auto_attach_on_session_start" in err


def test_coerce_partial_update_hook_prefix_too_long() -> None:
    base = pas.default_app_settings()
    _, err = pas.coerce_partial_update(base, {"hook": {"hook_target_package_prefix": "x" * 300}})
    assert err is not None
    assert "too long" in err
