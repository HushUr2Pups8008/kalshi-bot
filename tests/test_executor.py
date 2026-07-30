"""
Tests for trading/executor.py

Covers: live loss limit breach triggers halt, halt persists across subsequent calls,
        session start balance seeded on first balance check, and durable live submissions.
"""

import asyncio
import logging
import threading

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import config as _cfg_module
import trading.live_submission_hold as hold_module
from trading.executor import TradeExecutor, classify_skip_category
from trading.execution_terms import FinalExecutionTerms
from trading.venue import Venue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_classify_skip_category_groups_controllable_executor_reasons():
    assert classify_skip_category("paper cooldown: last trade 1.0h ago") == "cooldown"
    assert classify_skip_category("same-signal skip: open YES -- no new information") == "duplicate"
    assert classify_skip_category("paper duplicate skip: open YES") == "duplicate"
    assert classify_skip_category("per-prefix cap: 2 open in polymarket_us:abc") == "concentration"
    assert classify_skip_category("concentration limit: KX exposure would exceed cap") == "concentration"
    assert classify_skip_category("price 1.0c is near limit (too illiquid)") == "liquidity"
    assert classify_skip_category("market is not tradeable: price unavailable") == "liquidity"
    assert classify_skip_category("edge +0.0100 below min_edge 0.04") == "other"


def _make_executor(
    monkeypatch,
    bankroll=500.0,
    loss_limit_pct=0.10,
    live_submission_hold_path=None,
):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", bankroll)
    monkeypatch.setattr(_cfg_module.cfg, "live_loss_limit_pct", loss_limit_pct)
    monkeypatch.setattr(_cfg_module.cfg, "live_ticker_cooldown", 0)
    monkeypatch.setattr(_cfg_module.cfg, "paper_ticker_cooldown", 14400)
    monkeypatch.setattr(_cfg_module.cfg, "min_edge", 0.04)
    monkeypatch.setattr(_cfg_module.cfg, "max_ticker_exposure_pct", 1.0)

    rest = MagicMock()
    # CR-D re-fetch: default to None so the executor falls back to the
    # candidate.market snapshot, which test fixtures already populate.
    # Tests that exercise the re-fetch path explicitly should override
    # `rest.get_market.return_value` to a fully-formed market.
    rest.get_market.return_value = None
    paper = MagicMock()
    paper.get_notional_bankroll.return_value = bankroll
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.is_concentration_ok.return_value = True
    paper.portfolio.exposure.return_value = 0.0

    ex = TradeExecutor(
        rest,
        paper,
        live_submission_hold_path=live_submission_hold_path,
    )
    return ex, rest, paper


