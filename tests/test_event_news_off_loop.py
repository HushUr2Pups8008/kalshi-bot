"""Blocking order-path calls leave the event loop. A timeout places no order."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_event_news_grok_flags import (
    _analysis,
    _band_flags_unset,
    _executor,
    _quiet_live_side_effects,
    _resting,
)
from tests.test_equity_loss_halt import _executor as _equity_executor
from tests.test_equity_loss_halt import _politics
from tests.test_executor import _make_analysis
from trading.execution_terms import FinalExecutionTerms
from trading.executor import (
    _grok_hard_timeout_s,
    _kalshi_read_hard_timeout_s,
    _rules_hard_timeout_s,
)
from utils.event_news_grok_veto import GROK_TIMEOUT_SECONDS


def test_hard_timeouts_stay_above_the_http_timeouts() -> None:
    assert _grok_hard_timeout_s() > GROK_TIMEOUT_SECONDS
    assert _rules_hard_timeout_s() > GROK_TIMEOUT_SECONDS + 5.0
    assert _kalshi_read_hard_timeout_s(3) > 10.0 * 4 * 3


@pytest.mark.asyncio
async def test_slow_grok_does_not_stall_another_market(monkeypatch, tmp_path) -> None:
    _band_flags_unset(monkeypatch)
    monkeypatch.delenv("EVENT_NEWS_MAKER_FIRST", raising=False)
    monkeypatch.delenv("EVENT_NEWS_GROK_RULES_CHECK", raising=False)
    executor = _executor(monkeypatch, tmp_path)
    slow = _analysis()
    slow.market.ticker = "KXTRUTHSOCIAL-26OCT17-SLOW"
    fast = _analysis()
    fast.market.ticker = "KXTRUMPSAY-26OCT19-FAST"
    started = __import__("threading").Event()
    release = __import__("threading").Event()

    def veto(analysis, contracts=None):
        if str(analysis.market.ticker).endswith("SLOW"):
            started.set()
            release.wait(5)
        return True

    executor._rest.place_limit_order.return_value = _resting(
        "ord-fast",
        filled=8,
        status="executed",
    )
    journal, fill_log = _quiet_live_side_effects()
    plan = FinalExecutionTerms(89, 8, 7.12)
    with (
        patch(
            "utils.event_news_grok_veto.event_news_grok_veto_allows_live_post",
            side_effect=veto,
        ),
        journal,
        fill_log,
    ):
        slow_task = asyncio.create_task(
            executor._execute_live(slow, execution_plan=plan)
        )
        assert await asyncio.to_thread(started.wait, 2)
        began = time.monotonic()
        fast_id = await executor._execute_live(fast, execution_plan=plan)
        elapsed = time.monotonic() - began
        release.set()
        await slow_task
    assert fast_id == "ord-fast"
    assert elapsed < 0.5
    assert executor._rest.place_limit_order.call_count == 2


@pytest.mark.asyncio
async def test_grok_timeout_places_no_order_and_a_late_yes_does_not_post(
    monkeypatch, tmp_path
) -> None:
    _band_flags_unset(monkeypatch)
    monkeypatch.delenv("EVENT_NEWS_MAKER_FIRST", raising=False)
    monkeypatch.delenv("EVENT_NEWS_GROK_RULES_CHECK", raising=False)
    monkeypatch.setattr("trading.executor._grok_hard_timeout_s", lambda: 0.05)
    executor = _executor(monkeypatch, tmp_path)
    analysis = _analysis()
    finished = {"yes": False}

    def slow_yes(analysis, contracts=None):
        time.sleep(0.3)
        finished["yes"] = True
        return True

    executor._rest.place_limit_order.side_effect = AssertionError("posted")
    journal, fill_log = _quiet_live_side_effects()
    with (
        patch(
            "utils.event_news_grok_veto.event_news_grok_veto_allows_live_post",
            side_effect=slow_yes,
        ),
        journal,
        fill_log,
    ):
        order_id = await executor._execute_live(
            analysis,
            execution_plan=FinalExecutionTerms(89, 8, 7.12),
        )
        assert order_id is None
        await asyncio.sleep(0.4)
    assert finished["yes"] is True
    executor._rest.place_limit_order.assert_not_called()


@pytest.mark.asyncio
async def test_rules_timeout_places_no_order(monkeypatch, tmp_path) -> None:
    _band_flags_unset(monkeypatch)
    monkeypatch.delenv("EVENT_NEWS_MAKER_FIRST", raising=False)
    monkeypatch.setenv("EVENT_NEWS_GROK_RULES_CHECK", "true")
    monkeypatch.setattr("trading.executor._rules_hard_timeout_s", lambda: 0.05)
    executor = _executor(monkeypatch, tmp_path)
    analysis = _analysis()

    def slow_allow(analysis, *, live):
        time.sleep(0.3)
        return False

    executor._rest.place_limit_order.side_effect = AssertionError("posted")
    journal, fill_log = _quiet_live_side_effects()
    with (
        patch(
            "utils.event_news_rules_check.event_news_rules_check_skip",
            side_effect=slow_allow,
        ),
        patch(
            "utils.event_news_grok_veto.event_news_grok_veto_allows_live_post",
            return_value=True,
        ),
        journal,
        fill_log,
    ):
        order_id = await executor._execute_live(
            analysis,
            execution_plan=FinalExecutionTerms(89, 8, 7.12),
        )
        await asyncio.sleep(0.4)
    assert order_id is None
    executor._rest.place_limit_order.assert_not_called()


@pytest.mark.asyncio
async def test_slow_balance_read_does_not_stall_the_loop(monkeypatch, tmp_path) -> None:
    _politics(monkeypatch, tmp_path)
    executor, rest, _paper = _equity_executor(monkeypatch)

    def slow_balance():
        time.sleep(0.4)
        return 50.0

    rest.get_balance.side_effect = slow_balance
    rest._request.side_effect = RuntimeError("shadow read")
    analysis = _make_analysis(capped_dollars=2.0)
    loop_ran = asyncio.Event()

    async def other() -> None:
        await asyncio.sleep(0.05)
        loop_ran.set()

    began = time.monotonic()
    validate_task = asyncio.create_task(executor._validate_off_loop(analysis))
    other_task = asyncio.create_task(other())
    await other_task
    elapsed = time.monotonic() - began
    reason = await validate_task
    assert loop_ran.is_set()
    assert elapsed < 0.3
    assert reason is None


@pytest.mark.asyncio
async def test_maker_wait_does_not_block_the_next_order(monkeypatch, tmp_path) -> None:
    _band_flags_unset(monkeypatch)
    monkeypatch.setenv("EVENT_NEWS_MAKER_FIRST", "true")
    executor = _executor(monkeypatch, tmp_path)
    maker = _analysis()
    maker.executed_price_cents = 89
    fast = _analysis()
    fast.market.ticker = "KXTRUTHSOCIAL-26OCT17-FAST"
    fast.executed_price_cents = 89
    gate = asyncio.Event()
    slept: list[int] = []

    async def hold(seconds: int) -> None:
        slept.append(seconds)
        await gate.wait()

    executor._rest.place_limit_order.side_effect = [
        _resting("maker-ord"),
        _resting("fast-ord", filled=8, status="executed"),
        _resting("taker-ord", filled=8, status="executed"),
    ]
    executor._rest.cancel_order.return_value = True
    executor._rest.get_order_receipt.return_value = {
        "order_id": "maker-ord",
        "fill_count_fp": "0.00",
    }
    journal, fill_log = _quiet_live_side_effects()
    plan = FinalExecutionTerms(89, 8, 7.12)
    with (
        patch("trading.executor.asyncio.sleep", new=hold),
        patch(
            "utils.event_news_grok_veto.event_news_grok_veto_allows_live_post",
            return_value=True,
        ),
        patch(
            "trading.executor.fetch_kalshi_execution_liquidity",
            new=AsyncMock(),
        ),
        journal,
        fill_log,
    ):
        maker_id = await executor._execute_live(maker, execution_plan=plan)
        assert maker_id == "maker-ord"
        began = time.monotonic()
        fast_id = await executor._execute_live(
            fast,
            execution_plan=plan,
            maker_passthrough=True,
        )
        elapsed = time.monotonic() - began
        assert fast_id == "fast-ord"
        assert elapsed < 0.5
        assert slept == [180]
        gate.set()
        await executor._await_maker_waits()
    assert executor._rest.place_limit_order.call_count >= 2


@pytest.mark.asyncio
async def test_cash_halt_shutdown_stays_on_the_event_loop(monkeypatch) -> None:
    """The live callback is asyncio.create_task, so it has to run on the loop."""
    from tests.test_executor import _make_executor

    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    monkeypatch.delenv("EVENT_NEWS_EQUITY_LOSS_HALT", raising=False)
    executor, rest, _paper = _make_executor(
        monkeypatch,
        bankroll=500.0,
        loss_limit_pct=0.10,
    )
    rest.get_balance.side_effect = [500.0, 440.0]
    loop_thread = threading.get_ident()
    saw: list[int] = []
    scheduled = asyncio.Event()

    def callback() -> None:
        asyncio.create_task(asyncio.sleep(0))
        saw.append(threading.get_ident())
        scheduled.set()

    executor.set_shutdown_callback(callback)
    analysis = _make_analysis()
    assert await executor._validate_off_loop(analysis) is None
    reason = await executor._validate_off_loop(analysis)
    assert reason is not None and "HALTED" in reason
    await asyncio.wait_for(scheduled.wait(), timeout=1)
    assert saw == [loop_thread]
    assert executor._live_halted is True


@pytest.mark.asyncio
async def test_abandoned_cash_read_does_not_shutdown(monkeypatch) -> None:
    """A late balance after the hard timeout must not halt or shut down."""
    from tests.test_executor import _make_executor

    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    monkeypatch.delenv("EVENT_NEWS_EQUITY_LOSS_HALT", raising=False)
    executor, rest, _paper = _make_executor(
        monkeypatch,
        bankroll=500.0,
        loss_limit_pct=0.10,
    )
    callback = MagicMock()
    executor.set_shutdown_callback(callback)
    analysis = _make_analysis()
    rest.get_balance.return_value = 500.0
    assert await executor._validate_off_loop(analysis) is None

    def slow_balance() -> float:
        time.sleep(0.3)
        return 440.0

    rest.get_balance.side_effect = slow_balance
    with patch(
        "trading.executor._kalshi_read_hard_timeout_s",
        return_value=0.05,
    ):
        reason = await executor._validate_off_loop(analysis)
    await asyncio.sleep(0.4)
    assert reason == "live balance unreadable"
    callback.assert_not_called()
    assert executor._live_halted is False
