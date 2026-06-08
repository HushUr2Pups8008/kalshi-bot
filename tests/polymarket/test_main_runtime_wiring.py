from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from feeds import NewsItem
from main import TradingBot


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
