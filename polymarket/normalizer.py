from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from polymarket.models import PolymarketMarket
from trading.venue import Venue


def _money_value(payload: dict[str, Any] | None, default: float = 0.0) -> float:
    if isinstance(payload, (str, int, float)) and not isinstance(payload, bool):
        try:
            return float(payload)
        except (TypeError, ValueError):
            return default
    if not isinstance(payload, dict):
        return default
    try:
        return float(payload.get("value", default))
    except (TypeError, ValueError):
        return default


def _probability_cents(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        probability = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        return None
    return int(round(probability * 100))


def _quote_cents(quote: Any) -> int | None:
    if not isinstance(quote, dict):
        return None
    return _probability_cents(quote.get("value"))


def _price_cents(outcome: dict[str, Any]) -> int | None:
    return _quote_cents(outcome.get("bestAsk"))


def _bid_cents(outcome: dict[str, Any]) -> int | None:
    return _quote_cents(outcome.get("bestBid"))


def _quote_size(quote: Any) -> Decimal | None:
    if not isinstance(quote, dict):
        return None
    value = quote.get("quantity", quote.get("size"))
    if isinstance(value, dict):
        value = value.get("value")
    return _optional_decimal(value)


def _bid_size(outcome: dict[str, Any]) -> Decimal | None:
    return _quote_size(outcome.get("bestBid"))


def _token_id(outcome: dict[str, Any]) -> str | None:
    value = outcome.get("tokenId") or outcome.get("token_id")
    token_id = str(value).strip() if value is not None else ""
    return token_id or None


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _payload_hash(payload: dict[str, Any]) -> str:
    def json_safe(value: Any) -> Any:
        if isinstance(value, dict):
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


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _text_tuple(value: Any, *keys: str) -> tuple[str, ...]:
    items = _parse_json_list(value)
    result: list[str] = []
    for item in items:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = next(
                (
                    str(item.get(key) or "").strip()
                    for key in keys
                    if str(item.get(key) or "").strip()
                ),
                "",
            )
        else:
            text = ""
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _nested_text(payload: dict[str, Any], parent: str, *keys: str) -> str:
    nested = payload.get(parent)
    if not isinstance(nested, dict):
        return ""
    for key in keys:
        value = str(nested.get(key) or "").strip()
        if value:
            return value
    return ""


def _outcome_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = _parse_json_list(payload.get("outcomes"))
    if not outcomes:
        return []
    if all(isinstance(outcome, dict) for outcome in outcomes):
        return outcomes
    if all(isinstance(outcome, str) for outcome in outcomes):
        return [{"name": outcome} for outcome in outcomes]
    return []


def _oriented_market_sides(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    sides = _parse_json_list(payload.get("marketSides"))
    if len(sides) != 2 or not all(isinstance(side, dict) for side in sides):
        return None
    long_sides = [side for side in sides if side.get("long") is True]
    short_sides = [side for side in sides if side.get("long") is False]
    if len(long_sides) != 1 or len(short_sides) != 1:
        return None
    return long_sides[0], short_sides[0]


def _long_book_prices(
    payload: dict[str, Any],
) -> tuple[
    int | None,
    int,
    int | None,
    int,
    Decimal | None,
    Decimal | None,
    str,
] | None:
    sides = _oriented_market_sides(payload)
    if sides is None:
        return None

    has_top_book = "bestBidQuote" in payload or "bestAskQuote" in payload
    top_bid = payload.get("bestBidQuote")
    top_ask = payload.get("bestAskQuote")
    if has_top_book:
        if top_bid is None or top_ask is None:
            return None
        yes_bid = _quote_cents(top_bid)
        yes_ask = _quote_cents(top_ask)
        if yes_bid is None or yes_ask is None or yes_bid > yes_ask:
            return None
        return (
            yes_bid,
            yes_ask,
            100 - yes_ask,
            100 - yes_bid,
            _quote_size(top_bid),
            _quote_size(top_ask),
            "pm_long_book_v1",
        )

    long_side, short_side = sides
    yes_ask = _quote_cents(long_side.get("quote"))
    no_ask = _quote_cents(short_side.get("quote"))
    if yes_ask is None or no_ask is None:
        return None
    return None, yes_ask, None, no_ask, None, None, "pm_named_sides_v1"


def _status(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("status") or "").strip().lower()
    if explicit:
        return explicit
    if payload.get("closed") is True:
        return "closed"
    if payload.get("active") is True:
        return "open"
    return ""


def _venue_market_id(payload: dict[str, Any]) -> str | None:
    raw_id = payload.get("id")
    if raw_id is None:
        return None
    canonical_id = str(raw_id).strip()
    if not canonical_id:
        return None
    if not canonical_id.isascii() or not canonical_id.isdigit():
        raise ValueError("Polymarket canonical id must be numeric")
    return canonical_id


def normalize_polymarket_market(
    payload: dict[str, Any],
    *,
    snapshot_at: datetime | None = None,
) -> PolymarketMarket:
    outcomes = _outcome_dicts(payload)
    if len(outcomes) != 2:
        raise ValueError("Polymarket integration supports binary markets only")

    by_name = {
        str(outcome.get("name", "")).strip().lower(): outcome
        for outcome in outcomes
        if isinstance(outcome, dict)
    }
    if "yes" not in by_name or "no" not in by_name:
        raise ValueError("binary Polymarket market must contain Yes and No outcomes")

    if "marketSides" in payload or "bestBidQuote" in payload or "bestAskQuote" in payload:
        long_book_prices = _long_book_prices(payload)
        if long_book_prices is None:
            yes_bid_cents = None
            yes_ask_cents = None
            no_bid_cents = None
            no_ask_cents = None
            yes_bid_size = None
            no_bid_size = None
            price_source = ""
            price_method = ""
        else:
            (
                yes_bid_cents,
                yes_ask_cents,
                no_bid_cents,
                no_ask_cents,
                yes_bid_size,
                no_bid_size,
                price_method,
            ) = long_book_prices
            price_source = "polymarket_public"
    else:
        yes_bid_cents = _bid_cents(by_name["yes"])
        yes_ask_cents = _price_cents(by_name["yes"])
        no_bid_cents = _bid_cents(by_name["no"])
        no_ask_cents = _price_cents(by_name["no"])
        yes_bid_size = _bid_size(by_name["yes"])
        no_bid_size = _bid_size(by_name["no"])
        if yes_ask_cents is None or no_ask_cents is None:
            yes_ask_cents = None
            no_ask_cents = None
            price_source = ""
            price_method = ""
        else:
            price_source = "polymarket_public"
            price_method = "pm_named_outcomes_v1"

    return PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id=str(payload.get("slug") or payload.get("id") or "").strip(),
        title=str(payload.get("title") or payload.get("question") or "").strip(),
        status=_status(payload),
        yes_ask_cents=yes_ask_cents,
        no_ask_cents=no_ask_cents,
        yes_bid_cents=yes_bid_cents,
        no_bid_cents=no_bid_cents,
        yes_bid_size=yes_bid_size,
        no_bid_size=no_bid_size,
        volume_dollars=_money_value(payload.get("volume")),
        open_interest_dollars=_money_value(payload.get("openInterest")),
        close_time=str(
            payload.get("closeTime")
            or payload.get("close_time")
            or payload.get("endDate")
            or ""
        ),
        venue_market_id=_venue_market_id(payload),
        is_binary=True,
        question=str(payload.get("question") or "").strip(),
        subtitle=str(payload.get("subtitle") or "").strip(),
        category=str(payload.get("category") or "").strip().lower(),
        resolution_source=str(
            payload.get("resolutionSource")
            or payload.get("resolution_source")
            or ""
        ).strip(),
        description=str(payload.get("description") or "").strip(),
        event_title=str(payload.get("eventTitle") or _nested_text(payload, "event", "title", "name") or "").strip(),
        event_slug=str(payload.get("eventSlug") or _nested_text(payload, "event", "slug", "id") or "").strip(),
        series_title=str(payload.get("seriesTitle") or _nested_text(payload, "series", "title", "name") or "").strip(),
        series_slug=str(payload.get("seriesSlug") or _nested_text(payload, "series", "slug", "id") or "").strip(),
        tags=_text_tuple(payload.get("tags"), "label", "name", "slug"),
        public_comments=_text_tuple(payload.get("comments"), "body", "text", "comment"),
        price_source=price_source,
        price_method=price_method,
        yes_token_id=_token_id(by_name["yes"]),
        no_token_id=_token_id(by_name["no"]),
        fee_coefficient=_optional_decimal(payload.get("feeCoefficient")),
        fee_effective_at=_optional_datetime(payload.get("feeEffectiveAt")),
        quantity_step=_optional_decimal(payload.get("quantityStep")),
        price_tick=_optional_decimal(payload.get("priceTick")),
        fill_role=str(payload.get("fillRole") or "") or None,
        source_payload_hash=_payload_hash(payload),
        snapshot_at=snapshot_at or datetime.now(timezone.utc),
    )
