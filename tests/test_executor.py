"""
Tests for trading/executor.py

Covers: live loss limit breach triggers halt, halt persists across subsequent calls,
        session start balance seeded on first balance check, retry logic on transient errors.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
    # CR-D re-fetch: default to None so the executor falls back to the
    # candidate.market snapshot, which test fixtures already populate.
    # Tests that exercise the re-fetch path explicitly should override
    # `rest.get_market.return_value` to a fully-formed market.
    rest.get_market.return_value = None
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

    # Post-P0 pricing surface on the MagicMock market so executor reads
    # that go through `market.is_tradeable()` / `executed_price_cents`
    # see consistent values instead of MagicMock default attrs.
    market.price_available = True
    yes_int = max(0, min(100, int(round(yes_price))))
    market.yes_bid_cents = max(1, yes_int - 1)
    market.yes_ask_cents = min(99, yes_int + 1)
    market.no_bid_cents = max(1, 100 - yes_int - 1)
    market.no_ask_cents = min(99, 100 - yes_int + 1)
    market.is_tradeable = lambda: True

    a = MagicMock()
    a.market             = market
    a.news_item          = news
    a.side               = side
    a.edge               = edge
    a.estimated_probability = estimated_prob
    # P-5 LD-10 / P1-A: canonical executed-side ask cents. We use
    # yes_price directly to preserve legacy price-boundary semantics
    # (e.g. yes_price=98 should pass paper bounds at 98). Tests that
    # exercise live-order pricing override this explicitly.
    a.executed_price_cents = max(1, min(99, int(round(yes_price))))
    a.capped_dollars     = capped_dollars
    a.confidence         = 0.8
    a.kelly_fraction     = 0.5
    a.kelly_dollars      = capped_dollars
    a.llm_direction      = None
    a.llm_magnitude      = None
    a.llm_confidence     = None
    return a


def _make_blended_candidate(
    *,
    base_analysis=None,
    blended_probability=0.60,
    side="yes",
    signal_meta=None,
):
    base = base_analysis or _make_analysis(
        edge=blended_probability - 0.50,
        estimated_prob=blended_probability,
    )
    return SimpleNamespace(
        fast_lane_analysis=base,
        market=base.market,
        blended_probability=blended_probability,
        executed_price_cents=base.executed_price_cents,
        side=side,
        signal_meta=signal_meta or {
            "source_lane": "blend",
            "blended_p": blended_probability,
            "readiness_gate_min_edge_override": None,
        },
    )


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
        pos.entry_price_cents = 50.0
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
        pos.entry_price_cents = 50.0
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
        pos.entry_price_cents = 50.0
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


class TestBlendedCandidateCompatibility:
    @pytest.mark.asyncio
    async def test_execute_accepts_blended_candidate_and_uses_override_only_for_edge_gate(
        self,
        monkeypatch,
    ):
        ex, _, _ = _make_paper_executor(monkeypatch)
        ex._execute_paper = AsyncMock(return_value="paper-trade-id")
        # P-5 CR-D: blended_p chosen so the post-refetch executed-side edge
        # (0.525 - 0.51 = 0.015) exceeds the override threshold (0.01).
        candidate = _make_blended_candidate(
            blended_probability=0.525,
            signal_meta={
                "source_lane": "blend",
                "blended_p": 0.525,
                "readiness_gate_min_edge_override": 0.01,
            },
        )

        with patch("trading.executor.trade_log"):
            trade_id = await ex.execute(candidate)

        assert trade_id == "paper-trade-id"
        routed_analysis = ex._execute_paper.await_args.args[0]
        assert routed_analysis.estimated_probability == pytest.approx(0.525)
        # P-5 CR-D: edge re-derived against the executed-side ask cents
        # (yes_ask=51 here), not the legacy YES midpoint (50). For blended
        # YES side: 0.525 - 0.51 = 0.015.
        assert routed_analysis.edge == pytest.approx(0.015)
        assert routed_analysis.signal_type == "blend"
        assert routed_analysis.signal_meta == candidate.signal_meta

    @pytest.mark.asyncio
    async def test_absent_override_preserves_existing_min_edge_behavior(self, monkeypatch):
        ex, _, _ = _make_paper_executor(monkeypatch)
        ex._execute_paper = AsyncMock(return_value="paper-trade-id")
        candidate = _make_blended_candidate(
            blended_probability=0.515,
            signal_meta={
                "source_lane": "blend",
                "blended_p": 0.515,
                "readiness_gate_min_edge_override": None,
            },
        )

        with patch("trading.executor.trade_log") as trade_log_mock:
            trade_id = await ex.execute(candidate)

        assert trade_id is None
        ex._execute_paper.assert_not_called()
        trade_log_mock.log_skipped.assert_called_once()
        kwargs = trade_log_mock.log_skipped.call_args.kwargs
        # P-5 CR-D: edge re-derived against executed-side ask = 0.515-0.51=0.005.
        assert kwargs["reason"] == "edge +0.0050 below min_edge 0.02"
        assert kwargs["min_edge_threshold"] == pytest.approx(0.02)
        assert kwargs["signal_meta"] == candidate.signal_meta

    def test_override_validation_fails_closed_for_malformed_metadata(self, monkeypatch):
        ex, _, _ = _make_paper_executor(monkeypatch)
        analysis = _make_analysis(edge=0.015, estimated_prob=0.515)
        analysis.signal_meta = {"readiness_gate_min_edge_override": "0.01"}

        with pytest.raises(ValueError, match="readiness_gate_min_edge_override"):
            ex._validate(analysis)

    @pytest.mark.asyncio
    async def test_blended_signal_meta_logged_on_live_order_without_changing_pricing(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "min_edge", 0.04)

        rest = MagicMock()
        paper = MagicMock()
        ex = TradeExecutor(rest, paper)

        result = MagicMock()
        result.error = None
        result.order_id = "live-order-456"
        result.status = "resting"
        result.filled = 0
        rest.place_limit_order.return_value = result

        analysis = _make_analysis(
            edge=0.03,
            estimated_prob=0.53,
            yes_price=50.0,
            capped_dollars=10.0,
        )
        # P-5 LD-10: override fixture default to executed-side ask cents.
        analysis.executed_price_cents = 51
        analysis.signal_meta = {
            "source_lane": "blend",
            "blended_p": 0.53,
            "readiness_gate_min_edge_override": 0.03,
        }

        with patch("trading.executor.trade_log") as trade_log_mock:
            order_id = await ex._execute_live(analysis)

        assert order_id == "live-order-456"
        trade_log_mock.log_live_order.assert_called_once()
        kwargs = trade_log_mock.log_live_order.call_args.kwargs
        assert kwargs["contracts"] == 19
        assert kwargs["price_cents"] == 51
        assert kwargs["cost_dollars"] == pytest.approx(9.69)
        assert kwargs["min_edge_threshold"] == pytest.approx(0.03)
        assert kwargs["signal_meta"] == analysis.signal_meta


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
            market_price=float(analysis.executed_price_cents),
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
        # P-5 LD-10: legacy fixture maps executed_price_cents=int(yes_price).
        # The live-order semantics this test encodes expect the executed-side
        # ask (yes_ask=51 when yes_price=50), so override here.
        analysis.executed_price_cents = 51

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
            market_price=float(analysis.executed_price_cents),
            edge=analysis.edge,
            min_edge_threshold=0.04,
        )


# ---------------------------------------------------------------------------
# Execution-mode safety (mode frozen at construction, fail-closed guards)
# ---------------------------------------------------------------------------

def _make_paper_executor(monkeypatch):
    """Create a paper-mode executor -- mode frozen to paper at construction."""
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(_cfg_module.cfg, "live_loss_limit_pct", 0.10)
    monkeypatch.setattr(_cfg_module.cfg, "paper_ticker_cooldown", 14400)
    monkeypatch.setattr(_cfg_module.cfg, "min_edge", 0.04)
    monkeypatch.setattr(_cfg_module.cfg, "max_ticker_exposure_pct", 1.0)

    rest  = MagicMock()
    # CR-D re-fetch: default to None so executor falls back to the
    # candidate.market snapshot (which carries cents-level fields).
    rest.get_market.return_value = None
    paper = MagicMock()
    paper.get_notional_bankroll.return_value = 500.0
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.is_concentration_ok.return_value = True
    paper.portfolio.exposure.return_value = 0.0

    ex = TradeExecutor(rest, paper)
    assert ex._is_paper is True
    return ex, rest, paper


class TestExecutorModeSafety:
    def test_mode_frozen_at_construction_paper(self, monkeypatch):
        """Paper executor stays paper even if cfg is mutated to live after construction."""
        ex, _, _ = _make_paper_executor(monkeypatch)
        # Mutate global to live after construction -- must have no effect
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        assert ex._is_paper is True

    def test_mode_frozen_at_construction_live(self, monkeypatch):
        """Live executor stays live even if cfg is mutated to paper after construction."""
        ex, _, _ = _make_executor(monkeypatch)  # creates live executor
        assert ex._is_paper is False
        # Mutate global to paper after construction -- must have no effect
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        assert ex._is_paper is False

    @pytest.mark.asyncio
    async def test_paper_executor_routes_to_paper_not_live(self, monkeypatch):
        """execute() on a paper executor must call _execute_paper, never _execute_live."""
        ex, _, paper = _make_paper_executor(monkeypatch)

        recorded = []

        async def fake_paper(analysis):
            recorded.append("paper")
            return "paper-trade-id"

        async def fake_live(analysis):
            recorded.append("live")
            return "live-order-id"

        ex._execute_paper = fake_paper
        ex._execute_live  = fake_live

        analysis = _make_analysis(edge=0.05, estimated_prob=0.55, yes_price=50.0,
                                  capped_dollars=0.25)
        with patch("trading.executor.trade_log"):
            result = await ex.execute(analysis)

        assert result == "paper-trade-id"
        assert recorded == ["paper"]

    @pytest.mark.asyncio
    async def test_live_guard_blocks_paper_executor(self, monkeypatch):
        """_execute_live must return None and log [LIVE_GUARD] when called on a paper executor."""
        ex, _, _ = _make_paper_executor(monkeypatch)
        analysis = _make_analysis()
        result = await ex._execute_live(analysis)
        assert result is None

    def test_loss_limit_not_seeded_in_paper_mode(self, monkeypatch):
        """_check_live_loss_limit must return False and not seed balance for paper executors."""
        ex, _, _ = _make_paper_executor(monkeypatch)
        result = ex._check_live_loss_limit(500.0)
        assert result is False
        assert ex._session_start_balance is None

    def test_loss_limit_seeded_in_live_mode(self, monkeypatch):
        """_check_live_loss_limit must seed balance for live executors (existing behavior)."""
        ex, _, _ = _make_executor(monkeypatch, bankroll=500.0)
        result = ex._check_live_loss_limit(480.0)
        assert result is False
        assert ex._session_start_balance == 480.0

    def test_executor_state_log_emitted_at_init(self, monkeypatch, caplog):
        """[EXECUTOR_STATE] diagnostic log must be emitted at construction."""
        import logging
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        rest  = MagicMock()
        paper = MagicMock()
        paper.portfolio.tickers.return_value = []
        with caplog.at_level(logging.INFO, logger="executor"):
            TradeExecutor(rest, paper)
        state_logs = [r for r in caplog.records if "[EXECUTOR_STATE]" in r.message]
        assert len(state_logs) == 1
        assert "mode=paper" in state_logs[0].message


# ---------------------------------------------------------------------------
# MAC-ASYNC-001: _execute_paper must not block the event loop (record_trade)
# ---------------------------------------------------------------------------

class TestPaperExecutionAsync:
    """MAC-ASYNC-001 regression guard.

    Verifies that record_trade() and get_notional_bankroll() are dispatched
    via asyncio.to_thread() rather than called directly on the event loop
    thread.  If either call reverts to a synchronous direct call this test
    fails immediately.
    """

    @pytest.mark.asyncio
    async def test_record_trade_called_off_event_loop_thread(self, monkeypatch):
        import threading

        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)

        rest  = MagicMock()
        paper = MagicMock()
        paper.get_notional_bankroll.return_value = 500.0
        paper.portfolio.open_positions.return_value = []
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0

        ex = TradeExecutor(rest, paper)

        event_loop_thread = threading.current_thread().name
        record_trade_threads: list[str] = []

        original_record_trade = paper.record_trade.side_effect

        def tracking_record_trade(analysis):
            record_trade_threads.append(threading.current_thread().name)
            return "test-trade-id"

        paper.record_trade.side_effect = tracking_record_trade
        paper.record_trade.return_value = "test-trade-id"

        analysis = _make_analysis(edge=0.05, estimated_prob=0.55)

        with patch("trading.executor.trade_log"):
            trade_id = await ex._execute_paper(analysis)

        assert trade_id == "test-trade-id"
        assert record_trade_threads, "record_trade was never called"
        assert all(t != event_loop_thread for t in record_trade_threads), (
            f"record_trade was called on the event loop thread ({event_loop_thread!r}). "
            "It must be dispatched via asyncio.to_thread() — MAC-ASYNC-001."
        )

    @pytest.mark.asyncio
    async def test_execute_paper_returns_correct_trade_id_and_logs(self, monkeypatch):
        """End-to-end: trade_id flows through to_thread and is returned correctly."""
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)

        rest  = MagicMock()
        paper = MagicMock()
        paper.record_trade.return_value = "abc-123"
        paper.get_notional_bankroll.return_value = 475.50
        paper.portfolio.open_positions.return_value = []
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0

        ex      = TradeExecutor(rest, paper)
        analysis = _make_analysis(edge=0.05, estimated_prob=0.55, capped_dollars=10.0)

        with patch("trading.executor.trade_log"):
            trade_id = await ex._execute_paper(analysis)

        assert trade_id == "abc-123"
        paper.record_trade.assert_called_once_with(analysis)
        paper.get_notional_bankroll.assert_called_once()


# ---------------------------------------------------------------------------
# Coverage-focused tests — fill gaps identified in the 2026-04-23 baseline
# (cooldown seeding, input-validation branches, live-mode error paths, status)
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

from kalshi import OrderResult


class TestCooldownSeeding:
    """`_seed_cooldowns_from_db` — populates `_last_traded` from portfolio."""

    def _make_executor_with_portfolio(self, monkeypatch, *, tickers, latest_ts_map):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "paper_ticker_cooldown", 14400)  # 4h
        monkeypatch.setattr(_cfg_module.cfg, "live_ticker_cooldown", 0)

        paper = MagicMock()
        paper.portfolio.tickers.return_value = tickers
        paper.portfolio.latest_ts.side_effect = lambda t: latest_ts_map.get(t)
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0
        rest = MagicMock()
        return TradeExecutor(rest, paper)

    def test_seeds_cooldown_for_recent_ticker(self, monkeypatch):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        ex = self._make_executor_with_portfolio(
            monkeypatch,
            tickers={"KXRECENT-25DEC31"},
            latest_ts_map={"KXRECENT-25DEC31": recent},
        )
        assert "KXRECENT-25DEC31" in ex._last_traded

    def test_skips_ticker_with_no_latest_ts(self, monkeypatch):
        ex = self._make_executor_with_portfolio(
            monkeypatch,
            tickers={"KXNOTS-25DEC31"},
            latest_ts_map={"KXNOTS-25DEC31": None},
        )
        assert "KXNOTS-25DEC31" not in ex._last_traded

    def test_skips_ticker_outside_cooldown_window(self, monkeypatch):
        old = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        ex = self._make_executor_with_portfolio(
            monkeypatch,
            tickers={"KXOLD-25DEC31"},
            latest_ts_map={"KXOLD-25DEC31": old},
        )
        assert "KXOLD-25DEC31" not in ex._last_traded

    def test_handles_naive_timestamp_as_utc(self, monkeypatch):
        """Timestamps missing tzinfo are treated as UTC (not raising)."""
        naive = (datetime.now(timezone.utc) - timedelta(minutes=15)).replace(tzinfo=None).isoformat()
        ex = self._make_executor_with_portfolio(
            monkeypatch,
            tickers={"KXNAIVE-25DEC31"},
            latest_ts_map={"KXNAIVE-25DEC31": naive},
        )
        assert "KXNAIVE-25DEC31" in ex._last_traded


class TestSignalMetaValidation:
    """`_candidate_signal_meta` + `_signal_meta` — defensive branches."""

    def test_candidate_without_signal_meta_attr_returns_empty(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        candidate = SimpleNamespace(fast_lane_analysis=object(), market=object())
        assert ex._candidate_signal_meta(candidate) == {}

    def test_candidate_signal_meta_none_returns_empty(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        candidate = SimpleNamespace(signal_meta=None)
        assert ex._candidate_signal_meta(candidate) == {}

    def test_candidate_signal_meta_non_dict_raises(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        candidate = SimpleNamespace(signal_meta=["not", "a", "dict"])
        with pytest.raises(TypeError, match="signal_meta must be a dict"):
            ex._candidate_signal_meta(candidate)

    def test_analysis_without_signal_meta_attr_returns_empty(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        analysis = SimpleNamespace()
        assert ex._signal_meta(analysis) == {}

    def test_analysis_signal_meta_none_returns_empty(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        analysis = SimpleNamespace(signal_meta=None)
        assert ex._signal_meta(analysis) == {}

    def test_analysis_signal_meta_non_dict_raises(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        analysis = SimpleNamespace(signal_meta="oops")
        with pytest.raises(TypeError, match="signal_meta must be a dict"):
            ex._signal_meta(analysis)


class TestMinEdgeThreshold:
    def test_negative_override_raises_value_error(self, monkeypatch):
        ex, _, _ = _make_executor(monkeypatch)
        analysis = _make_analysis()
        analysis.signal_meta = {"readiness_gate_min_edge_override": -0.01}
        with pytest.raises(ValueError, match="must be non-negative"):
            ex._min_edge_threshold(analysis)


class TestExecutorStatus:
    def test_status_returns_expected_fields(self, monkeypatch):
        ex, _, paper = _make_executor(monkeypatch, bankroll=500.0)
        paper.portfolio.summary.return_value = {"open_positions": 0}
        out = ex.status()
        assert out["mode"] == "live"  # _make_executor sets is_paper_trading=False
        assert out["notional_bankroll"] == 500.0
        assert out["ticker_cooldowns"] == 0
        assert out["portfolio"] == {"open_positions": 0}


class TestLiveExecuteErrorPaths:
    """`_execute_live` error-path coverage (contracts=0, exception retries, fallthrough)."""

    @pytest.mark.asyncio
    async def test_zero_contracts_aborts_order(self, monkeypatch):
        """Defensive branch: if Kelly ever returns 0 contracts, live order
        must abort with a warning. `contracts_from_dollars` currently clamps
        to >=1 when price_cents>=1, so this path is unreachable by natural
        input — we patch the helper to exercise the defensive branch."""
        ex, _, _ = _make_executor(monkeypatch, bankroll=500.0)
        monkeypatch.setattr("trading.executor.contracts_from_dollars", lambda *a, **kw: 0)
        analysis = _make_analysis(yes_price=50.0, capped_dollars=10.0)
        result = await ex._execute_live(analysis)
        assert result is None

    @pytest.mark.asyncio
    async def test_order_exception_retries_then_fails(self, monkeypatch):
        ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0)
        rest.place_limit_order.side_effect = RuntimeError("boom")

        # Skip real backoff delays
        async def _no_sleep(_):
            return None
        monkeypatch.setattr("trading.executor.asyncio.sleep", _no_sleep)

        analysis = _make_analysis(yes_price=50.0, capped_dollars=10.0)
        with patch("trading.executor.trade_log"):
            result = await ex._execute_live(analysis)

        assert result is None
        assert rest.place_limit_order.call_count == 3

    @pytest.mark.asyncio
    async def test_order_non_retryable_error_fails_immediately(self, monkeypatch):
        ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0)
        # Non-retryable error (no transient markers) — should fail on first attempt
        rest.place_limit_order.return_value = OrderResult(
            order_id="", ticker="KXTEST-25DEC31", side="yes",
            contracts=0, price_cents=50, status="rejected", filled=0,
            error="invalid market",
        )

        analysis = _make_analysis(yes_price=50.0, capped_dollars=10.0)
        with patch("trading.executor.trade_log"):
            result = await ex._execute_live(analysis)

        assert result is None
        assert rest.place_limit_order.call_count == 1


# ---------------------------------------------------------------------------
# PROFIT-OBS-005 harness — never-traded sentinel must not trip the cooldown.
#
# Spec: docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md
# Status: pre-loaded during PROFIT-PHASE2-001 soak; do not implement before
# 2026-05-09. Until trading/executor.py:208 + :276 swap the sentinel from
# `0.0` to `float("-inf")` these tests xfail strictly.
#
# Approach: contract-pin via source inspection. The bug is silent in
# production (continuous uptime → time.monotonic() well past 14 400);
# replicating the bug at runtime requires either a new process or
# monkeypatching `time.monotonic`. Source-inspection avoids both and is
# stable against unrelated executor refactors — the assertion only fails
# when the literal sentinel at the two `_last_traded.get(...)` call sites
# changes.
# ---------------------------------------------------------------------------

_OBS005_XFAIL_REASON = (
    "PROFIT-OBS-005: never-traded cooldown sentinel still defaults to 0.0. "
    "Lands post-soak per docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md."
)


def _executor_source_lines() -> list[str]:
    import inspect
    import trading.executor as _ex_mod
    return inspect.getsource(_ex_mod).splitlines()


def _last_traded_get_lines() -> list[tuple[int, str]]:
    """Return (1-indexed line, stripped text) for every `_last_traded.get(...)` call site."""
    return [
        (i, line.strip())
        for i, line in enumerate(_executor_source_lines(), start=1)
        if "_last_traded.get(" in line
    ]


class TestCooldownSentinelOBS005:
    """Pin the post-fix sentinel default for never-traded tickers in `_validate`.

    Source-inspection contract: every `self._last_traded.get(...)` call site
    in `trading/executor.py` (the paper-mode site near line 208 and the
    live-mode site near line 276) must default to `float("-inf")` after
    PROFIT-OBS-005 lands. The set-on-trade write at line ~171 is unaffected.
    """

    def test_all_last_traded_get_call_sites_use_neg_inf_sentinel(self):
        """Every `_last_traded.get(ticker, <default>)` must default to float('-inf')."""
        sites = _last_traded_get_lines()
        # We expect at least the paper site and the live site.
        assert len(sites) >= 2, (
            f"expected at least 2 `_last_traded.get(...)` call sites; got {len(sites)}: {sites}"
        )
        bad_sites = [
            (lineno, text)
            for lineno, text in sites
            if 'float("-inf")' not in text and "float('-inf')" not in text
        ]
        assert bad_sites == [], (
            "post-fix invariant: every `_last_traded.get(ticker, <default>)` must use "
            "`float(\"-inf\")` as the sentinel; offending sites:\n"
            + "\n".join(f"  line {ln}: {tx}" for ln, tx in bad_sites)
        )

    def test_paper_cooldown_call_site_uses_neg_inf(self):
        """Paper path (executor.py:208 area) — sentinel pinned for the paper site."""
        sites = _last_traded_get_lines()
        # The paper-mode site sits near the `paper_ticker_cooldown` reference.
        src = _executor_source_lines()
        for lineno, text in sites:
            window = "\n".join(src[max(0, lineno - 1):min(len(src), lineno + 4)])
            if "paper_ticker_cooldown" in window:
                assert 'float("-inf")' in text or "float('-inf')" in text, (
                    f"paper-mode site at line {lineno} must use float('-inf') sentinel; got: {text}"
                )
                return
        pytest.fail("could not locate paper-mode `_last_traded.get(...)` call site")

    def test_live_cooldown_call_site_uses_neg_inf(self):
        """Live path (executor.py:276 area) — sentinel pinned for the live site."""
        sites = _last_traded_get_lines()
        src = _executor_source_lines()
        for lineno, text in sites:
            window = "\n".join(src[max(0, lineno - 1):min(len(src), lineno + 4)])
            if "live_ticker_cooldown" in window:
                assert 'float("-inf")' in text or "float('-inf')" in text, (
                    f"live-mode site at line {lineno} must use float('-inf') sentinel; got: {text}"
                )
                return
        pytest.fail("could not locate live-mode `_last_traded.get(...)` call site")

    def test_paper_never_traded_runtime_behavior_under_small_monotonic(self, monkeypatch):
        """F1 (Codex review): runtime-behavior pin to complement the source-inspection pins.

        Pre-fix: with `_last_traded.get(ticker, 0.0)` and `time.monotonic()`
        forced small (early process), `elapsed = monotonic() - 0.0` is far
        below `paper_ticker_cooldown=14400`, so a never-traded ticker trips
        the paper cooldown. This is the actual bug shape Codex flagged.
        Post-fix: sentinel becomes `float('-inf')`, `elapsed = +inf`, never
        trips. Behavior pin without depending on the literal sentinel text.

        Codex review of 56d641e (F3): assertion strengthened from "no
        cooldown string" to `_validate(...) is None` so a fully-passing
        analysis is required — catches a regression where the cooldown
        branch passes but a sibling branch starts blocking.
        """
        # Force monotonic small so the bug is observable under non-zero cooldown.
        monkeypatch.setattr("trading.executor.time.monotonic", lambda: 100.0)
        ex, _rest, _paper = _make_paper_executor(monkeypatch)
        assert ex._last_traded == {}
        assert ex._is_paper is True

        # Analysis fixture passes every other gate in `_validate`:
        # capped_dollars (paper skips); edge ≥ effective_min_edge=0.04;
        # market.status="active"; yes_price=50 (within paper [2,98]). Only the
        # cooldown branch decides the outcome on a never-traded ticker.
        analysis = _make_analysis(
            ticker="KXNEVERTRADED-26DEC31",
            yes_price=50.0,
            edge=0.10,
            estimated_prob=0.60,
            capped_dollars=10.0,
        )
        result = ex._validate(analysis)
        assert result is None, (
            "never-traded ticker must fully pass _validate post-fix "
            f"(no cooldown trip and no sibling-branch regression); got: {result!r}"
        )

    def test_ci_conftest_no_longer_zeroes_cooldowns(self):
        """Spec §7 acceptance: tests/conftest.py:_ci_stub_env must drop the
        PAPER_TICKER_COOLDOWN=0 / LIVE_TICKER_COOLDOWN=0 stubs once the fix lands.

        This test fails today because the stubs are still present (and required —
        without them, today's pre-fix tests would trip on never-traded tickers in
        CI). It xpasses post-fix when the stubs are removed.
        """
        from pathlib import Path
        conftest = Path(__file__).parent / "conftest.py"
        if not conftest.exists():
            pytest.skip("conftest.py not found")
        text = conftest.read_text()
        bad = []
        for needle in ("PAPER_TICKER_COOLDOWN", "LIVE_TICKER_COOLDOWN"):
            # The stub sets the env-var to "0"; we look for that exact pairing.
            if f'"{needle}"' in text and '"0"' in text and "_ci_stub_env" in text:
                # Heuristic: both names appear within the same stub block.
                bad.append(needle)
        assert bad == [], (
            f"conftest.py still zeroes cooldowns ({bad}); spec §7 requires removing "
            "these stubs once the executor sentinel fix lands."
        )
