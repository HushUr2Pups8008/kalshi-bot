from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from kalshi import KalshiMarket, OrderResult
from trading.venue import Venue


@runtime_checkable
class VenueClient(Protocol):
    venue: Venue

    def get_markets(
        self,
        status: str = "open",
        cursor: str | None = None,
        limit: int = 200,
        series_ticker: str | None = None,
        min_close_ts: int | None = None,
        max_close_ts: int | None = None,
    ) -> tuple[list[KalshiMarket], str | None]: ...

    def get_market(self, ticker: str) -> Optional[KalshiMarket]: ...

    def get_balance(self) -> float: ...

    def get_open_positions(self) -> list[dict]: ...

    def place_limit_order(
        self,
        ticker: str,
        side: str,
        count: int,
        limit_price: int,
        expiration_ts: Optional[int] = None,
    ) -> OrderResult: ...

    def cancel_order(self, order_id: str) -> bool: ...
