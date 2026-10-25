"""Hypothetical resting bid. No order is placed. The tape test is not queue-aware."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping, Sequence

from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    INITIAL_ORDER_FEE_ACCUMULATOR,
    FeeContext,
    FeeRole,
    FeeUnscorableError,
    fee_coefficient_for,
    fee_schedule_at,
    quote_fee,
)
from trading.venue import Venue
from utils.event_news_desk_flags import event_news_maker_limit_cents
from utils.event_news_grok_flags import EVENT_NEWS_BAND_FLOOR_CENTS


def resting_bid_cents(ask_cents: object) -> int | None:
    if isinstance(ask_cents, bool) or not isinstance(ask_cents, int):
        return None
    return event_news_maker_limit_cents(
        ask_cents, floor_cents=EVENT_NEWS_BAND_FLOOR_CENTS
    )


def yes_price_cents(raw: object) -> int | None:
    try:
        cents = Decimal(str(raw)) * 100
    except Exception:
        return None
    if not cents.is_finite() or cents != cents.to_integral_value():
        return None
    value = int(cents)
    return value if 0 <= value <= 100 else None


def _fills(side: str, limit_cents: int, yes_cents: int) -> bool:
    if side == "yes":
        return yes_cents <= limit_cents
    if side == "no":
        return yes_cents >= 100 - limit_cents
    return False


def _trade_cents(trade: Mapping[str, object]) -> int | None:
    raw = trade.get("yes_price_cents")
    if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= 100:
        return raw
    return yes_price_cents(trade.get("yes_price_dollars"))


def first_fill_cents(
    trades: Sequence[Mapping[str, object]],
    side: str,
    limit_cents: int,
) -> int | None:
    if isinstance(limit_cents, bool) or not isinstance(limit_cents, int):
        return None
    for trade in trades:
        yes_cents = _trade_cents(trade)
        if yes_cents is None:
            continue
        if _fills(side, limit_cents, yes_cents):
            return yes_cents
    return None


def quoted_fee(
    price_cents: int,
    contracts: int,
    role: FeeRole,
    venue: Venue,
    when: datetime,
) -> Decimal | None:
    if isinstance(price_cents, bool) or not isinstance(price_cents, int):
        return None
    if price_cents < 1 or price_cents > 99:
        return None
    if isinstance(contracts, bool) or not isinstance(contracts, int) or contracts < 1:
        return None
    if when.tzinfo is None or when.utcoffset() is None:
        return None
    price = Decimal(price_cents) / Decimal(100)
    quantity = Decimal(contracts)
    try:
        schedule = fee_schedule_at(venue=venue, timestamp=when)
        return quote_fee(
            FeeContext(
                schedule_id=schedule,
                role=role,
                quantity=quantity,
                price=price,
                signed_revenue=-(quantity * price),
                order_id="shadow",
                accumulator=INITIAL_ORDER_FEE_ACCUMULATOR,
                multiplier=Decimal("1"),
                coefficient=fee_coefficient_for(schedule, role),
                account_precision=(
                    DIRECT_ACCOUNT_PRECISION if venue is Venue.KALSHI else None
                ),
                timestamp=when,
            )
        ).net_fee
    except FeeUnscorableError:
        return None


def fee_saved(
    ask_cents: int,
    limit_cents: int,
    contracts: int,
    venue: Venue,
    when: datetime,
) -> Decimal | None:
    taker = quoted_fee(ask_cents, contracts, FeeRole.TAKER, venue, when)
    maker = quoted_fee(limit_cents, contracts, FeeRole.MAKER, venue, when)
    if taker is None or maker is None:
        return None
    return taker - maker


def settled_fee_net(
    result: str,
    side: str,
    price_cents: int,
    fee: Decimal,
    contracts: int,
) -> Decimal | None:
    if result not in {"yes", "no"} or side not in {"yes", "no"}:
        return None
    if not isinstance(fee, Decimal) or not fee.is_finite():
        return None
    if isinstance(price_cents, bool) or not isinstance(price_cents, int):
        return None
    if price_cents < 1 or price_cents > 99:
        return None
    if isinstance(contracts, bool) or not isinstance(contracts, int) or contracts < 1:
        return None
    revenue = Decimal(contracts) if result == side else Decimal(0)
    cost = Decimal(contracts) * Decimal(price_cents) / Decimal(100)
    return revenue - cost - fee
