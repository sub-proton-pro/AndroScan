"""Persistent bulk-decompile cache keyed by APK sha256.

Layout::

    apps/<app_id>/.decompiled/<apk_sha256>/
        sources/        <- jadx -d output (java + resources)
        index.json      <- {status, started_ts, finished_ts, error?, jadx_cmd, apk_path}
        tree.json       <- {packages: [{name, classes:[{name, methods:[...], rel_path}]}]}

The leading dot in ``.decompiled`` keeps the cache invisible to
``GET /api/projects`` (which already filters dot-prefixed dir names).

Decompile is **lazy** — we only run jadx when an Inspect-tab feature actually
needs source. Status transitions:

    missing -> pending -> ready
    missing -> pending -> failed
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from androscan.internal.app_meta import compute_apk_sha256, load_app_meta

CACHE_DIR_NAME = ".decompiled"
SOURCES_SUBDIR = "sources"
INDEX_FILENAME = "index.json"
TREE_FILENAME = "tree.json"

# jadx can be slow; allow up to 15 minutes for very large apps.
JADX_TIMEOUT_SEC = 900

# In-process registry of currently running decompile jobs, keyed by sha.
# Lets concurrent /api/decompile callers share one job and one status.
_RUNNING: dict[str, "_Job"] = {}
_RUNNING_LOCK = threading.Lock()


@dataclass
class _Job:
    sha: str
    cache_dir: Path
    thread: threading.Thread


# ---------------------------------------------------------------------------
# Path helpers


def cache_root_for(app_dir: Path, sha: str) -> Path:
    """Return ``apps/<app>/.decompiled/<sha>/`` (does not create it)."""
    return Path(app_dir) / CACHE_DIR_NAME / sha


def index_path(app_dir: Path, sha: str) -> Path:
    return cache_root_for(app_dir, sha) / INDEX_FILENAME


def sources_dir(app_dir: Path, sha: str) -> Path:
    return cache_root_for(app_dir, sha) / SOURCES_SUBDIR


def tree_path(app_dir: Path, sha: str) -> Path:
    return cache_root_for(app_dir, sha) / TREE_FILENAME


def app_apk_info(app_dir: Path) -> tuple[Optional[str], Optional[str]]:
    """Read ``app_meta.json`` and return ``(apk_sha256, apk_path)``."""
    meta = load_app_meta(app_dir)
    if not meta:
        return None, None
    return meta.get("apk_sha256"), meta.get("apk_path")


# ---------------------------------------------------------------------------
# Status model


def _read_index(app_dir: Path, sha: str) -> dict[str, Any]:
    p = index_path(app_dir, sha)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_index(app_dir: Path, sha: str, data: dict[str, Any]) -> None:
    p = index_path(app_dir, sha)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def get_status(app_dir: Path, sha: Optional[str] = None) -> dict[str, Any]:
    """Return ``{status, sha, sources_dir?, file_count?, error?, ...}``.

    ``status`` is one of: ``missing | pending | ready | failed | unknown``.
    """
    apk_sha, apk_path = app_apk_info(app_dir)
    use_sha = sha or apk_sha
    if not use_sha:
        return {"status": "unknown", "error": "no app_meta.json (run analysis first)"}

    idx = _read_index(app_dir, use_sha)
    src = sources_dir(app_dir, use_sha)
    if idx.get("status") == "ready" and src.is_dir():
        return {
            "status": "ready",
            "sha": use_sha,
            "apk_path": idx.get("apk_path") or apk_path,
            "started_ts": idx.get("started_ts"),
            "finished_ts": idx.get("finished_ts"),
            "file_count": idx.get("file_count"),
            "sources_dir": str(src),
            "tree_available": tree_path(app_dir, use_sha).is_file(),
        }
    if idx.get("status") == "pending":
        return {"status": "pending", "sha": use_sha, "started_ts": idx.get("started_ts")}
    if idx.get("status") == "failed":
        return {"status": "failed", "sha": use_sha, "error": idx.get("error", "")}
    return {"status": "missing", "sha": use_sha, "apk_path": apk_path}


# ---------------------------------------------------------------------------
# Decompile job


def _run_jadx_bulk(jadx_cmd: str, apk: Path, out_dir: Path, timeout: int = JADX_TIMEOUT_SEC) -> tuple[bool, str]:
    """Invoke ``jadx -d <out_dir> <apk>``; returns (success, error_text)."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [jadx_cmd, "-d", str(out_dir), str(apk)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # jadx returns non-zero on partial success too; we accept it as long as
        # something was written.
        if any(out_dir.rglob("*.java")) or any(out_dir.rglob("*.kt")):
            return True, ""
        stderr = (proc.stderr or proc.stdout or "").strip()
        return False, stderr or f"jadx exited {proc.returncode}, no .java/.kt produced"
    except subprocess.TimeoutExpired:
        return False, f"jadx timed out after {timeout}s"
    except OSError as e:
        return False, str(e)


