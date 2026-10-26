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
    """A 60c KXAPRPOTUS book clears the politics gate. Eight contracts is the cap."""
    price = 60
    opposite = 40
    if venue == "kalshi":
        ticker = "KXAPRPOTUS-26OCT17-DEPTH"
        series = "KXAPRPOTUS"
    else:
        ticker = "pm-depth-exec"
        series = ""
    yes_ask = price if side == "yes" else opposite
    no_ask = opposite if side == "yes" else price
    market = SimpleNamespace(
        ticker=ticker,
        series_ticker=series,
        venue=venue,
        yes_price=float(yes_ask),
        status="active",
        price_available=True,
        yes_ask_cents=yes_ask,
        no_ask_cents=no_ask,
        yes_ask=yes_ask,
        no_ask=no_ask,
        last_price_cents=yes_ask,
        yes_ask_size=100.0,
        no_ask_size=100.0,
        open_interest_fp=1000.0,
        volume_24h_fp=1000.0,
    )
    return SimpleNamespace(
        market=market,
        news_item=SimpleNamespace(headline="Depth guard test", source="test"),
        side=side,
        edge=0.25,
        estimated_probability=0.90 if side == "yes" else 0.10,
        executed_price_cents=price,
        capped_dollars=4.80,
        confidence=0.80,
        kelly_fraction=0.50,
        kelly_dollars=4.80,
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
    rest.get_open_positions.return_value = []

    def _balance_request(_method, endpoint, *_args, **_kwargs):
        if str(endpoint).endswith("/portfolio/balance"):
            return {"balance": 50000, "portfolio_value": 0}
        if str(endpoint).endswith("/portfolio/positions"):
            return {"market_positions": []}
        return {}

    rest._request.side_effect = _balance_request
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
        no_bids=(BookLevel(Decimal("0.40"), Decimal("8")),),
    )

    result = await executor.execute(analysis)

    assert result == "paper-depth-1"
    rest.get_market_orderbook.assert_called_once_with(analysis.market.ticker, depth=100)
    paper.record_trade.assert_called_once_with(
        analysis,
        execution_terms=FinalExecutionTerms(
            price_cents=60,
            contracts=8,
            cost_dollars=4.8,
        ),
    )
    assert analysis.signal_meta["final_execution_depth"] == {
        "source": "kalshi_orderbook",
        "status": "sufficient",
        "side": "yes",
        "limit_price": 0.6,
        "requested_contracts": 8,
        "available_contracts": 8.0,
        "available_notional": 4.8,
        "as_of": "2026-07-29T16:30:00+00:00",
        "raw_payload_hash": "e" * 64,
    }


@pytest.mark.asyncio
async def test_paper_execution_sizes_down_to_available_contracts(monkeypatch):
    """Seven lots at 60c still clear 0.04 fee-net, so the order is 7, not a skip."""
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="yes")
    rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        no_bids=(BookLevel(Decimal("0.40"), Decimal("7")),),
    )

    result = await executor.execute(analysis)

    assert result == "paper-depth-1"
    paper.record_trade.assert_called_once_with(
        analysis,
        execution_terms=FinalExecutionTerms(
            price_cents=60,
            contracts=7,
            cost_dollars=4.2,
        ),
    )
    depth = analysis.signal_meta["final_execution_depth"]
    assert depth["status"] == "sized_down"
    assert depth["requested_contracts"] == 8
    assert depth["available_contracts"] == 7.0
    assert depth["sized_contracts"] == 7
    assert depth["cost_dollars"] == 4.2
    assert Decimal(depth["fee_net"]) >= Decimal("0.04")


@pytest.mark.asyncio
async def test_paper_no_size_down_uses_the_no_probability(monkeypatch):
    """NO probability is 1 minus the YES probability. A 0.10 YES read is a 0.90 NO."""
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="no")
    rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        yes_bids=(BookLevel(Decimal("0.40"), Decimal("7")),),
    )

    result = await executor.execute(analysis)

    assert result == "paper-depth-1"
    assert paper.record_trade.call_args.kwargs["execution_terms"] == FinalExecutionTerms(
        price_cents=60,
        contracts=7,
        cost_dollars=4.2,
    )
    assert Decimal(analysis.signal_meta["final_execution_depth"]["fee_net"]) >= Decimal("0.04")


