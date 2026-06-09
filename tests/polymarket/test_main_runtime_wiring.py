from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

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

    bot = SimpleNamespace(polymarket_paper_runtime=Runtime())

    warmed = await TradingBot._warm_polymarket_paper_runtime_cache(bot)

    assert warmed == 2
    assert calls == ["warm"]
