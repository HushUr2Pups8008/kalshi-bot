from __future__ import annotations

import asyncio
import math
import socket
import urllib.error

import pytest

from analysis import research_gate


_VALID_IPV4_ANSWER = (
    socket.AF_INET,
    socket.SOCK_STREAM,
    socket.IPPROTO_TCP,
    "",
    ("8.8.8.8", 443),
)


class _SocketProbe:
    def __init__(
        self,
        *,
        fail_stage: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.fail_stage = fail_stage
        self.error = error
        self.timeout: float | None = None
        self.bound: tuple[str, int] | None = None
        self.connected: tuple[str, int] | None = None
        self.close_calls = 0

    def _raise_for(self, stage: str) -> None:
        if self.fail_stage == stage and self.error is not None:
            raise self.error

    def settimeout(self, timeout: float) -> None:
        self._raise_for("settimeout")
        self.timeout = timeout

    def bind(self, address: tuple[str, int]) -> None:
        self._raise_for("bind")
        self.bound = address

    def connect(self, address: tuple[str, int]) -> None:
        self._raise_for("connect")
        self.connected = address

    def close(self) -> None:
        self.close_calls += 1


def test_rss_search_uses_bounded_ipv4_google_news_transport(monkeypatch) -> None:
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>Federal Reserve update - Reuters</title>
      <link>https://news.google.com/rss/articles/fed-update</link>
      <source url="https://reuters.com">Reuters</source>
      <description>Federal Reserve policy update.</description>
      <pubDate>Wed, 15 Jul 2026 07:01:00 GMT</pubDate>
    </item></channel></rss>
    """
    calls: list[tuple[str, float, int]] = []

    def fetch(url: str, *, timeout: float, max_bytes: int) -> bytes:
        calls.append((url, timeout, max_bytes))
        return rss

    monkeypatch.setattr(research_gate, "_fetch_google_news_rss_ipv4", fetch)

    evidence = research_gate._rss_search(
        research_gate.ResearchQuery(
            query="Federal Reserve interest rate July 2026",
            query_intent="staleness_check",
            source_class="reputable_secondary",
        ),
        timeout=1.25,
    )

    assert len(evidence) == 1
    assert len(calls) == 1
    url, timeout, max_bytes = calls[0]
    assert url.startswith("https://news.google.com/rss/search?")
    assert timeout == 1.25
    assert max_bytes == 300_000


def test_ipv4_connection_excludes_ipv6_dns_answers(monkeypatch) -> None:
    resolver_calls: list[tuple[str, int, int, int]] = []
    fake_socket = _SocketProbe()

    def getaddrinfo(host: str, port: int, family: int, socktype: int):
        resolver_calls.append((host, port, family, socktype))
        return [(*_VALID_IPV4_ANSWER[:4], ("8.8.8.8", port))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "socket", lambda *_args: fake_socket)

    result = research_gate._create_ipv4_connection(
        ("news.google.com", 443),
        2.5,
        ("0.0.0.0", 0),
    )

    assert result is fake_socket
    assert resolver_calls == [("news.google.com", 443, socket.AF_INET, socket.SOCK_STREAM)]
    assert fake_socket.timeout == 2.5
    assert fake_socket.bound == ("0.0.0.0", 0)
    assert fake_socket.connected == ("8.8.8.8", 443)
    assert fake_socket.close_calls == 0


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700:4700::1111", 443, 0, 0)),
            id="ipv6-family",
        ),
        pytest.param(
            (socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP, "", ("8.8.8.8", 443)),
            id="datagram-socket",
        ),
        pytest.param(
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_UDP, "", ("8.8.8.8", 443)),
            id="non-tcp-protocol",
        ),
        pytest.param(
            (*_VALID_IPV4_ANSWER[:4], ("8.8.8.8", 444)),
            id="wrong-port",
        ),
        pytest.param(
            (*_VALID_IPV4_ANSWER[:4], ("127.0.0.1", 443)),
            id="loopback",
        ),
        pytest.param(
            (*_VALID_IPV4_ANSWER[:4], ("10.0.0.1", 443)),
            id="rfc1918",
        ),
        pytest.param(
            (*_VALID_IPV4_ANSWER[:4], ("169.254.1.1", 443)),
            id="link-local",
        ),
        pytest.param(
            (*_VALID_IPV4_ANSWER[:4], ("224.0.0.1", 443)),
            id="multicast",
        ),
        pytest.param(
            (*_VALID_IPV4_ANSWER[:4], ("0.0.0.0", 443)),
            id="unspecified",
        ),
        pytest.param(
            (*_VALID_IPV4_ANSWER[:4], ("240.0.0.1", 443)),
            id="reserved",
        ),
        pytest.param(
            (*_VALID_IPV4_ANSWER[:4], ("not-an-ip", 443)),
            id="invalid-ip",
        ),
        pytest.param(
            (*_VALID_IPV4_ANSWER[:4], (None, 443)),
            id="non-string-ip",
        ),
        pytest.param(
            (*_VALID_IPV4_ANSWER[:4], ("8.8.8.8",)),
            id="malformed-sockaddr",
        ),
    ],
)
def test_ipv4_connection_rejects_untrusted_resolver_answer_before_socket_creation(
    monkeypatch,
    answer,
) -> None:
    socket_calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args: [answer])
    monkeypatch.setattr(socket, "socket", lambda *_args: socket_calls.append(_args))

    with pytest.raises(ValueError, match="resolver answer"):
        research_gate._create_ipv4_connection(("news.google.com", 443), 1.0)

    assert socket_calls == []


@pytest.mark.parametrize("forbidden_first", [True, False])
def test_ipv4_connection_rejects_mixed_resolver_answers_without_fallthrough(
    monkeypatch,
    forbidden_first: bool,
) -> None:
    forbidden = (*_VALID_IPV4_ANSWER[:4], ("127.0.0.1", 443))
    answers = [forbidden, _VALID_IPV4_ANSWER]
    if not forbidden_first:
        answers.reverse()
    socket_calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args: answers)
    monkeypatch.setattr(socket, "socket", lambda *_args: socket_calls.append(_args))

    with pytest.raises(ValueError, match="resolver answer"):
        research_gate._create_ipv4_connection(("news.google.com", 443), 1.0)

    assert socket_calls == []


@pytest.mark.parametrize("timeout", [0.0, -1.0, math.inf, -math.inf, math.nan])
def test_ipv4_connection_rejects_nonfinite_or_nonpositive_timeout_before_resolution(
    monkeypatch,
    timeout: float,
) -> None:
    resolver_calls = []
    socket_calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args: resolver_calls.append(_args))
    monkeypatch.setattr(socket, "socket", lambda *_args: socket_calls.append(_args))

    with pytest.raises(ValueError, match="timeout"):
        research_gate._create_ipv4_connection(("news.google.com", 443), timeout)

    assert resolver_calls == []
    assert socket_calls == []


@pytest.mark.parametrize("stage", ["settimeout", "bind", "connect"])
@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        pytest.param(RuntimeError("transport bug"), RuntimeError, id="runtime-error"),
        pytest.param(KeyboardInterrupt(), KeyboardInterrupt, id="base-exception"),
        pytest.param(asyncio.CancelledError(), asyncio.CancelledError, id="cancelled"),
    ],
)
def test_ipv4_connection_closes_socket_on_every_non_oserror(
    monkeypatch,
    stage: str,
    error: BaseException,
    error_type: type[BaseException],
) -> None:
    probe = _SocketProbe(fail_stage=stage, error=error)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args: [_VALID_IPV4_ANSWER])
    monkeypatch.setattr(socket, "socket", lambda *_args: probe)

    with pytest.raises(error_type):
        research_gate._create_ipv4_connection(
            ("news.google.com", 443),
            1.0,
            ("0.0.0.0", 0),
        )

    assert probe.close_calls == 1


def test_ipv4_connection_closes_failed_oserror_socket_before_retry(monkeypatch) -> None:
    first = _SocketProbe(fail_stage="connect", error=ConnectionError("first address failed"))
    second = _SocketProbe()
    probes = iter((first, second))
    answers = [
        _VALID_IPV4_ANSWER,
        (*_VALID_IPV4_ANSWER[:4], ("1.1.1.1", 443)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args: answers)
    monkeypatch.setattr(socket, "socket", lambda *_args: next(probes))

    result = research_gate._create_ipv4_connection(("news.google.com", 443), 1.0)

    assert result is second
    assert first.close_calls == 1
    assert second.close_calls == 0


def test_google_news_transport_rejects_noncanonical_host() -> None:
    with pytest.raises(ValueError, match="canonical HTTPS host"):
        research_gate._fetch_google_news_rss_ipv4(
            "https://news.google.com.attacker.example/rss/search?q=test",
            timeout=1.0,
            max_bytes=1_000,
        )


def test_google_news_transport_preserves_http_availability_failure(monkeypatch) -> None:
    instances = []

    class Response:
        status = 503
        reason = "Service Unavailable"
        headers = {}

    class Connection:
        def __init__(self, host: str, *, port: int, timeout: float) -> None:
            self.host = host
            self.port = port
            self.timeout = timeout
            self.closed = False
            instances.append(self)

        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            assert method == "GET"
            assert path == "/rss/search?q=test"
            assert headers == {"User-Agent": "kalshi-bot-research/1.0"}
            assert self._create_connection is research_gate._create_ipv4_connection

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(research_gate.http.client, "HTTPSConnection", Connection)

    with pytest.raises(urllib.error.HTTPError) as raised:
        research_gate._fetch_google_news_rss_ipv4(
            "https://news.google.com/rss/search?q=test",
            timeout=1.0,
            max_bytes=1_000,
        )

    assert raised.value.code == 503
    assert len(instances) == 1
    assert instances[0].closed is True
