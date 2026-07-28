from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from analysis import DecisionFinancialProvenance, SignalAnalysis
from analysis.feedback_counterfactual import (
    FEEDBACK_ALGORITHM_VERSION,
    FeedbackDecisionRecord,
    FeedbackMultiplierReceipt,
    KeywordFeedbackCollector,
)
from analysis.kelly import kelly_bet
from analysis.market_matcher import _compute_pre_llm_match_meta, _token_downweight_details
from analysis.match_feedback import load_weights as _load_match_weights
from analysis.match_feedback import market_prefix_for
from analysis.signal_analyzer import estimate_probability
from analysis.side_selection import compute_edge, select_side
from config import PAPER_FLAT_CONTRACTS, PAPER_MAX_CANDIDATES, cfg
from feeds import NewsItem
from polymarket.candidate_adapter import adapt_polymarket_analysis
from polymarket.models import PolymarketMarket
from polymarket.public_client import PolymarketPublicClient
from trading.fees import INITIAL_ORDER_FEE_ACCUMULATOR
from trading.venue import Venue
from utils.logger import get_logger, trade_log, write_trade_log_async
from utils.lifecycle import build_lifecycle_id, settlement_source_match

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
_DISCOVERY_TOPIC_TOKENS = frozenset({
    "diplomacy",
    "election",
    "elections",
    "geopolitics",
    "government",
    "international",
    "iran",
    "israel",
    "military",
    "politics",
    "president",
    "trump",
    "war",
    "world",
})


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


def _supports_feedback_collector(estimator: _EstimateProbability) -> bool:
    """Keep third-party estimator injection compatible with the receipt trace."""

    try:
        parameters = inspect.signature(estimator).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "feedback_collector"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


@dataclass(frozen=True)
class PolymarketMatchMeta:
    venue: str
    match_score: float
    matched_tokens: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            # PROFIT-VENUE-PARITY V07: emit the GENERIC match_score / matched_tokens
            # keys (not only the polymarket_* aliases) so the L2-a
            # false_positive_neutral score-gate fires for PM exactly as for Kalshi
            # -- signal_analyzer reads match_meta['match_score'] (Kalshi sets it via
            # _process_candidate's setdefault). Without this, every PM neutral
            # verdict mapped to a missing score == 0.0 marginal (<0.12) and counted
            # toward downweighting regardless of match quality, poisoning the
            # feedback bucket. Aliases retained for the heartbeat log / back-compat.
            "match_score": self.match_score,
            "matched_tokens": self.matched_tokens,
            "polymarket_match_score": self.match_score,
            "polymarket_matched_tokens": self.matched_tokens,
        }


def _analysis_keywords(
    keywords: Sequence[str] | None,
    match_meta: dict[str, Any],
) -> list[str]:
    for raw_keywords in (
        keywords,
        match_meta.get("matched_tokens"),
        match_meta.get("polymarket_matched_tokens"),
        match_meta.get("pre_llm_semantic_overlap_tokens"),
    ):
        normalized = _dedupe_keyword_values(raw_keywords)
        if normalized:
            return normalized
    return []


def _dedupe_keyword_values(raw_keywords: Any) -> list[str]:
    if raw_keywords is None:
        return []
    if isinstance(raw_keywords, str):
        values: Sequence[Any] = [raw_keywords]
    elif isinstance(raw_keywords, Sequence):
        values = raw_keywords
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_keyword in values:
        if not isinstance(raw_keyword, str):
            continue
        keyword = raw_keyword.strip()
        if not keyword:
            continue
        dedupe_key = keyword.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(keyword)
    return normalized


@dataclass(frozen=True)
class PolymarketPaperRuntimeStats:
    market_count: int
    last_refresh_age_seconds: float | None
    news_processed: int
    routed_count: int
    no_match_count: int
    last_match_count: int
    last_error: str | None


