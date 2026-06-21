from __future__ import annotations

from typing import get_type_hints

from kalshi.rest_client import KalshiRestClient
from trading.venue import Venue
from trading.venue_client import VenueClient


def test_venue_client_is_runtime_checkable_protocol() -> None:
    assert getattr(VenueClient, "_is_runtime_protocol", False) is True


def test_kalshi_rest_client_declares_kalshi_venue() -> None:
    assert KalshiRestClient.venue is Venue.KALSHI
    assert KalshiRestClient().venue is Venue.KALSHI


def test_kalshi_rest_client_satisfies_venue_client_protocol(monkeypatch) -> None:
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY_SECRET", raising=False)

    client = KalshiRestClient()

    assert isinstance(client, VenueClient)


def test_venue_client_protocol_exposes_required_methods() -> None:
    hints = get_type_hints(VenueClient)

    assert hints["venue"] is Venue
    for method_name in (
        "get_markets",
        "get_market",
        "get_balance",
        "get_open_positions",
        "place_limit_order",
        "cancel_order",
    ):
        assert hasattr(VenueClient, method_name)
