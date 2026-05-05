#!/usr/bin/env python3
"""Project PHASE2-002 LLM-call budget from pre-Wave-1 throughput data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import llm_throughput_audit

_DEFAULT_INPUT = _REPO_ROOT / "logs/trades/live/trades.jsonl"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _observed_calls_per_cycle(report: dict[str, Any]) -> float:
    cycles = report.get("cycles")
    if not isinstance(cycles, dict) or not cycles:
        return float(report.get("attempted_calls") or 0)
    counts = [
        float(row.get("attempted_calls") or 0)
        for row in cycles.values()
        if isinstance(row, dict)
    ]
    if not counts:
        return float(report.get("attempted_calls") or 0)
    return sum(counts) / len(counts)


def _latency(report: dict[str, Any], key: str) -> float:
    stats = report.get("latency_ms")
    if not isinstance(stats, dict):
        return 0.0
    value = stats.get(key)
    return float(value) if isinstance(value, int | float) else 0.0


def project(
    throughput_report: dict[str, Any],
    *,
    cadence_minutes: float = 90.0,
    calls_per_cycle: float | None = None,
    llm_call_budget_per_day: float | None = None,
) -> dict[str, Any]:
    if cadence_minutes <= 0:
        raise ValueError("cadence_minutes must be positive")
    observed_calls = _observed_calls_per_cycle(throughput_report)
    projected_calls_per_cycle = observed_calls if calls_per_cycle is None else calls_per_cycle
    cycles_per_day = 24 * 60 / cadence_minutes
    calls_per_day = projected_calls_per_cycle * cycles_per_day
    median_ms = _latency(throughput_report, "median")
    p90_ms = _latency(throughput_report, "p90")
    median_wall_clock_seconds = calls_per_day * median_ms / 1000
    p90_wall_clock_seconds = calls_per_day * p90_ms / 1000
    budget_utilization_pct = None
    if llm_call_budget_per_day is not None and llm_call_budget_per_day > 0:
        budget_utilization_pct = round(calls_per_day / llm_call_budget_per_day * 100, 2)
    return {
        "input_path": throughput_report.get("input_path"),
        "observed_attempted_calls": int(throughput_report.get("attempted_calls") or 0),
        "observed_cycle_count": len(throughput_report.get("cycles") or {}),
        "observed_calls_per_cycle": round(observed_calls, 3),
        "projection": {
            "cadence_minutes": cadence_minutes,
            "cycles_per_day": round(cycles_per_day, 3),
            "calls_per_cycle": round(projected_calls_per_cycle, 3),
            "calls_per_day": round(calls_per_day, 3),
            "llm_call_budget_per_day": llm_call_budget_per_day,
            "budget_utilization_pct": budget_utilization_pct,
            "median_wall_clock_seconds_per_day": round(median_wall_clock_seconds, 3),
            "p90_wall_clock_seconds_per_day": round(p90_wall_clock_seconds, 3),
        },
        "latency_ms": throughput_report.get("latency_ms", {}),
    }


def render_markdown(report: dict[str, Any]) -> str:
    projection = report["projection"]
    budget = projection["llm_call_budget_per_day"]
    budget_line = "not set" if budget is None else f"{budget}"
    utilization = projection["budget_utilization_pct"]
    utilization_line = "n/a" if utilization is None else f"{utilization}%"
    return "\n".join(
        [
            "# Pre-Wave-1 LLM Call Budget Projection",
            "",
            f"Input: `{report.get('input_path')}`",
            "",
            "## Observed Baseline",
            "",
            f"- Attempted calls: `{report['observed_attempted_calls']}`",
            f"- Observed cycles: `{report['observed_cycle_count']}`",
            f"- Observed calls/cycle: `{report['observed_calls_per_cycle']}`",
            "",
            "## Projection",
            "",
            f"- Cadence minutes: `{projection['cadence_minutes']}`",
            f"- Cycles/day: `{projection['cycles_per_day']}`",
            f"- Calls/cycle: `{projection['calls_per_cycle']}`",
            f"- Calls/day: `{projection['calls_per_day']}`",
            f"- Daily call budget: `{budget_line}`",
            f"- Budget utilization: `{utilization_line}`",
            f"- Median LLM wall-clock seconds/day: `{projection['median_wall_clock_seconds_per_day']}`",
            f"- P90 LLM wall-clock seconds/day: `{projection['p90_wall_clock_seconds_per_day']}`",
        ]
    ) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=_DEFAULT_INPUT)
    parser.add_argument("--throughput-json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--cadence-minutes", type=float, default=90.0)
    parser.add_argument("--calls-per-cycle", type=float)
    parser.add_argument("--llm-call-budget-per-day", type=float)
    args = parser.parse_args(argv)

    throughput = (
        _load_json(args.throughput_json)
        if args.throughput_json
        else llm_throughput_audit.analyze(args.input)
    )
    report = project(
        throughput,
        cadence_minutes=args.cadence_minutes,
        calls_per_cycle=args.calls_per_cycle,
        llm_call_budget_per_day=args.llm_call_budget_per_day,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    else:
        sys.stdout.write(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
