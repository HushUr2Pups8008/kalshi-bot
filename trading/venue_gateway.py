"""The market facts the gate chain reads.

Kalshi calls the existing client. Polymarket US reads the public event,
book, rules, and settlement. This module does not place orders.
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
from utils.series_universe import get_universe


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


class PolymarketGateway:
    """Read-only Polymarket US. The client blocks every non-GET order path."""

    def __init__(
        self,
        client: Any,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._now = now or (lambda: datetime.now(timezone.utc))

    def list_markets(self, series: str) -> tuple[Any, ...]:
        name = str(series or "").strip().upper()
        if not name:
            raise ValueError("series is required")
        universe = get_universe()
        if universe.failed:
            raise ValueError("universe_unavailable")
        entry = next(
            (item for item in universe.entries if item.series == name),
            None,
        )
        if (
            entry is None
            or entry.venue != Venue.POLYMARKET_US.value
            or not entry.venue_market
        ):
            raise ValueError("series_not_polymarket")
        return tuple(self._client.list_event_markets(entry.venue_market))

    def order_book(self, market_id: str) -> Any:
        slug = str(market_id or "").strip()
        if not slug:
            raise ValueError("market_id is required")
        return self._client.get_market_book(slug)

    def fee(self, price: Decimal, quantity: Decimal) -> Decimal:
        if not isinstance(price, Decimal) or not isinstance(quantity, Decimal):
            raise ValueError("price and quantity must be Decimal")
        timestamp = self._now()
        schedule = fee_schedule_at(venue=Venue.POLYMARKET_US, timestamp=timestamp)
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
                account_precision=None,
                timestamp=timestamp,
            )
        )
        return quote.net_fee

    def rules_text(self, market_id: str) -> str:
        market = self._client.get_market(market_id)
        text = str(getattr(market, "description", "") or "").strip()
        if not text:
            raise ValueError("rules_missing")
        return text

    def settlement(self, market_id: str) -> str:
        payload = self._client.get_market_settlement(market_id)
        if not isinstance(payload, dict) or "settlement" not in payload:
            raise ValueError("settlement_unreadable")
        return _settlement_text(payload["settlement"])


def _settlement_text(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("settlement_unreadable")
    try:
        if isinstance(value, str):
            number = Decimal(value.strip())
        elif isinstance(value, (int, float, Decimal)):
            number = Decimal(str(value))
        else:
            raise ValueError("settlement_unreadable")
    except Exception as exc:
        raise ValueError("settlement_unreadable") from exc
    if not number.is_finite():
        raise ValueError("settlement_unreadable")
    return format(number, "f")


def gateway_for(venue: str | Venue, client: Any) -> VenueGateway:
    """Kalshi or Polymarket US. Another venue fails closed."""
    if isinstance(venue, Venue):
        normalized = venue
    else:
        try:
            normalized = Venue(str(venue).strip().lower())
        except ValueError as exc:
            raise ValueError(f"unsupported venue: {venue!r}") from exc
    if normalized is Venue.KALSHI:
        return KalshiGateway(client)
    if normalized is Venue.POLYMARKET_US:
        return PolymarketGateway(client)
    raise ValueError(f"unsupported venue: {normalized.value}")
