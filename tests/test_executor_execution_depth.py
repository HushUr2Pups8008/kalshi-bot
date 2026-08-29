from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config as config_module
from kalshi import KalshiMarket
from trading.executor import TradeExecutor
from trading.execution_terms import FinalExecutionTerms
from trading.orderbook import BinaryMarketBook, BookLevel
from trading.venue import Venue


def _book(
    ticker: str,
    *,
    yes_bids: tuple[BookLevel, ...] = (),
    no_bids: tuple[BookLevel, ...] = (),
) -> BinaryMarketBook:
    return BinaryMarketBook(
        venue=Venue.KALSHI,
        venue_market_id=ticker,
        yes_bids=yes_bids,
        no_bids=no_bids,
        as_of=datetime(2026, 7, 29, 16, 30, tzinfo=UTC),
        raw_payload_hash="e" * 64,
    )


def _analysis(*, side: str = "yes", venue: str = "kalshi") -> SimpleNamespace:
    ticker = "KXDEPTH-EXEC" if venue == "kalshi" else "pm-depth-exec"
    market = SimpleNamespace(
        ticker=ticker,
        venue=venue,
        yes_price=50.0,
        status="active",
        price_available=True,
    )
    return SimpleNamespace(
        market=market,
        news_item=SimpleNamespace(headline="Depth guard test", source="test"),
        side=side,
        edge=0.10,
        estimated_probability=0.60,
        executed_price_cents=50,
        capped_dollars=10.0,
        confidence=0.80,
        kelly_fraction=0.50,
        kelly_dollars=10.0,
        llm_direction=None,
        llm_magnitude=None,
        llm_confidence=None,
        signal_meta={},
    )


def _executor(monkeypatch: pytest.MonkeyPatch, *, paper_mode: bool) -> tuple[TradeExecutor, MagicMock, MagicMock]:
    monkeypatch.setattr(config_module.cfg, "is_paper_trading", paper_mode)
    monkeypatch.setattr(config_module.cfg, "paper_ticker_cooldown", 0)
    monkeypatch.setattr(config_module.cfg, "live_ticker_cooldown", 0)
    monkeypatch.setattr(config_module.cfg, "max_ticker_exposure_pct", 1.0)
    monkeypatch.setattr(config_module.cfg, "bankroll", 500.0)
    rest = MagicMock()
    rest.get_balance.return_value = 500.0
    paper = MagicMock()
    paper.get_notional_bankroll.return_value = 500.0
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.is_concentration_ok.return_value = True
    paper.portfolio.exposure.return_value = 0.0
    paper.record_trade.return_value = "paper-depth-1"
    return TradeExecutor(rest, paper), rest, paper


@pytest.mark.asyncio
async def test_paper_execution_requires_full_yes_depth_for_rounded_contract_count(monkeypatch):
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="yes")
    rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        no_bids=(BookLevel(Decimal("0.50"), Decimal("20")),),
    )

    result = await executor.execute(analysis)

    assert result == "paper-depth-1"
    rest.get_market_orderbook.assert_called_once_with(analysis.market.ticker, depth=100)
    paper.record_trade.assert_called_once_with(
        analysis,
        execution_terms=FinalExecutionTerms(
            price_cents=50,
            contracts=20,
            cost_dollars=10.0,
        ),
    )
    assert analysis.signal_meta["final_execution_depth"] == {
        "source": "kalshi_orderbook",
        "status": "sufficient",
        "side": "yes",
        "limit_price": 0.5,
        "requested_contracts": 20,
        "available_contracts": 20.0,
        "available_notional": 10.0,
        "as_of": "2026-07-29T16:30:00+00:00",
        "raw_payload_hash": "e" * 64,
    }


@pytest.mark.asyncio
async def test_paper_execution_fails_closed_one_contract_short(monkeypatch):
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="yes")
    rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        no_bids=(BookLevel(Decimal("0.50"), Decimal("19")),),
    )

    result = await executor.execute(analysis)

    assert result is None
    paper.record_trade.assert_not_called()
    assert analysis.signal_meta["final_execution_depth"] == {
        "source": "kalshi_orderbook",
        "status": "insufficient",
        "side": "yes",
        "limit_price": 0.5,
        "requested_contracts": 20,
        "available_contracts": 19.0,
        "available_notional": 9.5,
        "as_of": "2026-07-29T16:30:00+00:00",
        "raw_payload_hash": "e" * 64,
    }


@pytest.mark.asyncio
async def test_paper_execution_uses_yes_bids_for_final_no_depth(monkeypatch):
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="no")
    rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        yes_bids=(BookLevel(Decimal("0.50"), Decimal("20")),),
    )

    result = await executor.execute(analysis)

    assert result == "paper-depth-1"
    assert paper.record_trade.call_args.kwargs["execution_terms"] == FinalExecutionTerms(
        price_cents=50,
        contracts=20,
        cost_dollars=10.0,
    )
    assert analysis.signal_meta["final_execution_depth"]["side"] == "no"
    assert analysis.signal_meta["final_execution_depth"]["available_contracts"] == 20.0


