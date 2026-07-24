"""
RSS feed monitor.

Polls the configured RSS feeds every RSS_POLL_INTERVAL_SECONDS seconds,
deduplicates entries using a SHA-256 hash of (url+title), and puts new
NewsItem objects onto an asyncio.Queue for downstream processing.
"""

import asyncio
import hashlib
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Awaitable

import feedparser
from dateutil import parser as dateutil_parser, tz as dateutil_tz

# Abbreviated US timezone names that dateutil doesn't recognise by default.
# Passing this to parse() avoids UnknownTimezoneWarning (and future exceptions).
_RSS_TZINFOS = {
    "EST": dateutil_tz.tzoffset("EST", -5 * 3600),
    "EDT": dateutil_tz.tzoffset("EDT", -4 * 3600),
    "CST": dateutil_tz.tzoffset("CST", -6 * 3600),
    "CDT": dateutil_tz.tzoffset("CDT", -5 * 3600),
    "MST": dateutil_tz.tzoffset("MST", -7 * 3600),
    "MDT": dateutil_tz.tzoffset("MDT", -6 * 3600),
    "PST": dateutil_tz.tzoffset("PST", -8 * 3600),
    "PDT": dateutil_tz.tzoffset("PDT", -7 * 3600),
}

from config import RSS_FEEDS, RSS_POLL_INTERVAL_SECONDS
from feeds import NewsItem
from feeds.seen_state import checkpoint_seen_ids, load_seen_ids
from utils.logger import get_logger
from utils.output_paths import STATE_ROOT

log = get_logger("rss_monitor")

# Maximum number of dedup IDs to keep in memory (oldest dropped first)
MAX_SEEN = 5_000
RSS_SEEN_STATE_PATH = STATE_ROOT / "ingest_seen" / "rss_seen_ids.json"
FADE_TWEET_SEEN_STATE_PATH = STATE_ROOT / "ingest_seen" / "fade_tweet_seen_ids.json"

# Timeout for a single feedparser.parse() call (seconds).
# Prevents a hanging feed server from stalling the entire RSS poll cycle.
_FEED_PARSE_TIMEOUT = 30


def _make_id(entry) -> str:
    key = (getattr(entry, "link", "") + getattr(entry, "title", "")).encode()
    return hashlib.sha256(key).hexdigest()


def _parse_date(entry) -> datetime:
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return dateutil_parser.parse(raw, tzinfos=_RSS_TZINFOS).astimezone(timezone.utc)
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


def _entry_source(entry, fallback: str) -> str:
    """
    Return the real publisher for a feed entry when the feed exposes one.

    News aggregators (Google News RSS in particular) populate
    ``<source url="...">Publisher</source>`` at the item level, which
    feedparser parses as ``entry.source`` carrying the real publisher
    name (e.g. "Reuters", "AP News"). Regular publisher RSS feeds don't
    populate this; those items keep the feed-level label passed as
    ``fallback``.

    Accepts feedparser's FeedParserDict (attribute access + mapping
    access), plain dicts, or None. Empty and whitespace-only titles are
    treated as absent and fall through to ``fallback``.
    """
    src = getattr(entry, "source", None)
    if src:
        title = getattr(src, "title", None) if hasattr(src, "title") else None
        if not title and hasattr(src, "get"):
            title = src.get("title")
        if title:
            title = str(title).strip()
            if title:
                return title
    return fallback


async def poll_feed(
    url: str,
    callback: Callable[[NewsItem], Awaitable[None]],
    seen: OrderedDict,
) -> None:
    """Fetch one RSS feed URL and invoke callback for each unseen entry."""
    loop = asyncio.get_running_loop()
    try:
        # feedparser is synchronous -- run in executor to avoid blocking.
        # Wrap with wait_for() so a hanging feed server cannot stall the cycle.
        parsed = await asyncio.wait_for(
            loop.run_in_executor(None, feedparser.parse, url),
            timeout=_FEED_PARSE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning("Feed timed out after %ds: %s", _FEED_PARSE_TIMEOUT, url)
        return
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
            source=_entry_source(entry, fallback=source),
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
    seen_state_path: Path = RSS_SEEN_STATE_PATH,
) -> None:
    """
    Continuously poll all RSS feeds and call `callback` for each new item.

    Runs forever; cancel the task to stop.
    """
    if feeds is None:
        feeds = RSS_FEEDS

    seen = load_seen_ids(seen_state_path, MAX_SEEN)
    log.info("RSS monitor started -- watching %d feeds", len(feeds))

    while True:
        tasks = [poll_feed(url, callback, seen) for url in feeds]
        await asyncio.gather(*tasks, return_exceptions=True)
        checkpoint_seen_ids(seen_state_path, seen, MAX_SEEN)
        log.debug("RSS poll cycle complete, sleeping %ds", poll_interval)
        await asyncio.sleep(poll_interval)
