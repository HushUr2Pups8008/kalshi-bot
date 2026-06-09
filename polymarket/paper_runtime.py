from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from analysis import SignalAnalysis
from analysis.kelly import kelly_bet
from analysis.market_matcher import _compute_pre_llm_match_meta
from analysis.signal_analyzer import estimate_probability
from analysis.side_selection import compute_edge, select_side
from config import PAPER_FLAT_CONTRACTS, PAPER_MAX_CANDIDATES, cfg
from feeds import NewsItem
from polymarket.candidate_adapter import adapt_polymarket_analysis
from polymarket.models import PolymarketMarket
from polymarket.public_client import PolymarketPublicClient
from trading.venue import Venue
from utils.logger import get_logger

log = get_logger("polymarket.paper_runtime")

_DEFAULT_MARKET_LIMIT = 500
_DEFAULT_MARKET_CACHE_TTL_SECONDS = 300.0
_DEFAULT_MAX_CANDIDATES = PAPER_MAX_CANDIDATES
_DEFAULT_MIN_MATCH_SCORE = 0.08
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "be",
        "by",
        "for",
        "from",
        "get",
        "gets",
        "in",
        "is",
        "it",
        "more",
        "of",
        "on",
        "or",
        "the",
        "to",
        "will",
        "with",
    }
)
_ALLOWED_CATEGORIES = frozenset({"politics"})


class _PublicMarketClient(Protocol):
    def get_markets(self, *, limit: int) -> tuple[list[PolymarketMarket], str | None]:
        ...


_RouteAnalysis = Callable[..., Awaitable[Any]]
_EstimateProbability = Callable[
    ...,
    Awaitable[
        tuple[float, float, list[str], str, str | None, str | None, float | None]
    ],
]


@dataclass(frozen=True)
class PolymarketMatchMeta:
    venue: str
    match_score: float
    matched_tokens: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "polymarket_match_score": self.match_score,
            "polymarket_matched_tokens": self.matched_tokens,
        }


@dataclass(frozen=True)
class PolymarketPaperRuntimeStats:
    market_count: int
    last_refresh_age_seconds: float | None
    news_processed: int
    routed_count: int
    no_match_count: int
    last_match_count: int
    last_error: str | None


