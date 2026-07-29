from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading.orderbook import (
    BinaryMarketBook,
    BookLevel,
    executable_buy_liquidity,
)
from trading.venue import Venue


def _book(
    *,
    yes_bids: tuple[BookLevel, ...] = (),
    no_bids: tuple[BookLevel, ...] = (),
) -> BinaryMarketBook:
    return BinaryMarketBook(
        venue=Venue.KALSHI,
        venue_market_id="KXBOOK-1",
        yes_bids=yes_bids,
        no_bids=no_bids,
        as_of=datetime(2026, 7, 29, 15, 46, tzinfo=UTC),
        raw_payload_hash="a" * 64,
    )


def test_executable_yes_buy_liquidity_uses_no_bids_at_or_better_than_limit() -> None:
    liquidity = executable_buy_liquidity(
        _book(
            no_bids=(
                BookLevel(Decimal("0.98"), Decimal("70")),
                BookLevel(Decimal("0.95"), Decimal("100")),
                BookLevel(Decimal("0.90"), Decimal("40")),
            )
        ),
        side="yes",
        limit_price=Decimal("0.05"),
    )

    assert liquidity.market_ticker == "KXBOOK-1"
    assert liquidity.side == "yes"
    assert liquidity.best_price == Decimal("0.02")
    assert liquidity.executable_quantity == Decimal("170")
    assert liquidity.executable_notional == Decimal("6.40")


def test_executable_no_buy_liquidity_uses_yes_bids_at_or_better_than_limit() -> None:
    liquidity = executable_buy_liquidity(
        _book(
            yes_bids=(
                BookLevel(Decimal("0.97"), Decimal("5")),
                BookLevel(Decimal("0.95"), Decimal("100")),
            )
        ),
        side="no",
        limit_price=Decimal("0.05"),
    )

    assert liquidity.best_price == Decimal("0.03")
    assert liquidity.executable_quantity == Decimal("105")
    assert liquidity.executable_notional == Decimal("5.15")


def test_executable_buy_liquidity_reports_zero_when_no_level_meets_limit() -> None:
    liquidity = executable_buy_liquidity(
        _book(no_bids=(BookLevel(Decimal("0.90"), Decimal("40")),)),
        side="yes",
        limit_price=Decimal("0.05"),
    )

    assert liquidity.best_price is None
    assert liquidity.executable_quantity == Decimal("0")
    assert liquidity.executable_notional == Decimal("0")


def test_executable_buy_liquidity_rejects_invalid_side() -> None:
    with pytest.raises(ValueError, match="side"):
        executable_buy_liquidity(
            _book(),
            side="maybe",
            limit_price=Decimal("0.05"),
        )