def _decompile_worker(
    app_dir: Path,
    sha: str,
    apk_path: str,
    jadx_cmd: str,
    on_done: Optional[Callable[[bool], None]] = None,
) -> None:
    """Background thread body — runs jadx then builds the tree."""
    try:
        _write_index(app_dir, sha, {
            "status": "pending",
            "started_ts": time.time(),
            "apk_path": apk_path,
            "jadx_cmd": jadx_cmd,
        })
        out = sources_dir(app_dir, sha)
        # Clean any stale partial output before starting.
        if out.exists():
            shutil.rmtree(out, ignore_errors=True)
        ok, err = _run_jadx_bulk(jadx_cmd, Path(apk_path), out)
        if not ok:
            _write_index(app_dir, sha, {
                "status": "failed",
                "started_ts": time.time(),
                "finished_ts": time.time(),
                "apk_path": apk_path,
                "jadx_cmd": jadx_cmd,
                "error": err[:2000],
            })
            if on_done:
                on_done(False)
            return
        # Build + persist class tree once.
        tree, file_count = build_tree(out)
        tree_path(app_dir, sha).write_text(
            json.dumps(tree, indent=2, sort_keys=True), encoding="utf-8"
        )
        _write_index(app_dir, sha, {
            "status": "ready",
            "started_ts": time.time(),
            "finished_ts": time.time(),
            "apk_path": apk_path,
            "jadx_cmd": jadx_cmd,
            "file_count": file_count,
        })
        if on_done:
            on_done(True)
    finally:
        with _RUNNING_LOCK:
            _RUNNING.pop(sha, None)


def start_decompile(
    app_dir: Path,
    *,
    jadx_cmd: str = "jadx",
    blocking: bool = False,
    on_done: Optional[Callable[[bool], None]] = None,
) -> dict[str, Any]:
    """Kick off decompile (no-op if already pending or ready).

    ``blocking=True`` runs in the calling thread (used by tests).
    """
    apk_sha, apk_path = app_apk_info(app_dir)
    if not apk_sha or not apk_path:
        return {"status": "error", "error": "no app_meta.json (or missing apk_path/apk_sha256)"}
    apk_file = Path(apk_path)
    if not apk_file.is_file():
        return {"status": "error", "error": f"APK not found at {apk_path}"}
    if not shutil.which(jadx_cmd):
        return {"status": "error", "error": f"jadx not available on PATH (looked for {jadx_cmd!r})"}

    status = get_status(app_dir, apk_sha)
    if status["status"] in ("ready", "pending"):
        return status

    with _RUNNING_LOCK:
        if apk_sha in _RUNNING:
            return get_status(app_dir, apk_sha)
        if blocking:
            # Run inline; still register so concurrent callers see "pending".
            _RUNNING[apk_sha] = _Job(apk_sha, cache_root_for(app_dir, apk_sha), threading.current_thread())
        else:
            t = threading.Thread(
                target=_decompile_worker,
                args=(app_dir, apk_sha, apk_path, jadx_cmd, on_done),
                daemon=True,
                name=f"decompile-{apk_sha[:10]}",
            )
            _RUNNING[apk_sha] = _Job(apk_sha, cache_root_for(app_dir, apk_sha), t)
            t.start()
            return {"status": "pending", "sha": apk_sha, "started_ts": time.time()}

    # blocking branch falls through here:
    _decompile_worker(app_dir, apk_sha, apk_path, jadx_cmd, on_done)
    return get_status(app_dir, apk_sha)


