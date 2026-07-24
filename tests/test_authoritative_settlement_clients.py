from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from kalshi import KalshiMarket
from kalshi.rest_client import KalshiRestClient
from polymarket.public_client import PolymarketPublicClient
from polymarket.settlement import normalize_polymarket_settlement
from polymarket.settlement_reconciler import SettlementNotFound
from trading.authoritative_settlement_source import (
    POLYMARKET_SETTLEMENT_RULES_VERSION,
    POLYMARKET_SETTLEMENT_SOURCE_ID,
    AuthoritativeSettlementSource,
)
from trading.settlement import MarketOutcome, SettlementDriftError
from trading.venue import MarketRef, Venue


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


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


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


@pytest.mark.asyncio
async def test_polymarket_bounded_settlement_uses_exact_gateway_without_legacy_request() -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    calls: list[tuple[str, dict[str, object]]] = []

    def unexpected_legacy_request(*_: object, **__: object) -> object:
        pytest.fail("bounded lookup must not use the legacy requests client")

    client._request = unexpected_legacy_request  # type: ignore[method-assign]

    async def fetcher(url: str, **kwargs: object) -> bytes:
        calls.append((url, kwargs))
        return _json_bytes({"slug": "exact-slug", "settlement": 1})

    payload = await client.get_market_settlement_exact_bounded(
        "exact-slug", timeout_seconds=2.0, fetcher=fetcher
    )

    assert payload == {"slug": "exact-slug", "settlement": 1}
    assert calls == [
        (
            "https://gateway.polymarket.us/v1/markets/exact-slug/settlement",
            {
                "canonical_host": "gateway.polymarket.us",
                "provider_name": "Polymarket US authoritative settlement",
                "user_agent": "kalshi-bot/authoritative-settlement-v1",
                "timeout": 2.0,
                "max_bytes": 262_144,
            },
        )
    ]


