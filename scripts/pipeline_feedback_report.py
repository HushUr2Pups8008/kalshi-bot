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
FRESHNESS_STAGES: tuple[str, ...] = ("EARLY_FRESH_PASS", "EARLY_STALE_DROP")
MARKET_MIX_STAGES: tuple[str, ...] = (
    "MATCH_DIAGNOSTIC",
    "MATCH_LLM_REVIEW",
    "llm_neutral",
    "llm_true_positive",
    "SIGNAL_ANALYSIS_DETAIL",
    "SIGNAL",
    "OPPORTUNITY",
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


def _market_prefix(event: dict[str, Any]) -> str:
    for key in ("market_prefix", "series_ticker", "venue"):
        prefix = event.get(key)
        if prefix:
            return str(prefix)
    ticker = event.get("ticker") or event.get("market_ticker") or ""
    return str(ticker).split("-", 1)[0] if ticker else "unknown"


def _filled_market_mix_counts(counter: Counter[str]) -> dict[str, int]:
    base = Counter({stage: 0 for stage in MARKET_MIX_STAGES})
    base.update(counter)
    return dict(base)


def summarize_events(paths: Iterable[Path], *, top_n: int = 10) -> dict[str, Any]:
    stage_counts = Counter({stage: 0 for stage in FUNNEL_STAGES})
    reasons: Counter[str] = Counter()
    tickers: Counter[str] = Counter()
    freshness_totals = Counter({stage: 0 for stage in FRESHNESS_STAGES})
    freshness_by_source: dict[str, Counter[str]] = defaultdict(Counter)
    freshness_by_source_class: dict[str, Counter[str]] = defaultdict(Counter)
    freshness_reasons: Counter[str] = Counter()
    market_by_prefix: dict[str, Counter[str]] = defaultdict(Counter)
    market_by_source_class: dict[str, Counter[str]] = defaultdict(Counter)

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

        if event_type in freshness_totals:
            freshness_totals[event_type] += 1
            source = event.get("source") or "unknown"
            source_class = event.get("source_class") or event.get("source_type") or "unknown"
            freshness_by_source[source][event_type] += 1
            freshness_by_source_class[source_class][event_type] += 1
            if reason:
                freshness_reasons[f"{event_type}:{reason}"] += 1

        if event_type in {
            "MATCH_DIAGNOSTIC",
            "MATCH_LLM_REVIEW",
            "SIGNAL_ANALYSIS_DETAIL",
            "SIGNAL",
            "OPPORTUNITY",
        }:
            prefix = _market_prefix(event)
            source_class = event.get("source_class") or event.get("source_type") or "unknown"
            market_by_prefix[prefix][event_type] += 1
            market_by_source_class[source_class][event_type] += 1
            if event_type == "MATCH_LLM_REVIEW":
                verdict = event.get("verdict")
                if verdict == "false_positive_neutral":
                    market_by_prefix[prefix]["llm_neutral"] += 1
                    market_by_source_class[source_class]["llm_neutral"] += 1
                elif verdict == "true_positive":
                    market_by_prefix[prefix]["llm_true_positive"] += 1
                    market_by_source_class[source_class]["llm_true_positive"] += 1

    return {
        "funnel": {
            "stage_counts": dict(stage_counts),
            "top_reasons": _top(reasons, top_n),
            "top_tickers": _top(tickers, top_n),
        },
        "freshness": {
            "totals": dict(freshness_totals),
            "by_source": {
                key: dict(Counter({stage: 0 for stage in FRESHNESS_STAGES}) | value)
                for key, value in sorted(freshness_by_source.items())
            },
            "by_source_class": {
                key: dict(Counter({stage: 0 for stage in FRESHNESS_STAGES}) | value)
                for key, value in sorted(freshness_by_source_class.items())
            },
            "top_reasons": _top(freshness_reasons, top_n),
        },
        "market_mix": {
            "by_prefix": {
                key: _filled_market_mix_counts(value)
                for key, value in sorted(market_by_prefix.items())
            },
            "by_source_class": {
                key: _filled_market_mix_counts(value)
                for key, value in sorted(market_by_source_class.items())
            },
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
