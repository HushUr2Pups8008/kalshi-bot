"""Polymarket stays read-only. Order paths raise before any socket."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from polymarket.account_client import PolymarketAccountClient
from polymarket.public_client import PolymarketPublicClient
from polymarket.read_only import PolymarketOrderBlocked, assert_polymarket_read_only
from tests.test_polymarket_auth import _secret_b64


def test_only_a_plain_get_is_allowed() -> None:
    assert_polymarket_read_only("GET", "/v1/markets")
    assert_polymarket_read_only("GET", "/v1/markets/hormuz-normal/book")
    assert_polymarket_read_only("GET", "/v1/markets/hormuz-normal/settlement")
    assert_polymarket_read_only("GET", "/v1/events/slug/hormuz-normal")
    assert_polymarket_read_only("GET", "/v1/portfolio/positions")
    assert_polymarket_read_only("GET", "/v1/account/balances")
    for method, endpoint in (
        ("POST", "/v1/orders"),
        ("POST", "/v1/markets"),
        ("DELETE", "/v1/orders/abc"),
        ("GET", "/v1/orders"),
        ("GET", "/v1/portfolio/deposits"),
        ("GET", "/v1/wallet"),
        ("PUT", "/v1/orders/abc"),
    ):
        with pytest.raises(PolymarketOrderBlocked):
            assert_polymarket_read_only(method, endpoint)


def test_public_order_paths_do_not_call_the_session() -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    client._session.request = MagicMock()
    for method, endpoint in (
        ("POST", "/v1/orders"),
        ("GET", "/v1/orders"),
        ("POST", "/v1/transfers"),
    ):
        with pytest.raises(PolymarketOrderBlocked):
            client._request(method, endpoint)
    assert client._session.request.call_count == 0


def test_public_market_get_still_reaches_the_session() -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    response = MagicMock()
    response.text = '{"markets":[]}'
    response.json.return_value = {"markets": []}
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    markets, cursor = client.get_markets(limit=1)

    assert markets == []
    assert cursor is None
    assert client._session.request.call_args.args[:2] == (
        "GET",
        "https://gateway.polymarket.us/v1/markets",
    )


def test_event_get_uses_the_slug_path_and_keeps_open_markets() -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    response = MagicMock()
    payload = {
        "event": {
            "slug": "hormuz-normal",
            "markets": [
                {"slug": "stsc-hormuz-normal-2026-12-31", "status": "MARKET_STATUS_OPEN"},
                {"slug": "old-hormuz", "status": "MARKET_STATUS_RESOLVED"},
            ],
        }
    }
    response.text = "{}"
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    markets = client.list_event_markets("hormuz-normal")

    assert markets == (
        {"slug": "stsc-hormuz-normal-2026-12-31", "status": "MARKET_STATUS_OPEN"},
    )
    assert client._session.request.call_args.args[1] == (
        "https://gateway.polymarket.us/v1/events/slug/hormuz-normal"
    )


def test_unknown_event_market_status_fails_closed() -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    response = MagicMock()
    response.text = "{}"
    response.json.return_value = {
        "event": {
            "slug": "hormuz-normal",
            "markets": [{"slug": "stsc-hormuz-normal-2026-12-31", "status": "OPEN"}],
        }
    }
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)
    with pytest.raises(ValueError, match="market_status_unknown"):
        client.list_event_markets("hormuz-normal")


def test_account_order_paths_do_not_call_the_session() -> None:
    secret, _ = _secret_b64()
    client = PolymarketAccountClient(
        key_id="key",
        secret_b64=secret,
        base_url="https://api.polymarket.us",
    )
    client._session.request = MagicMock()
    with pytest.raises(PolymarketOrderBlocked):
        client._request("POST", "/v1/orders")
    with pytest.raises(PolymarketOrderBlocked):
        client._request("GET", "/v1/orders")
    assert client._session.request.call_count == 0


def test_authoritative_order_path_does_not_fetch() -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    fetcher = MagicMock()

    async def _blocked() -> None:
        await client._get_authoritative_object(
            "/v1/orders",
            timeout_seconds=1,
            fetcher=fetcher,
            provider_name="test",
        )

    with pytest.raises(PolymarketOrderBlocked):
        asyncio.run(_blocked())
    assert fetcher.call_count == 0
