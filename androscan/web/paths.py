"""Path helpers for apps/ run folders (path traversal safe)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from androscan.config import Config


def apps_root(config: Config, cwd: Optional[Path] = None) -> Path:
    """Resolved root directory for run output (e.g. repo/apps)."""
    base = cwd or Path.cwd()
    return (base / config.run_folder_root).resolve()


def safe_child(root: Path, *parts: str) -> Optional[Path]:
    """Return resolved path under root, or None if it escapes root."""
    root = root.resolve()
    if not parts:
        return root
    candidate = (root / Path(*parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
