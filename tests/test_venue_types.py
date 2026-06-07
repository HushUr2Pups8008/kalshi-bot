from __future__ import annotations

import pytest

from trading.venue import (
    Venue,
    namespaced_market_id,
    normalize_venue,
    split_namespaced_market_id,
)


def test_market_ids_are_namespaced_by_venue() -> None:
    value = namespaced_market_id(Venue.KALSHI, "KXTEST-25DEC31")

    assert value == "kalshi:KXTEST-25DEC31"
    assert split_namespaced_market_id(value) == (Venue.KALSHI, "KXTEST-25DEC31")


def test_polymarket_us_namespace_is_distinct_from_kalshi() -> None:
    assert namespaced_market_id("polymarket_us", "12345") == "polymarket_us:12345"


def test_normalize_venue_accepts_enum_and_trimmed_text() -> None:
    assert normalize_venue(Venue.KALSHI) is Venue.KALSHI
    assert normalize_venue(" POLYMARKET_US ") is Venue.POLYMARKET_US


def test_split_rejects_unknown_venue() -> None:
    with pytest.raises(ValueError, match="unsupported venue"):
        split_namespaced_market_id("unknown:123")


def test_empty_market_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="market_id is required"):
        namespaced_market_id(Venue.KALSHI, " ")

    with pytest.raises(ValueError, match="market_id is required"):
        split_namespaced_market_id("kalshi:")


def test_split_requires_venue_separator() -> None:
    with pytest.raises(ValueError, match="expected '<venue>:<market_id>'"):
        split_namespaced_market_id("KXTEST-25DEC31")
