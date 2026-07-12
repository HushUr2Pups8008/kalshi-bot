from __future__ import annotations

import errno
import socket
import time
import urllib.error
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, TypeVar


CircuitMode = Literal["off", "shadow", "enforce"]
CircuitState = Literal["closed", "open", "half_open"]
T = TypeVar("T")


@dataclass(frozen=True)
class GenericSearchCircuitEvent:
    kind: str
    mode: CircuitMode
    state: CircuitState
    generation: int
    failure_classes: tuple[str, ...]
    cooldown_seconds: float
    remaining_cooldown_seconds: float


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
    ) -> None:
        self._mode = mode
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._event_sink = event_sink
        self._state: CircuitState = "closed"
        self._generation = 0
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
        self._total_attempts += 1
        try:
            return await primary()
        except Exception as primary_exc:
            try:
                return await fallback()
            except Exception as fallback_exc:
                if not (
                    is_provider_availability_failure(primary_exc)
                    and is_provider_availability_failure(fallback_exc)
                ):
                    raise

                failure_classes = (
                    _failure_class(primary_exc),
                    _failure_class(fallback_exc),
                )
                self._record_double_availability_failure(failure_classes)
                diagnostic = ", ".join(failure_classes)
                raise GenericSearchUnavailable(
                    f"generic search unavailable ({diagnostic})"
                ) from None

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

    def _record_double_availability_failure(
        self,
        failure_classes: tuple[str, ...],
    ) -> None:
        self._double_availability_failures += 1
        self._last_failure_classes = failure_classes
        if self._mode == "off":
            return

        self._state = "open"
        self._generation += 1
        self._open_until = self._clock() + self._cooldown_seconds
        if self._mode == "enforce":
            self._open_transitions += 1
            kind = "open"
        else:
            self._would_open_transitions += 1
            kind = "would_open"
        if self._event_sink is not None:
            self._event_sink(
                GenericSearchCircuitEvent(
                    kind=kind,
                    mode=self._mode,
                    state=self._state,
                    generation=self._generation,
                    failure_classes=failure_classes,
                    cooldown_seconds=self._cooldown_seconds,
                    remaining_cooldown_seconds=self._cooldown_seconds,
                )
            )
