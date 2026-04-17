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
from config import ENABLE_LOW_QUALITY_MATCH_SUPPRESSION, ENABLE_MATCH_SUPPRESSION_DEBUG
from feeds import NewsItem
from kalshi import KalshiMarket
from kalshi.rest_client import KalshiRestClient
from utils.logger import get_logger, trade_log

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
    # Countries / regions
    "ukraine", "russia", "china", "taiwan", "iran", "israel", "gaza",
    "korea", "north korea", "nato", "europe", "usa", "united states",
    "pakistan", "india", "afghanistan", "syria", "iraq", "saudi",
    # Political roles / actions
    "president", "prime minister", "election", "military", "war", "ceasefire",
    "nuclear", "sanctions", "coup", "treaty", "summit", "un", "troops",
    "attack", "invasion", "strike", "withdraw", "deploy", "negotiate",
    # Trade & economic policy
    "tariff", "tariffs", "trade", "import", "export", "embargo", "customs",
    "commerce", "reciprocal", "duty", "duties", "liberation",
    # Foreign policy
    "diplomatic", "ambassador", "embassy", "alliance", "pact", "foreign",
    "bilateral", "multilateral", "sovereignty", "extradition",
    # Domestic policy (US-focused)
    "executive order", "shutdown", "impeach", "impeachment", "cabinet",
    "congress", "senate", "legislation", "regulation", "nomination",
    "confirmation", "indictment", "pardon", "veto",
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
    "brazil", "colombia", "philippines", "vietnam", "thailand",
    "egypt", "libya", "yemen", "somalia", "sudan", "ethiopia",
    # Adjective / demonym forms
    "russian", "chinese", "iranian", "ukrainian", "korean", "israeli",
    "european", "american", "british", "french", "german", "turkish",
    "japanese", "lebanese", "iraqi", "syrian", "saudi", "canadian",
    "mexican", "brazilian", "colombian", "egyptian",
    # Key individuals
    "zelensky", "zelenskyy", "putin", "trump", "biden", "netanyahu",
    "khamenei", "hegseth", "modi", "macron", "scholz", "starmer",
    "vance", "rubio", "waltz",
    # Institutions / orgs (distinctive enough for single-token match)
    "nato", "pentagon", "kremlin", "congress", "senate",
})


