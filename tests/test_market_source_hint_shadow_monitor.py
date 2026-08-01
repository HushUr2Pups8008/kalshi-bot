"""Focused boundaries for the source-hint shadow-capture lane."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import logging
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from config import BotConfig
from feeds import NewsItem
import feeds.market_source_hint_shadow_monitor as monitor_module
from feeds.market_source_hint_shadow_monitor import MarketSourceHintShadowMonitor
from feeds.search_news_monitor import SEARCH_SEEN_STATE_PATH
from kalshi import KalshiMarket
import main
from polymarket.models import PolymarketMarket
from tasks.market_source_hint_shadow_store import (
    MarketSourceHintShadowObservation,
    MarketSourceHintShadowStore,
)
from trading.venue import Venue


def _kalshi_market(
    *,
    ticker: str = "KXTEST-26",
    series_ticker: str = "KXTEST",
    title: str = "Will a test event occur?",
) -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        title=title,
        yes_bid=0,
        yes_ask=0,
        yes_price=0,
        volume=0,
        open_interest=0,
        close_time="",
        status="open",
        series_ticker=series_ticker,
    )


def test_source_hint_shadow_capture_flag_defaults_off_and_parses_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_MARKET_SOURCE_HINT_SHADOW_CAPTURE", raising=False)
    assert BotConfig().enable_market_source_hint_shadow_capture is False

    monkeypatch.setenv("ENABLE_MARKET_SOURCE_HINT_SHADOW_CAPTURE", "true")
    assert BotConfig().enable_market_source_hint_shadow_capture is True


@pytest.mark.asyncio
async def test_shadow_capture_appends_tight_provenance_to_its_own_store_and_seen_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "data" / "market_source_hint_shadow.db"
    seen_path = tmp_path / "state" / "market_source_hint_shadow_seen_ids.json"
    store = MarketSourceHintShadowStore(db_path)
    await store.initialize()
    context_calls: list[tuple[object, object]] = []
    query_calls: list[tuple[object, int]] = []
    polled_urls: list[str] = []
    market = _kalshi_market()

    def build_context(received_market: object, metadata: object) -> object:
        context_calls.append((received_market, metadata))
        return object()

    def build_queries(context: object, *, max_queries: int) -> tuple[str, ...]:
        query_calls.append((context, max_queries))
        return ("site:official.example test event",)

    async def poller(url: str, callback: object, seen: object) -> None:
        polled_urls.append(url)
        await callback(
            NewsItem(
                headline="Official test update",
                url="https://official.example/update",
                source="Official Example",
                published=datetime(2026, 7, 31, tzinfo=timezone.utc),
                body="This body must never enter the evidence store.",
                item_id="a" * 64,
            )
        )

    monkeypatch.setattr(monitor_module, "build_market_contract_context", build_context)
    monkeypatch.setattr(monitor_module, "build_market_first_queries", build_queries)
    monitor = MarketSourceHintShadowMonitor(
        store=store,
        get_markets=lambda: (market,),
        get_series_metadata=lambda: {"KXTEST": "metadata"},
        seen_state_path=seen_path,
        feed_url_builders=(("fixture", lambda _query: "https://fixture.test/rss"),),
        feed_poller=poller,
        max_markets=1,
        max_queries_per_market=1,
        max_records_per_cycle=1,
        max_concurrency=1,
        feed_timeout_seconds=1,
        now=lambda: datetime(2026, 7, 31, 12, tzinfo=timezone.utc),
    )

    result = await monitor.run_once()
    rows = store.read_records()

    assert result.records_captured == 1
    assert context_calls == [(market, "metadata")]
    assert query_calls and query_calls[0][1] == 1
    assert polled_urls == ["https://fixture.test/rss"]
    assert seen_path.is_file()
    assert seen_path != SEARCH_SEEN_STATE_PATH
    assert len(rows) == 1
    row = rows[0]
    assert row.ticker == "KXTEST-26"
    assert row.source_hint_query == "site:official.example test event"
    assert row.source_hint_domain == "official.example"
    assert row.item_url == "https://official.example/update"
    assert not hasattr(row, "body")


@pytest.mark.asyncio
async def test_shadow_capture_fails_closed_for_polymarket_and_unknown_markets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "data" / "market_source_hint_shadow.db"
    store = MarketSourceHintShadowStore(db_path)
    await store.initialize()
    kalshi_market = _kalshi_market()
    polymarket_market = PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id="pm-test-event",
        title="Will a Polymarket test event occur?",
        status="open",
        yes_ask_cents=50,
        no_ask_cents=50,
        volume_dollars=0.0,
        open_interest_dollars=0.0,
        close_time="",
    )
    unknown_market = SimpleNamespace(
        ticker="UNKNOWN-26",
        title="Unknown venue event",
        venue="unknown_venue",
    )
    context_tickers: list[str] = []

    def build_context(received_market: object, metadata: object) -> object:
        context_tickers.append(str(getattr(received_market, "ticker", "")))
        return object()

    monkeypatch.setattr(monitor_module, "build_market_contract_context", build_context)
    monkeypatch.setattr(
        monitor_module,
        "build_market_first_queries",
        lambda *_args, **_kwargs: ("site:official.example test event",),
    )

    async def poller(url: str, callback: object, seen: object) -> None:
        await callback(
            NewsItem(
                headline="Official test update",
                url="https://official.example/update",
                source="Official Example",
                published=datetime(2026, 7, 31, tzinfo=timezone.utc),
                item_id="d" * 64,
            )
        )

    monitor = MarketSourceHintShadowMonitor(
        store=store,
        get_markets=lambda: (kalshi_market, polymarket_market, unknown_market),
        get_series_metadata=lambda: {"KXTEST": "metadata"},
        seen_state_path=tmp_path / "state" / "seen.json",
        feed_url_builders=(("fixture", lambda _query: "https://fixture.test/rss"),),
        feed_poller=poller,
        max_markets=3,
        max_queries_per_market=1,
        max_records_per_cycle=3,
        max_concurrency=1,
        feed_timeout_seconds=1,
    )

    result = await monitor.run_once()
    rows = store.read_records()

    assert context_tickers == ["KXTEST-26"]
    assert result.queries_attempted == 1
    assert result.records_captured == 1
    assert [row.ticker for row in rows] == ["KXTEST-26"]


@pytest.mark.asyncio
async def test_shadow_capture_timeout_and_store_failure_are_contained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingStore:
        async def append(self, observation: object) -> None:
            raise sqlite3.OperationalError("locked")

    market = _kalshi_market()

    monkeypatch.setattr(monitor_module, "build_market_contract_context", lambda *_args: object())
    monkeypatch.setattr(
        monitor_module,
        "build_market_first_queries",
        lambda *_args, **_kwargs: ("site:official.example test event",),
    )

    async def hanging_poller(url: str, callback: object, seen: object) -> None:
        await callback(
            NewsItem(
                headline="Official test update",
                url="https://official.example/update",
                source="Official Example",
                published=datetime(2026, 7, 31, tzinfo=timezone.utc),
                item_id="c" * 64,
            )
        )
        await asyncio.Event().wait()

    monitor = MarketSourceHintShadowMonitor(
        store=FailingStore(),
        get_markets=lambda: (market,),
        get_series_metadata=lambda: {},
        seen_state_path=tmp_path / "state" / "seen.json",
        feed_url_builders=(("fixture", lambda _query: "https://fixture.test/rss"),),
        feed_poller=hanging_poller,
        max_markets=1,
        max_queries_per_market=1,
        max_records_per_cycle=1,
        max_concurrency=1,
        feed_timeout_seconds=0.01,
    )

    result = await monitor.run_once()

    assert result.timed_out_feeds == 1
    assert result.records_captured == 0
    assert result.store_failures == 1


@pytest.mark.asyncio
async def test_shadow_capture_consumes_no_more_markets_than_its_cycle_cap(
    tmp_path: Path,
) -> None:
    market = _kalshi_market()

    def markets() -> object:
        yield market
        pytest.fail("market provider exceeded the configured cap")

    monitor = MarketSourceHintShadowMonitor(
        store=SimpleNamespace(),
        get_markets=markets,
        get_series_metadata=lambda: {},
        seen_state_path=tmp_path / "state" / "seen.json",
        feed_url_builders=(),
        max_markets=1,
    )

    result = await monitor.run_once()

    assert result.markets_considered == 1


@pytest.mark.asyncio
async def test_shadow_capture_supervisor_contains_store_initialization_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingStore:
        async def initialize(self) -> None:
            raise sqlite3.OperationalError("unavailable")

        async def append(self, observation: object) -> None:
            pytest.fail("append must not run after initialization failure")

    stop_event = asyncio.Event()

    async def stop_after_wait(seconds: float) -> None:
        stop_event.set()

    monitor = MarketSourceHintShadowMonitor(
        store=FailingStore(),
        get_markets=lambda: pytest.fail("market provider must not run"),
        get_series_metadata=lambda: {},
        seen_state_path=tmp_path / "state" / "seen.json",
        sleep=stop_after_wait,
    )

    await monitor.run(stop_event)


@pytest.mark.asyncio
async def test_shadow_capture_supervisor_logs_each_completed_cycle(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stop_event = asyncio.Event()

    async def stop_after_cycle(_seconds: float) -> None:
        stop_event.set()

    monitor = MarketSourceHintShadowMonitor(
        store=MarketSourceHintShadowStore(tmp_path / "data" / "shadow.db"),
        get_markets=lambda: (_kalshi_market(),),
        get_series_metadata=lambda: {},
        seen_state_path=tmp_path / "state" / "seen.json",
        feed_url_builders=(),
        max_markets=1,
        sleep=stop_after_cycle,
    )

    with caplog.at_level(logging.INFO, logger=monitor_module.logger.name):
        await monitor.run(stop_event)

    assert (
        "source-hint shadow capture cycle markets=1 queries=0 feeds=0 "
        "captured=0 timeouts=0 failures=0 store_failures=0"
    ) in caplog.text


@pytest.mark.asyncio
async def test_store_is_append_only_and_reader_uses_read_only_connection(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data" / "market_source_hint_shadow.db"
    store = MarketSourceHintShadowStore(db_path)
    await store.initialize()
    observation = MarketSourceHintShadowObservation(
        captured_at=datetime(2026, 7, 31, 12, tzinfo=timezone.utc),
        ticker="KXTEST-26",
        market_title="Will a test event occur?",
        source_hint_query="site:official.example test event",
        source_hint_domain="official.example",
        feed_url="https://fixture.test/rss",
        item_id="b" * 64,
        headline="Official test update",
        item_url="https://official.example/update",
        item_source="Official Example",
        published_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )

    await store.append(observation)
    await store.append(observation)

    rows = store.read_records()
    assert len(rows) == 2
    assert {row.headline for row in rows} == {"Official test update"}

    with sqlite3.connect(db_path) as connection:
        trigger_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'trigger'"
            )
        }
        assert trigger_names >= {
            "immutable_market_source_hint_shadow_observations_update",
            "immutable_market_source_hint_shadow_observations_delete",
        }
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE market_source_hint_shadow_observations "
                "SET headline = 'mutated' WHERE observation_id = 1"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM market_source_hint_shadow_observations "
                "WHERE observation_id = 1"
            )
        connection.rollback()

    assert len(store.read_records()) == 2

    read_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(read_uri, uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "INSERT INTO market_source_hint_shadow_observations (captured_at) VALUES ('x')"
            )
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_enabled_runtime_task_uses_data_dir_without_queue_or_store_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []

    class SpyStore:
        def __init__(self, db_path: Path) -> None:
            events.append(("store", db_path))

    class SpyMonitor:
        def __init__(self, **kwargs: object) -> None:
            events.append(("monitor", kwargs))

        async def run(self) -> None:
            await asyncio.Event().wait()

    class Bot:
        matcher = SimpleNamespace(
            _cache=SimpleNamespace(
                _markets=(),
                get_series_metadata_snapshot=lambda: {},
            )
        )

        def _make_market_getter(self) -> object:
            pytest.fail("source-hint capture must not use the mixed market getter")

    from tasks import market_source_hint_shadow_store as store_module

    monkeypatch.setattr(main, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(
        main,
        "cfg",
        SimpleNamespace(
            enable_market_source_hint_shadow_capture=True,
            market_source_hint_shadow_capture_interval_seconds=60,
            market_source_hint_shadow_capture_max_markets=1,
            market_source_hint_shadow_capture_max_queries_per_market=1,
            market_source_hint_shadow_capture_max_records_per_cycle=1,
            market_source_hint_shadow_capture_concurrency=1,
            market_source_hint_shadow_capture_timeout_seconds=1,
        ),
    )
    monkeypatch.setattr(store_module, "MarketSourceHintShadowStore", SpyStore)
    monkeypatch.setattr(monitor_module, "MarketSourceHintShadowMonitor", SpyMonitor)

    task = main.TradingBot._create_market_source_hint_shadow_capture_task(Bot())
    assert task is not None
    assert events[0] == ("store", tmp_path / "data" / "market_source_hint_shadow.db")
    assert events[1][0] == "monitor"
    assert events[1][1]["get_markets"]() == ()
    assert not (tmp_path / "data" / "market_source_hint_shadow.db").exists()

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_disabled_runtime_task_creates_no_shadow_store_or_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(
        main,
        "cfg",
        SimpleNamespace(enable_market_source_hint_shadow_capture=False),
    )

    assert main.TradingBot._create_market_source_hint_shadow_capture_task(object()) is None
    assert not (tmp_path / "data" / "market_source_hint_shadow.db").exists()


def test_shadow_capture_module_has_no_normal_intake_or_trading_dependencies() -> None:
    source = inspect.getsource(monitor_module)

    assert "_enqueue_news" not in source
    assert "_news_queue" not in source
    assert "PaperTrader" not in source
    assert "trade_log" not in source
    assert "trades.jsonl" not in source
    assert "utils.logger" not in source
