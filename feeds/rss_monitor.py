"""
RSS feed monitor.

Polls the configured RSS feeds every RSS_POLL_INTERVAL_SECONDS seconds,
deduplicates entries using a SHA-256 hash of (url+title), and puts new
NewsItem objects onto an asyncio.Queue for downstream processing.
"""

import asyncio
import hashlib
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable, Awaitable

import feedparser
from dateutil import parser as dateutil_parser

from config import RSS_FEEDS, RSS_POLL_INTERVAL_SECONDS
from feeds import NewsItem
from utils.logger import get_logger

log = get_logger("rss_monitor")

# Maximum number of dedup IDs to keep in memory (oldest dropped first)
MAX_SEEN = 5_000


def _make_id(entry) -> str:
    key = (getattr(entry, "link", "") + getattr(entry, "title", "")).encode()
    return hashlib.sha256(key).hexdigest()


def _parse_date(entry) -> datetime:
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return dateutil_parser.parse(raw).astimezone(timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


def _source_name(feed_url: str, feed_title: str) -> str:
    """Derive a human-readable source label from the feed metadata."""
    if feed_title:
        return feed_title
    for name in ("reuters", "apnews", "bbci", "aljazeera"):
        if name in feed_url.lower():
            return name.capitalize()
    return feed_url.split("/")[2]  # domain


async def poll_feed(
    url: str,
    callback: Callable[[NewsItem], Awaitable[None]],
    seen: OrderedDict,
) -> None:
    """Fetch one RSS feed URL and invoke callback for each unseen entry."""
    loop = asyncio.get_running_loop()
    try:
        # feedparser is synchronous — run in executor to avoid blocking
        parsed = await loop.run_in_executor(None, feedparser.parse, url)
    except Exception as exc:
        log.warning("Failed to fetch %s: %s", url, exc)
        return

    source = _source_name(url, getattr(parsed.feed, "title", ""))
    new_count = 0

    for entry in parsed.entries:
        item_id = _make_id(entry)
        if item_id in seen:
            continue

        # Manage memory: evict oldest entry (FIFO — OrderedDict maintains insertion order)
        if len(seen) >= MAX_SEEN:
            seen.popitem(last=False)

        seen[item_id] = None
        new_count += 1

        item = NewsItem(
            headline=getattr(entry, "title", "(no title)"),
            url=getattr(entry, "link", url),
            source=source,
            published=_parse_date(entry),
            body=getattr(entry, "summary", ""),
            item_id=item_id,
        )
        try:
            await callback(item)
        except Exception as exc:
            log.error("Callback error for item %s: %s", item_id, exc)

    if new_count:
        log.debug("Feed %s: %d new items", source, new_count)


async def run_rss_monitor(
    callback: Callable[[NewsItem], Awaitable[None]],
    feeds: list[str] | None = None,
    poll_interval: int = RSS_POLL_INTERVAL_SECONDS,
) -> None:
    """
    Continuously poll all RSS feeds and call `callback` for each new item.

    Runs forever; cancel the task to stop.
    """
    if feeds is None:
        feeds = RSS_FEEDS

    seen: OrderedDict = OrderedDict()
    log.info("RSS monitor started — watching %d feeds", len(feeds))

    while True:
        tasks = [poll_feed(url, callback, seen) for url in feeds]
        await asyncio.gather(*tasks, return_exceptions=True)
        log.debug("RSS poll cycle complete, sleeping %ds", poll_interval)
        await asyncio.sleep(poll_interval)
