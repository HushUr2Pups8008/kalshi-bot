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
    a.llm_direction      = None
    a.llm_magnitude      = None
    a.llm_confidence     = None
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


# ---------------------------------------------------------------------------
# Skip reasons
# ---------------------------------------------------------------------------

class TestValidateSkipReasons:
    def test_live_zero_capped_dollars_skips(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        reason = ex._validate(_make_analysis(capped_dollars=0.0))
        assert reason == "capped_dollars=0 (below minimum bet size)"

    def test_edge_below_threshold_skips(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        reason = ex._validate(_make_analysis(edge=0.01, estimated_prob=0.51))
        assert reason is not None
        assert "below min_edge" in reason

    def test_closed_market_skips(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        analysis = _make_analysis()
        analysis.market.status = "closed"
        reason = ex._validate(analysis)
        assert reason == "market status=closed"

    def test_near_limit_price_skips(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        analysis = _make_analysis(yes_price=98.0)
        reason = ex._validate(analysis)
        assert reason == "price 98.0c is near limit (too illiquid)"

    def test_live_cooldown_skips(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        monkeypatch.setattr(_cfg_module.cfg, "live_ticker_cooldown", 600)
        ex._last_traded["KXTEST-25DEC31"] = 1000.0
        analysis = _make_analysis()
        with patch("trading.executor.time.monotonic", return_value=1030.0):
            reason = ex._validate(analysis)
        assert reason is not None
        assert "cooldown:" in reason

    def test_paper_cooldown_skips(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "paper_ticker_cooldown", 14400)
        rest = MagicMock()
        paper = MagicMock()
        paper.get_notional_bankroll.return_value = 500.0
        paper.portfolio.open_positions.return_value = []
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0
        ex = TradeExecutor(rest, paper)
        ex._last_traded["KXTEST-25DEC31"] = 1000.0
        analysis = _make_analysis()
        with patch("trading.executor.time.monotonic", return_value=1000.0 + 60):
            reason = ex._validate(analysis)
        assert reason is not None
        assert "paper cooldown:" in reason

    def test_opposing_position_skips(self, monkeypatch):
        ex, _, paper = _make_executor(monkeypatch)
        pos = MagicMock()
        pos.side = "no"
        pos.estimated_prob = 0.40
        paper.portfolio.open_positions.return_value = [pos]
        reason = ex._validate(_make_analysis(side="yes"))
        assert reason is not None
        assert "opposing position exists" in reason

    def test_same_signal_skips(self, monkeypatch):
        ex, _, paper = _make_executor(monkeypatch)
        pos = MagicMock()
        pos.side = "yes"
        pos.estimated_prob = 0.60
        pos.market_yes_price = 50.0
        paper.portfolio.open_positions.return_value = [pos]
        reason = ex._validate(_make_analysis(side="yes", estimated_prob=0.61, yes_price=51.0))
        assert reason is not None
        assert "same-signal skip" in reason

    def test_concentration_limit_skips(self, monkeypatch):
        ex, _, paper = _make_executor(monkeypatch)
        paper.portfolio.is_concentration_ok.return_value = False
        paper.portfolio.exposure.return_value = 80.0
        monkeypatch.setattr(_cfg_module.cfg, "max_ticker_exposure_pct", 0.10)
        reason = ex._validate(_make_analysis(capped_dollars=30.0))
        assert reason is not None
        assert "concentration limit:" in reason

    def test_insufficient_live_balance_skips(self, monkeypatch):
        ex, rest, _ = _make_executor(monkeypatch)
        rest.get_balance.return_value = 5.0
        reason = ex._validate(_make_analysis(capped_dollars=10.0))
        assert reason == "insufficient live balance ($5.00)"

    @pytest.mark.parametrize(
        ("yes_price", "is_paper", "expected_skip"),
        [
            (2.0, True, None),
            (1.0, True, "price 1.0c is near limit (too illiquid)"),
            (98.0, True, None),
            (99.0, True, "price 99.0c is near limit (too illiquid)"),
            (3.0, False, None),
            (2.0, False, "price 2.0c is near limit (too illiquid)"),
            (97.0, False, None),
            (98.0, False, "price 98.0c is near limit (too illiquid)"),
        ],
    )
    def test_price_boundary_behavior(self, monkeypatch, yes_price, is_paper, expected_skip):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", is_paper)
        rest = MagicMock()
        paper = MagicMock()
        paper.get_notional_bankroll.return_value = 500.0
        paper.portfolio.open_positions.return_value = []
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0
        ex = TradeExecutor(rest, paper)
        if not is_paper:
            rest.get_balance.return_value = 100.0
        reason = ex._validate(_make_analysis(yes_price=yes_price))
        assert reason == expected_skip

    @pytest.mark.parametrize(
        ("edge", "is_paper", "should_skip"),
        [
            (0.02, True, False),
            (0.0199, True, True),
            (0.04, False, False),
            (0.039, False, True),
            (-0.02, True, False),
            (-0.0199, True, True),
            (-0.04, False, False),
            (-0.039, False, True),
        ],
    )
    def test_edge_threshold_boundary(self, monkeypatch, edge, is_paper, should_skip):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", is_paper)
        monkeypatch.setattr(_cfg_module.cfg, "min_edge", 0.04)
        rest = MagicMock()
        paper = MagicMock()
        paper.get_notional_bankroll.return_value = 500.0
        paper.portfolio.open_positions.return_value = []
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0
        ex = TradeExecutor(rest, paper)
        if not is_paper:
            rest.get_balance.return_value = 100.0
        analysis = _make_analysis(edge=edge, estimated_prob=0.50 + edge, capped_dollars=10.0)
        reason = ex._validate(analysis)
        if should_skip:
            assert reason is not None
            assert "below min_edge" in reason
        else:
            assert reason is None

    def test_paper_duplicate_skip_reason_is_distinct_from_live_same_signal(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        rest = MagicMock()
        paper = MagicMock()
        paper.get_notional_bankroll.return_value = 500.0
        pos = MagicMock()
        pos.side = "yes"
        pos.estimated_prob = 0.60
        pos.market_yes_price = 50.0
        paper.portfolio.open_positions.return_value = [pos]
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0
        ex = TradeExecutor(rest, paper)

        reason = ex._validate(_make_analysis(side="yes", estimated_prob=0.61, yes_price=51.0))
        assert reason is not None
        assert "paper duplicate skip" in reason

    def test_live_same_signal_skip_reason_uses_generic_guard(self, monkeypatch):
        ex, rest, paper = _make_executor(monkeypatch)
        rest.get_balance.return_value = 100.0
        pos = MagicMock()
        pos.side = "yes"
        pos.estimated_prob = 0.60
        pos.market_yes_price = 50.0
        paper.portfolio.open_positions.return_value = [pos]

        reason = ex._validate(_make_analysis(side="yes", estimated_prob=0.61, yes_price=51.0))
        assert reason is not None
        assert "same-signal skip" in reason

    def test_halted_live_session_short_circuits_balance_check(self, monkeypatch):
        ex, rest, _ = _make_executor(monkeypatch)
        ex._live_halted = True
        reason = ex._validate(_make_analysis())
        assert reason == "LIVE HALTED: session loss limit reached -- all trading suspended"
        rest.get_balance.assert_not_called()


class TestStructuredBoundaryLogging:
    @pytest.mark.asyncio
    async def test_execute_logs_edge_context_on_skipped_trade(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        analysis = _make_analysis(edge=0.01, estimated_prob=0.51, yes_price=50.0)

        with patch("trading.executor.trade_log") as trade_log_mock:
            trade_id = await ex.execute(analysis)

        assert trade_id is None
        trade_log_mock.log_skipped.assert_called_once_with(
            reason="edge +0.0100 below min_edge 0.04",
            ticker=analysis.market.ticker,
            headline=analysis.news_item.headline[:80],
            source=analysis.news_item.source,
            method="keyword",
            llm_direction=None,
            llm_magnitude=None,
            model_probability=analysis.estimated_probability,
            market_price=analysis.market_yes_price,
            edge=analysis.edge,
            min_edge_threshold=0.04,
        )

    def test_log_skipped_computes_probability_price_diffs_with_precision(self):
        from utils.logger import TradeLogger

        logger = TradeLogger(path=MagicMock())
        with patch.object(logger, "_write") as write_mock:
            logger.log_skipped(
                reason="edge +0.0100 below min_edge 0.04",
                ticker="KXTEST-25DEC31",
                headline="Test headline",
                source="Reuters",
                method="keyword",
                model_probability=0.51234,
                market_price=0.50001,
                edge=0.01233,
                min_edge_threshold=0.04,
            )

        record = write_mock.call_args.args[0]
        assert record["model_probability"] == pytest.approx(0.5123)
        assert record["market_price"] == pytest.approx(0.5)
        assert record["signed_diff"] == pytest.approx(0.0123)
        assert record["absolute_diff"] == pytest.approx(0.0123)
        assert record["source"] == "Reuters"
        assert record["method"] == "keyword"

    @pytest.mark.asyncio
    async def test_execute_live_logs_edge_context_on_success(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "min_edge", 0.04)

        rest = MagicMock()
        paper = MagicMock()
        ex = TradeExecutor(rest, paper)

        result = MagicMock()
        result.error = None
        result.order_id = "live-order-123"
        result.status = "resting"
        result.filled = 0
        rest.place_limit_order.return_value = result

        analysis = _make_analysis(edge=0.10, estimated_prob=0.60, yes_price=50.0, capped_dollars=10.0)

        with patch("trading.executor.trade_log") as trade_log_mock:
            order_id = await ex._execute_live(analysis)

        assert order_id == "live-order-123"
        trade_log_mock.log_live_order.assert_called_once_with(
            order_id="live-order-123",
            ticker=analysis.market.ticker,
            side=analysis.side,
            contracts=19,
            price_cents=51,
            cost_dollars=9.69,
            status="resting",
            model_probability=analysis.estimated_probability,
            market_price=analysis.market_yes_price,
            edge=analysis.edge,
            min_edge_threshold=0.04,
        )
