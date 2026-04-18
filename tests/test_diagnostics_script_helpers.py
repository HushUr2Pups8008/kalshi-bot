from __future__ import annotations

import argparse
from datetime import datetime, timezone

from utils.diagnostics_script_helpers import (
    add_exclude_test_arg,
    add_path_arg,
    add_since_arg,
    add_top_arg,
    add_until_arg,
    in_window,
    is_test_record_source_only,
    is_test_record_source_or_signal_source,
    parse_date_end,
    parse_date_start,
    parse_iso_ts,
)


def test_parse_iso_ts_normalizes_offset_to_utc():
    parsed = parse_iso_ts("2026-04-18T10:30:00-06:00")

    assert parsed == datetime(2026, 4, 18, 16, 30, tzinfo=timezone.utc)


def test_parse_iso_ts_treats_naive_timestamp_as_utc():
    parsed = parse_iso_ts("2026-04-18T10:30:00")

    assert parsed == datetime(2026, 4, 18, 10, 30, tzinfo=timezone.utc)


def test_parse_iso_ts_returns_none_for_blank_or_invalid_values():
    assert parse_iso_ts("") is None
    assert parse_iso_ts(None) is None
    assert parse_iso_ts("not-a-timestamp") is None


def test_parse_date_start_returns_utc_midnight():
    parsed = parse_date_start("2026-04-18")

    assert parsed == datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc)


def test_parse_date_end_is_inclusive_last_moment_of_day():
    parsed = parse_date_end("2026-04-18")

    assert parsed == datetime(2026, 4, 18, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_in_window_is_inclusive_of_since_and_until():
    since = parse_date_start("2026-04-18")
    until = parse_date_end("2026-04-18")

    assert in_window(since, since, until) is True
    assert in_window(until, since, until) is True
    assert in_window(datetime(2026, 4, 17, 23, 59, 59, tzinfo=timezone.utc), since, until) is False
    assert in_window(datetime(2026, 4, 19, 0, 0, 0, tzinfo=timezone.utc), since, until) is False


def test_in_window_handles_none_timestamp_only_for_unbounded_window():
    assert in_window(None, None, None) is True
    assert in_window(None, parse_date_start("2026-04-18"), None) is False
    assert in_window(None, None, parse_date_end("2026-04-18")) is False


def test_is_test_record_source_only_ignores_signal_source():
    record = {
        "source": "Reuters",
        "signal_source": "r/test",
        "ticker": "KXREAL-1",
    }

    assert is_test_record_source_only(record) is False


def test_is_test_record_source_only_detects_source_and_ticker_markers():
    assert is_test_record_source_only({"source": "r/test", "ticker": "KXREAL-1"}) is True
    assert is_test_record_source_only({"source": "Reuters", "ticker": "KXTEST-1"}) is True


def test_is_test_record_source_or_signal_source_falls_back_to_signal_source():
    record = {
        "source": "",
        "signal_source": "r/test",
        "ticker": "KXREAL-1",
    }

    assert is_test_record_source_or_signal_source(record) is True


def test_is_test_record_source_or_signal_source_prefers_source_when_present():
    record = {
        "source": "Reuters",
        "signal_source": "r/test",
        "ticker": "KXREAL-1",
    }

    assert is_test_record_source_or_signal_source(record) is False


def test_argparse_adders_preserve_expected_flags_and_defaults():
    parser = argparse.ArgumentParser()
    add_path_arg(parser, default="logs/trades", help_text="path help")
    add_since_arg(parser, help_text="since help")
    add_until_arg(parser, help_text="until help")
    add_exclude_test_arg(parser, help_text="exclude help")
    add_top_arg(parser, default=7, help_text="top help")

    args = parser.parse_args(["--since", "2026-04-18", "--exclude-test", "--top", "9"])

    assert args.path == "logs/trades"
    assert args.since == "2026-04-18"
    assert args.until is None
    assert args.exclude_test is True
    assert args.top == 9

