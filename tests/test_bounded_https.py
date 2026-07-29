from __future__ import annotations

import asyncio
import socket
import ssl
import threading
import time
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable

import aiohttp
import pytest

from analysis import research_gate
from utils import bounded_https
from utils.bounded_https import fetch_bounded_https_ipv4


_URL = "https://example.com/data"
_HOST = "example.com"
_PROVIDER = "Example Provider"
_USER_AGENT = "kalshi-bot-test/1.0"


@dataclass(frozen=True)
class _Address:
    address: object


class _Resolver:
    def __init__(
        self,
        addresses: tuple[object, ...] = ("8.8.8.8",),
        *,
        delay: float = 0.0,
        blocker: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
        finalized: asyncio.Event | None = None,
    ) -> None:
        self.addresses = addresses
        self.delay = delay
        self.blocker = blocker
        self.started = started
        self.finalized = finalized
        self.calls: list[dict[str, Any]] = []

    async def resolve(
        self,
        host: str,
        rdtype: str,
        *,
        lifetime: float,
        search: bool,
    ) -> list[_Address]:
        self.calls.append(
            {
                "host": host,
                "rdtype": rdtype,
                "lifetime": lifetime,
                "search": search,
            }
        )
        if self.started is not None:
            self.started.set()
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.blocker is not None:
                await self.blocker.wait()
            return [_Address(item) for item in self.addresses]
        finally:
            if self.finalized is not None:
                self.finalized.set()


class _Content:
    def __init__(
        self,
        body: bytes = b"bounded body",
        *,
        blocker: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
        finalized: asyncio.Event | None = None,
    ) -> None:
        self.body = body
        self.blocker = blocker
        self.started = started
        self.finalized = finalized
        self.requested_bytes: list[int] = []

    async def readexactly(self, count: int) -> bytes:
        self.requested_bytes.append(count)
        if self.started is not None:
            self.started.set()
        try:
            if self.blocker is not None:
                await self.blocker.wait()
            if len(self.body) < count:
                raise asyncio.IncompleteReadError(partial=self.body, expected=count)
            return self.body[:count]
        finally:
            if self.finalized is not None:
                self.finalized.set()


class _Response:
    def __init__(
        self,
        body: bytes = b"bounded body",
        *,
        status: int = 200,
        reason: str = "OK",
        content: _Content | None = None,
    ) -> None:
        self.status = status
        self.reason = reason
        self.headers = {"X-Test": "bounded"}
        self.content = content or _Content(body)
        self.closed = False


