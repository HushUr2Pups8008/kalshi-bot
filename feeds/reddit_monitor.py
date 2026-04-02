"""
Reddit monitor -- OAuth2 authenticated (preferred) or public JSON fallback.

When REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are set in .env, uses Reddit's
OAuth2 client_credentials flow via oauth.reddit.com (600 req / 10 min).
Without credentials, falls back to the unauthenticated www.reddit.com/r/.json
endpoints which are aggressively rate-limited and IP-blocked.
"""

import asyncio
import hashlib
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable, Awaitable, Optional

import aiohttp

from config import cfg, REDDIT_SUBREDDITS, REDDIT_MIN_SCORE
from feeds import NewsItem
from utils.logger import get_logger

log = get_logger("reddit_monitor")

REDDIT_POLL_INTERVAL  = 300   # seconds between full poll cycles
FETCH_LIMIT           = 25    # posts per request
SCORE_RECHECK_DELAY   = 90    # seconds before rechecking score
MAX_SEEN              = 2_000
_STAGGER_DELAY        = 10    # seconds between each subreddit request (avoids burst)
_STAGGER_DELAY_OAUTH  = 3     # shorter stagger under OAuth (generous rate limit)
_MAX_BACKOFF          = 300   # max 429 backoff in seconds
_REDDIT_GLOBAL_BACKOFF   = 1800   # 30-min global suspension when IP-blocked (public mode)
_REDDIT_GLOBAL_BACKOFF_OAUTH = 600  # 10-min pause on OAuth 429 burst
_REDDIT_OUTAGE_THRESHOLD = 0.5    # fraction of subreddits failing triggers global backoff
_reddit_down_until: float = 0.0   # monotonic -- skip all Reddit until this time
_cycle_errors: list[int] = []     # 403/429 status codes collected during current poll cycle

# Reddit requires a descriptive User-Agent to avoid rate limiting
_USER_AGENT   = "KalshiBot/1.0 (geopolitical news monitor; no login required)"
_PUBLIC_URL   = "https://www.reddit.com/r/{subreddit}/new.json?limit={limit}&raw_json=1"
_OAUTH_URL    = "https://oauth.reddit.com/r/{subreddit}/new.json?limit={limit}&raw_json=1"
_PUBLIC_COMMENT_URL = "https://www.reddit.com/r/{subreddit}/comments/{post_id}.json?raw_json=1"
_OAUTH_COMMENT_URL  = "https://oauth.reddit.com/r/{subreddit}/comments/{post_id}.json?raw_json=1"
_TOKEN_URL    = "https://www.reddit.com/api/v1/access_token"


# ---------------------------------------------------------------------------
# OAuth2 token manager
# ---------------------------------------------------------------------------

