"""
Read-only diagnostics for MarketSourceHints shadow/advisory records.

Scans MARKET_SOURCE_HINT_DIAGNOSTIC structured records from trades.jsonl and
surfaces operator-facing coverage/safety signals. This script is diagnostic
only: it must not write DB state, call Kalshi APIs/websockets/account/order
endpoints, or feed readiness/admission/trading decisions.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.diagnostics_script_helpers import (
    add_exclude_test_arg,
    add_path_arg,
    add_since_arg,
    add_top_arg,
    add_until_arg,
    is_test_record_source_only as is_test_record,
    parse_date_end,
    parse_date_start,
    parse_iso_ts,
)
from utils.reporting_helpers import DEFAULT_CURRENT_STATE_WINDOW_HOURS
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records

DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades" / "live" / "trades.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize MarketSourceHints diagnostic records")
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text=(
            "Path to trade-log file or root "
            "(default: logs/trades/live/trades.jsonl; pass logs/trades/ for archive scans; "
            f"default window: last {DEFAULT_CURRENT_STATE_WINDOW_HOURS} hours when dates omitted)"
        ),
    )
    add_since_arg(parser, help_text="Inclusive start date in YYYY-MM-DD")
    add_until_arg(parser, help_text="Inclusive end date in YYYY-MM-DD")
    add_top_arg(parser, default=10, help_text="Max rows in grouped sections")
    parser.add_argument("--recent", type=int, default=10, help="Max recent diagnostic examples")
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    return parser.parse_args()


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _fmt_ts(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_counter(counter: Counter[str], top: int) -> str:
    if not counter:
        return "n/a"
    return ", ".join(f"{key}({count})" for key, count in counter.most_common(top))


def _iter_targets(record: dict[str, Any]) -> list[dict[str, Any]]:
    targets = record.get("targets") or []
    return [target for target in targets if isinstance(target, dict)]


def _rejected_reasons(record: dict[str, Any]) -> list[str]:
    rejected = record.get("rejected_labels") or {}
    if not isinstance(rejected, dict):
        return []
    return [str(reason or "unknown") for reason in rejected.values()]


def summarize(
    path: Path,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool = False,
    *,
    progress_tracker=None,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": path,
        "lines_total": 0,
        "lines_malformed": 0,
        "records_kept": 0,
        "diagnostic_records": 0,
        "shadow_only_records": 0,
        "non_shadow_records": 0,
        "records_with_targets": 0,
        "records_with_rejected_labels": 0,
        "child_shadow_records": 0,
        "target_query_count": 0,
        "target_feed_url_count": 0,
        "by_mode": Counter(),
        "by_source": Counter(),
        "by_source_domain": Counter(),
        "by_ticker": Counter(),
        "by_rejected_reason": Counter(),
        "examples": [],
    }
    rows: list[dict[str, Any]] = []
    read_stats = TradeLogReadStats()

    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if progress_tracker is not None:
            progress_tracker.tick()
        if exclude_test and is_test_record(record):
            continue
        stats["records_kept"] += 1
        if str(record.get("type") or "").strip() != "MARKET_SOURCE_HINT_DIAGNOSTIC":
            continue

        stats["diagnostic_records"] += 1
        if record.get("shadow_only") is True:
            stats["shadow_only_records"] += 1
        else:
            stats["non_shadow_records"] += 1

        mode = str(record.get("mode") or "unknown").strip() or "unknown"
        ticker = str(record.get("ticker") or "").strip()
        targets = _iter_targets(record)
        rejected_reasons = _rejected_reasons(record)
        child_records = [child for child in record.get("log_records") or [] if isinstance(child, dict)]

        stats["by_mode"][mode] += 1
        if ticker:
            stats["by_ticker"][ticker] += 1
        if targets:
            stats["records_with_targets"] += 1
        if rejected_reasons:
            stats["records_with_rejected_labels"] += 1
        for reason in rejected_reasons:
            stats["by_rejected_reason"][reason] += 1
        for target in targets:
            source = str(target.get("source") or "").strip()
            domain = str(target.get("domain") or "").strip()
            if source:
                stats["by_source"][source] += 1
            if domain:
                stats["by_source_domain"][domain] += 1
            stats["target_query_count"] += _safe_int(target.get("query_count"))
            stats["target_feed_url_count"] += _safe_int(target.get("feed_url_count"))
        for child in child_records:
            if child.get("shadow_only") is True:
                stats["child_shadow_records"] += 1

        rows.append(
            {
                "ts": parse_iso_ts(record.get("ts")),
                "ticker": ticker,
                "mode": mode,
                "shadow_only": record.get("shadow_only") is True,
                "target_count": len(targets),
                "rejected_label_count": len(rejected_reasons),
            }
        )

    stats["lines_total"] = read_stats.lines_total
    stats["lines_malformed"] = read_stats.lines_malformed
    stats["examples"] = sorted(
        rows,
        key=lambda row: row["ts"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return stats


def print_summary(stats: dict[str, Any], top: int = 10, recent: int = 10) -> None:
    total = int(stats["diagnostic_records"])
    shadow = int(stats["shadow_only_records"])
    shadow_pct = (100 * shadow / total) if total else 0.0

    print("MarketSourceHints Diagnostics")
    print("Diagnostic only -- not consumed by readiness/admission/trading")
    print(f"Path:                     {stats['path']}")
    print(f"Lines scanned:            {stats['lines_total']}")
    print(f"Malformed lines skipped:  {stats['lines_malformed']}")
    print(f"Records kept:             {stats['records_kept']}")
    print(f"Diagnostic records:       {total}")
    print(f"Shadow-only records:      {shadow} ({shadow_pct:.1f}%)")
    print(f"Non-shadow records:       {stats['non_shadow_records']}")
    if stats["non_shadow_records"]:
        print("SAFETY WARNING: non-shadow MarketSourceHints diagnostic records observed")
    print(f"Records with targets:     {stats['records_with_targets']}")
    print(f"Records with rejections:  {stats['records_with_rejected_labels']}")
    print(f"Child shadow records:     {stats['child_shadow_records']}")
    print(f"Target query count:       {stats['target_query_count']}")
    print(f"Target feed URL count:    {stats['target_feed_url_count']}")
    print()
    print("Modes observed")
    print(f"  {_fmt_counter(stats['by_mode'], top)}")
    print("Top hinted sources")
    print(f"  {_fmt_counter(stats['by_source'], top)}")
    print("Top hinted source domains")
    print(f"  {_fmt_counter(stats['by_source_domain'], top)}")
    print("Top tickers")
    print(f"  {_fmt_counter(stats['by_ticker'], top)}")
    print("Rejected label reasons")
    print(f"  {_fmt_counter(stats['by_rejected_reason'], top)}")
    print()
    print("Recent examples")
    examples = stats.get("examples") or []
    if not examples:
        print("  n/a")
        return
    for row in examples[:recent]:
        print(
            "  "
            f"{_fmt_ts(row['ts'])} ticker={row['ticker'] or 'n/a'} "
            f"mode={row['mode']} shadow_only={row['shadow_only']} "
            f"targets={row['target_count']} rejected={row['rejected_label_count']}"
        )


def main() -> int:
    args = parse_args()
    stats = summarize(
        Path(args.path),
        since=parse_date_start(args.since),
        until=parse_date_end(args.until),
        exclude_test=args.exclude_test,
    )
    print_summary(stats, top=max(1, args.top), recent=max(0, args.recent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
