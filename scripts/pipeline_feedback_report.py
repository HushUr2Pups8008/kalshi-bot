#!/usr/bin/env python3
"""Summarize rolling paper-trade pipeline feedback from trade JSONL logs."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

FUNNEL_STAGES: tuple[str, ...] = (
    "EARLY_FRESH_PASS",
    "MATCH_DIAGNOSTIC",
    "MATCH_WEIGHT_APPLIED",
    "MATCH_LLM_REVIEW",
    "SIGNAL_ANALYSIS_DETAIL",
    "SIGNAL",
    "OPPORTUNITY",
    "BLEND_DECISION",
    "SKIPPED",
    "PAPER_TRADE",
)


def _iter_events(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event


def _top(counter: Counter[str], limit: int = 10) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count}
        for key, count in counter.most_common(limit)
    ]


def summarize_events(paths: Iterable[Path], *, top_n: int = 10) -> dict[str, Any]:
    stage_counts = Counter({stage: 0 for stage in FUNNEL_STAGES})
    reasons: Counter[str] = Counter()
    tickers: Counter[str] = Counter()

    for event in _iter_events(paths):
        event_type = event.get("type")
        if event_type in stage_counts:
            stage_counts[event_type] += 1

        reason = (
            event.get("reason")
            or event.get("skip_reason")
            or event.get("rejection_reason")
            or event.get("verdict")
        )
        if event_type and reason:
            reasons[f"{event_type}:{reason}"] += 1

        ticker = event.get("ticker") or event.get("market_ticker")
        if ticker and event_type in FUNNEL_STAGES:
            tickers[ticker] += 1

    return {
        "funnel": {
            "stage_counts": dict(stage_counts),
            "top_reasons": _top(reasons, top_n),
            "top_tickers": _top(tickers, top_n),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Trade JSONL log paths")
    parser.add_argument("--top", type=int, default=10, help="Rows per top-N section")
    args = parser.parse_args(argv)

    print(json.dumps(summarize_events(args.paths, top_n=args.top), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
