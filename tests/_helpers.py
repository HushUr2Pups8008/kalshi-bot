"""
Shared test helpers.

Intentionally narrow: only symbols used by multiple test modules live here.
Module-specific fixtures stay local to their test file. A generic
`_make_market` helper is not provided because the three callers
(`test_signal_analyzer`, `test_market_matcher`, `test_main_pipeline`) each
construct markets with different shapes — a MagicMock, a configurable
KalshiMarket factory, and a fixed KalshiMarket — that are not interchangeable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from feeds import NewsItem


def make_news(headline: str, body: str = "") -> NewsItem:
    """Construct a canonical NewsItem for tests that don't care about fields
    other than headline and body."""
    return NewsItem(
        headline=headline,
        url="https://example.com/story",
        source="Reuters",
        published=datetime.now(timezone.utc),
        body=body,
        item_id="news-1",
    )
