from __future__ import annotations

from dataclasses import dataclass

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
    is_binary: bool = True
    question: str = ""
    subtitle: str = ""
    category: str = ""

    @property
    def tradeable_id(self) -> str:
        return self.market_id

    @property
    def ticker(self) -> str:
        return self.market_id

    @property
    def series_ticker(self) -> str:
        return self.venue.value

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
