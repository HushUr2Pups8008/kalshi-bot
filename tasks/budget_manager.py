"""LLM budget admission control for accumulation-lane tasks.

This module is a guardrail only. It admits or queues LLM call intents under the
S2.7 hourly budgets; it does not execute LLM calls, schedule workers, score
evidence, update dossiers, or interact with trading.
"""

from __future__ import annotations

import heapq
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from numbers import Real
from typing import Callable

from utils.logger import trade_log

PER_MARKET_HOURLY_LIMIT = 4
GLOBAL_HOURLY_LIMIT = 60
WINDOW_SECONDS = 60 * 60
CIRCUIT_BREAKER_MULTIPLIER = 3


class BudgetManagerError(ValueError):
    """Raised for malformed budget-manager inputs."""


@dataclass(order=True, frozen=True)
class QueuedLLMRequest:
    """Queued LLM intent.

    Lower numeric priority values are higher priority, matching the existing
    news priority queue convention. Sequence provides deterministic FIFO ties.
    """

    priority: float
    sequence: int
    market_ticker: str = field(compare=False)
    requested_at: float = field(compare=False)


class BudgetManager:
    """Rolling-window LLM admission controller with priority backlog tracking."""

    def __init__(
        self,
        *,
        per_market_limit: int = PER_MARKET_HOURLY_LIMIT,
        global_limit: int = GLOBAL_HOURLY_LIMIT,
        window_seconds: float = WINDOW_SECONDS,
        circuit_breaker_multiplier: int = CIRCUIT_BREAKER_MULTIPLIER,
        clock: Callable[[], float] = time.monotonic,
        logger=trade_log,
    ) -> None:
        if per_market_limit <= 0:
            raise BudgetManagerError("per_market_limit must be positive")
        if global_limit <= 0:
            raise BudgetManagerError("global_limit must be positive")
        if window_seconds <= 0:
            raise BudgetManagerError("window_seconds must be positive")
        if circuit_breaker_multiplier <= 0:
            raise BudgetManagerError("circuit_breaker_multiplier must be positive")

        self.per_market_limit = per_market_limit
        self.global_limit = global_limit
        self.window_seconds = window_seconds
        self.circuit_breaker_threshold = global_limit * circuit_breaker_multiplier
        self._clock = clock
        self._logger = logger
        self._global_calls: deque[float] = deque()
        self._market_calls: dict[str, deque[float]] = defaultdict(deque)
        self._pending_heap: list[QueuedLLMRequest] = []
        self._sequence = 0
        self._circuit_breaker_open = False

    def request_llm_call(self, market_ticker: str, priority: float) -> bool:
        """Return True if an LLM call is admitted immediately.

        False means the request was not admitted. When budgets are exhausted and
        the circuit breaker is not open, the request is recorded in the internal
        priority backlog for pressure observability and future drain integration.
        When the backlog reaches 3x global budget depth, new enqueues are paused
        and ``BUDGET_PRESSURE`` is emitted once per breaker entry.
        """
        ticker = _validate_market_ticker(market_ticker)
        normalized_priority = _validate_priority(priority)
        now = self._clock()
        self._prune(now)

        if self._can_admit(ticker):
            self._record_admission(ticker, now)
            return True

        if self._queue_depth() >= self.circuit_breaker_threshold:
            self._enter_circuit_breaker(ticker, reason="queue_depth_exceeded")
            return False

        self._enqueue(ticker, normalized_priority, now)
        if self._queue_depth() >= self.circuit_breaker_threshold:
            self._enter_circuit_breaker(ticker, reason="queue_depth_reached")
        return False

    def pending_requests(self) -> tuple[QueuedLLMRequest, ...]:
        """Return queued requests in deterministic priority order."""
        return tuple(sorted(self._pending_heap))

    @property
    def circuit_breaker_open(self) -> bool:
        return self._circuit_breaker_open

    @property
    def queue_depth(self) -> int:
        return self._queue_depth()

    def _can_admit(self, market_ticker: str) -> bool:
        return (
            len(self._market_calls[market_ticker]) < self.per_market_limit
            and len(self._global_calls) < self.global_limit
        )

    def _record_admission(self, market_ticker: str, now: float) -> None:
        self._global_calls.append(now)
        self._market_calls[market_ticker].append(now)

    def _enqueue(self, market_ticker: str, priority: float, now: float) -> None:
        request = QueuedLLMRequest(
            priority=priority,
            sequence=self._sequence,
            market_ticker=market_ticker,
            requested_at=now,
        )
        self._sequence += 1
        heapq.heappush(self._pending_heap, request)

    def _queue_depth(self) -> int:
        return len(self._pending_heap)

    def _enter_circuit_breaker(self, market_ticker: str, *, reason: str) -> None:
        if self._circuit_breaker_open:
            return
        self._circuit_breaker_open = True
        self._logger.log_budget_pressure(
            market_ticker=market_ticker,
            reason=reason,
            queue_depth=self._queue_depth(),
            circuit_breaker_threshold=self.circuit_breaker_threshold,
            per_market_limit=self.per_market_limit,
            global_limit=self.global_limit,
            per_market_calls_last_hour=len(self._market_calls[market_ticker]),
            global_calls_last_hour=len(self._global_calls),
        )

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        _prune_deque(self._global_calls, cutoff)
        for market_ticker in list(self._market_calls):
            calls = self._market_calls[market_ticker]
            _prune_deque(calls, cutoff)
            if not calls:
                del self._market_calls[market_ticker]
        if self._circuit_breaker_open and self._queue_depth() < self.circuit_breaker_threshold:
            self._circuit_breaker_open = False


def _prune_deque(values: deque[float], cutoff: float) -> None:
    while values and values[0] <= cutoff:
        values.popleft()


def _validate_market_ticker(market_ticker: str) -> str:
    if not isinstance(market_ticker, str) or not market_ticker.strip():
        raise BudgetManagerError("market_ticker must be a non-empty string")
    return market_ticker.strip()


def _validate_priority(priority: float) -> float:
    if isinstance(priority, bool) or not isinstance(priority, Real):
        raise BudgetManagerError("priority must be numeric")
    return float(priority)


_default_manager: BudgetManager | None = None


def default_manager() -> BudgetManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = BudgetManager()
    return _default_manager


def request_llm_call(market_ticker: str, priority: float) -> bool:
    return default_manager().request_llm_call(market_ticker, priority)
