#!/usr/bin/env python3
"""AndroScan CLI: LLM-native Android pentesting tool.

Usage:
  python androscan.py --apk <path> [--task <name> ...] [--output <dir>] [--config <file>]
"""

import argparse
import json
import shutil
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class ShutdownRequested(Exception):
    """Raised when SIGTERM or other forceful shutdown is received."""


def _sigterm_handler(_signum: int, _frame: Optional[object]) -> None:
    raise ShutdownRequested()


from androscan import constants
from androscan.cli_spinner import pause_active, resume_active, spinner
from androscan.cli_term import blue, bright_red, colored_json, dark_red, gold, green, grey, orange
from androscan.config import load_config
from androscan.internal.app_meta import extracted_apk_path, save_app_meta
from androscan.internal.resolve_app_id import resolve_app_id
from androscan.internal.run_folder import create_run_folder, run_folder_display_path
from androscan.internal.run_log import RunLogger
from androscan.internal.exploit_verification import run_exploit_verification
from androscan.internal.workflow import run_workflow
from androscan.llm import is_ollama_available
from androscan.llm.client import OLLAMA_SETUP_TIP
from androscan.llm.parser import Hypothesis


def _section(title: str, rule: Optional[str] = None) -> None:
    r = rule or constants.SECTION_RULE
    print(r)
    print(f"[*] {title}")
    print(r)


def _subsection(title: str) -> None:
    print(f"---------- {title} ----------")


def _exploitability_label(score: int) -> str:
    return constants.EXPLOITABILITY_LABELS.get(score, str(score))


def _severity_label(score: int) -> str:
    """Display severity for CLI (brackets); uses ISSUE_SEVERITY_LABELS."""
    return constants.ISSUE_SEVERITY_LABELS.get(score, "Informational")


def _severity_label_colored(score: int) -> str:
    """Severity label with color for terminal: Critical=dark_red, High=bright_red, Medium=orange, Low=blue, Informational=green."""
    labels_colors = {
        5: ("Critical", dark_red),
        4: ("High", bright_red),
        3: ("Medium", orange),
        2: ("Low", blue),
        1: ("Informational", green),
    }
    text, color_fn = labels_colors.get(score, ("Informational", green))
    return color_fn(f"[{text}]")


def _component_name_from_ref(dossier: Any, ref: str) -> Optional[str]:
    """Resolve evidence_ref (e.g. exported_activities[0]) to component name from dossier."""
    if not ref or not isinstance(ref, str) or "[" not in ref or not ref.endswith("]"):
        return None
    key, rest = ref.split("[", 1)
    try:
        idx = int(rest.rstrip("]"))
    except ValueError:
        return None
    if key == "exported_activities" and 0 <= idx < len(dossier.exported_activities):
        return dossier.exported_activities[idx].name
    if key == "exported_services" and 0 <= idx < len(dossier.exported_services):
        return dossier.exported_services[idx].name
    if key == "exported_receivers" and 0 <= idx < len(dossier.exported_receivers):
        return dossier.exported_receivers[idx].name
    if key == "exported_providers" and 0 <= idx < len(dossier.exported_providers):
        return dossier.exported_providers[idx].name
    if key == "deep_links" and 0 <= idx < len(dossier.deep_links):
        return dossier.deep_links[idx].component
    return None


