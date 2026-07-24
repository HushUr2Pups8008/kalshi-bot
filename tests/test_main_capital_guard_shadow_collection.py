"""Runtime wiring tests for default-off shadow settlement collection."""

from __future__ import annotations

import asyncio
import builtins
import importlib
import os
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

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


_LAZY_COLLECTION_MODULES = (
    "tasks.capital_guard_shadow_settlement",
    "trading.authoritative_settlement_source",
    "trading.capital_guard_shadow",
    "polymarket.public_client",
)


def _install_fake_module(monkeypatch: pytest.MonkeyPatch, name: str, **attrs: object) -> None:
    module = ModuleType(name)
    for attr_name, value in attrs.items():
        setattr(module, attr_name, value)
    monkeypatch.setitem(sys.modules, name, module)


def test_collection_factory_disabled_is_truly_lazy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        config_module.cfg,
        "enable_capital_guard_shadow_settlement_collection",
        False,
        raising=False,
    )
    monkeypatch.setattr(main_module, "DATA_DIR", tmp_path / "data")
    for module_name in _LAZY_COLLECTION_MODULES:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    original_import = builtins.__import__

    def _reject_collection_import(name: str, *args: object, **kwargs: object):
        if name in _LAZY_COLLECTION_MODULES:
            raise AssertionError(f"disabled collection imported {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _reject_collection_import)
    connect = MagicMock(side_effect=AssertionError("disabled collection opened SQLite"))
    monkeypatch.setattr(sqlite3, "connect", connect)
    create_task = MagicMock(side_effect=AssertionError("disabled collection created a task"))
    monkeypatch.setattr(main_module.asyncio, "create_task", create_task)

    bot = TradingBot.__new__(TradingBot)
    bot.rest = object()

    assert bot._create_capital_guard_shadow_settlement_collection_task() is None
    assert not (tmp_path / "data" / "capital_guard_shadow.db").exists()
    assert all(module_name not in sys.modules for module_name in _LAZY_COLLECTION_MODULES)
    connect.assert_not_called()
    create_task.assert_not_called()


def test_collection_factory_missing_db_is_inert_without_import_or_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        config_module.cfg,
        "enable_capital_guard_shadow_settlement_collection",
        True,
        raising=False,
    )
    monkeypatch.setattr(main_module, "DATA_DIR", tmp_path / "data")
    for module_name in _LAZY_COLLECTION_MODULES:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    original_import = builtins.__import__

    def _reject_collection_import(name: str, *args: object, **kwargs: object):
        if name in _LAZY_COLLECTION_MODULES:
            raise AssertionError(f"missing-db collection imported {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _reject_collection_import)
    connect = MagicMock(side_effect=AssertionError("missing-db collection opened SQLite"))
    monkeypatch.setattr(sqlite3, "connect", connect)
    create_task = MagicMock(side_effect=AssertionError("missing-db collection created a task"))
    monkeypatch.setattr(main_module.asyncio, "create_task", create_task)

    bot = TradingBot.__new__(TradingBot)
    bot.rest = object()

    assert bot._create_capital_guard_shadow_settlement_collection_task() is None
    assert not (tmp_path / "data").exists()
    assert all(module_name not in sys.modules for module_name in _LAZY_COLLECTION_MODULES)
    connect.assert_not_called()
    create_task.assert_not_called()


def test_collection_factory_existing_db_builds_only_strict_isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        config_module.cfg,
        "enable_capital_guard_shadow_settlement_collection",
        True,
        raising=False,
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "capital_guard_shadow.db"
    db_path.touch()
    monkeypatch.setattr(main_module, "DATA_DIR", data_dir)

    stores: list[object] = []
    sources: list[object] = []
    clients: list[object] = []
    collectors: list[object] = []

    class FakeStore:
        def __init__(self, *, db_path: Path, existing_only: bool) -> None:
            self.db_path = db_path
            self.existing_only = existing_only
            self.initialized = False
            stores.append(self)

        def initialize(self) -> None:
            self.initialized = True

    class FakePolymarketClient:
        def __init__(self) -> None:
            clients.append(self)

    class FakeSource:
        def __init__(self, *, kalshi_client: object, polymarket_client: object) -> None:
            self.kalshi_client = kalshi_client
            self.polymarket_client = polymarket_client
            sources.append(self)

    class FakeCollector:
        def __init__(self, *, store: object, source: object) -> None:
            self.store = store
            self.source = source
            collectors.append(self)

        async def run_once(self) -> None:
            return None

    _install_fake_module(
        monkeypatch,
        "trading.capital_guard_shadow",
        CapitalGuardShadowStore=FakeStore,
    )
    _install_fake_module(
        monkeypatch,
        "polymarket.public_client",
        PolymarketPublicClient=FakePolymarketClient,
    )
    _install_fake_module(
        monkeypatch,
        "trading.authoritative_settlement_source",
        AuthoritativeSettlementSource=FakeSource,
    )
    _install_fake_module(
        monkeypatch,
        "tasks.capital_guard_shadow_settlement",
        CapitalGuardShadowSettlementCollector=FakeCollector,
    )

    created_task = MagicMock()

    def _create_task(coro: object, *, name: str):
        coro.close()
        created_task.get_name.return_value = name
        return created_task

    create_task = MagicMock(side_effect=_create_task)
    monkeypatch.setattr(main_module.asyncio, "create_task", create_task)

    bot = TradingBot.__new__(TradingBot)
    bot.rest = object()

    task = bot._create_capital_guard_shadow_settlement_collection_task()

    assert task is created_task
    assert task.get_name() == "capital_guard_shadow_settlement_collection"
    assert len(stores) == 1
    assert stores[0].db_path == db_path
    assert stores[0].existing_only is True
    assert stores[0].initialized is True
    assert len(clients) == 1
    assert len(sources) == 1
    assert sources[0].kalshi_client is bot.rest
    assert sources[0].polymarket_client is clients[0]
    assert len(collectors) == 1
    assert collectors[0].store is stores[0]
    assert collectors[0].source is sources[0]
    create_task.assert_called_once()


@pytest.mark.parametrize("failure_stage", ("initialize", "source", "collector"))
def test_collection_factory_existing_db_failure_is_fail_isolated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_stage: str,
) -> None:
    monkeypatch.setattr(
        config_module.cfg,
        "enable_capital_guard_shadow_settlement_collection",
        True,
        raising=False,
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "capital_guard_shadow.db"
    db_path.touch()
    monkeypatch.setattr(main_module, "DATA_DIR", data_dir)

    class FakeStore:
        def __init__(self, *, db_path: Path, existing_only: bool) -> None:
            self.db_path = db_path
            self.existing_only = existing_only

        def initialize(self) -> None:
            if failure_stage == "initialize":
                raise RuntimeError("invalid isolated shadow store")

    class FakePolymarketClient:
        def __init__(self) -> None:
            return None

    class FakeSource:
        def __init__(self, *, kalshi_client: object, polymarket_client: object) -> None:
            if failure_stage == "source":
                raise RuntimeError("strict source construction failed")

    class FakeCollector:
        def __init__(self, *, store: object, source: object) -> None:
            if failure_stage == "collector":
                raise RuntimeError("collector construction failed")

    _install_fake_module(
        monkeypatch,
        "trading.capital_guard_shadow",
        CapitalGuardShadowStore=FakeStore,
    )
    _install_fake_module(
        monkeypatch,
        "polymarket.public_client",
        PolymarketPublicClient=FakePolymarketClient,
    )
    _install_fake_module(
        monkeypatch,
        "trading.authoritative_settlement_source",
        AuthoritativeSettlementSource=FakeSource,
    )
    _install_fake_module(
        monkeypatch,
        "tasks.capital_guard_shadow_settlement",
        CapitalGuardShadowSettlementCollector=FakeCollector,
    )
    create_task = MagicMock(side_effect=AssertionError("failed collection created a task"))
    monkeypatch.setattr(main_module.asyncio, "create_task", create_task)

    bot = TradingBot.__new__(TradingBot)
    bot.rest = object()

    assert bot._create_capital_guard_shadow_settlement_collection_task() is None
    create_task.assert_not_called()


@pytest.mark.asyncio
async def test_collection_loop_is_serial_and_cancellable() -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    class Collector:
        def __init__(self) -> None:
            self.calls = 0
            self.active = 0
            self.max_active = 0

        async def run_once(self) -> None:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                if self.calls == 1:
                    first_started.set()
                    await release_first.wait()
                else:
                    second_started.set()
            finally:
                self.active -= 1

    collector = Collector()
    task = asyncio.create_task(
        main_module._run_capital_guard_shadow_settlement_collection(
            collector,
            interval_seconds=0.001,
        )
    )
    try:
        await asyncio.wait_for(first_started.wait(), timeout=0.5)
        await asyncio.sleep(0.01)
        assert collector.calls == 1
        assert collector.max_active == 1

        release_first.set()
        await asyncio.wait_for(second_started.wait(), timeout=0.5)
        assert collector.max_active == 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
