from __future__ import annotations

import copy
from dataclasses import dataclass, field
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
    price_source: str = "polymarket_us_rest"
    price_method: str = "binary_ask"
    price_retrieved_at: str | None = None
    raw_payload_hash: str | None = None
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
        series_ticker=Venue.POLYMARKET_US.value,
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
