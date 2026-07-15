from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from polymarket.domain_key import pm_domain_key
from trading.venue import Venue


@dataclass(frozen=True)
class PolymarketMarket:
    venue: Venue
    market_id: str
    title: str
    status: str
    yes_ask_cents: int | None
    no_ask_cents: int | None
    volume_dollars: float
    open_interest_dollars: float
    close_time: str
    venue_market_id: str | None = None
    is_binary: bool = True
    question: str = ""
    subtitle: str = ""
    category: str = ""
    resolution_source: str = ""
    description: str = ""
    event_title: str = ""
    event_slug: str = ""
    series_title: str = ""
    series_slug: str = ""
    tags: tuple[str, ...] = ()
    public_comments: tuple[str, ...] = ()
    price_source: str = ""
    price_method: str = ""
    yes_bid_cents: int | None = None
    no_bid_cents: int | None = None
    yes_bid_size: Decimal | None = None
    no_bid_size: Decimal | None = None
    yes_token_id: str | None = None
    no_token_id: str | None = None
    fee_coefficient: Decimal | None = None
    fee_effective_at: datetime | None = None
    quantity_step: Decimal | None = None
    price_tick: Decimal | None = None
    fill_role: str | None = None
    source_payload_hash: str = ""
    snapshot_at: datetime | None = None
    yes_bid_levels: tuple[tuple[Decimal, Decimal], ...] = ()
    no_bid_levels: tuple[tuple[Decimal, Decimal], ...] = ()
    book_as_of: datetime | None = None
    book_payload_hash: str | None = None
    book_error: str | None = None
    report_venue: str | None = None
    report_venue_market_id: str | None = None

    @property
    def tradeable_id(self) -> str:
        return self.market_id

    @property
    def ticker(self) -> str:
        return self.market_id

    @property
    def series_ticker(self) -> str:
        # PROFIT-VENUE-PARITY V03: per-family identity instead of the venue
        # constant. Previously every PM market returned 'polymarket_us',
        # collapsing all PM matcher/keyword/calibration feedback into one bucket
        # (and disarming the defining-token guard). pm_domain_key derives a
        # stable 'polymarket_us:<family-stem>' from the slug (preserving the
        # leading venue segment for coarse readers); a slug with no ISO date
        # falls back to the bare venue value, so this is never empty / never
        # raises for a PM market_id.
        return pm_domain_key(self.market_id)

    @property
    def yes_price(self) -> int | None:
        return self.yes_ask_cents

    @property
    def no_price(self) -> int | None:
        return self.no_ask_cents

    @property
    def yes_prob(self) -> float:
        if self.yes_ask_cents is None:
            return 0.5
        return self.yes_ask_cents / 100.0

    @property
    def open_interest(self) -> float:
        return self.open_interest_dollars

    @property
    def price_available(self) -> bool:
        return self.yes_ask_cents is not None and self.no_ask_cents is not None

    def is_tradeable(self) -> bool:
        return (
            self.status in {"open", "active"}
            and self.is_binary
            and self.yes_ask_cents is not None
            and self.no_ask_cents is not None
            and 1 <= self.yes_ask_cents <= 99
            and 1 <= self.no_ask_cents <= 99
        )
