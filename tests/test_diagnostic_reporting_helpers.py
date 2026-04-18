from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from utils.diagnostic_reporting_helpers import (
    fmt_money,
    fmt_pct,
    format_counter,
    print_standard_trade_log_header,
)


def test_fmt_pct_formats_fraction_with_single_decimal():
    assert fmt_pct(None) == "n/a"
    assert fmt_pct(0.0) == "0.0%"
    assert fmt_pct(0.125) == "12.5%"


def test_fmt_money_formats_sign_and_two_decimals():
    assert fmt_money(None) == "n/a"
    assert fmt_money(0.0) == "+$0.00"
    assert fmt_money(12.3) == "+$12.30"
    assert fmt_money(-4.5) == "$-4.50"


def test_format_counter_renders_right_aligned_counts_and_top_n():
    counter = Counter({"SIGNAL": 12, "SKIPPED": 3, "OPPORTUNITY": 1})

    lines = format_counter(counter, top=2)

    assert lines == [
        "  12  SIGNAL",
        "   3  SKIPPED",
    ]


def test_format_counter_returns_none_marker_for_empty_counter():
    assert format_counter(Counter(), top=5) == ["  (none)"]


def test_print_standard_trade_log_header_renders_exact_lines(capsys):
    print_standard_trade_log_header(
        title="TRADE LOG SUMMARY",
        path=Path("logs/trades/live/trades.jsonl"),
        since=datetime(2026, 4, 18, 0, 0, tzinfo=timezone.utc),
        until=datetime(2026, 4, 18, 23, 59, 59, 999999, tzinfo=timezone.utc),
        lines_total=4775,
        lines_malformed=0,
        records_kept=4775,
    )

    captured = capsys.readouterr()

    assert captured.out == (
        "TRADE LOG SUMMARY\n"
        "Path: logs/trades/live/trades.jsonl\n"
        "Date range: 2026-04-18 -> 2026-04-18\n"
        "Lines read: 4775\n"
        "Malformed lines skipped: 0\n"
        "Records included: 4775\n"
    )


def test_print_standard_trade_log_header_omits_date_range_when_unbounded(capsys):
    print_standard_trade_log_header(
        title="TRADE LOG SUMMARY",
        path=Path("logs/trades"),
        since=None,
        until=None,
        lines_total=0,
        lines_malformed=0,
        records_kept=0,
    )

    captured = capsys.readouterr()

    assert captured.out == (
        "TRADE LOG SUMMARY\n"
        "Path: logs/trades\n"
        "Lines read: 0\n"
        "Malformed lines skipped: 0\n"
        "Records included: 0\n"
    )
