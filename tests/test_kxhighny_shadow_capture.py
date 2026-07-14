from __future__ import annotations

import asyncio
import ast
import builtins
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import logging
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import pytest

from kalshi.public_market_data import (
    KalshiPublicMarketDataError,
    KalshiPublicMarketDataReader,
)
import tasks.kxhighny_shadow_capture as capture_module
from tasks.kxhighny_shadow_validation import canonical_sha256
from tasks.kxhighny_shadow_capture import WeatherShadowCaptureTask
import tasks.weather_shadow_store as weather_store_module
from tasks.weather_shadow_store import WeatherShadowStore
from utils.output_paths import EVIDENCE_STORE_DB, PAPER_TRADES_DB, WEATHER_SHADOW_DB
from weather.nws_public_client import NwsPublicClient
from weather.shadow_models import (
    Fingerprints,
    NwsCapturePayloads,
    NwsGridForecast,
    NwsHourlyForecast,
    NwsObservation,
    RetrievedEvent,
    RetrievedMarket,
)

UTC = timezone.utc


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def capture_key(event_ticker: str, model_version: str) -> str:
    return canonical_sha256(
        {
            "event_ticker": event_ticker,
            "horizon_bucket": "T-1h",
            "model_version": model_version,
        }
    )


def event(
    event_ticker: str = "KXHIGHNY-26JUL12",
    *,
    retrieved_at: datetime = dt("2026-07-12T17:30:02Z"),
    status: str = "open",
) -> RetrievedEvent:
    close_time = dt("2026-07-12T18:30:03Z")
    bounds = ((None, 69, True, False), (70, 74, False, False), (75, None, False, True))
    markets = tuple(
        RetrievedMarket(
            market_ticker=f"{event_ticker}-{suffix}",
            event_ticker=event_ticker,
            status=status,
            close_time=close_time,
            lower_bound_f=lower,
            upper_bound_f=upper,
            is_lower_tail=is_lower,
            is_upper_tail=is_upper,
            fingerprints=Fingerprints(
                contract=f"contract-{suffix}",
                rules_source="rules-v1",
                settlement_source="settlement-v1",
            ),
            yes_bid_cents=20,
            yes_ask_cents=21,
            no_bid_cents=79,
            no_ask_cents=80,
            yes_bid_size=Decimal("10"),
            yes_ask_size=Decimal("11"),
            no_bid_size=Decimal("11"),
            no_ask_size=Decimal("10"),
            last_price_cents=20,
            volume=Decimal("100"),
            price_retrieved_at=retrieved_at,
            raw_payload_json=f'{{"ticker":"{event_ticker}-{suffix}"}}',
        )
        for suffix, (lower, upper, is_lower, is_upper) in zip(
            ("LOW", "MID", "HIGH"), bounds, strict=True
        )
    )
    return RetrievedEvent(
        event_ticker=event_ticker,
        status=status,
        close_time=close_time,
        market_tickers=tuple(item.market_ticker for item in markets),
        markets=markets,
        retrieved_at=retrieved_at,
    )


def due_event(event_ticker: str, close_time: datetime) -> RetrievedEvent:
    source = event(event_ticker)
    return replace(
        source,
        close_time=close_time,
        markets=tuple(
            replace(market, close_time=close_time) for market in source.markets
        ),
    )


def weather_payloads(
    *, retrieved_at: datetime = dt("2026-07-12T17:30:01Z")
) -> NwsCapturePayloads:
    issued_at = dt("2026-07-12T12:00:00Z")
    return NwsCapturePayloads(
        grid=(
            NwsGridForecast(
                valid_start=dt("2026-07-12T04:00:00Z"),
                valid_end=dt("2026-07-13T04:00:00Z"),
                issued_at=issued_at,
                temperature_c=Decimal("27"),
                source_id="grid-v1",
            ),
        ),
        hourly=(
            NwsHourlyForecast(
                start_time=dt("2026-07-12T16:00:00Z"),
                issued_at=issued_at,
                temperature_c=Decimal("26"),
                source_id="hourly-v1",
            ),
        ),
        observations=(
            NwsObservation(
                station_id="KNYC",
                measured_at=dt("2026-07-12T17:00:00Z"),
                temperature_c=Decimal("25"),
                source_id="observation-v1",
            ),
        ),
        retrieved_at=retrieved_at,
        grid_payload_json='{"grid":"public"}',
        hourly_payload_json='{"hourly":"public"}',
        observations_payload_json='{"observations":"public"}',
    )


