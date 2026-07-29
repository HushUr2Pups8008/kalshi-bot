from datetime import UTC, datetime
from decimal import Decimal

import pytest

from analysis import SignalAnalysis
from kalshi import KalshiMarket
from tasks.kalshi_execution_liquidity import fetch_kalshi_execution_liquidity
from trading.orderbook import BinaryMarketBook, BookLevel
from trading.venue import Venue


class FakeOrderbookReader:
    def __init__(self, book: BinaryMarketBook) -> None:
        self.book = book
        self.calls: list[tuple[str, int]] = []

    def get_market_orderbook(self, ticker: str, *, depth: int = 100) -> BinaryMarketBook:
        self.calls.append((ticker, depth))
        return self.book


def _analysis() -> SignalAnalysis:
    market = KalshiMarket(
        ticker="KXDEPTH-1",
        title="Will executable depth be used?",
        yes_bid=49,
        yes_ask=51,
        yes_price=50,
        volume=100,
        open_interest=50,
        close_time="2026-08-01T00:00:00Z",
        status="active",
        yes_bid_cents=49,
        yes_ask_cents=51,
        no_bid_cents=49,
        no_ask_cents=51,
        price_available=True,
    )
    return SignalAnalysis(
        news_item=None,
        market=market,
        estimated_probability=0.72,
        executed_price_cents=5,
        edge=0.67,
        side="yes",
        kelly_fraction=0.0,
        kelly_dollars=0.0,
        capped_dollars=0.0,
        reasoning="test",
        confidence=0.9,
        match_score=0.8,
    )


@pytest.mark.asyncio
async def test_fetches_side_aware_kalshi_orderbook_liquidity_without_blocking_loop() -> None:
    book = BinaryMarketBook(
        venue=Venue.KALSHI,
        venue_market_id="KXDEPTH-1",
        yes_bids=(),
        no_bids=(BookLevel(Decimal("0.98"), Decimal("70")),),
        as_of=datetime(2026, 7, 29, 15, 46, tzinfo=UTC),
        raw_payload_hash="c" * 64,
    )
    reader = FakeOrderbookReader(book)

    liquidity = await fetch_kalshi_execution_liquidity(reader, _analysis())

    assert reader.calls == [("KXDEPTH-1", 100)]
    assert liquidity.side == "yes"
    assert liquidity.limit_price == Decimal("0.05")
    assert liquidity.executable_quantity == Decimal("70")
    assert liquidity.executable_notional == Decimal("1.40")


@pytest.mark.asyncio
async def test_rejects_missing_executable_limit_price_before_orderbook_fetch() -> None:
    book = BinaryMarketBook(
        venue=Venue.KALSHI,
        venue_market_id="KXDEPTH-1",
        yes_bids=(),
        no_bids=(BookLevel(Decimal("0.98"), Decimal("70")),),
        as_of=datetime(2026, 7, 29, 15, 46, tzinfo=UTC),
        raw_payload_hash="c" * 64,
    )
    reader = FakeOrderbookReader(book)
    analysis = _analysis()
    analysis.executed_price_cents = None

    with pytest.raises(ValueError, match="executed price"):
        await fetch_kalshi_execution_liquidity(reader, analysis)

    assert reader.calls == []


@pytest.mark.asyncio
async def test_rejects_invalid_side_before_orderbook_fetch() -> None:
    book = BinaryMarketBook(
        venue=Venue.KALSHI,
        venue_market_id="KXDEPTH-1",
        yes_bids=(),
        no_bids=(BookLevel(Decimal("0.98"), Decimal("70")),),
        as_of=datetime(2026, 7, 29, 15, 46, tzinfo=UTC),
        raw_payload_hash="c" * 64,
    )
    reader = FakeOrderbookReader(book)
    analysis = _analysis()
    analysis.side = "maybe"

    with pytest.raises(ValueError, match="side"):
        await fetch_kalshi_execution_liquidity(reader, analysis)

    assert reader.calls == []
