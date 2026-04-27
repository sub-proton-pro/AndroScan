"""Defensive ``apktool d`` wrapper that produces Smali for the call graph.

Mirrors the shape of :func:`androscan.web.decompile_cache._run_jadx_bulk`
so reviewers see the same idioms: ``shutil.which`` availability check, hard
timeout, never raises, returns ``(success, error_text)``.

Invocation: ``apktool d -r -f -o <out> <apk>``.

* ``-r`` skips resource decoding (we only need smali for the call graph;
  resources slow apktool down and are already covered by jadx output).
* ``-f`` overwrites any existing output (we ``rmtree`` first as belt-and-braces).
* We deliberately do **not** pass ``--no-debug-info`` (a.k.a. ``-b``) — that
  flag strips ``.line`` directives, but the call-graph schema requires
  ``edges.src_line NOT NULL`` so the UI can jump to the exact invoke site.

Output layout::

    <out_dir>/
        smali/             classes.dex
        smali_classes2/    classes2.dex (multi-dex apps)
        smali_classes3/    ...
        AndroidManifest.xml

Only the ``smali*/`` trees are consumed by :mod:`androscan.analysis.smali_parser`.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Apktool can be slow on huge apps; cap at 10 minutes to keep the worker
# thread bounded. Real analysis runs typically finish in seconds.
APKTOOL_TIMEOUT_SEC = 600


def is_available(apktool_cmd: str = "apktool") -> bool:
    """``shutil.which`` availability check (called by call_graph.start_build_async)."""
    return bool(apktool_cmd) and shutil.which(apktool_cmd) is not None


def run_apktool_decode(
    apktool_cmd: str,
    apk: Path,
    out_dir: Path,
    *,
    timeout: int = APKTOOL_TIMEOUT_SEC,
) -> tuple[bool, str]:
    """Invoke ``apktool d -r -f -o <out_dir> <apk>``; returns ``(success, error_text)``.

    Success is judged by "did at least one ``.smali`` file appear", not by
    apktool's exit code (which can be non-zero on partial-decode quirks
    that don't actually break the smali output).
    """
    if not shutil.which(apktool_cmd):
        return False, f"apktool not on PATH (looked for {apktool_cmd!r})"
    if not apk.is_file():
        return False, f"APK not found: {apk}"

    try:
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [apktool_cmd, "d", "-r", "-f", "-o", str(out_dir), str(apk)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if any(out_dir.rglob("*.smali")):
            return True, ""
        stderr = (proc.stderr or proc.stdout or "").strip()
        return False, stderr or f"apktool exited {proc.returncode}, no .smali produced"
    except subprocess.TimeoutExpired:
        return False, f"apktool timed out after {timeout}s"
    except OSError as e:
        return False, str(e)


def find_smali_roots(apktool_out: Path) -> list[Path]:
    """Return ``[<apktool_out>/smali, <apktool_out>/smali_classes2, ...]``.

    Apktool writes one root per dex; we accept any directory whose name
    matches ``smali`` or ``smali_classesN`` to be safe across versions.
    """
    if not apktool_out.is_dir():
        return []
    roots: list[Path] = []
    for entry in sorted(apktool_out.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name == "smali" or (name.startswith("smali_classes") and name[len("smali_classes"):].isdigit()):
            roots.append(entry)
    return roots