class _RedditAuth:
    """Manages Reddit OAuth2 client_credentials token lifecycle."""

    def __init__(self, client_id: str, client_secret: str, user_agent: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent
        self._token: Optional[str] = None
        self._expires_at: float = 0.0  # monotonic
        self._credential_failed = False  # permanent flag if creds are bad

    @property
    def available(self) -> bool:
        """False if credentials were rejected by Reddit (stops retrying)."""
        return not self._credential_failed

    async def ensure_token(self, session: aiohttp.ClientSession) -> Optional[str]:
        """Return a valid bearer token, refreshing if needed."""
        if self._credential_failed:
            return None
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        return await self._fetch_token(session)

    async def _fetch_token(self, session: aiohttp.ClientSession) -> Optional[str]:
        """POST to Reddit's token endpoint with client_credentials grant."""
        try:
            async with session.post(
                _TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=aiohttp.BasicAuth(self._client_id, self._client_secret),
                headers={"User-Agent": self._user_agent},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 401:
                    log.error(
                        "Reddit OAuth credentials rejected (401) -- check "
                        "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env. "
                        "Falling back to public JSON for this session."
                    )
                    self._credential_failed = True
                    self._token = None
                    return None
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    log.warning("Reddit token endpoint rate-limited -- retry in %ds", retry_after)
                    await asyncio.sleep(retry_after)
                    return await self._fetch_token(session)  # one retry
                if resp.status != 200:
                    log.warning("Reddit token request returned HTTP %d", resp.status)
                    self._token = None
                    return None

                body = await resp.json()
                self._token = body.get("access_token")
                expires_in = int(body.get("expires_in", 3600))
                # Refresh 60s early to avoid using an expiring token
                self._expires_at = time.monotonic() + expires_in - 60
                log.info("Reddit OAuth token acquired (expires in %ds)", expires_in)
                return self._token
        except Exception as exc:
            log.warning("Reddit token request failed: %s", exc)
            self._token = None
            return None

    def invalidate(self) -> None:
        """Force token refresh on next call (e.g. after HTTP 401 on a request)."""
        self._token = None
        self._expires_at = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_id(post_id: str) -> str:
    return hashlib.sha256(post_id.encode()).hexdigest()


_backoff:       dict[str, float] = {}  # per-subreddit resume-time (monotonic clock)
_backoff_delay: dict[str, float] = {}  # per-subreddit current delay for exponential growth


async def _fetch_subreddit(
    session: aiohttp.ClientSession,
    subreddit: str,
    auth: Optional[_RedditAuth] = None,
) -> list[dict]:
    # Honour any active backoff for this subreddit
    if time.monotonic() < _backoff.get(subreddit, 0.0):
        log.debug("r/%s in backoff -- skipping this cycle", subreddit)
        return []

    # Build URL and headers based on auth mode
    use_oauth = auth is not None and auth.available
    token = None
    if use_oauth:
        token = await auth.ensure_token(session)
        use_oauth = token is not None  # degrade if token fetch failed

    if use_oauth:
        url = _OAUTH_URL.format(subreddit=subreddit, limit=FETCH_LIMIT)
        headers = {"Authorization": f"Bearer {token}"}
    else:
        url = _PUBLIC_URL.format(subreddit=subreddit, limit=FETCH_LIMIT)
        headers = {}

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 401 and use_oauth:
                # Token expired or revoked mid-cycle -- refresh and retry once
                auth.invalidate()
                token = await auth.ensure_token(session)
                if token:
                    retry_url = _OAUTH_URL.format(subreddit=subreddit, limit=FETCH_LIMIT)
                    async with session.get(
                        retry_url,
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as retry_resp:
                        if retry_resp.status == 200:
                            data = await retry_resp.json()
                            _backoff[subreddit] = 0.0
                            _backoff_delay.pop(subreddit, None)
                            return data.get("data", {}).get("children", [])
                        log.warning("r/%s OAuth retry returned HTTP %d", subreddit, retry_resp.status)
                        return []
                return []

            if resp.status == 429:
                delay = min(_backoff_delay.get(subreddit, 30.0) * 2, _MAX_BACKOFF)
                _backoff_delay[subreddit] = delay
                _backoff[subreddit] = time.monotonic() + delay
                _cycle_errors.append(429)
                log.warning("Reddit rate limit hit for r/%s -- backing off %.0fs", subreddit, delay)
                return []
            if resp.status == 403:
                if use_oauth:
                    # Under OAuth, 403 means private/quarantined subreddit, not IP block.
                    # Use short backoff, do NOT count toward global circuit breaker.
                    log.debug("r/%s returned 403 (private/quarantined?) under OAuth", subreddit)
                    _backoff[subreddit] = time.monotonic() + 600  # skip for 10 min
                    return []
                # Public mode: 403 = IP block
                delay = min(_backoff_delay.get(subreddit, 60.0) * 2, _MAX_BACKOFF)
                _backoff_delay[subreddit] = delay
                _backoff[subreddit] = time.monotonic() + delay
                _cycle_errors.append(403)
                log.warning("Reddit access denied for r/%s (403) -- backing off %.0fs", subreddit, delay)
                return []
            _backoff[subreddit] = 0.0          # 0.0 < monotonic() always -> not in backoff
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
    auth: Optional[_RedditAuth] = None,
) -> None:
    """Wait for upvotes to accumulate, then emit if above threshold."""
    await asyncio.sleep(SCORE_RECHECK_DELAY)

    post_id = post.get("id", "")

    # Build URL based on auth mode
    use_oauth = auth is not None and auth.available
    token = await auth.ensure_token(session) if use_oauth else None
    if token:
        url = _OAUTH_COMMENT_URL.format(subreddit=subreddit, post_id=post_id)
        headers = {"Authorization": f"Bearer {token}"}
    else:
        url = _PUBLIC_COMMENT_URL.format(subreddit=subreddit, post_id=post_id)
        headers = {}

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
    auth: Optional[_RedditAuth] = None,
) -> None:
    children = await _fetch_subreddit(session, subreddit, auth)
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
        asyncio.create_task(_score_recheck_and_emit(session, post, subreddit, callback, auth))
    if new_count:
        log.debug("r/%s: %d new posts queued for score recheck", subreddit, new_count)


async def run_reddit_monitor(
    callback: Callable[[NewsItem], Awaitable[None]],
    subreddits: "list[str] | Callable[[], Awaitable[list[str]]] | None" = None,
    poll_interval: int = REDDIT_POLL_INTERVAL,
) -> None:
    """
    Poll Reddit subreddits for geopolitical news.
    Uses OAuth2 when credentials are available, otherwise public JSON.
    Runs indefinitely; cancel the task to stop.

    subreddits may be:
      - None              -> use the full REDDIT_SUBREDDITS fallback list
      - list[str]         -> use that static list every cycle
      - async callable    -> call each cycle to get the current list (adaptive mode)
    """
    # Normalize subreddits to a uniform async callable
    if subreddits is None:
        _static = REDDIT_SUBREDDITS
        async def _get_subs() -> list[str]: return _static
    elif isinstance(subreddits, list):
        _static = subreddits
        async def _get_subs() -> list[str]: return _static
    else:
        _get_subs = subreddits

    seen: OrderedDict = OrderedDict()

    # Set up OAuth if credentials are configured
    auth: Optional[_RedditAuth] = None
    if cfg.reddit_oauth_available:
        user_agent = cfg.reddit_user_agent or _USER_AGENT
        auth = _RedditAuth(cfg.reddit_client_id, cfg.reddit_client_secret, user_agent)
        log.info("Reddit monitor started (OAuth2) -- adaptive subreddit selection active")
    else:
        log.info(
            "Reddit monitor started (public JSON -- degraded, expect rate limits). "
            "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env for OAuth2."
        )

    stagger = _STAGGER_DELAY_OAUTH if auth else _STAGGER_DELAY
    user_agent = (cfg.reddit_user_agent or _USER_AGENT) if auth else _USER_AGENT
    headers = {"User-Agent": user_agent}

    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            global _reddit_down_until
            # Recovery detection -- log once when circuit expires
            if _reddit_down_until > 0.0 and time.monotonic() >= _reddit_down_until:
                log.info("Reddit global circuit recovered -- resuming normal polling")
                _reddit_down_until = 0.0
            # Global circuit breaker: skip entire cycle when blocked
            if time.monotonic() < _reddit_down_until:
                remaining = (_reddit_down_until - time.monotonic()) / 60
                log.warning("Reddit global circuit open -- skipping poll cycle (%.0fm remaining)", remaining)
                await asyncio.sleep(poll_interval)
                continue

            # Resolve subreddit list for this cycle (adaptive or static)
            current_subs = await _get_subs()
            log.debug("Reddit cycle: polling %d subreddits", len(current_subs))

            # Poll each subreddit sequentially with a stagger delay
            for sub in current_subs:
                try:
                    await _poll_subreddit(session, sub, callback, seen, auth)
                except Exception as exc:
                    log.warning("Unhandled error polling r/%s: %s", sub, exc)
                await asyncio.sleep(stagger)

            # Evaluate global circuit after each cycle
            # Under OAuth, only 429s count (403 = private sub, not IP block).
            # Under public mode, both 403 and 429 count.
            if _cycle_errors:
                fail_rate = len(_cycle_errors) / max(len(current_subs), 1)
                if fail_rate >= _REDDIT_OUTAGE_THRESHOLD:
                    backoff_duration = (
                        _REDDIT_GLOBAL_BACKOFF_OAUTH if auth and auth.available
                        else _REDDIT_GLOBAL_BACKOFF
                    )
                    _reddit_down_until = time.monotonic() + backoff_duration
                    log.warning(
                        "Reddit global circuit open -- %.0f%% of subreddits failed (%d/%d), "
                        "suspending all Reddit polling for %.0fm.",
                        fail_rate * 100, len(_cycle_errors), len(current_subs),
                        backoff_duration / 60,
                    )
            _cycle_errors.clear()

            log.debug("Reddit poll cycle complete (%d subs), sleeping %ds", len(current_subs), poll_interval)
            await asyncio.sleep(poll_interval)
