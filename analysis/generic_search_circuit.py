from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import re
import socket
import time
import urllib.error
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeVar


CircuitMode = Literal["off", "shadow", "enforce"]
CircuitState = Literal["closed", "open", "half_open"]
T = TypeVar("T")

_LOGGER = logging.getLogger(__name__)
GENERIC_SEARCH_CIRCUIT_LOG_PREFIX = "[GENERIC_SEARCH_CIRCUIT] "
GENERIC_SEARCH_CIRCUIT_SCHEMA_VERSION = 1
GENERIC_SEARCH_CIRCUIT_EVENT_KINDS = frozenset(
    {
        "attempt",
        "blocked",
        "closed",
        "double_availability_failure",
        "half_open",
        "open",
        "probe_cancelled",
        "provider_error",
        "would_block",
        "would_open",
    }
)
_SAFE_FAILURE_CLASS_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*(?::(?:[A-Za-z_][A-Za-z0-9_.]*|\d{3}))?$"
)


@dataclass(frozen=True)
class GenericSearchCircuitEvent:
    kind: str
    mode: CircuitMode
    state: CircuitState
    generation: int
    failure_classes: tuple[str, ...]
    cooldown_seconds: float
    remaining_cooldown_seconds: float
    total_attempts: int
    double_availability_failures: int
    open_transitions: int
    would_open_transitions: int
    blocked_calls: int
    would_block_calls: int
    probe_successes: int
    probe_failures: int


@dataclass(frozen=True)
class GenericSearchCircuitSnapshot:
    mode: CircuitMode
    state: CircuitState
    generation: int
    open_until: float
    last_failure_classes: tuple[str, ...]
    total_attempts: int
    double_availability_failures: int
    open_transitions: int
    would_open_transitions: int
    blocked_calls: int
    would_block_calls: int
    probe_successes: int
    probe_failures: int


class GenericSearchUnavailable(RuntimeError):
    pass


def generic_search_circuit_event_record(
    event: GenericSearchCircuitEvent,
    *,
    observed_at: datetime | None = None,
    pid: int | None = None,
) -> str:
    timestamp = observed_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    payload = {
        "schema_version": GENERIC_SEARCH_CIRCUIT_SCHEMA_VERSION,
        "observed_at": timestamp.astimezone(timezone.utc).isoformat(),
        "pid": os.getpid() if pid is None else pid,
        "event_kind": event.kind,
        "mode": event.mode,
        "state": event.state,
        "generation": event.generation,
        "failure_classes": [
            value if _SAFE_FAILURE_CLASS_RE.fullmatch(value) else "UnknownFailure"
            for value in event.failure_classes
        ],
        "cooldown_seconds": event.cooldown_seconds,
        "remaining_cooldown_seconds": event.remaining_cooldown_seconds,
        "counters": {
            "total_attempts": event.total_attempts,
            "double_availability_failures": event.double_availability_failures,
            "open_transitions": event.open_transitions,
            "would_open_transitions": event.would_open_transitions,
            "blocked_calls": event.blocked_calls,
            "would_block_calls": event.would_block_calls,
            "probe_successes": event.probe_successes,
            "probe_failures": event.probe_failures,
        },
    }
    return GENERIC_SEARCH_CIRCUIT_LOG_PREFIX + json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    )


_NETWORK_ERRNOS = frozenset(
    value
    for name in (
        "ECONNABORTED",
        "ECONNREFUSED",
        "ECONNRESET",
        "EHOSTDOWN",
        "EHOSTUNREACH",
        "ENETDOWN",
        "ENETRESET",
        "ENETUNREACH",
        "ETIMEDOUT",
    )
    if (value := getattr(errno, name, None)) is not None
)


