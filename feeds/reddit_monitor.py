"""
Reddit monitor — no API credentials required.

Uses Reddit's public JSON endpoints (reddit.com/r/subreddit/new.json)
which are freely accessible without authentication for public subreddits.
Polls each subreddit every REDDIT_POLL_INTERVAL seconds and emits NewsItem
objects for new posts above the minimum score threshold.
"""

import asyncio
import hashlib
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable, Awaitable

import aiohttp

from config import REDDIT_SUBREDDITS, REDDIT_MIN_SCORE
from feeds import NewsItem
from utils.logger import get_logger

log = get_logger("reddit_monitor")

REDDIT_POLL_INTERVAL  = 300   # seconds between full poll cycles
FETCH_LIMIT           = 25    # posts per request
SCORE_RECHECK_DELAY   = 90    # seconds before rechecking score
MAX_SEEN              = 2_000
_STAGGER_DELAY        = 10    # seconds between each subreddit request (avoids burst)
_MAX_BACKOFF          = 300   # max 429 backoff in seconds
_REDDIT_GLOBAL_BACKOFF   = 1800   # 30-min global suspension when IP-blocked
_REDDIT_OUTAGE_THRESHOLD = 0.5    # fraction of subreddits failing triggers global backoff
_reddit_down_until: float = 0.0   # monotonic — skip all Reddit until this time
_cycle_errors: list[int] = []     # 403/429 status codes collected during current poll cycle

# Reddit requires a descriptive User-Agent to avoid rate limiting
_USER_AGENT = "KalshiBot/1.0 (geopolitical news monitor; no login required)"
_BASE_URL   = "https://www.reddit.com/r/{subreddit}/new.json?limit={limit}&raw_json=1"


def _make_id(post_id: str) -> str:
    return hashlib.sha256(post_id.encode()).hexdigest()


_backoff:       dict[str, float] = {}  # per-subreddit resume-time (monotonic clock)
_backoff_delay: dict[str, float] = {}  # per-subreddit current delay for exponential growth

async def _fetch_subreddit(session: aiohttp.ClientSession, subreddit: str) -> list[dict]:
    # Honour any active backoff for this subreddit
    if time.monotonic() < _backoff.get(subreddit, 0.0):
        log.debug("r/%s in backoff — skipping this cycle", subreddit)
        return []

    url = _BASE_URL.format(subreddit=subreddit, limit=FETCH_LIMIT)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 429:
                delay = min(_backoff_delay.get(subreddit, 30.0) * 2, _MAX_BACKOFF)
                _backoff_delay[subreddit] = delay
                _backoff[subreddit] = time.monotonic() + delay
                _cycle_errors.append(429)
                log.warning("Reddit rate limit hit for r/%s — backing off %.0fs", subreddit, delay)
                return []
            if resp.status == 403:
                delay = min(_backoff_delay.get(subreddit, 60.0) * 2, _MAX_BACKOFF)
                _backoff_delay[subreddit] = delay
                _backoff[subreddit] = time.monotonic() + delay
                _cycle_errors.append(403)
                log.warning("Reddit access denied for r/%s (403) — backing off %.0fs", subreddit, delay)
                return []
            _backoff[subreddit] = 0.0          # 0.0 < monotonic() always → not in backoff
            _backoff_delay.pop(subreddit, None) # reset exponential delay on success
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
                children = data[0]["data"]["children"] if data else []
                score = children[0]["data"].get("score", 0) if children else post.get("score", 0)
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
    seen: OrderedDict,
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
            seen.popitem(last=False)
        seen[pid] = None
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

    seen: OrderedDict = OrderedDict()
    log.info(
        "Reddit monitor started (public JSON API) — watching: %s",
        ", ".join(f"r/{s}" for s in subreddits),
    )

    headers = {"User-Agent": _USER_AGENT}
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            global _reddit_down_until
            # Recovery detection — log once when circuit expires
            if _reddit_down_until > 0.0 and time.monotonic() >= _reddit_down_until:
                log.info("Reddit global circuit recovered — resuming normal polling")
                _reddit_down_until = 0.0
            # Global circuit breaker: skip entire cycle when IP-blocked
            if time.monotonic() < _reddit_down_until:
                remaining = (_reddit_down_until - time.monotonic()) / 60
                log.warning("Reddit global circuit open — skipping poll cycle (%.0fm remaining)", remaining)
                await asyncio.sleep(poll_interval)
                continue

            # Poll each subreddit sequentially with a stagger delay to avoid
            # bursting Reddit's rate limiter with simultaneous requests.
            for sub in subreddits:
                try:
                    await _poll_subreddit(session, sub, callback, seen)
                except Exception as exc:
                    log.warning("Unhandled error polling r/%s: %s", sub, exc)
                await asyncio.sleep(_STAGGER_DELAY)

            # Evaluate global circuit after each cycle
            if _cycle_errors:
                fail_rate = len(_cycle_errors) / len(subreddits)
                if fail_rate >= _REDDIT_OUTAGE_THRESHOLD:
                    _reddit_down_until = time.monotonic() + _REDDIT_GLOBAL_BACKOFF
                    log.warning(
                        "Reddit global circuit open — %.0f%% of subreddits failed (%d/%d), "
                        "likely IP block. Suspending all Reddit polling for %.0fm.",
                        fail_rate * 100, len(_cycle_errors), len(subreddits),
                        _REDDIT_GLOBAL_BACKOFF / 60,
                    )
            _cycle_errors.clear()

            log.debug("Reddit poll cycle complete (%d subs), sleeping %ds", len(subreddits), poll_interval)
            await asyncio.sleep(poll_interval)
