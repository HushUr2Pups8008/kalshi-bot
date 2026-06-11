#!/usr/bin/env python3
"""Diff two performance-analysis reports for throughput + readiness deltas.

Purpose (PROFIT-MATCH-002 verification): after the matcher defining-token guard
(#131), opportunity throughput is expected to rise. This tool extracts the
load-bearing funnel + go-live numbers from two ``analysis_*.txt`` reports and
prints the deltas, so the operator can confirm (or refute) the throughput lift
and watch resolved-trade accumulation toward the go-live bar without eyeballing
two ~1500-line reports.

Reporting-only. Read-only. No DB, no network, no writes.

Usage:
    python scripts/perf_throughput_diff.py OLD_REPORT NEW_REPORT
    # or, with no args, diff the two most recent analysis_*.txt in the
    # performance reports dir:
    python scripts/perf_throughput_diff.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPORTS_DIR = Path(__file__).resolve().parent.parent / "logs" / "reports" / "performance"

# label -> regex capturing the first numeric (int / float / percent / dollar).
# Anchored on the exact emitted label text so a moved number does not mis-parse.
_METRICS = [
    ("Signals detected", r"Signals detected\s*:\s*([0-9]+)"),
    ("Opportunities", r"Opportunities identified\s*:\s*([0-9]+)"),
    ("Trades placed", r"Trades placed \(PAPER_TRADE\)\s*:\s*([0-9]+)"),
    ("Skipped by executor", r"Skipped by executor\s*:\s*([0-9]+)"),
    ("DB resolved trades", r"DB resolved trades\s*:\s*([0-9]+)"),
    ("DB open trades", r"DB open trades\s*:\s*([0-9]+)"),
    # Go-live readiness (lifetime gate line).
    ("Go-live resolved", r"Resolved trades\s*:\s*([0-9]+)\s*/\s*[0-9]+ required"),
    ("Go-live win rate %", r"Win rate\s*:\s*([0-9]+)%\s*/\s*[0-9]+% required"),
    ("Go-live drawdown %", r"Drawdown\s*:\s*([0-9.]+)%\s*/\s*[0-9]+% max"),
]


def _extract(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, pattern in _METRICS:
        m = re.search(pattern, text)
        if m:
            out[name] = float(m.group(1))
    return out


def _recent_reports(n: int = 2) -> list[Path]:
    files = sorted(REPORTS_DIR.glob("analysis_*.txt"))
    return files[-n:]


def diff(old_path: Path, new_path: Path) -> str:
    old = _extract(old_path.read_text(encoding="utf-8"))
    new = _extract(new_path.read_text(encoding="utf-8"))
    lines = [
        "Performance throughput / readiness diff",
        "  OLD: %s" % old_path.name,
        "  NEW: %s" % new_path.name,
        "",
        "%-24s %10s %10s %10s" % ("Metric", "OLD", "NEW", "Delta"),
        "-" * 56,
    ]
    for name, _ in _METRICS:
        o = old.get(name)
        n = new.get(name)
        if o is None and n is None:
            continue
        o_s = "--" if o is None else ("%g" % o)
        n_s = "--" if n is None else ("%g" % n)
        if o is None or n is None:
            d_s = "--"
        else:
            d = n - o
            d_s = "%+g" % d
        lines.append("%-24s %10s %10s %10s" % (name, o_s, n_s, d_s))
    lines.append("")
    lines.append(
        "Watch: 'Trades placed' / 'Opportunities' UP confirms the #131 matcher"
    )
    lines.append(
        "guard lifted throughput; 'Go-live resolved' rising toward 20 is the"
    )
    lines.append("binding go-live constraint.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) == 3:
        old_path, new_path = Path(argv[1]), Path(argv[2])
    elif len(argv) == 1:
        recent = _recent_reports(2)
        if len(recent) < 2:
            print("Need >= 2 analysis_*.txt reports in %s" % REPORTS_DIR)
            return 1
        old_path, new_path = recent[0], recent[1]
    else:
        print(__doc__)
        return 2
    for p in (old_path, new_path):
        if not p.exists():
            print("Report not found: %s" % p)
            return 1
    print(diff(old_path, new_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
