"""Runtime wiring tests for the disabled-default weather shadow capture."""

from __future__ import annotations

import asyncio
import builtins
import importlib
import os
import sqlite3
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest


def _import_runtime_modules():
    original = os.environ.get("POLYMARKET_US_ENABLED")
    os.environ["POLYMARKET_US_ENABLED"] = "false"
    try:
        return importlib.import_module("config"), importlib.import_module("main")
    finally:
        if original is None:
            os.environ.pop("POLYMARKET_US_ENABLED", None)
        else:
            os.environ["POLYMARKET_US_ENABLED"] = original


config_module, main_module = _import_runtime_modules()
TradingBot = main_module.TradingBot


_LAZY_WEATHER_MODULES = (
    "kalshi.public_market_data",
    "tasks.kxhighny_shadow_capture",
    "tasks.weather_shadow_store",
    "weather.nws_public_client",
)


def _weather_config():
    return config_module.BotConfig(
        polymarket_us_enabled=False,
        polymarket_us_live_trading_enabled=False,
    )


def test_weather_shadow_config_defaults_disabled(monkeypatch):
    monkeypatch.delenv("ENABLE_WEATHER_SHADOW_CAPTURE", raising=False)

    assert _weather_config().enable_weather_shadow_capture is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE ", "On"])
def test_weather_shadow_config_accepts_existing_true_spellings(monkeypatch, value):
    monkeypatch.setenv("ENABLE_WEATHER_SHADOW_CAPTURE", value)

    assert _weather_config().enable_weather_shadow_capture is True


def test_weather_shadow_config_uses_shared_bool_parser(monkeypatch):
    monkeypatch.setenv("POLYMARKET_US_ENABLED", "true")
    monkeypatch.delenv("POLYMARKET_US_KEY_ID", raising=False)
    monkeypatch.delenv("POLYMARKET_US_SECRET", raising=False)
    calls = []
    parse_bool_env = config_module._parse_bool_env

    def _parse(name, default="false"):
        calls.append((name, default))
        if name == "ENABLE_WEATHER_SHADOW_CAPTURE":
            return True
        return parse_bool_env(name, default)

    monkeypatch.setattr(config_module, "_parse_bool_env", _parse)

    assert _weather_config().enable_weather_shadow_capture is True
    assert [call for call in calls if call[0] == "ENABLE_WEATHER_SHADOW_CAPTURE"] == [
        ("ENABLE_WEATHER_SHADOW_CAPTURE", "false")
    ]