def _tokenize(text: str) -> set[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", " ", text)
    tokens = set(text.split())
    return tokens - _STOP_WORDS


def _meaningful_tokens(tokens: set[str]) -> set[str]:
    return {t for t in tokens if len(t) >= 3 and not t.isdigit()}


def _similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union        = tokens_a | tokens_b
    if not union:
        return 0.0
    boost = sum(1.5 for t in intersection if t in _GEOPOLITICAL_BOOST)
    return min(1.0, len(intersection) / len(union) + boost * 0.03)


def _structure_quality_flags(
    *,
    overlap: set[str],
    geo_overlap: set[str],
    generic_overlap: set[str],
    headline_meaningful: set[str],
    market_title_meaningful: set[str],
) -> list[str]:
    flags: list[str] = []
    overlap_ratio = len(overlap) / max(1, min(len(headline_meaningful), len(market_title_meaningful)))
    if overlap_ratio < 0.2:
        flags.append("low_token_overlap")
    if len(geo_overlap) == 1 and len(generic_overlap) == 0:
        flags.append("single_named_entity_only")
    if len(overlap) <= 1:
        flags.append("minimal_overlap")
    return flags


def _weak_match_penalty_multiplier(flags: set[str]) -> float:
    """Apply a lightweight penalty to structurally weak matches before thresholding."""
    if "single_named_entity_only" in flags and "minimal_overlap" in flags:
        return 0.75
    if "single_named_entity_only" in flags:
        return 0.9
    return 1.0


def _match_quality_flags(
    *,
    overlap: set[str],
    geo_overlap: set[str],
    generic_overlap: set[str],
    headline_meaningful: set[str],
    market_title_meaningful: set[str],
    score: float,
    min_score: float,
) -> list[str]:
    flags = _structure_quality_flags(
        overlap=overlap,
        geo_overlap=geo_overlap,
        generic_overlap=generic_overlap,
        headline_meaningful=headline_meaningful,
        market_title_meaningful=market_title_meaningful,
    )
    if score <= min_score + 0.02:
        flags.append("near_threshold_score")
    return flags


def _days_to_close(close_time_str: str) -> Optional[float]:
    if not close_time_str:
        return None
    try:
        from dateutil import parser as dp, tz as dtz
        _TZ = {
            "EST": dtz.tzoffset("EST", -5 * 3600),
            "EDT": dtz.tzoffset("EDT", -4 * 3600),
            "CST": dtz.tzoffset("CST", -6 * 3600),
            "CDT": dtz.tzoffset("CDT", -5 * 3600),
            "MST": dtz.tzoffset("MST", -7 * 3600),
            "MDT": dtz.tzoffset("MDT", -6 * 3600),
            "PST": dtz.tzoffset("PST", -8 * 3600),
            "PDT": dtz.tzoffset("PDT", -7 * 3600),
        }
        dt = dp.parse(close_time_str, tzinfos=_TZ)
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
    "brazil", "colombia", "philippines", "egypt", "libya", "yemen",
    "somalia", "sudan", "ethiopia",
    # Political leaders
    "zelensky", "putin", "trump", "vance", "rubio",
    # Political roles / institutions
    "president", "senator", "congress", "senate", "parliament",
    "governor", "chancellor", "minister", "supreme court",
    # US politics
    "republican", "democrat", "cabinet", "impeach",
    "executive", "legislation",
    # US domestic policy
    "budget", "regulation", "nomination", "confirmation",
    "department of",           # cabinet dept titles
    "doge",                    # Dept of Government Efficiency
    "shutdown", "debt ceiling", "indictment", "pardon",
    "executive order", "national emergency",
    # Trade & economic policy
    "tariff", "tariffs", "trade", "import", "export",
    "embargo", "customs", "commerce", "duty", "reciprocal",
    "liberation day",          # Trump tariff branding
    "trade war", "trade deal", "trade agreement",
    # Foreign policy
    "diplomatic", "foreign policy", "united nations",
    "ambassador", "embassy", "alliance", "bilateral",
    "sovereignty", "extradition", "deportation",
    # Events / actions
    "election", "ceasefire", "invasion", "military",
    "nuclear", "sanctions", "summit", "treaty", "coup",
    # Policy
    "healthcare", "immigration", "climate", "border",
    # Economic / financial
    "inflation", "gdp", "federal reserve", "recession",
    "interest rate", "treasury", "deficit",
    # AI / tech policy
    "ai safety", "ai regulation", "artificial intelligence",
})


def _is_geo_series(series: dict) -> bool:
    """Return True if the series title contains a geo/political keyword."""
    title = (series.get("title") or series.get("ticker") or "").lower()
    return any(kw in title for kw in _GEO_SERIES_KEYWORDS)


# ── Market cache ──────────────────────────────────────────────────────────────

_REFRESH_DEBOUNCE_SECONDS = 60  # min seconds between back-to-back refreshes
_TEST_MARKET_TICKER_PREFIX = "KXTEST"


