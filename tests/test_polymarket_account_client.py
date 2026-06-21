from unittest.mock import MagicMock

from kalshi import OrderResult
from polymarket.account_client import PolymarketAccountClient
from tests.test_polymarket_auth import _secret_b64


def test_get_positions_uses_authenticated_api_path():
    secret, _ = _secret_b64()
    client = PolymarketAccountClient(
        key_id="key",
        secret_b64=secret,
        base_url="https://api.polymarket.us",
    )
    response = MagicMock()
    response.text = '{"positions":{},"nextCursor":null,"eof":true}'
    response.json.return_value = {"positions": {}, "nextCursor": None, "eof": True}
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    data = client.get_positions(limit=10)

    assert data["positions"] == {}
    assert client._session.request.call_args.args[:2] == (
        "GET",
        "https://api.polymarket.us/v1/portfolio/positions",
    )
    kwargs = client._session.request.call_args.kwargs
    assert kwargs["params"] == {"limit": 10}
    headers = kwargs["headers"]
    assert headers["X-PM-Access-Key"] == "key"
    assert headers["X-PM-Signature"]


def test_get_positions_passes_market_and_cursor():
    secret, _ = _secret_b64()
    client = PolymarketAccountClient(key_id="key", secret_b64=secret)
    response = MagicMock()
    response.text = '{"positions":[]}'
    response.json.return_value = {"positions": []}
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    client.get_positions(market="m1", limit=5, cursor="abc")

    assert client._session.request.call_args.kwargs["params"] == {
        "limit": 5,
        "market": "m1",
        "cursor": "abc",
    }


def test_get_balance_reads_account_balances_buying_power():
    secret, _ = _secret_b64()
    client = PolymarketAccountClient(
        key_id="key",
        secret_b64=secret,
        base_url="https://api.polymarket.us",
    )
    response = MagicMock()
    response.text = '{"balances":[{"currency":"USD","buyingPower":850.0}]}'
    response.json.return_value = {
        "balances": [{"currency": "USD", "buyingPower": 850.0}]
    }
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    assert client.get_balance() == 850.0
    assert (
        client._session.request.call_args.args[1]
        == "https://api.polymarket.us/v1/account/balances"
    )


def test_get_balance_falls_back_to_current_balance():
    secret, _ = _secret_b64()
    client = PolymarketAccountClient(key_id="key", secret_b64=secret)
    response = MagicMock()
    response.text = '{"balances":[{"currency":"USD","currentBalance":125.5}]}'
    response.json.return_value = {
        "balances": [{"currency": "USD", "currentBalance": 125.5}]
    }
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    assert client.get_balance() == 125.5


def test_place_limit_order_is_hard_gated_before_live_phase():
    secret, _ = _secret_b64()
    client = PolymarketAccountClient(key_id="key", secret_b64=secret)
    client._session.request = MagicMock()

    result = client.place_limit_order(ticker="m1", side="yes", count=1, limit_price=40)

    assert isinstance(result, OrderResult)
    assert result.status == "error"
    assert "not enabled" in result.error.lower()
    assert client._session.request.call_count == 0