def _find_latest_report(app_id_root: Path) -> Optional[dict]:
    """Find the most recent report.json in the app's run folders (sorted by name descending)."""
    if not app_id_root.is_dir():
        return None
    candidates = sorted(
        [d for d in app_id_root.iterdir() if d.is_dir() and (d / "report.json").exists()],
        key=lambda d: d.name,
        reverse=True,
    )
    for run_dir in candidates:
        rpt = run_dir / "report.json"
        try:
            data = json.loads(rpt.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("hypotheses"):
                return data
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _report_hypotheses_to_objects(report_data: dict) -> list:
    """Convert hypotheses dicts from report.json to Hypothesis dataclass instances."""
    out = []
    for h in report_data.get("hypotheses") or []:
        out.append(Hypothesis(
            id=h.get("id", ""),
            component_type=h.get("component_type", ""),
            component_name=h.get("component_name", ""),
            title=h.get("title", ""),
            description=h.get("description", ""),
            evidence_refs=list(h.get("evidence_refs") or []),
            exploitability=h.get("exploitability", 1),
            confidence=h.get("confidence", 0),
            remediation_hint=h.get("remediation_hint", ""),
        ))
    return out


def main() -> int:
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None:
        signal.signal(sigterm, _sigterm_handler)

    try:
        return _run()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except ShutdownRequested:
        print("Shutdown requested.", file=sys.stderr)
        return 143


def _run() -> int:
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
    parser.add_argument(
        "--exploit_verification_test",
        action="store_true",
        default=False,
        help="Skip LLM analysis; load hypotheses from the most recent report.json and run exploit verification only.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=1,
        help="Verbosity (default: 1; -vv shows LLM thinking in terminal)",
    )
    args = parser.parse_args()
    verbosity = max(1, args.verbose)

    config = load_config(args.config)
    apk_path = args.apk
    tasks = args.tasks if args.tasks else ["exported_components"]

    if not Path(apk_path).exists() and apk_path != "/dummy.apk":
        print(f"Error: APK path does not exist: {apk_path}", file=sys.stderr)
        return 1

    if not shutil.which(config.apktool_cmd):
        print(orange("apktool not found."), file=sys.stderr)
        print(grey("Install: https://apktool.org/docs/install"), file=sys.stderr)
        return 1

    run_start = datetime.now()
    started = run_start.strftime("%Y-%m-%d %H:%M:%S")
    tasks_str = ", ".join(tasks)
    _section("Run started", config.section_rule)
    print(f"  Started:  {started}")
    print(f"  APK:      {apk_path}")
    print(f"  Tasks:    {tasks_str}")
    print()

    try:
        app_id, dossier, temp_extraction, apk_hash = resolve_app_id(apk_path, config)
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
    _subsection("Dossier")
    print(colored_json(dossier.to_dict()))
    print()

    if args.output:
        from androscan.internal.run_folder import run_timestamp
        run_folder = Path(args.output) / app_id / run_timestamp()
        run_folder.mkdir(parents=True, exist_ok=True)
    else:
        run_folder = create_run_folder(app_id, config)

    # Move fresh extraction from system temp to apps/<app_id>/extracted_apk/
    if temp_extraction is not None:
        app_id_root = run_folder.parent
        final_extracted = extracted_apk_path(app_id_root)
        temp_extracted = temp_extraction / "extracted_apk"
        if temp_extracted.is_dir() and not final_extracted.exists():
            final_extracted.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp_extracted), str(final_extracted))
        shutil.rmtree(temp_extraction, ignore_errors=True)
        if apk_hash:
            save_app_meta(app_id_root, apk_hash, dossier.to_dict(), apk_path)

    # --exploit_verification_test: skip LLM, load hypotheses from latest report.json
    if args.exploit_verification_test:
        app_id_root = run_folder.parent
        report_data = _find_latest_report(app_id_root)
        if not report_data:
            print(
                bright_red(f"Error: no report.json with hypotheses found under {app_id_root}"),
                file=sys.stderr,
            )
            print(grey("Run a full analysis first so that a report.json exists."), file=sys.stderr)
            return 1
        loaded_hyps = _report_hypotheses_to_objects(report_data)
        if not loaded_hyps:
            print(bright_red("Error: report.json contains no hypotheses."), file=sys.stderr)
            return 1
        print(green(f"  Loaded {len(loaded_hyps)} hypothesis(es) from latest report.json"))
        print()
        _section("Exploit Verification Test", config.section_rule)
        vuln_module = tasks[0]

        def _cli_sink_test(kind: str, payload: object) -> None:
            if kind == "task":
                _spinner_ref_test.update(str(payload))
            elif kind == "error":
                pause_active()
                print(orange("[ERROR] " + str(payload)), file=sys.stderr)
                resume_active()

        run_logger = RunLogger(run_folder, verbosity=verbosity, ui_sink=_cli_sink_test)
        from androscan.skills import SkillContext, execute as execute_skill

        ctx = SkillContext(config=config, run_folder=run_folder, dossier_dict=dossier.to_dict(), apk_path=apk_path)
        with spinner("Exploit verification test...", done_message="Exploit verification test complete.") as _spinner_ref_test:
            try:
                verification_results = run_exploit_verification(
                    loaded_hyps, dossier.to_dict(), run_folder, vuln_module, ctx, run_logger
                )
            except Exception as e:
                run_logger.error(f"Exploit verification test failed: {e}")
                print(f"Error: exploit verification test failed: {e}", file=sys.stderr)
                return 1

        report_params = {
            "hypotheses": [
                {
                    "id": h.id,
                    "component_type": h.component_type,
                    "component_name": h.component_name,
                    "title": h.title,
                    "description": h.description,
                    "evidence_refs": h.evidence_refs,
                    "exploitability": h.exploitability,
                    "confidence": h.confidence,
                    "remediation_hint": h.remediation_hint,
                }
                for h in loaded_hyps
            ],
            "summary": "",
            "verification_results": verification_results,
        }
        execute_skill("generate_report", report_params, ctx)

        report_path = run_folder / "report.json"
        report_data_new = None
        if report_path.exists():
            try:
                report_data_new = json.loads(report_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        package = dossier.apk_info.package
        run_elapsed = datetime.now() - run_start
        total_sec = max(0, run_elapsed.total_seconds())
        hours = int(total_sec // 3600)
        minutes = int((total_sec % 3600) // 60)
        seconds = int(total_sec % 60)
        duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        section_rule = config.section_rule or constants.SECTION_RULE
        print(section_rule)
        print("[*] Exploit verification test summary")
        print(section_rule)
        print(f"  Duration:  {duration_str}")
        if report_data_new and report_data_new.get("hypotheses"):
            for i, h in enumerate(report_data_new["hypotheses"], 1):
                title = h.get("title", "(no title)")
                verified = h.get("verified")
                v_label = green("VERIFIED") if verified else (bright_red("NOT VERIFIED") if verified is False else grey("N/A"))
                print(f"  {i}. {v_label} {title}")
                reasoning = (h.get("verification_reasoning") or "").strip()
                if reasoning:
                    print(f"     Reasoning: {reasoning}")
        print(f"\n  Full report:  {report_path}")
        display_output = run_folder_display_path(run_folder)
        print(f"  Output:       {display_output}")
        return 0

    base_url = (config.ollama_base_url or "").strip().rstrip("/") or "http://localhost:11434"
    if not is_ollama_available(base_url):
        print(orange("Ollama not reachable at " + base_url + "."), file=sys.stderr)
        print(grey(OLLAMA_SETUP_TIP), file=sys.stderr)
        return 1

    _section("Analysis", config.section_rule)
    print(f"  Running:  {tasks_str}")
    print()

    def _cli_sink(kind: str, payload: object) -> None:
        if kind == "task":
            _spinner_ref.update(str(payload))
        elif kind == "thinking":
            pause_active()
            print(grey(str(payload)))
            resume_active()
        elif kind == "error":
            pause_active()
            print(orange("[ERROR] " + str(payload)), file=sys.stderr)
            resume_active()
        elif kind == "component_findings":
            pause_active()
            if isinstance(payload, dict):
                comp_type = payload.get("component_type") or "component"
                comp_label = payload.get("component_label") or "—"
                hyps = payload.get("hypotheses") or []
                n = len(hyps)
                print(gold(f"  [{comp_type}] {comp_label}: {n} finding(s)"))
                for h in hyps:
                    title = h.get("title") or "(no title)"
                    exp = h.get("exploitability", 1)
                    conf = h.get("confidence", 0)
                    desc = (h.get("description") or "").strip()
                    print(f"     • {_severity_label_colored(exp)} {title} (confidence: {conf})")
                    if desc:
                        print(f"       Description: {desc}")
                        print()
            resume_active()

    run_logger = RunLogger(run_folder, verbosity=verbosity, ui_sink=_cli_sink)
    with spinner("Analysis starting...", done_message="Analysis complete.") as _spinner_ref:
        try:
            run_workflow(apk_path, tasks, run_folder, config, run_logger=run_logger)
        except Exception as e:
            run_logger.error(f"Workflow failed: {e}")
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
    section_rule = config.section_rule or constants.SECTION_RULE
    run_elapsed = datetime.now() - run_start
    total_sec = max(0, run_elapsed.total_seconds())
    hours = int(total_sec // 3600)
    minutes = int((total_sec % 3600) // 60)
    seconds = int(total_sec % 60)
    duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    run_summary_lines = [section_rule, "[*] Run summary", section_rule, f"  Duration:  {duration_str}", ""]
    run_summary_lines_display = list(run_summary_lines)
    if report_data and report_data.get("hypotheses"):
        hypotheses = report_data["hypotheses"]
        n = len(hypotheses)
        by_exp = {}
        for h in hypotheses:
            exp = h.get("exploitability", 1)
            by_exp[exp] = by_exp.get(exp, 0) + 1
        parts = [f"{by_exp[exp]} {_exploitability_label(exp)}" for exp in sorted(by_exp.keys(), reverse=True)]
        line = f"  Findings:  {n} ({', '.join(parts)})"
        run_summary_lines.append(line)
        run_summary_lines_display.append(line)
        run_summary_lines.append("")
        run_summary_lines_display.append("")
        for i, h in enumerate(hypotheses, 1):
            title = h.get("title") or "(no title)"
            comp = h.get("component_name")
            if not comp and (refs := h.get("evidence_refs")):
                first_ref = refs[0] if isinstance(refs[0], str) else None
                if first_ref:
                    comp = _component_name_from_ref(dossier, first_ref)
                if not comp and first_ref:
                    comp = first_ref
            comp = comp or "—"
            exp = h.get("exploitability", 1)
            conf = h.get("confidence", 0)
            desc = (h.get("description") or "").strip()
            run_summary_lines.append(f"  {i}. [{_severity_label(exp)}] {title}")
            run_summary_lines_display.append(f"  {i}. {_severity_label_colored(exp)} {title}")
            run_summary_lines.append(f"     Component: {comp}  (confidence: {conf})")
            run_summary_lines_display.append(f"     Component: {comp}  (confidence: {conf})")
            if desc:
                run_summary_lines.append(f"     Description: {desc}")
                run_summary_lines_display.append(f"     Description: {desc}")
            run_summary_lines.append("")
            run_summary_lines_display.append("")
        run_summary_lines.append(f"  Full report:  {report_path}")
        run_summary_lines_display.append(f"  Full report:  {report_path}")
    else:
        run_summary_lines.append("  Findings:  0 hypotheses")
        run_summary_lines_display.append("  Findings:  0 hypotheses")
        run_summary_lines.append(f"  Full report:  {report_path}")
        run_summary_lines_display.append(f"  Full report:  {report_path}")
    run_summary_text = "\n".join(run_summary_lines)
    run_summary_text_display = "\n".join(run_summary_lines_display)
    print(run_summary_text_display)
    run_logger.write_raw(run_summary_text)

    _section("Appendix", config.section_rule)
    _subsection("Run log")
    display_output = run_folder_display_path(run_folder)
    print(f"  APK:      {apk_path}")
    print(f"  App:      {app_id} ({package})")
    print(f"  Tasks:    {tasks_str}")
    print(f"  Output:   {display_output}")
    print(f"  Report:   {Path(display_output) / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
