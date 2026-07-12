#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

REPO_ROOT_FOR_IMPORTS = Path(__file__).resolve().parent.parent
if str(REPO_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_FOR_IMPORTS))

from analysis.generic_search_circuit import (
    GENERIC_SEARCH_CIRCUIT_EVENT_KINDS,
    GENERIC_SEARCH_CIRCUIT_LOG_PREFIX,
    GENERIC_SEARCH_CIRCUIT_SCHEMA_VERSION,
)

_EVENT_TOTAL_KEYS = {
    "attempt": "attempts",
    "double_availability_failure": "double_availability_failures",
    "open": "open_transitions",
    "would_open": "would_open_transitions",
    "blocked": "blocked_calls",
    "would_block": "would_block_calls",
    "half_open": "probes",
    "provider_error": "provider_error_events",
}
_COUNTER_KEYS = {
    "total_attempts",
    "double_availability_failures",
    "open_transitions",
    "would_open_transitions",
    "blocked_calls",
    "would_block_calls",
    "probe_successes",
    "probe_failures",
}


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_nonnegative_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
    )


def _is_schema_v1_event(payload: dict[str, Any]) -> bool:
    counters = payload.get("counters")
    failure_classes = payload.get("failure_classes")
    return (
        _is_nonnegative_int(payload.get("pid"))
        and payload["pid"] > 0
        and payload.get("mode") in {"off", "shadow", "enforce"}
        and payload.get("state") in {"closed", "open", "half_open"}
        and _is_nonnegative_int(payload.get("generation"))
        and payload.get("event_kind") in GENERIC_SEARCH_CIRCUIT_EVENT_KINDS
        and isinstance(failure_classes, list)
        and all(isinstance(value, str) for value in failure_classes)
        and _is_nonnegative_number(payload.get("cooldown_seconds"))
        and _is_nonnegative_number(payload.get("remaining_cooldown_seconds"))
        and isinstance(counters, dict)
        and _COUNTER_KEYS <= counters.keys()
        and all(_is_nonnegative_int(counters[key]) for key in _COUNTER_KEYS)
    )


def _iter_log_paths(log_dir: Path) -> Iterable[Path]:
    if not log_dir.is_dir():
        return
    for path in sorted(log_dir.iterdir()):
        if path.is_file() and (
            path.name == "bot.log" or path.name.startswith("bot.log.")
        ):
            yield path


def _open_log(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def _empty_totals() -> dict[str, int]:
    return {key: 0 for key in _EVENT_TOTAL_KEYS.values()}


def build_report(
    *,
    log_dir: Path,
    since_hours: float = 24.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    if since_hours <= 0:
        raise ValueError("since_hours must be positive")
    window_end = now or datetime.now(timezone.utc)
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=timezone.utc)
    window_end = window_end.astimezone(timezone.utc)
    window_start = window_end - timedelta(hours=since_hours)
    totals = _empty_totals()
    warnings = {"malformed_lines": 0, "unsupported_schema_versions": 0}
    latest_payload: dict[str, Any] | None = None
    latest_timestamp: datetime | None = None
    first_timestamp: datetime | None = None
    events_read = 0
    files_read = 0
    event_source_by_payload: dict[str, Path] = {}

    for path in _iter_log_paths(log_dir):
        try:
            handle = _open_log(path)
        except OSError:
            warnings["malformed_lines"] += 1
            continue
        files_read += 1
        try:
            with handle:
                for line in handle:
                    marker_index = line.find(GENERIC_SEARCH_CIRCUIT_LOG_PREFIX)
                    if marker_index < 0:
                        continue
                    raw_payload = line[
                        marker_index + len(GENERIC_SEARCH_CIRCUIT_LOG_PREFIX) :
                    ].strip()
                    try:
                        payload = json.loads(raw_payload)
                    except json.JSONDecodeError:
                        warnings["malformed_lines"] += 1
                        continue
                    if not isinstance(payload, dict):
                        warnings["malformed_lines"] += 1
                        continue
                    schema_version = payload.get("schema_version")
                    if (
                        type(schema_version) is not int
                        or schema_version != GENERIC_SEARCH_CIRCUIT_SCHEMA_VERSION
                    ):
                        warnings["unsupported_schema_versions"] += 1
                        continue
                    if not _is_schema_v1_event(payload):
                        warnings["malformed_lines"] += 1
                        continue
                    observed_at = _parse_timestamp(payload.get("observed_at"))
                    if observed_at is None:
                        warnings["malformed_lines"] += 1
                        continue
                    if observed_at < window_start or observed_at > window_end:
                        continue
                    canonical_payload = json.dumps(
                        payload,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    prior_source = event_source_by_payload.get(canonical_payload)
                    if prior_source is not None and prior_source != path:
                        continue
                    event_source_by_payload.setdefault(canonical_payload, path)
                    event_kind = payload["event_kind"]
                    events_read += 1
                    total_key = _EVENT_TOTAL_KEYS.get(event_kind)
                    if total_key is not None:
                        totals[total_key] += 1
                    if first_timestamp is None or observed_at < first_timestamp:
                        first_timestamp = observed_at
                    if latest_timestamp is None or observed_at > latest_timestamp:
                        latest_timestamp = observed_at
                        latest_payload = payload
        except OSError:
            warnings["malformed_lines"] += 1

    return {
        "report_schema_version": 1,
        "log_dir": str(log_dir),
        "log_files_read": files_read,
        "events_read": events_read,
        "totals": totals,
        "latest": {
            "observed_at": latest_timestamp.isoformat() if latest_timestamp else None,
            "pid": latest_payload.get("pid") if latest_payload else None,
            "mode": latest_payload.get("mode") if latest_payload else None,
            "state": latest_payload.get("state") if latest_payload else None,
            "generation": latest_payload.get("generation") if latest_payload else None,
        },
        "bounds": {
            "since_hours": since_hours,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "first_observed_at": first_timestamp.isoformat() if first_timestamp else None,
            "last_observed_at": latest_timestamp.isoformat() if latest_timestamp else None,
        },
        "warnings": warnings,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report generic search circuit telemetry")
    parser.add_argument("--since-hours", type=float, default=24.0)
    parser.add_argument("--log-dir", type=Path, default=Path("logs/app"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = build_report(log_dir=args.log_dir, since_hours=args.since_hours)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
