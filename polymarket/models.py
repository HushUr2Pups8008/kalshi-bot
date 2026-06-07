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

    def is_tradeable(self) -> bool:
        return (
            self.status in {"open", "active"}
            and self.is_binary
            and self.yes_ask_cents is not None
            and self.no_ask_cents is not None
            and 1 <= self.yes_ask_cents <= 99
            and 1 <= self.no_ask_cents <= 99
        )
