"""
Read-only paper trading performance drilldown from data/paper_trades.db.

This report focuses on metrics that are reliably derivable from the stored
paper_trades schema:
  - total / resolved / unresolved trade counts
  - win rate on resolved trades
  - total / average P&L for resolved trades
  - breakdowns by source, signal type, ticker, and series ticker
  - holding-period summary when both ts and resolved_ts are available

Usage:
  python scripts/paper_performance_drilldown.py
  python scripts/paper_performance_drilldown.py --path data/paper_trades.db
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "paper_trades.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paper trading performance drilldown")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_DB_PATH),
        help="Path to paper_trades.db (default: data/paper_trades.db)",
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
        help="Exclude synthetic/test trades (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    return parser.parse_args()


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
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


def fmt_duration_hours(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value < 24:
        return f"{value:.1f}h"
    return f"{value / 24:.1f}d"


def format_counter_lines(counter: Counter[str], top: int) -> list[str]:
    if not counter:
        return ["  (none)"]
    width = max(len(str(count)) for count in counter.values())
    return [f"  {count:>{width}}  {label}" for label, count in counter.most_common(top)]


def format_group_table(rows: list[dict[str, Any]], top: int) -> list[str]:
    if not rows:
        return ["  (none)"]
    shown = rows[:top]
    name_width = max(len(str(row["name"])) for row in shown)
    trades_width = max(len(str(row["trades"])) for row in shown)
    resolved_width = max(len(str(row["resolved"])) for row in shown)

    lines = []
    for row in shown:
        lines.append(
            "  "
            f"{row['name']:<{name_width}}  "
            f"trades={row['trades']:>{trades_width}}  "
            f"resolved={row['resolved']:>{resolved_width}}  "
            f"win_rate={fmt_pct(row['win_rate'])}  "
            f"pnl={fmt_money(row['pnl'])}"
        )
    return lines


def load_trades(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    if not path.exists():
        return [], set()

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(paper_trades)")}
        if not columns:
            return [], set()
        rows = conn.execute("SELECT * FROM paper_trades ORDER BY ts ASC").fetchall()
        return [dict(row) for row in rows], columns
    finally:
        conn.close()


def is_test_trade(trade: dict[str, Any]) -> bool:
    source = str(trade.get("signal_source") or "").lower()
    ticker = str(trade.get("ticker") or "").upper()
    return "r/test" in source or "KXTEST" in ticker


def group_trade_rows(trades: list[dict[str, Any]], key_name: str, default_label: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        key = str(trade.get(key_name) or "").strip() or default_label
        groups[key].append(trade)

    result = []
    for name, rows in groups.items():
        resolved = [row for row in rows if safe_int(row.get("resolved")) == 1]
        wins = [row for row in resolved if (safe_float(row.get("pnl_dollars")) or 0.0) > 0]
        pnl_values = [safe_float(row.get("pnl_dollars")) for row in resolved]
        pnl = sum(value for value in pnl_values if value is not None)
        result.append(
            {
                "name": name,
                "trades": len(rows),
                "resolved": len(resolved),
                "win_rate": (len(wins) / len(resolved)) if resolved else None,
                "pnl": pnl if resolved else None,
            }
        )

    result.sort(
        key=lambda row: (
            -row["trades"],
            row["name"],
        )
    )
    return result


def summarize(path: Path, exclude_test: bool = False) -> dict[str, Any]:
    trades, columns = load_trades(path)
    if exclude_test:
        trades = [trade for trade in trades if not is_test_trade(trade)]
    stats: dict[str, Any] = {
        "path": path,
        "exists": path.exists(),
        "columns": columns,
        "trades": trades,
        "total_trades": len(trades),
        "resolved_trades": 0,
        "open_trades": 0,
        "win_rate": None,
        "total_pnl": None,
        "avg_pnl": None,
        "avg_win": None,
        "avg_loss": None,
        "sources": [],
        "signal_types": [],
        "tickers": [],
        "series": [],
        "holding_period_count": 0,
        "holding_period_avg_hours": None,
        "holding_period_median_hours": None,
    }

    if not trades:
        return stats

    resolved = [trade for trade in trades if safe_int(trade.get("resolved")) == 1]
    open_trades = [trade for trade in trades if safe_int(trade.get("resolved")) != 1]
    wins = [trade for trade in resolved if (safe_float(trade.get("pnl_dollars")) or 0.0) > 0]
    losses = [trade for trade in resolved if (safe_float(trade.get("pnl_dollars")) or 0.0) <= 0]
    pnl_values = [safe_float(trade.get("pnl_dollars")) for trade in resolved]
    pnl_values = [value for value in pnl_values if value is not None]

    stats["resolved_trades"] = len(resolved)
    stats["open_trades"] = len(open_trades)
    stats["win_rate"] = (len(wins) / len(resolved)) if resolved else None
    if pnl_values:
        stats["total_pnl"] = sum(pnl_values)
        stats["avg_pnl"] = sum(pnl_values) / len(pnl_values)
    if wins:
        win_values = [safe_float(trade.get("pnl_dollars")) for trade in wins]
        win_values = [value for value in win_values if value is not None]
        stats["avg_win"] = sum(win_values) / len(win_values) if win_values else None
    if losses:
        loss_values = [safe_float(trade.get("pnl_dollars")) for trade in losses]
        loss_values = [value for value in loss_values if value is not None]
        stats["avg_loss"] = sum(loss_values) / len(loss_values) if loss_values else None

    stats["sources"] = group_trade_rows(trades, "signal_source", "(unknown)")

    signal_type_key = "signal_type" if "signal_type" in columns else ""
    if signal_type_key:
        stats["signal_types"] = group_trade_rows(trades, signal_type_key, "(unknown)")
    else:
        stats["signal_types"] = [
            {
                "name": "news (default/legacy schema)",
                "trades": len(trades),
                "resolved": len(resolved),
                "win_rate": stats["win_rate"],
                "pnl": stats["total_pnl"],
            }
        ]

    stats["tickers"] = group_trade_rows(trades, "ticker", "(unknown)")
    if "series_ticker" in columns:
        stats["series"] = group_trade_rows(trades, "series_ticker", "(unknown)")

    holding_hours = []
    if "resolved_ts" in columns:
        for trade in resolved:
            opened = parse_ts(trade.get("ts"))
            closed = parse_ts(trade.get("resolved_ts"))
            if opened is None or closed is None or closed < opened:
                continue
            holding_hours.append((closed - opened).total_seconds() / 3600.0)
    if holding_hours:
        ordered = sorted(holding_hours)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 1:
            median = ordered[mid]
        else:
            median = (ordered[mid - 1] + ordered[mid]) / 2.0
        stats["holding_period_count"] = len(holding_hours)
        stats["holding_period_avg_hours"] = sum(holding_hours) / len(holding_hours)
        stats["holding_period_median_hours"] = median

    return stats


def print_summary(stats: dict[str, Any], top: int) -> None:
    print("PAPER TRADING PERFORMANCE DRILLDOWN")
    print(f"Path: {stats['path']}")

    if not stats["exists"]:
        print()
        print("Database file not found.")
        return

    print(f"Trades loaded: {stats['total_trades']}")
    if stats["total_trades"] == 0:
        print()
        print("No paper trades found.")
        return

    print()
    print("Overview")
    print(f"  Resolved trades   : {stats['resolved_trades']}")
    print(f"  Open trades       : {stats['open_trades']}")
    print(f"  Win rate          : {fmt_pct(stats['win_rate'])}")
    print(f"  Total P&L         : {fmt_money(stats['total_pnl'])}")
    print(f"  Avg resolved P&L  : {fmt_money(stats['avg_pnl'])}")
    print(f"  Avg win           : {fmt_money(stats['avg_win'])}")
    print(f"  Avg loss          : {fmt_money(stats['avg_loss'])}")

    print()
    print("Holding Period")
    if stats["holding_period_count"] > 0:
        print(f"  Trades with holding data : {stats['holding_period_count']}")
        print(f"  Average hold             : {fmt_duration_hours(stats['holding_period_avg_hours'])}")
        print(f"  Median hold              : {fmt_duration_hours(stats['holding_period_median_hours'])}")
    else:
        print("  Holding period summary not reliably derivable from current rows.")

    print()
    print(f"By Source (top {top})")
    for line in format_group_table(stats["sources"], top):
        print(line)

    print()
    print(f"By Signal Type (top {top})")
    for line in format_group_table(stats["signal_types"], top):
        print(line)

    print()
    print(f"By Ticker (top {top})")
    for line in format_group_table(stats["tickers"], top):
        print(line)

    if stats["series"]:
        print()
        print(f"By Series Ticker (top {top})")
        for line in format_group_table(stats["series"], top):
            print(line)
    else:
        print()
        print("By Series Ticker")
        print("  Series-level breakdown not reliably derivable from current schema.")


def main() -> int:
    args = parse_args()
    stats = summarize(Path(args.path), exclude_test=args.exclude_test)
    print_summary(stats, top=max(1, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
