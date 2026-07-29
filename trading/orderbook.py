from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping

from trading.venue import Venue


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        if not self.price.is_finite() or self.price <= 0 or self.price >= 1:
            raise ValueError("book price must be between zero and one")
        if not self.quantity.is_finite() or self.quantity <= 0:
            raise ValueError("book quantity must be positive")


@dataclass(frozen=True)
class BinaryMarketBook:
    venue: Venue
    venue_market_id: str
    yes_bids: tuple[BookLevel, ...]
    no_bids: tuple[BookLevel, ...]
    as_of: datetime
    raw_payload_hash: str

    def __post_init__(self) -> None:
        if not self.venue_market_id.strip():
            raise ValueError("venue_market_id is required")
        if self.as_of.tzinfo is None:
            raise ValueError("book timestamp must be timezone-aware")
        for levels in (self.yes_bids, self.no_bids):
            if any(left.price < right.price for left, right in zip(levels, levels[1:])):
                raise ValueError("bid levels must be sorted best-to-worst")
        if self.yes_bids and self.no_bids:
            if self.yes_bids[0].price + self.no_bids[0].price > Decimal("1"):
                raise ValueError("crossed binary book")
        if len(self.raw_payload_hash) != 64:
            raise ValueError("raw_payload_hash must be a SHA-256 digest")


@dataclass(frozen=True)
class ExecutableLiquidity:
    """Fresh, side-aware executable liquidity derived from one binary book."""

    market_ticker: str
    side: Literal["yes", "no"]
    limit_price: Decimal
    best_price: Decimal | None
    executable_quantity: Decimal
    executable_notional: Decimal
    as_of: datetime
    raw_payload_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.market_ticker, str) or not self.market_ticker.strip():
            raise ValueError("market_ticker is required")
        if self.side not in ("yes", "no"):
            raise ValueError("side must be yes or no")
        if (
            not isinstance(self.limit_price, Decimal)
            or not self.limit_price.is_finite()
            or self.limit_price <= 0
            or self.limit_price >= 1
        ):
            raise ValueError("limit_price must be between zero and one")
        if self.best_price is not None and (
            not isinstance(self.best_price, Decimal)
            or not self.best_price.is_finite()
            or self.best_price <= 0
            or self.best_price > self.limit_price
        ):
            raise ValueError("best_price must be positive and no worse than the limit")
        for field_name, value in (
            ("executable_quantity", self.executable_quantity),
            ("executable_notional", self.executable_notional),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{field_name} must be finite and non-negative")
        if self.executable_quantity == 0 and (
            self.best_price is not None or self.executable_notional != 0
        ):
            raise ValueError("empty executable liquidity cannot have price or notional")
        if self.executable_quantity > 0 and self.best_price is None:
            raise ValueError("executable liquidity requires a best price")
        if self.as_of.tzinfo is None:
            raise ValueError("liquidity timestamp must be timezone-aware")
        if len(self.raw_payload_hash) != 64:
            raise ValueError("raw_payload_hash must be a SHA-256 digest")


def executable_buy_liquidity(
    book: BinaryMarketBook,
    *,
    side: Literal["yes", "no"],
    limit_price: Decimal,
) -> ExecutableLiquidity:
    """Return fillable binary-contract notional at or better than a buy limit."""

    if side not in ("yes", "no"):
        raise ValueError("side must be yes or no")
    if (
        not isinstance(limit_price, Decimal)
        or not limit_price.is_finite()
        or limit_price <= 0
        or limit_price >= 1
    ):
        raise ValueError("limit_price must be between zero and one")

    opposing_bids = book.no_bids if side == "yes" else book.yes_bids
    eligible_levels = [
        (Decimal("1") - level.price, level.quantity)
        for level in opposing_bids
        if Decimal("1") - level.price <= limit_price
    ]
    executable_quantity = sum((quantity for _, quantity in eligible_levels), Decimal("0"))
    executable_notional = sum(
        (price * quantity for price, quantity in eligible_levels),
        Decimal("0"),
    )
    return ExecutableLiquidity(
        market_ticker=book.venue_market_id,
        side=side,
        limit_price=limit_price,
        best_price=eligible_levels[0][0] if eligible_levels else None,
        executable_quantity=executable_quantity,
        executable_notional=executable_notional,
        as_of=book.as_of,
        raw_payload_hash=book.raw_payload_hash,
    )


def decimal_value(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid decimal book value") from exc
    if not parsed.is_finite():
        raise ValueError("book value must be finite")
    return parsed


def payload_sha256(payload: Mapping[str, Any]) -> str:
    def json_safe(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(item) for item in value]
        if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > 12_000:
            sign = "-" if value < 0 else ""
            return {"__oversized_int_hex__": f"{sign}{abs(value):x}"}
        return value

    canonical = json.dumps(
        json_safe(payload), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