class _RequestContext:
    def __init__(
        self,
        response: _Response,
        *,
        enter_delay: float = 0.0,
        enter_error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.enter_delay = enter_delay
        self.enter_error = enter_error

    async def __aenter__(self) -> _Response:
        if self.enter_delay:
            await asyncio.sleep(self.enter_delay)
        if self.enter_error is not None:
            raise self.enter_error
        return self.response

    async def __aexit__(self, *_args: object) -> None:
        self.response.closed = True


class _Connector:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(
        self,
        *,
        connector: _Connector,
        timeout: aiohttp.ClientTimeout,
        headers: dict[str, str],
        request_context: _RequestContext,
    ) -> None:
        self.connector = connector
        self.timeout = timeout
        self.headers = headers
        self.request_context = request_context
        self.closed = False
        self.active = False
        self.get_calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _Session:
        self.active = True
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.active = False
        self.closed = True
        await self.connector.close()

    def get(self, url: str, **kwargs: Any) -> _RequestContext:
        self.get_calls.append({"url": url, **kwargs})
        return self.request_context


class _Admission:
    def __init__(
        self,
        *,
        delay: float = 0.0,
        started: asyncio.Event | None = None,
        finalized: asyncio.Event | None = None,
    ) -> None:
        self.delay = delay
        self.started = started
        self.finalized = finalized
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> None:
        if self.started is not None:
            self.started.set()
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.entered = True
        finally:
            if self.finalized is not None:
                self.finalized.set()

    async def __aexit__(self, *_args: object) -> None:
        self.exited = True


def _transport_factories(
    response: _Response | None = None,
    *,
    enter_delay: float = 0.0,
    enter_error: BaseException | None = None,
) -> tuple[list[_Connector], list[_Session], Any, Any]:
    connectors: list[_Connector] = []
    sessions: list[_Session] = []
    response = response or _Response()

    def connector_factory(**kwargs: Any) -> _Connector:
        connector = _Connector(**kwargs)
        connectors.append(connector)
        return connector

    def session_factory(**kwargs: Any) -> _Session:
        session = _Session(
            **kwargs,
            request_context=_RequestContext(
                response,
                enter_delay=enter_delay,
                enter_error=enter_error,
            ),
        )
        sessions.append(session)
        return session

    return connectors, sessions, connector_factory, session_factory


def _assert_resources_closed(
    connectors: list[_Connector], sessions: list[_Session]
) -> None:
    assert len(connectors) == 1
    assert len(sessions) == 1
    assert connectors[0].closed
    assert sessions[0].closed
    assert not sessions[0].active


def _telemetry_event(
    provider_name: str,
) -> bounded_https.BoundedHTTPSAttemptTelemetry:
    return bounded_https.BoundedHTTPSAttemptTelemetry(
        provider_name=provider_name,
        outcome="success",
        terminal_stage="complete",
        budget_ms=500,
        total_ms=10,
        admission_wait_ms=0,
        dns_ms=1,
        response_headers_ms=1,
        body_read_ms=1,
        http_status=200,
        bytes_read=2,
        error_class=None,
    )


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("timed out waiting for deferred telemetry")
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_no_admission_factory_uses_noop_context() -> None:
    response = _Response(body=b"ok")
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )
    telemetry: list[Any] = []

    raw = await fetch_bounded_https_ipv4(
        _URL,
        canonical_host=_HOST,
        provider_name=_PROVIDER,
        user_agent=_USER_AGENT,
        timeout=0.5,
        max_bytes=10,
        resolver_factory=_Resolver,
        connector_factory=connector_factory,
        session_factory=session_factory,
        telemetry_sink=telemetry.append,
    )

    assert raw == b"ok"
    assert response.closed
    await _wait_until(lambda: len(telemetry) == 1)
    assert len(telemetry) == 1
    event = telemetry[0]
    assert event.provider_name == _PROVIDER
    assert event.outcome == "success"
    assert event.terminal_stage == "complete"
    assert event.budget_ms == 500
    assert event.total_ms >= 0
    assert event.admission_wait_ms >= 0
    assert event.dns_ms is not None
    assert event.response_headers_ms is not None
    assert event.body_read_ms is not None
    assert event.http_status == 200
    assert event.bytes_read == 2
    assert event.error_class is None
    assert _URL not in repr(event)
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
async def test_private_headers_are_merged_without_overriding_user_agent() -> None:
    response = _Response(body=b"{}")
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )

    raw = await fetch_bounded_https_ipv4(
        "https://api.search.brave.com/res/v1/web/search?q=contract",
        canonical_host="api.search.brave.com",
        provider_name="Brave Search API Shadow",
        user_agent="kalshi-bot-brave-shadow/1.0",
        timeout=1.0,
        max_bytes=100,
        request_headers={
            "Accept": "application/json",
            "X-Subscription-Token": "test-secret",
        },
        resolver_factory=_Resolver,
        connector_factory=connector_factory,
        session_factory=session_factory,
    )

    assert raw == b"{}"
    assert sessions[0].headers == {
        "User-Agent": "kalshi-bot-brave-shadow/1.0",
        "Accept": "application/json",
        "X-Subscription-Token": "test-secret",
    }
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    (
        {"Host": "other.example"},
        {" hOsT ": "other.example"},
        {"User-Agent": "other-agent"},
        {" User-Agent ": "other-agent"},
        {"X\rTest": "bad-header"},
        {"X\nTest": "bad-header"},
        {"X\x00Test": "bad-header"},
        {"X-Test": "bad\r\nheader"},
        {"X-Test": "bad\nheader"},
        {"X-Test": "bad\x00header"},
    ),
)
async def test_private_headers_cannot_override_routing_or_inject_lines(
    headers: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="request headers"):
        await fetch_bounded_https_ipv4(
            "https://api.search.brave.com/res/v1/web/search?q=contract",
            canonical_host="api.search.brave.com",
            provider_name="Brave Search API Shadow",
            user_agent="kalshi-bot-brave-shadow/1.0",
            timeout=1.0,
            max_bytes=100,
            request_headers=headers,
        )


