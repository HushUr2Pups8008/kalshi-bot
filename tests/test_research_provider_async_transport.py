from __future__ import annotations

import asyncio
import socket
import time
import urllib.error
from dataclasses import dataclass
from typing import Any

import aiohttp
import pytest

from analysis import research_gate
from analysis.generic_search_circuit import is_provider_availability_failure


_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss><channel><item>
  <title>Federal Reserve update - Reuters</title>
  <link>https://news.google.com/rss/articles/fed-update</link>
  <source url="https://reuters.com">Reuters</source>
  <description>Federal Reserve policy update.</description>
  <pubDate>Wed, 15 Jul 2026 07:01:00 GMT</pubDate>
</item></channel></rss>
"""

_DUCK_HTML = b"""
<html><body>
  <a rel="nofollow" href="https://example.com/current-result">Current result</a>
  <td class="result-snippet">Current supporting evidence.</td>
</body></html>
"""


@dataclass(frozen=True)
class _Address:
    address: str


class _Resolver:
    def __init__(
        self,
        addresses: tuple[str, ...] = ("8.8.8.8",),
        *,
        delay: float = 0.0,
        blocker: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
        finalized: asyncio.Event | None = None,
        error: BaseException | None = None,
        counters: dict[str, int] | None = None,
    ) -> None:
        self.addresses = addresses
        self.delay = delay
        self.blocker = blocker
        self.started = started
        self.finalized = finalized
        self.error = error
        self.counters = counters
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
        if self.counters is not None:
            self.counters["active"] = self.counters.get("active", 0) + 1
            self.counters["peak"] = max(
                self.counters.get("peak", 0),
                self.counters["active"],
            )
        if self.started is not None:
            self.started.set()
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.blocker is not None:
                await self.blocker.wait()
            if self.error is not None:
                raise self.error
            return [_Address(item) for item in self.addresses]
        finally:
            if self.counters is not None:
                self.counters["active"] -= 1
            if self.finalized is not None:
                self.finalized.set()


class _Content:
    def __init__(
        self,
        body: bytes,
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
        body: bytes = _RSS,
        *,
        status: int = 200,
        reason: str = "OK",
        content: _Content | None = None,
    ) -> None:
        self.status = status
        self.reason = reason
        self.headers = {}
        self.content = content or _Content(body)
        self.closed = False


class _RequestContext:
    def __init__(
        self,
        response: _Response,
        *,
        enter_delay: float = 0.0,
        enter_blocker: asyncio.Event | None = None,
        enter_started: asyncio.Event | None = None,
    ) -> None:
        self.response = response
        self.enter_delay = enter_delay
        self.enter_blocker = enter_blocker
        self.enter_started = enter_started

    async def __aenter__(self) -> _Response:
        if self.enter_started is not None:
            self.enter_started.set()
        if self.enter_delay:
            await asyncio.sleep(self.enter_delay)
        if self.enter_blocker is not None:
            await self.enter_blocker.wait()
        return self.response

    async def __aexit__(self, *_args) -> None:
        self.response.closed = True


class _Connector:
    def __init__(self, **kwargs) -> None:
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
        self.get_calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args) -> None:
        self.closed = True
        await self.connector.close()

    def get(self, url: str, **kwargs) -> _RequestContext:
        self.get_calls.append({"url": url, **kwargs})
        return self.request_context


def _transport_factories(
    response: _Response | None = None,
    *,
    enter_delay: float = 0.0,
    enter_blocker: asyncio.Event | None = None,
    enter_started: asyncio.Event | None = None,
) -> tuple[list[_Connector], list[_Session], Any, Any]:
    connectors: list[_Connector] = []
    sessions: list[_Session] = []
    response = response or _Response()

    def connector_factory(**kwargs) -> _Connector:
        connector = _Connector(**kwargs)
        connectors.append(connector)
        return connector

    def session_factory(**kwargs) -> _Session:
        session = _Session(
            **kwargs,
            request_context=_RequestContext(
                response,
                enter_delay=enter_delay,
                enter_blocker=enter_blocker,
                enter_started=enter_started,
            ),
        )
        sessions.append(session)
        return session

    return connectors, sessions, connector_factory, session_factory


@pytest.fixture(autouse=True)
def _reset_work_limiters() -> None:
    research_gate._reset_generic_web_search_work_limiters_for_tests()


@pytest.mark.asyncio
async def test_slow_dns_is_cancelled_by_total_deadline_without_background_work() -> None:
    started = asyncio.Event()
    finalized = asyncio.Event()
    resolver = _Resolver(
        blocker=asyncio.Event(),
        started=started,
        finalized=finalized,
    )
    session_calls = []
    before = time.monotonic()

    with pytest.raises(TimeoutError):
        await research_gate._fetch_google_news_rss_ipv4(
            "https://news.google.com/rss/search?q=test",
            timeout=0.03,
            max_bytes=300_000,
            resolver_factory=lambda: resolver,
            session_factory=lambda **kwargs: session_calls.append(kwargs),
        )

    assert time.monotonic() - before < 0.15
    assert started.is_set()
    assert finalized.is_set()
    assert session_calls == []
    assert research_gate._generic_web_search_work_snapshot_for_tests().active == 0


@pytest.mark.asyncio
async def test_empty_async_resolver_is_recognized_provider_availability_failure() -> None:
    resolver = _Resolver(addresses=())

    with pytest.raises(socket.gaierror) as raised:
        await research_gate._fetch_google_news_rss_ipv4(
            "https://news.google.com/rss/search?q=test",
            timeout=0.2,
            max_bytes=300_000,
            resolver_factory=lambda: resolver,
        )

    assert is_provider_availability_failure(raised.value)
    assert research_gate._generic_web_search_work_snapshot_for_tests().active == 0


@pytest.mark.asyncio
async def test_multi_address_connect_tls_write_share_remaining_total_deadline() -> None:
    resolver = _Resolver(addresses=("8.8.8.8", "1.1.1.1"), delay=0.02)
    connectors, sessions, connector_factory, session_factory = _transport_factories(
        enter_delay=0.05,
    )
    before = time.monotonic()

    with pytest.raises(TimeoutError):
        await research_gate._fetch_google_news_rss_ipv4(
            "https://news.google.com/rss/search?q=test",
            timeout=0.05,
            max_bytes=300_000,
            resolver_factory=lambda: resolver,
            connector_factory=connector_factory,
            session_factory=session_factory,
        )

    assert time.monotonic() - before < 0.15
    assert len(connectors) == 1
    assert connectors[0].closed
    assert sessions[0].closed
    assert sessions[0].timeout.total is not None
    assert 0 < sessions[0].timeout.total < 0.05
    pinned = connectors[0].kwargs["resolver"]
    answers = await pinned.resolve("news.google.com", 443, socket.AF_INET)
    assert [item["host"] for item in answers] == ["8.8.8.8", "1.1.1.1"]
    assert research_gate._generic_web_search_work_snapshot_for_tests().active == 0


@pytest.mark.asyncio
async def test_slow_body_read_is_cancelled_and_closes_response_session_connector() -> None:
    read_started = asyncio.Event()
    read_finalized = asyncio.Event()
    content = _Content(
        b"",
        blocker=asyncio.Event(),
        started=read_started,
        finalized=read_finalized,
    )
    response = _Response(content=content)
    connectors, sessions, connector_factory, session_factory = _transport_factories(response)

    with pytest.raises(TimeoutError):
        await research_gate._fetch_google_news_rss_ipv4(
            "https://news.google.com/rss/search?q=test",
            timeout=0.03,
            max_bytes=300_000,
            resolver_factory=lambda: _Resolver(),
            connector_factory=connector_factory,
            session_factory=session_factory,
        )

    assert read_started.is_set()
    assert read_finalized.is_set()
    assert response.closed
    assert sessions[0].closed
    assert connectors[0].closed
    assert content.requested_bytes == [300_001]
    assert research_gate._generic_web_search_work_snapshot_for_tests().active == 0


@pytest.mark.asyncio
async def test_external_cancellation_closes_response_session_connector_without_task_leak() -> None:
    read_started = asyncio.Event()
    read_finalized = asyncio.Event()
    response = _Response(
        content=_Content(
            b"",
            blocker=asyncio.Event(),
            started=read_started,
            finalized=read_finalized,
        )
    )
    connectors, sessions, connector_factory, session_factory = _transport_factories(response)
    task = asyncio.create_task(
        research_gate._fetch_google_news_rss_ipv4(
            "https://news.google.com/rss/search?q=test",
            timeout=1.0,
            max_bytes=300_000,
            resolver_factory=lambda: _Resolver(),
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
    assert sessions[0].closed
    assert connectors[0].closed
    assert research_gate._generic_web_search_work_snapshot_for_tests().active == 0


@pytest.mark.asyncio
async def test_generic_web_search_work_is_bounded_and_waiters_cancel_cleanly() -> None:
    blocker = asyncio.Event()
    counters = {"active": 0, "peak": 0}

    def resolver_factory() -> _Resolver:
        return _Resolver(blocker=blocker, counters=counters)

    tasks = [
        asyncio.create_task(
            research_gate._fetch_google_news_rss_ipv4(
                "https://news.google.com/rss/search?q=test",
                timeout=1.0,
                max_bytes=300_000,
                resolver_factory=resolver_factory,
            )
        )
        for _ in range(8)
    ]
    for _ in range(50):
        if counters["active"] == research_gate._GENERIC_WEB_SEARCH_MAX_CONCURRENCY:
            break
        await asyncio.sleep(0.005)

    snapshot = research_gate._generic_web_search_work_snapshot_for_tests()
    assert counters["peak"] == research_gate._GENERIC_WEB_SEARCH_MAX_CONCURRENCY
    assert snapshot.active == research_gate._GENERIC_WEB_SEARCH_MAX_CONCURRENCY
    assert snapshot.peak == research_gate._GENERIC_WEB_SEARCH_MAX_CONCURRENCY

    for task in tasks:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(isinstance(item, asyncio.CancelledError) for item in results)
    assert all(task.done() for task in tasks)
    assert counters["active"] == 0
    assert research_gate._generic_web_search_work_snapshot_for_tests().active == 0


@pytest.mark.asyncio
async def test_google_and_duckduckgo_transport_emit_distinct_provider_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry: list[Any] = []
    monkeypatch.setattr(
        research_gate,
        "_log_generic_search_transport_event",
        telemetry.append,
    )

    google_response = _Response(body=_RSS)
    google_connectors, google_sessions, google_connector_factory, google_session_factory = (
        _transport_factories(google_response)
    )
    duck_response = _Response(body=_DUCK_HTML)
    duck_connectors, duck_sessions, duck_connector_factory, duck_session_factory = (
        _transport_factories(duck_response)
    )

    await research_gate._fetch_google_news_rss_ipv4(
        "https://news.google.com/rss/search?q=private-google-query",
        timeout=0.5,
        max_bytes=300_000,
        resolver_factory=_Resolver,
        connector_factory=google_connector_factory,
        session_factory=google_session_factory,
    )
    await research_gate._fetch_duckduckgo_lite_ipv4(
        "https://lite.duckduckgo.com/lite/?q=private-duck-query",
        timeout=0.5,
        max_bytes=300_000,
        resolver_factory=_Resolver,
        connector_factory=duck_connector_factory,
        session_factory=duck_session_factory,
    )

    for _ in range(200):
        if len(telemetry) == 2:
            break
        await asyncio.sleep(0.005)
    assert [(item.provider_name, item.outcome) for item in telemetry] == [
        ("Google News RSS", "success"),
        ("DuckDuckGo Lite", "success"),
    ]
    assert all(item.terminal_stage == "complete" for item in telemetry)
    assert "private-google-query" not in repr(telemetry)
    assert "private-duck-query" not in repr(telemetry)
    assert all(connector.closed for connector in google_connectors + duck_connectors)
    assert all(session.closed for session in google_sessions + duck_sessions)


@pytest.mark.asyncio
async def test_canonical_host_is_pinned_without_redirects_and_body_is_bounded() -> None:
    response = _Response()
    connectors, sessions, connector_factory, session_factory = _transport_factories(response)

    raw = await research_gate._fetch_google_news_rss_ipv4(
        "https://news.google.com/rss/search?q=test",
        timeout=0.5,
        max_bytes=300_000,
        resolver_factory=lambda: _Resolver(addresses=("8.8.8.8",)),
        connector_factory=connector_factory,
        session_factory=session_factory,
    )

    assert raw == _RSS
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
    answers = await pinned.resolve("news.google.com", 443, socket.AF_INET)
    assert answers == [
        {
            "hostname": "news.google.com",
            "host": "8.8.8.8",
            "port": 443,
            "family": socket.AF_INET,
            "proto": socket.IPPROTO_TCP,
            "flags": socket.AI_NUMERICHOST,
        }
    ]
    with pytest.raises(OSError, match="canonical host"):
        await pinned.resolve("attacker.example", 443, socket.AF_INET)
    assert sessions[0].headers == {"User-Agent": "kalshi-bot-research/1.0"}
    assert sessions[0].get_calls == [
        {
            "url": "https://news.google.com/rss/search?q=test",
            "allow_redirects": False,
        }
    ]
    assert response.content.requested_bytes == [300_001]
    assert response.closed
    assert sessions[0].closed
    assert connectors[0].closed


@pytest.mark.asyncio
async def test_duckduckgo_202_is_success_and_body_remains_parseable(
    monkeypatch,
) -> None:
    response = _Response(body=_DUCK_HTML, status=202, reason="Accepted")
    connectors, sessions, connector_factory, session_factory = _transport_factories(response)

    raw = await research_gate._fetch_duckduckgo_lite_ipv4(
        "https://lite.duckduckgo.com/lite/?q=current+evidence",
        timeout=0.5,
        max_bytes=300_000,
        resolver_factory=lambda: _Resolver(),
        connector_factory=connector_factory,
        session_factory=session_factory,
    )

    async def fetch(*_args, **_kwargs) -> bytes:
        return raw

    monkeypatch.setattr(research_gate, "_fetch_duckduckgo_lite_ipv4", fetch)
    evidence = await research_gate._duckduckgo_lite_search(
        research_gate.ResearchQuery(
            query="current evidence",
            query_intent="supporting",
            source_class="reputable_secondary",
        )
    )

    assert raw == _DUCK_HTML
    assert [item.source_url for item in evidence] == [
        "https://example.com/current-result"
    ]
    assert sessions[0].get_calls[0]["allow_redirects"] is False
    assert response.closed
    assert sessions[0].closed
    assert connectors[0].closed


@pytest.mark.asyncio
async def test_redirect_is_refused_with_existing_http_error_taxonomy() -> None:
    response = _Response(status=302, reason="Found")
    connectors, sessions, connector_factory, session_factory = _transport_factories(response)

    with pytest.raises(urllib.error.HTTPError) as raised:
        await research_gate._fetch_google_news_rss_ipv4(
            "https://news.google.com/rss/search?q=test",
            timeout=0.5,
            max_bytes=300_000,
            resolver_factory=lambda: _Resolver(),
            connector_factory=connector_factory,
            session_factory=session_factory,
        )

    assert raised.value.code == 302
    assert sessions[0].get_calls[0]["allow_redirects"] is False
    assert response.closed
    assert sessions[0].closed
    assert connectors[0].closed


@pytest.mark.asyncio
async def test_oversized_body_is_rejected_at_exact_cap_and_all_resources_close() -> None:
    response = _Response(body=b"x" * 11)
    connectors, sessions, connector_factory, session_factory = _transport_factories(response)

    with pytest.raises(ValueError, match="10-byte limit"):
        await research_gate._fetch_google_news_rss_ipv4(
            "https://news.google.com/rss/search?q=test",
            timeout=0.5,
            max_bytes=10,
            resolver_factory=lambda: _Resolver(),
            connector_factory=connector_factory,
            session_factory=session_factory,
        )

    assert response.content.requested_bytes == [11]
    assert response.closed
    assert sessions[0].closed
    assert connectors[0].closed


@pytest.mark.asyncio
async def test_duckduckgo_search_uses_cancellable_bounded_ipv4_transport(
    monkeypatch,
) -> None:
    calls: list[tuple[str, float, int]] = []

    async def fetch(url: str, *, timeout: float, max_bytes: int) -> bytes:
        calls.append((url, timeout, max_bytes))
        return _DUCK_HTML

    monkeypatch.setattr(
        research_gate,
        "_fetch_duckduckgo_lite_ipv4",
        fetch,
        raising=False,
    )
    monkeypatch.setattr(
        research_gate.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("sync fallback transport was used"),
    )

    evidence = await research_gate._duckduckgo_lite_search(
        research_gate.ResearchQuery(
            query="current evidence",
            query_intent="supporting",
            source_class="reputable_secondary",
        ),
        timeout=1.25,
    )

    assert len(evidence) == 1
    assert evidence[0].source_url == "https://example.com/current-result"
    assert calls == [
        (
            "https://lite.duckduckgo.com/lite/?q=current+evidence",
            1.25,
            300_000,
        )
    ]
