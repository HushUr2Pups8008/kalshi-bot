"""
Read-only source scorecard for operator review.

Combines source-attributed metrics from:
  - logs/trades/
  - data/paper_trades.db

This report is intentionally conservative:
  - EARLY_STALE_DROP, ANALYSIS_REJECTED, and SIGNAL are source-attributed
    directly from structured logs.
  - paper trade outcomes are source-attributed directly from signal_source in
    paper_trades.db.
  - OPPORTUNITY and SKIPPED are not currently source-attributable from the log
    schema, so the report says so instead of guessing.

The preferred source is logs/trades/. The legacy monolithic
logs/trades/trades.jsonl path is still supported during the cutover
validation window.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import DISABLED_NEWS_SOURCES, DISABLED_SOURCE_FAMILIES
from utils.reporting_helpers import warn_if_full_trade_root_scan
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records

DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "paper_trades.db"

KNOWN_PUBLISHER_MARKERS = (
    "Reuters",
    "AP",
    "BBC",
    "NYT",
    "Guardian",
    "Al Jazeera",
    "France 24",
    "Deutsche Welle",
    "Defense One",
    "Foreign Policy",
    "Politics",
    "Middle East and north Africa",
    "World news |",
    "Ukraine |",
)

KNOWN_DIRECT_FEED_SOURCES = {
    "Just In News",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Source usefulness scorecard")
    parser.add_argument(
        "--logs-path",
        default=str(DEFAULT_LOG_PATH),
        help="Path to trade-log file or root (default: logs/trades/; legacy logs/trades/trades.jsonl still supported)",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="Path to paper_trades.db (default: data/paper_trades.db)",
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
        help="Max rows per group (default: 10)",
    )
    parser.add_argument(
        "--exclude-test",
        action="store_true",
        help="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    parser.add_argument(
        "--hide-disabled",
        action="store_true",
        help="Hide the 'Disabled by Config' section",
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


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:.2f}"


def is_test_source_ticker(source: str, ticker: str) -> bool:
    return "r/test" in source.lower() or "KXTEST" in ticker.upper()


def default_source_metrics() -> dict[str, Any]:
    return {
        "observed_records": 0,
        "early_stale_drops": 0,
        "analysis_stale_rejections": 0,
        "signals": 0,
        "opportunities": None,
        "executor_skips": None,
        "paper_trades": 0,
        "resolved_paper_trades": 0,
        "wins": 0,
        "total_pnl": 0.0,
    }


def is_disabled_source(source: str) -> bool:
    if source in DISABLED_NEWS_SOURCES:
        return True
    source_lower = source.strip().lower()
    return any(disabled.strip().lower() == source_lower for disabled in DISABLED_NEWS_SOURCES)


def disabled_status(source: str, family: str) -> tuple[bool, str | None]:
    if is_disabled_source(source):
        return True, "source"
    if family in DISABLED_SOURCE_FAMILIES:
        return True, "family"
    return False, None


def classify_source_family(source: str) -> str:
    source = source.strip()
    if not source:
        return "other"
    if source.endswith(" - BingNews"):
        return "bing_news_query"
    if source.endswith(" - Google News"):
        return "google_news_query"
    if source.startswith("r/"):
        return "reddit"
    if source in KNOWN_DIRECT_FEED_SOURCES:
        return "direct_feed"
    if any(marker.lower() in source.lower() for marker in KNOWN_PUBLISHER_MARKERS):
        return "publisher_rss"
    return "other"


def collect_log_metrics(
    path: Path,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    metrics: dict[str, dict[str, Any]] = defaultdict(default_source_metrics)
    meta = {"lines_total": 0, "lines_malformed": 0, "records_kept": 0}

    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        event_type = str(record.get("type") or "").strip()
        source = str(record.get("source") or "").strip()
        ticker = str(record.get("ticker") or "").strip()

        if exclude_test and is_test_source_ticker(source, ticker):
            continue

        meta["records_kept"] += 1

        if not source:
            continue

        row = metrics[source]
        row["observed_records"] += 1

        if event_type == "EARLY_STALE_DROP" and str(record.get("reason") or "") == "stale_by_source_policy":
            row["early_stale_drops"] += 1
        elif event_type == "ANALYSIS_REJECTED" and str(record.get("reason") or "") == "stale_news":
            row["analysis_stale_rejections"] += 1
        elif event_type == "SIGNAL":
            row["signals"] += 1

    meta["lines_total"] = read_stats.lines_total
    meta["lines_malformed"] = read_stats.lines_malformed

    return dict(metrics), meta


def collect_db_metrics(
    path: Path,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool,
) -> tuple[dict[str, dict[str, Any]], set[str], bool]:
    metrics: dict[str, dict[str, Any]] = defaultdict(default_source_metrics)
    columns: set[str] = set()

    if not path.exists():
        return {}, columns, False

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(paper_trades)")}
        if not columns:
            return {}, columns, True

        rows = conn.execute("SELECT * FROM paper_trades ORDER BY ts ASC").fetchall()
        for row_obj in rows:
            row_dict = dict(row_obj)
            ts = parse_iso_ts(row_dict.get("ts"))
            if not in_window(ts, since, until):
                continue

            source = str(row_dict.get("signal_source") or "").strip()
            ticker = str(row_dict.get("ticker") or "").strip()
            if exclude_test and is_test_source_ticker(source, ticker):
                continue
            if not source:
                continue

            row = metrics[source]
            row["paper_trades"] += 1

            if safe_int(row_dict.get("resolved")) == 1:
                row["resolved_paper_trades"] += 1
                pnl = safe_float(row_dict.get("pnl_dollars"))
                if pnl is not None:
                    row["total_pnl"] += pnl
                    if pnl > 0:
                        row["wins"] += 1

        return dict(metrics), columns, True
    finally:
        conn.close()


def classify_source(row: dict[str, Any]) -> str:
    resolved = row["resolved_paper_trades"]
    pnl = row["total_pnl"] if resolved > 0 else None
    win_rate = row["win_rate"]
    stale_burden = row["early_stale_drops"] + row["analysis_stale_rejections"]

    if resolved >= 2 and pnl is not None and pnl > 0 and win_rate is not None and win_rate >= 0.5:
        return "likely keep"
    if resolved >= 2 and pnl is not None and pnl < 0:
        return "likely prune"
    if row["observed_records"] >= 5 and row["signals"] == 0 and row["paper_trades"] == 0 and stale_burden >= 3:
        return "likely prune"
    return "investigate"


def top_performer(row: dict[str, Any]) -> bool:
    resolved = row["resolved_paper_trades"]
    pnl = row["total_pnl"] if resolved > 0 else None
    win_rate = row["win_rate"]
    return (
        resolved >= 2
        and pnl is not None
        and pnl > 0
        and win_rate is not None
        and win_rate >= 0.75
    )


def rank_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
    pnl = row["total_pnl"] if row["resolved_paper_trades"] > 0 else float("-inf")
    return (
        -row["resolved_paper_trades"],
        -pnl,
        -row["paper_trades"],
        -row["signals"],
        -row["observed_records"],
        row["source"].lower(),
    )


def auto_disable_candidate(row: dict[str, Any]) -> bool:
    if row["observed_records"] < 15:
        return False
    if row["signals"] != 0 or row["paper_trades"] != 0:
        return False
    burden = row["stale_drop_ratio"]
    return burden is not None and burden >= 0.80


def watchlist_candidate(row: dict[str, Any]) -> bool:
    if auto_disable_candidate(row):
        return False
    if row["observed_records"] < 10:
        return False
    if row["paper_trades"] != 0:
        return False
    burden = row["stale_drop_ratio"]
    if burden is None:
        return False
    return burden >= 0.60 and row["signals"] <= 1


def recommendation_tier(row: dict[str, Any]) -> str:
    classification = row["classification"]
    if auto_disable_candidate(row):
        return "remove immediately"
    if classification == "likely prune":
        return "prune"
    if top_performer(row):
        return "top performers"
    if classification == "likely keep":
        return "keep"
    if watchlist_candidate(row) or classification == "investigate":
        return "watch / investigate"
    return "watch / investigate"


def summarize(
    logs_path: Path,
    db_path: Path,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool = False,
) -> dict[str, Any]:
    log_metrics, log_meta = collect_log_metrics(logs_path, since, until, exclude_test)
    db_metrics, db_columns, db_exists = collect_db_metrics(db_path, since, until, exclude_test)

    sources = sorted(set(log_metrics) | set(db_metrics))
    rows: list[dict[str, Any]] = []
    for source in sources:
        row = default_source_metrics()
        for key, value in log_metrics.get(source, {}).items():
            row[key] = value
        for key, value in db_metrics.get(source, {}).items():
            if key == "total_pnl":
                row[key] += value
            elif key in {"paper_trades", "resolved_paper_trades", "wins"}:
                row[key] += value
        resolved = row["resolved_paper_trades"]
        row["source"] = source
        row["source_family"] = classify_source_family(source)
        row["win_rate"] = (row["wins"] / resolved) if resolved else None
        stale_total = row["early_stale_drops"] + row["analysis_stale_rejections"]
        row["stale_drop_ratio"] = (stale_total / row["observed_records"]) if row["observed_records"] > 0 else None
        row["is_disabled"], row["disabled_reason"] = disabled_status(row["source"], row["source_family"])
        row["classification"] = classify_source(row)
        rows.append(row)

    grouped = {
        "remove immediately": [],
        "prune": [],
        "watch / investigate": [],
        "keep": [],
        "top performers": [],
        "disabled by source": [],
        "disabled by family": [],
    }
    for row in rows:
        if row["is_disabled"]:
            grouped[f"disabled by {row['disabled_reason']}"].append(row)
        else:
            grouped[recommendation_tier(row)].append(row)
    for key in grouped:
        grouped[key].sort(key=rank_tuple)

    return {
        "logs_path": logs_path,
        "db_path": db_path,
        "since": since,
        "until": until,
        "log_meta": log_meta,
        "db_exists": db_exists,
        "db_columns": db_columns,
        "rows": rows,
        "grouped": grouped,
        "disabled_sources": sorted(DISABLED_NEWS_SOURCES, key=str.lower),
        "opportunities_attributable": False,
        "executor_skips_attributable": False,
    }


def format_group_rows(rows: list[dict[str, Any]], top: int) -> list[str]:
    if not rows:
        return ["  (none)"]

    shown = rows[:top]
    source_width = max(len(row["source"]) for row in shown)
    family_width = max(len(row["source_family"]) for row in shown)
    disabled_width = max(len(str(row.get("disabled_reason") or "-")) for row in shown)
    obs_width = max(len(str(row["observed_records"])) for row in shown)
    stale_width = max(len(str(row["early_stale_drops"])) for row in shown)
    analysis_width = max(len(str(row["analysis_stale_rejections"])) for row in shown)
    signal_width = max(len(str(row["signals"])) for row in shown)
    trade_width = max(len(str(row["paper_trades"])) for row in shown)
    resolved_width = max(len(str(row["resolved_paper_trades"])) for row in shown)

    lines = []
    for row in shown:
        lines.append(
            "  "
            f"{row['source']:<{source_width}}  "
            f"family={row['source_family']:<{family_width}}  "
            f"disabled_by={str(row.get('disabled_reason') or '-'):<{disabled_width}}  "
            f"obs={row['observed_records']:>{obs_width}}  "
            f"early_stale={row['early_stale_drops']:>{stale_width}}  "
            f"analysis_stale={row['analysis_stale_rejections']:>{analysis_width}}  "
            f"signals={row['signals']:>{signal_width}}  "
            f"paper={row['paper_trades']:>{trade_width}}  "
            f"resolved={row['resolved_paper_trades']:>{resolved_width}}  "
            f"win_rate={fmt_pct(row['win_rate'])}  "
            f"pnl={fmt_money(row['total_pnl']) if row['resolved_paper_trades'] else 'n/a'}"
        )
    return lines


def print_summary(stats: dict[str, Any], top: int, hide_disabled: bool = False) -> None:
    print("SOURCE SCORECARD")
    print(f"Logs path: {stats['logs_path']}")
    print(f"DB path:   {stats['db_path']}")
    if stats["since"] or stats["until"]:
        since_text = stats["since"].date().isoformat() if stats["since"] else "(beginning)"
        until_text = stats["until"].date().isoformat() if stats["until"] else "(latest)"
        print(f"Date range: {since_text} -> {until_text}")

    print()
    print("Attribution Notes")
    print("  Opportunities produced: not reliably attributable from current trades.jsonl schema.")
    print("  Executor skips: not reliably attributable from current trades.jsonl schema.")
    print("  Paper-trade outcomes are attributed from signal_source in paper_trades.db.")

    print()
    print("Input Coverage")
    print(f"  Log lines read         : {stats['log_meta']['lines_total']}")
    print(f"  Malformed log lines    : {stats['log_meta']['lines_malformed']}")
    print(f"  Log records included   : {stats['log_meta']['records_kept']}")
    print(f"  DB available           : {'yes' if stats['db_exists'] else 'no'}")
    print(f"  Sources scored         : {len(stats['rows'])}")
    print(
        "  Disabled in config     : "
        f"{len(stats['grouped']['disabled by source']) + len(stats['grouped']['disabled by family'])}"
    )

    if not stats["rows"]:
        print()
        print("No source-attributable records found.")
        return

    print()
    print("Active Source Recommendations")
    for title in (
        "top performers",
        "keep",
        "watch / investigate",
        "prune",
        "remove immediately",
    ):
        print()
        print(title.title())
        if title == "top performers":
            print("  Sources with strong realized outcomes (>=2 resolved trades, positive P&L, >=75% win rate)")
        elif title == "keep":
            print("  Sources producing acceptable signals with no major issues")
        elif title == "watch / investigate":
            print("  Mixed performance; needs more data or review")
        elif title == "prune":
            print("  Low value; consider removal soon")
        elif title == "remove immediately":
            print("  High-confidence zero-value sources; safe to disable")
        for line in format_group_rows(stats["grouped"][title], top):
            print(line)

    if not hide_disabled:
        print()
        print("Disabled Sources")
        print()
        print("Disabled By Source")
        for line in format_group_rows(stats["grouped"]["disabled by source"], top):
            print(line)
        print()
        print("Disabled By Family")
        for line in format_group_rows(stats["grouped"]["disabled by family"], top):
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

    warn_if_full_trade_root_scan(Path(args.logs_path), since=since, until=until)

    stats = summarize(
        logs_path=Path(args.logs_path),
        db_path=Path(args.db_path),
        since=since,
        until=until,
        exclude_test=args.exclude_test,
    )
    print_summary(stats, top=max(1, args.top), hide_disabled=args.hide_disabled)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