def is_provider_availability_failure(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        return isinstance(reason, BaseException) and is_provider_availability_failure(reason)
    if isinstance(exc, (TimeoutError, socket.gaierror, ConnectionError)):
        return True
    return isinstance(exc, OSError) and exc.errno in _NETWORK_ERRNOS


def _failure_class(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"{type(exc).__name__}:{exc.code}"
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, BaseException):
        return f"{type(exc).__name__}:{type(exc.reason).__name__}"
    return type(exc).__name__


class GenericSearchCircuit:
    def __init__(
        self,
        *,
        mode: CircuitMode,
        cooldown_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
        event_sink: Callable[[GenericSearchCircuitEvent], None] | None = None,
        telemetry_sink: Callable[[GenericSearchCircuitEvent], None] | None = None,
    ) -> None:
        self._mode = mode
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._event_sink = event_sink
        self._telemetry_sink = telemetry_sink
        self._lock = asyncio.Lock()
        self._state: CircuitState = "closed"
        self._generation = 0
        self._half_open_owner: object | None = None
        self._open_until = 0.0
        self._last_failure_classes: tuple[str, ...] = ()
        self._total_attempts = 0
        self._double_availability_failures = 0
        self._open_transitions = 0
        self._would_open_transitions = 0
        self._blocked_calls = 0
        self._would_block_calls = 0
        self._probe_successes = 0
        self._probe_failures = 0

    async def run(
        self,
        primary: Callable[[], Awaitable[T]],
        fallback: Callable[[], Awaitable[T]],
    ) -> T:
        admission_generation, owner_token, can_open = await self._admit()
        try:
            try:
                result = await primary()
            except Exception as primary_exc:
                await self._emit_provider_error(primary_exc)
                try:
                    result = await fallback()
                except Exception as fallback_exc:
                    await self._emit_provider_error(fallback_exc)
                    failure_classes = (
                        _failure_class(primary_exc),
                        _failure_class(fallback_exc),
                    )
                    if not (
                        is_provider_availability_failure(primary_exc)
                        and is_provider_availability_failure(fallback_exc)
                    ):
                        event = await self._finish_owner_failure(
                            admission_generation,
                            owner_token,
                            failure_classes,
                        )
                        self._emit_event(event)
                        raise

                    event, double_failure_event = (
                        await self._record_double_availability_failure(
                        admission_generation,
                        owner_token,
                        can_open,
                        failure_classes,
                    )
                    )
                    self._emit_event(event)
                    self._emit_telemetry_event(double_failure_event)
                    diagnostic = ", ".join(failure_classes)
                    raise GenericSearchUnavailable(
                        f"generic search unavailable ({diagnostic})"
                    ) from None

            event = await self._finish_owner_success(
                admission_generation,
                owner_token,
            )
            self._emit_event(event)
            return result
        except asyncio.CancelledError:
            event = await self._release_cancelled_owner(
                admission_generation,
                owner_token,
            )
            self._emit_event(event)
            raise
        except BaseException as exc:
            event = await self._finish_owner_failure(
                admission_generation,
                owner_token,
                (_failure_class(exc),),
            )
            self._emit_event(event)
            raise

    def snapshot(self) -> GenericSearchCircuitSnapshot:
        return GenericSearchCircuitSnapshot(
            mode=self._mode,
            state=self._state,
            generation=self._generation,
            open_until=self._open_until,
            last_failure_classes=self._last_failure_classes,
            total_attempts=self._total_attempts,
            double_availability_failures=self._double_availability_failures,
            open_transitions=self._open_transitions,
            would_open_transitions=self._would_open_transitions,
            blocked_calls=self._blocked_calls,
            would_block_calls=self._would_block_calls,
            probe_successes=self._probe_successes,
            probe_failures=self._probe_failures,
        )

    async def _admit(self) -> tuple[int, object | None, bool]:
        event: GenericSearchCircuitEvent | None = None
        attempt_event: GenericSearchCircuitEvent
        owner_token: object | None = None
        can_open = False
        blocked = False
        async with self._lock:
            self._total_attempts += 1
            admission_generation = self._generation
            if self._mode == "off":
                can_open = True
            elif self._state == "closed":
                can_open = True
            elif self._state == "open":
                remaining = max(0.0, self._open_until - self._clock())
                if remaining > 0.0:
                    blocked, event = self._record_block_locked(remaining)
                else:
                    owner_token = object()
                    self._half_open_owner = owner_token
                    self._state = "half_open"
                    event = self._event_locked(
                        kind="half_open",
                        remaining_cooldown_seconds=0.0,
                    )
            else:
                blocked, event = self._record_block_locked(0.0)
            attempt_event = self._event_locked(
                kind="attempt",
                failure_classes=(),
                remaining_cooldown_seconds=max(
                    0.0,
                    self._open_until - self._clock(),
                ),
            )

        self._emit_telemetry_event(attempt_event)
        self._emit_event(event)
        if blocked:
            raise GenericSearchUnavailable("generic search circuit unavailable")
        return admission_generation, owner_token, can_open

    def _record_block_locked(
        self,
        remaining_cooldown_seconds: float,
    ) -> tuple[bool, GenericSearchCircuitEvent]:
        if self._mode == "shadow":
            self._would_block_calls += 1
            kind = "would_block"
            blocked = False
        else:
            self._blocked_calls += 1
            kind = "blocked"
            blocked = True
        return blocked, self._event_locked(
            kind=kind,
            remaining_cooldown_seconds=remaining_cooldown_seconds,
        )

    async def _record_double_availability_failure(
        self,
        admission_generation: int,
        owner_token: object | None,
        can_open: bool,
        failure_classes: tuple[str, ...],
    ) -> tuple[GenericSearchCircuitEvent | None, GenericSearchCircuitEvent]:
        async with self._lock:
            self._double_availability_failures += 1
            if self._mode == "off":
                self._last_failure_classes = failure_classes
                return None, self._event_locked(
                    kind="double_availability_failure",
                    failure_classes=failure_classes,
                    remaining_cooldown_seconds=0.0,
                )

            owns_half_open = self._owns_half_open_locked(
                admission_generation,
                owner_token,
            )
            owns_closed_generation = (
                can_open
                and owner_token is None
                and self._state == "closed"
                and self._generation == admission_generation
            )
            if not (owns_half_open or owns_closed_generation):
                return None, self._event_locked(
                    kind="double_availability_failure",
                    failure_classes=failure_classes,
                    remaining_cooldown_seconds=max(
                        0.0,
                        self._open_until - self._clock(),
                    ),
                )

            if owns_half_open:
                self._probe_failures += 1
            self._half_open_owner = None
            self._state = "open"
            self._generation += 1
            self._open_until = self._clock() + self._cooldown_seconds
            self._last_failure_classes = failure_classes
            if self._mode == "enforce":
                self._open_transitions += 1
                kind = "open"
            else:
                self._would_open_transitions += 1
                kind = "would_open"
            transition_event = self._event_locked(
                kind=kind,
                remaining_cooldown_seconds=self._cooldown_seconds,
            )
            return transition_event, self._event_locked(
                kind="double_availability_failure",
                failure_classes=failure_classes,
                remaining_cooldown_seconds=self._cooldown_seconds,
            )

    async def _emit_provider_error(self, exc: BaseException) -> None:
        async with self._lock:
            event = self._event_locked(
                kind="provider_error",
                failure_classes=(_failure_class(exc),),
                remaining_cooldown_seconds=max(
                    0.0,
                    self._open_until - self._clock(),
                ),
            )
        self._emit_telemetry_event(event)

    async def _finish_owner_success(
        self,
        admission_generation: int,
        owner_token: object | None,
    ) -> GenericSearchCircuitEvent | None:
        async with self._lock:
            if not self._owns_half_open_locked(admission_generation, owner_token):
                return None
            self._state = "closed"
            self._half_open_owner = None
            self._open_until = 0.0
            self._probe_successes += 1
            return self._event_locked(
                kind="closed",
                remaining_cooldown_seconds=0.0,
            )

    async def _finish_owner_failure(
        self,
        admission_generation: int,
        owner_token: object | None,
        failure_classes: tuple[str, ...],
    ) -> GenericSearchCircuitEvent | None:
        async with self._lock:
            if not self._owns_half_open_locked(admission_generation, owner_token):
                return None
            self._state = "closed"
            self._half_open_owner = None
            self._open_until = 0.0
            self._probe_failures += 1
            return self._event_locked(
                kind="closed",
                failure_classes=failure_classes,
                remaining_cooldown_seconds=0.0,
            )

    async def _release_cancelled_owner(
        self,
        admission_generation: int,
        owner_token: object | None,
    ) -> GenericSearchCircuitEvent | None:
        async with self._lock:
            if not self._owns_half_open_locked(admission_generation, owner_token):
                return None
            self._state = "open"
            self._half_open_owner = None
            return self._event_locked(
                kind="probe_cancelled",
                remaining_cooldown_seconds=max(
                    0.0,
                    self._open_until - self._clock(),
                ),
            )

    def _owns_half_open_locked(
        self,
        admission_generation: int,
        owner_token: object | None,
    ) -> bool:
        return (
            owner_token is not None
            and self._state == "half_open"
            and self._generation == admission_generation
            and self._half_open_owner is owner_token
        )

    def _event_locked(
        self,
        *,
        kind: str,
        remaining_cooldown_seconds: float,
        failure_classes: tuple[str, ...] | None = None,
    ) -> GenericSearchCircuitEvent:
        return GenericSearchCircuitEvent(
            kind=kind,
            mode=self._mode,
            state=self._state,
            generation=self._generation,
            failure_classes=(
                self._last_failure_classes
                if failure_classes is None
                else failure_classes
            ),
            cooldown_seconds=self._cooldown_seconds,
            remaining_cooldown_seconds=remaining_cooldown_seconds,
            total_attempts=self._total_attempts,
            double_availability_failures=self._double_availability_failures,
            open_transitions=self._open_transitions,
            would_open_transitions=self._would_open_transitions,
            blocked_calls=self._blocked_calls,
            would_block_calls=self._would_block_calls,
            probe_successes=self._probe_successes,
            probe_failures=self._probe_failures,
        )

    def _emit_event(self, event: GenericSearchCircuitEvent | None) -> None:
        self._emit_to_sink(self._event_sink, event)
        if self._telemetry_sink is not self._event_sink:
            self._emit_to_sink(self._telemetry_sink, event)

    def _emit_telemetry_event(self, event: GenericSearchCircuitEvent | None) -> None:
        self._emit_to_sink(self._telemetry_sink, event)

    @staticmethod
    def _emit_to_sink(
        sink: Callable[[GenericSearchCircuitEvent], None] | None,
        event: GenericSearchCircuitEvent | None,
    ) -> None:
        if event is None or sink is None:
            return
        try:
            sink(event)
        except BaseException as exc:
            _LOGGER.warning(
                "generic search circuit event sink failed (%s)",
                type(exc).__name__,
            )
