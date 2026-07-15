from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from analysis import SignalAnalysis
from polymarket.models import PolymarketMarket
from trading.venue import Venue


@dataclass(frozen=True)
class PolymarketExecutionMarket:
    venue: Venue
    ticker: str
    series_ticker: str
    title: str
    subtitle: str
    status: str
    yes_price: int
    no_price: int
    yes_ask_cents: int
    no_ask_cents: int
    volume_dollars: float
    open_interest_dollars: float
    close_time: str
    venue_market_id: str | None = None
    question: str = ""
    description: str = ""
    resolution_source: str = ""
    event_title: str = ""
    event_slug: str = ""
    series_title: str = ""
    series_slug: str = ""
    tags: tuple[str, ...] = ()
    public_comments: tuple[str, ...] = ()
    price_source: str = "polymarket_us_rest"
    price_method: str = "binary_ask"
    price_retrieved_at: str | None = None
    raw_payload_hash: str | None = None
    yes_bid_cents: int | None = None
    no_bid_cents: int | None = None
    yes_bid_size: Decimal | None = None
    no_bid_size: Decimal | None = None
    yes_bid_levels: tuple[tuple[Decimal, Decimal], ...] = ()
    no_bid_levels: tuple[tuple[Decimal, Decimal], ...] = ()
    book_as_of: datetime | None = None
    book_payload_hash: str | None = None
    book_error: str | None = None
    quantity_step: Decimal | None = None
    price_tick: Decimal | None = None
    fee_coefficient: Decimal | None = None
    fee_effective_at: datetime | None = None
    fill_role: str | None = None
    source_payload_hash: str = ""
    report_venue: str | None = None
    report_venue_market_id: str | None = None
    regime_weights: dict[str, float] = field(
        default_factory=lambda: {
            "fast": 1.0,
            "interpretation": 0.0,
            "structural": 0.0,
        }
    )

    @property
    def yes_prob(self) -> float:
        return self.yes_price / 100.0

    @property
    def price_available(self) -> bool:
        return 1 <= self.yes_price <= 99 and 1 <= self.no_price <= 99

    def is_tradeable(self) -> bool:
        return self.status in {"open", "active"} and self.price_available


def adapt_polymarket_analysis(
    analysis: SignalAnalysis,
    market: PolymarketMarket,
) -> SignalAnalysis:
    """Return a paper-execution analysis for a normalized Polymarket binary market."""
    if market.venue != Venue.POLYMARKET_US:
        raise ValueError(f"unsupported Polymarket venue: {market.venue!r}")
    if not market.is_binary:
        raise ValueError("Polymarket adapter supports binary markets only")
    if not market.is_tradeable():
        raise ValueError("Polymarket market is not tradeable")
    if market.yes_ask_cents is None or market.no_ask_cents is None:
        raise ValueError("Polymarket market is missing executable ask prices")

    side = str(analysis.side).strip().lower()
    if side not in {"yes", "no"}:
        raise ValueError(f"unsupported Polymarket side: {analysis.side!r}")

    adapted = copy.copy(analysis)
    adapted.market = PolymarketExecutionMarket(
        venue=Venue.POLYMARKET_US,
        ticker=market.market_id,
        venue_market_id=market.venue_market_id,
        # PROFIT-VENUE-PARITY V03: carry the market's per-family series_ticker
        # (pm_domain_key-derived) onto the execution market, NOT the venue
        # constant -- so record_trade persists a per-family prefix and
        # keyword_outcomes / calibration / replay key per-family. adapted.venue
        # below stays the venue string for routing.
        series_ticker=market.series_ticker,
        title=market.title,
        subtitle=market.subtitle,
        status=market.status,
        yes_price=market.yes_ask_cents,
        no_price=market.no_ask_cents,
        yes_ask_cents=market.yes_ask_cents,
        no_ask_cents=market.no_ask_cents,
        volume_dollars=market.volume_dollars,
        open_interest_dollars=market.open_interest_dollars,
        close_time=market.close_time,
        question=market.question,
        description=market.description,
        resolution_source=market.resolution_source,
        event_title=market.event_title,
        event_slug=market.event_slug,
        series_title=market.series_title,
        series_slug=market.series_slug,
        tags=market.tags,
        public_comments=market.public_comments,
        price_source=market.price_source or "polymarket_us_rest",
        price_method=market.price_method or "binary_ask",
        raw_payload_hash=market.source_payload_hash,
        yes_bid_cents=market.yes_bid_cents,
        no_bid_cents=market.no_bid_cents,
        yes_bid_size=market.yes_bid_size,
        no_bid_size=market.no_bid_size,
        yes_bid_levels=market.yes_bid_levels,
        no_bid_levels=market.no_bid_levels,
        book_as_of=market.book_as_of,
        book_payload_hash=market.book_payload_hash,
        book_error=market.book_error,
        quantity_step=market.quantity_step,
        price_tick=market.price_tick,
        fee_coefficient=market.fee_coefficient,
        fee_effective_at=market.fee_effective_at,
        fill_role=market.fill_role,
        source_payload_hash=market.source_payload_hash,
        report_venue=market.report_venue,
        report_venue_market_id=market.report_venue_market_id,
    )
    adapted.venue = Venue.POLYMARKET_US.value
    adapted.side = side
    adapted.executed_price_cents = (
        market.yes_ask_cents if side == "yes" else market.no_ask_cents
    )

    chosen_probability = (
        adapted.estimated_probability
        if side == "yes"
        else 1.0 - adapted.estimated_probability
    )
    adapted.edge = chosen_probability - (adapted.executed_price_cents / 100.0)
    adapted.signal_meta = _with_polymarket_meta(
        getattr(analysis, "signal_meta", None),
        market,
    )
    return adapted


def _with_polymarket_meta(
    signal_meta: Any,
    market: PolymarketMarket,
) -> dict[str, Any]:
    meta = dict(signal_meta) if isinstance(signal_meta, dict) else {}
    meta.update(
        {
            "venue": Venue.POLYMARKET_US.value,
            "polymarket_market_id": market.market_id,
            "price_source": "polymarket_us_rest",
            "price_method": "binary_ask",
        }
    )
    return meta