@pytest.mark.asyncio
async def test_live_execution_sizes_down_the_posted_count(monkeypatch, tmp_path):
    """The live order count is the book, still inside the 8-contract cap."""
    executor, rest, _paper = _executor(monkeypatch, paper_mode=False)
    executor._live_submission_holds._path = tmp_path / "holds.json"
    analysis = _analysis(side="yes")
    rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        no_bids=(BookLevel(Decimal("0.40"), Decimal("7.9")),),
    )
    monkeypatch.setattr(
        "utils.event_news_grok_veto.event_news_grok_veto_allows_live_post",
        lambda *_args, **_kwargs: True,
    )
    rest.place_limit_order.return_value = SimpleNamespace(
        error=None,
        order_id="live-depth-7",
        status="resting",
        filled=0,
    )

    result = await executor.execute(analysis)

    assert result == "live-depth-7"
    assert rest.place_limit_order.call_args.kwargs["count"] == 7
    assert rest.place_limit_order.call_args.kwargs["limit_price"] == 60
    assert analysis.signal_meta["final_execution_depth"]["sized_contracts"] == 7
    assert Decimal(analysis.signal_meta["final_execution_depth"]["fee_net"]) >= Decimal("0.04")


@pytest.mark.asyncio
async def test_depth_below_one_contract_still_skips(monkeypatch):
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="yes")
    rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        no_bids=(BookLevel(Decimal("0.40"), Decimal("0.50")),),
    )

    result = await executor.execute(analysis)

    assert result is None
    paper.record_trade.assert_not_called()
    assert analysis.signal_meta["final_execution_depth"]["status"] == "insufficient"
    assert analysis.signal_meta["final_execution_depth"]["available_contracts"] == 0.5
    assert "sized_contracts" not in analysis.signal_meta["final_execution_depth"]


@pytest.mark.asyncio
async def test_depth_size_down_skips_below_the_minimum_bet(monkeypatch):
    """7 * 60c is $4.20. A $5 minimum bet leaves that short book untraded."""
    monkeypatch.setattr(config_module.cfg, "min_bet_dollars", 5.0)
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="yes")
    rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        no_bids=(BookLevel(Decimal("0.40"), Decimal("7")),),
    )

    result = await executor.execute(analysis)

    assert result is None
    paper.record_trade.assert_not_called()
    depth = analysis.signal_meta["final_execution_depth"]
    assert depth["status"] == "insufficient"
    assert depth["reason"] == "below_min_bet"
    assert depth["sized_contracts"] == 7


@pytest.mark.asyncio
async def test_depth_size_down_skips_when_fee_net_is_below_four_cents(monkeypatch):
    executor, _rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="yes")
    analysis.estimated_probability = 0.62
    executor._rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        no_bids=(BookLevel(Decimal("0.40"), Decimal("7")),),
    )

    plan, reason = await executor._final_execution_plan(analysis)

    assert plan is None
    assert reason == "G8_execution_depth_below_fee_net"
    paper.record_trade.assert_not_called()
    depth = analysis.signal_meta["final_execution_depth"]
    assert depth["reason"] == "below_fee_net"
    assert Decimal(depth["fee_net"]) < Decimal("0.04")


@pytest.mark.asyncio
async def test_depth_size_down_fails_closed_when_the_fee_is_unreadable(monkeypatch):
    executor, _rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="yes")
    analysis.estimated_probability = None
    executor._rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        no_bids=(BookLevel(Decimal("0.40"), Decimal("7")),),
    )

    plan, reason = await executor._final_execution_plan(analysis)

    assert plan is None
    assert reason == "G8_execution_depth_fee_unreadable"
    paper.record_trade.assert_not_called()
    assert analysis.signal_meta["final_execution_depth"]["reason"] == "fee_unreadable"


@pytest.mark.asyncio
async def test_paper_execution_uses_yes_bids_for_final_no_depth(monkeypatch):
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="no")
    rest.get_market_orderbook.return_value = _book(
        analysis.market.ticker,
        yes_bids=(BookLevel(Decimal("0.40"), Decimal("8")),),
    )

    result = await executor.execute(analysis)

    assert result == "paper-depth-1"
    assert paper.record_trade.call_args.kwargs["execution_terms"] == FinalExecutionTerms(
        price_cents=60,
        contracts=8,
        cost_dollars=4.8,
    )
    assert analysis.signal_meta["final_execution_depth"]["side"] == "no"
    assert analysis.signal_meta["final_execution_depth"]["available_contracts"] == 8.0


