#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


def _day(row: dict) -> str | None:
    raw = row.get("decided_at") or row.get("ts") or row.get("evaluate_at")
    if not raw:
        return None
    return str(raw)[:10]


def audit(path: Path, daily_budget: int, min_fraction: float) -> dict:
    counts: Counter[str] = Counter()
    if path.exists():
        for line in path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") == "GOVERNANCE_DECISION":
                day = _day(row)
                if day:
                    counts[day] += 1
    total = sum(counts.values())
    reviewable = sum(min(count, daily_budget) for count in counts.values())
    fraction = (reviewable / total) if total else 1.0
    return {
        "status": "pass" if fraction >= min_fraction else "fail",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "daily_budget": daily_budget,
        "min_fraction": min_fraction,
        "total_decisions": total,
        "reviewable_decisions": reviewable,
        "reviewable_fraction": fraction,
        "per_day": dict(sorted(counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="logs/governance/decisions.jsonl")
    parser.add_argument("--daily-budget", type=int, default=80)
    parser.add_argument("--min-fraction", type=float, default=0.85)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = audit(Path(args.log), args.daily_budget, args.min_fraction)
    if args.json:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print("# Manual review capacity audit")
        print()
        print(f"Status: {result['status']}")
        print(f"- total_decisions: {result['total_decisions']}")
        print(f"- reviewable_decisions: {result['reviewable_decisions']}")
        print(f"- reviewable_fraction: {result['reviewable_fraction']:.3f}")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
