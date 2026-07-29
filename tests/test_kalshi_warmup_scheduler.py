from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests

import analysis.market_matcher as market_matcher_module
from analysis.market_matcher import MarketMatcher
from kalshi.rest_client import (
    KalshiRestClient,
    KalshiSeriesDiscoveryError,
    RequestStartGate,
)


def _market(ticker: str, series_ticker: str):
    from kalshi import KalshiMarket

    return KalshiMarket(
        ticker=ticker,
        title=f"Will {series_ticker} have an eligible market?",
        subtitle="",
        status="active",
        yes_bid=44.0,
        yes_ask=46.0,
        yes_price=45.0,
        volume=100,
        open_interest=100,
        close_time=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        series_ticker=series_ticker,
        result="",
        yes_bid_cents=44,
        yes_ask_cents=46,
        no_bid_cents=54,
        no_ask_cents=56,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


class _RecordingGate:
    def __init__(self) -> None:
        self.calls = 0
        self.deferred: list[float] = []

    def wait_for_slot(self) -> float:
        self.calls += 1
        return 0.0

    def defer_for(self, delay: float) -> None:
        self.deferred.append(delay)


def _http_error(status: int, retry_after: str | None = None) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return requests.HTTPError(response=response)


def test_request_start_gate_spaces_shared_reservations() -> None:
    clock = _Clock()
    gate = RequestStartGate(
        interval_seconds=0.12,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    slots = [gate.wait_for_slot() for _ in range(3)]

    assert slots == pytest.approx([0.0, 0.12, 0.24])
    assert clock.sleeps == pytest.approx([0.12, 0.12])


def test_request_start_gate_defers_every_reader_after_a_cooldown() -> None:
    clock = _Clock()
    gate = RequestStartGate(
        interval_seconds=0.12,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert gate.wait_for_slot() == pytest.approx(0.0)
    gate.defer_for(2.0)

    assert gate.wait_for_slot() == pytest.approx(2.0)
    assert clock.sleeps == pytest.approx([2.0])


def test_request_start_gate_resamples_the_clock_after_lock_contention() -> None:
    class AdvancingLock:
        def __init__(self, clock: _Clock) -> None:
            self._clock = clock
            self._entries = 0

        def __enter__(self):
            self._entries += 1
            if self._entries == 1:
                self._clock.now = 1.0
            return self

        def __exit__(self, _exc_type, _exc, _traceback) -> None:
            return None

    clock = _Clock()
    gate = RequestStartGate(
        interval_seconds=0.15,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    gate._lock = AdvancingLock(clock)  # noqa: SLF001

    gate.wait_for_slot()
    first_dispatch = clock.now
    gate.wait_for_slot()
    second_dispatch = clock.now

    assert second_dispatch - first_dispatch >= 0.15 - 1e-9


def test_public_market_readers_have_isolated_sessions_and_share_client_gate() -> None:
    client = KalshiRestClient()

    first = client.fork_public_market_reader()
    second = client.fork_public_market_reader()

    assert first._session is not second._session  # noqa: SLF001
    assert first._request_gate is client._request_gate  # noqa: SLF001
    assert second._request_gate is client._request_gate  # noqa: SLF001


def test_public_market_reader_retries_transient_attempts_through_the_gate() -> None:
    client = KalshiRestClient()
    reader = client.fork_public_market_reader()
    gate = _RecordingGate()
    reader._request_gate = gate  # noqa: SLF001
    delays: list[float] = []
    reader._sleep = delays.append  # noqa: SLF001
    response = MagicMock()
    response.status_code = 200
    response.text = "{}"
    response.json.return_value = {}
    reader._session.request = MagicMock(  # noqa: SLF001
        side_effect=[requests.Timeout("temporary timeout"), response]
    )

    page, cursor = reader.get_markets(
        status="open",
        series_ticker="KXIRAN",
        limit=200,
    )

    assert page == []
    assert cursor is None
    assert gate.calls == 2
    assert gate.deferred == pytest.approx([0.5])
    assert delays == []


def test_series_discovery_refuses_a_partial_page_sequence() -> None:
    client = KalshiRestClient()
    client._request = MagicMock(  # noqa: SLF001
        side_effect=[
            {"series": [{"ticker": "KXIRAN", "title": "Iran"}], "cursor": "next"},
            RuntimeError("simulated later page failure"),
        ]
    )

    with pytest.raises(KalshiSeriesDiscoveryError):
        client.get_all_series()


def test_series_discovery_refuses_to_silently_hit_its_page_cap() -> None:
    client = KalshiRestClient()
    client._request = MagicMock(  # noqa: SLF001
        return_value={"series": [{"ticker": "KXIRAN", "title": "Iran"}], "cursor": "next"}
    )

    with pytest.raises(KalshiSeriesDiscoveryError):
        client.get_all_series(max_pages=1)


def test_public_market_reader_cools_down_peers_after_an_exhausted_429() -> None:
    client = KalshiRestClient()
    reader = client.fork_public_market_reader()
    gate = _RecordingGate()
    reader._request_gate = gate  # noqa: SLF001
    reader._session.request = MagicMock(  # noqa: SLF001
        side_effect=[_http_error(429, "3")] * 4
    )

    with pytest.raises(requests.HTTPError):
        reader.get_markets(status="open", series_ticker="KXIRAN", limit=200)

    assert gate.deferred == pytest.approx([3.0, 3.0, 3.0, 3.0])


def test_normal_client_retry_cools_down_its_public_reader_peer() -> None:
    client = KalshiRestClient()
    gate = _RecordingGate()
    client._request_gate = gate  # noqa: SLF001
    reader = client.fork_public_market_reader()
    client._session = MagicMock()  # noqa: SLF001
    client._session.request.side_effect = [_http_error(429, "3")] * 4

    with pytest.raises(requests.HTTPError):
        client._request("GET", "/markets")  # noqa: SLF001

    assert reader._request_gate is gate  # noqa: SLF001
    assert gate.deferred == pytest.approx([3.0, 3.0, 3.0, 3.0])


class _WarmupReader:
    def __init__(self, owner: "_WarmupClient", reader_id: int) -> None:
        self._owner = owner
        self._reader_id = reader_id

    def get_markets(self, *, status: str, series_ticker: str, limit: int):
        assert status == "open"
        assert limit == 200
        return self._owner.fetch(self._reader_id, series_ticker)


class _WarmupClient:
    def __init__(self) -> None:
        self.get_all_series_calls = 0
        self.reader_ids: list[int] = []
        self.calls: list[tuple[int, str]] = []
        self._next_reader_id = 0
        self._active = 0
        self.max_active = 0
        self._lock = threading.Lock()
        self.iran_started = threading.Event()
        self.china_started = threading.Event()
        self.release_iran = threading.Event()

    def get_all_series(self):
        self.get_all_series_calls += 1
        return [
            {"ticker": "KXIRAN", "title": "Iran"},
            {"ticker": "KXCHINA", "title": "China"},
        ]

    def fork_public_market_reader(self):
        reader_id = self._next_reader_id
        self._next_reader_id += 1
        self.reader_ids.append(reader_id)
        return _WarmupReader(self, reader_id)

    def get_markets(self, **_kwargs):
        raise AssertionError("geo warmup must use isolated public readers")

    def fetch(self, reader_id: int, series_ticker: str):
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            self.calls.append((reader_id, series_ticker))
        try:
            if series_ticker == "KXIRAN":
                self.iran_started.set()
                assert self.china_started.wait(timeout=1)
                assert self.release_iran.wait(timeout=1)
                return [_market("KXIRAN-1", series_ticker)], None
            self.china_started.set()
            assert self.iran_started.wait(timeout=1)
            return [_market("KXCHINA-1", series_ticker)], None
        finally:
            with self._lock:
                self._active -= 1


class _FailingWarmupClient:
    def __init__(self) -> None:
        self.get_all_series_calls = 0

    def get_all_series(self):
        self.get_all_series_calls += 1
        return [
            {"ticker": "KXIRAN", "title": "Iran"},
            {"ticker": "KXCHINA", "title": "China"},
        ]

    def fork_public_market_reader(self):
        return _WarmupReader(self, 0)

    def fetch(self, _reader_id: int, series_ticker: str):
        if series_ticker == "KXCHINA":
            raise RuntimeError("simulated series failure")
        return [_market("KXIRAN-1", series_ticker)], None


def test_geo_warmup_uses_isolated_workers_and_preserves_discovery_order() -> None:
    client = _WarmupClient()
    cache = MarketMatcher(client)._cache

    def release_when_both_workers_start() -> None:
        assert client.iran_started.wait(timeout=1)
        assert client.china_started.wait(timeout=1)
        client.release_iran.set()

    worker = threading.Thread(target=release_when_both_workers_start)
    worker.start()
    try:
        markets, series_count = cache._fetch_geo_markets()
    finally:
        worker.join(timeout=1)

    assert series_count == 2
    assert [market.ticker for market in markets] == ["KXIRAN-1", "KXCHINA-1"]
    assert client.max_active == 2
    assert len(set(client.reader_ids)) == 2
    assert {series for _, series in client.calls} == {"KXIRAN", "KXCHINA"}


@pytest.mark.asyncio
async def test_geo_cache_does_not_publish_partial_results_while_warmup_runs() -> None:
    client = _WarmupClient()
    cache = MarketMatcher(client)._cache

    refresh = asyncio.create_task(cache.get_markets())
    assert await asyncio.to_thread(client.iran_started.wait, 1)
    assert await asyncio.to_thread(client.china_started.wait, 1)
    assert cache.get_markets_snapshot() == []
    client.release_iran.set()

    markets = await refresh

    assert [market.ticker for market in markets] == ["KXIRAN-1", "KXCHINA-1"]


@pytest.mark.asyncio
async def test_geo_cache_refuses_partial_snapshot_after_worker_failure(caplog) -> None:
    cache = MarketMatcher(_FailingWarmupClient())._cache

    caplog.set_level(logging.WARNING, logger="market_matcher")
    markets = await cache.get_markets()

    assert markets == []
    assert cache.has_market_snapshot() is False
    assert any("Market cache refresh failed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_failed_geo_refresh_is_backed_off_before_the_next_full_scan(monkeypatch) -> None:
    clock = _Clock()
    clock.now = 100.0
    client = _FailingWarmupClient()
    monkeypatch.setattr(market_matcher_module.time, "monotonic", clock.monotonic)
    cache = MarketMatcher(client)._cache

    assert await cache.get_markets() == []
    assert await cache.get_markets() == []

    assert client.get_all_series_calls == 1
    clock.now += market_matcher_module._REFRESH_FAILURE_BACKOFF_SECONDS
    assert await cache.get_markets() == []
    assert client.get_all_series_calls == 2
