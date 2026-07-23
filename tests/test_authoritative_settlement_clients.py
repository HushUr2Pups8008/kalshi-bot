from __future__ import annotations

import json
import urllib.error

import pytest

from kalshi.rest_client import KalshiRestClient


def _kalshi_market_payload(ticker: str) -> bytes:
    return json.dumps(
        {
            "market": {
                "ticker": ticker,
                "title": "Will the bounded client preserve exact market identity?",
                "yes_bid_dollars": "0.4100",
                "yes_ask_dollars": "0.4300",
                "no_bid_dollars": "0.5700",
                "no_ask_dollars": "0.5900",
                "status": "finalized",
                "close_time": "2027-01-01T00:00:00Z",
            }
        }
    ).encode("utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "external-api.kalshi.com",
        "api.elections.kalshi.com",
        "external-api.demo.kalshi.co",
        "demo-api.kalshi.co",
    ],
)
async def test_kalshi_bounded_exact_market_uses_documented_host_and_decodes_object(
    host: str,
) -> None:
    client = KalshiRestClient()
    client._base = f"https://{host}/trade-api/v2"
    calls: list[tuple[str, dict[str, object]]] = []

    def unexpected_legacy_request(*_: object, **__: object) -> object:
        pytest.fail("bounded lookup must not use the legacy requests client")

    client._request = unexpected_legacy_request  # type: ignore[method-assign]

    async def fetcher(url: str, **kwargs: object) -> bytes:
        calls.append((url, kwargs))
        return _kalshi_market_payload("KXTEST-1")

    market = await client.get_market_exact_bounded(
        "KXTEST-1", timeout_seconds=2.0, fetcher=fetcher
    )

    assert market is not None
    assert market.ticker == "KXTEST-1"
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == f"https://{host}/trade-api/v2/markets/KXTEST-1"
    assert kwargs == {
        "canonical_host": host,
        "provider_name": "Kalshi authoritative settlement",
        "user_agent": "kalshi-bot/authoritative-settlement-v1",
        "timeout": 2.0,
        "max_bytes": 262_144,
    }


@pytest.mark.asyncio
async def test_kalshi_bounded_exact_market_canonicalizes_explicit_https_port() -> None:
    client = KalshiRestClient()
    client._base = "https://api.elections.kalshi.com:443/trade-api/v2"
    calls: list[str] = []

    async def fetcher(url: str, **_: object) -> bytes:
        calls.append(url)
        return _kalshi_market_payload("KXTEST-1")

    market = await client.get_market_exact_bounded(
        "KXTEST-1", timeout_seconds=2.0, fetcher=fetcher
    )

    assert market is not None
    assert calls == [
        "https://api.elections.kalshi.com/trade-api/v2/markets/KXTEST-1"
    ]


@pytest.mark.asyncio
async def test_kalshi_bounded_exact_market_preserves_exact_ticker_identity() -> None:
    client = KalshiRestClient()
    client._base = "https://external-api.kalshi.com/trade-api/v2"
    calls: list[str] = []

    async def fetcher(url: str, **_: object) -> bytes:
        calls.append(url)
        return _kalshi_market_payload("KX.TEST_1-ABC")

    market = await client.get_market_exact_bounded(
        "KX.TEST_1-ABC", timeout_seconds=2.0, fetcher=fetcher
    )

    assert market is not None
    assert market.ticker == "KX.TEST_1-ABC"
    assert calls == [
        "https://external-api.kalshi.com/trade-api/v2/markets/KX.TEST_1-ABC"
    ]


@pytest.mark.asyncio
async def test_kalshi_bounded_exact_market_maps_only_404_to_none() -> None:
    client = KalshiRestClient()
    client._base = "https://api.elections.kalshi.com/trade-api/v2"

    async def fetcher(url: str, **_: object) -> bytes:
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    assert await client.get_market_exact_bounded(
        "KXTEST-1", timeout_seconds=2.0, fetcher=fetcher
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
async def test_kalshi_bounded_exact_market_propagates_non_404_errors(
    status: int,
) -> None:
    client = KalshiRestClient()
    client._base = "https://api.elections.kalshi.com/trade-api/v2"

    async def fetcher(url: str, **_: object) -> bytes:
        raise urllib.error.HTTPError(url, status, "non-404", {}, None)

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        await client.get_market_exact_bounded(
            "KXTEST-1", timeout_seconds=2.0, fetcher=fetcher
        )

    assert exc_info.value.code == status


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError("deadline"), ConnectionError("offline")])
async def test_kalshi_bounded_exact_market_propagates_transport_errors(
    error: BaseException,
) -> None:
    client = KalshiRestClient()
    client._base = "https://api.elections.kalshi.com/trade-api/v2"

    async def fetcher(*_: object, **__: object) -> bytes:
        raise error

    with pytest.raises(type(error)):
        await client.get_market_exact_bounded(
            "KXTEST-1", timeout_seconds=2.0, fetcher=fetcher
        )