class PolymarketPaperRuntime:
    """Paper-only Polymarket analysis router backed by public market data."""

    def __init__(
        self,
        *,
        route_analysis: _RouteAnalysis,
        keyword_stats: Any,
        source_stats: Any | None = None,
        client: _PublicMarketClient | None = None,
        estimate_probability_fn: _EstimateProbability = estimate_probability,
        market_limit: int = _DEFAULT_MARKET_LIMIT,
        market_cache_ttl_seconds: float = _DEFAULT_MARKET_CACHE_TTL_SECONDS,
        max_candidates: int = _DEFAULT_MAX_CANDIDATES,
        min_match_score: float = _DEFAULT_MIN_MATCH_SCORE,
    ) -> None:
        self._client = client or PolymarketPublicClient()
        self._route_analysis = route_analysis
        self._keyword_stats = keyword_stats
        self._source_stats = source_stats
        self._estimate_probability = estimate_probability_fn
        self._market_limit = market_limit
        self._market_cache_ttl_seconds = market_cache_ttl_seconds
        self._max_candidates = max_candidates
        self._min_match_score = min_match_score
        self._markets: list[PolymarketMarket] = []
        self._last_fetch = 0.0
        self._fetch_lock = asyncio.Lock()
        self._news_processed = 0
        self._routed_count = 0
        self._no_match_count = 0
        self._last_match_count = 0
        self._last_error: str | None = None

    async def process_news(self, news: NewsItem) -> int:
        self._news_processed += 1
        markets = await self._get_markets()
        if not markets:
            self._last_match_count = 0
            self._log_heartbeat()
            return 0

        matches = match_polymarket_markets(
            news,
            markets,
            max_results=self._max_candidates,
            min_score=self._min_match_score,
        )
        self._last_match_count = len(matches)
        if not matches:
            self._no_match_count += 1
            log.info(
                "[POLYMARKET_MATCH] no_match markets=%d headline=%s",
                len(markets),
                news.headline[:80],
            )
            self._log_heartbeat()
            return 0

        routed = 0
        for market, match_score, match_meta in matches:
            log.info(
                "[POLYMARKET_MATCH] candidate ticker=%s score=%.3f matched_tokens=%s",
                market.market_id,
                match_score,
                ",".join(match_meta.get("polymarket_matched_tokens", [])[:8]),
            )
            analysis = await self._build_analysis(news, market, match_score, match_meta)
            if analysis is None:
                continue
            if self._source_stats is not None:
                self._source_stats.increment_signals(news.source)
            result = await self._route_analysis(analysis, accumulate=True, watch=False)
            routed += 1
            self._routed_count += 1
            log.info(
                "[POLYMARKET_PAPER] routed ticker=%s enqueued=%s match_score=%.3f "
                "paper_execution=blend",
                market.market_id,
                getattr(result, "enqueued", None),
                match_score,
            )
        self._log_heartbeat()
        return routed

    def stats(self) -> PolymarketPaperRuntimeStats:
        age = None
        if self._last_fetch:
            age = max(0.0, time.monotonic() - self._last_fetch)
        return PolymarketPaperRuntimeStats(
            market_count=len(self._markets),
            last_refresh_age_seconds=age,
            news_processed=self._news_processed,
            routed_count=self._routed_count,
            no_match_count=self._no_match_count,
            last_match_count=self._last_match_count,
            last_error=self._last_error,
        )

    def cached_markets(self) -> list[PolymarketMarket]:
        return list(self._markets)

    def cached_candidate_markets(self) -> list[PolymarketMarket]:
        return [
            market
            for market in self._markets
            if (
                market.venue == Venue.POLYMARKET_US
                and market.is_tradeable()
                and not _is_suppressed_market(market)
            )
        ]

    async def warm_cache(self) -> int:
        markets = await self._get_markets()
        return len(markets)

    def _log_heartbeat(self) -> None:
        stats = self.stats()
        age = (
            "unknown"
            if stats.last_refresh_age_seconds is None
            else f"{stats.last_refresh_age_seconds:.1f}s"
        )
        log.info(
            "[POLYMARKET_PAPER] heartbeat markets=%d cache_age=%s "
            "news_processed=%d routed=%d no_match=%d last_match=%d last_error=%s",
            stats.market_count,
            age,
            stats.news_processed,
            stats.routed_count,
            stats.no_match_count,
            stats.last_match_count,
            stats.last_error or "none",
        )

    async def _get_markets(self) -> list[PolymarketMarket]:
        async with self._fetch_lock:
            age = time.monotonic() - self._last_fetch
            if self._markets and age < self._market_cache_ttl_seconds:
                return list(self._markets)
            try:
                markets, _cursor = await asyncio.to_thread(
                    self._client.get_markets,
                    limit=self._market_limit,
                )
            except Exception as exc:
                self._last_error = "public_market_fetch_failed"
                log.warning(
                    "[POLYMARKET_PAPER] public_market_fetch_failed error=%s",
                    exc,
                )
                return []
            self._markets = list(markets)
            self._last_fetch = time.monotonic()
            self._last_error = None
            log.info(
                "[POLYMARKET_PAPER] market_cache_refreshed markets=%d limit=%d",
                len(self._markets),
                self._market_limit,
            )
            return list(self._markets)

    async def _build_analysis(
        self,
        news: NewsItem,
        market: PolymarketMarket,
        match_score: float,
        match_meta: dict[str, Any],
    ) -> SignalAnalysis | None:
        try:
            (
                estimated_probability,
                confidence,
                keywords,
                reasoning,
                llm_direction,
                llm_magnitude,
                llm_confidence,
            ) = await self._estimate_probability(
                news,
                market,
                keyword_stats=self._keyword_stats,
                match_meta=match_meta,
            )
            side, executed_price_cents = select_side(market, estimated_probability)
            edges = compute_edge(market, estimated_probability)
        except ValueError as exc:
            log.debug(
                "[POLYMARKET_PAPER] analysis_skip ticker=%s reason=%s",
                market.market_id,
                exc,
            )
            return None
        except Exception as exc:
            log.warning(
                "[POLYMARKET_PAPER] analysis_failed ticker=%s error=%s",
                market.market_id,
                exc,
            )
            return None

        source_mult = 1.0
        if self._keyword_stats is not None:
            source_mult = 1.0
        notional = cfg.bankroll
        max_bet = cfg.dynamic_max_bet(notional)
        kelly_fraction, kelly_dollars, capped_dollars = kelly_bet(
            estimated_probability=(
                estimated_probability
                if side == "yes"
                else 1.0 - estimated_probability
            ),
            market_price_cents=executed_price_cents,
            bankroll=notional,
            kelly_fraction=cfg.kelly_fraction,
            max_bet_dollars=max_bet,
            min_bet_dollars=cfg.min_bet_dollars,
            min_edge=cfg.min_edge,
            source_multiplier=source_mult,
            confidence=confidence,
        )
        if capped_dollars <= 0:
            capped_dollars = (
                PAPER_FLAT_CONTRACTS
                * max(1, min(99, int(executed_price_cents)))
                / 100.0
            )

        side_edge = edges.yes_edge if side == "yes" else edges.no_edge
        base_analysis = SignalAnalysis(
            news_item=news,
            market=market,
            estimated_probability=estimated_probability,
            executed_price_cents=executed_price_cents,
            edge=side_edge,
            side=side,
            kelly_fraction=kelly_fraction,
            kelly_dollars=kelly_dollars,
            capped_dollars=capped_dollars,
            keywords_matched=keywords,
            reasoning=reasoning,
            confidence=confidence,
            match_score=match_score,
            signal_type="polymarket_news",
            llm_direction=llm_direction,
            llm_magnitude=llm_magnitude,
            llm_confidence=llm_confidence,
            signal_meta=match_meta,
        )
        adapted = adapt_polymarket_analysis(base_analysis, market)
        adapted.signal_meta = {
            **(adapted.signal_meta or {}),
            **match_meta,
        }
        log.info(
            "[POLYMARKET_ANALYSIS] candidate ticker=%s side=%s price_cents=%d "
            "edge=%.4f est_prob=%.4f confidence=%.3f",
            market.market_id,
            adapted.side,
            adapted.executed_price_cents,
            adapted.edge,
            adapted.estimated_probability,
            adapted.confidence,
        )
        return adapted


