"""Focused contracts for pre-queue book provenance collection."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from analysis import SignalAnalysis
from kalshi import KalshiMarket
from tasks.prequeue_book_provenance import fetch_prequeue_book_provenance
from trading.orderbook import BinaryMarketBook, BookLevel
from trading.venue import Venue


class _KalshiReader:
    def __init__(self, book: BinaryMarketBook | BaseException) -> None:
        self.book = book
        self.calls: list[tuple[str, int]] = []

    def get_market_orderbook(self, ticker: str, *, depth: int = 100) -> BinaryMarketBook:
        self.calls.append((ticker, depth))
        if isinstance(self.book, BaseException):
            raise self.book
        return self.book


class _PolymarketReader:
    def __init__(
        self,
        book: BinaryMarketBook | BaseException,
        *,
        payload: dict[str, object] | BaseException | None = None,
    ) -> None:
        self.book = book
        self.payload = payload
        self.calls: list[str] = []
        self.payload_calls: list[str] = []

    def get_market_payload(self, market_id: str) -> dict[str, object]:
        self.payload_calls.append(market_id)
        if isinstance(self.payload, BaseException):
            raise self.payload
        if self.payload is None:
            raise AssertionError("numeric market lookup requires a payload binding")
        return self.payload

    def get_market_book(self, market_id: str) -> BinaryMarketBook:
        self.calls.append(market_id)
        if isinstance(self.book, BaseException):
            raise self.book
        return self.book


class _SlugOnlyPolymarketReader(_PolymarketReader):
    def __init__(
        self,
        book: BinaryMarketBook | BaseException,
        *,
        payload: dict[str, object],
        expected_slug: str,
    ) -> None:
        super().__init__(book, payload=payload)
        self.expected_slug = expected_slug

    def get_market_book(self, market_id: str) -> BinaryMarketBook:
        if market_id != self.expected_slug:
            raise AssertionError("book lookup must use the validated canonical slug")
        return super().get_market_book(market_id)


def _book(*, venue: Venue, market_id: str, payload_hash: str = "b" * 64) -> BinaryMarketBook:
    return BinaryMarketBook(
        venue=venue,
        venue_market_id=market_id,
        yes_bids=(BookLevel(Decimal("0.49"), Decimal("2")),),
        no_bids=(BookLevel(Decimal("0.48"), Decimal("3")),),
        as_of=datetime(2026, 8, 1, 12, tzinfo=UTC),
        raw_payload_hash=payload_hash,
    )


def _kalshi_analysis(ticker: str = "KXTEST-26AUG01-T50") -> SignalAnalysis:
    market = KalshiMarket(
        ticker=ticker,
        title="Will the test settle above 50?",
        yes_bid=49,
        yes_ask=51,
        yes_price=50,
        volume=100,
        open_interest=10,
        close_time="2026-08-02T00:00:00Z",
        status="active",
        yes_bid_cents=49,
        yes_ask_cents=51,
        no_bid_cents=48,
        no_ask_cents=52,
        price_available=True,
    )
    return SignalAnalysis(
        news_item=None,
        market=market,
        estimated_probability=0.70,
        executed_price_cents=51,
        edge=0.19,
        side="yes",
    )


def _polymarket_analysis(
    *,
    ticker: str = "will-test-pass",
    venue_market_id: str | None = "123456",
) -> SignalAnalysis:
    market = SimpleNamespace(
        venue=Venue.POLYMARKET_US,
        ticker=ticker,
        venue_market_id=venue_market_id,
    )
    return SignalAnalysis(
        news_item=None,
        market=market,
        estimated_probability=0.70,
        executed_price_cents=51,
        edge=0.19,
        side="yes",
    )


@pytest.mark.asyncio
async def test_kalshi_book_provenance_uses_ticker_and_validates_returned_identity():
    analysis = _kalshi_analysis()
    reader = _KalshiReader(
        _book(venue=Venue.KALSHI, market_id=analysis.market.ticker)
    )

    result = await fetch_prequeue_book_provenance(
        analysis,
        kalshi_reader=reader,
    )

    assert reader.calls == [(analysis.market.ticker, 100)]
    assert result.status == "available"
    assert result.venue == Venue.KALSHI.value
    assert result.requested_market_id == analysis.market.ticker
    assert result.native_market_id == analysis.market.ticker
    assert result.book_market_id == analysis.market.ticker
    assert result.book_observed_at == datetime(2026, 8, 1, 12, tzinfo=UTC)
    assert result.book_payload_hash == "b" * 64
    assert result.reason is None


@pytest.mark.asyncio
async def test_kalshi_book_provenance_returns_unavailable_on_identity_mismatch():
    analysis = _kalshi_analysis()
    reader = _KalshiReader(_book(venue=Venue.KALSHI, market_id="KXOTHER-26AUG01-T50"))

    result = await fetch_prequeue_book_provenance(
        analysis,
        kalshi_reader=reader,
    )

    assert result.status == "unavailable"
    assert result.reason == "kalshi_book_identity_mismatch"
    assert result.book_observed_at is None
    assert result.book_payload_hash is None


@pytest.mark.asyncio
async def test_polymarket_book_provenance_rejects_mismatched_slug_for_numeric_native_id():
    analysis = _polymarket_analysis()
    reader = _PolymarketReader(
        _book(venue=Venue.POLYMARKET_US, market_id="other-market"),
        payload={"id": "123456", "slug": analysis.market.ticker},
    )

    result = await fetch_prequeue_book_provenance(
        analysis,
        polymarket_reader=reader,
    )

    assert reader.payload_calls == ["123456"]
    assert reader.calls == [analysis.market.ticker]
    assert result.status == "unavailable"
    assert result.reason == "polymarket_book_identity_mismatch"
    assert result.requested_market_id == "123456"
    assert result.native_market_id == "123456"
    assert result.book_market_id is None
    assert result.book_payload_hash is None


@pytest.mark.asyncio
async def test_polymarket_book_provenance_binds_numeric_native_id_to_canonical_slug():
    analysis = _polymarket_analysis()
    reader = _SlugOnlyPolymarketReader(
        _book(venue=Venue.POLYMARKET_US, market_id=analysis.market.ticker),
        payload={"id": "123456", "slug": analysis.market.ticker},
        expected_slug=analysis.market.ticker,
    )

    result = await fetch_prequeue_book_provenance(
        analysis,
        polymarket_reader=reader,
    )

    assert reader.payload_calls == ["123456"]
    assert reader.calls == [analysis.market.ticker]
    assert result.status == "available"
    assert result.requested_market_id == "123456"
    assert result.native_market_id == "123456"
    assert result.book_market_id == "will-test-pass"
    assert result.book_payload_hash == "b" * 64


@pytest.mark.asyncio
async def test_polymarket_book_provenance_falls_back_to_slug_when_native_id_missing():
    analysis = _polymarket_analysis(venue_market_id=None)
    reader = _PolymarketReader(
        _book(venue=Venue.POLYMARKET_US, market_id=analysis.market.ticker)
    )

    result = await fetch_prequeue_book_provenance(
        analysis,
        polymarket_reader=reader,
    )

    assert reader.calls == ["will-test-pass"]
    assert result.status == "available"
    assert result.requested_market_id == "will-test-pass"
    assert result.native_market_id is None
    assert result.book_market_id == "will-test-pass"


@pytest.mark.asyncio
async def test_book_provenance_turns_reader_error_into_explicit_unavailable_result():
    analysis = _kalshi_analysis()
    reader = _KalshiReader(RuntimeError("reader offline"))

    result = await fetch_prequeue_book_provenance(
        analysis,
        kalshi_reader=reader,
    )

    assert result.status == "unavailable"
    assert result.reason == "reader_error:RuntimeError"
    assert result.book_observed_at is None
    assert result.book_payload_hash is None


@pytest.mark.asyncio
async def test_book_provenance_reraises_cancellation():
    analysis = _kalshi_analysis()
    reader = _KalshiReader(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await fetch_prequeue_book_provenance(
            analysis,
            kalshi_reader=reader,
        )
