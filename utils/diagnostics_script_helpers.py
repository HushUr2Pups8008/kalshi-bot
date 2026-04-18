from __future__ import annotations

import argparse
from datetime import datetime, time, timezone
from typing import Any


def add_path_arg(parser: argparse.ArgumentParser, *, default: str, help_text: str) -> None:
    parser.add_argument("--path", default=default, help=help_text)


def add_since_arg(parser: argparse.ArgumentParser, *, help_text: str) -> None:
    parser.add_argument("--since", help=help_text)


def add_until_arg(parser: argparse.ArgumentParser, *, help_text: str) -> None:
    parser.add_argument("--until", help=help_text)


def add_exclude_test_arg(parser: argparse.ArgumentParser, *, help_text: str) -> None:
    parser.add_argument("--exclude-test", action="store_true", help=help_text)


def add_top_arg(parser: argparse.ArgumentParser, *, default: int, help_text: str) -> None:
    parser.add_argument("--top", type=int, default=default, help=help_text)


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


def is_test_record_source_only(record: dict[str, Any]) -> bool:
    source = str(record.get("source") or "").lower()
    ticker = str(record.get("ticker") or "").upper()
    return "r/test" in source or "KXTEST" in ticker


def is_test_record_source_or_signal_source(record: dict[str, Any]) -> bool:
    source = str(record.get("source") or record.get("signal_source") or "").lower()
    ticker = str(record.get("ticker") or "").upper()
    return "r/test" in source or "KXTEST" in ticker
