from __future__ import annotations

import ast
from copy import deepcopy
from datetime import timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from kalshi.public_market_data import (
    KalshiPublicMarketDataError,
    KalshiPublicMarketDataReader,
)


FIXTURES = Path(__file__).parent / "fixtures" / "weather_shadow"
ORIGIN = "https://api.elections.kalshi.com"
EVENTS_URL = f"{ORIGIN}/trade-api/v2/events"
EVENT_URL = f"{EVENTS_URL}/KXHIGHNY-26JUL13"
MARKETS_URL = f"{ORIGIN}/trade-api/v2/markets"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def test_representative_kalshi_fixture_key_sets_are_explicit() -> None:
    events = _fixture("kalshi_events_page_1.json")
    event_detail = _fixture("kalshi_event.json")
    markets = _fixture("kalshi_markets_page_1.json")

    assert set(events) == {"cursor", "events"}
    assert set(event_detail) == {"event", "markets"}
    assert set(markets) == {"cursor", "markets"}
    expected_event_keys = {
        "event_ticker",
        "series_ticker",
        "settlement_sources",
        "markets",
    }
    assert set(events["events"][0]) == expected_event_keys
    assert set(event_detail["event"]) == expected_event_keys
    assert all(
        set(market) == {"ticker", "event_ticker"}
        for market in event_detail["event"]["markets"]
    )
    assert set(events["events"][0]["settlement_sources"][0]) == {"name", "url"}
    expected_market_keys = {
        "ticker",
        "event_ticker",
        "status",
        "close_time",
        "strike_type",
        "cap_strike",
        "title",
        "rules_primary",
        "rules_secondary",
        "yes_bid_dollars",
        "yes_ask_dollars",
        "no_bid_dollars",
        "no_ask_dollars",
        "yes_bid_size_fp",
        "yes_ask_size_fp",
        "last_price_dollars",
        "volume_fp",
        "result",
    }
    assert all(set(market) == expected_market_keys for market in events["events"][0]["markets"])
    assert all(set(market) == expected_market_keys for market in markets["markets"])


