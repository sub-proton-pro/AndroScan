"""Smoke test: package imports."""

import pytest


def test_import_androscan():
    """androscan package can be imported."""
    import androscan  # noqa: F401
    assert androscan is not None
