import pytest

from tasks.budget_manager import (
    GLOBAL_HOURLY_LIMIT,
    PER_MARKET_HOURLY_LIMIT,
    BudgetManager,
    BudgetManagerError,
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SpyLogger:
    def __init__(self) -> None:
        self.budget_pressure_events: list[dict] = []

    def log_budget_pressure(self, **payload) -> None:
        self.budget_pressure_events.append(payload)


def _manager(
    *,
    clock: FakeClock | None = None,
    logger: SpyLogger | None = None,
    per_market_limit: int = PER_MARKET_HOURLY_LIMIT,
    global_limit: int = GLOBAL_HOURLY_LIMIT,
) -> tuple[BudgetManager, FakeClock, SpyLogger]:
    clock = clock or FakeClock()
    logger = logger or SpyLogger()
    return (
        BudgetManager(
            per_market_limit=per_market_limit,
            global_limit=global_limit,
            clock=clock,
            logger=logger,
        ),
        clock,
        logger,
    )


def test_per_market_budget_blocks_fifth_call_within_hour():
    manager, _, _ = _manager()

    results = [
        manager.request_llm_call("KXMARKET-26DEC31", priority=1)
        for _ in range(PER_MARKET_HOURLY_LIMIT + 1)
    ]

    assert results == [True, True, True, True, False]
    assert manager.queue_depth == 1
    assert manager.request_llm_call("KXOTHER-26DEC31", priority=1) is True


def test_global_budget_blocks_sixty_first_call_within_hour():
    manager, _, _ = _manager()

    results = [
        manager.request_llm_call(f"KXMARKET-{idx}", priority=1)
        for idx in range(GLOBAL_HOURLY_LIMIT + 1)
    ]

    assert results.count(True) == GLOBAL_HOURLY_LIMIT
    assert results[-1] is False
    assert manager.queue_depth == 1


def test_rolling_window_pruning_allows_calls_after_hour_expires():
    manager, clock, _ = _manager()

    for _ in range(PER_MARKET_HOURLY_LIMIT):
        assert manager.request_llm_call("KXMARKET-26DEC31", priority=1) is True
    assert manager.request_llm_call("KXMARKET-26DEC31", priority=1) is False

    clock.advance(3600.1)

    assert manager.request_llm_call("KXMARKET-26DEC31", priority=1) is True


def test_priority_queue_orders_lower_priority_number_first_with_fifo_ties():
    manager, _, _ = _manager(per_market_limit=1, global_limit=1)
    assert manager.request_llm_call("KXSEED", priority=1) is True

    assert manager.request_llm_call("KXLOW", priority=5) is False
    assert manager.request_llm_call("KXHIGH-1", priority=1) is False
    assert manager.request_llm_call("KXHIGH-2", priority=1) is False

    assert [
        (request.market_ticker, request.priority, request.sequence)
        for request in manager.pending_requests()
    ] == [
        ("KXHIGH-1", 1.0, 1),
        ("KXHIGH-2", 1.0, 2),
        ("KXLOW", 5.0, 0),
    ]


def test_circuit_breaker_triggers_at_three_times_global_budget_depth():
    manager, _, logger = _manager(per_market_limit=1, global_limit=2)
    assert manager.circuit_breaker_threshold == 6
    assert manager.request_llm_call("KXSEED-1", priority=1) is True
    assert manager.request_llm_call("KXSEED-2", priority=1) is True

    for idx in range(6):
        assert manager.request_llm_call(f"KXQUEUED-{idx}", priority=idx) is False

    assert manager.queue_depth == 6
    assert manager.circuit_breaker_open is True
    assert logger.budget_pressure_events == [
        {
            "market_ticker": "KXQUEUED-5",
            "reason": "queue_depth_reached",
            "queue_depth": 6,
            "circuit_breaker_threshold": 6,
            "per_market_limit": 1,
            "global_limit": 2,
            "per_market_calls_last_hour": 0,
            "global_calls_last_hour": 2,
        }
    ]


def test_circuit_breaker_rejects_new_enqueues_without_reemitting():
    manager, _, logger = _manager(per_market_limit=1, global_limit=1)
    assert manager.request_llm_call("KXSEED", priority=1) is True

    for idx in range(3):
        assert manager.request_llm_call(f"KXQUEUED-{idx}", priority=idx) is False
    assert manager.circuit_breaker_open is True

    assert manager.request_llm_call("KXREJECTED", priority=0) is False

    assert manager.queue_depth == 3
    assert len(logger.budget_pressure_events) == 1


def test_market_specific_pressure_does_not_block_unrelated_market():
    manager, _, _ = _manager()

    for _ in range(PER_MARKET_HOURLY_LIMIT):
        assert manager.request_llm_call("KXMARKET-26DEC31", priority=1) is True

    assert manager.request_llm_call("KXMARKET-26DEC31", priority=1) is False
    assert manager.request_llm_call("KXOTHER-26DEC31", priority=1) is True


@pytest.mark.parametrize("ticker", ["", "   ", None, 123])
def test_invalid_market_ticker_fails_clearly(ticker):
    manager, _, _ = _manager()

    with pytest.raises(BudgetManagerError, match="market_ticker"):
        manager.request_llm_call(ticker, priority=1)


@pytest.mark.parametrize("priority", ["high", None, True])
def test_invalid_priority_fails_clearly(priority):
    manager, _, _ = _manager()

    with pytest.raises(BudgetManagerError, match="priority"):
        manager.request_llm_call("KXMARKET-26DEC31", priority=priority)
