"""
Tests for trading/executor.py

Covers: live loss limit breach triggers halt, halt persists across subsequent calls,
        session start balance seeded on first balance check, retry logic on transient errors.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

import config as _cfg_module
from trading.executor import TradeExecutor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", bankroll)
    monkeypatch.setattr(_cfg_module.cfg, "live_loss_limit_pct", loss_limit_pct)
    monkeypatch.setattr(_cfg_module.cfg, "live_ticker_cooldown", 0)
    monkeypatch.setattr(_cfg_module.cfg, "paper_ticker_cooldown", 14400)
    monkeypatch.setattr(_cfg_module.cfg, "min_edge", 0.04)
    monkeypatch.setattr(_cfg_module.cfg, "max_ticker_exposure_pct", 1.0)

    rest   = MagicMock()
    paper  = MagicMock()
    paper.get_notional_bankroll.return_value = bankroll
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.is_concentration_ok.return_value = True
    paper.portfolio.exposure.return_value = 0.0

    ex = TradeExecutor(rest, paper)
    return ex, rest, paper


def _make_analysis(ticker="KXTEST-25DEC31", side="yes", yes_price=50.0,
                   edge=0.10, estimated_prob=0.60, capped_dollars=10.0):
    market = MagicMock()
    market.ticker    = ticker
    market.yes_price = yes_price
    market.yes_bid   = yes_price - 1
    market.yes_ask   = yes_price + 1
    market.yes_prob  = yes_price / 100.0
    market.status    = "active"

    news = MagicMock()
    news.headline = "Test"
    news.source   = "test"

    a = MagicMock()
    a.market             = market
    a.news_item          = news
    a.side               = side
    a.edge               = edge
    a.estimated_probability = estimated_prob
    a.market_yes_price   = yes_price
    a.capped_dollars     = capped_dollars
    a.confidence         = 0.8
    a.kelly_fraction     = 0.5
    a.kelly_dollars      = capped_dollars
    return a


# ---------------------------------------------------------------------------
# Loss limit
# ---------------------------------------------------------------------------

class TestLiveLossLimit:
    def test_no_breach_on_first_call(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
        assert not ex._check_live_loss_limit(500.0)
        assert ex._session_start_balance == 500.0

    def test_session_start_seeded_on_first_call(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        ex._check_live_loss_limit(480.0)
        assert ex._session_start_balance == 480.0

    def test_breach_when_loss_exceeds_limit(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
        ex._check_live_loss_limit(500.0)     # seeds at 500
        assert ex._check_live_loss_limit(449.0)  # loss=$51 >= limit=$50

    def test_no_breach_just_below_limit(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
        ex._check_live_loss_limit(500.0)
        assert not ex._check_live_loss_limit(451.0)  # loss=$49 < limit=$50

    def test_validate_returns_halt_message_on_breach(self, monkeypatch):
        ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
        # First call seeds start balance at 500; second call at 440 triggers breach
        rest.get_balance.side_effect = [500.0, 440.0]

        analysis = _make_analysis()
        # First call: seeds, no halt
        ex._validate(analysis)
        # Second call: breach
        reason = ex._validate(analysis)
        assert reason is not None
        assert "HALTED" in reason

    def test_halt_persists_after_breach(self, monkeypatch):
        ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
        rest.get_balance.side_effect = [500.0, 440.0, 500.0]  # "recovery" in 3rd call

        analysis = _make_analysis()
        ex._validate(analysis)   # seed
        ex._validate(analysis)   # breach -> halt
        reason = ex._validate(analysis)  # should still be halted even if balance recovered
        assert reason is not None
        assert "HALTED" in reason
        # get_balance should NOT be called after halt (short-circuit)
        assert rest.get_balance.call_count == 2

    def test_shutdown_callback_invoked_on_breach(self, monkeypatch):
        ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
        rest.get_balance.side_effect = [500.0, 440.0]

        callback = MagicMock()
        ex.set_shutdown_callback(callback)

        analysis = _make_analysis()
        ex._validate(analysis)  # seed
        ex._validate(analysis)  # breach
        callback.assert_called_once()


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

class TestOrderRetry:
    def test_is_retryable_transient(self):
        assert TradeExecutor._is_retryable_error("connection timeout")
        assert TradeExecutor._is_retryable_error("HTTP 503 Service Unavailable")
        assert TradeExecutor._is_retryable_error("HTTP 429 Too Many Requests")
        assert TradeExecutor._is_retryable_error("network reset by peer")

    def test_is_retryable_permanent(self):
        assert not TradeExecutor._is_retryable_error("HTTP 400 Bad Request")
        assert not TradeExecutor._is_retryable_error("HTTP 403 Forbidden")
        assert not TradeExecutor._is_retryable_error(None)
        assert not TradeExecutor._is_retryable_error("")

    @pytest.mark.asyncio
    async def test_execute_live_succeeds_on_first_attempt(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)

        rest  = MagicMock()
        paper = MagicMock()
        ex    = TradeExecutor(rest, paper)

        result = MagicMock()
        result.error     = None
        result.order_id  = "test-order-123"
        result.status    = "resting"
        result.filled    = 0
        rest.place_limit_order.return_value = result

        analysis = _make_analysis()
        with patch("trading.executor.trade_log"):
            order_id = await ex._execute_live(analysis)

        assert order_id == "test-order-123"
        assert rest.place_limit_order.call_count == 1

    @pytest.mark.asyncio
    async def test_execute_live_retries_on_transient_error(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)

        rest  = MagicMock()
        paper = MagicMock()
        ex    = TradeExecutor(rest, paper)

        fail_result = MagicMock()
        fail_result.error = "connection timeout"

        success_result = MagicMock()
        success_result.error    = None
        success_result.order_id = "retry-order-456"
        success_result.status   = "resting"
        success_result.filled   = 0

        rest.place_limit_order.side_effect = [fail_result, success_result]

        analysis = _make_analysis()
        with patch("trading.executor.trade_log"), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            order_id = await ex._execute_live(analysis)

        assert order_id == "retry-order-456"
        assert rest.place_limit_order.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_live_fails_after_3_attempts(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)

        rest  = MagicMock()
        paper = MagicMock()
        ex    = TradeExecutor(rest, paper)

        fail_result = MagicMock()
        fail_result.error = "HTTP 503 Service Unavailable"
        rest.place_limit_order.return_value = fail_result

        analysis = _make_analysis()
        with patch("trading.executor.trade_log"), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            order_id = await ex._execute_live(analysis)

        assert order_id is None
        assert rest.place_limit_order.call_count == 3

    @pytest.mark.asyncio
    async def test_execute_live_no_retry_on_permanent_error(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)

        rest  = MagicMock()
        paper = MagicMock()
        ex    = TradeExecutor(rest, paper)

        fail_result = MagicMock()
        fail_result.error = "HTTP 400 Bad Request -- invalid order"
        rest.place_limit_order.return_value = fail_result

        analysis = _make_analysis()
        with patch("trading.executor.trade_log"), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            order_id = await ex._execute_live(analysis)

        assert order_id is None
        # Must not retry on non-retryable errors
        assert rest.place_limit_order.call_count == 1
