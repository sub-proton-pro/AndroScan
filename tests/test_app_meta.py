"""Tests for app_meta (APK hash, load/save app_meta.json)."""

from pathlib import Path

import pytest

from androscan.internal.app_meta import (
    APP_META_FILENAME,
    compute_apk_sha256,
    extracted_apk_path,
    load_app_meta,
    save_app_meta,
)


def test_compute_apk_sha256(tmp_path):
    """compute_apk_sha256 returns hex digest of file content."""
    f = tmp_path / "dummy.apk"
    f.write_bytes(b"content")
    h = compute_apk_sha256(f)
    assert isinstance(h, str)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
    assert compute_apk_sha256(f) == compute_apk_sha256(str(f))  # path or str


def test_load_app_meta_missing_returns_none(tmp_path):
    """load_app_meta returns None when file does not exist."""
    assert load_app_meta(tmp_path) is None


def test_save_and_load_app_meta(tmp_path):
    """save_app_meta creates file; load_app_meta returns apk_sha256 and dossier."""
    save_app_meta(tmp_path, "abc123", {"apk_info": {"package": "com.example.app"}}, apk_path="/foo.apk")
    path = tmp_path / APP_META_FILENAME
    assert path.exists()
    meta = load_app_meta(tmp_path)
    assert meta is not None
    assert meta["apk_sha256"] == "abc123"
    assert meta["dossier"]["apk_info"]["package"] == "com.example.app"
    assert meta["apk_path"] == "/foo.apk"


def test_load_app_meta_invalid_or_incomplete_returns_none(tmp_path):
    """load_app_meta returns None when apk_sha256 or dossier missing."""
    path = tmp_path / APP_META_FILENAME
    path.write_text("{}", encoding="utf-8")
    assert load_app_meta(tmp_path) is None
    path.write_text('{"apk_sha256": "x"}', encoding="utf-8")
    assert load_app_meta(tmp_path) is None


def test_extracted_apk_path(tmp_path):
    """extracted_apk_path returns app_id_root/extracted_apk."""
    p = extracted_apk_path(tmp_path)
    assert p == tmp_path / "extracted_apk"