@pytest.mark.asyncio
async def test_admission_wait_consumes_total_deadline() -> None:
    started = asyncio.Event()
    finalized = asyncio.Event()
    admission = _Admission(delay=0.1, started=started, finalized=finalized)
    resolver = _Resolver()
    telemetry: list[Any] = []
    before = time.monotonic()

    with pytest.raises(TimeoutError):
        await fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=0.02,
            max_bytes=10,
            admission_factory=lambda: admission,
            resolver_factory=lambda: resolver,
            telemetry_sink=telemetry.append,
        )

    assert time.monotonic() - before < 0.15
    assert started.is_set()
    assert finalized.is_set()
    assert not admission.entered
    assert resolver.calls == []
    await _wait_until(lambda: len(telemetry) == 1)
    assert len(telemetry) == 1
    event = telemetry[0]
    assert event.outcome == "timeout"
    assert event.terminal_stage == "admission"
    assert event.admission_wait_ms > 0
    assert event.dns_ms is None
    assert event.response_headers_ms is None
    assert event.body_read_ms is None
    assert event.error_class == "TimeoutError"


@pytest.mark.asyncio
async def test_dns_connect_and_read_share_one_deadline() -> None:
    read_started = asyncio.Event()
    read_finalized = asyncio.Event()
    response = _Response(
        content=_Content(
            blocker=asyncio.Event(),
            started=read_started,
            finalized=read_finalized,
        )
    )
    resolver = _Resolver(delay=0.025)
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response,
        enter_delay=0.025,
    )
    telemetry: list[Any] = []
    before = time.monotonic()

    with pytest.raises(TimeoutError):
        await fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=0.08,
            max_bytes=10,
            resolver_factory=lambda: resolver,
            connector_factory=connector_factory,
            session_factory=session_factory,
            telemetry_sink=telemetry.append,
        )

    assert time.monotonic() - before < 0.2
    assert read_started.is_set()
    assert read_finalized.is_set()
    assert response.closed
    assert sessions[0].timeout.total is not None
    assert 0 < sessions[0].timeout.total < 0.08
    assert sessions[0].timeout.total == sessions[0].timeout.connect
    assert sessions[0].timeout.total == sessions[0].timeout.sock_connect
    assert sessions[0].timeout.total == sessions[0].timeout.sock_read
    await _wait_until(lambda: len(telemetry) == 1)
    assert len(telemetry) == 1
    event = telemetry[0]
    assert event.outcome == "timeout"
    assert event.terminal_stage == "body_read"
    assert event.dns_ms is not None
    assert event.response_headers_ms is not None
    assert event.body_read_ms is not None
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
async def test_transport_telemetry_labels_dns_timeout() -> None:
    resolver = _Resolver(delay=0.1)
    telemetry: list[Any] = []

    with pytest.raises(TimeoutError):
        await fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=0.02,
            max_bytes=10,
            resolver_factory=lambda: resolver,
            telemetry_sink=telemetry.append,
        )

    await _wait_until(lambda: len(telemetry) == 1)
    assert len(telemetry) == 1
    event = telemetry[0]
    assert event.outcome == "timeout"
    assert event.terminal_stage == "dns"
    assert event.admission_wait_ms >= 0
    assert event.dns_ms is not None
    assert event.response_headers_ms is None
    assert event.body_read_ms is None