class FakeResponse:
    def __init__(self, payload: Any, *, status: int = 200, headers: dict[str, str] | None = None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    async def json(self, *, content_type: None = None) -> Any:
        return deepcopy(self.payload)

    def release(self) -> None:
        return None


class FakeSession:
    def __init__(self, responses: dict[str, list[FakeResponse]], calls: list[dict[str, Any]]):
        self.responses = responses
        self.calls = calls

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        try:
            response = self.responses[url].pop(0)
        except (KeyError, IndexError) as exc:
            raise AssertionError(f"unexpected GET {url}") from exc
        if isinstance(response, BaseException):
            raise response
        return response


class FakeFactory:
    def __init__(self, responses: dict[str, list[FakeResponse]]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.kwargs: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> FakeSession:
        self.kwargs = kwargs
        return FakeSession(self.responses, self.calls)


def _reader(*, events: Any | None = None, event: Any | None = None, markets: Any | None = None):
    responses: dict[str, list[FakeResponse]] = {}
    if events is not None:
        responses[EVENTS_URL] = events if isinstance(events, list) else [FakeResponse(events)]
    if event is not None:
        responses[EVENT_URL] = event if isinstance(event, list) else [FakeResponse(event)]
    if markets is not None:
        responses[MARKETS_URL] = markets if isinstance(markets, list) else [FakeResponse(markets)]
    factory = FakeFactory(responses)
    return KalshiPublicMarketDataReader(session_factory=factory), factory


def test_module_has_no_private_or_trading_imports() -> None:
    path = Path(__file__).parents[1] / "kalshi" / "public_market_data.py"
    tree = ast.parse(path.read_text())
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports |= {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    forbidden = (
        "config",
        "rest_client",
        "credential",
        "sign",
        "trading",
        "executor",
        "account",
        "order",
        "balance",
        "position",
    )
    assert not [name for name in imports if any(token in name.lower() for token in forbidden)]


@pytest.mark.asyncio
async def test_lists_complete_normalized_ladder_with_fixed_public_contract() -> None:
    reader, factory = _reader(
        events=_fixture("kalshi_events_page_1.json"),
        markets=_fixture("kalshi_markets_page_1.json"),
    )

    events = await reader.list_active_events(series_ticker="KXHIGHNY")

    assert len(events) == 1
    event = events[0]
    assert event.event_ticker == "KXHIGHNY-26JUL13"
    assert event.status == "open"
    assert event.close_time.tzinfo is timezone.utc
    assert event.market_tickers == tuple(m.market_ticker for m in event.markets)
    assert [(m.lower_bound_f, m.upper_bound_f) for m in event.markets] == [
        (None, 81),
        (86, 87),
        (90, None),
    ]
    assert event.markets[0].yes_bid_cents == 4
    assert event.markets[0].no_ask_cents == 96
    assert event.markets[0].no_bid_size == Decimal("455.00")
    assert event.markets[0].fingerprints.contract
    assert len(event.markets[0].fingerprints.contract) == 64
    assert json.loads(event.markets[0].raw_payload_json)["ticker"].endswith("T82")
    assert factory.kwargs["timeout"].total == 8
    assert "headers" not in factory.kwargs
    assert all(call["allow_redirects"] is False for call in factory.calls)
    assert factory.calls[0]["params"] == {
        "series_ticker": "KXHIGHNY",
        "status": "open",
        "with_nested_markets": "true",
        "limit": 200,
    }
    assert factory.calls[1]["params"] == {"event_ticker": "KXHIGHNY-26JUL13", "limit": 1000}


@pytest.mark.asyncio
async def test_normalization_tolerates_unrelated_live_payload_keys() -> None:
    events = _fixture("kalshi_events_page_1.json")
    markets = _fixture("kalshi_markets_page_1.json")
    events["milestones"] = []
    events["events"][0]["category"] = "Climate and Weather"
    events["events"][0]["markets"][0]["subtitle"] = "81 or below"
    markets["unrelated"] = {"future": True}
    markets["markets"][0]["price_level_structure"] = "linear_cent"
    reader, _ = _reader(events=events, markets=markets)

    normalized = await reader.list_active_events(series_ticker="KXHIGHNY")

    assert len(normalized) == 1
    assert len(normalized[0].markets) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_market_status", ["active", "open"])
async def test_event_readers_share_canonical_open_status(
    raw_market_status: str,
) -> None:
    events_payload = _fixture("kalshi_events_page_1.json")
    list_markets = _fixture("kalshi_markets_page_1.json")
    detail_markets = _fixture("kalshi_markets_page_1.json")
    for payload in (events_payload["events"][0]["markets"], list_markets["markets"]):
        for market in payload:
            market["status"] = raw_market_status
    for market in detail_markets["markets"]:
        market["status"] = raw_market_status
    list_reader, _ = _reader(events=events_payload, markets=list_markets)
    detail_reader, _ = _reader(
        event=_fixture("kalshi_event.json"),
        markets=detail_markets,
    )

    listed = await list_reader.list_active_events(series_ticker="KXHIGHNY")
    detailed = await detail_reader.get_event(event_ticker="KXHIGHNY-26JUL13")

    assert listed[0].status == detailed.status == "open"


@pytest.mark.asyncio
async def test_get_event_uses_fixed_path_and_complete_paginated_markets() -> None:
    first = _fixture("kalshi_markets_page_1.json")
    second = {"markets": [first["markets"].pop()], "cursor": ""}
    first["cursor"] = "next"
    reader, factory = _reader(
        event=_fixture("kalshi_event.json"),
        markets=[FakeResponse(first), FakeResponse(second)],
    )

    event = await reader.get_event(event_ticker="KXHIGHNY-26JUL13")

    assert len(event.markets) == 3
    assert event.market_tickers == (
        "KXHIGHNY-26JUL13-T82",
        "KXHIGHNY-26JUL13-B86.5",
        "KXHIGHNY-26JUL13-T89",
    )
    assert factory.calls[0]["url"] == EVENT_URL
    assert factory.calls[0]["params"] == {"with_nested_markets": "true"}
    assert factory.calls[2]["params"]["cursor"] == "next"


@pytest.mark.asyncio
async def test_settled_event_rejects_nested_sibling_missing_from_markets_rows() -> None:
    event_payload = _fixture("kalshi_event.json")
    event_payload["event"]["markets"].append(
        {
            "ticker": "KXHIGHNY-26JUL13-B84.5",
            "event_ticker": "KXHIGHNY-26JUL13",
        }
    )
    markets = _fixture("kalshi_markets_page_1.json")
    for market in markets["markets"]:
        market["status"] = "settled"
        market["result"] = "no"
    reader, _ = _reader(
        event=event_payload,
        markets=markets,
    )

    with pytest.raises(KalshiPublicMarketDataError, match="enumeration"):
        await reader.get_event(event_ticker="KXHIGHNY-26JUL13")


@pytest.mark.asyncio
async def test_rejects_markets_row_missing_from_nested_enumeration() -> None:
    markets = _fixture("kalshi_markets_page_1.json")
    extra = deepcopy(markets["markets"][1])
    extra.update(
        ticker="KXHIGHNY-26JUL13-B84.5",
        cap_strike=85,
        title="Will the high temp in NYC be 84-85 degrees on Jul 13, 2026?",
    )
    markets["markets"].append(extra)
    reader, _ = _reader(event=_fixture("kalshi_event.json"), markets=markets)

    with pytest.raises(KalshiPublicMarketDataError, match="enumeration"):
        await reader.get_event(event_ticker="KXHIGHNY-26JUL13")


@pytest.mark.asyncio
async def test_rejects_duplicate_authoritative_nested_ticker() -> None:
    event_payload = _fixture("kalshi_event.json")
    event_payload["event"]["markets"].append(
        deepcopy(event_payload["event"]["markets"][0])
    )
    reader, _ = _reader(event=event_payload)

    with pytest.raises(KalshiPublicMarketDataError, match="duplicate market"):
        await reader.get_event(event_ticker="KXHIGHNY-26JUL13")


@pytest.mark.asyncio
async def test_accepts_markets_rows_reordered_from_authoritative_nested_enumeration() -> None:
    event_payload = _fixture("kalshi_event.json")
    nested_tickers = tuple(
        market["ticker"] for market in reversed(event_payload["event"]["markets"])
    )
    event_payload["event"]["markets"].reverse()
    markets = _fixture("kalshi_markets_page_1.json")
    reader, _ = _reader(event=event_payload, markets=markets)

    event = await reader.get_event(event_ticker="KXHIGHNY-26JUL13")

    assert event.market_tickers == nested_tickers
    assert tuple(market.market_ticker for market in event.markets) != nested_tickers
    assert set(event.market_tickers) == {
        market.market_ticker for market in event.markets
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["events"][0].update(series_ticker="OTHER"),
        lambda payload: payload["events"][0].update(event_ticker="OTHER-26JUL13"),
        lambda payload: payload["events"][0]["markets"][0].update(event_ticker="OTHER"),
        lambda payload: payload["events"][0]["markets"][0].update(ticker="OTHER-T82"),
    ],
)
async def test_rejects_returned_attribution_outside_requested_series(mutation) -> None:
    payload = _fixture("kalshi_events_page_1.json")
    mutation(payload)
    reader, _ = _reader(events=payload)
    with pytest.raises(KalshiPublicMarketDataError):
        await reader.list_active_events(series_ticker="KXHIGHNY")


@pytest.mark.asyncio
async def test_rejects_duplicate_event_market_and_cursor() -> None:
    events = _fixture("kalshi_events_page_1.json")
    events["cursor"] = "same"
    reader, _ = _reader(
        events=[FakeResponse(events), FakeResponse({"events": [], "cursor": "same"})]
    )
    with pytest.raises(KalshiPublicMarketDataError, match="cursor"):
        await reader.list_active_events(series_ticker="KXHIGHNY")

    markets = _fixture("kalshi_markets_page_1.json")
    markets["markets"].append(deepcopy(markets["markets"][0]))
    reader, _ = _reader(event=_fixture("kalshi_event.json"), markets=markets)
    with pytest.raises(KalshiPublicMarketDataError, match="duplicate market"):
        await reader.get_event(event_ticker="KXHIGHNY-26JUL13")

    invalid_result = _fixture("kalshi_markets_page_1.json")
    invalid_result["markets"][0]["result"] = "void"
    reader, _ = _reader(event=_fixture("kalshi_event.json"), markets=invalid_result)
    with pytest.raises(KalshiPublicMarketDataError, match="result"):
        await reader.get_event(event_ticker="KXHIGHNY-26JUL13")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"events": "bad", "cursor": ""}, {"events": [], "cursor": 7}])
async def test_rejects_malformed_event_pages(payload: Any) -> None:
    reader, _ = _reader(events=payload)
    with pytest.raises(KalshiPublicMarketDataError):
        await reader.list_active_events(series_ticker="KXHIGHNY")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        FakeResponse({}, status=503),
        FakeResponse({}, status=302, headers={"Location": "https://evil.example/events"}),
    ],
)
async def test_fails_closed_on_http_errors_and_redirects(response: FakeResponse) -> None:
    reader, _ = _reader(events=[response])
    with pytest.raises(KalshiPublicMarketDataError):
        await reader.list_active_events(series_ticker="KXHIGHNY")


@pytest.mark.asyncio
async def test_fails_closed_on_transport_timeout() -> None:
    reader, _ = _reader(events=[TimeoutError("eight seconds elapsed")])
    with pytest.raises(KalshiPublicMarketDataError, match="request failed"):
        await reader.list_active_events(series_ticker="KXHIGHNY")


def test_rejects_unsafe_identifiers_before_http() -> None:
    reader, factory = _reader()
    with pytest.raises(KalshiPublicMarketDataError):
        reader._event_url("../orders")
    assert factory.calls == []
