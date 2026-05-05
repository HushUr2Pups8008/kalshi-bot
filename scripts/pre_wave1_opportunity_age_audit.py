"""Pre-Wave-1 OPPORTUNITY age distribution audit for OBS-005 monitoring."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_INPUT = _REPO_ROOT / "logs/trades/live/trades.jsonl"
_COUNTED_TYPES = {"OPPORTUNITY", "MATCH_DIAGNOSTIC", "EARLY_FRESH_PASS"}


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _age(row: dict[str, Any]) -> float | None:
    for key in ("age_seconds", "age_at_match_seconds", "news_age_seconds"):
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
    event_counts: Counter = Counter()
    ages = []
    for row in _rows(path):
        typ = row.get("type")
        if typ not in _COUNTED_TYPES:
            continue
        event_counts[str(typ)] += 1
        value = _age(row)
        if value is not None:
            ages.append(value)
    return {
        "input_path": str(path),
        "event_counts": dict(event_counts),
        "age_seconds": _stats(ages),
    }


def render_markdown(report: dict[str, Any]) -> str:
    stats = report["age_seconds"]
    events = [f"- `{key}`: {value}" for key, value in sorted(report["event_counts"].items())] or ["- None."]
    return "\n".join([
        "# Pre-Wave-1 Opportunity Age Audit",
        "",
        f"Input: `{report['input_path']}`",
        "",
        "## Summary",
        "",
        f"- Age samples: `{stats['count']}`",
        f"- Median age seconds: `{stats['median']}`",
        f"- P90 age seconds: `{stats['p90']}`",
        f"- Max age seconds: `{stats['max']}`",
        "",
        "## Event Counts",
        "",
        "\n".join(events),
    ]) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=_DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = analyze(args.input)
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
