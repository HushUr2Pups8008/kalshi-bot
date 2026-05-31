"""
Subreddit discovery via Reddit post search.

Queries Reddit's public search API for posts about active Kalshi market topics,
extracts subreddit names from results, filters against known/blocklisted subs,
and inserts new candidates into the subreddit_candidates table for probe evaluation.

Discovery runs every SUBREDDIT_DISCOVERY_INTERVAL_SECS (default 6h).
source_stats then evaluates candidate quality the same as any other source --
zero-signal candidates are suppressed after ZERO_SIGNAL_POSTS posts.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import aiohttp

from config import (
    cfg,
    MARKET_SERIES_BLOCKLIST_PREFIXES,
    REDDIT_CORE_SUBREDDITS,
    REDDIT_SUBREDDIT_TOPIC_MAP,
    SUBREDDIT_DISCOVERY_MAX_QUERIES,
)
from feeds.search_news_monitor import _markets_to_queries
from kalshi import KalshiMarket

log = logging.getLogger("subreddit_discovery")

_REDDIT_SEARCH_BASE = "https://www.reddit.com/search.json"
_REQUEST_DELAY_SECS = 2.0  # Stay well within Reddit rate limits

# Subreddits that are obviously not relevant to geopolitical news.
# source_stats will suppress anything genuinely low-quality, but this
# blocklist keeps obvious garbage (entertainment, memes) out of the DB.
_GENERIC_BLOCKLIST = frozenset({
    "askreddit", "funny", "pics", "videos", "gaming", "aww", "music",
    "movies", "books", "food", "cooking", "art", "diy", "fitness",
    "relationships", "tifu", "showerthoughts", "mildlyinteresting",
    "lifeprotips", "todayilearned", "explainlikeimfive", "science",
    "technology", "gadgets", "pcmasterrace", "hardware", "programming",
    "learnprogramming", "webdev", "dataisbeautiful", "futurology",
    "space", "askscience", "history", "philosophy", "psychology",
    "personalfinance", "investing", "wallstreetbets", "stocks",
    "cryptocurrency", "bitcoin", "ethereum",
    "nfl", "nba", "soccer", "baseball", "hockey", "tennis",
    "formula1", "mma", "wrestling",
    "anime", "manga", "games", "boardgames", "chess",
    "television", "netflix", "hbo",
    "facepalm", "cringe", "memes", "dankmemes", "me_irl",
    "reddit", "announcements", "changelog",
})


def _reddit_search_url(query: str) -> str:
    """Build Reddit public search URL for a query."""
    import urllib.parse
    params = urllib.parse.urlencode({
        "q": query,
        "sort": "new",
        "t": "week",
        "limit": "25",
    })
    return f"{_REDDIT_SEARCH_BASE}?{params}"


async def _fetch_subreddits_from_search(
    session: aiohttp.ClientSession,
    query: str,
) -> list[str]:
    """
    Query Reddit post search for a query string.
    Returns list of unique subreddit names (lowercase) from results.
    """
    url = _reddit_search_url(query)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                log.debug("[DISCOVERY] Reddit search returned %d for query: %s", resp.status, query)
                return []
            data = await resp.json(content_type=None)
    except Exception as exc:
        log.debug("[DISCOVERY] Reddit search failed for query '%s': %s", query, exc)
        return []

    posts = data.get("data", {}).get("children", [])
    subs: list[str] = []
    seen: set[str] = set()
    for post in posts:
        sub = post.get("data", {}).get("subreddit", "").lower().strip()
        if sub and sub not in seen:
            seen.add(sub)
            subs.append(sub)
    return subs


def _is_known(sub: str, db_conn: sqlite3.Connection) -> bool:
    """
    Return True if sub is already known:
    - in REDDIT_CORE_SUBREDDITS (bare names, lowercase)
    - in any list in REDDIT_SUBREDDIT_TOPIC_MAP (lowercase)
    - already in subreddit_candidates table (any status)
    """
    core_lower = {s.lower() for s in REDDIT_CORE_SUBREDDITS}
    if sub in core_lower:
        return True

    topic_lower = {s.lower() for subs in REDDIT_SUBREDDIT_TOPIC_MAP.values() for s in subs}
    if sub in topic_lower:
        return True

    row = db_conn.execute(
        "SELECT 1 FROM subreddit_candidates WHERE sub = ?", (sub,)
    ).fetchone()
    return row is not None


def _is_blocklisted(sub: str) -> bool:
    """
    Return True if sub should never be probed.
    Checks both the generic content blocklist and the market series blocklist
    prefixes (repurposed: if sub name starts with a blocklisted ticker prefix,
    it is a sports/entertainment community).
    """
    if sub in _GENERIC_BLOCKLIST:
        return True
    sub_upper = sub.upper()
    if any(sub_upper.startswith(p) for p in MARKET_SERIES_BLOCKLIST_PREFIXES):
        return True
    return False


async def run_discovery_pass(
    markets: Sequence[KalshiMarket],
    db_path: Path,
) -> int:
    """
    Query Reddit post search for each active market topic.
    Extract subreddit names from results.
    Filter against known/blocklisted subs.
    Insert new candidates into subreddit_candidates table.
    Returns count of new candidates found.
    """
    # Reddit master switch (default off) -- discovery hits Reddit's (also IP-blocked)
    # public search API, so skip it entirely when Reddit ingestion is disabled.
    if not cfg.reddit_enabled:
        log.debug("[DISCOVERY] Reddit disabled -- skipping subreddit discovery pass")
        return 0

    queries = _markets_to_queries(list(markets))
    if not queries:
        log.debug("[DISCOVERY] No market queries available, skipping pass")
        return 0

    # Cap at configured max
    queries = queries[:SUBREDDIT_DISCOVERY_MAX_QUERIES]
    log.debug("[DISCOVERY] Running discovery pass with %d queries", len(queries))

    headers = {"User-Agent": "kalshi-bot/0.21 (geopolitical market research)"}
    new_count = 0
    now_ts = datetime.now(timezone.utc).isoformat()

    async with aiohttp.ClientSession(headers=headers) as session:
        conn = sqlite3.connect(db_path)
        try:
            for i, query in enumerate(queries):
                if i > 0:
                    await asyncio.sleep(_REQUEST_DELAY_SECS)

                subs = await _fetch_subreddits_from_search(session, query)
                for sub in subs:
                    if _is_blocklisted(sub):
                        continue
                    if _is_known(sub, conn):
                        continue
                    cur = conn.execute(
                        """
                        INSERT INTO subreddit_candidates
                            (sub, discovered_ts, discovered_via, probe_count, last_probed, status)
                        VALUES (?, ?, ?, 0, NULL, 'candidate')
                        ON CONFLICT(sub) DO NOTHING
                        """,
                        (sub, now_ts, query),
                    )
                    new_count += cur.rowcount

            conn.commit()
        finally:
            conn.close()

    if new_count:
        log.info("[DISCOVERY] Found %d new candidate subreddits", new_count)
    else:
        log.debug("[DISCOVERY] No new candidate subreddits found this pass")

    return new_count