@pytest.mark.asyncio
async def test_polymarket_bounded_market_by_slug_requires_documented_envelope() -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    calls: list[tuple[str, dict[str, object]]] = []

    async def fetcher(url: str, **kwargs: object) -> bytes:
        calls.append((url, kwargs))
        return _json_bytes({"market": {"id": "42", "slug": "exact-slug"}})

    payload = await client.get_market_by_slug_exact_bounded(
        "exact-slug", timeout_seconds=2.0, fetcher=fetcher
    )

    assert payload == {"id": "42", "slug": "exact-slug"}
    assert calls == [
        (
            "https://gateway.polymarket.us/v1/market/slug/exact-slug",
            {
                "canonical_host": "gateway.polymarket.us",
                "provider_name": "Polymarket US authoritative market",
                "user_agent": "kalshi-bot/authoritative-settlement-v1",
                "timeout": 2.0,
                "max_bytes": 262_144,
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name",
    ["get_market_settlement_exact_bounded", "get_market_by_slug_exact_bounded"],
)
async def test_polymarket_bounded_exact_reads_map_only_404_to_none(
    method_name: str,
) -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")

    async def fetcher(url: str, **_: object) -> bytes:
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    method = getattr(client, method_name)
    assert await method("exact-slug", timeout_seconds=2.0, fetcher=fetcher) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://gateway.polymarket.us",
        "https://untrusted.example",
        "https://gateway.polymarket.us/",
        "https://gateway.polymarket.us/prefix",
        "https://gateway.polymarket.us?ignored=1",
        "https://gateway.polymarket.us#fragment",
        "https://user:pass@gateway.polymarket.us",
        "https://GATEWAY.POLYMARKET.US",
        "https://gateway.polymarket.us:",
        "https://gateway.polymarket.us:443",
        "https://gateway.polymarket.us:444",
        "https://gateway.polymarket.us:invalid",
    ],
)
async def test_polymarket_bounded_exact_reads_reject_invalid_base_before_fetch(
    base_url: str,
) -> None:
    client = PolymarketPublicClient(base_url=base_url)

    async def fetcher(*_: object, **__: object) -> bytes:
        pytest.fail("base validation must complete before fetch")

    with pytest.raises(ValueError):
        await client.get_market_settlement_exact_bounded(
            "exact-slug", timeout_seconds=2.0, fetcher=fetcher
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["", "exact slug", "exact/slug", "exact?slug"])
async def test_polymarket_bounded_exact_reads_reject_invalid_slug_before_fetch(
    slug: str,
) -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")

    async def fetcher(*_: object, **__: object) -> bytes:
        pytest.fail("slug validation must complete before fetch")

    with pytest.raises(ValueError, match="slug"):
        await client.get_market_settlement_exact_bounded(
            slug, timeout_seconds=2.0, fetcher=fetcher
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_seconds", [0.0, -1.0, float("inf"), float("nan")])
async def test_polymarket_bounded_exact_reads_reject_unbounded_timeout_before_fetch(
    timeout_seconds: float,
) -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")

    async def fetcher(*_: object, **__: object) -> bytes:
        pytest.fail("timeout validation must complete before fetch")

    with pytest.raises(ValueError, match="timeout"):
        await client.get_market_settlement_exact_bounded(
            "exact-slug", timeout_seconds=timeout_seconds, fetcher=fetcher
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("returned_slug", ["other-slug", " exact-slug", "exact-slug ", None])
async def test_polymarket_bounded_settlement_rejects_non_exact_returned_slug(
    returned_slug: object,
) -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")

    async def fetcher(*_: object, **__: object) -> bytes:
        return _json_bytes({"slug": returned_slug, "settlement": 1})

    with pytest.raises(ValueError, match="slug"):
        await client.get_market_settlement_exact_bounded(
            "exact-slug", timeout_seconds=2.0, fetcher=fetcher
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [b"\xff", b"not-json", b"", b"[]", b"null", b'{"slug": "exact-slug"}'],
)
async def test_polymarket_bounded_settlement_never_maps_malformed_2xx_to_none(
    raw: bytes,
) -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")

    async def fetcher(*_: object, **__: object) -> bytes:
        return raw

    with pytest.raises(ValueError):
        await client.get_market_settlement_exact_bounded(
            "exact-slug", timeout_seconds=2.0, fetcher=fetcher
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("settlement", [[], True, float("nan"), float("inf")])
async def test_polymarket_bounded_settlement_rejects_non_decimal_value(
    settlement: object,
) -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")

    async def fetcher(*_: object, **__: object) -> bytes:
        return _json_bytes({"slug": "exact-slug", "settlement": settlement})

    with pytest.raises(ValueError, match="settlement"):
        await client.get_market_settlement_exact_bounded(
            "exact-slug", timeout_seconds=2.0, fetcher=fetcher
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"id": "42", "slug": "exact-slug"},
        {"market": {"slug": "exact-slug"}},
        {"market": {"id": 42, "slug": "exact-slug"}},
        {"market": {"id": "42", "slug": " exact-slug"}},
    ],
)
async def test_polymarket_bounded_market_by_slug_rejects_invalid_envelope_or_identity(
    payload: dict[str, object],
) -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")

    async def fetcher(*_: object, **__: object) -> bytes:
        return _json_bytes(payload)

    with pytest.raises(ValueError):
        await client.get_market_by_slug_exact_bounded(
            "exact-slug", timeout_seconds=2.0, fetcher=fetcher
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
async def test_polymarket_bounded_exact_reads_propagate_non_404_statuses(
    status: int,
) -> None:
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")

    async def fetcher(url: str, **_: object) -> bytes:
        raise urllib.error.HTTPError(url, status, "non-404", {}, None)

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        await client.get_market_settlement_exact_bounded(
            "exact-slug", timeout_seconds=2.0, fetcher=fetcher
        )

    assert exc_info.value.code == status


UTC = timezone.utc
SOURCE_NOW = datetime(2026, 7, 23, 18, 0, tzinfo=UTC)


def _source_kalshi_market(
    *,
    ticker: str = "KXTEST-1",
    status: str = "settled",
    result: str = "yes",
) -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        title="Authoritative source test market",
        yes_bid=0.0,
        yes_ask=0.0,
        yes_price=0.0,
        volume=0,
        open_interest=0,
        close_time="2026-07-23T17:00:00+00:00",
        status=status,
        result=result,
        expiration_time="2026-07-23T17:00:00+00:00",
        raw_payload_hash="a" * 64,
    )


class _FakeKalshiClient:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.calls: list[tuple[str, float]] = []

    async def get_market_exact_bounded(
        self, ticker: str, *, timeout_seconds: float
    ) -> KalshiMarket | None:
        self.calls.append((ticker, timeout_seconds))
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]


class _FakePolymarketClient:
    def __init__(self, settlements: list[object], markets: list[object]) -> None:
        self.settlements = settlements
        self.markets = markets
        self.settlement_calls: list[tuple[str, float]] = []
        self.market_calls: list[tuple[str, float]] = []

    async def get_market_settlement_exact_bounded(
        self, slug: str, *, timeout_seconds: float
    ) -> dict[str, object] | None:
        self.settlement_calls.append((slug, timeout_seconds))
        value = self.settlements.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    async def get_market_by_slug_exact_bounded(
        self, slug: str, *, timeout_seconds: float
    ) -> dict[str, object] | None:
        self.market_calls.append((slug, timeout_seconds))
        value = self.markets.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]


