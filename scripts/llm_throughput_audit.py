"""Audit per-cycle LLM-call latency distribution from trade logs."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_INPUT = _REPO_ROOT / "logs/trades/live/trades.jsonl"


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _latency(row: dict[str, Any]) -> float | None:
    for key in ("llm_latency_ms", "llm_total_stage_ms", "llm_http_round_trip_ms"):
        value = row.get(key)
        if isinstance(value, int | float):
            return float(value)
    return None


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "p90": None, "max": None}
    ordered = sorted(values)
    p90_index = min(len(ordered) - 1, int(0.9 * (len(ordered) - 1)))
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": statistics.median(ordered),
        "p90": ordered[p90_index],
        "max": ordered[-1],
    }


def analyze(path: Path = _DEFAULT_INPUT) -> dict[str, Any]:
    rows = _iter_jsonl(path)
    attempted = [
        row for row in rows
        if row.get("type") == "SIGNAL_ANALYSIS_DETAIL" and row.get("llm_attempted") is True
    ]
    latencies = [value for row in attempted if (value := _latency(row)) is not None]
    by_cycle: dict[str, list[float]] = defaultdict(list)
    call_counts: dict[str, int] = defaultdict(int)
    for row in attempted:
        cycle_id = str(row.get("cycle_id") or row.get("batch_id") or "unknown")
        call_counts[cycle_id] += 1
        value = _latency(row)
        if value is not None:
            by_cycle[cycle_id].append(value)
    return {
        "input_path": str(path),
        "attempted_calls": len(attempted),
        "latency_ms": _stats(latencies),
        "cycles": {
            cycle_id: {
                "attempted_calls": call_counts[cycle_id],
                "latency_ms": _stats(values),
            }
            for cycle_id, values in sorted(by_cycle.items())
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    stats = report["latency_ms"]
    lines = [
        "# LLM Throughput Audit",
        "",
        f"Input: `{report['input_path']}`",
        "",
        "## Summary",
        "",
        f"- Attempted calls: `{report['attempted_calls']}`",
        f"- Median latency ms: `{stats['median']}`",
        f"- P90 latency ms: `{stats['p90']}`",
        f"- Max latency ms: `{stats['max']}`",
        "",
        "## Per-Cycle",
        "",
    ]
    if not report["cycles"]:
        lines.append("- None.")
    for cycle_id, row in report["cycles"].items():
        lines.append(
            f"- `{cycle_id}`: calls={row['attempted_calls']} "
            f"median_ms={row['latency_ms']['median']} p90_ms={row['latency_ms']['p90']}"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=_DEFAULT_INPUT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = analyze(args.input)
    if args.json:
        print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    else:
        sys.stdout.write(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
