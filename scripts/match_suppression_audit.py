"""
Read-only audit for MATCH_SUPPRESSION_CANDIDATE events.

Classifies suppression candidates into likely safe-to-suppress vs risky-to-suppress
using a simple first-pass proxy:

  - safe:  none of the matched_tokens appear in the ticker string
  - risky: at least one matched token appears in the ticker string
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from utils.diagnostic_reporting_helpers import fmt_pct, print_standard_trade_log_header
from utils.reporting_helpers import warn_if_full_trade_root_scan
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit MATCH_SUPPRESSION_CANDIDATE events")
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text="Path to trade-log file or root (default: logs/trades/; legacy logs/trades/trades.jsonl still supported)",
    )
    add_since_arg(parser, help_text="Inclusive start date in YYYY-MM-DD")
    add_until_arg(parser, help_text="Inclusive end date in YYYY-MM-DD")
    add_top_arg(parser, default=10, help_text="Max rows to show in grouped sections")
    parser.add_argument("--recent", type=int, default=10, help="Max examples to show per section")
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    return parser.parse_args()


def classify_candidate(record: dict[str, Any]) -> str:
    ticker = str(record.get("ticker") or "").lower()
    matched_tokens = [str(token).lower() for token in (record.get("matched_tokens") or [])]
    for token in matched_tokens:
        if token and token in ticker:
            return "risky"
    return "safe"


def fmt_ts(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def summarize(path: Path, since: datetime | None, until: datetime | None, exclude_test: bool = False) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": path,
        "lines_total": 0,
        "lines_malformed": 0,
        "records_kept": 0,
        "total_candidates": 0,
        "safe_count": 0,
        "risky_count": 0,
        "by_source": defaultdict(lambda: Counter({"safe": 0, "risky": 0})),
        "by_ticker": defaultdict(lambda: Counter({"safe": 0, "risky": 0})),
        "safe_examples": [],
        "risky_examples": [],
    }

    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if exclude_test and is_test_record(record):
            continue

        stats["records_kept"] += 1
        if str(record.get("type") or "").strip() != "MATCH_SUPPRESSION_CANDIDATE":
            continue

        classification = classify_candidate(record)
        stats["total_candidates"] += 1
        if classification == "safe":
            stats["safe_count"] += 1
        else:
            stats["risky_count"] += 1

        source = str(record.get("source") or "").strip()
        ticker = str(record.get("ticker") or "").strip()
        if source:
            stats["by_source"][source][classification] += 1
        if ticker:
            stats["by_ticker"][ticker][classification] += 1

        row = {
            "ts": parse_iso_ts(record.get("ts")),
            "source": source,
            "headline": str(record.get("headline") or "").strip(),
            "ticker": ticker,
            "match_score": record.get("match_score"),
            "overlap_count": record.get("overlap_count"),
            "overlap_ratio": record.get("overlap_ratio"),
            "heuristic_flags": list(record.get("heuristic_flags") or []),
            "matched_tokens": list(record.get("matched_tokens") or []),
        }
        if classification == "safe":
            stats["safe_examples"].append(row)
        else:
            stats["risky_examples"].append(row)

    stats["lines_total"] = read_stats.lines_total
    stats["lines_malformed"] = read_stats.lines_malformed

    stats["safe_examples"].sort(key=lambda row: row["ts"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    stats["risky_examples"].sort(key=lambda row: row["ts"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return stats


def format_group_rows(rows: list[dict[str, Any]], *, label: str, top: int) -> list[str]:
    if not rows:
        return ["  (none)"]
    shown = rows[:top]
    label_width = max(len(str(row[label])) for row in shown)
    safe_width = max(len(str(row["safe"])) for row in shown)
    risky_width = max(len(str(row["risky"])) for row in shown)
    return [
        "  "
        f"{row[label]:<{label_width}}  "
        f"safe={row['safe']:>{safe_width}}  "
        f"risky={row['risky']:>{risky_width}}  "
        f"safe_rate={fmt_pct(row['safe_rate'])}  "
        f"risky_rate={fmt_pct(row['risky_rate'])}"
        for row in shown
    ]


def format_examples(rows: list[dict[str, Any]], top: int) -> list[str]:
    if not rows:
        return ["  (none)"]
    lines = []
    for row in rows[:top]:
        tokens = ",".join(row["matched_tokens"]) if row["matched_tokens"] else "(none)"
        flags = ",".join(row["heuristic_flags"]) if row["heuristic_flags"] else "(none)"
        lines.append(
            "  "
            f"{fmt_ts(row['ts'])}  "
            f"source={row['source'] or 'n/a'}  "
            f"ticker={row['ticker'] or 'n/a'}  "
            f"score={row['match_score'] if row['match_score'] is not None else 'n/a'}  "
            f"overlap={row['overlap_count'] if row['overlap_count'] is not None else 'n/a'}  "
            f"ratio={row['overlap_ratio'] if row['overlap_ratio'] is not None else 'n/a'}  "
            f"matched_tokens={tokens}  "
            f"flags={flags}  "
            f"headline={row['headline'][:80] if row['headline'] else 'n/a'}"
        )
    return lines


def print_summary(stats: dict[str, Any], *, top: int, recent: int, since: datetime | None, until: datetime | None) -> None:
    print_standard_trade_log_header(
        title="MATCH SUPPRESSION AUDIT",
        path=stats["path"],
        since=since,
        until=until,
        lines_total=stats["lines_total"],
        lines_malformed=stats["lines_malformed"],
        records_kept=stats["records_kept"],
    )

    if stats["total_candidates"] == 0:
        print()
        print("No MATCH_SUPPRESSION_CANDIDATE records found.")
        return

    safe_pct = stats["safe_count"] / stats["total_candidates"] if stats["total_candidates"] else None
    risky_pct = stats["risky_count"] / stats["total_candidates"] if stats["total_candidates"] else None

    print()
    print("Attribution Notes")
    print("  This classification is heuristic only and intended as a first-pass audit.")
    print("  Token-in-ticker is used as a proxy for market-subject alignment, not a semantic guarantee.")

    print()
    print("Summary")
    print(f"  Total candidates            : {stats['total_candidates']}")
    print(f"  Likely safe to suppress     : {stats['safe_count']} ({fmt_pct(safe_pct)})")
    print(f"  Likely risky to suppress    : {stats['risky_count']} ({fmt_pct(risky_pct)})")

    by_source_rows = sorted(
        [
            {
                "source": source,
                "safe": counts["safe"],
                "risky": counts["risky"],
                "safe_rate": counts["safe"] / (counts["safe"] + counts["risky"]),
                "risky_rate": counts["risky"] / (counts["safe"] + counts["risky"]),
            }
            for source, counts in stats["by_source"].items()
        ],
        key=lambda row: (-row["safe"], -row["risky"], row["source"].lower()),
    )
    by_ticker_rows = sorted(
        [
            {
                "ticker": ticker,
                "safe": counts["safe"],
                "risky": counts["risky"],
                "safe_rate": counts["safe"] / (counts["safe"] + counts["risky"]),
                "risky_rate": counts["risky"] / (counts["safe"] + counts["risky"]),
            }
            for ticker, counts in stats["by_ticker"].items()
        ],
        key=lambda row: (-row["safe"], -row["risky"], row["ticker"].lower()),
    )

    print()
    print(f"By Source (top {top})")
    for line in format_group_rows(by_source_rows, label="source", top=top):
        print(line)

    print()
    print(f"By Ticker (top {top})")
    for line in format_group_rows(by_ticker_rows, label="ticker", top=top):
        print(line)

    print()
    print(f"Recent Safe Examples (top {recent})")
    for line in format_examples(stats["safe_examples"], recent):
        print(line)

    print()
    print(f"Recent Risky Examples (top {recent})")
    for line in format_examples(stats["risky_examples"], recent):
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
    print_summary(stats, top=max(1, args.top), recent=max(1, args.recent), since=since, until=until)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
