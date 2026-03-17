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

# Named geo entities specific enough that a single token overlap is meaningful.
# Used in the tiered headline gate and geo-coherence edge checks.
# Generic conflict words (war, attack, bank, people) are NOT included here --
# those require 2+ overlaps to pass the gate.
_GEO_NAMED_ENTITIES = frozenset({
    # Country names
    "ukraine", "russia", "china", "taiwan", "iran", "israel", "gaza",
    "korea", "pakistan", "india", "japan", "turkey", "saudi", "syria",
    "iraq", "afghanistan", "venezuela", "cuba", "mexico", "canada",
    "france", "germany", "britain", "lebanon", "hamas", "hezbollah",
    # Adjective / demonym forms
    "russian", "chinese", "iranian", "ukrainian", "korean", "israeli",
    "european", "american", "british", "french", "german", "turkish",
    "japanese", "lebanese", "iraqi", "syrian", "saudi",
    # Key individuals
    "zelensky", "zelenskyy", "putin", "trump", "biden", "netanyahu",
    "khamenei", "hegseth", "modi",
})


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


# ── Series discovery ──────────────────────────────────────────────────────────
#
# Kalshi's market catalogue has shifted away from organised geo series
# (e.g. KXUKR, KXINTL) toward thousands of one-off series per topic.
# We discover political/geo series by keyword-matching their titles, then
# fetch markets only for matched series — avoiding 10k+ sports parlays.
#
# The keywords here cast a wide net; the _GEOPOLITICAL_BOOST check inside
# find_candidates() acts as the final precision gate at match time.

_GEO_SERIES_KEYWORDS = frozenset({
    # Countries / regions
    "russia", "ukraine", "china", "taiwan", "iran", "israel", "gaza",
    "korea", "nato", "pakistan", "india", "japan", "turkey", "saudi",
    "europe", "european", "syria", "iraq", "afghanistan", "venezuela",
    "cuba", "mexico", "canada", "france", "germany", "britain",
    # Political leaders
    "zelensky", "putin", "trump",
    # Political roles / institutions
    "president", "senator", "congress", "senate", "parliament",
    "governor", "chancellor", "minister", "supreme court",
    # US politics
    "republican", "democrat", "cabinet", "impeach",
    "tariff", "executive", "legislation",
    # US domestic policy (expanded)
    "budget", "regulation", "nomination", "confirmation",
    "department of",           # cabinet dept titles
    "doge",                    # Dept of Government Efficiency
    # Events / actions
    "election", "ceasefire", "invasion", "military",
    "nuclear", "sanctions", "summit", "treaty", "coup",
    # International topics
    "diplomatic", "foreign policy", "united nations",
    # Policy
    "healthcare", "immigration", "climate",
    # Economic / financial
    "inflation", "gdp", "federal reserve",
    # AI / tech policy
    "ai safety", "ai regulation", "artificial intelligence",
})


def _is_geo_series(series: dict) -> bool:
    """Return True if the series title contains a geo/political keyword."""
    title = (series.get("title") or series.get("ticker") or "").lower()
    return any(kw in title for kw in _GEO_SERIES_KEYWORDS)


# ── Market cache ──────────────────────────────────────────────────────────────