def _make_analysis(
    ticker="KXTEST-25DEC31", side="yes", yes_price=50.0, edge=0.10, estimated_prob=0.60, capped_dollars=10.0
):
    market = MagicMock()
    market.ticker = ticker
    market.yes_price = yes_price
    market.yes_bid = yes_price - 1
    market.yes_ask = yes_price + 1
    market.yes_prob = yes_price / 100.0
    market.status = "active"

    news = MagicMock()
    news.headline = "Test"
    news.source = "test"

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
    a.market = market
    a.news_item = news
    a.side = side
    a.edge = edge
    a.estimated_probability = estimated_prob
    # P-5 LD-10 / P1-A: canonical executed-side ask cents. We use
    # yes_price directly to preserve legacy price-boundary semantics
    # (e.g. yes_price=98 should pass paper bounds at 98). Tests that
    # exercise live-order pricing override this explicitly.
    a.executed_price_cents = max(1, min(99, int(round(yes_price))))
    a.capped_dollars = capped_dollars
    a.confidence = 0.8
    a.kelly_fraction = 0.5
    a.kelly_dollars = capped_dollars
    a.llm_direction = None
    a.llm_magnitude = None
    a.llm_confidence = None
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
        signal_meta=signal_meta
        or {
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
        ex._check_live_loss_limit(500.0)  # seeds at 500
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
        ex._validate(analysis)  # seed
        ex._validate(analysis)  # breach -> halt
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
# Live submission safety
# ---------------------------------------------------------------------------


class TestLiveSubmissionNoRetry:
    @pytest.mark.asyncio
    async def test_execute_live_succeeds_on_first_attempt(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)

        rest = MagicMock()
        paper = MagicMock()
        ex = TradeExecutor(
            rest,
            paper,
            live_submission_hold_path=tmp_path / "unknown_submission_holds.json",
        )

        result = MagicMock()
        result.error = None
        result.order_id = "test-order-123"
        result.status = "resting"
        result.filled = 0
        rest.place_limit_order.return_value = result

        analysis = _make_analysis()
        with patch("trading.executor.trade_log") as trade_log_mock:
            order_id = await ex._execute_live(analysis)

        assert order_id == "test-order-123"
        rest.place_limit_order.assert_called_once()
        trade_log_mock.log_live_submission_intent.assert_called_once()
        intent_kwargs = trade_log_mock.log_live_submission_intent.call_args.kwargs
        live_order_kwargs = trade_log_mock.log_live_order.call_args.kwargs
        assert intent_kwargs["submission_id"] == live_order_kwargs["submission_id"]
        assert len(intent_kwargs["submission_id"]) == 32

    @pytest.mark.asyncio
    async def test_fresh_executor_blocks_while_initial_post_is_reserved(
        self,
        monkeypatch,
        tmp_path,
    ):
        hold_path = tmp_path / "unknown_submission_holds.json"
        ex, rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        post_started = asyncio.Event()
        release_post = threading.Event()
        loop = asyncio.get_running_loop()

        def blocking_post(**_kwargs):
            loop.call_soon_threadsafe(post_started.set)
            release_post.wait(timeout=5)
            return MagicMock(error=None, order_id="first-order", status="resting", filled=0)

        rest.place_limit_order.side_effect = blocking_post
        analysis = _make_analysis()
        with patch("trading.executor.trade_log") as trade_log_mock:
            task = asyncio.create_task(ex._execute_live(analysis))
            await asyncio.wait_for(post_started.wait(), timeout=1)

            fresh_ex, fresh_rest, _ = _make_executor(
                monkeypatch,
                live_submission_hold_path=hold_path,
            )
            assert await fresh_ex._execute_live(analysis) is None
            fresh_rest.place_limit_order.assert_not_called()
            assert trade_log_mock.log_live_submission_intent.call_count == 1

            release_post.set()
            assert await asyncio.wait_for(task, timeout=1) == "first-order"

        rest.place_limit_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_successful_durable_journal_releases_reservation(
        self,
        monkeypatch,
        tmp_path,
    ):
        hold_path = tmp_path / "unknown_submission_holds.json"
        ex, rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        rest.place_limit_order.return_value = MagicMock(
            error=None,
            order_id="first-order",
            status="resting",
            filled=0,
        )
        analysis = _make_analysis()
        journal_checked = False

        with patch("trading.executor.trade_log") as trade_log_mock:

            async def durable_write(writer, *args, **kwargs):
                nonlocal journal_checked
                if writer is trade_log_mock.log_live_order:
                    journal_checked = True
                    blocked_ex, blocked_rest, _ = _make_executor(
                        monkeypatch,
                        live_submission_hold_path=hold_path,
                    )
                    assert await blocked_ex._execute_live(analysis) is None
                    blocked_rest.place_limit_order.assert_not_called()
                return writer(*args, **kwargs)

            with patch(
                "trading.executor.write_trade_log_async",
                side_effect=durable_write,
            ):
                assert await ex._execute_live(analysis) == "first-order"

            trade_log_mock.log_live_order.assert_called_once()

        assert journal_checked

        fresh_ex, fresh_rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        fresh_rest.place_limit_order.return_value = MagicMock(
            error=None,
            order_id="second-order",
            status="resting",
            filled=0,
        )
        with patch("trading.executor.trade_log"):
            assert await fresh_ex._execute_live(analysis) == "second-order"

        fresh_rest.place_limit_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_reservation_persistence_failure_makes_zero_posts(
        self,
        monkeypatch,
        tmp_path,
    ):
        hold_path = tmp_path / "unknown_submission_holds.json"
        ex, rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )

        def fail_replace(*_args):
            raise OSError("reservation replace unavailable")

        monkeypatch.setattr(hold_module.os, "replace", fail_replace)
        with patch("trading.executor.trade_log") as trade_log_mock:
            assert await ex._execute_live(_make_analysis()) is None

        trade_log_mock.log_live_submission_intent.assert_called_once()
        trade_log_mock.log_live_submission_unknown.assert_not_called()
        rest.place_limit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_failure_keeps_reservation_and_is_observable(
        self,
        monkeypatch,
        tmp_path,
        caplog,
    ):
        hold_path = tmp_path / "unknown_submission_holds.json"
        ex, rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        rest.place_limit_order.return_value = MagicMock(
            error=None,
            order_id="accepted-order",
            status="resting",
            filled=0,
        )
        real_replace = hold_module.os.replace
        replace_calls = 0

        def fail_release_replace(*args):
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls == 2:
                raise OSError("release replace unavailable")
            return real_replace(*args)

        monkeypatch.setattr(hold_module.os, "replace", fail_release_replace)
        with caplog.at_level(logging.ERROR, logger="executor"):
            with patch("trading.executor.trade_log"):
                assert await ex._execute_live(_make_analysis()) == "accepted-order"

        assert "reservation release failed" in caplog.text
        fresh_ex, fresh_rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        assert await fresh_ex._execute_live(_make_analysis()) is None
        fresh_rest.place_limit_order.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            "HTTP 429 Too Many Requests: internal request detail",
            "HTTP 503 Service Unavailable: internal request detail",
            "unexpected redirect response",
        ],
    )
    async def test_execute_live_error_result_holds_ticker_across_executors(
        self,
        monkeypatch,
        tmp_path,
        error,
    ):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)

        rest = MagicMock()
        paper = MagicMock()
        hold_path = tmp_path / "unknown_submission_holds.json"
        ex = TradeExecutor(
            rest,
            paper,
            live_submission_hold_path=hold_path,
        )

        fail_result = MagicMock()
        fail_result.error = error
        events = []

        def post_once(**_kwargs):
            events.append("post")
            return fail_result

        rest.place_limit_order.side_effect = post_once

        async def durable_write(writer, **kwargs):
            events.append(writer._mock_name)
            return writer(**kwargs)

        analysis = _make_analysis()
        analysis.signal_meta = {"lifecycle_id": "lc-live-unknown"}
        with (
            patch("trading.executor.trade_log") as trade_log_mock,
            patch(
                "trading.executor.write_trade_log_async",
                side_effect=durable_write,
            ),
        ):
            order_id = await ex._execute_live(analysis)

        assert order_id is None
        rest.place_limit_order.assert_called_once()
        trade_log_mock.log_live_submission_intent.assert_called_once()
        trade_log_mock.log_live_submission_unknown.assert_called_once()
        trade_log_mock.log_live_order.assert_not_called()
        intent_kwargs = trade_log_mock.log_live_submission_intent.call_args.kwargs
        unknown_kwargs = trade_log_mock.log_live_submission_unknown.call_args.kwargs
        assert intent_kwargs["venue"] == "kalshi"
        assert intent_kwargs["signal_meta"] == analysis.signal_meta
        assert unknown_kwargs["submission_id"] == intent_kwargs["submission_id"]
        assert unknown_kwargs["outcome"] == "error_result"
        assert unknown_kwargs["venue"] == "kalshi"
        assert unknown_kwargs["signal_meta"] == analysis.signal_meta
        for field in ("ticker", "side", "contracts", "price_cents", "cost_dollars"):
            assert unknown_kwargs[field] == intent_kwargs[field]
        assert error not in repr(unknown_kwargs)
        assert events == ["log_live_submission_intent", "post", "log_live_submission_unknown"]
        assert hold_path.exists()

        next_rest = MagicMock()
        next_ex = TradeExecutor(
            next_rest,
            MagicMock(),
            live_submission_hold_path=hold_path,
        )
        with patch("trading.executor.trade_log") as next_trade_log:
            next_order_id = await next_ex._execute_live(analysis)

        assert next_order_id is None
        next_rest.place_limit_order.assert_not_called()
        next_trade_log.log_live_submission_intent.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_live_exception_is_unknown_after_one_post(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)

        rest = MagicMock()
        paper = MagicMock()
        hold_path = tmp_path / "unknown_submission_holds.json"
        ex = TradeExecutor(
            rest,
            paper,
            live_submission_hold_path=hold_path,
        )

        exception_detail = "transport failure with secret request detail"
        rest.place_limit_order.side_effect = RuntimeError(exception_detail)

        analysis = _make_analysis()
        with patch("trading.executor.trade_log") as trade_log_mock:
            order_id = await ex._execute_live(analysis)

        assert order_id is None
        rest.place_limit_order.assert_called_once()
        trade_log_mock.log_live_submission_intent.assert_called_once()
        trade_log_mock.log_live_submission_unknown.assert_called_once()
        unknown_kwargs = trade_log_mock.log_live_submission_unknown.call_args.kwargs
        assert unknown_kwargs["outcome"] == "exception"
        assert exception_detail not in repr(unknown_kwargs)

        next_ex, next_rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        with patch("trading.executor.trade_log") as next_trade_log:
            next_order_id = await next_ex._execute_live(analysis)

        assert next_order_id is None
        next_rest.place_limit_order.assert_not_called()
        next_trade_log.log_live_submission_intent.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_post_holds_ticker_and_preserves_cancellation(
        self,
        monkeypatch,
        tmp_path,
    ):
        hold_path = tmp_path / "unknown_submission_holds.json"
        ex, rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        post_started = asyncio.Event()
        release_post = threading.Event()
        loop = asyncio.get_running_loop()

        def blocking_post(**_kwargs):
            loop.call_soon_threadsafe(post_started.set)
            release_post.wait(timeout=5)
            return MagicMock(error=None, order_id="late-order", status="resting", filled=0)

        rest.place_limit_order.side_effect = blocking_post
        analysis = _make_analysis()
        with patch("trading.executor.trade_log") as trade_log_mock:
            task = asyncio.create_task(ex._execute_live(analysis))
            await asyncio.wait_for(post_started.wait(), timeout=1)
            task.cancel()
            try:
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release_post.set()

        rest.place_limit_order.assert_called_once()
        unknown_kwargs = trade_log_mock.log_live_submission_unknown.call_args.kwargs
        assert unknown_kwargs["outcome"] == "cancelled"

        next_ex, next_rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        with patch("trading.executor.trade_log") as next_trade_log:
            next_order_id = await next_ex._execute_live(analysis)

        assert next_order_id is None
        next_rest.place_limit_order.assert_not_called()
        next_trade_log.log_live_submission_intent.assert_not_called()

    @pytest.mark.asyncio
    async def test_live_order_journal_failure_holds_ticker_across_executors(
        self,
        monkeypatch,
        tmp_path,
    ):
        hold_path = tmp_path / "unknown_submission_holds.json"
        ex, rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        rest.place_limit_order.return_value = MagicMock(
            error=None,
            order_id="accepted-order",
            status="resting",
            filled=0,
        )

        async def fail_live_order_journal(writer, **kwargs):
            if writer._mock_name == "log_live_order":
                raise OSError("live order journal unavailable")
            return writer(**kwargs)

        analysis = _make_analysis()
        with (
            patch("trading.executor.trade_log") as trade_log_mock,
            patch(
                "trading.executor.write_trade_log_async",
                side_effect=fail_live_order_journal,
            ),
        ):
            order_id = await ex._execute_live(analysis)

        assert order_id is None
        rest.place_limit_order.assert_called_once()
        unknown_kwargs = trade_log_mock.log_live_submission_unknown.call_args.kwargs
        assert unknown_kwargs["outcome"] == "live_order_journal_failure"
        assert unknown_kwargs["venue_order_id"] == "accepted-order"
        assert "live order journal unavailable" not in repr(unknown_kwargs)

        next_ex, next_rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        with patch("trading.executor.trade_log") as next_trade_log:
            next_order_id = await next_ex._execute_live(analysis)

        assert next_order_id is None
        next_rest.place_limit_order.assert_not_called()
        next_trade_log.log_live_submission_intent.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("receipt_id", [None, "", "unknown"])
    async def test_unverified_receipt_holds_ticker_across_executors(
        self,
        monkeypatch,
        tmp_path,
        receipt_id,
    ):
        hold_path = tmp_path / "unknown_submission_holds.json"
        ex, rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        rest.place_limit_order.return_value = MagicMock(
            error=None,
            order_id=receipt_id,
            status="resting",
            filled=0,
        )
        analysis = _make_analysis()

        with patch("trading.executor.trade_log") as trade_log_mock:
            order_id = await ex._execute_live(analysis)

        assert order_id is None
        rest.place_limit_order.assert_called_once()
        trade_log_mock.log_live_order.assert_not_called()
        unknown_kwargs = trade_log_mock.log_live_submission_unknown.call_args.kwargs
        assert unknown_kwargs["outcome"] == "unverified_receipt"

        next_ex, next_rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        with patch("trading.executor.trade_log") as next_trade_log:
            next_order_id = await next_ex._execute_live(analysis)

        assert next_order_id is None
        next_rest.place_limit_order.assert_not_called()
        next_trade_log.log_live_submission_intent.assert_not_called()

    @pytest.mark.asyncio
    async def test_unavailable_reservation_persistence_makes_zero_posts(
        self,
        monkeypatch,
        tmp_path,
    ):
        hold_path = tmp_path / "unknown_submission_holds.json"
        ex, rest, _ = _make_executor(
            monkeypatch,
            live_submission_hold_path=hold_path,
        )
        monkeypatch.setattr(
            ex._live_submission_holds,
            "_write",
            lambda *_args: (_ for _ in ()).throw(OSError("hold store unavailable")),
        )
        rest.place_limit_order.return_value = MagicMock(error="HTTP 503")

        with patch("trading.executor.trade_log") as trade_log_mock:
            order_id = await ex._execute_live(_make_analysis())

        assert order_id is None
        trade_log_mock.log_live_submission_intent.assert_called_once()
        trade_log_mock.log_live_submission_unknown.assert_not_called()
        rest.place_limit_order.assert_not_called()

        next_ex = ex
        next_rest = rest
        next_rest.place_limit_order.reset_mock()
        with patch("trading.executor.trade_log") as next_trade_log:
            next_order_id = await next_ex._execute_live(_make_analysis())

        assert next_order_id is None
        next_rest.place_limit_order.assert_not_called()
        next_trade_log.log_live_submission_intent.assert_not_called()

    @pytest.mark.asyncio
    async def test_intent_persistence_failure_blocks_post(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)

        rest = MagicMock()
        paper = MagicMock()
        ex = TradeExecutor(
            rest,
            paper,
            live_submission_hold_path=tmp_path / "unknown_submission_holds.json",
        )

        with patch(
            "trading.executor.write_trade_log_async",
            new_callable=AsyncMock,
            side_effect=OSError("intent store unavailable"),
        ) as write_mock:
            order_id = await ex._execute_live(_make_analysis())

        assert order_id is None
        write_mock.assert_awaited_once()
        rest.place_limit_order.assert_not_called()


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
    async def test_live_executor_blocks_research_paper_review_candidate(self, monkeypatch):
        ex, rest, _ = _make_executor(monkeypatch)
        ex._execute_live = AsyncMock(return_value="live-order-id")
        candidate = _make_blended_candidate(
            blended_probability=0.65,
            signal_meta={
                "source_lane": "blend",
                "blended_p": 0.65,
                "readiness_gate_min_edge_override": 0.01,
                "research_admission_status": "decision_grade_candidate",
                "research_run_id": "rr-decision-grade",
            },
        )

        with patch("trading.executor.trade_log") as trade_log_mock:
            trade_id = await ex.execute(candidate)

        assert trade_id is None
        ex._execute_live.assert_not_called()
        rest.get_balance.assert_not_called()
        trade_log_mock.log_skipped.assert_called_once()
        kwargs = trade_log_mock.log_skipped.call_args.kwargs
        assert kwargs["reason"] == "research_paper_review_live_block"
        assert kwargs["method"] == "research_decision_grade"
        assert kwargs["side"] == candidate.side
        assert kwargs["signal_meta"] == candidate.signal_meta

    @pytest.mark.asyncio
    async def test_non_kalshi_blended_candidate_skips_kalshi_refetch(self, monkeypatch):
        ex, rest, _ = _make_paper_executor(monkeypatch)
        ex._execute_paper = AsyncMock(return_value="paper-trade-id")
        base = _make_analysis(
            ticker="will-example-event-happen-2026",
            edge=0.23,
            estimated_prob=0.65,
            yes_price=42.0,
        )
        base.market.venue = Venue.POLYMARKET_US
        base.market.series_ticker = Venue.POLYMARKET_US.value
        base.market.status = "open"
        base.market.yes_ask_cents = 42
        base.market.no_ask_cents = 59
        candidate = _make_blended_candidate(
            base_analysis=base,
            blended_probability=0.65,
            signal_meta={
                "source_lane": "blend",
                "venue": "polymarket_us",
                "readiness_gate_min_edge_override": 0.01,
            },
        )

        with patch("trading.executor.trade_log"):
            trade_id = await ex.execute(candidate)

        assert trade_id == "paper-trade-id"
        rest.get_market.assert_not_called()
        routed_analysis = ex._execute_paper.await_args.args[0]
        assert routed_analysis.market.venue == Venue.POLYMARKET_US
        assert routed_analysis.market.ticker == "will-example-event-happen-2026"
        assert routed_analysis.executed_price_cents == 42
        assert routed_analysis.edge == pytest.approx(0.23)

    @pytest.mark.asyncio
    async def test_non_kalshi_blended_executor_skip_logs_venue(self, monkeypatch):
        ex, rest, _ = _make_paper_executor(monkeypatch)
        ex._execute_paper = AsyncMock(return_value="paper-trade-id")
        base = _make_analysis(
            ticker="will-example-event-happen-2026",
            edge=0.01,
            estimated_prob=0.43,
            yes_price=42.0,
        )
        base.market.venue = Venue.POLYMARKET_US
        base.market.series_ticker = Venue.POLYMARKET_US.value
        base.market.status = "open"
        base.market.yes_ask_cents = 42
        base.market.no_ask_cents = 59
        candidate = _make_blended_candidate(
            base_analysis=base,
            blended_probability=0.43,
            signal_meta={
                "source_lane": "blend",
                "venue": "polymarket_us",
                "readiness_gate_min_edge_override": None,
            },
        )

        with patch("trading.executor.trade_log") as trade_log_mock:
            trade_id = await ex.execute(candidate)

        assert trade_id is None
        rest.get_market.assert_not_called()
        ex._execute_paper.assert_not_called()
        trade_log_mock.log_skipped.assert_called_once()
        kwargs = trade_log_mock.log_skipped.call_args.kwargs
        assert kwargs["reason"] == "edge +0.0100 below min_edge 0.02"
        assert kwargs["venue"] == "polymarket_us"
        assert kwargs["side"] == candidate.side
        assert kwargs["signal_meta"] == candidate.signal_meta

    @pytest.mark.asyncio
    async def test_execute_accepts_blended_candidate_and_uses_override_only_for_edge_gate(
        self,
        monkeypatch,
    ):
        ex, _, _ = _make_paper_executor(monkeypatch)
        ex._execute_paper = AsyncMock(return_value="paper-trade-id")
        ex._final_execution_plan = AsyncMock(
            return_value=(
                FinalExecutionTerms(price_cents=51, contracts=1, cost_dollars=0.51),
                None,
            )
        )
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
        assert kwargs["venue"] == "kalshi"
        assert kwargs["side"] == candidate.side

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
            skip_category="other",
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
            venue="kalshi",
            side=analysis.side,
        )

    def test_log_skipped_computes_probability_price_diffs_with_precision(self):
        from utils.logger import TradeLogger

        logger = TradeLogger(path=MagicMock())
        with patch.object(logger, "_write") as write_mock:
            logger.log_skipped(
                reason="edge +0.0100 below min_edge 0.04",
                skip_category="other",
                ticker="KXTEST-25DEC31",
                headline="Test headline",
                source="Reuters",
                method="keyword",
                model_probability=0.51234,
                market_price=0.50001,
                edge=0.01233,
                min_edge_threshold=0.04,
                venue="polymarket_us",
            )

        record = write_mock.call_args.args[0]
        assert record["model_probability"] == pytest.approx(0.5123)
        assert record["market_price"] == pytest.approx(0.5)
        assert record["signed_diff"] == pytest.approx(0.0123)
        assert record["absolute_diff"] == pytest.approx(0.0123)
        assert record["source"] == "Reuters"
        assert record["method"] == "keyword"
        assert record["venue"] == "polymarket_us"
        assert record["skip_category"] == "other"

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
        intent_kwargs = trade_log_mock.log_live_submission_intent.call_args.kwargs
        trade_log_mock.log_live_order.assert_called_once_with(
            order_id="live-order-123",
            submission_id=intent_kwargs["submission_id"],
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
            venue="kalshi",
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

    rest = MagicMock()
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
    @pytest.mark.parametrize("capped_dollars", [0.0, -1.0])
    def test_paper_non_positive_capped_dollars_skips(
        self,
        monkeypatch,
        capped_dollars,
    ):
        ex, _, _ = _make_paper_executor(monkeypatch)

        reason = ex._validate(_make_analysis(capped_dollars=capped_dollars))

        assert reason == "capped_dollars=0 (below minimum bet size)"

    def test_paper_contract_rounding_cannot_exceed_capped_dollars(self, monkeypatch):
        ex, _, _ = _make_paper_executor(monkeypatch)

        reason = ex._validate(_make_analysis(capped_dollars=0.49, yes_price=50.0))

        assert reason == "G8_execution_depth_invalid_terms"

    def test_paper_contract_cost_cannot_exceed_notional_bankroll(self, monkeypatch):
        ex, _, paper = _make_paper_executor(monkeypatch)
        paper.get_notional_bankroll.return_value = 0.40

        reason = ex._validate(_make_analysis(capped_dollars=0.50, yes_price=50.0))

        assert reason == "paper contract cost $0.50 exceeds notional bankroll $0.40"

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

        async def fake_paper(analysis, *, execution_plan):
            recorded.append("paper")
            return "paper-trade-id"

        async def fake_live(analysis, *, execution_plan):
            recorded.append("live")
            return "live-order-id"

        ex._execute_paper = fake_paper
        ex._execute_live = fake_live
        ex._final_execution_plan = AsyncMock(
            return_value=(
                FinalExecutionTerms(price_cents=50, contracts=1, cost_dollars=0.50),
                None,
            )
        )

        analysis = _make_analysis(edge=0.05, estimated_prob=0.55, yes_price=50.0, capped_dollars=0.50)
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
        rest = MagicMock()
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

        rest = MagicMock()
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

        rest = MagicMock()
        paper = MagicMock()
        paper.record_trade.return_value = "abc-123"
        paper.get_notional_bankroll.return_value = 475.50
        paper.portfolio.open_positions.return_value = []
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0

        ex = TradeExecutor(rest, paper)
        analysis = _make_analysis(edge=0.05, estimated_prob=0.55, capped_dollars=10.0)

        with patch("trading.executor.trade_log"):
            trade_id = await ex._execute_paper(analysis)

        assert trade_id == "abc-123"
        paper.record_trade.assert_called_once_with(analysis)
        paper.get_notional_bankroll.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_paper_derives_fee_net_request_id_from_lifecycle(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
        monkeypatch.setattr(_cfg_module.cfg, "paper_cohort_id", "active-test")

        rest = MagicMock()
        paper = MagicMock()
        paper.record_trade_result.return_value = SimpleNamespace(
            trade_id="paper-entry-1",
            created=True,
        )
        paper.get_notional_bankroll.return_value = 499.25
        paper.portfolio.open_positions.return_value = []
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0
        executor = TradeExecutor(rest, paper)
        analysis = _make_analysis(edge=0.05, estimated_prob=0.55)
        analysis.signal_meta = {"lifecycle_id": "lc-" + "a" * 32}
        terms = FinalExecutionTerms(price_cents=50, contracts=1, cost_dollars=0.50)

        with patch("trading.executor.trade_log"):
            trade_id = await executor._execute_paper(analysis, execution_plan=terms)

        assert trade_id == "paper-entry-1"
        paper.record_trade_result.assert_called_once_with(
            analysis,
            entry_request_id="paper-entry:v1:active-test:lc-" + "a" * 32,
            execution_terms=terms,
        )

    @pytest.mark.asyncio
    async def test_execute_paper_rejects_missing_fee_net_lifecycle(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
        monkeypatch.setattr(_cfg_module.cfg, "paper_cohort_id", "active-test")

        rest = MagicMock()
        paper = MagicMock()
        paper.get_notional_bankroll.return_value = 500.0
        paper.portfolio.open_positions.return_value = []
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0
        executor = TradeExecutor(rest, paper)
        analysis = _make_analysis(edge=0.05, estimated_prob=0.55)
        analysis.signal_meta = {}

        with patch("trading.executor.trade_log"):
            trade_id = await executor._execute_paper(analysis)

        assert trade_id is None
        paper.record_trade.assert_not_called()
        paper.record_trade_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_paper_hides_idempotent_fee_net_replay_from_source_stats(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
        monkeypatch.setattr(_cfg_module.cfg, "paper_cohort_id", "active-test")

        rest = MagicMock()
        paper = MagicMock()
        paper.record_trade_result.return_value = SimpleNamespace(
            trade_id="existing-paper-entry",
            created=False,
        )
        paper.get_notional_bankroll.return_value = 499.25
        paper.portfolio.open_positions.return_value = []
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0
        executor = TradeExecutor(rest, paper)
        analysis = _make_analysis(edge=0.05, estimated_prob=0.55)
        analysis.signal_meta = {"lifecycle_id": "lc-" + "b" * 32}

        with patch("trading.executor.trade_log"):
            trade_id = await executor._execute_paper(analysis)

        assert trade_id == ""
        paper.record_trade_result.assert_called_once()


# ---------------------------------------------------------------------------
# Coverage-focused tests — fill gaps identified in the 2026-04-23 baseline
# (cooldown seeding, input-validation branches, live-mode error paths, status)
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone


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
    """`_execute_live` defensive-path coverage."""

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
        (i, line.strip()) for i, line in enumerate(_executor_source_lines(), start=1) if "_last_traded.get(" in line
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
        assert len(sites) >= 2, f"expected at least 2 `_last_traded.get(...)` call sites; got {len(sites)}: {sites}"
        bad_sites = [
            (lineno, text) for lineno, text in sites if 'float("-inf")' not in text and "float('-inf')" not in text
        ]
        assert bad_sites == [], (
            "post-fix invariant: every `_last_traded.get(ticker, <default>)` must use "
            '`float("-inf")` as the sentinel; offending sites:\n'
            + "\n".join(f"  line {ln}: {tx}" for ln, tx in bad_sites)
        )

    def test_paper_cooldown_call_site_uses_neg_inf(self):
        """Paper path (executor.py:208 area) — sentinel pinned for the paper site."""
        sites = _last_traded_get_lines()
        # The paper-mode site sits near the `paper_ticker_cooldown` reference.
        src = _executor_source_lines()
        for lineno, text in sites:
            window = "\n".join(src[max(0, lineno - 1) : min(len(src), lineno + 4)])
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
            window = "\n".join(src[max(0, lineno - 1) : min(len(src), lineno + 4)])
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


# ---------------------------------------------------------------------------
# PROFIT-ALIGN-004 (2026-05-25) — per-prefix open-position cap
# ---------------------------------------------------------------------------


class TestPerPrefixPositionCap:
    """Pins the cfg.max_open_positions_per_prefix gate in _validate.

    The matcher can produce multiple outcome-contract candidates in the same
    series (e.g. KXTXRUNOFFENDORSE-DJT-BOTH / -KPAX / -JCOR). Without this
    cap, one news event could open N concurrent bets against the same
    underlying topic.
    """

    def _setup(self, monkeypatch, *, open_in_prefix=None, cap=2):
        # Paper-mode executor — avoid live-only balance gates in _validate.
        # Mirrors the pattern used by test_paper_cooldown_skips.
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "max_open_positions_per_prefix", cap)
        monkeypatch.setattr(_cfg_module.cfg, "paper_ticker_cooldown", 0)
        monkeypatch.setattr(_cfg_module.cfg, "min_edge", 0.04)
        rest = MagicMock()
        paper = MagicMock()
        paper.get_notional_bankroll.return_value = 500.0
        paper.portfolio.open_positions.return_value = []
        paper.portfolio.is_concentration_ok.return_value = True
        paper.portfolio.exposure.return_value = 0.0
        paper.portfolio.open_positions_by_prefix = MagicMock(return_value=open_in_prefix or [])
        ex = TradeExecutor(rest, paper)
        return ex, paper

    def test_below_cap_passes(self, monkeypatch):
        ex, paper = self._setup(monkeypatch, open_in_prefix=[], cap=2)
        a = _make_analysis(ticker="KXTXRUNOFFENDORSE-26MAY26-DJT-BOTH", side="yes", yes_price=50.0, edge=0.10)
        reason = ex._validate(a)
        assert reason is None or "per-prefix cap" not in (reason or ""), (
            f"empty prefix should not trigger cap; got: {reason}"
        )

    def test_at_cap_blocks(self, monkeypatch):
        # 2 already open in KXTXRUNOFFENDORSE prefix
        existing = [
            SimpleNamespace(ticker="KXTXRUNOFFENDORSE-26MAY26-DJT-BOTH"),
            SimpleNamespace(ticker="KXTXRUNOFFENDORSE-26MAY26-DJT-KPAX"),
        ]
        ex, paper = self._setup(monkeypatch, open_in_prefix=existing, cap=2)
        a = _make_analysis(ticker="KXTXRUNOFFENDORSE-26MAY26-DJT-JCOR", side="yes", yes_price=50.0, edge=0.10)
        reason = ex._validate(a)
        assert reason is not None
        assert "per-prefix cap" in reason
        assert "KXTXRUNOFFENDORSE" in reason

    def test_different_prefix_unaffected(self, monkeypatch):
        # 2 open in KXTRUMPIRAN, but trade is on KXTXRUNOFFENDORSE
        existing = [
            SimpleNamespace(ticker="KXTRUMPIRAN-26JUN01"),
            SimpleNamespace(ticker="KXTRUMPIRAN-26JUL01"),
        ]
        ex, paper = self._setup(monkeypatch, open_in_prefix=[], cap=2)
        # _setup wired open_positions_by_prefix to return [] regardless
        # of args; we want to assert different-prefix lookup returns 0
        paper.portfolio.open_positions_by_prefix = MagicMock(
            side_effect=lambda pfx: existing if pfx == "KXTRUMPIRAN" else []
        )
        a = _make_analysis(ticker="KXTXRUNOFFENDORSE-26MAY26-DJT-BOTH", side="yes", yes_price=50.0, edge=0.10)
        reason = ex._validate(a)
        assert reason is None or "per-prefix cap" not in (reason or "")

    def test_cap_disabled_when_zero(self, monkeypatch):
        # cfg.max_open_positions_per_prefix = 0 → cap disabled even with N open
        existing = [SimpleNamespace(ticker="KX-A"), SimpleNamespace(ticker="KX-B")]
        ex, paper = self._setup(monkeypatch, open_in_prefix=existing, cap=0)
        a = _make_analysis(ticker="KX-C", side="yes", yes_price=50.0, edge=0.10)
        reason = ex._validate(a)
        assert reason is None or "per-prefix cap" not in (reason or "")

    # ------------------------------------------------------------------
    # PROFIT-VENUE-PARITY-001 — per-contest exposure key (fix/pm-exposure-cap-granularity)
    #
    # The cap key MUST be the *correlated-exposure* family, not the raw
    # split('-',1)[0] token. For Kalshi the leading token IS the contest
    # series (correct). For Polymarket the leading slug token ('ewc') is a
    # market-MAKER prefix shared across ~30 INDEPENDENT contests (Maine
    # Senate, Georgia Senate, Iowa Governor, ...). Lumping them into one
    # 2-slot bucket over-throttles the venue whose binding constraint is
    # opportunity throughput. The correct key is the canonical per-contest
    # family stem from pm_domain_key, namespace-stripped so it startswith-
    # matches the ticker (Portfolio.open_positions_by_prefix semantics).
    #
    # The mocks below reproduce the REAL open_positions_by_prefix matcher
    # (ticker == prefix OR ticker.startswith(prefix+'-')) so the assertion
    # turns purely on which prefix the executor derives.
    # ------------------------------------------------------------------

    @staticmethod
    def _real_prefix_matcher(open_tickers):
        """Mirror Portfolio.open_positions_by_prefix's exact-or-startswith match."""
        positions = [SimpleNamespace(ticker=t) for t in open_tickers]

        def lookup(prefix):
            if not prefix:
                return []
            return [p for p in positions if p.ticker == prefix or p.ticker.startswith(f"{prefix}-")]

        return lookup

    def _pm_analysis(self, ticker, side="yes"):
        a = _make_analysis(ticker=ticker, side=side, yes_price=50.0, edge=0.10)
        # PolymarketExecutionMarket carries .venue == 'polymarket_us'; this is
        # how the executor distinguishes PM markets from Kalshi ones.
        a.market.venue = Venue.POLYMARKET_US.value
        return a

    def test_pm_independent_contests_not_lumped(self, monkeypatch):
        # MONEY ASSERTION: two open positions in DIFFERENT ewc contests
        # (Maine Senate + Iowa Governor) must NOT block a THIRD position in
        # yet another independent ewc contest (Georgia Senate). Pre-fix all
        # three collapsed to the 'ewc' bucket → blocked at cap=2. The fix
        # keys on the per-contest stem ('ewc-usse-ga') which the two open
        # contests do not match → 0 in bucket → passes.
        open_tickers = [
            "ewc-usse-me-2026-11-03-dem",  # Maine Senate
            "ewc-usgub-ia-2026-11-03-dem",  # Iowa Governor
        ]
        ex, paper = self._setup(monkeypatch, cap=2)
        paper.portfolio.open_positions_by_prefix = MagicMock(side_effect=self._real_prefix_matcher(open_tickers))
        a = self._pm_analysis("ewc-usse-ga-2026-11-03-dem")  # Georgia Senate
        reason = ex._validate(a)
        assert reason is None or "per-prefix cap" not in (reason or ""), (
            f"independent PM contests must not share an exposure bucket; got over-throttle: {reason}"
        )

    def test_pm_same_contest_outcomes_capped_together(self, monkeypatch):
        # CORRELATION CONTROL: two outcomes of the SAME contest (Maine
        # Senate dem + rep) are correlated exposure and MUST be capped
        # together. A third position in that same contest IS blocked.
        # pm_domain_key maps all three to 'polymarket_us:ewc-usse-me', so the
        # stem 'ewc-usse-me' startswith-matches both open outcomes → cap hit.
        open_tickers = [
            "ewc-usse-me-2026-11-03-dem",
            "ewc-usse-me-2026-11-03-rep",
        ]
        ex, paper = self._setup(monkeypatch, cap=2)
        paper.portfolio.open_positions_by_prefix = MagicMock(side_effect=self._real_prefix_matcher(open_tickers))
        a = self._pm_analysis("ewc-usse-me-2026-11-03-grn")  # 3rd outcome, same contest
        reason = ex._validate(a)
        assert reason is not None and "per-prefix cap" in reason, (
            f"same-contest multi-outcome correlation control must still fire; got: {reason}"
        )
        # Assert on the DERIVED-PREFIX token in the reason ("open in <prefix>"),
        # not a bare 'ewc-usse-me' substring — the latter also appears in the
        # open=[...] ticker list pre-fix, making the check tautological. Pre-fix
        # the reason reads "open in ewc"; only the fix makes it the per-contest
        # stem "ewc-usse-me", so this distinguishes fixed from broken behavior.
        assert "open in ewc-usse-me " in reason, (
            "cap must key on the per-contest stem 'ewc-usse-me', not the coarse "
            f"market-maker prefix 'ewc'; got: {reason}"
        )

    def test_pm_tagged_market_with_kx_ticker_does_not_crash(self):
        # DEFENSIVE: pm_domain_key raises ValueError on KX* tickers. A market
        # tagged venue='polymarket_us' but carrying a KX* ticker is impossible
        # today, but the cap key derivation runs on EVERY trade decision, so an
        # uncaught raise there would crash the decision path. The helper must
        # swallow it and fall back to the coarse stem (over-group, not
        # over-trade — the safe direction for a cap), never propagate.
        from trading.executor import _correlated_exposure_prefix

        market = SimpleNamespace(ticker="KXFOO-27-26JUL", venue=Venue.POLYMARKET_US.value)
        assert _correlated_exposure_prefix(market) == "KXFOO"

    def test_kalshi_prefix_byte_identical_blocks(self, monkeypatch):
        # KALSHI BYTE-IDENTICAL: two open KXUSAIRANAGREEMENT-* outcomes block
        # a third in the same series. Kalshi keeps split('-',1)[0] semantics;
        # the fix must NOT change this. (No .venue attr → Kalshi default.)
        open_tickers = [
            "KXUSAIRANAGREEMENT-27-26JUL",
            "KXUSAIRANAGREEMENT-27-26AUG",
        ]
        ex, paper = self._setup(monkeypatch, cap=2)
        paper.portfolio.open_positions_by_prefix = MagicMock(side_effect=self._real_prefix_matcher(open_tickers))
        a = _make_analysis(ticker="KXUSAIRANAGREEMENT-27-26SEP", side="yes", yes_price=50.0, edge=0.10)
        reason = ex._validate(a)
        assert reason is not None and "per-prefix cap" in reason, (
            f"Kalshi same-series cap must still block; got: {reason}"
        )
        assert "KXUSAIRANAGREEMENT" in reason

    def test_kalshi_different_series_byte_identical_unaffected(self, monkeypatch):
        # KALSHI BYTE-IDENTICAL: two KXFOO-* positions must NOT block a
        # KXBAR-* position. Distinct series → distinct split('-',1)[0] key.
        open_tickers = ["KXFOO-26JUN01", "KXFOO-26JUL01"]
        ex, paper = self._setup(monkeypatch, cap=2)
        paper.portfolio.open_positions_by_prefix = MagicMock(side_effect=self._real_prefix_matcher(open_tickers))
        a = _make_analysis(ticker="KXBAR-26JUN01", side="yes", yes_price=50.0, edge=0.10)
        reason = ex._validate(a)
        assert reason is None or "per-prefix cap" not in (reason or ""), (
            f"distinct Kalshi series must stay independent; got: {reason}"
        )
