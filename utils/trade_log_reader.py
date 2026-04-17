from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterator


@dataclass
class TradeLogReadStats:
    lines_total: int = 0
    lines_malformed: int = 0


def _parse_iso_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _in_window(ts: datetime | None, since: datetime | None, until: datetime | None) -> bool:
    if ts is None:
        return since is None and until is None
    if since is not None and ts < since:
        return False
    if until is not None and ts > until:
        return False
    return True


def _iter_candidate_files(path: Path) -> Iterator[Path]:
    path_str = os.fspath(path)

    if os.path.isfile(path_str):
        yield path
        return

    live_path_str = os.path.join(path_str, "live", "trades.jsonl")
    legacy_path_str = os.path.join(path_str, "trades.jsonl")
    archive_root_str = os.path.join(path_str, "archive")

    if os.path.exists(archive_root_str):
        archive_files = []
        for root, dirs, files in os.walk(archive_root_str):
            for file in files:
                if file.endswith(".jsonl") or file.endswith(".jsonl.gz"):
                    full_path = os.path.join(root, file)
                    archive_files.append(full_path)
        for candidate_str in sorted(archive_files):
            yield Path(candidate_str)

    if os.path.exists(legacy_path_str) and os.path.exists(live_path_str):
        yield Path(legacy_path_str)
        yield Path(live_path_str)
        return

    if os.path.exists(legacy_path_str):
        yield Path(legacy_path_str)
    if os.path.exists(live_path_str):
        yield Path(live_path_str)


def _archive_partition_date(path: Path) -> date | None:
    """Return the UTC day encoded in a daily archive filename, if present."""

    path_str = os.fspath(path)
    basename = os.path.basename(path_str)
    stem = basename[:-3] if basename.endswith(".gz") else basename
    if not stem.endswith(".jsonl"):
        return None
    day_text = stem[:-6]
    try:
        return datetime.strptime(day_text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _archive_file_definitely_outside_window(
    path: Path,
    *,
    since: datetime | None,
    until: datetime | None,
) -> bool:
    partition_day = _archive_partition_date(path)
    if partition_day is None:
        return False
    if since is not None:
        partition_end = datetime.combine(partition_day, time.max, tzinfo=timezone.utc)
        if partition_end < since:
            return True
    if until is not None:
        partition_start = datetime.combine(partition_day, time.min, tzinfo=timezone.utc)
        if partition_start > until:
            return True
    return False


def _first_valid_ts(path: Path) -> datetime | None:
    for record in _iter_records_from_file(path):
        ts = _parse_iso_ts(record.get("ts"))
        if ts is not None:
            return ts
    return None


def _is_legacy_root_file(file_path: Path, root: Path) -> bool:
    """Return True when *file_path* is the legacy root trades.jsonl for *root*.

    Avoids pathlib property access (.name, .parent) on Windows/Python 3.14, where
    accessing these properties can trigger internal caching machinery that blocks.
    Uses os.path functions with string paths instead for robust comparisons.
    """

    file_str = os.fspath(file_path)
    root_str = os.fspath(root)

    # Construct expected legacy path and compare (case-insensitive on Windows)
    expected_path = os.path.join(root_str, "trades.jsonl")
    return os.path.normcase(file_str) == os.path.normcase(expected_path)


def _iter_records_from_file(path: Path, stats: TradeLogReadStats | None = None) -> Iterator[dict[str, Any]]:
    path_str = os.fspath(path)
    opener = gzip.open if path_str.endswith(".gz") else open
    try:
        with opener(path_str, "rt", encoding="utf-8") as fh:
            for raw_line in fh:
                if stats is not None:
                    stats.lines_total += 1
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    if stats is not None:
                        stats.lines_malformed += 1
                    continue
                if isinstance(record, dict):
                    yield record
                elif stats is not None:
                    stats.lines_malformed += 1
    except OSError:
        return


def iter_trade_records(
    path: Path,
    since: datetime | None = None,
    until: datetime | None = None,
    event_types: set[str] | None = None,
    stats: TradeLogReadStats | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield trade-log records from a single file or a trade-log root directory.

    Directory behavior is intentionally compatibility-aware:
      - archive partitions are read oldest to newest
      - if both legacy and live files exist, legacy records at or after the
        first valid live timestamp are skipped to avoid double-counting active
        records during a phased rollout
    """

    normalized_event_types = {event.strip() for event in event_types or set() if event.strip()}
    live_cutover_ts: datetime | None = None

    # Convert Path to string once at entry to avoid Windows/Python 3.14 pathlib fragility
    path_str = os.fspath(path)

    if os.path.isdir(path_str):
        live_path_str = os.path.join(path_str, "live", "trades.jsonl")
        legacy_path_str = os.path.join(path_str, "trades.jsonl")
        if os.path.exists(live_path_str) and os.path.exists(legacy_path_str):
            live_cutover_ts = _first_valid_ts(Path(live_path_str))

    for file_path in _iter_candidate_files(path):
        if _archive_file_definitely_outside_window(file_path, since=since, until=until):
            continue
        for record in _iter_records_from_file(file_path, stats=stats):
            event_type = str(record.get("type") or "").strip()
            if normalized_event_types and event_type not in normalized_event_types:
                continue
            ts = _parse_iso_ts(record.get("ts"))
            if (
                live_cutover_ts is not None
                and os.path.isdir(path_str)
                and _is_legacy_root_file(file_path, path)
                and ts is not None
                and ts >= live_cutover_ts
            ):
                continue
            if not _in_window(ts, since, until):
                continue
            yield record
