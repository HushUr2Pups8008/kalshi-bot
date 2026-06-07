from __future__ import annotations

from enum import StrEnum


class Venue(StrEnum):
    KALSHI = "kalshi"
    POLYMARKET_US = "polymarket_us"


def normalize_venue(value: str | Venue) -> Venue:
    if isinstance(value, Venue):
        return value

    try:
        return Venue(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported venue: {value!r}") from exc


def namespaced_market_id(venue: str | Venue, market_id: str) -> str:
    normalized = normalize_venue(venue)
    market = str(market_id).strip()
    if not market:
        raise ValueError("market_id is required")
    return f"{normalized.value}:{market}"


def split_namespaced_market_id(value: str) -> tuple[Venue, str]:
    text = str(value).strip()
    if ":" not in text:
        raise ValueError("expected '<venue>:<market_id>'")

    venue_text, market_id = text.split(":", 1)
    venue = normalize_venue(venue_text)
    market = market_id.strip()
    if not market:
        raise ValueError("market_id is required")
    return venue, market
