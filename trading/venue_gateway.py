"""The market facts the gate chain reads. Kalshi calls the existing client.

This module does not place orders. A later venue uses the same five methods.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Protocol

from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    INITIAL_ORDER_FEE_ACCUMULATOR,
    FeeContext,
    FeeRole,
    fee_coefficient_for,
    fee_schedule_at,
    quote_fee,
)
from trading.venue import Venue


class VenueGateway(Protocol):
    def list_markets(self, series: str) -> tuple[Any, ...]: ...

    def order_book(self, market_id: str) -> Any: ...

    def fee(self, price: Decimal, quantity: Decimal) -> Decimal: ...

    def rules_text(self, market_id: str) -> str: ...

    def settlement(self, market_id: str) -> str: ...


class KalshiGateway:
    """Existing Kalshi reads. The request filter stays status=open."""

    def __init__(
        self,
        client: Any,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._now = now or (lambda: datetime.now(timezone.utc))

    def list_markets(self, series: str) -> tuple[Any, ...]:
        name = str(series or "").strip()
        if not name:
            raise ValueError("series is required")
        found: list[Any] = []
        cursor: str | None = None
        for _page in range(20):
            kwargs: dict[str, Any] = {"status": "open", "series_ticker": name}
            if cursor:
                kwargs["cursor"] = cursor
            markets, cursor = self._client.get_markets(**kwargs)
            found.extend(markets)
            if not cursor:
                return tuple(found)
        raise RuntimeError("kalshi market pages did not end")

    def order_book(self, market_id: str) -> Any:
        ticker = str(market_id or "").strip()
        if not ticker:
            raise ValueError("market_id is required")
        return self._client.get_market_orderbook(ticker)

    def fee(self, price: Decimal, quantity: Decimal) -> Decimal:
        if not isinstance(price, Decimal) or not isinstance(quantity, Decimal):
            raise ValueError("price and quantity must be Decimal")
        timestamp = self._now()
        schedule = fee_schedule_at(venue=Venue.KALSHI, timestamp=timestamp)
        quote = quote_fee(
            FeeContext(
                schedule_id=schedule,
                role=FeeRole.TAKER,
                quantity=quantity,
                price=price,
                signed_revenue=-(quantity * price),
                order_id="venue-fee",
                accumulator=INITIAL_ORDER_FEE_ACCUMULATOR,
                multiplier=Decimal("1"),
                coefficient=fee_coefficient_for(schedule, FeeRole.TAKER),
                account_precision=DIRECT_ACCOUNT_PRECISION,
                timestamp=timestamp,
            )
        )
        return quote.net_fee

    def rules_text(self, market_id: str) -> str:
        market = self._market(market_id)
        return str(getattr(market, "rules_primary", "") or "")

    def settlement(self, market_id: str) -> str:
        market = self._market(market_id, exact=True)
        return str(getattr(market, "result", "") or "")

    def _market(self, market_id: str, *, exact: bool = False) -> Any:
        ticker = str(market_id or "").strip()
        if not ticker:
            raise ValueError("market_id is required")
        market = (
            self._client.get_market_exact(ticker)
            if exact
            else self._client.get_market(ticker)
        )
        if market is None:
            raise ValueError("market_missing")
        return market


def gateway_for(venue: str | Venue, client: Any) -> VenueGateway:
    """Kalshi is the only gateway. Another venue fails closed."""
    if isinstance(venue, Venue):
        normalized = venue
    else:
        try:
            normalized = Venue(str(venue).strip().lower())
        except ValueError as exc:
            raise ValueError(f"unsupported venue: {venue!r}") from exc
    if normalized is Venue.KALSHI:
        return KalshiGateway(client)
    raise ValueError(f"unsupported venue: {normalized.value}")
