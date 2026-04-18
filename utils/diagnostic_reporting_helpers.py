from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:.2f}"


def format_counter(counter: Counter[str], top: int) -> list[str]:
    if not counter:
        return ["  (none)"]
    width = max(len(str(count)) for count in counter.values())
    return [f"  {count:>{width}}  {label}" for label, count in counter.most_common(top)]


def print_standard_trade_log_header(
    *,
    title: str,
    path: Path,
    since: datetime | None,
    until: datetime | None,
    lines_total: int,
    lines_malformed: int,
    records_kept: int,
) -> None:
    print(title)
    print(f"Path: {path}")
    if since or until:
        since_text = since.date().isoformat() if since else "(beginning)"
        until_text = until.date().isoformat() if until else "(latest)"
        print(f"Date range: {since_text} -> {until_text}")
    print(f"Lines read: {lines_total}")
    print(f"Malformed lines skipped: {lines_malformed}")
    print(f"Records included: {records_kept}")