def _source(
    kalshi: _FakeKalshiClient,
    polymarket: _FakePolymarketClient,
    *,
    clock=lambda: SOURCE_NOW,
    monotonic=lambda: 0.0,
    timeout_seconds: float = 3.0,
) -> AuthoritativeSettlementSource:
    return AuthoritativeSettlementSource(
        kalshi_client=kalshi,
        polymarket_client=polymarket,
        clock=clock,
        monotonic=monotonic,
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    (
        "initialized",
        "inactive",
        "active",
        "closed",
        "determined",
        "disputed",
        "amended",
        "unopened",
        "open",
        "paused",
    ),
)
async def test_source_returns_none_only_for_known_kalshi_nonterminal_statuses(
    status: str,
) -> None:
    kalshi = _FakeKalshiClient([_source_kalshi_market(status=status)])
    polymarket = _FakePolymarketClient([], [])

    observation = await _source(kalshi, polymarket).get_settlement_exact(
        MarketRef(Venue.KALSHI, "KXTEST-1", "KXTEST-1"), prior_observation=None
    )

    assert observation is None
    assert kalshi.calls == [("KXTEST-1", 3.0)]


@pytest.mark.asyncio
async def test_source_normalizes_exact_terminal_kalshi_market() -> None:
    kalshi = _FakeKalshiClient([_source_kalshi_market()])
    polymarket = _FakePolymarketClient([], [])

    observation = await _source(kalshi, polymarket).get_settlement_exact(
        MarketRef(Venue.KALSHI, "KXTEST-1", "KXTEST-1"), prior_observation=None
    )

    assert observation is not None
    assert observation.outcome is MarketOutcome.YES
    assert observation.source_id == "kalshi-market-api"


@pytest.mark.asyncio
async def test_source_rejects_kalshi_alias_or_returned_ticker_mismatch() -> None:
    kalshi = _FakeKalshiClient([_source_kalshi_market(ticker="KXOTHER-1")])
    polymarket = _FakePolymarketClient([], [])
    source = _source(kalshi, polymarket)

    with pytest.raises(SettlementDriftError, match="alias"):
        await source.get_settlement_exact(
            MarketRef(Venue.KALSHI, "KXTEST-1", "other"), prior_observation=None
        )
    assert kalshi.calls == []

    with pytest.raises(SettlementDriftError, match="ticker"):
        await source.get_settlement_exact(
            MarketRef(Venue.KALSHI, "KXTEST-1", "KXTEST-1"), prior_observation=None
        )


@pytest.mark.asyncio
async def test_source_rejects_unknown_or_missing_kalshi_terminal_state() -> None:
    kalshi = _FakeKalshiClient(
        [_source_kalshi_market(status="mystery"), None]
    )
    polymarket = _FakePolymarketClient([], [])
    source = _source(kalshi, polymarket)
    ref = MarketRef(Venue.KALSHI, "KXTEST-1", "KXTEST-1")

    with pytest.raises(SettlementDriftError, match="status"):
        await source.get_settlement_exact(ref, prior_observation=None)
    with pytest.raises(SettlementNotFound):
        await source.get_settlement_exact(ref, prior_observation=None)


@pytest.mark.asyncio
async def test_source_converts_only_kalshi_client_value_error_to_drift() -> None:
    kalshi = _FakeKalshiClient([ValueError("malformed"), TimeoutError("slow")])
    polymarket = _FakePolymarketClient([], [])
    source = _source(kalshi, polymarket)
    ref = MarketRef(Venue.KALSHI, "KXTEST-1", "KXTEST-1")

    with pytest.raises(SettlementDriftError):
        await source.get_settlement_exact(ref, prior_observation=None)
    with pytest.raises(TimeoutError, match="slow"):
        await source.get_settlement_exact(ref, prior_observation=None)


@pytest.mark.asyncio
async def test_source_confirms_polymarket_identity_for_settlement() -> None:
    kalshi = _FakeKalshiClient([])
    polymarket = _FakePolymarketClient(
        [{"slug": "exact", "settlement": 1}],
        [{"slug": "exact", "id": "42"}],
    )
    ticks = iter((0.0, 0.1, 0.2))
    source = _source(kalshi, polymarket, monotonic=lambda: next(ticks))

    observation = await source.get_settlement_exact(
        MarketRef(Venue.POLYMARKET_US, "42", "exact"), prior_observation=None
    )

    assert observation is not None
    assert observation.outcome is MarketOutcome.YES
    assert json.loads(observation.canonical_payload_json)["id"] == "42"
    assert polymarket.settlement_calls == [("exact", 2.9)]
    assert polymarket.market_calls == [("exact", 2.8)]


