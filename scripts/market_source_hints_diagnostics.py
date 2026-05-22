"""
Read-only diagnostics for MarketSourceHints shadow/advisory records.

Scans MARKET_SOURCE_HINT_DIAGNOSTIC structured records from trades.jsonl and
surfaces operator-facing coverage/safety signals. This script is diagnostic
only: it must not write DB state, call Kalshi APIs/websockets/account/order
endpoints, or feed readiness/admission/trading decisions.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
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
BUCKET_DESCRIPTIONS = {
    "healthy_shadow_signal": "shadow-only records with validated source targets and no rejected labels",
    "no_validated_source_hints": "shadow-only records that produced no validated source targets",
    "rejected_source_labels_present": "records with rejected source labels that may need metadata/extraction review",
    "safety_anomaly": "non-shadow MarketSourceHints diagnostic records",
    "low_coverage": "records with no validated source targets",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize MarketSourceHints diagnostic records")
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text=(
            "Path to trade-log file or root "
            "(default: logs/trades/live/trades.jsonl; pass logs/trades/ for archive scans; "
            "use --since or --since-hours to apply a review window)"
        ),
    )
    add_since_arg(parser, help_text="Inclusive start date in YYYY-MM-DD")
    add_until_arg(parser, help_text="Inclusive end date in YYYY-MM-DD")
    parser.add_argument(
        "--since-hours",
        type=float,
        help="Rolling lookback window in hours when --since is not provided",
    )
    add_top_arg(parser, default=10, help_text="Max rows in grouped sections")
    parser.add_argument("--recent", type=int, default=10, help="Max recent diagnostic examples")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON diagnostics instead of the human-readable report",
    )
    parser.add_argument(
        "--bucket",
        choices=sorted(BUCKET_DESCRIPTIONS),
        help="Filter recent examples to one operator review bucket; aggregate counts remain unchanged",
    )
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    return parser.parse_args()


def resolve_since_filter(
    since_arg: str | None,
    since_hours: float | None,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Resolve CLI start-time filters without changing runtime behavior."""

    if since_arg:
        return parse_date_start(since_arg)
    if since_hours is None:
        return None
    if since_hours <= 0:
        raise ValueError("--since-hours must be greater than 0")
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference.astimezone(timezone.utc) - timedelta(hours=since_hours)


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


def _empty_bucket(description: str) -> dict[str, Any]:
    return {"description": description, "count": 0, "tickers": []}