@pytest.mark.asyncio
async def test_transport_telemetry_sink_failure_does_not_change_result() -> None:
    response = _Response(body=b"ok")
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )

    sink_called = threading.Event()

    def failing_sink(_event: object) -> None:
        sink_called.set()
        raise RuntimeError("private telemetry failure")

    raw = await fetch_bounded_https_ipv4(
        _URL,
        canonical_host=_HOST,
        provider_name=_PROVIDER,
        user_agent=_USER_AGENT,
        timeout=0.5,
        max_bytes=10,
        resolver_factory=_Resolver,
        connector_factory=connector_factory,
        session_factory=session_factory,
        telemetry_sink=failing_sink,
    )

    assert raw == b"ok"
    await _wait_until(sink_called.is_set)
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
async def test_transport_telemetry_sink_cancel_does_not_change_result() -> None:
    response = _Response(body=b"ok")
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )

    sink_called = threading.Event()

    def cancelling_sink(_event: object) -> None:
        sink_called.set()
        raise asyncio.CancelledError("telemetry-only cancellation")

    raw = await fetch_bounded_https_ipv4(
        _URL,
        canonical_host=_HOST,
        provider_name=_PROVIDER,
        user_agent=_USER_AGENT,
        timeout=0.5,
        max_bytes=10,
        resolver_factory=_Resolver,
        connector_factory=connector_factory,
        session_factory=session_factory,
        telemetry_sink=cancelling_sink,
    )

    assert raw == b"ok"
    await _wait_until(sink_called.is_set)
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
async def test_telemetry_dispatcher_drops_on_bounded_overflow() -> None:
    dispatcher = bounded_https._TelemetryDispatcher(capacity=1)
    loop = asyncio.get_running_loop()
    release_first = threading.Event()
    first_started = asyncio.Event()
    first_finished = asyncio.Event()
    second_delivered = asyncio.Event()
    delivered: list[str] = []

    def first_sink(event: bounded_https.BoundedHTTPSAttemptTelemetry) -> None:
        loop.call_soon_threadsafe(first_started.set)
        release_first.wait()
        delivered.append(event.provider_name)
        loop.call_soon_threadsafe(first_finished.set)

    def second_sink(event: bounded_https.BoundedHTTPSAttemptTelemetry) -> None:
        delivered.append(event.provider_name)
        loop.call_soon_threadsafe(second_delivered.set)

    first = _telemetry_event("first")
    second = _telemetry_event("second")
    dropped = _telemetry_event("dropped")

    try:
        assert dispatcher.submit(first_sink, first)
        await first_started.wait()
        assert dispatcher.submit(second_sink, second)
        assert not dispatcher.submit(second_sink, dropped)

        stats = dispatcher.snapshot()
        assert stats.accepted == 2
        assert stats.dropped == 1
        assert stats.worker_alive
        assert stats.worker_daemon
        assert not second_delivered.is_set()
    finally:
        release_first.set()

    await first_finished.wait()
    await second_delivered.wait()
    assert delivered == ["first", "second"]


@pytest.mark.asyncio
async def test_telemetry_dispatcher_survives_sink_base_exception() -> None:
    dispatcher = bounded_https._TelemetryDispatcher(capacity=2)
    loop = asyncio.get_running_loop()
    delivered = asyncio.Event()
    delivered_provider_names: list[str] = []

    class SinkFailure(BaseException):
        pass

    def failing_sink(_event: bounded_https.BoundedHTTPSAttemptTelemetry) -> None:
        raise SinkFailure()

    def healthy_sink(event: bounded_https.BoundedHTTPSAttemptTelemetry) -> None:
        delivered_provider_names.append(event.provider_name)
        loop.call_soon_threadsafe(delivered.set)

    assert dispatcher.submit(failing_sink, _telemetry_event("failing"))
    assert dispatcher.submit(healthy_sink, _telemetry_event("healthy"))

    await delivered.wait()
    assert delivered_provider_names == ["healthy"]
    stats = dispatcher.snapshot()
    assert stats.accepted == 2
    assert stats.dropped == 0


