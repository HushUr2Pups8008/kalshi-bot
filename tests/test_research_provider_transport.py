from __future__ import annotations

import socket
import urllib.error

import pytest

from analysis import research_gate


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

    class FakeSocket:
        timeout: float | None = None
        bound: tuple[str, int] | None = None
        connected: tuple[str, int] | None = None

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

        def bind(self, address: tuple[str, int]) -> None:
            self.bound = address

        def connect(self, address: tuple[str, int]) -> None:
            self.connected = address

        def close(self) -> None:
            raise AssertionError("successful socket must remain open")

    fake_socket = FakeSocket()

    def getaddrinfo(host: str, port: int, family: int, socktype: int):
        resolver_calls.append((host, port, family, socktype))
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.0.2.1", port))]

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
    assert fake_socket.connected == ("192.0.2.1", 443)


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
