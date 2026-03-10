"""
Market matcher: finds Kalshi markets that are relevant to a news item.

Scoring strategy:
  1. Tokenize both the news headline+body and the market title+subtitle.
  2. Compute weighted Jaccard overlap between the token sets.
  3. Boost score for markets whose close date is soon (more liquid / relevant).
  4. Require a minimum similarity score before considering a match.

The market list is cached and refreshed every MARKET_CACHE_TTL_SECONDS seconds.
"""

import asyncio
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import cfg, MARKET_CACHE_TTL_SECONDS, MAX_MARKET_DAYS_TO_EXPIRY
from feeds import NewsItem
from kalshi import KalshiMarket
from kalshi.rest_client import KalshiRestClient
from utils.logger import get_logger

log = get_logger("market_matcher")

# Minimum token-overlap score to consider a market "relevant"
MIN_MATCH_SCORE = 0.06
# Maximum number of candidate markets to return per news item
MAX_CANDIDATES  = 5


# ── Text tokeniser ────────────────────────────────────────────────────────────

_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "on",
    "at", "by", "for", "with", "from", "and", "or", "but", "not", "no",
    "this", "that", "it", "its", "their", "there", "if", "as", "about",
    "after", "before", "than", "when", "who", "which", "what", "how",
}

_GEOPOLITICAL_BOOST = {
    # Country / region names
    "ukraine", "russia", "china", "taiwan", "iran", "israel", "gaza",
    "korea", "north korea", "nato", "europe", "usa", "united states",
    "pakistan", "india", "afghanistan", "syria", "iraq", "saudi",
    # Key actors
    "president", "prime minister", "election", "military", "war", "ceasefire",
    "nuclear", "sanctions", "coup", "treaty", "summit", "nato", "un",
    # Action words
    "attack", "invasion", "strike", "withdraw", "deploy", "negotiate",
}


def _tokenize(text: str) -> set[str]:
    text = text.lower()
    # Remove punctuation except hyphens within words
    text = re.sub(r"[^\w\s-]", " ", text)
    tokens = set(text.split())
    return tokens - _STOP_WORDS


def _similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    """Weighted Jaccard similarity with geopolitical term boosting."""
    if not tokens_a or not tokens_b:
        return 0.0

    intersection = tokens_a & tokens_b
    union        = tokens_a | tokens_b

    if not union:
        return 0.0

    # Boost score for geopolitically important terms
    boost = sum(1.5 for t in intersection if t in _GEOPOLITICAL_BOOST)
    raw   = len(intersection) / len(union)
    return min(1.0, raw + boost * 0.03)


def _days_to_close(close_time_str: str) -> Optional[float]:
    """Parse close_time and return days until closing. None if unparseable."""
    if not close_time_str:
        return None
    try:
        from dateutil import parser as dp
        dt = dp.parse(close_time_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds() / 86_400
        return delta
    except Exception:
        return None


# ── Market cache ──────────────────────────────────────────────────────────────

class MarketCache:
    """Thread-safe in-memory cache of open Kalshi markets."""

    def __init__(self, rest_client: KalshiRestClient):
        self._client    = rest_client
        self._markets:  list[KalshiMarket] = []
        self._last_fetch: float = 0.0
        self._lock      = asyncio.Lock()

    async def get_markets(self) -> list[KalshiMarket]:
        async with self._lock:
            age = time.monotonic() - self._last_fetch
            if age > MARKET_CACHE_TTL_SECONDS or not self._markets:
                await self._refresh()
        return list(self._markets)

    async def _refresh(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            markets = await loop.run_in_executor(
                None, self._client.get_all_open_markets
            )
            # Filter to markets closing within MAX_MARKET_DAYS_TO_EXPIRY
            now = datetime.now(timezone.utc)
            cutoff = now + timedelta(days=MAX_MARKET_DAYS_TO_EXPIRY)
            filtered = []
            for m in markets:
                days = _days_to_close(m.close_time)
                if days is not None and 0 < days <= MAX_MARKET_DAYS_TO_EXPIRY:
                    filtered.append(m)
                elif days is None:
                    filtered.append(m)  # keep if we can't parse the date

            self._markets   = filtered
            self._last_fetch = time.monotonic()
            log.info("Market cache refreshed: %d markets (after expiry filter)", len(filtered))
        except Exception as exc:
            log.error("Market cache refresh failed: %s", exc)


# ── Matcher ───────────────────────────────────────────────────────────────────

class MarketMatcher:
    """
    Matches a NewsItem to relevant Kalshi markets.

    Usage:
        matcher = MarketMatcher(rest_client)
        candidates = await matcher.find_candidates(news_item)
    """

    def __init__(self, rest_client: KalshiRestClient):
        self._cache = MarketCache(rest_client)

    async def find_candidates(
        self, news: NewsItem, max_results: int = MAX_CANDIDATES
    ) -> list[tuple[KalshiMarket, float]]:
        """
        Return up to max_results (market, score) pairs, sorted by score descending.

        Only returns markets with score >= MIN_MATCH_SCORE.
        """
        news_tokens = _tokenize(f"{news.headline} {news.body}")
        markets     = await self._cache.get_markets()

        scored: list[tuple[KalshiMarket, float]] = []
        for market in markets:
            market_tokens = _tokenize(f"{market.title} {market.subtitle}")
            score = _similarity(news_tokens, market_tokens)

            if score < MIN_MATCH_SCORE:
                continue

            # Urgency bonus: markets closing sooner are more sensitive to news
            days = _days_to_close(market.close_time)
            if days is not None:
                if days <= 1:
                    score *= 1.5
                elif days <= 7:
                    score *= 1.2
                elif days <= 14:
                    score *= 1.1

            scored.append((market, round(score, 4)))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = scored[:max_results]

        if results:
            log.debug(
                "Matched %d markets for: '%s...' — top: %s (%.3f)",
                len(results),
                news.headline[:50],
                results[0][0].ticker,
                results[0][1],
            )

        return results

    async def refresh_cache(self) -> None:
        """Force a market cache refresh (e.g. called periodically from main loop)."""
        await self._cache._refresh()
