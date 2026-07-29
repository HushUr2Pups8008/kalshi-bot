from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
import requests

import kalshi.rest_client as rest_client_module
from kalshi.rest_client import (
    KalshiRestClient,
    _is_transient_request_exception,
)


class _FailingSession:
    def __init__(self, exc: requests.RequestException) -> None:
        self.exc = exc

    def request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise self.exc


def _http_error(status: int, body: bytes) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response._content = body  # noqa: SLF001
    return requests.HTTPError(response=response)


def _only_record(caplog) -> logging.LogRecord:
    assert len(caplog.records) == 1
    return caplog.records[0]


def test_rest_client_retry_policy_explicitly_excludes_post(monkeypatch) -> None:
    retry_kwargs: dict[str, object] = {}

    class CapturingRetry:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            retry_kwargs.update(kwargs)

    class CapturingAdapter:
        def __init__(self, *, max_retries) -> None:  # noqa: ANN001
            self.max_retries = max_retries

    monkeypatch.setattr(rest_client_module, "Retry", CapturingRetry)
    monkeypatch.setattr(rest_client_module, "HTTPAdapter", CapturingAdapter)

    KalshiRestClient()

    allowed_methods = retry_kwargs["allowed_methods"]
    assert allowed_methods == frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PUT", "TRACE"})
    assert "POST" not in allowed_methods


def test_legacy_order_post_disables_redirects() -> None:
    client = KalshiRestClient()
    response = MagicMock()
    response.status_code = 200
    response.text = '{"order": {"order_id": "order-123"}}'
    response.json.return_value = {"order": {"order_id": "order-123"}}
    client._session.request = MagicMock(return_value=response)  # noqa: SLF001

    result = client.place_limit_order(
        ticker="KXTEST-25DEC31",
        side="yes",
        count=2,
        limit_price=50,
    )

    assert result.order_id == "order-123"
    request_kwargs = client._session.request.call_args.kwargs  # noqa: SLF001
    assert request_kwargs["allow_redirects"] is False


def test_legacy_order_redirect_is_sanitized_error(caplog) -> None:
    client = KalshiRestClient()
    response = MagicMock()
    response.status_code = 307
    response.text = '{"Authorization":"redirect-secret"}'
    response.json.return_value = {"order": {"order_id": "wrong-order"}}
    client._session.request = MagicMock(return_value=response)  # noqa: SLF001

    with caplog.at_level(logging.ERROR, logger="kalshi_rest"):
        result = client.place_limit_order(
            ticker="KXTEST-25DEC31",
            side="yes",
            count=2,
            limit_price=50,
        )

    assert result.order_id == ""
    assert result.error == "unexpected redirect response"
    response.json.assert_not_called()
    assert "redirect-secret" not in caplog.text
    request_kwargs = client._session.request.call_args.kwargs  # noqa: SLF001
    assert request_kwargs["allow_redirects"] is False