@pytest.mark.asyncio
async def test_source_returns_none_for_polymarket_404_only_after_identity_read() -> None:
    kalshi = _FakeKalshiClient([])
    polymarket = _FakePolymarketClient(
        [None], [{"slug": "exact", "id": "42"}]
    )
    source = _source(kalshi, polymarket)

    observation = await source.get_settlement_exact(
        MarketRef(Venue.POLYMARKET_US, "42", "exact"), prior_observation=None
    )

    assert observation is None
    assert len(polymarket.settlement_calls) == len(polymarket.market_calls) == 1


@pytest.mark.asyncio
async def test_source_fails_closed_for_polymarket_unresolved_identity_paths() -> None:
    ref = MarketRef(Venue.POLYMARKET_US, "42", "exact")

    with pytest.raises(SettlementNotFound):
        await _source(
            _FakeKalshiClient([]), _FakePolymarketClient([None], [None])
        ).get_settlement_exact(ref, prior_observation=None)
    with pytest.raises(SettlementDriftError, match="identity"):
        await _source(
            _FakeKalshiClient([]),
            _FakePolymarketClient(
                [{"slug": "exact", "settlement": 1}], [None]
            ),
        ).get_settlement_exact(ref, prior_observation=None)
    with pytest.raises(SettlementDriftError, match="identity"):
        await _source(
            _FakeKalshiClient([]),
            _FakePolymarketClient(
                [{"slug": "exact", "settlement": 1}],
                [{"slug": "exact", "id": "43"}],
            ),
        ).get_settlement_exact(ref, prior_observation=None)


@pytest.mark.asyncio
async def test_source_rejects_polymarket_conflicting_settlement_id_before_normalizing() -> None:
    source = _source(
        _FakeKalshiClient([]),
        _FakePolymarketClient(
            [{"slug": "exact", "settlement": 1, "marketId": "43"}],
            [{"slug": "exact", "id": "42"}],
        ),
    )

    with pytest.raises(SettlementDriftError, match="canonical"):
        await source.get_settlement_exact(
            MarketRef(Venue.POLYMARKET_US, "42", "exact"), prior_observation=None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ("id", "market_id", "marketId"))
async def test_source_canonicalizes_equivalent_numeric_polymarket_settlement_ids(
    key: str,
) -> None:
    source = _source(
        _FakeKalshiClient([]),
        _FakePolymarketClient(
            [{"slug": "exact", "settlement": 1, key: 42}],
            [{"slug": "exact", "id": "42"}],
        ),
    )

    observation = await source.get_settlement_exact(
        MarketRef(Venue.POLYMARKET_US, "42", "exact"), prior_observation=None
    )

    assert observation is not None
    payload = json.loads(observation.canonical_payload_json)
    assert payload["id"] == "42"
    assert "market_id" not in payload and "marketId" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("value", (True, 42.0, "042", " 42", "42 "))
async def test_source_rejects_noncanonical_polymarket_settlement_ids(
    value: object,
) -> None:
    source = _source(
        _FakeKalshiClient([]),
        _FakePolymarketClient(
            [{"slug": "exact", "settlement": 1, "id": value}],
            [{"slug": "exact", "id": "42"}],
        ),
    )

    with pytest.raises(SettlementDriftError, match="canonical"):
        await source.get_settlement_exact(
            MarketRef(Venue.POLYMARKET_US, "42", "exact"), prior_observation=None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "market_ref",
    (
        MarketRef(Venue.POLYMARKET_US, "not-numeric", "exact"),
        MarketRef(Venue.POLYMARKET_US, "42", "bad/slug"),
    ),
)
async def test_source_rejects_invalid_polymarket_identity_before_io(
    market_ref: MarketRef,
) -> None:
    polymarket = _FakePolymarketClient([], [])

    with pytest.raises(SettlementDriftError):
        await _source(_FakeKalshiClient([]), polymarket).get_settlement_exact(
            market_ref, prior_observation=None
        )

    assert polymarket.settlement_calls == polymarket.market_calls == []


