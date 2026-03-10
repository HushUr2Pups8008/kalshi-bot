"""
Reddit monitor.

Streams new posts from the configured subreddits using PRAW's async-compatible
streaming. Posts below REDDIT_MIN_SCORE are ignored (checked after a short
delay to let upvotes accumulate).

Falls back gracefully if Reddit credentials are missing.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable

from config import cfg, REDDIT_SUBREDDITS, REDDIT_MIN_SCORE
from feeds import NewsItem
from utils.logger import get_logger

log = get_logger("reddit_monitor")

# How often to poll the subreddit "new" listing (seconds)
REDDIT_POLL_INTERVAL = 120
# Max posts to fetch per poll cycle
FETCH_LIMIT = 25
# Seconds to wait before re-checking score (let upvotes accumulate)
SCORE_RECHECK_DELAY = 90

MAX_SEEN = 2_000


def _make_id(post_id: str) -> str:
    return hashlib.sha256(post_id.encode()).hexdigest()


async def _check_and_emit(
    submission,
    callback: Callable[[NewsItem], Awaitable[None]],
    delay: float = SCORE_RECHECK_DELAY,
) -> None:
    """Wait a bit, then check score and emit if above threshold."""
    await asyncio.sleep(delay)
    try:
        submission.comments  # triggers lazy load / refresh
        score = submission.score
    except Exception:
        score = 0

    if score < REDDIT_MIN_SCORE:
        log.debug("Reddit post below score threshold (%d < %d): %s",
                  score, REDDIT_MIN_SCORE, submission.title[:60])
        return

    item = NewsItem(
        headline=submission.title,
        url=f"https://www.reddit.com{submission.permalink}",
        source=f"r/{submission.subreddit.display_name}",
        published=datetime.fromtimestamp(submission.created_utc, tz=timezone.utc),
        body=submission.selftext[:500] if submission.is_self else submission.url,
        item_id=_make_id(submission.id),
    )
    await callback(item)


async def run_reddit_monitor(
    callback: Callable[[NewsItem], Awaitable[None]],
    subreddits: list[str] | None = None,
    poll_interval: int = REDDIT_POLL_INTERVAL,
) -> None:
    """
    Poll Reddit subreddits for new posts and invoke callback for qualifying ones.

    Requires REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET to be set.
    If credentials are absent, logs a warning and exits without error.
    """
    if not cfg.reddit_client_id or not cfg.reddit_client_secret:
        log.warning(
            "Reddit credentials not configured — Reddit monitor disabled. "
            "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env to enable."
        )
        return

    try:
        import praw
    except ImportError:
        log.error("praw not installed — run: pip install praw")
        return

    if subreddits is None:
        subreddits = REDDIT_SUBREDDITS

    reddit = praw.Reddit(
        client_id=cfg.reddit_client_id,
        client_secret=cfg.reddit_client_secret,
        user_agent=cfg.reddit_user_agent,
    )

    loop     = asyncio.get_event_loop()
    seen: set[str] = set()
    sub_str  = "+".join(subreddits)
    log.info("Reddit monitor started — watching r/%s", sub_str)

    while True:
        try:
            subreddit = await loop.run_in_executor(None, reddit.subreddit, sub_str)
            posts = await loop.run_in_executor(
                None, lambda: list(subreddit.new(limit=FETCH_LIMIT))
            )
            new_count = 0
            for post in posts:
                pid = _make_id(post.id)
                if pid in seen:
                    continue
                if len(seen) >= MAX_SEEN:
                    seen.pop()
                seen.add(pid)
                new_count += 1
                # Schedule delayed score-check without blocking the poll loop
                asyncio.create_task(_check_and_emit(post, callback))

            if new_count:
                log.debug("Reddit: %d new posts queued for score recheck", new_count)

        except Exception as exc:
            log.warning("Reddit poll error: %s", exc)

        await asyncio.sleep(poll_interval)
