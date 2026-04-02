"""
Google News RSS monitor.

Generates Google News RSS search queries from active Kalshi market titles
and polls them each cycle. No API key required. Complements the static RSS
feeds and Reddit with targeted, high-freshness, market-specific news.

Architecture:
  - Each cycle, top markets by open_interest are converted to search queries
  - Up to GNEWS_MAX_QUERIES distinct queries per cycle
  - Feeds fetched in parallel via asyncio.gather
  - Reuses poll_feed() from rss_monitor for feedparser + dedup + NewsItem creation
  - Same NewsItem callback chain as all other news sources

Poll interval: 300s -- Google News indexes within minutes of publication.
"""

import asyncio
import re
import urllib.parse
from collections import OrderedDict
from typing import Callable, Awaitable, Sequence

from feeds import NewsItem
from feeds.rss_monitor import poll_feed
from kalshi import KalshiMarket
from utils.logger import get_logger

log = get_logger("gnews_monitor")

GNEWS_POLL_INTERVAL = 300   # seconds between full fetch cycles
GNEWS_MAX_QUERIES   = 25    # max distinct queries per cycle (prioritized by open_interest)
GNEWS_MAX_SEEN      = 2000  # dedup cache entry limit

_GNEWS_BASE = "https://news.google.com/rss/search"

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "will", "would", "could",
    "to", "of", "in", "on", "at", "by", "for", "with", "from", "about",
    "and", "or", "but", "not", "this", "that", "it", "if", "as", "be",
    "yes", "no", "new", "more", "how", "what", "when", "who", "which",
    "win", "wins", "its", "has", "have", "had", "can", "get", "per",
    "any", "all", "than", "over", "under", "least", "most", "first",
    "before", "after", "between", "during", "within", "without",
    "exceed", "reach", "hit", "pass", "become", "remain", "stay",
    "there", "their", "they", "them", "then", "that",
})

# Tokens that add no Google News query value even after stop-word removal
_NOISE_TOKENS = frozenset({
    "100", "2025", "2026", "2027", "percent", "pct", "number",
    "total", "end", "year", "month", "week", "day", "days",
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
})


def _gnews_url(query: str) -> str:
    """Build a Google News RSS URL for a search query."""
    params = urllib.parse.urlencode({
        "q":    query,
        "hl":   "en-US",
        "gl":   "US",
        "ceid": "US:en",
    })
    return f"{_GNEWS_BASE}?{params}"


def _tokenize(text: str) -> list[str]:
    """Extract meaningful tokens from a market title for query construction."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return [
        t for t in text.split()
        if t not in _STOP_WORDS
        and t not in _NOISE_TOKENS
        and len(t) >= 3
    ]


def _markets_to_queries(markets: Sequence[KalshiMarket]) -> list[str]:
    """
    Convert active market titles to deduplicated Google News search queries.

    Markets are sorted by open_interest descending so the most-traded topics
    get query slots first. Each market contributes up to 4 key tokens. Markets
    with identical token sets are skipped (deduped). Capped at GNEWS_MAX_QUERIES.
    """
    sorted_markets = sorted(
        markets,
        key=lambda m: getattr(m, "open_interest", 0),
        reverse=True,
    )

    seen_sets: set[frozenset] = set()
    queries: list[str] = []

    for market in sorted_markets:
        if len(queries) >= GNEWS_MAX_QUERIES:
            break
        tokens = _tokenize(market.title)[:4]
        if len(tokens) < 2:
            continue
        token_set = frozenset(tokens)
        if token_set in seen_sets:
            continue
        seen_sets.add(token_set)
        queries.append(" ".join(tokens))

    return queries


async def run_google_news_monitor(
    callback: Callable[[NewsItem], Awaitable[None]],
    get_markets: Callable[[], Sequence[KalshiMarket]],
    poll_interval: int = GNEWS_POLL_INTERVAL,
) -> None:
    """
    Poll Google News RSS for queries derived from active Kalshi market titles.
    Runs indefinitely; cancel the task to stop.

    Args:
        callback:     Called for each new NewsItem (same signature as RSS/Reddit monitors).
        get_markets:  Sync callable returning the current market cache list.
                      Typically reads self.matcher._cache._markets directly.
        poll_interval: Seconds between fetch cycles (default 300).
    """
    seen: OrderedDict = OrderedDict()
    log.info(
        "Google News monitor started (poll interval %ds, max %d queries/cycle)",
        poll_interval, GNEWS_MAX_QUERIES,
    )

    while True:
        try:
            markets = get_markets()
            queries = _markets_to_queries(markets)

            if not queries:
                log.debug("Google News: no active markets, skipping cycle")
                await asyncio.sleep(poll_interval)
                continue

            urls = [_gnews_url(q) for q in queries]
            log.debug(
                "Google News: fetching %d queries for %d active markets",
                len(queries), len(markets),
            )

            await asyncio.gather(
                *[poll_feed(url, callback, seen) for url in urls],
                return_exceptions=True,
            )

            # Trim dedup cache to bound memory
            while len(seen) > GNEWS_MAX_SEEN:
                seen.popitem(last=False)

        except Exception as exc:
            log.warning("Google News monitor cycle error: %s", exc)

        await asyncio.sleep(poll_interval)
