"""Audit day-by-day source-class distribution from trade-log source labels."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from main import _source_class_for_evidence

_DEFAULT_INPUT = _REPO_ROOT / "logs/trades/live/trades.jsonl"
_COUNTED_TYPES = {
    "EARLY_FRESH_PASS",
    "MATCH_DIAGNOSTIC",
    "SIGNAL_ANALYSIS_DETAIL",
    "ANALYSIS_REJECTED",
    "MATCH_SUPPRESSED",
    "PAPER_TRADE",
}


def _parse_day(row: dict[str, Any]) -> str:
    value = str(row.get("ts") or row.get("ingested_ts") or "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    return parsed.astimezone(timezone.utc).date().isoformat()


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def analyze(path: Path = _DEFAULT_INPUT) -> dict[str, Any]:
    counted_rows = 0
    per_day: dict[str, Counter] = defaultdict(Counter)
    event_types: Counter = Counter()
    for row in _iter_jsonl(path):
        typ = row.get("type")
        if typ not in _COUNTED_TYPES:
            continue
        source = row.get("source")
        if not source:
            continue
        day = _parse_day(row)
        cls = _source_class_for_evidence(str(source))
        per_day[day][cls] += 1
        event_types[str(typ)] += 1
        counted_rows += 1
    return {
        "input_path": str(path),
        "total_rows": counted_rows,
        "counted_event_types": sorted(_COUNTED_TYPES),
        "event_type_counts": dict(event_types),
        "per_day": {day: dict(counter) for day, counter in sorted(per_day.items())},
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Source-Class Evolution Audit",
        "",
        f"Input: `{report['input_path']}`",
        "",
        "## Summary",
        "",
        f"- Counted rows: `{report['total_rows']}`",
        "",
        "## Event Types",
        "",
    ]
    if not report["event_type_counts"]:
        lines.append("- None.")
    for event_type, count in sorted(report["event_type_counts"].items()):
        lines.append(f"- `{event_type}`: {count}")
    lines += [""]
    lines += ["## Per-Day Distribution", ""]
    if not report["per_day"]:
        lines.append("- None.")
    for day, counts in report["per_day"].items():
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        lines.append(f"- `{day}`: {rendered}")
    return "\n".join(lines) + "\n"


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
