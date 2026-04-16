from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime, timezone
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
    if path.is_file():
        yield path
        return

    live_path = path / "live" / "trades.jsonl"
    legacy_path = path / "trades.jsonl"
    archive_root = path / "archive"

    if archive_root.exists():
        archive_files = list(archive_root.rglob("*.jsonl")) + list(archive_root.rglob("*.jsonl.gz"))
        for candidate in sorted(archive_files):
            yield candidate

    if legacy_path.exists() and live_path.exists():
        yield legacy_path
        yield live_path
        return

    if legacy_path.exists():
        yield legacy_path
    if live_path.exists():
        yield live_path


def _first_valid_ts(path: Path) -> datetime | None:
    for record in _iter_records_from_file(path):
        ts = _parse_iso_ts(record.get("ts"))
        if ts is not None:
            return ts
    return None


def _iter_records_from_file(path: Path, stats: TradeLogReadStats | None = None) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8") as fh:
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
    if path.is_dir():
        live_path = path / "live" / "trades.jsonl"
        legacy_path = path / "trades.jsonl"
        if live_path.exists() and legacy_path.exists():
            live_cutover_ts = _first_valid_ts(live_path)

    for file_path in _iter_candidate_files(path):
        for record in _iter_records_from_file(file_path, stats=stats):
            event_type = str(record.get("type") or "").strip()
            if normalized_event_types and event_type not in normalized_event_types:
                continue
            ts = _parse_iso_ts(record.get("ts"))
            if (
                live_cutover_ts is not None
                and path.is_dir()
                and file_path == (path / "trades.jsonl")
                and ts is not None
                and ts >= live_cutover_ts
            ):
                continue
            if not _in_window(ts, since, until):
                continue
            yield record