def test_transient_request_exception_logs_warning_context(caplog) -> None:
    client = KalshiRestClient()
    client._session = _FailingSession(  # noqa: SLF001
        requests.ConnectionError(
            "HTTPSConnectionPool(host='api.elections.kalshi.com', port=443): "
            "Max retries exceeded with url: /trade-api/v2/series/KXGDP "
            "(Caused by ResponseError('too many 503 error responses'))"
        )
    )

    with caplog.at_level(logging.WARNING, logger="kalshi_rest"):
        with pytest.raises(requests.ConnectionError):
            client._request("GET", "/series/KXGDP")  # noqa: SLF001

    record = _only_record(caplog)
    assert record.levelno == logging.WARNING
    assert "transient=true" in record.getMessage()
    assert "retry_exhausted" not in record.getMessage()
    assert "method=GET" in record.getMessage()
    assert "endpoint=/series/KXGDP" in record.getMessage()
    assert "status=503" in record.getMessage()
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.parametrize("status", [500, 503])
def test_transient_http_failure_logs_warning_without_claiming_retry_exhaustion(
    caplog,
    status: int,
) -> None:
    client = KalshiRestClient()
    client._session = _FailingSession(  # noqa: SLF001
        _http_error(status, b"service unavailable")
    )

    with caplog.at_level(logging.WARNING, logger="kalshi_rest"):
        with pytest.raises(requests.HTTPError):
            client._request("GET", "/series/KXGDP")  # noqa: SLF001

    record = _only_record(caplog)
    assert record.levelno == logging.WARNING
    assert "transient=true" in record.getMessage()
    assert "retry_exhausted" not in record.getMessage()
    assert "method=GET" in record.getMessage()
    assert "endpoint=/series/KXGDP" in record.getMessage()
    assert f"status={status}" in record.getMessage()
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_http_400_still_logs_error_with_request_context(caplog) -> None:
    client = KalshiRestClient()
    client._session = _FailingSession(_http_error(400, b"bad request"))  # noqa: SLF001

    with caplog.at_level(logging.ERROR, logger="kalshi_rest"):
        with pytest.raises(requests.HTTPError):
            client._request("POST", "/series/BAD")  # noqa: SLF001

    record = _only_record(caplog)
    assert record.levelno == logging.ERROR
    assert "method=POST" in record.getMessage()
    assert "endpoint=/series/BAD" in record.getMessage()
    assert "status=400" in record.getMessage()
    assert "transient=false" in record.getMessage()
    assert "retry_exhausted" not in record.getMessage()


def test_transient_post_is_not_retried_but_cools_down_shared_gate() -> None:
    client = KalshiRestClient()
    gate = MagicMock()
    client._request_gate = gate  # noqa: SLF001
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = "3"
    client._session = MagicMock()  # noqa: SLF001
    client._session.request.side_effect = requests.HTTPError(response=response)  # noqa: SLF001

    with pytest.raises(requests.HTTPError):
        client._request("POST", "/series/BAD")  # noqa: SLF001

    assert client._session.request.call_count == 1  # noqa: SLF001
    gate.defer_for.assert_called_once_with(3.0)


def test_transient_get_retries_through_the_shared_gate() -> None:
    client = KalshiRestClient()
    gate = MagicMock()
    client._request_gate = gate  # noqa: SLF001
    retry_response = requests.Response()
    retry_response.status_code = 429
    retry_response.headers["Retry-After"] = "3"
    success = MagicMock()
    success.status_code = 200
    success.text = "{}"
    success.json.return_value = {}
    client._session = MagicMock()  # noqa: SLF001
    client._session.request.side_effect = [
        requests.HTTPError(response=retry_response),
        success,
    ]

    assert client._request("GET", "/markets") == {}  # noqa: SLF001

    assert client._session.request.call_count == 2  # noqa: SLF001
    assert gate.wait_for_slot.call_count == 2
    gate.defer_for.assert_called_once_with(3.0)


def test_retry_after_413_keeps_adapter_compatible_get_retry_behavior() -> None:
    client = KalshiRestClient()
    gate = MagicMock()
    client._request_gate = gate  # noqa: SLF001
    retry_response = requests.Response()
    retry_response.status_code = 413
    retry_response.headers["Retry-After"] = "3"
    success = MagicMock()
    success.status_code = 200
    success.text = "{}"
    success.json.return_value = {}
    client._session = MagicMock()  # noqa: SLF001
    client._session.request.side_effect = [
        requests.HTTPError(response=retry_response),
        success,
    ]

    assert client._request("GET", "/markets") == {}  # noqa: SLF001

    assert client._session.request.call_count == 2  # noqa: SLF001
    gate.defer_for.assert_called_once_with(3.0)


def test_headers_are_built_after_the_shared_dispatch_gate_releases() -> None:
    order: list[str] = []

    class Gate:
        def wait_for_slot(self) -> float:
            order.append("gate")
            return 0.0

        def defer_for(self, _delay: float) -> None:
            return None

    client = KalshiRestClient()
    client._request_gate = Gate()  # noqa: SLF001
    client._headers = MagicMock(side_effect=lambda *_args: order.append("headers") or {})  # noqa: SLF001
    response = MagicMock()
    response.status_code = 200
    response.text = "{}"
    response.json.return_value = {}
    client._session = MagicMock()  # noqa: SLF001
    client._session.request.return_value = response

    assert client._request("GET", "/markets") == {}  # noqa: SLF001

    assert order == ["gate", "headers"]