@dataclass(frozen=True)
class _FeedbackDecisionDraft:
    lifecycle_id: str
    ticker: str
    source: str
    series_ticker: str | None
    decision_at: str
    source_receipt: FeedbackMultiplierReceipt
    keyword_receipts: tuple[FeedbackMultiplierReceipt, ...]
    probability_actual: float
    probability_keyword_neutral: float | None
    keyword_counterfactual_status: str
    sizing_inputs: dict[str, Any]
    actual: dict[str, Any]
    source_neutral: dict[str, Any]

    def to_record(self, route_result: Any) -> FeedbackDecisionRecord:
        return FeedbackDecisionRecord(
            lifecycle_id=self.lifecycle_id,
            venue=Venue.POLYMARKET_US.value,
            ticker=self.ticker,
            source=self.source,
            series_ticker=self.series_ticker,
            decision_at=self.decision_at,
            source_receipt=self.source_receipt,
            keyword_receipts=self.keyword_receipts,
            probability_actual=self.probability_actual,
            probability_keyword_neutral=self.probability_keyword_neutral,
            keyword_counterfactual_status=self.keyword_counterfactual_status,
            sizing_inputs=self.sizing_inputs,
            actual=self.actual,
            source_neutral=self.source_neutral,
            keyword_neutral=None,
            all_neutral=None,
            gate={
                "enqueued": bool(getattr(route_result, "enqueued", False)),
                "trade_blocked_reason": getattr(route_result, "trade_blocked_reason", None),
            },
        )


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
            matched_tokens = list(match_meta.get("matched_tokens", []))
            headline_tokens = _meaningful_tokens(news.headline)
            market_tokens = _meaningful_tokens(_market_match_text(market))
            publish_ts = (
                news.published.isoformat()
                if getattr(news, "published", None) is not None
                else None
            )
            trade_log.log_match_diagnostic(
                source=news.source,
                headline=news.headline,
                ticker=market.ticker,
                market_title=_market_match_text(market),
                match_score=match_score,
                matched_tokens=matched_tokens,
                token_overlap_count=len(matched_tokens),
                geo_overlap_count=0,
                generic_overlap_count=0,
                headline_token_count=len(headline_tokens),
                market_title_token_count=len(market_tokens),
                overlap_ratio=(
                    len(matched_tokens)
                    / max(1, min(len(headline_tokens), len(market_tokens)))
                ),
                low_match_quality=not bool(match_meta.get("pre_llm_quality_pass", True)),
                pre_llm_semantic_overlap_count=match_meta.get(
                    "pre_llm_semantic_overlap_count"
                ),
                pre_llm_semantic_overlap_ratio=match_meta.get(
                    "pre_llm_semantic_overlap_ratio"
                ),
                would_fail_pre_llm_gate=not bool(
                    match_meta.get("pre_llm_quality_pass", True)
                ),
                pre_llm_headline_token_count=match_meta.get(
                    "pre_llm_headline_token_count"
                ),
                pre_llm_market_token_count=match_meta.get("pre_llm_market_token_count"),
                pre_llm_filtered_stopword_count=match_meta.get(
                    "pre_llm_filtered_stopword_count"
                ),
                pre_llm_filtered_generic_count=match_meta.get(
                    "pre_llm_filtered_generic_count"
                ),
                pre_llm_semantic_token_types=match_meta.get(
                    "pre_llm_semantic_token_types"
                ),
                publish_ts=publish_ts,
                venue=Venue.POLYMARKET_US.value,
                lifecycle_id=match_meta["lifecycle_id"],
                settlement_source_match=match_meta["settlement_source_match"],
            )
            analysis = await self._build_analysis(news, market, match_score, match_meta)
            if analysis is None:
                continue
            method = (
                "llm"
                if any(
                    value is not None
                    for value in (
                        analysis.llm_direction,
                        analysis.llm_magnitude,
                        analysis.llm_confidence,
                    )
                )
                else "keyword"
            )
            trade_log.log_opportunity(
                ticker=market.ticker,
                market_title=market.title,
                entry_price_cents=float(analysis.executed_price_cents or 0),
                estimated_probability=analysis.estimated_probability,
                edge=analysis.edge,
                kelly_fraction=analysis.kelly_fraction,
                kelly_dollars=analysis.kelly_dollars,
                capped_dollars=analysis.capped_dollars,
                side=analysis.side,
                reasoning=analysis.reasoning,
                source=news.source,
                headline=news.headline,
                method=method,
                llm_direction=analysis.llm_direction,
                llm_magnitude=analysis.llm_magnitude,
                venue=Venue.POLYMARKET_US.value,
                keywords=analysis.keywords_matched,
                source_class="news",
                settlement_source_match=match_meta["settlement_source_match"],
                lifecycle_id=match_meta["lifecycle_id"],
                signal_type=analysis.signal_type,
            )
            if self._source_stats is not None:
                self._source_stats.increment_signals(news.source)
                increment_opportunities = getattr(
                    self._source_stats, "increment_opportunities", None
                )
                if callable(increment_opportunities):
                    increment_opportunities(news.source)
            feedback_draft = None
            if isinstance(analysis.signal_meta, dict):
                feedback_draft = analysis.signal_meta.pop("_feedback_decision_draft", None)
            result = await self._route_analysis(analysis, accumulate=True, watch=False)
            if isinstance(feedback_draft, _FeedbackDecisionDraft):
                try:
                    await write_trade_log_async(
                        trade_log.log_feedback_decision,
                        feedback_draft.to_record(result),
                    )
                except Exception:
                    log.warning(
                        "[FEEDBACK_DECISION] receipt write failed ticker=%s source=%s",
                        market.market_id,
                        news.source,
                        exc_info=True,
                    )
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
        feedback_collector = KeywordFeedbackCollector()
        estimate_kwargs: dict[str, Any] = {
            "keyword_stats": self._keyword_stats,
            "match_meta": match_meta,
        }
        estimator_supports_feedback = _supports_feedback_collector(
            self._estimate_probability
        )
        if estimator_supports_feedback:
            estimate_kwargs["feedback_collector"] = feedback_collector
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
                **estimate_kwargs,
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
        paper_placeholder_applied = capped_dollars <= 0
        if paper_placeholder_applied:
            capped_dollars = (
                PAPER_FLAT_CONTRACTS
                * max(1, min(99, int(executed_price_cents)))
                / 100.0
            )

        sizing_bankroll = Decimal(str(notional))
        side_edge = edges.yes_edge if side == "yes" else edges.no_edge
        analysis_keywords = _analysis_keywords(keywords, match_meta)
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
            keywords_matched=analysis_keywords,
            reasoning=reasoning,
            confidence=confidence,
            match_score=match_score,
            signal_type="polymarket_news",
            llm_direction=llm_direction,
            llm_magnitude=llm_magnitude,
            llm_confidence=llm_confidence,
            signal_meta=match_meta,
            decision_financial_provenance=DecisionFinancialProvenance(
                sizing_bankroll_dollars=sizing_bankroll,
                max_position_dollars=Decimal(str(max_bet)),
                max_ticker_exposure_dollars=(
                    Decimal(str(cfg.max_ticker_exposure_pct)) * sizing_bankroll
                ),
                fee_account_precision_dollars=None,
                fee_accumulator_dollars=INITIAL_ORDER_FEE_ACCUMULATOR,
            ),
        )
        adapted = adapt_polymarket_analysis(base_analysis, market)
        adapted.signal_meta = {
            **(adapted.signal_meta or {}),
            **match_meta,
        }
        keyword_counterfactual_status = feedback_collector.keyword_counterfactual_status
        if not estimator_supports_feedback:
            keyword_counterfactual_status = "unavailable_estimator_feedback_collector"
        elif any(
            value is not None
            for value in (llm_direction, llm_magnitude, llm_confidence)
        ):
            keyword_counterfactual_status = (
                "unscorable_llm_final_probability:"
                f"{keyword_counterfactual_status}"
            )
        elif feedback_collector.keyword_probability_neutral is not None:
            keyword_counterfactual_status = (
                "captured_keyword_probability_not_sizing_replayed:"
                f"{keyword_counterfactual_status}"
            )
        source_receipt = FeedbackMultiplierReceipt(
            channel="source",
            key=news.source,
            series_ticker=None,
            applied_multiplier=1.0,
            status="not_applicable_venue",
            canonical_basis_sha256=None,
            delivered_event_count=0,
            effective_sample_count=0,
            algorithm_version=FEEDBACK_ALGORITHM_VERSION,
            as_of=datetime.now(timezone.utc).isoformat(),
        )
        adapted.signal_meta["_feedback_decision_draft"] = _FeedbackDecisionDraft(
            lifecycle_id=str(match_meta["lifecycle_id"]),
            ticker=adapted.market.ticker,
            source=news.source,
            series_ticker=str(getattr(market, "series_ticker", "") or "") or None,
            decision_at=datetime.now(timezone.utc).isoformat(),
            source_receipt=source_receipt,
            keyword_receipts=feedback_collector.keyword_receipts,
            probability_actual=float(adapted.estimated_probability),
            probability_keyword_neutral=feedback_collector.keyword_probability_neutral,
            keyword_counterfactual_status=keyword_counterfactual_status,
            sizing_inputs={
                "executed_price_cents": float(executed_price_cents),
                "side": side,
                "bankroll": float(notional),
                "kelly_fraction": float(cfg.kelly_fraction),
                "max_bet_dollars": float(max_bet),
                "min_bet_dollars": float(cfg.min_bet_dollars),
                "min_edge": float(cfg.min_edge),
                "confidence": float(confidence),
                "days_to_close": None,
                "time_discount_half_life": None,
                "time_discount_floor": None,
                "source_multiplier": 1.0,
                "is_paper_trading": bool(cfg.is_paper_trading),
                "paper_flat_contracts": int(PAPER_FLAT_CONTRACTS),
                "floor_clamp_applied": False,
                "floor_clamp_kelly_multiplier": 1.0,
            },
            actual={
                "source_multiplier": 1.0,
                "kelly_fraction": float(adapted.kelly_fraction),
                "kelly_dollars": float(adapted.kelly_dollars),
                "capped_dollars": float(adapted.capped_dollars),
                "paper_placeholder_applied": paper_placeholder_applied,
            },
            source_neutral={
                "status": "not_applicable_venue",
                "sizing_error": None,
                "source_multiplier": 1.0,
                "kelly_fraction": float(adapted.kelly_fraction),
                "kelly_dollars": float(adapted.kelly_dollars),
                "capped_dollars": float(adapted.capped_dollars),
                "paper_placeholder_applied": paper_placeholder_applied,
            },
        )
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
    token_weights: dict[str, dict[str, Any]] | None = None,
) -> list[tuple[PolymarketMarket, float, dict[str, Any]]]:
    news_tokens = _meaningful_tokens(f"{news.headline} {news.body}")
    headline_tokens = _meaningful_tokens(news.headline)
    if not news_tokens and not headline_tokens:
        return []
    if token_weights is None:
        token_weights = _load_match_weights()

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
        headline_overlap = headline_tokens & market_tokens
        if not overlap:
            continue
        full_score = len(overlap) / max(1, len(news_tokens | market_tokens))
        headline_score = len(headline_overlap) / max(1, len(headline_tokens | market_tokens))
        score = max(full_score, headline_score)
        market_prefix = market_prefix_for(market)
        if overlap and token_weights:
            pre_weight_score = score
            weight_details = _token_downweight_details(
                overlap,
                market_prefix,
                token_weights,
            )
            score *= weight_details["final_multiplier"]
            trade_log.log_match_weight_applied(
                source=news.source,
                headline=news.headline,
                ticker=market.ticker,
                market_title=_market_match_text(market),
                market_prefix=market_prefix,
                tokens=weight_details["tokens"],
                token_weights=weight_details["token_weights"],
                composition_rule=weight_details["composition_rule"],
                final_multiplier=weight_details["final_multiplier"],
                pre_weight_score=pre_weight_score,
                post_weight_score=score,
            )
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
        meta["market_prefix"] = market_prefix
        meta["matched_tokens"] = matched_tokens
        lifecycle_id = build_lifecycle_id(
            venue=Venue.POLYMARKET_US.value,
            ticker=market.ticker,
            source=news.source,
            url=getattr(news, "url", None),
            headline=news.headline,
            published=getattr(news, "published", None),
        )
        settlement_match = settlement_source_match(
            source=news.source,
            url=getattr(news, "url", None),
            source_hint_domain=getattr(news, "source_hint_domain", None),
            settlement_sources=(market.resolution_source,) if market.resolution_source else (),
        )
        meta["lifecycle_id"] = lifecycle_id
        meta["settlement_source_match"] = settlement_match
        scored.append((market, rounded_score, meta))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:max_results]


def _is_suppressed_market(market: PolymarketMarket) -> bool:
    category = market.category.strip().lower()
    if category in _ALLOWED_CATEGORIES:
        return False
    has_resolution_source = bool(market.resolution_source.strip())
    has_liquidity = market.volume_dollars > 0.0 or market.open_interest_dollars > 0.0
    context_text = " ".join(
        part
        for part in (
            category,
            market.event_title,
            market.series_title,
            *market.tags,
        )
        if part
    ).lower()
    topic_relevant = bool(_meaningful_tokens(context_text) & _DISCOVERY_TOPIC_TOKENS)
    return not (has_resolution_source and has_liquidity and topic_relevant)


def _market_match_text(market: PolymarketMarket) -> str:
    return " ".join(
        part
        for part in (
            market.title,
            market.question,
            market.subtitle,
            market.description,
            market.category,
            market.resolution_source,
            market.event_title,
            market.event_slug,
            market.series_title,
            market.series_slug,
            *market.tags,
            *market.public_comments,
        )
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