@pytest.mark.asyncio
async def test_transport_telemetry_avoids_default_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _Response(body=b"ok")
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )
    dispatcher = bounded_https._TelemetryDispatcher(capacity=1)
    monkeypatch.setattr(bounded_https, "_TELEMETRY_DISPATCHER", dispatcher)
    loop = asyncio.get_running_loop()
    delivered = asyncio.Event()
    telemetry: list[bounded_https.BoundedHTTPSAttemptTelemetry] = []

    def sink(event: bounded_https.BoundedHTTPSAttemptTelemetry) -> None:
        telemetry.append(event)
        loop.call_soon_threadsafe(delivered.set)

    def fail_default_executor(*_args: object, **_kwargs: object) -> None:
        pytest.fail("telemetry must not use the default executor")

    monkeypatch.setattr(loop, "run_in_executor", fail_default_executor)

    raw = await fetch_bounded_https_ipv4(
        _URL,
        canonical_host=_HOST,
        provider_name=_PROVIDER,
        user_agent=_USER_AGENT,
        timeout=0.5,
        max_bytes=10,
        resolver_factory=_Resolver,
        connector_factory=connector_factory,
        session_factory=session_factory,
        telemetry_sink=sink,
    )

    assert raw == b"ok"
    await delivered.wait()
    assert [event.outcome for event in telemetry] == ["success"]
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
async def test_transport_telemetry_blocking_sink_does_not_delay_gather(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(body=b"ok")
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )
    dispatcher = bounded_https._TelemetryDispatcher(capacity=1)
    monkeypatch.setattr(bounded_https, "_TELEMETRY_DISPATCHER", dispatcher)
    loop = asyncio.get_running_loop()
    loop_thread_id = threading.get_ident()
    release_sink = threading.Event()
    sink_started = asyncio.Event()
    sink_finished = asyncio.Event()
    sink_ran_on_loop = asyncio.Event()
    telemetry: list[bounded_https.BoundedHTTPSAttemptTelemetry] = []

    def slow_sink(event: bounded_https.BoundedHTTPSAttemptTelemetry) -> None:
        if threading.get_ident() == loop_thread_id:
            sink_ran_on_loop.set()
            return
        telemetry.append(event)
        loop.call_soon_threadsafe(sink_started.set)
        release_sink.wait()
        loop.call_soon_threadsafe(sink_finished.set)

    try:
        (raw,) = await asyncio.gather(
            fetch_bounded_https_ipv4(
                _URL,
                canonical_host=_HOST,
                provider_name=_PROVIDER,
                user_agent=_USER_AGENT,
                timeout=0.5,
                max_bytes=10,
                resolver_factory=_Resolver,
                connector_factory=connector_factory,
                session_factory=session_factory,
                telemetry_sink=slow_sink,
            )
        )

        assert raw == b"ok"
        assert not sink_ran_on_loop.is_set()
        await sink_started.wait()
        assert not sink_finished.is_set()
        assert [event.outcome for event in telemetry] == ["success"]
        _assert_resources_closed(connectors, sessions)
    finally:
        release_sink.set()

    await sink_finished.wait()


@pytest.mark.asyncio
async def test_transport_telemetry_blocking_sink_does_not_delay_body_read_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body_read_started = asyncio.Event()
    body_read_finalized = asyncio.Event()
    response = _Response(
        content=_Content(
            blocker=asyncio.Event(),
            started=body_read_started,
            finalized=body_read_finalized,
        )
    )
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )
    dispatcher = bounded_https._TelemetryDispatcher(capacity=1)
    monkeypatch.setattr(bounded_https, "_TELEMETRY_DISPATCHER", dispatcher)
    loop = asyncio.get_running_loop()
    loop_thread_id = threading.get_ident()
    release_sink = threading.Event()
    sink_started = asyncio.Event()
    sink_finished = asyncio.Event()
    sink_ran_on_loop = asyncio.Event()
    telemetry: list[bounded_https.BoundedHTTPSAttemptTelemetry] = []

    def slow_sink(event: bounded_https.BoundedHTTPSAttemptTelemetry) -> None:
        if threading.get_ident() == loop_thread_id:
            sink_ran_on_loop.set()
            return
        telemetry.append(event)
        loop.call_soon_threadsafe(sink_started.set)
        release_sink.wait()
        loop.call_soon_threadsafe(sink_finished.set)

    gathered = asyncio.gather(
        fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=0.5,
            max_bytes=10,
            resolver_factory=_Resolver,
            connector_factory=connector_factory,
            session_factory=session_factory,
            telemetry_sink=slow_sink,
        )
    )
    try:
        await body_read_started.wait()
        gathered.cancel()
        with pytest.raises(asyncio.CancelledError):
            await gathered

        assert body_read_finalized.is_set()
        assert response.closed
        assert not sink_ran_on_loop.is_set()
        await sink_started.wait()
        assert not sink_finished.is_set()
        assert [event.outcome for event in telemetry] == ["cancelled"]
        _assert_resources_closed(connectors, sessions)
    finally:
        release_sink.set()

    await sink_finished.wait()