def test_transient_http_warning_redacts_sensitive_response_body(caplog) -> None:
    client = KalshiRestClient()
    client._session = _FailingSession(  # noqa: SLF001
        _http_error(503, b'{"Authorization":"top-secret"}')
    )

    with caplog.at_level(logging.WARNING, logger="kalshi_rest"):
        with pytest.raises(requests.HTTPError):
            client._request("GET", "/series/KXGDP")  # noqa: SLF001

    message = _only_record(caplog).getMessage()
    assert "response body redacted" in message
    assert "top-secret" not in message


def test_non_transient_request_exception_is_not_transient() -> None:
    assert _is_transient_request_exception(requests.ConnectionError("bad certificate")) is False


@pytest.mark.parametrize(
    "message",
    [
        "NewConnectionError: [Errno 111] Connection refused",
        "Name or service not known",
    ],
)
def test_connection_failures_retain_the_legacy_retry_eligibility(message: str) -> None:
    assert _is_transient_request_exception(requests.ConnectionError(message)) is True


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (
            requests.ConnectionError(
                "Max retries exceeded with url: /trade-api/v2/markets "
                "(Caused by ResponseError('too many 429 error responses'))"
            ),
            "429",
        ),
        (requests.ReadTimeout("HTTPSConnectionPool: Read timed out."), "unknown"),
        (requests.ConnectionError("Connection reset by peer"), "unknown"),
    ],
)
def test_known_transient_request_failures_log_warning(
    caplog,
    exc: requests.RequestException,
    expected_status: str,
) -> None:
    client = KalshiRestClient()
    client._session = _FailingSession(exc)  # noqa: SLF001

    with caplog.at_level(logging.WARNING, logger="kalshi_rest"):
        with pytest.raises(type(exc)):
            client._request("GET", "/markets")  # noqa: SLF001

    record = _only_record(caplog)
    assert record.levelno == logging.WARNING
    assert "method=GET" in record.getMessage()
    assert "endpoint=/markets" in record.getMessage()
    assert f"status={expected_status}" in record.getMessage()
    assert "transient=true" in record.getMessage()
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.parametrize(
    "exc",
    [
        requests.ConnectTimeout("opaque transport timeout"),
        requests.ReadTimeout("opaque transport timeout"),
        requests.Timeout("opaque transport timeout"),
    ],
)
def test_typed_timeout_logs_transient_warning(
    caplog,
    exc: requests.Timeout,
) -> None:
    client = KalshiRestClient()
    client._session = _FailingSession(exc)  # noqa: SLF001

    with caplog.at_level(logging.WARNING, logger="kalshi_rest"):
        with pytest.raises(type(exc)):
            client._request("GET", "/markets")  # noqa: SLF001

    record = _only_record(caplog)
    assert record.levelno == logging.WARNING
    assert "method=GET" in record.getMessage()
    assert "endpoint=/markets" in record.getMessage()
    assert "status=unknown" in record.getMessage()
    assert "transient=true" in record.getMessage()
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_wrapped_certificate_max_retry_failure_logs_error(caplog) -> None:
    exc = requests.exceptions.SSLError(
        "HTTPSConnectionPool(host='api.elections.kalshi.com', port=443): "
        "Max retries exceeded with url: /trade-api/v2/markets "
        "(Caused by SSLError(SSLCertVerificationError(1, "
        "'[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed')))"
    )
    client = KalshiRestClient()
    client._session = _FailingSession(exc)  # noqa: SLF001

    with caplog.at_level(logging.WARNING, logger="kalshi_rest"):
        with pytest.raises(requests.exceptions.SSLError):
            client._request("GET", "/markets")  # noqa: SLF001

    record = _only_record(caplog)
    assert record.levelno == logging.ERROR
    assert "method=GET" in record.getMessage()
    assert "endpoint=/markets" in record.getMessage()
    assert "status=unknown" in record.getMessage()
    assert "transient=false" in record.getMessage()
