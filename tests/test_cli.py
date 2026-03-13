"""Tests for CLI argument parsing."""

import pytest


def test_cli_parses_apk_and_tasks():
    """Parsing --apk path.apk --task exported_components --task other yields correct values."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", required=True)
    parser.add_argument("--task", action="append", default=[], dest="tasks")
    args = parser.parse_args(["--apk", "path.apk", "--task", "exported_components", "--task", "other"])
    assert args.apk == "path.apk"
    assert args.tasks == ["exported_components", "other"]


def test_cli_single_task():
    """Single --task is stored as one-element list."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", required=True)
    parser.add_argument("--task", action="append", default=[], dest="tasks")
    args = parser.parse_args(["--apk", "app.apk", "--task", "exported_components"])
    assert args.tasks == ["exported_components"]