def match_polymarket_markets(
    news: NewsItem,
    markets: Sequence[PolymarketMarket],
    *,
    max_results: int = _DEFAULT_MAX_CANDIDATES,
    min_score: float = _DEFAULT_MIN_MATCH_SCORE,
) -> list[tuple[PolymarketMarket, float, dict[str, Any]]]:
    news_tokens = _meaningful_tokens(f"{news.headline} {news.body}")
    if not news_tokens:
        return []

    scored: list[tuple[PolymarketMarket, float, dict[str, Any]]] = []
    for market in markets:
        if (
            market.venue != Venue.POLYMARKET_US
            or not market.is_tradeable()
            or _is_suppressed_market(market)
        ):
            continue
        market_tokens = _meaningful_tokens(_market_match_text(market))
        if not market_tokens:
            continue
        overlap = news_tokens & market_tokens
        if not overlap:
            continue
        score = len(overlap) / max(1, len(news_tokens | market_tokens))
        if score < min_score:
            continue
        rounded_score = round(score, 4)
        matched_tokens = sorted(overlap)
        pre_llm_meta = _compute_pre_llm_match_meta(
            news.headline,
            _market_match_text(market),
            matched_tokens,
        )
        meta = PolymarketMatchMeta(
            venue=Venue.POLYMARKET_US.value,
            match_score=rounded_score,
            matched_tokens=matched_tokens,
        ).as_dict()
        meta.update(pre_llm_meta)
        meta["matched_tokens"] = matched_tokens
        scored.append((market, rounded_score, meta))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:max_results]


def _is_suppressed_market(market: PolymarketMarket) -> bool:
    return market.category.strip().lower() not in _ALLOWED_CATEGORIES


def _market_match_text(market: PolymarketMarket) -> str:
    return " ".join(
        part
        for part in (market.title, market.question, market.subtitle)
        if part
    )


def polymarket_paper_runtime_disabled_reason(config: Any = cfg) -> str | None:
    if not bool(getattr(config, "polymarket_us_enabled", False)):
        return "polymarket_us_enabled=false"
    if not bool(getattr(config, "is_paper_trading", False)):
        return "bot_not_in_paper_mode"
    if bool(getattr(config, "polymarket_us_live_trading_enabled", False)):
        return "polymarket_live_trading_enabled"
    return None


def _meaningful_tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    }
