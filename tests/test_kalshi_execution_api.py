from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from kalshi.rest_client import KalshiRestClient


def _client_with_payload(payload: object) -> KalshiRestClient:
    client = KalshiRestClient()
    response = MagicMock()
    response.status_code = 200
    response.text = "{}"
    response.json.return_value = payload
    client._session.request = MagicMock(return_value=response)  # noqa: SLF001
    return client


def test_get_order_receipt_uses_one_exact_get_without_redirects() -> None:
    expected_order = {"order_id": "order-123", "status": "executed"}
    client = _client_with_payload({"order": expected_order})

    assert client.get_order_receipt("order-123") == expected_order

    request = client._session.request  # noqa: SLF001
    request.assert_called_once()
    args, kwargs = request.call_args
    assert args == ("GET", f"{client._base}/portfolio/orders/order-123")  # noqa: SLF001
    assert kwargs["params"] is None
    assert kwargs["data"] is None
    assert kwargs["allow_redirects"] is False


def test_get_fills_page_uses_exact_get_params_without_redirects() -> None:
    expected_fills = [{"fill_id": "fill-123", "order_id": "order-123", "count": 2}]
    client = _client_with_payload({"fills": expected_fills, "cursor": "next-cursor"})

    fills, cursor = client.get_fills_page(
        order_id="order-123",
        min_ts=1_700_000_000,
        max_ts=1_700_000_100,
        limit=50,
        cursor="prior-cursor",
        subaccount=7,
    )

    assert fills == expected_fills
    assert cursor == "next-cursor"
    request = client._session.request  # noqa: SLF001
    request.assert_called_once()
    args, kwargs = request.call_args
    assert args == ("GET", f"{client._base}/portfolio/fills")  # noqa: SLF001
    assert kwargs["params"] == {
        "order_id": "order-123",
        "min_ts": 1_700_000_000,
        "max_ts": 1_700_000_100,
        "limit": 50,
        "cursor": "prior-cursor",
        "subaccount": 7,
    }
    assert kwargs["data"] is None
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"order": []},
        {"order": {"order_id": 123}},
        {"order": {"order_id": "different-order"}},
    ],
)
def test_get_order_receipt_rejects_malformed_or_mismatched_response(payload: object) -> None:
    client = _client_with_payload(payload)

    with pytest.raises(ValueError):
        client.get_order_receipt("order-123")


@pytest.mark.parametrize(
    "payload",
    [
        {"fills": {}, "cursor": "next-cursor"},
        {"fills": [], "cursor": None},
        {"fills": [{"fill_id": "fill-123", "order_id": "order-123"}], "cursor": 123},
    ],
)
def test_get_fills_page_rejects_invalid_outer_response(payload: object) -> None:
    client = _client_with_payload(payload)

    with pytest.raises(ValueError):
        client.get_fills_page(order_id="order-123")


@pytest.mark.parametrize(
    "raw_fills",
    [
        ["not-an-object"],
        [{"order_id": "order-123"}],
        [{"fill_id": "fill-123"}],
        [{"fill_id": "fill-123", "order_id": "different-order"}],
    ],
)
def test_get_fills_page_preserves_unexpected_receipts_for_ledger_quarantine(
    raw_fills: list[object],
) -> None:
    client = _client_with_payload({"fills": raw_fills, "cursor": "next-cursor"})

    fills, cursor = client.get_fills_page(order_id="order-123")

    assert fills == raw_fills
    assert cursor == "next-cursor"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"limit": 0}, "fill limit"),
        ({"limit": 1_001}, "fill limit"),
        ({"subaccount": -1}, "fill subaccount"),
        ({"subaccount": 64}, "fill subaccount"),
    ],
)
def test_get_fills_page_rejects_out_of_range_query_before_network(
    kwargs: dict[str, int],
    match: str,
) -> None:
    client = _client_with_payload({"fills": [], "cursor": ""})

    with pytest.raises(ValueError, match=match):
        client.get_fills_page(order_id="order-123", **kwargs)

    client._session.request.assert_not_called()  # noqa: SLF001


def test_execution_ids_are_trimmed_and_opaque_cursors_are_preserved() -> None:
    client = _client_with_payload({"order": {"order_id": "order-123"}})

    assert client.get_order_receipt(" order-123 ") == {"order_id": "order-123"}
    args, _kwargs = client._session.request.call_args  # noqa: SLF001
    assert args == ("GET", f"{client._base}/portfolio/orders/order-123")  # noqa: SLF001

    client = _client_with_payload({"fills": [], "cursor": ""})
    fills, cursor = client.get_fills_page(order_id="order-123", cursor=" opaque cursor ")

    assert fills == []
    assert cursor is None
    request_kwargs = client._session.request.call_args.kwargs  # noqa: SLF001
    assert request_kwargs["params"]["cursor"] == " opaque cursor "


def test_get_order_receipt_rejects_redirect_before_decoding_receipt() -> None:
    client = KalshiRestClient()
    response = MagicMock()
    response.status_code = 307
    response.text = '{"order": {"order_id": "order-123"}}'
    client._session.request = MagicMock(return_value=response)  # noqa: SLF001

    with pytest.raises(requests.HTTPError, match="unexpected redirect response"):
        client.get_order_receipt("order-123")

    response.json.assert_not_called()
    request_kwargs = client._session.request.call_args.kwargs  # noqa: SLF001
    assert request_kwargs["allow_redirects"] is False
