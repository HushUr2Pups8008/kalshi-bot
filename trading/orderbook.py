from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

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
