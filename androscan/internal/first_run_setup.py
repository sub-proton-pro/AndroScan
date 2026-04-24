"""First-time setup: editable Python install + RE Workbench frontend build."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence, TextIO


_RunCommand = Callable[[Sequence[str], Path], int]


def _default_run(cmd: Sequence[str], cwd: Path) -> int:
    """Spawn ``cmd`` in ``cwd`` inheriting std streams; return exit code."""
    completed = subprocess.run(list(cmd), cwd=str(cwd))
    return int(completed.returncode)


def run_first_time_setup(
    repo_root: Path,
    *,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    runner: Optional[_RunCommand] = None,
    npm_path: Optional[str] = None,
) -> int:
    """Run ``pip install -e ".[dev,rag]"`` and (if Node available) ``npm ci`` + ``npm run build``.

    The ``[rag]`` extra pulls in ``fastembed`` + ``numpy`` so semantic retrieval
    (Inspect-tab chat enrichment, ``search_decompiled_sources`` skill, Settings →
    Status RAG card) works out of the box. Without it, only the deterministic
    ``hash`` embed provider is available, which is fine for tests but useless
    for real semantic search. The first ``Build now`` click in the UI will then
    download the fastembed ONNX model (~130 MB) into the per-user cache.

    Returns 0 on success, 1 on the first failed required step. The npm step is
    skipped (with a warning, exit 1) only if ``npm`` is not on PATH; otherwise
    npm failures abort with exit 1. ``runner`` and ``npm_path`` are injection
    points for tests.
    """
    from androscan.cli_term import green, grey, orange

    run = runner or _default_run

    repo_root = repo_root.resolve()
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        print(orange(f"Not a repo root (missing pyproject.toml): {repo_root}"), file=err)
        return 1

    print(green("[*] AndroScan first-time setup"), file=out)
    print(grey(f"    Repo: {repo_root}"), file=out)
    print(file=out)

    # Step 1 -- editable install + dev deps (pytest, httpx for TestClient) +
    # rag deps (fastembed, numpy) so semantic retrieval works out of the box.
    pip_cmd = [sys.executable, "-m", "pip", "install", "-e", ".[dev,rag]"]
    print(green("Step 1/2: pip install -e \".[dev,rag]\""), file=out)
    print(grey(f"    $ {' '.join(pip_cmd)}"), file=out)
    rc = run(pip_cmd, repo_root)
    if rc != 0:
        print(orange(f"pip step failed (exit {rc})."), file=err)
        return 1
    print(green("    pip: OK"), file=out)
    print(file=out)

    # Step 2 -- RE Workbench frontend build (Vite -> androscan/web/static).
    frontend = repo_root / "androscan" / "web" / "frontend"
    if not (frontend / "package.json").is_file():
        print(orange(f"No frontend at {frontend}; skipping npm step."), file=err)
        print(green("Setup complete (Python only)."), file=out)
        return 0

    npm = npm_path or shutil.which("npm")
    if not npm:
        print(orange("npm not on PATH; install Node.js to build the RE Workbench UI."), file=err)
        print(grey("    See https://nodejs.org/ -- then re-run with --setup."), file=err)
        return 1

    lock = frontend / "package-lock.json"
    install_cmd = [npm, "ci"] if lock.is_file() else [npm, "install"]
    install_label = "npm ci" if lock.is_file() else "npm install"

    print(green(f"Step 2/2: {install_label} + npm run build (RE Workbench UI)"), file=out)
    print(grey(f"    cwd: {frontend}"), file=out)
    print(grey(f"    $ {' '.join(install_cmd)}"), file=out)
    rc = run(install_cmd, frontend)
    if rc != 0:
        print(orange(f"{install_label} failed (exit {rc})."), file=err)
        return 1

    build_cmd = [npm, "run", "build"]
    print(grey(f"    $ {' '.join(build_cmd)}"), file=out)
    rc = run(build_cmd, frontend)
    if rc != 0:
        print(orange(f"npm run build failed (exit {rc})."), file=err)
        return 1

    print(green("    npm: OK (static assets at androscan/web/static/)"), file=out)
    print(file=out)
    print(green("Setup complete. Try: python androscan.py --serve"), file=out)
    return 0