@pytest.mark.asyncio
async def test_source_rejects_non_text_polymarket_alias_before_io() -> None:
    polymarket = _FakePolymarketClient([], [])

    with pytest.raises(SettlementDriftError, match="slug"):
        await _source(_FakeKalshiClient([]), polymarket).get_settlement_exact(
            MarketRef(Venue.POLYMARKET_US, "42", None),  # type: ignore[arg-type]
            prior_observation=None,
        )

    assert polymarket.settlement_calls == polymarket.market_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rules_version", "source_id"),
    (
        (POLYMARKET_SETTLEMENT_RULES_VERSION, "foreign-source"),
        ("foreign-rules", POLYMARKET_SETTLEMENT_SOURCE_ID),
    ),
)
async def test_source_rejects_foreign_prior_provenance_before_io(
    rules_version: str,
    source_id: str,
) -> None:
    ref = MarketRef(Venue.POLYMARKET_US, "42", "exact")
    prior = normalize_polymarket_settlement(
        ref,
        {"slug": "exact", "id": "42", "settlement": 1},
        observed_at=SOURCE_NOW,
        effective_at=SOURCE_NOW,
        rules_version=rules_version,
        source_id=source_id,
    )
    polymarket = _FakePolymarketClient([], [])

    with pytest.raises(SettlementDriftError, match="provenance"):
        await _source(_FakeKalshiClient([]), polymarket).get_settlement_exact(
            ref, prior_observation=prior
        )

    assert polymarket.settlement_calls == polymarket.market_calls == []


@pytest.mark.asyncio
async def test_source_rejects_non_exact_polymarket_settlement_slug() -> None:
    source = _source(
        _FakeKalshiClient([]),
        _FakePolymarketClient(
            [{"slug": "other", "settlement": 1}],
            [{"slug": "exact", "id": "42"}],
        ),
    )

    with pytest.raises(SettlementDriftError, match="alias"):
        await source.get_settlement_exact(
            MarketRef(Venue.POLYMARKET_US, "42", "exact"), prior_observation=None
        )


@pytest.mark.asyncio
async def test_source_polymarket_deadline_stops_before_second_read() -> None:
    polymarket = _FakePolymarketClient(
        [None], [{"slug": "exact", "id": "42"}]
    )
    ticks = iter((0.0, 0.0, 1.0))
    source = _source(
        _FakeKalshiClient([]),
        polymarket,
        monotonic=lambda: next(ticks),
        timeout_seconds=0.5,
    )

    with pytest.raises(TimeoutError, match="deadline"):
        await source.get_settlement_exact(
            MarketRef(Venue.POLYMARKET_US, "42", "exact"), prior_observation=None
        )
    assert len(polymarket.settlement_calls) == 1
    assert polymarket.market_calls == []


@pytest.mark.asyncio
async def test_source_converts_only_polymarket_client_value_error_to_drift() -> None:
    ref = MarketRef(Venue.POLYMARKET_US, "42", "exact")
    source = _source(
        _FakeKalshiClient([]),
        _FakePolymarketClient([ValueError("malformed"), TimeoutError("slow")], []),
    )

    with pytest.raises(SettlementDriftError):
        await source.get_settlement_exact(ref, prior_observation=None)
    with pytest.raises(TimeoutError, match="slow"):
        await source.get_settlement_exact(ref, prior_observation=None)


@pytest.mark.asyncio
async def test_source_stabilizes_identical_polymarket_poll_and_links_change() -> None:
    kalshi = _FakeKalshiClient([])
    polymarket = _FakePolymarketClient(
        [
            {"slug": "exact", "settlement": 1},
            {"slug": "exact", "settlement": 1},
            {"slug": "exact", "settlement": 0},
        ],
        [
            {"slug": "exact", "id": "42"},
            {"slug": "exact", "id": "42"},
            {"slug": "exact", "id": "42"},
        ],
    )
    times = iter(
        (
            SOURCE_NOW,
            SOURCE_NOW + timedelta(minutes=1),
            SOURCE_NOW + timedelta(minutes=2),
            SOURCE_NOW + timedelta(minutes=3),
        )
    )
    source = _source(kalshi, polymarket, clock=lambda: next(times))
    ref = MarketRef(Venue.POLYMARKET_US, "42", "exact")

    first = await source.get_settlement_exact(ref, prior_observation=None)
    assert first is not None
    identical = await source.get_settlement_exact(ref, prior_observation=first)
    changed = await source.get_settlement_exact(ref, prior_observation=first)

    assert identical is not None
    assert identical.observation_sha256 == first.observation_sha256
    assert identical.supersedes_observation_sha256 is None
    assert identical.effective_at == first.effective_at
    assert changed is not None
    assert changed.outcome is MarketOutcome.NO
    assert changed.supersedes_observation_sha256 == first.observation_sha256
    assert changed.observed_at == changed.effective_at == SOURCE_NOW + timedelta(
        minutes=3
    )