@pytest.mark.asyncio
async def test_kalshi_bounded_exact_market_rejects_unsafe_ticker_before_fetch() -> None:
    client = KalshiRestClient()
    client._base = "https://api.elections.kalshi.com/trade-api/v2"

    async def fetcher(*_: object, **__: object) -> bytes:
        pytest.fail("ticker validation must complete before fetch")

    with pytest.raises(ValueError, match="ticker"):
        await client.get_market_exact_bounded(
            "KX TEST/1", timeout_seconds=2.0, fetcher=fetcher
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("returned_ticker", ["KXOTHER-1", "kxtest-1", ""])
async def test_kalshi_bounded_exact_market_rejects_response_ticker_mismatch(
    returned_ticker: str,
) -> None:
    client = KalshiRestClient()
    client._base = "https://api.elections.kalshi.com/trade-api/v2"

    async def fetcher(*_: object, **__: object) -> bytes:
        return _kalshi_market_payload(returned_ticker)

    with pytest.raises(ValueError, match="ticker mismatch"):
        await client.get_market_exact_bounded(
            "KXTEST-1", timeout_seconds=2.0, fetcher=fetcher
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base",
    [
        "http://api.elections.kalshi.com/trade-api/v2",
        "https://untrusted.example/trade-api/v2",
        "https://api.elections.kalshi.com/trade-api/v2?ignored=1",
        "https://api.elections.kalshi.com/trade-api/v2#fragment",
        "https://user:pass@api.elections.kalshi.com/trade-api/v2",
        "https://api.elections.kalshi.com:444/trade-api/v2",
        "https://api.elections.kalshi.com:invalid/trade-api/v2",
        "https://api.elections.kalshi.com./trade-api/v2",
        "https://api.elections.kalshi.com/trade-api/v2/extra",
        "https://api.elections.kalshi.com/trade-api/v2/",
    ],
)
async def test_kalshi_bounded_exact_market_rejects_unapproved_base_before_fetch(
    base: str,
) -> None:
    client = KalshiRestClient()
    client._base = base

    async def fetcher(*_: object, **__: object) -> bytes:
        pytest.fail("base validation must complete before fetch")

    with pytest.raises(ValueError):
        await client.get_market_exact_bounded(
            "KXTEST-1", timeout_seconds=2.0, fetcher=fetcher
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_seconds", [0.0, -1.0, float("inf"), float("nan")])
async def test_kalshi_bounded_exact_market_rejects_unbounded_timeout_before_fetch(
    timeout_seconds: float,
) -> None:
    client = KalshiRestClient()
    client._base = "https://api.elections.kalshi.com/trade-api/v2"

    async def fetcher(*_: object, **__: object) -> bytes:
        pytest.fail("timeout validation must complete before fetch")

    with pytest.raises(ValueError, match="timeout"):
        await client.get_market_exact_bounded(
            "KXTEST-1", timeout_seconds=timeout_seconds, fetcher=fetcher
        )


@pytest.mark.asyncio
async def test_kalshi_bounded_exact_market_preserves_json_contract_failures() -> None:
    client = KalshiRestClient()
    client._base = "https://api.elections.kalshi.com/trade-api/v2"

    async def fetcher(*_: object, **__: object) -> bytes:
        return b"[]"

    with pytest.raises(ValueError, match="JSON object"):
        await client.get_market_exact_bounded(
            "KXTEST-1", timeout_seconds=2.0, fetcher=fetcher
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [b"\xff", b"not-json", b"", b"null", b'{"market": []}'],
)
async def test_kalshi_bounded_exact_market_never_maps_malformed_2xx_to_none(
    raw: bytes,
) -> None:
    client = KalshiRestClient()
    client._base = "https://api.elections.kalshi.com/trade-api/v2"

    async def fetcher(*_: object, **__: object) -> bytes:
        return raw

    with pytest.raises(ValueError):
        await client.get_market_exact_bounded(
            "KXTEST-1", timeout_seconds=2.0, fetcher=fetcher
        )
