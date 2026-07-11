from __future__ import annotations

import logging

import pytest
import requests

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
