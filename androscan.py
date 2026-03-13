#!/usr/bin/env python3
"""AndroScan CLI: LLM-native Android pentesting tool.

Usage:
  python androscan.py --apk <path> [--task <name> ...] [--output <dir>] [--config <file>]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from androscan import constants
from androscan.config import load_config
from androscan.extraction import extract_dossier
from androscan.internal import app_id_from_dossier
from androscan.internal.run_folder import create_run_folder
from androscan.internal.workflow import run_workflow


def _section(title: str, rule: Optional[str] = None) -> None:
    r = rule or constants.SECTION_RULE
    print(r)
    print(f"[*] {title}")
    print(r)


def _subsection(title: str) -> None:
    print(f"---------- {title} ----------")


def _exploitability_label(score: int) -> str:
    return constants.EXPLOITABILITY_LABELS.get(score, str(score))


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
    parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="Path to global_config.yaml (default: cwd or config/global_config.yaml)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    apk_path = args.apk
    tasks = args.tasks if args.tasks else ["exported_components"]

    if not Path(apk_path).exists() and apk_path != "/dummy.apk":
        print(f"Error: APK path does not exist: {apk_path}", file=sys.stderr)
        return 1

    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tasks_str = ", ".join(tasks)
    _section("Run started", config.section_rule)
    print(f"  Started:  {started}")
    print(f"  APK:      {apk_path}")
    print(f"  Tasks:    {tasks_str}")
    print()

    try:
        dossier = extract_dossier(apk_path)
    except Exception as e:
        print(f"Error: extraction failed: {e}", file=sys.stderr)
        return 1

    n_act = len(dossier.exported_activities)
    n_svc = len(dossier.exported_services)
    n_rec = len(dossier.exported_receivers)
    n_prv = len(dossier.exported_providers)
    n_perm = len(dossier.permissions)
    n_deep = len(dossier.deep_links)
    _section("Extraction", config.section_rule)
    print(f"  Package:  {dossier.apk_info.package}")
    print(f"  Dossier:  {n_act} activities, {n_svc} services, {n_rec} receivers, {n_prv} providers, {n_perm} permissions, {n_deep} deep links")
    print()

    app_id = app_id_from_dossier(dossier)

    if args.output:
        from androscan.internal.run_folder import run_timestamp
        run_folder = Path(args.output) / app_id / run_timestamp()
        run_folder.mkdir(parents=True, exist_ok=True)
    else:
        run_folder = create_run_folder(app_id, config)

    _section("Analysis", config.section_rule)
    print(f"  Running:  {tasks_str}")
    print()

    try:
        run_workflow(apk_path, tasks, run_folder, config)
    except Exception as e:
        print(f"Error: workflow failed: {e}", file=sys.stderr)
        return 1

    report_path = run_folder / "report.json"
    report_data = None
    if report_path.exists():
        try:
            report_data = json.loads(report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    package = dossier.apk_info.package

    _section("Run summary", config.section_rule)
    if report_data and report_data.get("hypotheses"):
        hypotheses = report_data["hypotheses"]
        n = len(hypotheses)
        by_exp = {}
        for h in hypotheses:
            exp = h.get("exploitability", 1)
            by_exp[exp] = by_exp.get(exp, 0) + 1
        parts = [f"{by_exp[exp]} {_exploitability_label(exp)}" for exp in sorted(by_exp.keys(), reverse=True)]
        print(f"  Findings:  {n} hypothesis" + ("es" if n != 1 else "") + f" ({', '.join(parts)} exploitability)")
        print()
        for i, h in enumerate(hypotheses, 1):
            title = h.get("title") or "(no title)"
            comp = h.get("component_name") or "—"
            exp = h.get("exploitability", 0)
            conf = h.get("confidence", 0)
            print(f"  {i}. [{h.get('id', '—')}] {title}")
            print(f"     Component: {comp}  (exploitability: {exp}, confidence: {conf})")
            print()
        print(f"  Full report:  {report_path}")
    else:
        print("  Findings:  0 hypotheses")
        print(f"  Full report:  {report_path}")

    _section("Appendix", config.section_rule)
    _subsection("Run log")
    print(f"  APK:      {apk_path}")
    print(f"  App:      {app_id} ({package})")
    print(f"  Tasks:    {tasks_str}")
    print(f"  Output:   {run_folder}")
    print(f"  Report:   {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