@pytest.mark.asyncio
async def test_invalid_candidate_skips_before_final_orderbook_fetch(monkeypatch):
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis()
    analysis.edge = 0.0
    analysis.estimated_probability = 0.60

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
        no_bids=(BookLevel(Decimal("0.40"), Decimal("8")),),
    )

    result = await executor.execute(analysis)

    assert result is None
    paper.record_trade.assert_not_called()
    assert analysis.signal_meta["final_execution_depth"] == {
        "source": "kalshi_orderbook",
        "status": "unavailable",
        "reason": "ValueError",
        "requested_contracts": 8,
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
    ticker = "KXAPRPOTUS-26OCT17-BLEND"
    original = _analysis()
    original.market = SimpleNamespace(
        ticker=ticker,
        series_ticker="KXAPRPOTUS",
        venue="kalshi",
        yes_price=60.0,
        status="active",
        price_available=True,
        yes_ask_cents=60,
        no_ask_cents=40,
    )
    fresh = _kalshi_market(ticker=ticker, yes_bid=58, yes_ask=60)
    rest.get_market.return_value = fresh
    rest.get_market_orderbook.return_value = _book(
        ticker,
        no_bids=(BookLevel(Decimal("0.40"), Decimal("8")),),
    )
    candidate = SimpleNamespace(
        fast_lane_analysis=original,
        market=original.market,
        blended_probability=0.90,
        signal_meta={},
    )

    result = await executor.execute(candidate)

    assert result == "paper-depth-1"
    assert paper.record_trade.call_args.kwargs["execution_terms"] == FinalExecutionTerms(
        price_cents=60,
        contracts=8,
        cost_dollars=4.8,
    )
    routed_analysis = paper.record_trade.call_args.args[0]
    assert routed_analysis.signal_meta["final_execution_depth"]["limit_price"] == 0.6
    assert routed_analysis.signal_meta["final_execution_depth"]["requested_contracts"] == 8


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
        "requested_contracts": 8,
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
async def test_paper_politics_research_fails_closed_when_orderbook_unavailable(
    monkeypatch,
) -> None:
    """Paper uses the same depth fail-closed path as live. No REST size fallback."""
    monkeypatch.setattr(
        "utils.event_news_research.is_event_news_paper_cohort",
        lambda _config=None: True,
    )
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(side="yes")
    analysis.signal_type = "research_decision_grade"
    analysis.market.ticker = "KXAPRPOTUS-26OCT17-DEPTH"
    analysis.market.series_ticker = "KXAPRPOTUS"
    analysis.market.yes_ask_size = Decimal("20")
    analysis.market.yes_ask_cents = 60
    analysis.market.last_price_cents = 60
    analysis.executed_price_cents = 60
    analysis.estimated_probability = 0.90
    rest.get_market_orderbook.side_effect = RuntimeError("orderbook unavailable")

    result = await executor.execute(analysis)

    assert result is None
    rest.place_limit_order.assert_not_called()
    assert analysis.signal_meta["final_execution_depth"]["source"] == "kalshi_orderbook"
    assert analysis.signal_meta["final_execution_depth"]["status"] == "unavailable"
    del paper


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
    analysis.market.ticker = "KXAPRPOTUS-26OCT17-DEPTH"
    analysis.market.series_ticker = "KXAPRPOTUS"
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
        no_bids=(BookLevel(Decimal("0.40"), Decimal("8")),),
    )
    monkeypatch.setattr(
        "utils.event_news_grok_veto.event_news_grok_veto_allows_live_post",
        lambda *_args, **_kwargs: True,
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
    rest.place_limit_order.assert_called_once()
    live_kwargs = rest.place_limit_order.call_args.kwargs
    assert live_kwargs["ticker"] == analysis.market.ticker
    assert live_kwargs["side"] == "yes"
    assert live_kwargs["count"] == 8
    assert live_kwargs["limit_price"] == 60
    assert live_kwargs["client_order_id"]
    intent_kwargs = next(
        call.kwargs
        for call in write_log.await_args_list
        if call.args[0] is trade_log_mock.log_live_submission_intent
    )
    assert "client_order_id" not in intent_kwargs
    order_kwargs = next(
        call.kwargs
        for call in write_log.await_args_list
        if call.args[0] is trade_log_mock.log_live_order
    )
    assert intent_kwargs["signal_meta"]["final_execution_depth"] == {
        "source": "kalshi_orderbook",
        "status": "sufficient",
        "side": "yes",
        "limit_price": 0.6,
        "requested_contracts": 8,
        "available_contracts": 8.0,
        "available_notional": 4.8,
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
            "requested_contracts": 8,
        }
    }


@pytest.mark.asyncio
async def test_no_execution_depth_request_for_polymarket(monkeypatch):
    executor, rest, paper = _executor(monkeypatch, paper_mode=True)
    analysis = _analysis(venue="polymarket_us")

    result = await executor.execute(analysis)

    assert result is None
    rest.get_market_orderbook.assert_not_called()
    paper.record_trade.assert_not_called()