@pytest.mark.asyncio
async def test_invalid_candidate_skips_before_final_orderbook_fetch(monkeypatch):
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis()
    analysis.edge = 0.0

    result = await executor.execute(analysis)

    assert result is None
    rest.get_market_orderbook.assert_not_called()
    paper.record_trade.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("paper_mode", [True, False])
async def test_final_terms_reject_cost_above_cap_before_depth_fetch(monkeypatch, paper_mode):
    executor, rest, paper = _executor(monkeypatch, paper_mode=paper_mode)
    analysis = _analysis()
    analysis.capped_dollars = 0.10

    result = await executor.execute(analysis)

    assert result is None
    rest.get_market_orderbook.assert_not_called()
    paper.record_trade.assert_not_called()
    rest.place_limit_order.assert_not_called()
    assert analysis.signal_meta == {
        "final_execution_depth": {
            "source": "execution_terms",
            "status": "invalid",
            "reason": "final execution cost exceeds capped dollars",
        }
    }


@pytest.mark.asyncio
async def test_wrong_final_orderbook_identity_fails_closed(monkeypatch):
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis()
    rest.get_market_orderbook.return_value = _book(
        "KXOTHER-DEPTH",
        no_bids=(BookLevel(Decimal("0.50"), Decimal("20")),),
    )

    result = await executor.execute(analysis)

    assert result is None
    paper.record_trade.assert_not_called()
    assert analysis.signal_meta["final_execution_depth"] == {
        "source": "kalshi_orderbook",
        "status": "unavailable",
        "reason": "ValueError",
        "requested_contracts": 20,
    }


def _kalshi_market(*, ticker: str, yes_ask: int, yes_bid: int) -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        title="Will final terms use the fresh quote?",
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        yes_price=(yes_bid + yes_ask) / 2,
        volume=100,
        open_interest=100,
        close_time="2026-08-01T00:00:00Z",
        status="active",
        yes_bid_cents=yes_bid,
        yes_ask_cents=yes_ask,
        no_bid_cents=100 - yes_ask,
        no_ask_cents=100 - yes_bid,
        price_available=True,
    )


@pytest.mark.asyncio
async def test_blended_candidate_depth_uses_refreshed_side_price(monkeypatch):
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    ticker = "KXDEPTH-REFRESH"
    original = _analysis()
    original.market = SimpleNamespace(
        ticker=ticker,
        venue="kalshi",
        yes_price=50.0,
        status="active",
        price_available=True,
    )
    fresh = _kalshi_market(ticker=ticker, yes_bid=38, yes_ask=40)
    rest.get_market.return_value = fresh
    rest.get_market_orderbook.return_value = _book(
        ticker,
        no_bids=(BookLevel(Decimal("0.60"), Decimal("25")),),
    )
    candidate = SimpleNamespace(
        fast_lane_analysis=original,
        market=original.market,
        blended_probability=0.60,
        signal_meta={},
    )

    result = await executor.execute(candidate)

    assert result == "paper-depth-1"
    assert paper.record_trade.call_args.kwargs["execution_terms"] == FinalExecutionTerms(
        price_cents=40,
        contracts=25,
        cost_dollars=10.0,
    )
    routed_analysis = paper.record_trade.call_args.args[0]
    assert routed_analysis.signal_meta["final_execution_depth"]["limit_price"] == 0.4
    assert routed_analysis.signal_meta["final_execution_depth"]["requested_contracts"] == 25


@pytest.mark.asyncio
async def test_live_execution_does_not_submit_when_final_depth_is_unavailable(monkeypatch, tmp_path):
    executor, rest, _paper = _executor(monkeypatch, paper_mode=False)
    executor._live_submission_holds._path = tmp_path / "holds.json"
    analysis = _analysis(side="yes")
    rest.get_market_orderbook.side_effect = RuntimeError("orderbook unavailable")

    result = await executor.execute(analysis)

    assert result is None
    rest.place_limit_order.assert_not_called()
    assert analysis.signal_meta["final_execution_depth"] == {
        "source": "kalshi_orderbook",
        "status": "unavailable",
        "reason": "RuntimeError",
        "requested_contracts": 20,
    }


@pytest.mark.asyncio
async def test_paper_skip_does_not_crash_when_research_news_item_is_missing(
    monkeypatch,
) -> None:
    executor, _rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="yes")
    analysis.news_item = None
    analysis.capped_dollars = 0.0
    analysis.estimated_probability = 0.50
    analysis.edge = 0.0
    analysis.executed_price_cents = 50
    analysis.confidence = 0.85
    analysis.market.close_time = "2026-08-30T13:59:00Z"

    result = await executor.execute(analysis)

    assert result is None
    paper.record_trade.assert_not_called()


