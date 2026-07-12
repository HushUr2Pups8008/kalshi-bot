import asyncio
import errno
import json
import socket
import urllib.error
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from analysis.generic_search_circuit import (
    GenericSearchCircuit,
    GenericSearchCircuitEvent,
    GenericSearchUnavailable,
    is_provider_availability_failure,
)


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://provider.invalid/private-query",
        code=status,
        msg="provider payload",
        hdrs=None,
        fp=None,
    )


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        pytest.param(TimeoutError("timed out"), True, id="timeout-error"),
        pytest.param(asyncio.TimeoutError("timed out"), True, id="asyncio-timeout"),
        pytest.param(socket.gaierror(-2, "dns failed"), True, id="dns-error"),
        pytest.param(ConnectionError("connection failed"), True, id="connection-error"),
        pytest.param(
            OSError(errno.ENETUNREACH, "network unreachable"),
            True,
            id="network-os-error",
        ),
        pytest.param(
            urllib.error.URLError(TimeoutError("timed out")),
            True,
            id="url-timeout",
        ),
        pytest.param(
            urllib.error.URLError(socket.gaierror(-2, "dns failed")),
            True,
            id="url-dns-error",
        ),
        pytest.param(
            urllib.error.URLError(OSError(errno.ECONNREFUSED, "refused")),
            True,
            id="url-connection-os-error",
        ),
        pytest.param(_http_error(429), True, id="http-429"),
        pytest.param(_http_error(500), True, id="http-500"),
        pytest.param(_http_error(503), True, id="http-503"),
        pytest.param(_http_error(400), False, id="http-400"),
        pytest.param(_http_error(401), False, id="http-401"),
        pytest.param(_http_error(403), False, id="http-403"),
        pytest.param(_http_error(404), False, id="http-404"),
        pytest.param(urllib.error.URLError("timed out"), False, id="url-string-reason"),
        pytest.param(
            urllib.error.URLError(ValueError("bad value")),
            False,
            id="url-programming-error",
        ),
        pytest.param(
            urllib.error.URLError(FileNotFoundError("missing file")),
            False,
            id="url-local-os-error",
        ),
        pytest.param(ValueError("bad value"), False, id="value-error"),
        pytest.param(AssertionError("bad assertion"), False, id="assertion-error"),
        pytest.param(ET.ParseError("malformed xml"), False, id="xml-parse-error"),
        pytest.param(
            json.JSONDecodeError("malformed json", "{", 1),
            False,
            id="json-parse-error",
        ),
    ],
)
def test_provider_availability_failure_taxonomy(
    exc: BaseException,
    expected: bool,
) -> None:
    assert is_provider_availability_failure(exc) is expected


def _raising_provider(
    label: str,
    calls: list[str],
    exc: BaseException,
) -> Callable[[], Awaitable[Any]]:
    async def provider() -> Any:
        calls.append(label)
        raise exc

    return provider


def _successful_provider(
    label: str,
    calls: list[str],
    result: Any,
) -> Callable[[], Awaitable[Any]]:
    async def provider() -> Any:
        calls.append(label)
        return result

    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
async def test_closed_modes_fall_back_after_every_primary_exception(mode: str) -> None:
    calls: list[str] = []
    fallback_result = object()
    circuit = GenericSearchCircuit(mode=mode)

    result = await circuit.run(
        _raising_provider("rss", calls, ValueError("malformed RSS")),
        _successful_provider("ddg", calls, fallback_result),
    )

    assert calls == ["rss", "ddg"]
    assert result is fallback_result
    assert circuit.snapshot().state == "closed"


@pytest.mark.asyncio
async def test_non_availability_primary_failure_does_not_mutate_circuit() -> None:
    calls: list[str] = []
    fallback_result = object()
    circuit = GenericSearchCircuit(mode="enforce")

    result = await circuit.run(
        _raising_provider("rss", calls, ET.ParseError("private query payload")),
        _successful_provider("ddg", calls, fallback_result),
    )

    snapshot = circuit.snapshot()
    assert calls == ["rss", "ddg"]
    assert result is fallback_result
    assert snapshot.state == "closed"
    assert snapshot.double_availability_failures == 0
    assert snapshot.open_transitions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "open_transitions", "would_open_transitions", "expected_state"),
    [
        pytest.param("off", 0, 0, "closed", id="off"),
        pytest.param("shadow", 0, 1, "open", id="shadow"),
        pytest.param("enforce", 1, 0, "open", id="enforce"),
    ],
)
async def test_double_availability_failure_is_sanitized(
    mode: str,
    open_transitions: int,
    would_open_transitions: int,
    expected_state: str,
) -> None:
    calls: list[str] = []
    events: list[GenericSearchCircuitEvent] = []
    circuit = GenericSearchCircuit(
        mode=mode,
        cooldown_seconds=30.0,
        clock=lambda: 100.0,
        event_sink=events.append,
    )
    primary_secret = "forecast query secret"
    fallback_secret = "https://provider.invalid/private?q=secret"
    primary_error = TimeoutError(primary_secret)
    fallback_error = urllib.error.HTTPError(
        url=fallback_secret,
        code=503,
        msg="private response payload",
        hdrs=None,
        fp=None,
    )

    with pytest.raises(GenericSearchUnavailable) as raised:
        await circuit.run(
            _raising_provider("rss", calls, primary_error),
            _raising_provider("ddg", calls, fallback_error),
        )

    snapshot = circuit.snapshot()
    diagnostic = str(raised.value)
    assert calls == ["rss", "ddg"]
    assert snapshot.state == expected_state
    assert snapshot.last_failure_classes == ("TimeoutError", "HTTPError:503")
    assert snapshot.double_availability_failures == 1
    assert snapshot.open_transitions == open_transitions
    assert snapshot.would_open_transitions == would_open_transitions
    assert "TimeoutError" in diagnostic
    assert "HTTPError:503" in diagnostic
    for secret in (primary_secret, fallback_secret, "private response payload"):
        assert secret not in diagnostic
        assert all(secret not in repr(event) for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary_error", "fallback_error"),
    [
        pytest.param(
            TimeoutError("rss unavailable"),
            ValueError("ddg parser failed"),
            id="eligible-then-ineligible",
        ),
        pytest.param(
            ValueError("rss parser failed"),
            TimeoutError("ddg unavailable"),
            id="ineligible-then-eligible",
        ),
        pytest.param(
            AssertionError("rss invariant"),
            ET.ParseError("ddg malformed response"),
            id="double-ineligible",
        ),
    ],
)
async def test_non_double_availability_failure_propagates_fallback_exception(
    primary_error: BaseException,
    fallback_error: BaseException,
) -> None:
    calls: list[str] = []
    circuit = GenericSearchCircuit(mode="enforce")

    with pytest.raises(type(fallback_error)) as raised:
        await circuit.run(
            _raising_provider("rss", calls, primary_error),
            _raising_provider("ddg", calls, fallback_error),
        )

    snapshot = circuit.snapshot()
    assert raised.value is fallback_error
    assert calls == ["rss", "ddg"]
    assert snapshot.state == "closed"
    assert snapshot.double_availability_failures == 0
    assert snapshot.open_transitions == 0
