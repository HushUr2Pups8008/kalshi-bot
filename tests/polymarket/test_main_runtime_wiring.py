from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from feeds import NewsItem
from feeds.search_news_monitor import _markets_to_queries
from main import TradingBot
from polymarket.models import PolymarketMarket
from trading.venue import Venue


def _news() -> NewsItem:
    return NewsItem(
        headline="Example event gets more likely",
        url="https://example.test/story",
        source="Example Wire",
        published=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_on_news_item_processes_polymarket_before_kalshi_exchange_gate():
    calls = []

    class Runtime:
        async def process_news(self, news):
            calls.append(news.headline)
            return 1

    bot = SimpleNamespace(
        source_stats=SimpleNamespace(increment_posts=lambda source: None),
        polymarket_paper_runtime=Runtime(),
        _exchange_open_or_skip=lambda source: False,
    )

    await TradingBot.on_news_item(bot, _news())

    assert calls == ["Example event gets more likely"]


def test_market_getter_includes_cached_polymarket_markets_for_search_monitor():
    kalshi_market = SimpleNamespace(ticker="KXIRAN-26JUL", title="Iran nuclear talks")
    polymarket_market = SimpleNamespace(
        ticker="will-us-iran-deal-happen",
        title="Will the US and Iran reach a nuclear deal?",
    )

    class Runtime:
        def cached_candidate_markets(self):
            return [polymarket_market]

    bot = SimpleNamespace(
        matcher=SimpleNamespace(_cache=SimpleNamespace(_markets=[kalshi_market])),
        polymarket_paper_runtime=Runtime(),
    )

    markets = TradingBot._make_market_getter(bot)()

    assert markets == [kalshi_market, polymarket_market]


def test_polymarket_markets_contribute_search_queries():
    market = PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id="will-us-iran-deal-happen",
        title="Will the US and Iran reach a nuclear deal?",
        status="open",
        yes_ask_cents=45,
        no_ask_cents=56,
        volume_dollars=10_000.0,
        open_interest_dollars=2_500.0,
        close_time="2026-12-31T23:59:59Z",
        question="Will the US and Iran reach a nuclear deal?",
        category="politics",
    )

    assert _markets_to_queries([market]) == ["iran nuclear deal"]


@pytest.mark.asyncio
async def test_warm_polymarket_cache_runs_before_shared_search_getters():
    calls = []

    class Runtime:
        async def warm_cache(self):
            calls.append("warm")
            return 2

        def cached_candidate_markets(self):
            return []

        def stats(self):
            return SimpleNamespace(market_count=2)

    bot = SimpleNamespace(polymarket_paper_runtime=Runtime())

    warmed = await TradingBot._warm_polymarket_paper_runtime_cache(bot)

    assert warmed == 2
    assert calls == ["warm"]


@pytest.mark.asyncio
async def test_polymarket_refresh_detects_new_candidate_market_and_triggers_search(
    monkeypatch,
):
    old_market = PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id="old-senate-market",
        title="Old Senate market",
        status="open",
        yes_ask_cents=49,
        no_ask_cents=52,
        volume_dollars=1_000.0,
        open_interest_dollars=500.0,
        close_time="2026-12-31T23:59:59Z",
        category="politics",
    )
    new_market = PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id="new-senate-market",
        title="New Senate market",
        status="open",
        yes_ask_cents=48,
        no_ask_cents=53,
        volume_dollars=1_000.0,
        open_interest_dollars=500.0,
        close_time="2026-12-31T23:59:59Z",
        category="politics",
    )
    created = []

    class Runtime:
        def __init__(self):
            self.markets = [old_market]

        async def warm_cache(self):
            self.markets = [old_market, new_market]
            return 2

        def cached_candidate_markets(self):
            return list(self.markets)

        def stats(self):
            return SimpleNamespace(market_count=len(self.markets))

    async def trigger(ticker):
        created.append(ticker)

    bot = SimpleNamespace(
        polymarket_paper_runtime=Runtime(),
        _known_polymarket_market_tickers={old_market.ticker},
        _trigger_targeted_search=trigger,
    )

    real_create_task = __import__("asyncio").create_task

    def capture_task(coro):
        task = real_create_task(coro)
        created.append(task)
        return task

    monkeypatch.setattr("main.asyncio.create_task", capture_task)

    warmed = await TradingBot._refresh_polymarket_paper_runtime_cache(bot)
    await created[-1]

    assert warmed == 2
    assert new_market.ticker in created
    assert bot._known_polymarket_market_tickers == {
        old_market.ticker,
        new_market.ticker,
    }


@pytest.mark.asyncio
async def test_targeted_search_resolves_polymarket_candidate_market(monkeypatch):
    market = PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id="will-us-iran-deal-happen",
        title="Will the US and Iran reach a nuclear deal?",
        status="open",
        yes_ask_cents=45,
        no_ask_cents=56,
        volume_dollars=10_000.0,
        open_interest_dollars=2_500.0,
        close_time="2026-12-31T23:59:59Z",
        question="Will the US and Iran reach a nuclear deal?",
        category="politics",
    )
    enqueued = []

    async def poll_feed(url, callback, seen):
        await callback(_news())

    async def enqueue_news(item):
        enqueued.append(item)

    bot = SimpleNamespace(
        matcher=SimpleNamespace(
            _cache=SimpleNamespace(get_markets=AsyncMock(return_value=[]))
        ),
        polymarket_paper_runtime=SimpleNamespace(
            cached_candidate_markets=lambda: [market]
        ),
        _enqueue_news=enqueue_news,
    )
    monkeypatch.setattr("main.poll_feed", poll_feed, raising=False)
    monkeypatch.setattr("feeds.rss_monitor.poll_feed", poll_feed)
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", {"bing_news_query"})

    await TradingBot._trigger_targeted_search(bot, market.ticker)

    assert enqueued
