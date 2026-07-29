from __future__ import annotations

import inspect
import socket

import pytest

from analysis import research_gate


_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss><channel><item>
  <title>Federal Reserve update - Reuters</title>
  <link>https://news.google.com/rss/articles/fed-update</link>
  <source url="https://reuters.com">Reuters</source>
  <description>Federal Reserve policy update.</description>
  <pubDate>Wed, 15 Jul 2026 07:01:00 GMT</pubDate>
</item></channel></rss>
"""


@pytest.mark.asyncio
async def test_rss_search_uses_bounded_dual_stack_google_news_transport(
    monkeypatch,
) -> None:
    calls: list[tuple[str, float, int]] = []

    async def fetch(url: str, *, timeout: float, max_bytes: int) -> bytes:
        calls.append((url, timeout, max_bytes))
        return _RSS

    monkeypatch.setattr(research_gate, "_fetch_google_news_rss_dual_stack", fetch)

    evidence = await research_gate._rss_search(
        research_gate.ResearchQuery(
            query="Federal Reserve interest rate July 2026",
            query_intent="staleness_check",
            source_class="reputable_secondary",
        ),
        timeout=1.25,
    )

    assert len(evidence) == 1
    assert calls == [
        (
            "https://news.google.com/rss/search?"
            "q=Federal+Reserve+interest+rate+July+2026&hl=en-US&gl=US&ceid=US%3Aen",
            1.25,
            300_000,
        )
    ]


@pytest.mark.asyncio
async def test_google_news_transport_uses_bounded_dual_stack_transport(
    monkeypatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def fetch(url: str, **kwargs: object) -> bytes:
        calls.append((url, kwargs))
        return _RSS

    monkeypatch.setattr(
        research_gate,
        "fetch_bounded_https_dual_stack",
        fetch,
        raising=False,
    )
    transport = getattr(research_gate, "_fetch_google_news_rss_dual_stack", None)
    assert transport is not None

    await transport(
        "https://news.google.com/rss/search?q=test",
        timeout=1.0,
        max_bytes=1_000,
    )

    assert calls[0][0] == "https://news.google.com/rss/search?q=test"
    assert calls[0][1]["canonical_host"] == "news.google.com"
    assert calls[0][1]["provider_name"] == "Google News RSS"
    assert callable(calls[0][1]["admission_factory"])
    assert callable(calls[0][1]["telemetry_sink"])


def test_ipv4_validation_deduplicates_global_answers() -> None:
    assert research_gate._validated_global_ipv4_addresses(
        ("8.8.8.8", "1.1.1.1", "8.8.8.8")
    ) == ("8.8.8.8", "1.1.1.1")


def test_ipv4_validation_preserves_private_name_and_signature() -> None:
    validate = research_gate._validated_global_ipv4_addresses

    assert validate.__name__ == "_validated_global_ipv4_addresses"
    assert str(inspect.signature(validate)) == (
        "(addresses: 'Iterable[str]', *, provider_name: 'str' = 'Google News') "
        "-> 'tuple[str, ...]'"
    )


def test_ipv4_validation_accepts_addresses_keyword() -> None:
    assert research_gate._validated_global_ipv4_addresses(
        addresses=("8.8.8.8",),
    ) == ("8.8.8.8",)


@pytest.mark.parametrize(
    "address",
    (
        "::1",
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "0.0.0.0",
        "not-an-ip",
    ),
)
def test_ipv4_validation_rejects_untrusted_resolver_answer(address: str) -> None:
    with pytest.raises(ValueError, match="DNS answer"):
        research_gate._validated_global_ipv4_addresses((address,))


def test_ipv4_validation_rejects_mixed_answers_without_fallthrough() -> None:
    with pytest.raises(ValueError, match="global IPv4"):
        research_gate._validated_global_ipv4_addresses(("8.8.8.8", "127.0.0.1"))


def test_ipv4_validation_maps_empty_answer_to_availability_failure() -> None:
    with pytest.raises(socket.gaierror):
        research_gate._validated_global_ipv4_addresses(())


@pytest.mark.asyncio
async def test_google_news_transport_rejects_noncanonical_host() -> None:
    with pytest.raises(ValueError, match="canonical HTTPS host"):
        await research_gate._fetch_google_news_rss_dual_stack(
            "https://news.google.com.attacker.example/rss/search?q=test",
            timeout=1.0,
            max_bytes=1_000,
        )


@pytest.mark.asyncio
async def test_duckduckgo_transport_rejects_noncanonical_host() -> None:
    with pytest.raises(ValueError, match="canonical HTTPS host"):
        await research_gate._fetch_duckduckgo_lite_dual_stack(
            "https://lite.duckduckgo.com.attacker.example/lite/?q=test",
            timeout=1.0,
            max_bytes=1_000,
        )
