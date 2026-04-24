"""Tests for the --setup first-time installer."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

import pytest

from androscan.internal.first_run_setup import run_first_time_setup


def _make_repo(tmp_path: Path, *, with_lock: bool = True, with_frontend: bool = True) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    if with_frontend:
        fe = tmp_path / "androscan" / "web" / "frontend"
        fe.mkdir(parents=True)
        (fe / "package.json").write_text("{}", encoding="utf-8")
        if with_lock:
            (fe / "package-lock.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_setup_runs_pip_then_npm_ci_then_build(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, with_lock=True)
    calls: list[tuple[list[str], Path]] = []

    def runner(cmd: Sequence[str], cwd: Path) -> int:
        calls.append((list(cmd), cwd))
        return 0

    rc = run_first_time_setup(repo, runner=runner, npm_path="/usr/local/bin/npm")
    assert rc == 0
    assert len(calls) == 3
    pip_cmd, pip_cwd = calls[0]
    assert pip_cmd[:3] == [sys.executable, "-m", "pip"]
    assert "install" in pip_cmd and "-e" in pip_cmd and ".[dev,rag]" in pip_cmd
    assert pip_cwd == repo.resolve()

    install_cmd, install_cwd = calls[1]
    assert install_cmd == ["/usr/local/bin/npm", "ci"]
    assert install_cwd == (repo / "androscan" / "web" / "frontend").resolve()

    build_cmd, build_cwd = calls[2]
    assert build_cmd == ["/usr/local/bin/npm", "run", "build"]
    assert build_cwd == install_cwd


def test_setup_uses_npm_install_without_lockfile(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, with_lock=False)
    calls: list[list[str]] = []

    def runner(cmd: Sequence[str], _cwd: Path) -> int:
        calls.append(list(cmd))
        return 0

    rc = run_first_time_setup(repo, runner=runner, npm_path="npm")
    assert rc == 0
    assert calls[1] == ["npm", "install"]
    assert calls[2] == ["npm", "run", "build"]


def test_setup_pip_failure_aborts_before_npm(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    calls: list[list[str]] = []

    def runner(cmd: Sequence[str], _cwd: Path) -> int:
        calls.append(list(cmd))
        return 1 if cmd[1:3] == ["-m", "pip"] else 0

    rc = run_first_time_setup(repo, runner=runner, npm_path="npm")
    assert rc == 1
    assert len(calls) == 1


def test_setup_missing_pyproject(tmp_path: Path) -> None:
    rc = run_first_time_setup(tmp_path, runner=lambda *_: 0, npm_path="npm")
    assert rc == 1


def test_setup_skips_npm_when_no_frontend(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, with_frontend=False)
    calls: list[list[str]] = []

    def runner(cmd: Sequence[str], _cwd: Path) -> int:
        calls.append(list(cmd))
        return 0

    rc = run_first_time_setup(repo, runner=runner, npm_path="npm")
    assert rc == 0
    assert len(calls) == 1


def test_setup_no_npm_returns_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo(tmp_path)
    calls: list[list[str]] = []

    def runner(cmd: Sequence[str], _cwd: Path) -> int:
        calls.append(list(cmd))
        return 0

    monkeypatch.setattr("androscan.internal.first_run_setup.shutil.which", lambda _x: None)
    rc = run_first_time_setup(repo, runner=runner)
    assert rc == 1
    assert len(calls) == 1
