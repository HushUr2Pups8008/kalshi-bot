"""
Concise read-only decision funnel summary for structured trade logs.

Uses the preferred trade-log root at logs/trades/ to show what is directly observable from the
structured event stream:
  - total records considered
  - signal / opportunity counts where present
  - executor skips by reason
  - executions (paper trades / live orders)
  - news vs fade path contributions where inferable

This tool does not modify runtime or trading behavior.
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
    in_window,
    is_test_record_source_or_signal_source as is_test_record,
    parse_date_end,
    parse_date_start,
    parse_iso_ts,
)
from utils.diagnostic_reporting_helpers import format_counter, print_standard_trade_log_header
from utils.reporting_helpers import DEFAULT_CURRENT_STATE_WINDOW_HOURS, resolve_recent_window
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records


REPO_ROOT = Path(__file__).resolve().parent.parent
# Default to the active live file -- the funnel summary is a current-run metric.
# Pass --path logs/trades to include archive data for multi-day analysis.
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades" / "live" / "trades.jsonl"
DECISION_EVENT_TYPES = {"ANALYSIS_REJECTED", "SIGNAL", "OPPORTUNITY", "SKIPPED", "PAPER_TRADE", "LIVE_ORDER"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize kalshi-bot decision funnel")
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text=(
            "Path to trade-log file or root "
            "(default: logs/trades/live/trades.jsonl; pass logs/trades/ for archive scans; "
            f"default window: last {DEFAULT_CURRENT_STATE_WINDOW_HOURS} hours when dates omitted; "
            "legacy logs/trades/trades.jsonl still supported)"
        ),
    )
    add_since_arg(parser, help_text="Inclusive start date in YYYY-MM-DD")
    add_until_arg(
        parser,
        help_text=f"Inclusive end date in YYYY-MM-DD (default: now when both dates omitted; last {DEFAULT_CURRENT_STATE_WINDOW_HOURS} hours)",
    )
    add_top_arg(parser, default=5, help_text="Max rows to show in top breakdowns (default: 5)")
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


def pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{(100.0 * numerator / denominator):.1f}%"


def summarize(path: Path, since: datetime | None, until: datetime | None, exclude_test: bool = False, *, progress_tracker=None) -> dict[str, Any]:
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
        "path_counts": Counter(),
        "execution_paths": Counter(),
    }

    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if progress_tracker is not None:
            progress_tracker.tick()
        if exclude_test and is_test_record(record):
            continue

        stats["records_kept"] += 1
        event_type = str(record.get("type") or "UNKNOWN").strip() or "UNKNOWN"
        stats["event_counts"][event_type] += 1

        signal_type = infer_signal_type(record)
        if event_type in DECISION_EVENT_TYPES:
            stats["path_counts"][signal_type] += 1

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
                source = str(record.get("source") or record.get("signal_source") or "").strip()
                if source:
                    stats["stale_news_sources"][source] += 1
                ticker = str(record.get("ticker") or "").strip()
                if ticker:
                    stats["stale_news_tickers"][ticker] += 1
        if event_type in {"PAPER_TRADE", "LIVE_ORDER"}:
            stats["execution_paths"][signal_type] += 1

    stats["lines_total"] = read_stats.lines_total
    stats["lines_malformed"] = read_stats.lines_malformed

    return stats


def print_summary(stats: dict[str, Any], top: int, since: datetime | None, until: datetime | None) -> None:
    print_standard_trade_log_header(
        title="DECISION FUNNEL SUMMARY",
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

    signals = stats["event_counts"].get("SIGNAL", 0)
    analysis_rejected = stats["event_counts"].get("ANALYSIS_REJECTED", 0)
    opportunities = stats["event_counts"].get("OPPORTUNITY", 0)
    skipped = stats["event_counts"].get("SKIPPED", 0)
    paper_trades = stats["event_counts"].get("PAPER_TRADE", 0)
    live_orders = stats["event_counts"].get("LIVE_ORDER", 0)
    executions = paper_trades + live_orders
    comparable_window = opportunities > 0 and skipped <= opportunities and executions <= opportunities

    print()
    print("Funnel")
    print(f"  Structured records considered : {stats['records_kept']}")
    print(f"  Pre-signal rejections         : {analysis_rejected}")
    print(f"  Signals logged                : {signals}")
    print(f"  Opportunities logged          : {opportunities} ({pct(opportunities, signals)} of signals)")
    if analysis_rejected:
        print("  No-signal exits               : partially observable via ANALYSIS_REJECTED")
    else:
        print("  No-signal exits               : not directly observable in trades.jsonl")
    if comparable_window:
        print(f"  Executor skips               : {skipped} ({pct(skipped, opportunities)} of opportunities)")
        print(f"  Executions                   : {executions} ({pct(executions, opportunities)} of opportunities)")
    else:
        print(f"  Executor skips               : {skipped}")
        print(f"  Executions                   : {executions}")
        print("  Note                         : window mixes stages from different decision lifecycles")
    if paper_trades:
        print(f"    Paper trades               : {paper_trades}")
    if live_orders:
        print(f"    Live orders                : {live_orders}")

    print()
    print("Pre-Signal Rejection Reasons")
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
    print("Executor Skip Reasons")
    for line in format_counter(stats["skip_reasons"], top=top):
        print(line)

    print()
    print("Path Contribution (decision-stage records)")
    for line in format_counter(stats["path_counts"], top=max(top, len(stats["path_counts"]))):
        print(line)

    print()
    print("Execution Path Contribution")
    for line in format_counter(stats["execution_paths"], top=max(top, len(stats["execution_paths"]))):
        print(line)

    print()
    print("Observed Event Types")
    for line in format_counter(stats["event_counts"], top=max(top, len(stats["event_counts"]))):
        print(line)


def main() -> int:
    args = parse_args()

    try:
        since_input = parse_date_start(args.since)
        until_input = parse_date_end(args.until)
    except ValueError as exc:
        raise SystemExit(f"Invalid date: {exc}") from exc

    since, until, _ = resolve_recent_window(since_input, until_input)

    if since and until and since > until:
        raise SystemExit("--since must be on or before --until")

    stats = summarize(Path(args.path), since, until, exclude_test=args.exclude_test)
    print_summary(stats, top=max(1, args.top), since=since, until=until)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
