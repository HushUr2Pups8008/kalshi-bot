"""
Simple read-only summary tool for structured trade logs.

Reads the preferred trade-log root at logs/trades/ and prints:
  - event counts by type
  - skip reason counts and top reasons
  - source breakdown
  - ticker breakdown
  - signal type split (news / fade_tweet / price_fade)

Usage:
  python scripts/trade_log_summary.py
  python scripts/trade_log_summary.py --path logs/trades/
  python scripts/trade_log_summary.py --since 2026-04-01 --until 2026-04-11

The preferred source is logs/trades/. The legacy monolithic
logs/trades/trades.jsonl path is still supported during the cutover
validation window.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.diagnostics_script_helpers import (
    add_exclude_test_arg,
    add_path_arg,
    add_since_arg,
    add_top_arg,
    add_until_arg,
    is_test_record_source_or_signal_source as is_test_record,
    parse_date_end,
    parse_date_start,
    parse_iso_ts,  # re-exported for scripts.validate_trade_log_cutover
)

# scripts/validate_trade_log_cutover.py imports parse_iso_ts via
# `scripts.trade_log_summary.parse_iso_ts` dotted access; the re-export must
# stay even if ruff F401 flags it as unused in this module.
__all__ = ["parse_iso_ts"]
from utils.diagnostic_reporting_helpers import format_counter, print_standard_trade_log_header
from utils.reporting_helpers import warn_if_full_trade_root_scan
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize kalshi-bot trade logs")
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text="Path to trade-log file or root (default: logs/trades/; legacy logs/trades/trades.jsonl still supported)",
    )
    add_since_arg(parser, help_text="Inclusive start date in YYYY-MM-DD")
    add_until_arg(parser, help_text="Inclusive end date in YYYY-MM-DD")
    add_top_arg(parser, default=10, help_text="Max rows to show in top breakdowns (default: 10)")
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    return parser.parse_args()


def infer_signal_type(record: dict[str, Any]) -> str:
    explicit = record.get("signal_type")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    source = str(record.get("source") or record.get("signal_source") or "").strip().lower()
    url = str(record.get("url") or "").strip().lower()
    reasoning = str(record.get("reasoning") or "").strip().upper()
    headline = str(record.get("headline") or record.get("signal_headline") or "").strip().upper()

    if source == "price_fade" or url.startswith("kalshi://price_fade/") or "[PRICE_FADE]" in reasoning or headline.startswith("[PRICE_FADE]"):
        return "price_fade"
    if "[FADE/" in reasoning:
        return "fade_tweet"
    return "news"


def stale_age_bucket(age_seconds: Any) -> str | None:
    if not isinstance(age_seconds, (int, float)):
        return None
    if age_seconds < 0:
        return None
    if age_seconds < 5 * 60:
        return "0-5m"
    if age_seconds < 15 * 60:
        return "5-15m"
    if age_seconds < 30 * 60:
        return "15-30m"
    if age_seconds < 60 * 60:
        return "30-60m"
    return "60m+"


def summarize(path: Path, since: datetime | None, until: datetime | None, exclude_test: bool = False) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": path,
        "lines_total": 0,
        "lines_malformed": 0,
        "records_kept": 0,
        "event_counts": Counter(),
        "skip_reasons": Counter(),
        "analysis_rejected_reasons": Counter(),
        "stale_news_age_buckets": Counter(),
        "stale_news_sources": Counter(),
        "stale_news_tickers": Counter(),
        "sources": Counter(),
        "tickers": Counter(),
        "signal_types": Counter(),
    }

    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if exclude_test and is_test_record(record):
            continue

        stats["records_kept"] += 1

        event_type = str(record.get("type") or "UNKNOWN").strip() or "UNKNOWN"
        stats["event_counts"][event_type] += 1

        source = str(record.get("source") or record.get("signal_source") or "").strip()
        if source:
            stats["sources"][source] += 1

        ticker = str(record.get("ticker") or "").strip()
        if ticker:
            stats["tickers"][ticker] += 1

        if event_type == "SKIPPED":
            reason = str(record.get("reason") or "unknown").strip() or "unknown"
            stats["skip_reasons"][reason] += 1
        elif event_type == "ANALYSIS_REJECTED":
            reason = str(record.get("reason") or "unknown").strip() or "unknown"
            stats["analysis_rejected_reasons"][reason] += 1
            if reason == "stale_news":
                bucket = stale_age_bucket(record.get("age_seconds"))
                if bucket:
                    stats["stale_news_age_buckets"][bucket] += 1
                if source:
                    stats["stale_news_sources"][source] += 1
                if ticker:
                    stats["stale_news_tickers"][ticker] += 1

        stats["signal_types"][infer_signal_type(record)] += 1

    stats["lines_total"] = read_stats.lines_total
    stats["lines_malformed"] = read_stats.lines_malformed

    return stats


def print_summary(stats: dict[str, Any], top: int, since: datetime | None, until: datetime | None) -> None:
    print_standard_trade_log_header(
        title="TRADE LOG SUMMARY",
        path=stats["path"],
        since=since,
        until=until,
        lines_total=stats["lines_total"],
        lines_malformed=stats["lines_malformed"],
        records_kept=stats["records_kept"],
    )

    if stats["records_kept"] == 0:
        print()
        print("No matching log records found.")
        return

    print()
    print("Event Counts")
    for line in format_counter(stats["event_counts"], top=max(top, len(stats["event_counts"]))):
        print(line)

    print()
    print("Executor Skip Reasons")
    if stats["skip_reasons"]:
        total_skips = sum(stats["skip_reasons"].values())
        print(f"  Total skipped events: {total_skips}")
    for line in format_counter(stats["skip_reasons"], top=top):
        print(line)

    print()
    print("Analysis Rejection Reasons")
    if stats["analysis_rejected_reasons"]:
        total_rejections = sum(stats["analysis_rejected_reasons"].values())
        print(f"  Total analysis rejections: {total_rejections}")
    for line in format_counter(stats["analysis_rejected_reasons"], top=top):
        print(line)

    print()
    print("Stale-News Age Buckets")
    for line in format_counter(stats["stale_news_age_buckets"], top=max(top, len(stats["stale_news_age_buckets"]))):
        print(line)

    print()
    print(f"Stale-News Sources (top {top})")
    for line in format_counter(stats["stale_news_sources"], top=top):
        print(line)

    print()
    print(f"Stale-News Tickers (top {top})")
    for line in format_counter(stats["stale_news_tickers"], top=top):
        print(line)

    print()
    print(f"Top Sources (top {top})")
    for line in format_counter(stats["sources"], top=top):
        print(line)

    print()
    print(f"Top Tickers (top {top})")
    for line in format_counter(stats["tickers"], top=top):
        print(line)

    print()
    print("Signal Type Split")
    for line in format_counter(stats["signal_types"], top=max(top, len(stats["signal_types"]))):
        print(line)


def main() -> int:
    args = parse_args()

    try:
        since = parse_date_start(args.since)
        until = parse_date_end(args.until)
    except ValueError as exc:
        raise SystemExit(f"Invalid date: {exc}") from exc

    if since and until and since > until:
        raise SystemExit("--since must be on or before --until")

    path = Path(args.path)
    warn_if_full_trade_root_scan(path, since=since, until=until)
    stats = summarize(path, since, until, exclude_test=args.exclude_test)
    print_summary(stats, top=max(1, args.top), since=since, until=until)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