def _same_book_executors(monkeypatch):
    """One paper executor and one live executor on the same bankroll."""
    monkeypatch.setenv("EVENT_NEWS_YES_FLOOR_CENTS", "55")
    monkeypatch.setenv("EVENT_NEWS_EXCLUDE_EXACT_35", "true")
    for name in (
        "EVENT_NEWS_UNIFIED_EDGE",
        "EVENT_NEWS_EQUITY_LOSS_HALT",
        "EVENT_NEWS_LIVE_TRADABLE_RESEARCH",
        "EVENT_NEWS_SESSION_HALT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config_module.cfg, "min_edge", 0.04)
    paper_ex, paper_rest, paper_book = _executor(monkeypatch, paper_mode=True)
    live_ex, live_rest, live_book = _executor(monkeypatch, paper_mode=False)
    for book in (paper_book, live_book):
        book._starting_bankroll = 50.0
        book.get_effective_sizing_bankroll.return_value = 10.0
    return paper_ex, paper_rest, paper_book, live_ex, live_rest, live_book


def test_same_book_paper_and_live_share_edge_size_and_fee(monkeypatch) -> None:
    """A 60c book is the same decision, size, and fee. The POST is the exception."""
    from utils.event_news_research import event_news_fee_net_edge

    paper_ex, _, _, live_ex, _, _ = _same_book_executors(monkeypatch)
    paper_reason = paper_ex._validate(_analysis())
    live_reason = live_ex._validate(_analysis())
    assert paper_reason is None
    assert live_reason is None

    paper_sized = _analysis()
    live_sized = _analysis()
    paper_sized.capped_dollars = 0
    live_sized.capped_dollars = 0
    paper_sized.market.close_time = "2026-11-01T00:00:00Z"
    live_sized.market.close_time = "2026-11-01T00:00:00Z"
    paper_ex._ensure_sized(paper_sized)
    live_ex._ensure_sized(live_sized)
    assert paper_sized.capped_dollars == live_sized.capped_dollars
    assert paper_sized.capped_dollars > 0

    low_paper = _analysis()
    low_live = _analysis()
    low_paper.edge = 0.03
    low_live.edge = 0.03
    low_paper.estimated_probability = 0.53
    low_live.estimated_probability = 0.53
    paper_skip = paper_ex._validate(low_paper)
    live_skip = live_ex._validate(low_live)
    assert paper_skip == live_skip == "below_fee_net_min_edge"

    fee_paper = event_news_fee_net_edge(0.90, 60, 8)
    fee_live = event_news_fee_net_edge(0.90, 60, 8)
    assert fee_paper is not None and fee_live is not None
    assert fee_paper[1] == fee_live[1]


@pytest.mark.asyncio
async def test_same_book_paper_records_and_live_posts_the_same_size(
    monkeypatch, tmp_path
) -> None:
    paper_ex, paper_rest, paper_book, live_ex, live_rest, _live_book = _same_book_executors(
        monkeypatch
    )
    live_ex._live_submission_holds._path = tmp_path / "holds.json"
    monkeypatch.setattr(
        "utils.event_news_grok_veto.event_news_grok_veto_allows_live_post",
        lambda *_args, **_kwargs: True,
    )
    book = _book(
        "KXAPRPOTUS-26OCT17-DEPTH",
        no_bids=(BookLevel(Decimal("0.40"), Decimal("8")),),
    )
    paper_rest.get_market_orderbook.return_value = book
    live_rest.get_market_orderbook.return_value = book
    live_rest.place_limit_order.return_value = SimpleNamespace(
        error=None,
        order_id="live-depth-1",
        status="resting",
        filled=0,
    )

    with (
        patch("trading.executor.trade_log"),
        patch("trading.executor.write_trade_log_async", new_callable=AsyncMock),
    ):
        paper_id = await paper_ex.execute(_analysis())
        live_id = await live_ex.execute(_analysis())

    assert paper_id == "paper-depth-1"
    assert live_id == "live-depth-1"
    paper_rest.place_limit_order.assert_not_called()
    live_rest.place_limit_order.assert_called_once()
    paper_terms = paper_book.record_trade.call_args.kwargs["execution_terms"]
    posted = live_rest.place_limit_order.call_args.kwargs
    assert paper_terms.contracts == posted["count"]
    assert paper_terms.price_cents == posted["limit_price"]
    assert paper_terms.contracts == 8
    assert paper_terms.price_cents == 60

    blocked_ex, blocked_rest, _blocked_book = _executor(monkeypatch, paper_mode=False)
    blocked_ex._live_submission_holds._path = tmp_path / "holds-paper.json"
    blocked_rest.get_market_orderbook.return_value = book
    blocked_rest.place_limit_order.return_value = SimpleNamespace(
        error=None,
        order_id="should-not-post",
        status="resting",
        filled=0,
    )
    monkeypatch.setattr(
        "utils.series_universe.event_news_live_post_block_reason",
        lambda *_args, **_kwargs: "paper_series_no_post",
    )
    with (
        patch("trading.executor.trade_log"),
        patch("trading.executor.write_trade_log_async", new_callable=AsyncMock),
    ):
        blocked = await blocked_ex.execute(_analysis())
    assert blocked is None
    blocked_rest.place_limit_order.assert_not_called()