class FakeStore:
    def __init__(self, claimed: set[str] | None = None) -> None:
        self.claimed = set() if claimed is None else set(claimed)
        self.initialized = 0
        self.batches = []

    async def initialize(self) -> None:
        self.initialized += 1

    async def capture_key_state(self, key: str) -> SimpleNamespace:
        return SimpleNamespace(claimed=key in self.claimed, snapshot_id="existing" if key in self.claimed else None)

    async def append_capture(self, batch: object) -> SimpleNamespace:
        self.batches.append(batch)
        self.claimed.add(batch.capture_key)
        return SimpleNamespace(status="inserted", snapshot_id=batch.snapshot_id)

    async def list_outcome_targets(self, now: datetime) -> tuple[object, ...]:
        return ()


class FakeMarkets:
    def __init__(self, events: tuple[RetrievedEvent, ...], calls: list[str] | None = None) -> None:
        self.events = events
        self.calls = [] if calls is None else calls
        self.series_calls = 0
        self.get_calls = 0
        self.active = 0
        self.max_active = 0

    async def list_active_events(self, *, series_ticker: str) -> tuple[RetrievedEvent, ...]:
        assert series_ticker == "KXHIGHNY"
        self.series_calls += 1
        self.calls.append("series")
        return self.events

    async def get_event(self, *, event_ticker: str) -> RetrievedEvent:
        self.get_calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(f"quotes:{event_ticker}")
        try:
            await asyncio.sleep(0)
            return next(item for item in self.events if item.event_ticker == event_ticker)
        finally:
            self.active -= 1


class FakeWeather:
    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls = [] if calls is None else calls
        self.fetch_calls = 0
        self.active = 0
        self.max_active = 0

    async def fetch_capture_bundle(
        self, *, target_date: date, station_id: str = "KNYC"
    ) -> NwsCapturePayloads:
        assert target_date == date(2026, 7, 12)
        assert station_id == "KNYC"
        self.fetch_calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(f"weather:{target_date.isoformat()}")
        try:
            await asyncio.sleep(0)
            return weather_payloads()
        finally:
            self.active -= 1


class IncompleteCrossEndpointMarkets(FakeMarkets):
    async def get_event(self, *, event_ticker: str) -> RetrievedEvent:
        self.get_calls += 1
        raise KalshiPublicMarketDataError(
            "event nested enumeration does not match markets response"
        )


def task(
    *,
    store: object,
    markets: object,
    weather: object,
    now: object = lambda: dt("2026-07-12T17:30:03Z"),
    sleep: object = asyncio.sleep,
    model_version: str = "model-test-v1",
) -> WeatherShadowCaptureTask:
    return WeatherShadowCaptureTask(
        store=store,
        capture_markets=markets,
        capture_weather=weather,
        label_markets=object(),
        label_weather=object(),
        model_version=model_version,
        fee_schedule_version="fee-test-v1",
        now=now,
        sleep=sleep,
    )


@pytest.mark.asyncio
async def test_incomplete_cross_endpoint_ladder_never_reaches_capture_build_or_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_event = event()
    store = FakeStore()
    markets = IncompleteCrossEndpointMarkets((source_event,))
    weather = FakeWeather()

    def build_bomb(*args: object, **kwargs: object) -> object:
        raise AssertionError("incomplete ladder reached capture build")

    monkeypatch.setattr(capture_module, "build_capture_batch", build_bomb)

    result = await task(store=store, markets=markets, weather=weather).capture_event(
        source_event, "T-1h"
    )

    assert result.captured is False
    assert result.reason == "ineligible"
    assert markets.get_calls == 1
    assert weather.fetch_calls == 1
    assert store.batches == []


@pytest.mark.asyncio
async def test_already_claimed_capture_skips_all_network_work() -> None:
    model_version = "model-test-v1"
    source_event = event()
    store = FakeStore({capture_key(source_event.event_ticker, model_version)})
    markets = FakeMarkets((source_event,))
    weather = FakeWeather()

    result = await task(store=store, markets=markets, weather=weather).capture_event(
        source_event, "T-1h"
    )

    assert result.captured is False
    assert result.reason == "already_claimed"
    assert markets.series_calls == markets.get_calls == weather.fetch_calls == 0
    assert store.batches == []


