"""Fresh Kalshi executable-liquidity lookup for the G7 admission gate."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Literal, Protocol, cast

from analysis import SignalAnalysis
from trading.orderbook import BinaryMarketBook, ExecutableLiquidity, executable_buy_liquidity
from trading.venue import Venue


class KalshiOrderbookReader(Protocol):
    def get_market_orderbook(
        self,
        ticker: str,
        *,
        depth: int = 100,
    ) -> BinaryMarketBook: ...


async def fetch_kalshi_execution_liquidity(
    reader: KalshiOrderbookReader,
    analysis: SignalAnalysis,
) -> ExecutableLiquidity:
    """Fetch one book only after admission reaches its liquidity check."""

    executed_price_cents = analysis.executed_price_cents
    if (
        isinstance(executed_price_cents, bool)
        or not isinstance(executed_price_cents, int)
        or not 0 < executed_price_cents < 100
    ):
        raise ValueError("executed price must be an integer between 1 and 99 cents")
    side = analysis.side
    if side not in ("yes", "no"):
        raise ValueError("side must be yes or no")

    ticker = analysis.market.ticker
    book = await asyncio.to_thread(reader.get_market_orderbook, ticker, depth=100)
    if book.venue is not Venue.KALSHI or book.venue_market_id != ticker:
        raise ValueError("orderbook does not match the Kalshi market")
    return executable_buy_liquidity(
        book,
        side=cast(Literal["yes", "no"], side),
        limit_price=Decimal(executed_price_cents) / Decimal("100"),
    )