def _classify_operator_review_buckets(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Classify diagnostic rows into operator review buckets only.

    These buckets summarize what humans may want to inspect next; they are not
    consumed by readiness, admission, scoring, routing, or trading behavior.
    """

    buckets = {name: _empty_bucket(description) for name, description in BUCKET_DESCRIPTIONS.items()}
    tickers_by_bucket: dict[str, set[str]] = {name: set() for name in buckets}

    def add(bucket_name: str, row: dict[str, Any]) -> None:
        buckets[bucket_name]["count"] += 1
        ticker = str(row.get("ticker") or "").strip()
        if ticker:
            tickers_by_bucket[bucket_name].add(ticker)

    for row in rows:
        shadow_only = row.get("shadow_only") is True
        target_count = int(row.get("target_count") or 0)
        rejected_label_count = int(row.get("rejected_label_count") or 0)

        if not shadow_only:
            add("safety_anomaly", row)
        if shadow_only and target_count > 0 and rejected_label_count == 0:
            add("healthy_shadow_signal", row)
        if shadow_only and target_count == 0:
            add("no_validated_source_hints", row)
        if rejected_label_count > 0:
            add("rejected_source_labels_present", row)
        if target_count == 0:
            add("low_coverage", row)

    for name, tickers in tickers_by_bucket.items():
        buckets[name]["tickers"] = sorted(tickers)
    return buckets


def _row_matches_bucket(row: dict[str, Any], bucket: str) -> bool:
    shadow_only = row.get("shadow_only") is True
    target_count = int(row.get("target_count") or 0)
    rejected_label_count = int(row.get("rejected_label_count") or 0)
    if bucket == "healthy_shadow_signal":
        return shadow_only and target_count > 0 and rejected_label_count == 0
    if bucket == "no_validated_source_hints":
        return shadow_only and target_count == 0
    if bucket == "rejected_source_labels_present":
        return rejected_label_count > 0
    if bucket == "safety_anomaly":
        return not shadow_only
    if bucket == "low_coverage":
        return target_count == 0
    raise ValueError(f"unknown operator review bucket: {bucket}")


def filter_examples_by_bucket(rows: list[dict[str, Any]], bucket: str | None) -> list[dict[str, Any]]:
    if bucket is None:
        return list(rows)
    return [row for row in rows if _row_matches_bucket(row, bucket)]


def _fmt_bucket(bucket: dict[str, Any], *, max_tickers: int = 5) -> str:
    tickers = bucket.get("tickers") or []
    ticker_text = ", ".join(tickers[:max_tickers]) if tickers else "n/a"
    extra = "" if len(tickers) <= max_tickers else f", +{len(tickers) - max_tickers} more"
    return f"{bucket['count']} — {bucket['description']} — tickers: {ticker_text}{extra}"


def _counter_to_dict(counter: Counter[str], top: int) -> dict[str, int]:
    return {key: count for key, count in counter.most_common(top)}


def _json_example(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": row["ts"].astimezone(timezone.utc).isoformat() if row.get("ts") is not None else None,
        "ticker": row.get("ticker") or "",
        "mode": row.get("mode") or "unknown",
        "shadow_only": row.get("shadow_only") is True,
        "target_count": int(row.get("target_count") or 0),
        "rejected_label_count": int(row.get("rejected_label_count") or 0),
    }


def format_json_summary(
    stats: dict[str, Any],
    top: int = 10,
    recent: int = 10,
    bucket: str | None = None,
) -> str:
    """Return machine-readable diagnostics without changing any runtime behavior."""

    top = max(1, top)
    recent = max(0, recent)
    examples = filter_examples_by_bucket(stats.get("examples") or [], bucket)
    payload = {
        "schema_version": 1,
        "diagnostic_only": True,
        "non_consumption": "not consumed by readiness/admission/scoring/routing/trading",
        "selected_bucket": bucket,
        "path": str(stats["path"]),
        "counts": {
            "lines_total": int(stats["lines_total"]),
            "lines_malformed": int(stats["lines_malformed"]),
            "records_kept": int(stats["records_kept"]),
            "diagnostic_records": int(stats["diagnostic_records"]),
            "shadow_only_records": int(stats["shadow_only_records"]),
            "non_shadow_records": int(stats["non_shadow_records"]),
            "records_with_targets": int(stats["records_with_targets"]),
            "records_with_rejected_labels": int(stats["records_with_rejected_labels"]),
            "child_shadow_records": int(stats["child_shadow_records"]),
            "target_query_count": int(stats["target_query_count"]),
            "target_feed_url_count": int(stats["target_feed_url_count"]),
        },
        "counters": {
            "by_mode": _counter_to_dict(stats["by_mode"], top),
            "by_source": _counter_to_dict(stats["by_source"], top),
            "by_source_domain": _counter_to_dict(stats["by_source_domain"], top),
            "by_ticker": _counter_to_dict(stats["by_ticker"], top),
            "by_rejected_reason": _counter_to_dict(stats["by_rejected_reason"], top),
        },
        "operator_review_buckets": stats.get("operator_review_buckets", {}),
        "examples": [_json_example(row) for row in examples[:recent]],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


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
    stats["operator_review_buckets"] = _classify_operator_review_buckets(rows)
    return stats


def print_summary(stats: dict[str, Any], top: int = 10, recent: int = 10, bucket: str | None = None) -> None:
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
    print("Operator review buckets")
    print("  Diagnostic only -- classifications do not affect readiness/admission/trading")
    for name, bucket_info in stats.get("operator_review_buckets", {}).items():
        print(f"  {name}: {_fmt_bucket(bucket_info)}")
    print()
    examples = filter_examples_by_bucket(stats.get("examples") or [], bucket)
    heading = "Recent examples" if bucket is None else f"Recent examples (bucket={bucket})"
    print(heading)
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
    try:
        since = resolve_since_filter(args.since, args.since_hours)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    stats = summarize(
        Path(args.path),
        since=since,
        until=parse_date_end(args.until),
        exclude_test=args.exclude_test,
    )
    if args.json_output:
        print(format_json_summary(stats, top=max(1, args.top), recent=max(0, args.recent), bucket=args.bucket))
    else:
        print_summary(stats, top=max(1, args.top), recent=max(0, args.recent), bucket=args.bucket)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