@pytest.mark.asyncio
async def test_non_2xx_preserves_urllib_http_error() -> None:
    response = _Response(status=404, reason="Not Found")
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )

    with pytest.raises(urllib.error.HTTPError) as raised:
        await fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=0.5,
            max_bytes=10,
            resolver_factory=_Resolver,
            connector_factory=connector_factory,
            session_factory=session_factory,
        )

    assert raised.value.code == 404
    assert raised.value.url == _URL
    assert raised.value.headers == response.headers
    assert response.closed
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 202, 299])
async def test_every_2xx_is_accepted(status: int) -> None:
    response = _Response(body=b"ok", status=status)
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )

    raw = await fetch_bounded_https_ipv4(
        _URL,
        canonical_host=_HOST,
        provider_name=_PROVIDER,
        user_agent=_USER_AGENT,
        timeout=0.5,
        max_bytes=10,
        resolver_factory=_Resolver,
        connector_factory=connector_factory,
        session_factory=session_factory,
    )

    assert raw == b"ok"
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
async def test_redirect_is_not_followed() -> None:
    response = _Response(status=302, reason="Found")
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )

    with pytest.raises(urllib.error.HTTPError) as raised:
        await fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=0.5,
            max_bytes=10,
            resolver_factory=_Resolver,
            connector_factory=connector_factory,
            session_factory=session_factory,
        )

    assert raised.value.code == 302
    assert sessions[0].get_calls == [{"url": _URL, "allow_redirects": False}]
    assert response.closed
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
async def test_response_larger_than_cap_is_rejected() -> None:
    response = _Response(body=b"x" * 11)
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )

    with pytest.raises(ValueError, match="10-byte limit"):
        await fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=0.5,
            max_bytes=10,
            resolver_factory=_Resolver,
            connector_factory=connector_factory,
            session_factory=session_factory,
        )

    assert response.content.requested_bytes == [11]
    assert response.closed
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
async def test_cancellation_closes_resources_and_propagates() -> None:
    read_started = asyncio.Event()
    read_finalized = asyncio.Event()
    response = _Response(
        content=_Content(
            blocker=asyncio.Event(),
            started=read_started,
            finalized=read_finalized,
        )
    )
    admission = _Admission()
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        response
    )
    task = asyncio.create_task(
        fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=1.0,
            max_bytes=10,
            admission_factory=lambda: admission,
            resolver_factory=_Resolver,
            connector_factory=connector_factory,
            session_factory=session_factory,
        )
    )
    await asyncio.wait_for(read_started.wait(), timeout=0.2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.done()
    assert read_finalized.is_set()
    assert response.closed
    assert admission.exited
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("addresses", "error_type"),
    [
        (("10.0.0.1",), ValueError),
        (("2001:4860:4860::8888",), ValueError),
        ((None,), ValueError),
        ((), socket.gaierror),
    ],
)
async def test_dns_answers_must_be_global_ipv4(
    addresses: tuple[object, ...], error_type: type[BaseException]
) -> None:
    connector_calls: list[dict[str, Any]] = []

    with pytest.raises(error_type):
        await fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=0.5,
            max_bytes=10,
            resolver_factory=lambda: _Resolver(addresses),
            connector_factory=lambda **kwargs: connector_calls.append(kwargs),
        )

    assert connector_calls == []


