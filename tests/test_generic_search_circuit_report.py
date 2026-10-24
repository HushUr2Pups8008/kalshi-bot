from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from analysis.generic_search_circuit import (
    GENERIC_SEARCH_CIRCUIT_EVENT_KINDS,
    GENERIC_SEARCH_CIRCUIT_LOG_PREFIX,
    GenericSearchCircuit,
    GenericSearchCircuitEvent,
    generic_search_circuit_event_record,
)


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


def test_schema_v1_event_kind_enum_is_exact() -> None:
    assert GENERIC_SEARCH_CIRCUIT_EVENT_KINDS == {
        "attempt",
        "blocked",
        "closed",
        "double_availability_failure",
        "gate_provider_error_verdict",
        "half_open",
        "open",
        "probe_cancelled",
        "probe_failed",
        "probe_succeeded",
        "provider_error",
        "would_block",
        "would_open",
    }


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


@pytest.mark.asyncio
async def test_probe_outcomes_emit_explicit_telemetry_events() -> None:
    now = [0.0]
    events: list[GenericSearchCircuitEvent] = []
    circuit = GenericSearchCircuit(
        mode="enforce",
        cooldown_seconds=10.0,
        clock=lambda: now[0],
        telemetry_sink=events.append,
    )

    async def unavailable() -> None:
        raise TimeoutError("private provider payload")

    async def recovered() -> str:
        return "recovered"

    with pytest.raises(Exception):
        await circuit.run(unavailable, unavailable)
    now[0] = 10.0
    assert await circuit.run(recovered, unavailable) == "recovered"
    with pytest.raises(Exception):
        await circuit.run(unavailable, unavailable)
    now[0] = 20.0
    with pytest.raises(Exception):
        await circuit.run(unavailable, unavailable)

    event_kinds = [event.kind for event in events]
    assert event_kinds.count("probe_succeeded") == 1
    assert event_kinds.count("probe_failed") == 1
    succeeded = next(event for event in events if event.kind == "probe_succeeded")
    assert succeeded.failure_classes == ()
    assert "private provider payload" not in repr(events)
