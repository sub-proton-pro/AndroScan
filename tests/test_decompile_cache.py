"""Tests for the persistent decompile cache (jadx is mocked)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from androscan.web import decompile_cache as dc


# ---------------------------------------------------------------------------
# Tree extraction (no jadx involved)


def _java(pkg: str, cls: str, methods: list[str]) -> str:
    body = "\n".join(
        f"    public void {m}() {{ /* body */ }}" for m in methods
    )
    return f"package {pkg};\n\npublic class {cls} {{\n{body}\n}}\n"


def test_build_tree_groups_by_package(tmp_path: Path) -> None:
    src = tmp_path / "sources"
    (src / "com" / "example" / "a").mkdir(parents=True)
    (src / "com" / "example" / "a" / "Foo.java").write_text(
        _java("com.example.a", "Foo", ["onCreate", "doStuff"]),
        encoding="utf-8",
    )
    (src / "com" / "example" / "a" / "Bar.java").write_text(
        _java("com.example.a", "Bar", ["run"]),
        encoding="utf-8",
    )
    tree, count = dc.build_tree(src)
    assert count == 2
    pkgs = {p["name"]: p for p in tree["packages"]}
    assert "com.example.a" in pkgs
    classes = {c["name"]: c for c in pkgs["com.example.a"]["classes"]}
    assert set(classes) == {"Foo", "Bar"}
    assert set(classes["Foo"]["methods"]) >= {"onCreate", "doStuff"}
    assert classes["Bar"]["methods"] == ["run"]


def test_build_tree_skips_non_source(tmp_path: Path) -> None:
    src = tmp_path / "sources"
    src.mkdir()
    (src / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")
    (src / "Foo.java").write_text(_java("p", "Foo", ["x"]), encoding="utf-8")
    tree, count = dc.build_tree(src)
    assert count == 1
    assert any(c["name"] == "Foo" for p in tree["packages"] for c in p["classes"])


def test_extract_classes_blacklists_keywords(tmp_path: Path) -> None:
    src = tmp_path / "Foo.java"
    src.write_text(
        "package p;\nclass Foo {\n  void real() { if (true) { } for (int i=0;i<1;i++) { } }\n}\n",
        encoding="utf-8",
    )
    classes = dc._extract_classes(src.read_text())
    assert classes[0]["name"] == "Foo"
    assert "if" not in classes[0]["methods"]
    assert "for" not in classes[0]["methods"]
    assert "real" in classes[0]["methods"]


# ---------------------------------------------------------------------------
# Status & lazy decompile (jadx + which mocked)


def _make_app(tmp_path: Path) -> tuple[Path, Path]:
    apps = tmp_path / "apps"
    app_dir = apps / "myapp"
    app_dir.mkdir(parents=True)
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK\x03\x04fake")
    sha = "deadbeef" * 8
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": sha, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )
    return app_dir, apk


def test_status_missing_when_no_app_meta(tmp_path: Path) -> None:
    app_dir = tmp_path / "apps" / "myapp"
    app_dir.mkdir(parents=True)
    s = dc.get_status(app_dir)
    assert s["status"] == "unknown"


def test_start_decompile_runs_and_writes_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir, apk = _make_app(tmp_path)

    def fake_run_jadx(jadx_cmd: str, apk_path: Path, out_dir: Path, timeout: int = 0) -> tuple[bool, str]:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "Foo.java").write_text(
            _java("p", "Foo", ["onCreate"]), encoding="utf-8"
        )
        return True, ""

    monkeypatch.setattr(dc, "_run_jadx_bulk", fake_run_jadx)
    monkeypatch.setattr(dc.shutil, "which", lambda c: "/usr/bin/" + c)

    result = dc.start_decompile(app_dir, blocking=True)
    assert result["status"] == "ready"
    assert result["file_count"] == 1
    tree = dc.load_tree(app_dir)
    assert tree is not None
    assert any(p["name"] == "p" for p in tree["packages"])


def test_start_decompile_records_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir, _ = _make_app(tmp_path)

    def fake_fail(*a, **kw):
        return False, "boom"

    monkeypatch.setattr(dc, "_run_jadx_bulk", fake_fail)
    monkeypatch.setattr(dc.shutil, "which", lambda c: "/usr/bin/" + c)

    result = dc.start_decompile(app_dir, blocking=True)
    assert result["status"] == "failed"
    assert "boom" in (result.get("error") or "")


def test_start_decompile_noop_when_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir, _ = _make_app(tmp_path)
    calls = {"n": 0}

    def fake_run_jadx(jadx_cmd: str, apk_path: Path, out_dir: Path, timeout: int = 0) -> tuple[bool, str]:
        calls["n"] += 1
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "F.java").write_text(_java("p", "F", ["m"]), encoding="utf-8")
        return True, ""

    monkeypatch.setattr(dc, "_run_jadx_bulk", fake_run_jadx)
    monkeypatch.setattr(dc.shutil, "which", lambda c: "/usr/bin/" + c)
    dc.start_decompile(app_dir, blocking=True)
    dc.start_decompile(app_dir, blocking=True)
    assert calls["n"] == 1


def test_start_decompile_errors_when_jadx_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir, _ = _make_app(tmp_path)
    monkeypatch.setattr(dc.shutil, "which", lambda c: None)
    result = dc.start_decompile(app_dir, blocking=True)
    assert result["status"] == "error"
    assert "jadx" in result["error"].lower()


def test_read_source_file_path_traversal_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir, _ = _make_app(tmp_path)

    def fake_run(cmd: str, p: Path, out: Path, timeout: int = 0) -> tuple[bool, str]:
        out.mkdir(parents=True, exist_ok=True)
        (out / "F.java").write_text("package p;\nclass F {}\n", encoding="utf-8")
        return True, ""

    monkeypatch.setattr(dc, "_run_jadx_bulk", fake_run)
    monkeypatch.setattr(dc.shutil, "which", lambda c: "/usr/bin/" + c)
    dc.start_decompile(app_dir, blocking=True)
    assert dc.read_source_file(app_dir, "../../etc/passwd") is None
    assert dc.read_source_file(app_dir, "F.java") is not None
