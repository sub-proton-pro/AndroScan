#!/usr/bin/env python3
"""AndroScan CLI: LLM-native Android pentesting tool.

Usage:
  python androscan.py --apk <path> --task <name> [--task <name> ...] [--output <dir>]
"""

import argparse
import json
import sys
from pathlib import Path

from androscan.extraction import extract_dossier
from androscan.internal import app_id_from_dossier
from androscan.internal.run_folder import create_run_folder
from androscan.internal.workflow import run_workflow


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AndroScan: analyze APK for exported component exploitability (LLM-assisted)."
    )
    parser.add_argument("--apk", required=True, help="Path to the APK file")
    parser.add_argument(
        "--task",
        action="append",
        default=[],
        dest="tasks",
        metavar="NAME",
        help="Task(s) to run (e.g. exported_components). Can be repeated.",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="DIR",
        help="Override run folder root (default: apps)",
    )
    args = parser.parse_args()

    apk_path = args.apk
    tasks = args.tasks if args.tasks else ["exported_components"]

    if not Path(apk_path).exists() and apk_path != "/dummy.apk":
        # Allow /dummy.apk for stub testing
        print(f"Error: APK path does not exist: {apk_path}", file=sys.stderr)
        return 1

    try:
        dossier = extract_dossier(apk_path)
    except Exception as e:
        print(f"Error: extraction failed: {e}", file=sys.stderr)
        return 1

    app_id = app_id_from_dossier(dossier)

    if args.output:
        from androscan.internal.run_folder import run_timestamp
        run_folder = Path(args.output) / app_id / run_timestamp()
        run_folder.mkdir(parents=True, exist_ok=True)
    else:
        run_folder = create_run_folder(app_id)

    try:
        run_workflow(apk_path, tasks, run_folder)
    except Exception as e:
        print(f"Error: workflow failed: {e}", file=sys.stderr)
        return 1

    # Descriptive run summary
    report_path = run_folder / "report.json"
    n_findings = None
    if report_path.exists():
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
            n_findings = len(data.get("hypotheses") or [])
        except (json.JSONDecodeError, OSError):
            pass

    package = dossier.apk_info.package
    tasks_str = ", ".join(tasks)
    print("AndroScan — run complete\n")
    print(f"  APK:     {apk_path}")
    print(f"  App:     {app_id} ({package})")
    print(f"  Tasks:   {tasks_str}")
    print(f"  Output:  {run_folder}")
    if n_findings is not None:
        word = "hypothesis" if n_findings == 1 else "hypotheses"
        print(f"  Report:  report.json ({n_findings} {word})")
    else:
        print("  Report:  report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
