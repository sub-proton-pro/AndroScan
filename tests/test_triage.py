"""Tests for triage upsert + safe path handling."""

from __future__ import annotations

import json
from pathlib import Path

from androscan.web.triage import load_triage, upsert_triage


def _mkrun(tmp_path: Path) -> Path:
    run = tmp_path / "apps" / "myapp" / "01-jan-26_12-00-00"
    run.mkdir(parents=True)
    return tmp_path / "apps"


def test_upsert_creates_file_with_entry(tmp_path: Path) -> None:
    root = _mkrun(tmp_path)
    ok, err, entry = upsert_triage(
        root, "myapp", "01-jan-26_12-00-00", "finding-001",
        status="false_positive", note="resource id is internal-only",
    )
    assert ok, err
    assert entry["status"] == "false_positive"
    assert entry["note"].startswith("resource id")
    on_disk = json.loads((root / "myapp" / "01-jan-26_12-00-00" / "triage.json").read_text())
    assert on_disk["finding-001"]["status"] == "false_positive"


def test_upsert_merges_partial_updates(tmp_path: Path) -> None:
    root = _mkrun(tmp_path)
    upsert_triage(root, "myapp", "01-jan-26_12-00-00", "f1", status="needs_review")
    ok, _, entry = upsert_triage(
        root, "myapp", "01-jan-26_12-00-00", "f1", severity_override="low"
    )
    assert ok
    assert entry["status"] == "needs_review"
    assert entry["severity_override"] == "low"


def test_upsert_rejects_invalid_status(tmp_path: Path) -> None:
    root = _mkrun(tmp_path)
    ok, err, _ = upsert_triage(root, "myapp", "01-jan-26_12-00-00", "f1", status="lol")
    assert not ok
    assert "status" in err


def test_upsert_rejects_invalid_severity(tmp_path: Path) -> None:
    root = _mkrun(tmp_path)
    ok, err, _ = upsert_triage(root, "myapp", "01-jan-26_12-00-00", "f1", severity_override="severe")
    assert not ok
    assert "severity_override" in err


def test_upsert_unknown_run(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    ok, err, _ = upsert_triage(tmp_path / "apps", "ghost", "00", "f1", status="confirmed")
    assert not ok
    assert "unknown run" in err.lower()


def test_load_triage_corrupt_returns_empty(tmp_path: Path) -> None:
    root = _mkrun(tmp_path)
    (root / "myapp" / "01-jan-26_12-00-00" / "triage.json").write_text("{not json")
    assert load_triage(root, "myapp", "01-jan-26_12-00-00") == {}


def test_path_traversal_blocked(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    ok, err, _ = upsert_triage(
        tmp_path / "apps", "../../etc", "passwd", "f1", status="confirmed"
    )
    assert not ok
    assert "unknown run" in err.lower()
