"""
Simple read-only summary tool for structured trade logs.

Reads logs/trades/trades.jsonl and prints:
  - event counts by type
  - skip reason counts and top reasons
  - source breakdown
  - ticker breakdown
  - signal type split (news / fade_tweet / price_fade)

Usage:
  python scripts/trade_log_summary.py
  python scripts/trade_log_summary.py --path logs/trades/trades.jsonl
  python scripts/trade_log_summary.py --since 2026-04-01 --until 2026-04-11
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades" / "trades.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize kalshi-bot trade logs")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_LOG_PATH),
        help="Path to trades.jsonl (default: logs/trades/trades.jsonl)",
    )
    parser.add_argument(
        "--since",
        help="Inclusive start date in YYYY-MM-DD",
    )
    parser.add_argument(
        "--until",
        help="Inclusive end date in YYYY-MM-DD",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Max rows to show in top breakdowns (default: 10)",
    )
    parser.add_argument(
        "--exclude-test",
        action="store_true",
        help="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    return parser.parse_args()


def parse_iso_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date_start(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    return dt.replace(tzinfo=timezone.utc)


def parse_date_end(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    return datetime.combine(dt.date(), time.max, tzinfo=timezone.utc)


def in_window(ts: datetime | None, since: datetime | None, until: datetime | None) -> bool:
    if ts is None:
        return since is None and until is None
    if since is not None and ts < since:
        return False
    if until is not None and ts > until:
        return False
    return True


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


def format_counter(counter: Counter[str], top: int) -> list[str]:
    if not counter:
        return ["  (none)"]
    width = max(len(str(count)) for count in counter.values())
    return [f"  {count:>{width}}  {label}" for label, count in counter.most_common(top)]


def is_test_record(record: dict[str, Any]) -> bool:
    source = str(record.get("source") or record.get("signal_source") or "").lower()
    ticker = str(record.get("ticker") or "").upper()
    return "r/test" in source or "KXTEST" in ticker


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

    if not path.exists():
        return stats

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            stats["lines_total"] += 1
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                stats["lines_malformed"] += 1
                continue
            if not isinstance(record, dict):
                stats["lines_malformed"] += 1
                continue

            ts = parse_iso_ts(record.get("ts"))
            if not in_window(ts, since, until):
                continue
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

    return stats


def print_summary(stats: dict[str, Any], top: int, since: datetime | None, until: datetime | None) -> None:
    print("TRADE LOG SUMMARY")
    print(f"Path: {stats['path']}")
    if since or until:
        since_text = since.date().isoformat() if since else "(beginning)"
        until_text = until.date().isoformat() if until else "(latest)"
        print(f"Date range: {since_text} -> {until_text}")
    print(f"Lines read: {stats['lines_total']}")
    print(f"Malformed lines skipped: {stats['lines_malformed']}")
    print(f"Records included: {stats['records_kept']}")

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
    stats = summarize(path, since, until, exclude_test=args.exclude_test)
    print_summary(stats, top=max(1, args.top), since=since, until=until)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
