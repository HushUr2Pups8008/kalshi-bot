"""
Reddit monitor — no API credentials required.

Uses Reddit's public JSON endpoints (reddit.com/r/subreddit/new.json)
which are freely accessible without authentication for public subreddits.
Polls each subreddit every REDDIT_POLL_INTERVAL seconds and emits NewsItem
objects for new posts above the minimum score threshold.
"""

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Callable, Awaitable

import aiohttp

from config import REDDIT_SUBREDDITS, REDDIT_MIN_SCORE
from feeds import NewsItem
from utils.logger import get_logger

log = get_logger("reddit_monitor")

REDDIT_POLL_INTERVAL = 120   # seconds between polls
FETCH_LIMIT          = 25    # posts per request
SCORE_RECHECK_DELAY  = 90    # seconds before rechecking score
MAX_SEEN             = 2_000

# Reddit requires a descriptive User-Agent to avoid rate limiting
_USER_AGENT = "KalshiBot/1.0 (geopolitical news monitor; no login required)"
_BASE_URL   = "https://www.reddit.com/r/{subreddit}/new.json?limit={limit}&raw_json=1"


def _make_id(post_id: str) -> str:
    return hashlib.sha256(post_id.encode()).hexdigest()


async def _fetch_subreddit(session: aiohttp.ClientSession, subreddit: str) -> list[dict]:
    url = _BASE_URL.format(subreddit=subreddit, limit=FETCH_LIMIT)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 429:
                log.warning("Reddit rate limit hit for r/%s — backing off 30s", subreddit)
                await asyncio.sleep(30)
                return []
            if resp.status != 200:
                log.warning("r/%s returned HTTP %d", subreddit, resp.status)
                return []
            data = await resp.json()
            return data.get("data", {}).get("children", [])
    except asyncio.TimeoutError:
        log.warning("Timeout fetching r/%s", subreddit)
        return []
    except Exception as exc:
        log.warning("Error fetching r/%s: %s", subreddit, exc)
        return []


async def _score_recheck_and_emit(
    session: aiohttp.ClientSession,
    post: dict,
    subreddit: str,
    callback: Callable[[NewsItem], Awaitable[None]],
) -> None:
    """Wait for upvotes to accumulate, then emit if above threshold."""
    await asyncio.sleep(SCORE_RECHECK_DELAY)

    post_id = post.get("id", "")
    url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json?raw_json=1"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                score = data[0]["data"]["children"][0]["data"].get("score", 0)
            else:
                score = post.get("score", 0)
    except Exception:
        score = post.get("score", 0)

    if score < REDDIT_MIN_SCORE:
        log.debug(
            "r/%s post below score threshold (%d < %d): %s",
            subreddit, score, REDDIT_MIN_SCORE, post.get("title", "")[:60],
        )
        return

    body = ""
    if post.get("is_self") and post.get("selftext"):
        body = post["selftext"][:500]
    elif post.get("url"):
        body = post["url"]

    item = NewsItem(
        headline=post.get("title", "(no title)"),
        url=f"https://www.reddit.com{post.get('permalink', '')}",
        source=f"r/{subreddit}",
        published=datetime.fromtimestamp(
            float(post.get("created_utc", 0)), tz=timezone.utc
        ),
        body=body,
        item_id=_make_id(post_id),
    )
    await callback(item)


async def _poll_subreddit(
    session: aiohttp.ClientSession,
    subreddit: str,
    callback: Callable[[NewsItem], Awaitable[None]],
    seen: set[str],
) -> None:
    children = await _fetch_subreddit(session, subreddit)
    new_count = 0
    for child in children:
        post = child.get("data", {})
        post_id = post.get("id", "")
        if not post_id:
            continue
        pid = _make_id(post_id)
        if pid in seen:
            continue
        if len(seen) >= MAX_SEEN:
            seen.pop()
        seen.add(pid)
        new_count += 1
        asyncio.create_task(_score_recheck_and_emit(session, post, subreddit, callback))
    if new_count:
        log.debug("r/%s: %d new posts queued for score recheck", subreddit, new_count)


async def run_reddit_monitor(
    callback: Callable[[NewsItem], Awaitable[None]],
    subreddits: list[str] | None = None,
    poll_interval: int = REDDIT_POLL_INTERVAL,
) -> None:
    """
    Poll Reddit subreddits using the public JSON API.
    No API credentials required. Runs indefinitely; cancel the task to stop.
    """
    if subreddits is None:
        subreddits = REDDIT_SUBREDDITS

    seen: set[str] = set()
    log.info(
        "Reddit monitor started (public JSON API) — watching: %s",
        ", ".join(f"r/{s}" for s in subreddits),
    )

    headers = {"User-Agent": _USER_AGENT}
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            tasks = [
                _poll_subreddit(session, sub, callback, seen)
                for sub in subreddits
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
            log.debug("Reddit poll cycle complete, sleeping %ds", poll_interval)
            await asyncio.sleep(poll_interval)