@pytest.mark.asyncio
async def test_weather_completes_before_quotes_and_batch_uses_finish_as_as_of() -> None:
    calls: list[str] = []
    source_event = event()
    store = FakeStore()
    markets = FakeMarkets((source_event,), calls)
    weather = FakeWeather(calls)
    times = iter((dt("2026-07-12T17:29:58Z"), dt("2026-07-12T17:30:03Z")))

    result = await task(
        store=store, markets=markets, weather=weather, now=lambda: next(times)
    ).capture_event(source_event, "T-1h")

    assert result.captured is True
    assert calls == ["weather:2026-07-12", "quotes:KXHIGHNY-26JUL12"]
    batch = store.batches[0]
    assert batch.as_of == batch.capture_finished_at == dt("2026-07-12T17:30:03Z")
    assert batch.features.weather_retrieved_at <= min(
        quote.price_retrieved_at for quote in batch.quotes
    )


@pytest.mark.asyncio
async def test_capture_accepts_active_refresh_normalized_by_public_reader() -> None:
    source_event = event(status="open")
    active_refresh = event(status="active")

    class ReaderContractMarkets(FakeMarkets):
        async def get_event(self, *, event_ticker: str) -> RetrievedEvent:
            assert event_ticker == active_refresh.event_ticker
            self.get_calls += 1
            return KalshiPublicMarketDataReader._normalize_event(
                active_refresh.event_ticker,
                active_refresh.markets,
                active_refresh.retrieved_at,
                authoritative_market_tickers=active_refresh.market_tickers,
                requested_open=False,
            )

    store = FakeStore()
    markets = ReaderContractMarkets((source_event,))
    times = iter((dt("2026-07-12T17:29:58Z"), dt("2026-07-12T17:30:03Z")))

    result = await task(
        store=store,
        markets=markets,
        weather=FakeWeather(),
        now=lambda: next(times),
    ).capture_event(source_event, "T-1h")

    assert result.captured is True
    assert markets.get_calls == 1
    assert len(store.batches) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_market_status", ["closed", "settled"])
async def test_capture_rejects_non_open_refresh_normalized_by_public_reader(
    raw_market_status: str,
) -> None:
    source_event = event(status="open")
    refreshed = event(status=raw_market_status)

    class ReaderContractMarkets(FakeMarkets):
        async def get_event(self, *, event_ticker: str) -> RetrievedEvent:
            assert event_ticker == refreshed.event_ticker
            self.get_calls += 1
            return KalshiPublicMarketDataReader._normalize_event(
                refreshed.event_ticker,
                refreshed.markets,
                refreshed.retrieved_at,
                authoritative_market_tickers=refreshed.market_tickers,
                requested_open=False,
            )

    store = FakeStore()
    markets = ReaderContractMarkets((source_event,))
    times = iter((dt("2026-07-12T17:29:58Z"), dt("2026-07-12T17:30:03Z")))

    result = await task(
        store=store,
        markets=markets,
        weather=FakeWeather(),
        now=lambda: next(times),
    ).capture_event(source_event, "T-1h")

    assert result.captured is False
    assert result.reason == "ineligible"
    assert markets.get_calls == 1
    assert store.batches == []


@pytest.mark.asyncio
async def test_cycle_fetches_series_once_and_processes_at_most_two_events_sequentially() -> None:
    source_event = event()
    cycle_close = dt("2026-07-12T18:29:58Z")
    source_event = replace(
        source_event,
        close_time=cycle_close,
        markets=tuple(
            replace(market, close_time=cycle_close) for market in source_event.markets
        ),
    )
    markets = FakeMarkets((source_event,))
    weather = FakeWeather()
    store = FakeStore()
    times = iter(
        (
            dt("2026-07-12T17:29:58Z"),
            dt("2026-07-12T17:29:58Z"),
            dt("2026-07-12T17:30:03Z"),
        )
    )

    result = await task(
        store=store, markets=markets, weather=weather, now=lambda: next(times)
    ).run_capture_once()

    assert markets.series_calls == 1
    assert markets.get_calls <= 2
    assert weather.fetch_calls <= 2
    assert markets.max_active == weather.max_active == 1
    assert result.attempted <= 2
    assert result.captured == 1