def test_weather_shadow_factory_disabled_is_truly_lazy(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config_module.cfg,
        "enable_weather_shadow_capture",
        False,
        raising=False,
    )
    monkeypatch.chdir(tmp_path)
    for module_name in _LAZY_WEATHER_MODULES:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    original_import = builtins.__import__

    def _reject_weather_import(name, *args, **kwargs):
        if name in _LAZY_WEATHER_MODULES:
            raise AssertionError(f"disabled factory imported {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _reject_weather_import)
    open_file = MagicMock(side_effect=AssertionError("disabled factory performed file I/O"))
    monkeypatch.setattr(builtins, "open", open_file)
    monkeypatch.setattr(
        sqlite3,
        "connect",
        MagicMock(side_effect=AssertionError("disabled factory opened SQLite")),
    )
    monkeypatch.setattr(
        aiohttp,
        "ClientSession",
        MagicMock(side_effect=AssertionError("disabled factory built a network client")),
    )
    create_task = MagicMock(side_effect=AssertionError("disabled factory created a task"))
    monkeypatch.setattr(main_module.asyncio, "create_task", create_task)

    bot = TradingBot.__new__(TradingBot)
    result = bot._create_weather_shadow_runtime_task()

    assert result is None
    assert not (tmp_path / "data" / "weather_shadow.db").exists()
    assert all(module_name not in sys.modules for module_name in _LAZY_WEATHER_MODULES)
    sqlite3.connect.assert_not_called()
    aiohttp.ClientSession.assert_not_called()
    create_task.assert_not_called()
    open_file.assert_not_called()


def test_weather_shadow_factory_enabled_builds_one_isolated_supervisor(monkeypatch):
    monkeypatch.setattr(
        config_module.cfg,
        "enable_weather_shadow_capture",
        True,
        raising=False,
    )
    stores = []
    market_clients = []
    weather_clients = []
    supervisors = []

    class FakeStore:
        def __init__(self):
            stores.append(self)

    class FakeMarketClient:
        def __init__(self):
            market_clients.append(self)

    class FakeWeatherClient:
        def __init__(self):
            weather_clients.append(self)

    class FakeSupervisor:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            supervisors.append(self)

        def run(self):
            async def _run():
                return None

            return _run()

    fake_modules = {
        "kalshi.public_market_data": {"KalshiPublicMarketDataReader": FakeMarketClient},
        "tasks.kxhighny_shadow_capture": {"WeatherShadowCaptureTask": FakeSupervisor},
        "tasks.weather_shadow_store": {"WeatherShadowStore": FakeStore},
        "weather.nws_public_client": {"NwsPublicClient": FakeWeatherClient},
    }
    for name, attrs in fake_modules.items():
        module = ModuleType(name)
        for attr_name, value in attrs.items():
            setattr(module, attr_name, value)
        monkeypatch.setitem(sys.modules, name, module)

    created_task = MagicMock()

    def _create_task(coro, *, name):
        coro.close()
        created_task.get_name.return_value = name
        return created_task

    create_task = MagicMock(side_effect=_create_task)
    monkeypatch.setattr(main_module.asyncio, "create_task", create_task)

    task = TradingBot.__new__(TradingBot)._create_weather_shadow_runtime_task()

    assert task is created_task
    assert task.get_name() == "weather_shadow_capture"
    create_task.assert_called_once()
    assert len(stores) == 1
    assert len(market_clients) == 2
    assert len(weather_clients) == 2
    assert len(supervisors) == 1
    assert supervisors[0].kwargs == {
        "store": stores[0],
        "capture_markets": market_clients[0],
        "capture_weather": weather_clients[0],
        "label_markets": market_clients[1],
        "label_weather": weather_clients[1],
    }


@pytest.mark.asyncio
async def test_weather_supervisor_cancel_during_initialize_drains_worker(
    monkeypatch, tmp_path
):
    from tasks.kxhighny_shadow_capture import WeatherShadowCaptureTask
    from tasks.weather_shadow_store import WeatherShadowStore

    db_path = tmp_path / "weather.db"
    store = WeatherShadowStore(db_path=db_path)
    initialize_sync = store._initialize_sync
    worker_started = threading.Event()
    release_worker = threading.Event()

    def _blocked_initialize_sync():
        worker_started.set()
        assert release_worker.wait(timeout=2)
        initialize_sync()

    monkeypatch.setattr(store, "_initialize_sync", _blocked_initialize_sync)
    supervisor = WeatherShadowCaptureTask(
        store=store,
        capture_markets=object(),
        capture_weather=object(),
        label_markets=object(),
        label_weather=object(),
    )
    task = asyncio.create_task(supervisor.run(), name="weather-shadow-init-probe")

    try:
        assert await asyncio.to_thread(worker_started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)

        assert task.done() is False
        assert db_path.exists() is False

        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
            tables_at_return = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                )
            )
        stat_at_return = db_path.stat()
        await asyncio.sleep(0.05)

        assert tables_at_return
        assert db_path.stat().st_mtime_ns == stat_at_return.st_mtime_ns
    finally:
        release_worker.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_run_drains_only_weather_before_existing_cleanup(monkeypatch):
    events = []
    global_items = []
    drain_items = []

    class FakeRuntimeTask:
        def __init__(self, name):
            self.name = name
            self.cancelled = False

        def cancel(self):
            self.cancelled = True
            events.append(f"cancel:{self.name}")

        def __await__(self):
            async def _wait():
                events.append(f"drain:{self.name}")
                return None

            return _wait().__await__()

    runtime_tasks = []

    def _create_task(coro, *, name):
        coro.close()
        task = FakeRuntimeTask(name)
        runtime_tasks.append(task)
        return task

    async def _gather(*items, return_exceptions=False):
        if not return_exceptions:
            global_items.extend(items)
            events.append("global-gather-cancelled")
            raise asyncio.CancelledError
        drain_items.extend(items)
        for item in items:
            await item
        return [None for _ in items]

    async def _idle(*args, **kwargs):
        return None

    bot = TradingBot.__new__(TradingBot)
    bot.paper = SimpleNamespace(get_notional_bankroll=lambda: 100.0)
    bot.rest = SimpleNamespace(get_balance=lambda: 0.0)
    bot.ws = SimpleNamespace(run=_idle, stop=lambda: events.append("ws-stop"))
    bot.matcher = SimpleNamespace(
        _cache=SimpleNamespace(get_series_metadata_snapshot=lambda: {})
    )
    bot._news_queue = SimpleNamespace(qsize=lambda: 0, maxsize=1)
    bot._evidence_queue = object()
    bot._runtime_overrides_reader = object()
    bot._accumulation_task = SimpleNamespace(run=_idle)
    bot._check_llm_health = AsyncMock()
    bot._run_startup_observability_probe = AsyncMock()
    bot._warm_polymarket_paper_runtime_cache = AsyncMock()
    bot._enqueue_news = MagicMock()
    bot._make_subreddit_getter = MagicMock(return_value=lambda: ())
    bot._make_market_getter = MagicMock(return_value=lambda: ())
    for method_name in (
        "_news_consumer_task",
        "_warm_ws_subscriptions",
        "_market_refresh_task",
        "_polymarket_market_refresh_task",
        "_auto_resolve_task",
        "_subreddit_discovery_task",
        "_log_rotation_task",
        "_log_maintenance_task",
        "_trading_queue_consumer_task",
        "_structural_recompute_task",
    ):
        setattr(bot, method_name, _idle)

    weather_task = FakeRuntimeTask("weather_shadow_capture")
    bot._create_research_prewarm_runtime_task = MagicMock(return_value=None)
    bot._create_weather_shadow_runtime_task = MagicMock(return_value=weather_task)

    async def _cancel_targeted():
        events.append("targeted-prewarm-cleanup")

    bot.cancel_targeted_research_prewarm_tasks = AsyncMock(side_effect=_cancel_targeted)

    monkeypatch.setattr(config_module.cfg, "polymarket_us_enabled", False)
    monkeypatch.setattr(config_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(main_module, "emit_startup_banner", MagicMock())
    monkeypatch.setattr(main_module, "_log_bankroll_summary", MagicMock())
    monkeypatch.setattr(main_module, "run_rss_monitor", _idle)
    monkeypatch.setattr(main_module, "run_reddit_monitor", _idle)
    monkeypatch.setattr(main_module, "run_search_news_monitor", _idle)
    monkeypatch.setattr(main_module, "run_gdelt_monitor", _idle)
    monkeypatch.setattr(main_module, "run_runtime_overrides_poll", _idle)
    monkeypatch.setattr(main_module.asyncio, "create_task", _create_task)
    monkeypatch.setattr(main_module.asyncio, "gather", _gather)

    await bot.run()

    assert global_items.count(weather_task) == 1
    assert drain_items == [weather_task]
    assert weather_task.cancelled is True
    assert all(task.cancelled for task in runtime_tasks)
    assert all(task not in drain_items for task in runtime_tasks)
    assert events.index("drain:weather_shadow_capture") < events.index(
        "targeted-prewarm-cleanup"
    )
    assert events.index("targeted-prewarm-cleanup") < events.index("ws-stop")
