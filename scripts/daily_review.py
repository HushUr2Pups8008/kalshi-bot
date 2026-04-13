"""
Cross-platform daily review runner.

Runs the reporting scripts in a fixed sequence, mirrors their output to the
console and to logs/reports/daily_review_YYYYMMDD.txt, and preserves the
existing PowerShell wrapper's behavior as closely as practical.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "logs" / "reports"
DEFAULT_REPORT_PATH = REPORTS_DIR / f"daily_review_{datetime.now().strftime('%Y%m%d')}.txt"
REPORTS = [
    ("Trade Log Summary", "scripts/trade_log_summary.py"),
    ("Decision Funnel Summary", "scripts/decision_funnel_summary.py"),
    ("Paper Performance Drilldown", "scripts/paper_performance_drilldown.py"),
    ("Freshness Diagnostics", "scripts/freshness_diagnostics.py"),
    ("Signal-to-Edge Diagnostics", "scripts/signal_edge_diagnostics.py"),
    ("Match Quality Diagnostics", "scripts/match_quality_diagnostics.py"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the kalshi-bot daily review suite")
    parser.add_argument("--since", help="Inclusive start date in YYYY-MM-DD")
    parser.add_argument("--until", help="Inclusive end date in YYYY-MM-DD")
    parser.add_argument("--top", type=int, help="Max rows to show in top breakdowns")
    parser.add_argument(
        "--exclude-test",
        action="store_true",
        help="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    return parser.parse_args()


def write_report_line(report_path: Path, text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))
        sys.stdout.buffer.flush()
    with report_path.open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def build_shared_args(args: argparse.Namespace) -> list[str]:
    shared_args: list[str] = []
    if args.since:
        shared_args.extend(["--since", args.since])
    if args.until:
        shared_args.extend(["--until", args.until])
    if args.top is not None:
        shared_args.extend(["--top", str(args.top)])
    if args.exclude_test:
        shared_args.append("--exclude-test")
    return shared_args


def main() -> int:
    args = parse_args()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = DEFAULT_REPORT_PATH
    shared_args = build_shared_args(args)

    for title, relative_script in REPORTS:
        script_path = REPO_ROOT / relative_script
        write_report_line(report_path)
        write_report_line(report_path, "=" * 72)
        write_report_line(report_path, title)
        write_report_line(report_path, "=" * 72)

        cmd = [sys.executable, str(script_path), *shared_args]
        try:
            completed = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            write_report_line(report_path, f"WARNING: Failed to run {relative_script}: {exc}")
            continue

        combined_output = completed.stdout
        if completed.stderr:
            combined_output += completed.stderr
        if combined_output:
            for line in combined_output.rstrip("\n").splitlines():
                write_report_line(report_path, line)
        if completed.returncode != 0:
            write_report_line(report_path, f"WARNING: {relative_script} exited with code {completed.returncode}")

    write_report_line(report_path)
    write_report_line(report_path, f"Daily review report saved to: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
