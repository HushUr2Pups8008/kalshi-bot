from unittest.mock import MagicMock

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