@pytest.mark.asyncio
async def test_run_initializes_lazily_and_uses_300_second_cadence() -> None:
    store = FakeStore()
    markets = FakeMarkets(())
    weather = FakeWeather()
    stop_event = asyncio.Event()
    sleeps: list[float] = []

    async def stop_after_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        stop_event.set()

    capture = task(store=store, markets=markets, weather=weather, sleep=stop_after_sleep)
    assert store.initialized == 0

    await capture.run(stop_event)

    assert store.initialized == 1
    assert markets.series_calls == 1
    assert sleeps == [300]


@pytest.mark.asyncio
async def test_initialize_failure_is_sanitized_retried_and_does_not_cancel_sibling(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "private-init-path-and-key"

    class RetryStore(FakeStore):
        async def initialize(self) -> None:
            self.initialized += 1
            if self.initialized == 1:
                raise RuntimeError(secret)

    store = RetryStore()
    markets = FakeMarkets(())
    stop_event = asyncio.Event()
    sleeps: list[float] = []
    sibling_finished = asyncio.Event()

    async def cadence(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            stop_event.set()

    async def sibling() -> None:
        await asyncio.sleep(0)
        sibling_finished.set()

    caplog.set_level(logging.WARNING, logger="tasks.kxhighny_shadow_capture")
    async with asyncio.TaskGroup() as group:
        group.create_task(
            task(
                store=store,
                markets=markets,
                weather=FakeWeather(),
                sleep=cadence,
            ).run(stop_event)
        )
        group.create_task(sibling())

    assert sibling_finished.is_set()
    assert store.initialized == 2
    assert markets.series_calls == 1
    assert sleeps == [300, 300]
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "KXHIGHNY weather shadow failed stage=initialize error=RuntimeError"
    ]
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_initialize_cancellation_drains_before_propagating() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    class BlockingStore(FakeStore):
        async def initialize(self) -> None:
            started.set()
            try:
                await release.wait()
            finally:
                completed.set()

    owner = asyncio.create_task(
        task(
            store=BlockingStore(), markets=FakeMarkets(()), weather=FakeWeather()
        ).run()
    )
    await started.wait()
    owner.cancel()
    await asyncio.sleep(0)

    assert owner.done() is False
    assert completed.is_set() is False
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await owner
    assert completed.is_set()


@pytest.mark.asyncio
async def test_stop_set_during_initialize_skips_capture_and_all_network() -> None:
    stop_event = asyncio.Event()

    class StopDuringInitializeStore(FakeStore):
        async def initialize(self) -> None:
            self.initialized += 1
            stop_event.set()

    store = StopDuringInitializeStore()
    markets = FakeMarkets((event(),))
    weather = FakeWeather()

    await task(store=store, markets=markets, weather=weather).run(stop_event)

    assert store.initialized == 1
    assert markets.series_calls == markets.get_calls == 0
    assert weather.fetch_calls == 0
    assert store.batches == []


@pytest.mark.asyncio
async def test_three_due_events_process_exactly_two_with_global_concurrency_one() -> None:
    calls: list[str] = []
    events = (
        due_event("KXHIGHNY-26JUL12", dt("2026-07-12T18:30:00Z")),
        due_event("KXHIGHNY-26JUL13", dt("2026-07-12T23:30:00Z")),
        due_event("KXHIGHNY-26JUL14", dt("2026-07-13T05:30:00Z")),
    )
    activity = SimpleNamespace(active=0, maximum=0)

    class BlockingMarkets(FakeMarkets):
        async def get_event(self, *, event_ticker: str) -> RetrievedEvent:
            self.get_calls += 1
            activity.active += 1
            activity.maximum = max(activity.maximum, activity.active)
            calls.append(f"quotes:{event_ticker}")
            try:
                await asyncio.sleep(0)
                return next(item for item in self.events if item.event_ticker == event_ticker)
            finally:
                activity.active -= 1

    class BlockingWeather(FakeWeather):
        async def fetch_capture_bundle(
            self, *, target_date: date, station_id: str = "KNYC"
        ) -> NwsCapturePayloads:
            self.fetch_calls += 1
            activity.active += 1
            activity.maximum = max(activity.maximum, activity.active)
            calls.append(f"weather:{target_date.isoformat()}")
            try:
                await asyncio.sleep(0)
                return weather_payloads()
            finally:
                activity.active -= 1

    markets = BlockingMarkets(events)
    weather = BlockingWeather()
    times = iter(
        (
            dt("2026-07-12T17:30:00Z"),
            dt("2026-07-12T17:29:58Z"),
            dt("2026-07-12T17:30:03Z"),
            dt("2026-07-12T17:30:04Z"),
            dt("2026-07-12T17:30:05Z"),
        )
    )

    result = await task(
        store=FakeStore(),
        markets=markets,
        weather=weather,
        now=lambda: next(times),
    ).run_capture_once()

    assert result.attempted == 2
    assert markets.get_calls == weather.fetch_calls == 2
    assert activity.maximum == 1
    assert calls == [
        "weather:2026-07-12",
        "quotes:KXHIGHNY-26JUL12",
        "weather:2026-07-13",
        "quotes:KXHIGHNY-26JUL13",
    ]


@pytest.mark.asyncio
async def test_second_event_budget_expiry_discards_first_staged_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tasks.kxhighny_shadow_capture as capture_module

    events = (
        due_event("KXHIGHNY-26JUL12", dt("2026-07-12T18:30:00Z")),
        due_event("KXHIGHNY-26JUL13", dt("2026-07-12T23:30:00Z")),
    )
    second_started = asyncio.Event()
    second_cancelled = asyncio.Event()

    class SecondWeatherBlocks(FakeWeather):
        async def fetch_capture_bundle(
            self, *, target_date: date, station_id: str = "KNYC"
        ) -> NwsCapturePayloads:
            self.fetch_calls += 1
            if self.fetch_calls == 1:
                return weather_payloads()
            second_started.set()
            try:
                await asyncio.Future()
            finally:
                second_cancelled.set()

    times = iter(
        (
            dt("2026-07-12T17:30:00Z"),
            dt("2026-07-12T17:29:58Z"),
            dt("2026-07-12T17:30:03Z"),
            dt("2026-07-12T17:30:04Z"),
        )
    )
    store = FakeStore()
    markets = FakeMarkets(events)
    monkeypatch.setattr(capture_module, "NETWORK_BUILD_BUDGET_SECONDS", 0.01)

    result = await task(
        store=store,
        markets=markets,
        weather=SecondWeatherBlocks(),
        now=lambda: next(times),
    ).run_capture_once()

    assert second_started.is_set() and second_cancelled.is_set()
    assert markets.get_calls == 1
    assert result.attempted == 2
    assert result.captured == 0
    assert store.batches == []


@pytest.mark.asyncio
async def test_network_build_budget_expiry_cancels_work_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tasks.kxhighny_shadow_capture as capture_module

    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingWeather(FakeWeather):
        async def fetch_capture_bundle(
            self, *, target_date: date, station_id: str = "KNYC"
        ) -> NwsCapturePayloads:
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

    monkeypatch.setattr(capture_module, "NETWORK_BUILD_BUDGET_SECONDS", 0.01)
    store = FakeStore()
    capture = task(store=store, markets=FakeMarkets((event(),)), weather=BlockingWeather())

    result = await capture.run_capture_once()

    assert started.is_set() and cancelled.is_set()
    assert result.captured == 0
    assert store.batches == []


@pytest.mark.asyncio
async def test_cancelled_caller_awaits_named_shielded_persistence_before_propagating() -> None:
    append_started = asyncio.Event()
    allow_append = asyncio.Event()

    class SlowStore(FakeStore):
        async def append_capture(self, batch: object) -> SimpleNamespace:
            append_started.set()
            await allow_append.wait()
            return await super().append_capture(batch)

    store = SlowStore()
    times = iter((dt("2026-07-12T17:29:58Z"), dt("2026-07-12T17:30:03Z")))
    capture = task(
        store=store,
        markets=FakeMarkets((event(),)),
        weather=FakeWeather(),
        now=lambda: next(times),
    )
    owner = asyncio.create_task(capture.capture_event(event(), "T-1h"))
    await append_started.wait()
    persistence = [
        item
        for item in asyncio.all_tasks()
        if item is not owner and item.get_name().startswith("kxhighny-shadow-persist:")
    ]
    assert len(persistence) == 1

    owner.cancel()
    await asyncio.sleep(0)
    assert not owner.done()
    allow_append.set()
    with pytest.raises(asyncio.CancelledError):
        await owner

    assert len(store.batches) == 1
    await asyncio.sleep(0.02)
    assert len(store.batches) == 1
    assert persistence[0].done()


def test_capture_module_has_no_trading_research_auth_or_runtime_path_dependencies() -> None:
    source_path = Path(__file__).parents[1] / "tasks/kxhighny_shadow_capture.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imports <= {
        "__future__",
        "asyncio",
        "collections.abc",
        "dataclasses",
        "datetime",
        "kalshi.public_market_data",
        "logging",
        "tasks.kxhighny_shadow_validation",
        "tasks.weather_shadow_store",
        "weather.nws_public_client",
        "weather.shadow_models",
    }


