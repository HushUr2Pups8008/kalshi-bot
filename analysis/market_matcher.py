"""
Market matcher: finds Kalshi markets relevant to a news item.

Scoring strategy:
  1. Tokenize both the news headline+body and the market title+subtitle.
  2. Compute weighted Jaccard overlap between the token sets.
  3. Boost score for markets closing soon (more liquid / time-sensitive).
  4. Require a minimum similarity score (lower during paper trading).

The market list is cached and refreshed every MARKET_CACHE_TTL_SECONDS.
"""

import asyncio
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import cfg, MARKET_CACHE_TTL_SECONDS, MAX_MARKET_DAYS_TO_EXPIRY
from config import PAPER_MIN_MATCH_SCORE, PAPER_MAX_CANDIDATES, MARKET_SERIES_BLOCKLIST_PREFIXES
from feeds import NewsItem
from kalshi import KalshiMarket
from kalshi.rest_client import KalshiRestClient
from utils.logger import get_logger

log = get_logger("market_matcher")

# Live thresholds (tighter — only trade high-confidence matches)
LIVE_MIN_MATCH_SCORE = 0.06
LIVE_MAX_CANDIDATES  = 5


# ── Text tokeniser ────────────────────────────────────────────────────────────

_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "on",
    "at", "by", "for", "with", "from", "and", "or", "but", "not", "no",
    "this", "that", "it", "its", "their", "there", "if", "as", "about",
    "after", "before", "than", "when", "who", "which", "what", "how",
    # Sports betting noise words — prevent "Over 224.5" matching "war is over"
    "over", "under", "yes", "no", "scored", "points", "goals", "rebounds",
    "assists", "win", "wins", "loss", "losses", "vs", "per", "total",
}

_GEOPOLITICAL_BOOST = {
    "ukraine", "russia", "china", "taiwan", "iran", "israel", "gaza",
    "korea", "north korea", "nato", "europe", "usa", "united states",
    "pakistan", "india", "afghanistan", "syria", "iraq", "saudi",
    "president", "prime minister", "election", "military", "war", "ceasefire",
    "nuclear", "sanctions", "coup", "treaty", "summit", "un", "troops",
    "attack", "invasion", "strike", "withdraw", "deploy", "negotiate",
}


def _tokenize(text: str) -> set[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", " ", text)
    tokens = set(text.split())
    return tokens - _STOP_WORDS


def _similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union        = tokens_a | tokens_b
    if not union:
        return 0.0
    boost = sum(1.5 for t in intersection if t in _GEOPOLITICAL_BOOST)
    return min(1.0, len(intersection) / len(union) + boost * 0.03)


def _days_to_close(close_time_str: str) -> Optional[float]:
    if not close_time_str:
        return None
    try:
        from dateutil import parser as dp
        dt = dp.parse(close_time_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - datetime.now(timezone.utc)).total_seconds() / 86_400
    except Exception:
        return None


# ── Market cache ──────────────────────────────────────────────────────────────

class MarketCache:
    def __init__(self, rest_client: KalshiRestClient):
        self._client      = rest_client
        self._markets:    list[KalshiMarket] = []
        self._last_fetch: float = 0.0
        self._lock        = asyncio.Lock()

    async def get_markets(self) -> list[KalshiMarket]:
        async with self._lock:
            age = time.monotonic() - self._last_fetch
            if age > MARKET_CACHE_TTL_SECONDS or not self._markets:
                await self._refresh()
        return list(self._markets)

    async def _refresh(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            markets = await loop.run_in_executor(None, self._client.get_all_open_markets)
            filtered = []
            for m in markets:
                # Drop sports / non-geopolitical series — check both series_ticker
                # and the market ticker itself (series_ticker may be empty from API)
                series = (m.series_ticker or "").upper()
                ticker = m.ticker.upper()
                if any(series.startswith(p) or ticker.startswith(p)
                       for p in MARKET_SERIES_BLOCKLIST_PREFIXES):
                    continue
                days = _days_to_close(m.close_time)
                if days is None or 0 < days <= MAX_MARKET_DAYS_TO_EXPIRY:
                    filtered.append(m)
            self._markets    = filtered
            self._last_fetch = time.monotonic()
            log.info("Market cache refreshed: %d markets (sports/non-geo filtered)", len(filtered))
        except Exception as exc:
            log.error("Market cache refresh failed: %s", exc)


# ── Matcher ───────────────────────────────────────────────────────────────────

class MarketMatcher:
    """
    Matches a NewsItem to relevant Kalshi markets.

    During paper trading, uses lower thresholds and returns more candidates
    so we collect the maximum amount of resolution data.
    """

    def __init__(self, rest_client: KalshiRestClient):
        self._cache = MarketCache(rest_client)

    async def find_candidates(
        self, news: NewsItem, max_results: int | None = None
    ) -> list[tuple[KalshiMarket, float]]:
        """
        Return (market, score) pairs sorted by score descending.

        Thresholds and candidate count are automatically adjusted based on
        whether the bot is in paper trading mode.
        """
        is_paper     = cfg.is_paper_trading
        min_score    = PAPER_MIN_MATCH_SCORE if is_paper else LIVE_MIN_MATCH_SCORE
        max_results  = max_results or (PAPER_MAX_CANDIDATES if is_paper else LIVE_MAX_CANDIDATES)

        news_tokens = _tokenize(f"{news.headline} {news.body}")
        markets     = await self._cache.get_markets()

        scored: list[tuple[KalshiMarket, float]] = []
        for market in markets:
            market_tokens = _tokenize(f"{market.title} {market.subtitle}")
            # Require the market itself to contain at least one geopolitical token.
            # This prevents sports/financial markets from ever matching geo news.
            if not (market_tokens & _GEOPOLITICAL_BOOST):
                continue
            score = _similarity(news_tokens, market_tokens)
            if score < min_score:
                continue

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
                "[%s] Matched %d markets for '%s...' — top: %s (%.3f)",
                "PAPER" if is_paper else "LIVE",
                len(results),
                news.headline[:50],
                results[0][0].ticker,
                results[0][1],
            )

        return results

    async def refresh_cache(self) -> None:
        await self._cache._refresh()