def _is_excluded_test_market(market: KalshiMarket) -> bool:
    """Return True for explicit test-market tickers that must never enter shared caches."""
    return market.ticker.upper().startswith(_TEST_MARKET_TICKER_PREFIX)


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
        if time.monotonic() - self._last_fetch < _REFRESH_DEBOUNCE_SECONDS:
            log.debug("Market cache refresh debounced (last refresh %.0fs ago)", time.monotonic() - self._last_fetch)
            return
        loop = asyncio.get_running_loop()
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
                    if _is_excluded_test_market(m):
                        continue
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
        if time.monotonic() - self._all_last_fetch < _REFRESH_DEBOUNCE_SECONDS:
            log.debug("All-markets cache refresh debounced (last refresh %.0fs ago)", time.monotonic() - self._all_last_fetch)
            return
        loop = asyncio.get_running_loop()
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
                if _is_excluded_test_market(m):
                    continue
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
            meaningful_hl = _meaningful_tokens(headline_tokens)
            meaningful_mt = _meaningful_tokens(market_title_tokens)
            overlap = meaningful_hl & meaningful_mt
            # Tiered gate: a specific named geo-entity (country, person) is
            # distinctive enough to pass alone. Generic words like "bank",
            # "people", "war", "attack" are too common -- require 2+ of them.
            geo_overlap     = overlap & _GEO_NAMED_ENTITIES
            generic_overlap = overlap - _GEO_NAMED_ENTITIES
            if not geo_overlap and len(generic_overlap) < 2:
                continue
            score = _similarity(news_tokens, market_tokens)

            days = _days_to_close(market.close_time)
            if days is not None:
                if days <= 1:
                    score *= 1.5
                elif days <= 7:
                    score *= 1.2
                elif days <= 14:
                    score *= 1.1

            structure_flags = _structure_quality_flags(
                overlap=overlap,
                geo_overlap=geo_overlap,
                generic_overlap=generic_overlap,
                headline_meaningful=meaningful_hl,
                market_title_meaningful=meaningful_mt,
            )
            score *= _weak_match_penalty_multiplier(set(structure_flags))
            if score < min_score:
                continue

            score = round(score, 4)
            heuristic_flags = _match_quality_flags(
                overlap=overlap,
                geo_overlap=geo_overlap,
                generic_overlap=generic_overlap,
                headline_meaningful=meaningful_hl,
                market_title_meaningful=meaningful_mt,
                score=score,
                min_score=min_score,
            )
            overlap_ratio = len(overlap) / max(1, min(len(meaningful_hl), len(meaningful_mt)))
            trade_log.log_match_diagnostic(
                source=news.source,
                headline=news.headline,
                ticker=market.ticker,
                market_title=market.title,
                match_score=score,
                matched_tokens=sorted(overlap),
                token_overlap_count=len(overlap),
                geo_overlap_count=len(geo_overlap),
                generic_overlap_count=len(generic_overlap),
                headline_token_count=len(meaningful_hl),
                market_title_token_count=len(meaningful_mt),
                overlap_ratio=overlap_ratio,
                low_match_quality=bool(heuristic_flags),
                heuristic_flags=heuristic_flags,
            )

            flag_set = set(heuristic_flags)
            ticker_lower = market.ticker.lower()
            _token_not_in_ticker = not any(token in ticker_lower for token in overlap)
            # Path A (original): near-threshold score + structural weakness.
            # Catches fragile matches that barely passed the score floor.
            _near_threshold_weak = (
                "near_threshold_score" in flag_set
                and ("minimal_overlap" in flag_set or "single_named_entity_only" in flag_set)
            )
            # Path B: pure single-entity match with no additional semantic support.
            # A single named-entity token with no other overlap can inflate scores via
            # the geopolitical boost, bypassing near_threshold detection. Suppressing
            # score-independently is safe: any legitimately topic-aligned market will
            # embed the entity in its ticker (e.g. KXTRUMP-*), which the ticker guard
            # below preserves.
            _pure_single_entity = (
                "single_named_entity_only" in flag_set
                and "minimal_overlap" in flag_set
            )
            _meets_suppression_criteria = (
                bool(heuristic_flags)
                and _token_not_in_ticker
                and (_near_threshold_weak or _pure_single_entity)
            )

            if ENABLE_MATCH_SUPPRESSION_DEBUG and _meets_suppression_criteria:
                trade_log.log_match_suppression_candidate(
                    source=news.source,
                    headline=news.headline,
                    ticker=market.ticker,
                    match_score=score,
                    overlap_count=len(overlap),
                    overlap_ratio=overlap_ratio,
                    heuristic_flags=heuristic_flags,
                    matched_tokens=sorted(overlap),
                )

            if ENABLE_LOW_QUALITY_MATCH_SUPPRESSION and _meets_suppression_criteria:
                reason = "+".join(sorted(flag_set))
                trade_log.log_match_suppressed(
                    source=news.source,
                    headline=news.headline,
                    ticker=market.ticker,
                    market_title=market.title,
                    match_score=score,
                    matched_tokens=sorted(overlap),
                    heuristic_flags=heuristic_flags,
                    reason=reason,
                )
                log.debug(
                    "[SUPPRESSED] %s -> %s (score=%.3f flags=%s)",
                    news.headline[:60], market.ticker, score, reason,
                )
                continue

            scored.append((market, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = scored[:max_results]

        if results:
            log.debug(
                "[%s] Matched %d markets for '%s...' -- top: %s (%.3f)",
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
                "[FADE] Matched %d market(s) for '%s...' -- top: %s (%.3f)",
                len(results), news.headline[:50],
                results[0][0].ticker, results[0][1],
            )
        return results

    async def refresh_cache(self) -> None:
        await self._cache._refresh()
