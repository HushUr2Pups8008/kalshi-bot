"""
GDELT Document 2.0 API monitor.

Queries GDELT's geopolitical event database for articles matching active
Kalshi market titles. Free, no API key required, updates every 15 minutes.

GDELT is specifically designed for geopolitical event monitoring and covers
global news sources that RSS aggregators miss. Returns JSON (not RSS) so
this module uses aiohttp directly rather than poll_feed().

Architecture:
  - Each cycle, up to GDELT_MAX_QUERIES queries are generated from active markets
  - Queries reuse _markets_to_queries() from search_news_monitor (no duplication)
  - Queries fetched sequentially with a short stagger to avoid GDELT request bursting
  - NewsItem objects emitted via the same callback chain as all other sources

Poll interval: 900s -- matches GDELT's 15-minute indexing cadence.
"""

import asyncio
import hashlib
import time
import urllib.parse
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable, Awaitable, Sequence

import aiohttp

from feeds import NewsItem
from feeds.search_news_monitor import _markets_to_queries
from kalshi import KalshiMarket
from utils.logger import get_logger

log = get_logger("gdelt_monitor")

GDELT_POLL_INTERVAL = 900    # seconds -- matches GDELT's 15-minute update cadence
GDELT_MAX_QUERIES   = 15     # fewer than search engines; sequential fetch, not parallel
GDELT_MAX_RECORDS   = 25     # articles per query
GDELT_MAX_SEEN      = 2000   # dedup cache entry limit
GDELT_TIMEOUT       = 15     # seconds per request
GDELT_STAGGER       = 2      # seconds between queries within a cycle

_GDELT_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"

# Module-level backoff state -- shared across all calls in the process.
# Mirrors the per-subreddit backoff pattern in reddit_monitor.
_gdelt_backoff_until: float = 0.0   # monotonic clock; 0.0 = not in backoff
_gdelt_backoff_secs:  float = 60.0  # current delay; doubles on each 429, resets on success


def _gdelt_url(query: str) -> str:
    """Build a GDELT artlist API URL, restricting to English-language sources."""
    params = urllib.parse.urlencode({
        "query":      f"{query} sourcelang:English",
        "mode":       "artlist",
        "format":     "json",
        "maxrecords": str(GDELT_MAX_RECORDS),
        "timespan":   "1d",
    })
    return f"{_GDELT_BASE}?{params}"


def _parse_seendate(seendate: str) -> datetime:
    """Parse GDELT seendate format: '20260402T120000Z'."""
    try:
        return datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _make_item_id(url: str, title: str) -> str:
    """SHA-256 dedup key matching the same convention used in rss_monitor."""
    return hashlib.sha256(f"{url}:{title}".encode()).hexdigest()


async def _fetch_gdelt_query(
    session: aiohttp.ClientSession,
    query: str,
) -> list[dict]:
    """Fetch one GDELT query; return articles list or empty list on error."""
    global _gdelt_backoff_until, _gdelt_backoff_secs
    url = _gdelt_url(query)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=GDELT_TIMEOUT)) as resp:
            if resp.status == 429:
                _gdelt_backoff_secs = min(_gdelt_backoff_secs * 2, 900.0)
                _gdelt_backoff_until = time.monotonic() + _gdelt_backoff_secs
                log.warning(
                    "GDELT 429 rate limit -- backing off %.0fs (next retry after backoff expires)",
                    _gdelt_backoff_secs,
                )
                return []
            if resp.status != 200:
                log.warning("GDELT returned HTTP %d for query '%s'", resp.status, query)
                return []
            # GDELT occasionally returns text/plain Content-Type for JSON responses
            data = await resp.json(content_type=None)
            # Successful response -- reset backoff
            _gdelt_backoff_until = 0.0
            _gdelt_backoff_secs  = 60.0
            return data.get("articles") or []
    except asyncio.TimeoutError:
        log.warning("GDELT request timed out for query '%s'", query)
        return []
    except Exception as exc:
        log.warning("GDELT fetch error for query '%s': %s", query, exc)
        return []


async def run_gdelt_monitor(
    callback: Callable[[NewsItem], Awaitable[None]],
    get_markets: Callable[[], Sequence[KalshiMarket]],
    poll_interval: int = GDELT_POLL_INTERVAL,
) -> None:
    """
    Poll GDELT for articles matching active Kalshi market titles.
    Runs indefinitely; cancel the task to stop.

    Args:
        callback:      Called for each new NewsItem.
        get_markets:   Sync callable returning the current market cache list.
        poll_interval: Seconds between fetch cycles (default 900).
    """
    seen: OrderedDict = OrderedDict()
    log.info(
        "GDELT monitor started (poll interval %ds, max %d queries/cycle)",
        poll_interval, GDELT_MAX_QUERIES,
    )

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                markets = get_markets()
                # Reuse query generation; cap at GDELT_MAX_QUERIES (fewer than search engines)
                queries = _markets_to_queries(markets)[:GDELT_MAX_QUERIES]

                if not queries:
                    log.debug("GDELT: no active markets, skipping cycle")
                    await asyncio.sleep(poll_interval)
                    continue

                log.debug(
                    "GDELT: fetching %d queries for %d active markets",
                    len(queries), len(markets),
                )

                for query in queries:
                    # Skip queries while in backoff -- entire GDELT endpoint is rate-limited
                    if time.monotonic() < _gdelt_backoff_until:
                        remaining = _gdelt_backoff_until - time.monotonic()
                        log.debug("GDELT in backoff (%.0fs remaining) -- skipping cycle", remaining)
                        break
                    articles = await _fetch_gdelt_query(session, query)
                    new_count = 0

                    for article in articles:
                        raw_url = article.get("url", "")
                        title   = article.get("title", "(no title)")
                        if not raw_url:
                            continue

                        item_id = _make_item_id(raw_url, title)
                        if item_id in seen:
                            continue

                        if len(seen) >= GDELT_MAX_SEEN:
                            seen.popitem(last=False)
                        seen[item_id] = None
                        new_count += 1

                        item = NewsItem(
                            headline=title,
                            url=raw_url,
                            source="GDELT",
                            published=_parse_seendate(article.get("seendate", "")),
                            body=article.get("domain", ""),
                            item_id=item_id,
                        )
                        try:
                            await callback(item)
                        except Exception as exc:
                            log.error("GDELT callback error for '%s': %s", title[:60], exc)

                    if new_count:
                        log.debug("GDELT query '%s': %d new articles", query, new_count)

                    # Short stagger between queries -- conservative against GDELT rate limits
                    await asyncio.sleep(GDELT_STAGGER)

                # Trim dedup cache to bound memory
                while len(seen) > GDELT_MAX_SEEN:
                    seen.popitem(last=False)

            except Exception as exc:
                log.warning("GDELT monitor cycle error: %s", exc)

            await asyncio.sleep(poll_interval)
