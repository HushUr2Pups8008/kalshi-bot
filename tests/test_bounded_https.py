from __future__ import annotations

import asyncio
import socket
import ssl
import time
import urllib.error
from dataclasses import dataclass
from typing import Any

import aiohttp
import pytest

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


@pytest.mark.asyncio
async def test_no_admission_factory_uses_noop_context() -> None:
    response = _Response(body=b"ok")
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
    assert response.closed
    _assert_resources_closed(connectors, sessions)


@pytest.mark.asyncio
async def test_admission_wait_consumes_total_deadline() -> None:
    started = asyncio.Event()
    finalized = asyncio.Event()
    admission = _Admission(delay=0.1, started=started, finalized=finalized)
    resolver = _Resolver()
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
        )

    assert time.monotonic() - before < 0.15
    assert started.is_set()
    assert finalized.is_set()
    assert not admission.entered
    assert resolver.calls == []


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
    _assert_resources_closed(connectors, sessions)


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