# ---------------------------------------------------------------------------
# Tree extraction
#
# We don't try to build a perfectly correct Java AST — a regex over each file
# suffices to get class + method names for navigation, and is robust to jadx
# decompiler quirks like Kotlin lambdas, anonymous inner classes, etc. Errors
# in extraction are silently skipped.

# (?<!\w) avoids matching "subclass" / "interfaceX". Modifiers vary widely;
# we just match "class|interface|enum|@interface" preceded by typical
# whitespace, then capture the identifier.
_CLASS_RE = re.compile(
    r"(?<!\w)(?:class|interface|enum|@interface)\s+([A-Za-z_][A-Za-z0-9_$]*)"
)
# Method signatures: <return-type> <name>(... ) {  with optional generics on
# the return type. Skip language keywords like "if", "while" etc.
_METHOD_RE = re.compile(
    r"^[ \t]*(?:public|private|protected|static|final|synchronized|native|abstract|default|\s)*"
    r"(?:[A-Za-z_][\w<>\[\],?\s.]*?\s+)?"
    r"([A-Za-z_][A-Za-z0-9_$]*)\s*\([^;{)]*\)\s*(?:throws\s+[^;{]+)?\s*\{",
    re.MULTILINE,
)
_METHOD_BLACKLIST = {
    "if", "for", "while", "switch", "synchronized", "catch", "try",
    "return", "do", "else", "new",
}


def _extract_classes(source: str) -> list[dict[str, Any]]:
    classes: list[dict[str, Any]] = []
    for m in _CLASS_RE.finditer(source):
        classes.append({"name": m.group(1), "methods": []})
    if classes:
        # Methods are file-scoped here; we don't try to attribute methods to
        # nested classes precisely. The first class entry gets all methods.
        primary = classes[0]
        seen: set[str] = set()
        for mm in _METHOD_RE.finditer(source):
            name = mm.group(1)
            if name in _METHOD_BLACKLIST or name in classes[0]["name"]:
                continue
            if name in seen:
                continue
            seen.add(name)
            primary["methods"].append(name)
    return classes


def _package_from_source(source: str) -> str:
    m = re.search(r"^\s*package\s+([\w.]+)\s*;", source, re.MULTILINE)
    return m.group(1) if m else ""


def build_tree(sources_root: Path) -> tuple[dict[str, Any], int]:
    """Walk ``sources_root`` and produce ``({packages: [...]}, file_count)``.

    Each package: ``{name, classes:[{name, methods:[...], rel_path}]}``.
    Limited to ~16 KB of source per file scanned (we only need the head for
    declarations); resource files (R.java) are included with empty methods.
    """
    packages: dict[str, list[dict[str, Any]]] = {}
    file_count = 0
    for p in sorted(sources_root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix not in (".java", ".kt"):
            continue
        file_count += 1
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:16_000]
        except OSError:
            continue
        pkg = _package_from_source(head) or "(default)"
        for cls in _extract_classes(head):
            cls["rel_path"] = str(p.relative_to(sources_root))
            packages.setdefault(pkg, []).append(cls)
    out = {
        "packages": [
            {"name": k, "classes": sorted(v, key=lambda c: c["name"].lower())}
            for k, v in sorted(packages.items())
        ],
    }
    return out, file_count


def load_tree(app_dir: Path, sha: Optional[str] = None) -> Optional[dict[str, Any]]:
    apk_sha, _ = app_apk_info(app_dir)
    use_sha = sha or apk_sha
    if not use_sha:
        return None
    p = tree_path(app_dir, use_sha)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Read a single decompiled source file (path-traversal safe)


def read_source_file(app_dir: Path, rel_path: str, sha: Optional[str] = None) -> Optional[str]:
    apk_sha, _ = app_apk_info(app_dir)
    use_sha = sha or apk_sha
    if not use_sha:
        return None
    src = sources_dir(app_dir, use_sha)
    if not src.is_dir():
        return None
    candidate = (src / rel_path).resolve()
    try:
        candidate.relative_to(src.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    try:
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
