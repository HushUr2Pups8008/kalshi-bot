from __future__ import annotations

import asyncio
import gzip
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from analysis.generic_search_circuit import (
    GENERIC_SEARCH_CIRCUIT_LOG_PREFIX,
    GenericSearchCircuit,
    GenericSearchCircuitEvent,
    generic_search_circuit_event_record,
)
from scripts.generic_search_circuit_report import build_report


def _event(*, kind: str, failure_classes: tuple[str, ...] = ()) -> GenericSearchCircuitEvent:
    return GenericSearchCircuitEvent(
        kind=kind,
        mode="shadow",
        state="open",
        generation=3,
        failure_classes=failure_classes,
        cooldown_seconds=120.0,
        remaining_cooldown_seconds=45.0,
        total_attempts=11,
        double_availability_failures=2,
        open_transitions=0,
        would_open_transitions=2,
        blocked_calls=0,
        would_block_calls=4,
        probe_successes=1,
        probe_failures=1,
    )


def _record(kind: str, timestamp: datetime, **overrides: object) -> str:
    payload = {
        "schema_version": 1,
        "observed_at": timestamp.isoformat(),
        "pid": 1234,
        "event_kind": kind,
        "mode": "shadow",
        "state": "closed",
        "generation": 2,
        "failure_classes": [],
        "cooldown_seconds": 120.0,
        "remaining_cooldown_seconds": 0.0,
        "counters": {
            "total_attempts": 5,
            "double_availability_failures": 1,
            "open_transitions": 0,
            "would_open_transitions": 1,
            "blocked_calls": 0,
            "would_block_calls": 0,
            "probe_successes": 0,
            "probe_failures": 0,
        },
    }
    payload.update(overrides)
    return f"2026-07-12 INFO {GENERIC_SEARCH_CIRCUIT_LOG_PREFIX}{json.dumps(payload)}\n"


def test_event_record_is_schema_v1_timestamped_pid_scoped_and_sanitized() -> None:
    observed_at = datetime(2026, 7, 12, 18, 30, tzinfo=timezone.utc)

    record = generic_search_circuit_event_record(
        _event(
            kind="double_availability_failure",
            failure_classes=("TimeoutError private-query", "HTTPError:503"),
        ),
        observed_at=observed_at,
        pid=4321,
    )

    assert record.startswith(GENERIC_SEARCH_CIRCUIT_LOG_PREFIX)
    payload = json.loads(record.removeprefix(GENERIC_SEARCH_CIRCUIT_LOG_PREFIX))
    assert payload["schema_version"] == 1
    assert payload["observed_at"] == observed_at.isoformat()
    assert payload["pid"] == 4321
    assert payload["event_kind"] == "double_availability_failure"
    assert payload["mode"] == "shadow"
    assert payload["state"] == "open"
    assert payload["generation"] == 3
    assert payload["failure_classes"] == ["UnknownFailure", "HTTPError:503"]
    assert payload["counters"]["total_attempts"] == 11
    assert "private-query" not in record


@pytest.mark.asyncio
async def test_stale_double_failure_still_emits_telemetry_event() -> None:
    events: list[GenericSearchCircuitEvent] = []
    circuit = GenericSearchCircuit(
        mode="enforce",
        cooldown_seconds=120.0,
        clock=lambda: 10.0,
        telemetry_sink=events.append,
    )
    started = 0
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def primary() -> None:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()
        raise TimeoutError("private primary payload")

    async def fallback() -> None:
        raise ConnectionError("private fallback payload")

    first = asyncio.create_task(circuit.run(primary, fallback))
    second = asyncio.create_task(circuit.run(primary, fallback))
    await both_started.wait()
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(result, Exception) for result in results)
    assert [event.kind for event in events].count("attempt") == 2
    assert [event.kind for event in events].count("double_availability_failure") == 2
    assert [event.kind for event in events].count("open") == 1


def test_report_aggregates_active_and_rotated_logs_with_window_filtering(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 12, 20, 0, tzinfo=timezone.utc)
    log_dir = tmp_path / "app"
    log_dir.mkdir()
    (log_dir / "bot.log").write_text(
        _record("attempt", now - timedelta(hours=1))
        + _record("provider_error", now - timedelta(minutes=50))
        + _record("open", now - timedelta(minutes=40), state="open"),
        encoding="utf-8",
    )
    (log_dir / "bot.log.1").write_text(
        _record("attempt", now - timedelta(hours=23), pid=2222)
        + _record("double_availability_failure", now - timedelta(hours=22))
        + _record("blocked", now - timedelta(hours=21))
        + _record("attempt", now - timedelta(hours=25)),
        encoding="utf-8",
    )
    with gzip.open(log_dir / "bot.log.2.gz", "wt", encoding="utf-8") as handle:
        handle.write(_record("half_open", now - timedelta(hours=2)))
        handle.write(_record("would_open", now - timedelta(hours=2), state="open"))
        handle.write(_record("would_block", now - timedelta(hours=2), state="open"))

    report = build_report(log_dir=log_dir, since_hours=24, now=now)

    assert report["totals"] == {
        "attempts": 2,
        "double_availability_failures": 1,
        "open_transitions": 1,
        "would_open_transitions": 1,
        "blocked_calls": 1,
        "would_block_calls": 1,
        "probes": 1,
        "provider_error_events": 1,
    }
    assert report["latest"]["pid"] == 1234
    assert report["latest"]["state"] == "open"
    assert report["latest"]["generation"] == 2
    assert report["bounds"]["window_start"] == (now - timedelta(hours=24)).isoformat()
    assert report["bounds"]["window_end"] == now.isoformat()
    assert report["bounds"]["first_observed_at"] == (
        now - timedelta(hours=23)
    ).isoformat()
    assert report["bounds"]["last_observed_at"] == (now - timedelta(minutes=40)).isoformat()


def test_report_counts_malformed_and_unsupported_version_lines(tmp_path: Path) -> None:
    now = datetime(2026, 7, 12, 20, 0, tzinfo=timezone.utc)
    log_dir = tmp_path / "app"
    log_dir.mkdir()
    (log_dir / "errors.log").write_text(
        "unrelated application line\n"
        f"{GENERIC_SEARCH_CIRCUIT_LOG_PREFIX}not-json\n"
        + _record("attempt", now, schema_version=2)
        + _record("attempt", now, observed_at="not-a-timestamp")
        + _record("attempt", now, pid=None),
        encoding="utf-8",
    )

    report = build_report(log_dir=log_dir, since_hours=24, now=now)

    assert report["events_read"] == 0
    assert report["warnings"] == {
        "malformed_lines": 3,
        "unsupported_schema_versions": 1,
    }


def test_report_returns_zero_event_shape_for_missing_log_directory(tmp_path: Path) -> None:
    now = datetime(2026, 7, 12, 20, 0, tzinfo=timezone.utc)

    report = build_report(log_dir=tmp_path / "missing", since_hours=24, now=now)

    assert report["events_read"] == 0
    assert all(value == 0 for value in report["totals"].values())
    assert report["latest"] == {
        "observed_at": None,
        "pid": None,
        "mode": None,
        "state": None,
        "generation": None,
    }
    assert report["bounds"]["first_observed_at"] is None
    assert report["bounds"]["last_observed_at"] is None


def test_report_cli_runs_as_a_script(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "generic_search_circuit_report.py"),
            "--since-hours",
            "24",
            "--log-dir",
            str(tmp_path / "missing"),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["events_read"] == 0