def test_paper_research_admission_gets_kelly_size_when_capped_dollars_zero(
    monkeypatch,
) -> None:
    executor, _rest, paper = _executor(monkeypatch, paper_mode=True)
    paper.get_notional_bankroll.return_value = 50.0
    paper.get_effective_sizing_bankroll.return_value = 50.0
    analysis = _analysis(side="yes")
    analysis.capped_dollars = 0.0
    analysis.kelly_dollars = 0.0
    analysis.estimated_probability = 0.73
    analysis.edge = 0.58
    analysis.executed_price_cents = 14
    analysis.confidence = 0.85
    analysis.market.close_time = "2026-08-30T13:59:00Z"
    analysis.signal_type = "research_decision_grade"

    executor._ensure_sized(analysis)

    assert analysis.capped_dollars > 0
    assert analysis.kelly_dollars > 0


@pytest.mark.asyncio
async def test_paper_politics_research_uses_rest_top_size_when_orderbook_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "utils.event_news_research.is_event_news_paper_cohort",
        lambda _config=None: True,
    )
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="yes")
    analysis.signal_type = "research_decision_grade"
    analysis.market.yes_ask_size = Decimal("20")
    analysis.market.yes_ask_cents = 50
    rest.get_market_orderbook.side_effect = RuntimeError("orderbook unavailable")

    result = await executor.execute(analysis)

    rest.place_limit_order.assert_not_called()
    assert analysis.signal_meta["final_execution_depth"] == {
        "source": "rest_top_size",
        "status": "fallback",
        "reason": "orderbook_unavailable",
        "requested_contracts": 20,
        "available_contracts": 20.0,
    }
    assert result in {"paper-depth-1", None}


@pytest.mark.asyncio
async def test_live_politics_research_stays_g8_fail_closed_when_orderbook_unavailable(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "utils.event_news_research.is_event_news_paper_cohort",
        lambda _config=None: True,
    )
    executor, rest, _paper = _executor(monkeypatch, paper_mode=False)
    executor._live_submission_holds._path = tmp_path / "holds.json"
    analysis = _analysis(side="yes")
    analysis.signal_type = "research_decision_grade"
    analysis.market.yes_ask_size = Decimal("20")
    rest.get_market_orderbook.side_effect = RuntimeError("orderbook unavailable")

    result = await executor.execute(analysis)

    assert result is None
    rest.place_limit_order.assert_not_called()
    assert analysis.signal_meta["final_execution_depth"]["source"] == "kalshi_orderbook"
    assert analysis.signal_meta["final_execution_depth"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_live_execution_uses_final_depth_plan_and_journals_it(monkeypatch, tmp_path):
    executor, rest, _paper = _executor(monkeypatch, paper_mode=False)
    executor._live_submission_holds._path = tmp_path / "holds.json"
    analysis = _analysis(side="yes")
    rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        no_bids=(BookLevel(Decimal("0.50"), Decimal("20")),),
    )
    rest.place_limit_order.return_value = SimpleNamespace(
        error=None,
        order_id="live-depth-1",
        status="resting",
        filled=0,
    )

    with (
        patch("trading.executor.trade_log") as trade_log_mock,
        patch("trading.executor.write_trade_log_async", new_callable=AsyncMock) as write_log,
    ):
        result = await executor.execute(analysis)

    assert result == "live-depth-1"
    rest.place_limit_order.assert_called_once_with(
        ticker=analysis.market.ticker,
        side="yes",
        count=20,
        limit_price=50,
    )
    intent_kwargs = next(
        call.kwargs
        for call in write_log.await_args_list
        if call.args[0] is trade_log_mock.log_live_submission_intent
    )
    order_kwargs = next(
        call.kwargs
        for call in write_log.await_args_list
        if call.args[0] is trade_log_mock.log_live_order
    )
    assert intent_kwargs["signal_meta"]["final_execution_depth"] == {
        "source": "kalshi_orderbook",
        "status": "sufficient",
        "side": "yes",
        "limit_price": 0.5,
        "requested_contracts": 20,
        "available_contracts": 20.0,
        "available_notional": 10.0,
        "as_of": "2026-07-29T16:30:00+00:00",
        "raw_payload_hash": "e" * 64,
    }
    assert order_kwargs["signal_meta"] == intent_kwargs["signal_meta"]


@pytest.mark.asyncio
async def test_malformed_final_depth_response_fails_closed(monkeypatch):
    executor, _rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis()

    async def malformed_liquidity(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(
        "trading.executor.fetch_kalshi_execution_liquidity",
        malformed_liquidity,
    )

    result = await executor.execute(analysis)

    assert result is None
    paper.record_trade.assert_not_called()
    assert analysis.signal_meta == {
        "final_execution_depth": {
            "source": "kalshi_orderbook",
            "status": "unavailable",
            "reason": "AttributeError",
            "requested_contracts": 20,
        }
    }


@pytest.mark.asyncio
async def test_no_execution_depth_request_for_polymarket(monkeypatch):
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(venue="polymarket_us")

    result = await executor.execute(analysis)

    assert result == "paper-depth-1"
    rest.get_market_orderbook.assert_not_called()
    paper.record_trade.assert_called_once_with(
        analysis,
        execution_terms=FinalExecutionTerms(
            price_cents=50,
            contracts=20,
            cost_dollars=10.0,
        ),
    )