@pytest.mark.asyncio
async def test_capture_behavior_avoids_forbidden_modules_and_production_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    forbidden_modules = {
        "analysis.decision_blender",
        "analysis.kelly",
        "analysis.llm_dedup_cache",
        "analysis.research_gate",
        "kalshi.rest_client",
        "kalshi.websocket_client",
        "polymarket.auth",
        "tasks.blend_task",
        "tasks.research_dossier",
        "tasks.research_paper_admission",
        "tasks.research_prewarm_task",
        "trading.executor",
        "trading.paper_trader",
    }
    forbidden_calls: list[str] = []

    class BombModule(ModuleType):
        def __getattr__(self, attribute: str) -> object:
            forbidden_calls.append(f"{self.__name__}.{attribute}")
            raise AssertionError(f"forbidden dependency used: {self.__name__}.{attribute}")

    for module_name in forbidden_modules:
        monkeypatch.setitem(sys.modules, module_name, BombModule(module_name))

    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name in forbidden_modules:
            forbidden_calls.append(f"import:{name}")
            raise AssertionError(f"forbidden dependency imported: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    temp_evidence = tmp_path / "evidence_store.db"
    temp_paper = tmp_path / "paper_trades.db"
    with sqlite3.connect(temp_evidence), sqlite3.connect(temp_paper):
        pass

    canonical = {
        EVIDENCE_STORE_DB.resolve(),
        PAPER_TRADES_DB.resolve(),
        WEATHER_SHADOW_DB.resolve(),
    }
    connected: list[Path] = []
    original_connect = sqlite3.connect

    def guarded_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        path = Path(database).resolve()
        if path in canonical:
            raise AssertionError(f"canonical production DB opened: {path.name}")
        connected.append(path)
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(weather_store_module.sqlite3, "connect", guarded_connect)
    temp_weather = tmp_path / "weather_shadow.db"
    store = WeatherShadowStore(db_path=temp_weather)
    await store.initialize()
    times = iter((dt("2026-07-12T17:29:58Z"), dt("2026-07-12T17:30:03Z")))

    result = await task(
        store=store,
        markets=FakeMarkets((event(),)),
        weather=FakeWeather(),
        now=lambda: next(times),
    ).capture_event(event(), "T-1h")

    assert result.captured is True
    assert forbidden_calls == []
    assert connected and set(connected) == {temp_weather.resolve()}
    assert temp_evidence.exists() and temp_paper.exists()
    with original_connect(temp_weather) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM research_weather_shadow_snapshots"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_public_capture_clients_construct_exact_eight_second_timeouts() -> None:
    class TimeoutProbe(Exception):
        pass

    class ProbeFactory:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def __call__(self, **kwargs: object) -> object:
            self.kwargs = kwargs
            raise TimeoutProbe

    kalshi_factory = ProbeFactory()
    weather_factory = ProbeFactory()
    kalshi = KalshiPublicMarketDataReader(session_factory=kalshi_factory)
    weather = NwsPublicClient(session_factory=weather_factory)

    with pytest.raises(TimeoutProbe):
        await kalshi.list_active_events(series_ticker="KXHIGHNY")
    with pytest.raises(TimeoutProbe):
        await weather.fetch_capture_bundle(target_date=date(2026, 7, 12))

    kalshi_timeout = kalshi_factory.kwargs["timeout"]
    weather_timeout = weather_factory.kwargs["timeout"]
    assert kalshi_timeout.__class__.__name__ == "ClientTimeout"
    assert weather_timeout.__class__.__name__ == "ClientTimeout"
    assert kalshi_timeout.total == weather_timeout.total == 8