@pytest.mark.asyncio
async def test_pinned_resolver_requires_canonical_host_port_and_family() -> None:
    connectors, sessions, connector_factory, session_factory = _transport_factories()

    await fetch_bounded_https_ipv4(
        _URL,
        canonical_host=_HOST,
        provider_name=_PROVIDER,
        user_agent=_USER_AGENT,
        timeout=0.5,
        max_bytes=20,
        resolver_factory=lambda: _Resolver(("8.8.8.8", "1.1.1.1")),
        connector_factory=connector_factory,
        session_factory=session_factory,
    )

    assert connectors[0].kwargs == {
        "resolver": connectors[0].kwargs["resolver"],
        "family": socket.AF_INET,
        "use_dns_cache": False,
        "force_close": True,
        "limit": 1,
        "limit_per_host": 1,
        "happy_eyeballs_delay": None,
    }
    pinned = connectors[0].kwargs["resolver"]
    answers = await pinned.resolve(_HOST, 443, socket.AF_INET)
    assert [item["host"] for item in answers] == ["8.8.8.8", "1.1.1.1"]
    for host, port, family in (
        ("attacker.example", 443, socket.AF_INET),
        (_HOST, 80, socket.AF_INET),
        (_HOST, 443, socket.AF_INET6),
    ):
        with pytest.raises(socket.gaierror, match="canonical host"):
            await pinned.resolve(host, port, family)
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        aiohttp.ClientConnectorCertificateError(
            None, ssl.CertificateError("certificate failed")
        ),
        aiohttp.ClientSSLError(None, OSError("TLS failed")),
    ],
)
async def test_certificate_and_tls_errors_are_not_reclassified(
    error: aiohttp.ClientConnectionError,
) -> None:
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        enter_error=error
    )

    with pytest.raises(type(error)) as raised:
        await fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=0.5,
            max_bytes=10,
            resolver_factory=_Resolver,
            connector_factory=connector_factory,
            session_factory=session_factory,
        )

    assert raised.value is error
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
async def test_other_client_connection_errors_map_to_connection_error() -> None:
    client_error = aiohttp.ClientConnectionError("socket failed")
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        enter_error=client_error
    )

    with pytest.raises(ConnectionError, match="Example Provider connection failed") as raised:
        await fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=0.5,
            max_bytes=10,
            resolver_factory=_Resolver,
            connector_factory=connector_factory,
            session_factory=session_factory,
        )

    assert raised.value.__cause__ is client_error
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
async def test_research_wrapper_keeps_shared_limiter_inside_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission_started = asyncio.Event()
    blocker = asyncio.Event()
    shared_calls: list[Any] = []

    class _BlockingAdmission:
        async def __aenter__(self) -> None:
            admission_started.set()
            await blocker.wait()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    class _BlockingLimiter:
        def slot(self) -> _BlockingAdmission:
            return _BlockingAdmission()

    async def shared_fetch(*args: Any, **kwargs: Any) -> bytes:
        shared_calls.append(kwargs["admission_factory"])
        return await fetch_bounded_https_ipv4(*args, **kwargs)

    monkeypatch.setattr(
        research_gate,
        "fetch_bounded_https_ipv4",
        shared_fetch,
        raising=False,
    )
    monkeypatch.setattr(
        research_gate,
        "_get_generic_web_search_work_limiter",
        lambda: _BlockingLimiter(),
    )

    before = time.monotonic()
    with pytest.raises(TimeoutError):
        await research_gate._fetch_bounded_https_ipv4(
            _URL,
            canonical_host=_HOST,
            provider_name=_PROVIDER,
            user_agent=_USER_AGENT,
            timeout=0.03,
            max_bytes=10,
            resolver_factory=_Resolver,
        )

    assert time.monotonic() - before < 0.15
    assert admission_started.is_set()
    assert len(shared_calls) == 1