class MarketCache:
    def __init__(self, rest_client: KalshiRestClient):
        self._client          = rest_client
        self._markets:        list[KalshiMarket] = []
        self._last_fetch:     float = 0.0
        self._all_markets:    list[KalshiMarket] = []
        self._all_last_fetch: float = 0.0
        self._lock            = asyncio.Lock()
        self._all_lock        = asyncio.Lock()

    async def get_markets(self) -> list[KalshiMarket]:
        async with self._lock:
            age = time.monotonic() - self._last_fetch
            if age > MARKET_CACHE_TTL_SECONDS or not self._markets:
                await self._refresh()
        return list(self._markets)

    async def _refresh(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            markets, n_series = await loop.run_in_executor(
                None, self._fetch_geo_markets
            )
            self._markets    = markets
            self._last_fetch = time.monotonic()
            log.info(
                "Market cache refreshed: %d geo markets from %d series",
                len(markets), n_series,
            )
        except Exception as exc:
            log.error("Market cache refresh failed: %s", exc)

    def _fetch_geo_markets(self) -> tuple[list, int]:
        """
        Synchronous: discover geo/political series then fetch their open markets.

        Strategy:
          1. Fetch all Kalshi series (~8k+) from /series endpoint.
          2. Keyword-match series titles to identify geo/political ones.
          3. For each matched series, fetch open markets.
          4. Apply the days-to-expiry filter.

        Runs in a thread pool executor so it doesn't block the event loop.
        """
        all_series = self._client.get_all_series()

        # Pass 1: keyword-match series titles to find geo/political candidates
        keyword_matched = [s for s in all_series if _is_geo_series(s)]

        # Pass 2: drop sports leagues from geo-relevant countries (e.g. Saudi
        # Pro League matches "saudi", J-League matches "japan").
        geo_tickers = [
            s["ticker"] for s in keyword_matched
            if not any(
                s["ticker"].upper().startswith(p)
                for p in MARKET_SERIES_BLOCKLIST_PREFIXES
            )
        ]
        log.info(
            "Series discovery: %d geo/political of %d total (%d dropped by sports blocklist)",
            len(geo_tickers), len(all_series), len(keyword_matched) - len(geo_tickers),
        )

        filtered = []
        for series_ticker in geo_tickers:
            try:
                page, _ = self._client.get_markets(
                    series_ticker=series_ticker, limit=200
                )
                for m in page:
                    days = _days_to_close(m.close_time)
                    if days is None or 0 < days <= MAX_MARKET_DAYS_TO_EXPIRY:
                        filtered.append(m)
            except Exception as exc:
                log.debug("Skipping series %s: %s", series_ticker, exc)

        return filtered, len(geo_tickers)

    async def get_all_markets(self) -> list[KalshiMarket]:
        """
        Return all active Kalshi markets (up to 2000), regardless of category.
        Used exclusively by the fade signal to search sports + geo markets.
        Cached for MARKET_CACHE_TTL_SECONDS (same as geo cache).
        """
        async with self._all_lock:
            age = time.monotonic() - self._all_last_fetch
            if age > MARKET_CACHE_TTL_SECONDS or not self._all_markets:
                await self._refresh_all()
        return list(self._all_markets)

    async def _refresh_all(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            markets = await loop.run_in_executor(None, self._fetch_all_markets)
            self._all_markets    = markets
            self._all_last_fetch = time.monotonic()
            log.info("All-markets cache refreshed: %d active markets", len(markets))
        except Exception as exc:
            log.error("All-markets cache refresh failed: %s", exc)

    def _fetch_all_markets(self) -> list[KalshiMarket]:
        """
        Synchronous: page through all active markets (up to 10 pages × 200 = 2000).
        Applies the same days-to-expiry filter as the geo cache.
        Runs in a thread pool executor.
        """
        markets = []
        cursor  = None
        for _ in range(10):
            page, cursor = self._client.get_markets(
                status="open", cursor=cursor, limit=200
            )
            for m in page:
                days = _days_to_close(m.close_time)
                if days is None or 0 < days <= MAX_MARKET_DAYS_TO_EXPIRY:
                    markets.append(m)
            if not cursor:
                break
        return markets


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

        headline_tokens = _tokenize(news.headline)

        scored: list[tuple[KalshiMarket, float]] = []
        for market in markets:
            market_title_tokens = _tokenize(market.title)
            market_tokens = market_title_tokens | _tokenize(market.subtitle)
            # Require the market itself to contain at least one geopolitical token.
            # This prevents sports/financial markets from ever matching geo news.
            if not (market_tokens & _GEOPOLITICAL_BOOST):
                continue
            # Require at least one MEANINGFUL token from the news HEADLINE to
            # appear in the market title. Filter out short/numeric tokens first
            # to prevent date fragments like '1' (from 'Apr 1') or 's' (from
            # 'U.S.') from creating false positives.
            meaningful_hl = {t for t in headline_tokens if len(t) >= 3 and not t.isdigit()}
            meaningful_mt = {t for t in market_title_tokens if len(t) >= 3 and not t.isdigit()}
            overlap = meaningful_hl & meaningful_mt
            # Tiered gate: a specific named geo-entity (country, person) is
            # distinctive enough to pass alone. Generic words like "bank",
            # "people", "war", "attack" are too common -- require 2+ of them.
            geo_overlap     = overlap & _GEO_NAMED_ENTITIES
            generic_overlap = overlap - _GEO_NAMED_ENTITIES
            if not geo_overlap and len(generic_overlap) < 2:
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

    async def find_all_candidates(
        self, news: NewsItem, max_results: int = 1
    ) -> list[tuple[KalshiMarket, float]]:
        """
        Like find_candidates() but searches ALL active markets (geo + sports + entertainment).
        Used exclusively by the fade signal pipeline.

        Applies a relaxed gate: Jaccard similarity only, no geopolitical boost requirement,
        no named-entity gate. This allows sports/entertainment markets to match.
        """
        _FADE_MIN_SCORE = 0.02

        news_tokens = _tokenize(f"{news.headline} {news.body}")
        markets     = await self._cache.get_all_markets()

        scored: list[tuple[KalshiMarket, float]] = []
        for market in markets:
            if market.status not in ("open", "active"):
                continue
            market_tokens = _tokenize(market.title) | _tokenize(market.subtitle)
            if not market_tokens:
                continue
            score = _similarity(news_tokens, market_tokens)
            if score < _FADE_MIN_SCORE:
                continue
            days = _days_to_close(market.close_time)
            if days is not None:
                if days <= 0:
                    continue
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
                "[FADE] Matched %d market(s) for '%s...' — top: %s (%.3f)",
                len(results), news.headline[:50],
                results[0][0].ticker, results[0][1],
            )
        return results

    async def refresh_cache(self) -> None:
        await self._cache._refresh()
