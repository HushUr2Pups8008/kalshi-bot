from unittest.mock import MagicMock

import requests

from polymarket.public_client import PolymarketPublicClient
from trading.venue import Venue


def _market_payload(slug: str = "m1") -> dict:
    return {
        "slug": slug,
        "title": "Will X?",
        "status": "open",
        "outcomes": [
            {"name": "Yes", "bestAsk": {"value": "0.40"}},
            {"name": "No", "bestAsk": {"value": "0.61"}},
        ],
    }


def test_get_markets_uses_public_gateway_and_normalizes():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    response = MagicMock()
    response.text = '{"markets":[{"slug":"m1"}]}'
    response.json.return_value = {"markets": [_market_payload()], "cursor": None}
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    markets, cursor = client.get_markets(limit=1)

    assert client.venue == Venue.POLYMARKET_US
    assert len(markets) == 1
    assert markets[0].market_id == "m1"
    assert cursor is None
    assert client._session.request.call_args.args[:2] == (
        "GET",
        "https://gateway.polymarket.us/v1/markets",
    )
    kwargs = client._session.request.call_args.kwargs
    assert kwargs["params"] == {"limit": 1, "closed": "false"}
    headers = kwargs["headers"]
    assert headers["Accept"] == "application/json"
    assert "X-PM-Access-Key" not in headers
    assert "X-PM-Signature" not in headers


def test_get_markets_passes_cursor_and_returns_cursor():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us/")
    response = MagicMock()
    response.text = '{"markets":[],"cursor":"next"}'
    response.json.return_value = {"markets": [], "cursor": "next"}
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    markets, cursor = client.get_markets(limit=25, cursor="abc")

    assert markets == []
    assert cursor == "next"
    assert client._session.request.call_args.kwargs["params"] == {
        "limit": 25,
        "cursor": "abc",
        "closed": "false",
    }


def test_get_market_normalizes_single_market_payload():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    response = MagicMock()
    response.text = '{"market":{"slug":"m2"}}'
    response.json.return_value = {"market": _market_payload("m2")}
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    market = client.get_market("m2")

    assert market.market_id == "m2"
    assert client._session.request.call_args.args[:2] == (
        "GET",
        "https://gateway.polymarket.us/v1/markets/m2",
    )


def test_get_market_payload_falls_back_to_open_market_slug_lookup_on_404():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    not_found = MagicMock()
    not_found.text = "{}"
    not_found.status_code = 404
    not_found.raise_for_status.side_effect = requests.HTTPError(
        response=not_found
    )
    listed = MagicMock()
    listed.text = '{"markets":[]}'
    listed.json.return_value = {"markets": [_market_payload("m2")], "cursor": None}
    listed.raise_for_status.return_value = None
    client._session.request = MagicMock(side_effect=[not_found, listed])

    payload = client.get_market_payload("m2")

    assert payload["slug"] == "m2"
    assert client._session.request.call_args_list[1].kwargs["params"] == {
        "limit": 500,
        "closed": "false",
    }


def test_get_market_payload_falls_back_to_closed_market_slug_lookup_for_settlement():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    not_found = MagicMock()
    not_found.text = "{}"
    not_found.status_code = 404
    not_found.raise_for_status.side_effect = requests.HTTPError(
        response=not_found
    )
    open_page = MagicMock()
    open_page.text = '{"markets":[]}'
    open_page.json.return_value = {"markets": [], "cursor": None}
    open_page.raise_for_status.return_value = None
    closed_page = MagicMock()
    closed_page.text = '{"markets":[]}'
    closed_payload = {
        **_market_payload("m2"),
        "closed": True,
        "resolvedOutcome": "YES",
    }
    closed_page.json.return_value = {"markets": [closed_payload], "cursor": None}
    closed_page.raise_for_status.return_value = None
    client._session.request = MagicMock(
        side_effect=[not_found, open_page, closed_page]
    )

    payload = client.get_market_payload("m2")

    assert payload["slug"] == "m2"
    assert payload["resolvedOutcome"] == "YES"
    assert client._session.request.call_args_list[2].kwargs["params"] == {
        "limit": 500,
        "closed": "true",
    }


def test_get_markets_skips_unsupported_payloads():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    response = MagicMock()
    response.text = '{"markets":[]}'
    response.json.return_value = {
        "markets": [
            _market_payload("m1"),
            {"slug": "multi", "outcomes": [{"name": "A"}, {"name": "B"}, {"name": "C"}]},
        ]
    }
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    markets, cursor = client.get_markets()

    assert [market.market_id for market in markets] == ["m1"]
    assert cursor is None
