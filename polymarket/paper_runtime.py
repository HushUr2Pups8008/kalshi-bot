from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import math
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
from config import (
    MAX_MARKET_DAYS_TO_EXPIRY,
    PAPER_FLAT_CONTRACTS,
    PAPER_MAX_CANDIDATES,
    cfg,
)
from feeds import NewsItem
from polymarket.candidate_adapter import adapt_polymarket_analysis
from polymarket.horizon_selection import select_polymarket_horizon_band
from polymarket.models import PolymarketMarket
from polymarket.public_client import PolymarketPublicClient
from trading.fees import INITIAL_ORDER_FEE_ACCUMULATOR
from trading.venue import Venue
from utils.logger import get_logger, trade_log, write_trade_log_async
from utils.lifecycle import build_lifecycle_id, settlement_source_match
from utils.market_horizon import evaluate_market_horizon

log = get_logger("polymarket.paper_runtime")

_DEFAULT_MARKET_LIMIT = 500
_DEFAULT_MARKET_CACHE_TTL_SECONDS = 300.0
_DEFAULT_MAX_CANDIDATES = PAPER_MAX_CANDIDATES
_DEFAULT_MIN_MATCH_SCORE = 0.08
# The public gateway caps a page at 500. Thirty pages cover the current open
# universe while retaining an explicit bound when the universe grows.
_PUBLIC_MARKET_PAGE_SIZE = 500
_MAX_MARKET_FETCH_PAGES = 30
_HORIZON_SHADOW_END_DAYS = float(MAX_MARKET_DAYS_TO_EXPIRY)
_COUNTERFACTUAL_SNAPSHOT_SCHEMA_VERSION = 1
_COUNTERFACTUAL_SNAPSHOT_MAX_CANDIDATES = 4
_COUNTERFACTUAL_SNAPSHOT_MAX_TITLE_CHARS = 160
_COUNTERFACTUAL_SNAPSHOT_MAX_RAW_TITLE_CHARS = 512
_COUNTERFACTUAL_SNAPSHOT_MAX_TICKER_CHARS = 128
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


def _counterfactual_title(value: Any) -> str | None:
    """Return a bounded ASCII-safe public market title for shadow telemetry."""
    if type(value) is not str or len(value) > _COUNTERFACTUAL_SNAPSHOT_MAX_RAW_TITLE_CHARS:
        return None
    sanitized = "".join(char for char in value if " " <= char <= "~")
    return sanitized[:_COUNTERFACTUAL_SNAPSHOT_MAX_TITLE_CHARS] or None


def _counterfactual_ticker(value: Any) -> str | None:
    if (
        type(value) is not str
        or not value
        or len(value) > _COUNTERFACTUAL_SNAPSHOT_MAX_TICKER_CHARS
        or any(not ("!" <= char <= "~") for char in value)
    ):
        return None
    return value


def _counterfactual_finite_score(value: float | None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def _counterfactual_candidate(
    market: PolymarketMarket,
    *,
    rejection_reason: str,
    market_token_count: int,
    matched_token_count: int,
    pre_weight_score: float | None = None,
    post_weight_score: float | None = None,
) -> dict[str, Any] | None:
    ticker = _counterfactual_ticker(market.ticker)
    if ticker is None:
        return None
    candidate: dict[str, Any] = {
        "ticker": ticker,
        "rejection_reason": rejection_reason,
        "market_token_count": market_token_count,
        "matched_token_count": matched_token_count,
    }
    market_title = _counterfactual_title(market.title)
    if market_title is not None:
        candidate["market_title"] = market_title
    finite_pre_weight_score = _counterfactual_finite_score(pre_weight_score)
    finite_post_weight_score = _counterfactual_finite_score(post_weight_score)
    if finite_pre_weight_score is not None:
        candidate["pre_weight_score"] = finite_pre_weight_score
    if finite_post_weight_score is not None:
        candidate["post_weight_score"] = finite_post_weight_score
    return candidate


def _counterfactual_candidate_sort_key(candidate: dict[str, Any]) -> tuple[str, float, str]:
    post_weight_score = candidate.get("post_weight_score")
    descending_score = (
        -post_weight_score if isinstance(post_weight_score, float) else 0.0
    )
    return (
        candidate["rejection_reason"],
        descending_score,
        candidate["ticker"],
    )


def _append_counterfactual_candidate(
    candidates: list[dict[str, Any]], candidate: dict[str, Any]
) -> None:
    candidates.append(candidate)
    candidates.sort(key=_counterfactual_candidate_sort_key)
    del candidates[_COUNTERFACTUAL_SNAPSHOT_MAX_CANDIDATES:]


async def _write_funnel_telemetry(
    event_type: str,
    writer: Callable[..., Any],
    /,
    **fields: Any,
) -> None:
    """Persist optional funnel telemetry without changing routing outcomes."""
    try:
        await write_trade_log_async(writer, **fields)
    except Exception:
        log.warning(
            "[POLYMARKET_PAPER] funnel_telemetry_write_failed event=%s",
            event_type,
            exc_info=True,
        )


@dataclass(frozen=True)
class _MarketCacheTelemetry:
    raw_fetched: int
    raw_unique: int
    pages_fetched: int
    cursor_present: bool
    pagination_exhausted: bool
    pagination_stop_reason: str
    eligible_30d: int
    candidate_within_admission_horizon: int
    admission_horizon_days: float
    market_limit: int


@dataclass(frozen=True)
class _NoCandidatePoolTelemetry:
    candidate_pool_stage: str
    pre_admission_matchable_market_count: int
    within_admission_horizon_market_count: int
    admission_horizon_days: float


@dataclass(frozen=True)
class _PostAdmissionRejectionTelemetry:
    no_token_overlap_count: int
    below_min_post_weight_score_count: int
    weight_demoted_below_min_score_count: int
    min_match_score: float
    best_rejected_pre_weight_score: float | None
    best_rejected_post_weight_score: float | None
    qualifying_match_count: int
    counterfactual_shadow: dict[str, Any] | None = None

    def as_log_fields(self) -> dict[str, Any]:
        if self.qualifying_match_count:
            return {}
        fields: dict[str, Any] = {
            "post_admission_no_token_overlap_count": self.no_token_overlap_count,
            "post_admission_below_min_post_weight_score_count": (
                self.below_min_post_weight_score_count
            ),
            "post_admission_weight_demoted_below_min_score_count": (
                self.weight_demoted_below_min_score_count
            ),
            "post_admission_min_match_score": self.min_match_score,
        }
        if self.best_rejected_pre_weight_score is not None:
            fields["post_admission_best_rejected_pre_weight_score"] = (
                self.best_rejected_pre_weight_score
            )
        if self.best_rejected_post_weight_score is not None:
            fields["post_admission_best_rejected_post_weight_score"] = (
                self.best_rejected_post_weight_score
            )
        if self.counterfactual_shadow is not None:
            fields["post_admission_counterfactual_shadow"] = (
                self.counterfactual_shadow
            )
        return fields


class _PublicMarketClient(Protocol):
    def get_markets(self, *, limit: int) -> tuple[list[PolymarketMarket], str | None]:
        ...


@dataclass(frozen=True)
class _MarketPageFetch:
    markets: list[PolymarketMarket]
    raw_fetched: int
    raw_unique: int
    pages_fetched: int
    cursor_present: bool
    pagination_exhausted: bool
    pagination_stop_reason: str


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
        sizing_bankroll_provider: Callable[[], float] | None = None,
        now_provider: Callable[[], datetime] | None = None,
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
        self._sizing_bankroll_provider = sizing_bankroll_provider or (lambda: cfg.bankroll)
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
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
        markets, market_fetch_failed = await self._get_markets()
        if not markets:
            self._last_match_count = 0
            no_candidate_pool = _NoCandidatePoolTelemetry(
                candidate_pool_stage=(
                    "provider_fetch_failed"
                    if market_fetch_failed
                    else "eligible_cache_empty"
                ),
                pre_admission_matchable_market_count=0,
                within_admission_horizon_market_count=0,
                admission_horizon_days=cfg.paper_admission_max_days_to_close,
            )
            await _write_funnel_telemetry(
                "MATCH_NO_CANDIDATE",
                trade_log.log_match_no_candidate,
                source=news.source,
                headline=news.headline,
                venue=Venue.POLYMARKET_US.value,
                eligible_market_count=0,
                reason=(
                    "market_fetch_failed" if market_fetch_failed else "no_eligible_markets"
                ),
                candidate_pool_stage=no_candidate_pool.candidate_pool_stage,
                pre_admission_matchable_market_count=(
                    no_candidate_pool.pre_admission_matchable_market_count
                ),
                within_admission_horizon_market_count=(
                    no_candidate_pool.within_admission_horizon_market_count
                ),
                admission_horizon_days=no_candidate_pool.admission_horizon_days,
            )
            self._log_heartbeat()
            return 0

        match_now = self._horizon_now()
        admission_horizon_days = cfg.paper_admission_max_days_to_close
        news_tokens, headline_tokens = _news_match_tokens(news)
        has_match_tokens = bool(news_tokens or headline_tokens)
        token_weights = _load_match_weights() if has_match_tokens else None
        matches, post_admission_rejection = _match_polymarket_markets_with_rejection_telemetry(
            news,
            markets,
            max_results=self._max_candidates,
            min_score=self._min_match_score,
            token_weights=token_weights,
            now=match_now,
            admission_horizon_days=admission_horizon_days,
        )
        self._last_match_count = len(matches)
        if not matches:
            self._no_match_count += 1
            no_candidate_pool = _classify_no_candidate_pool(
                news,
                markets,
                now=match_now,
                admission_horizon_days=admission_horizon_days,
            )
            rejection_fields = (
                post_admission_rejection.as_log_fields()
                if no_candidate_pool.candidate_pool_stage == "post_admission_no_match"
                else {}
            )
            log.info(
                "[POLYMARKET_MATCH] no_match markets=%d headline=%s",
                len(markets),
                news.headline[:80],
            )
            await _write_funnel_telemetry(
                "MATCH_NO_CANDIDATE",
                trade_log.log_match_no_candidate,
                source=news.source,
                headline=news.headline,
                venue=Venue.POLYMARKET_US.value,
                eligible_market_count=len(markets),
                reason="no_match",
                candidate_pool_stage=no_candidate_pool.candidate_pool_stage,
                pre_admission_matchable_market_count=(
                    no_candidate_pool.pre_admission_matchable_market_count
                ),
                within_admission_horizon_market_count=(
                    no_candidate_pool.within_admission_horizon_market_count
                ),
                admission_horizon_days=no_candidate_pool.admission_horizon_days,
                **rejection_fields,
            )
            await self._write_horizon_shadow_telemetry(
                news,
                markets,
                now=match_now,
                production_horizon_days=admission_horizon_days,
                production_rejection=post_admission_rejection,
                token_weights=token_weights,
                has_match_tokens=has_match_tokens,
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
        await self._write_horizon_shadow_telemetry(
            news,
            markets,
            now=match_now,
            production_horizon_days=admission_horizon_days,
            production_rejection=post_admission_rejection,
            token_weights=token_weights,
            has_match_tokens=has_match_tokens,
        )
        self._log_heartbeat()
        return routed

    async def _write_horizon_shadow_telemetry(
        self,
        news: NewsItem,
        markets: Sequence[PolymarketMarket],
        *,
        now: datetime,
        production_horizon_days: float,
        production_rejection: _PostAdmissionRejectionTelemetry,
        token_weights: dict[str, dict[str, Any]] | None,
        has_match_tokens: bool,
    ) -> None:
        """Compare the next horizon band without creating a candidate or order path."""
        try:
            if not has_match_tokens or production_horizon_days >= _HORIZON_SHADOW_END_DAYS:
                return
            production_markets, shadow_markets = _horizon_shadow_market_sets(
                markets,
                now=now,
                production_horizon_days=production_horizon_days,
                shadow_horizon_end_days=_HORIZON_SHADOW_END_DAYS,
            )
            _shadow_matches, shadow_rejection = (
                _match_polymarket_markets_with_rejection_telemetry(
                    news,
                    shadow_markets,
                    max_results=self._max_candidates,
                    min_score=self._min_match_score,
                    token_weights=token_weights,
                    now=now,
                    admission_horizon_days=_HORIZON_SHADOW_END_DAYS,
                    emit_match_weight_telemetry=False,
                )
            )
            fields: dict[str, Any] = {
                "source": news.source,
                "headline": news.headline,
                "venue": Venue.POLYMARKET_US.value,
                "production_horizon_days": production_horizon_days,
                "shadow_horizon_start_days": production_horizon_days,
                "shadow_horizon_end_days": _HORIZON_SHADOW_END_DAYS,
                "production_candidate_count": len(production_markets),
                "shadow_candidate_count": len(shadow_markets),
                "production_qualifying_match_count": (
                    production_rejection.qualifying_match_count
                ),
                "shadow_qualifying_match_count": shadow_rejection.qualifying_match_count,
                "production_no_token_overlap_count": (
                    production_rejection.no_token_overlap_count
                ),
                "production_below_min_post_weight_score_count": (
                    production_rejection.below_min_post_weight_score_count
                ),
                "production_weight_demoted_below_min_score_count": (
                    production_rejection.weight_demoted_below_min_score_count
                ),
                "production_min_match_score": production_rejection.min_match_score,
                "shadow_no_token_overlap_count": shadow_rejection.no_token_overlap_count,
                "shadow_below_min_post_weight_score_count": (
                    shadow_rejection.below_min_post_weight_score_count
                ),
                "shadow_weight_demoted_below_min_score_count": (
                    shadow_rejection.weight_demoted_below_min_score_count
                ),
                "shadow_min_match_score": shadow_rejection.min_match_score,
                "shadow_analysis_status": "not_evaluated_shadow_only",
            }
            if production_rejection.counterfactual_shadow is not None:
                fields["production_counterfactual_shadow"] = (
                    production_rejection.counterfactual_shadow
                )
            if production_rejection.best_rejected_pre_weight_score is not None:
                fields["production_best_rejected_pre_weight_score"] = (
                    production_rejection.best_rejected_pre_weight_score
                )
            if production_rejection.best_rejected_post_weight_score is not None:
                fields["production_best_rejected_post_weight_score"] = (
                    production_rejection.best_rejected_post_weight_score
                )
            if shadow_rejection.counterfactual_shadow is not None:
                fields["shadow_counterfactual_shadow"] = (
                    shadow_rejection.counterfactual_shadow
                )
            if shadow_rejection.best_rejected_pre_weight_score is not None:
                fields["shadow_best_rejected_pre_weight_score"] = (
                    shadow_rejection.best_rejected_pre_weight_score
                )
            if shadow_rejection.best_rejected_post_weight_score is not None:
                fields["shadow_best_rejected_post_weight_score"] = (
                    shadow_rejection.best_rejected_post_weight_score
                )
            await _write_funnel_telemetry(
                "POLYMARKET_HORIZON_SHADOW",
                trade_log.log_polymarket_horizon_shadow,
                **fields,
            )
        except Exception:
            log.warning(
                "[POLYMARKET_PAPER] horizon_shadow_telemetry_failed",
                exc_info=True,
            )

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
        now = self._horizon_now()
        admission_horizon_days = cfg.paper_admission_max_days_to_close
        return [
            market
            for market in self._markets
            if (
                _is_pre_admission_matchable_market(market)
                and _is_within_admission_horizon(
                    market,
                    now=now,
                    admission_horizon_days=admission_horizon_days,
                )
            )
        ]

    async def warm_cache(self) -> int:
        markets, _ = await self._get_markets()
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

    def _fetch_market_pages(self) -> _MarketPageFetch:
        page_size = min(self._market_limit, _PUBLIC_MARKET_PAGE_SIZE)
        if page_size <= 0:
            raise ValueError("Polymarket market_limit must be positive")

        markets: list[PolymarketMarket] = []
        seen_market_ids: set[str] = set()
        seen_cursors: set[str] = set()
        raw_fetched = 0
        pages_fetched = 0
        cursor_present = False
        using_cursor = False
        cursor: str | None = None
        offset = 0
        get_market_page = getattr(self._client, "get_market_page", None)
        has_page_metadata = callable(get_market_page)

        for _ in range(_MAX_MARKET_FETCH_PAGES):
            request: dict[str, Any] = {"limit": page_size}
            if cursor is not None:
                request["cursor"] = cursor
            elif offset:
                request["offset"] = offset
            if has_page_metadata:
                page_result = get_market_page(**request)
                page = page_result.markets
                next_cursor = page_result.cursor
                raw_page_count = page_result.raw_count
                if (
                    isinstance(raw_page_count, bool)
                    or not isinstance(raw_page_count, int)
                    or raw_page_count < len(page)
                    or raw_page_count > page_size
                ):
                    raise ValueError("Polymarket market page raw_count is invalid")
            else:
                page, next_cursor = self._client.get_markets(limit=page_size)
                raw_page_count = len(page)
            pages_fetched += 1
            raw_fetched += raw_page_count

            for market in page:
                if market.market_id in seen_market_ids:
                    continue
                seen_market_ids.add(market.market_id)
                markets.append(market)

            if not has_page_metadata:
                return _MarketPageFetch(
                    markets=markets,
                    raw_fetched=raw_fetched,
                    raw_unique=len(markets),
                    pages_fetched=pages_fetched,
                    cursor_present=bool(next_cursor),
                    pagination_exhausted=False,
                    pagination_stop_reason="legacy_client_no_page_metadata",
                )

            if next_cursor:
                cursor_present = True
                if next_cursor in seen_cursors:
                    return _MarketPageFetch(
                        markets=markets,
                        raw_fetched=raw_fetched,
                        raw_unique=len(markets),
                        pages_fetched=pages_fetched,
                        cursor_present=cursor_present,
                        pagination_exhausted=False,
                        pagination_stop_reason="cursor_cycle",
                    )
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                using_cursor = True
                continue

            if using_cursor:
                return _MarketPageFetch(
                    markets=markets,
                    raw_fetched=raw_fetched,
                    raw_unique=len(markets),
                    pages_fetched=pages_fetched,
                    cursor_present=cursor_present,
                    pagination_exhausted=True,
                    pagination_stop_reason="cursor_end",
                )

            if raw_page_count < page_size:
                return _MarketPageFetch(
                    markets=markets,
                    raw_fetched=raw_fetched,
                    raw_unique=len(markets),
                    pages_fetched=pages_fetched,
                    cursor_present=cursor_present,
                    pagination_exhausted=True,
                    pagination_stop_reason="short_page",
                )

            offset += page_size

        return _MarketPageFetch(
            markets=markets,
            raw_fetched=raw_fetched,
            raw_unique=len(markets),
            pages_fetched=pages_fetched,
            cursor_present=cursor_present,
            pagination_exhausted=False,
            pagination_stop_reason="page_cap",
        )

    async def _get_markets(self) -> tuple[list[PolymarketMarket], bool]:
        async with self._fetch_lock:
            age = time.monotonic() - self._last_fetch
            if self._markets and age < self._market_cache_ttl_seconds:
                return list(self._markets), False
            try:
                market_fetch = await asyncio.to_thread(self._fetch_market_pages)
            except Exception as exc:
                self._last_error = "public_market_fetch_failed"
                log.warning(
                    "[POLYMARKET_PAPER] public_market_fetch_failed error=%s",
                    exc,
                )
                return [], True
            markets = market_fetch.markets
            horizon_now = self._horizon_now()
            self._markets = [
                market
                for market in markets
                if evaluate_market_horizon(
                    market.close_time,
                    now=horizon_now,
                    max_days_to_close=MAX_MARKET_DAYS_TO_EXPIRY,
                ).eligible
            ]
            self._last_fetch = time.monotonic()
            self._last_error = None
            admission_horizon_days = cfg.paper_admission_max_days_to_close
            cache_telemetry = _MarketCacheTelemetry(
                raw_fetched=market_fetch.raw_fetched,
                raw_unique=market_fetch.raw_unique,
                pages_fetched=market_fetch.pages_fetched,
                cursor_present=market_fetch.cursor_present,
                pagination_exhausted=market_fetch.pagination_exhausted,
                pagination_stop_reason=market_fetch.pagination_stop_reason,
                eligible_30d=len(self._markets),
                candidate_within_admission_horizon=len(self.cached_candidate_markets()),
                admission_horizon_days=admission_horizon_days,
                market_limit=self._market_limit,
            )
            refreshed_markets = list(self._markets)
        await _write_funnel_telemetry(
            "POLYMARKET_MARKET_CACHE",
            trade_log.log_polymarket_market_cache,
            raw_fetched=cache_telemetry.raw_fetched,
            raw_unique=cache_telemetry.raw_unique,
            pages_fetched=cache_telemetry.pages_fetched,
            cursor_present=cache_telemetry.cursor_present,
            pagination_exhausted=cache_telemetry.pagination_exhausted,
            pagination_stop_reason=cache_telemetry.pagination_stop_reason,
            eligible_30d=cache_telemetry.eligible_30d,
            candidate_within_admission_horizon=(
                cache_telemetry.candidate_within_admission_horizon
            ),
            admission_horizon_days=cache_telemetry.admission_horizon_days,
            market_limit=cache_telemetry.market_limit,
        )
        log.info(
            "[POLYMARKET_PAPER] market_cache_refreshed markets=%d raw=%d unique=%d "
            "pages=%d exhausted=%s stop=%s limit=%d",
            len(refreshed_markets),
            cache_telemetry.raw_fetched,
            cache_telemetry.raw_unique,
            cache_telemetry.pages_fetched,
            cache_telemetry.pagination_exhausted,
            cache_telemetry.pagination_stop_reason,
            self._market_limit,
        )
        return refreshed_markets, False

    def _horizon_now(self) -> datetime:
        now = self._now_provider()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Polymarket paper runtime clock must be timezone-aware")
        return now.astimezone(timezone.utc)

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
        try:
            supplied_bankroll = self._sizing_bankroll_provider()
            if isinstance(supplied_bankroll, bool):
                raise ValueError("boolean sizing bankroll")
            notional = float(supplied_bankroll)
        except Exception as exc:  # sizing source must never make a paper order fail open
            log.warning(
                "[POLYMARKET_PAPER] analysis_skip ticker=%s reason=invalid_sizing_bankroll error=%s",
                market.market_id,
                exc,
            )
            return None
        if not math.isfinite(notional) or notional <= 0:
            log.warning(
                "[POLYMARKET_PAPER] analysis_skip ticker=%s reason=invalid_sizing_bankroll",
                market.market_id,
            )
            return None
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
            as_of=self._horizon_now().isoformat(),
        )
        adapted.signal_meta["_feedback_decision_draft"] = _FeedbackDecisionDraft(
            lifecycle_id=str(match_meta["lifecycle_id"]),
            ticker=adapted.market.ticker,
            source=news.source,
            series_ticker=str(getattr(market, "series_ticker", "") or "") or None,
            decision_at=self._horizon_now().isoformat(),
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
    now: datetime | None = None,
    admission_horizon_days: float | None = None,
) -> list[tuple[PolymarketMarket, float, dict[str, Any]]]:
    matches, _ = _match_polymarket_markets_with_rejection_telemetry(
        news,
        markets,
        max_results=max_results,
        min_score=min_score,
        token_weights=token_weights,
        now=now,
        admission_horizon_days=admission_horizon_days,
    )
    return matches


def _match_polymarket_markets_with_rejection_telemetry(
    news: NewsItem,
    markets: Sequence[PolymarketMarket],
    *,
    max_results: int = _DEFAULT_MAX_CANDIDATES,
    min_score: float = _DEFAULT_MIN_MATCH_SCORE,
    token_weights: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
    admission_horizon_days: float | None = None,
    emit_match_weight_telemetry: bool = True,
) -> tuple[
    list[tuple[PolymarketMarket, float, dict[str, Any]]],
    _PostAdmissionRejectionTelemetry,
]:
    news_tokens, headline_tokens = _news_match_tokens(news)
    if not news_tokens and not headline_tokens:
        return [], _PostAdmissionRejectionTelemetry(
            no_token_overlap_count=0,
            below_min_post_weight_score_count=0,
            weight_demoted_below_min_score_count=0,
            min_match_score=min_score,
            best_rejected_pre_weight_score=None,
            best_rejected_post_weight_score=None,
            qualifying_match_count=0,
        )
    if token_weights is None:
        token_weights = _load_match_weights()

    scored: list[tuple[PolymarketMarket, float, dict[str, Any]]] = []
    no_token_overlap_count = 0
    below_min_post_weight_score_count = 0
    weight_demoted_below_min_score_count = 0
    best_rejected_pre_weight_score: float | None = None
    best_rejected_post_weight_score: float | None = None
    qualifying_match_count = 0
    counterfactual_candidates: list[dict[str, Any]] = []
    counterfactual_candidate_count_total = 0
    match_time = now or datetime.now(timezone.utc)
    if match_time.tzinfo is None or match_time.utcoffset() is None:
        raise ValueError("Polymarket market matching clock must be timezone-aware")
    match_time = match_time.astimezone(timezone.utc)
    if admission_horizon_days is None:
        admission_horizon_days = cfg.paper_admission_max_days_to_close
    for market in markets:
        if not _is_pre_admission_matchable_market(market):
            continue
        if not _is_within_admission_horizon(
            market,
            now=match_time,
            admission_horizon_days=admission_horizon_days,
        ):
            continue
        # Covers every admitted market, including rows we cannot safely serialize.
        counterfactual_candidate_count_total += 1
        market_tokens = _meaningful_tokens(_market_match_text(market))
        if not market_tokens:
            no_token_overlap_count += 1
            candidate = _counterfactual_candidate(
                market,
                rejection_reason="market_without_match_tokens",
                market_token_count=0,
                matched_token_count=0,
            )
            if candidate is not None:
                _append_counterfactual_candidate(counterfactual_candidates, candidate)
            continue
        overlap = news_tokens & market_tokens
        headline_overlap = headline_tokens & market_tokens
        if not overlap:
            no_token_overlap_count += 1
            candidate = _counterfactual_candidate(
                market,
                rejection_reason="no_token_overlap",
                market_token_count=len(market_tokens),
                matched_token_count=0,
            )
            if candidate is not None:
                _append_counterfactual_candidate(counterfactual_candidates, candidate)
            continue
        full_score = len(overlap) / max(1, len(news_tokens | market_tokens))
        headline_score = len(headline_overlap) / max(1, len(headline_tokens | market_tokens))
        score = max(full_score, headline_score)
        pre_weight_score = score
        market_prefix = market_prefix_for(market)
        if overlap and token_weights:
            weight_details = _token_downweight_details(
                overlap,
                market_prefix,
                token_weights,
            )
            score *= weight_details["final_multiplier"]
            if emit_match_weight_telemetry:
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
            below_min_post_weight_score_count += 1
            weight_demoted = pre_weight_score >= min_score
            if weight_demoted:
                weight_demoted_below_min_score_count += 1
            best_rejected_pre_weight_score = max(
                best_rejected_pre_weight_score or pre_weight_score,
                pre_weight_score,
            )
            best_rejected_post_weight_score = max(
                best_rejected_post_weight_score or score,
                score,
            )
            candidate = _counterfactual_candidate(
                market,
                rejection_reason=(
                    "weight_demoted_below_min_score"
                    if weight_demoted
                    else "below_min_post_weight_score"
                ),
                market_token_count=len(market_tokens),
                matched_token_count=len(overlap),
                pre_weight_score=pre_weight_score,
                post_weight_score=score,
            )
            if candidate is not None:
                _append_counterfactual_candidate(counterfactual_candidates, candidate)
            continue
        qualifying_match_count += 1
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
    counterfactual_shadow: dict[str, Any] | None = None
    if not qualifying_match_count and counterfactual_candidate_count_total:
        captured_market_count = len(counterfactual_candidates)
        omitted_market_count = (
            counterfactual_candidate_count_total - captured_market_count
        )
        counterfactual_shadow = {
            "schema_version": _COUNTERFACTUAL_SNAPSHOT_SCHEMA_VERSION,
            "match_clock_utc": match_time.isoformat(),
            "news_headline_token_count": len(headline_tokens),
            "news_match_token_count": len(news_tokens),
            "candidate_count_total": counterfactual_candidate_count_total,
            "captured_market_count": captured_market_count,
            "omitted_market_count": omitted_market_count,
            "truncated": omitted_market_count > 0,
            "candidates": counterfactual_candidates,
        }
    return scored[:max_results], _PostAdmissionRejectionTelemetry(
        no_token_overlap_count=no_token_overlap_count,
        below_min_post_weight_score_count=below_min_post_weight_score_count,
        weight_demoted_below_min_score_count=weight_demoted_below_min_score_count,
        min_match_score=min_score,
        best_rejected_pre_weight_score=best_rejected_pre_weight_score,
        best_rejected_post_weight_score=best_rejected_post_weight_score,
        qualifying_match_count=qualifying_match_count,
        counterfactual_shadow=counterfactual_shadow,
    )


def _classify_no_candidate_pool(
    news: NewsItem,
    markets: Sequence[PolymarketMarket],
    *,
    now: datetime,
    admission_horizon_days: float,
) -> _NoCandidatePoolTelemetry:
    pre_admission_markets = [
        market for market in markets if _is_pre_admission_matchable_market(market)
    ]
    within_admission_horizon_markets = [
        market
        for market in pre_admission_markets
        if _is_within_admission_horizon(
            market,
            now=now,
            admission_horizon_days=admission_horizon_days,
        )
    ]
    if not any(_news_match_tokens(news)):
        candidate_pool_stage = "input_without_match_tokens"
    elif not pre_admission_markets:
        candidate_pool_stage = "pre_admission_filter_empty"
    elif not within_admission_horizon_markets:
        candidate_pool_stage = "admission_horizon_pruned"
    else:
        candidate_pool_stage = "post_admission_no_match"
    return _NoCandidatePoolTelemetry(
        candidate_pool_stage=candidate_pool_stage,
        pre_admission_matchable_market_count=len(pre_admission_markets),
        within_admission_horizon_market_count=len(within_admission_horizon_markets),
        admission_horizon_days=admission_horizon_days,
    )


def _horizon_shadow_market_sets(
    markets: Sequence[PolymarketMarket],
    *,
    now: datetime,
    production_horizon_days: float,
    shadow_horizon_end_days: float,
) -> tuple[list[PolymarketMarket], list[PolymarketMarket]]:
    """Return current admission candidates and the disjoint next horizon band."""
    production = select_polymarket_horizon_band(
        markets,
        now=now,
        lower_exclusive_days=0.0,
        upper_inclusive_days=production_horizon_days,
    )
    shadow = select_polymarket_horizon_band(
        markets,
        now=now,
        lower_exclusive_days=production_horizon_days,
        upper_inclusive_days=shadow_horizon_end_days,
    )
    return production, shadow


def _is_pre_admission_matchable_market(market: PolymarketMarket) -> bool:
    return (
        market.venue == Venue.POLYMARKET_US
        and market.is_tradeable()
        and not _is_suppressed_market(market)
    )


def _news_match_tokens(news: NewsItem) -> tuple[set[str], set[str]]:
    return (
        _meaningful_tokens(f"{news.headline} {news.body}"),
        _meaningful_tokens(news.headline),
    )


def _is_within_admission_horizon(
    market: PolymarketMarket,
    *,
    now: datetime,
    admission_horizon_days: float,
) -> bool:
    return evaluate_market_horizon(
        market.close_time,
        now=now,
        max_days_to_close=admission_horizon_days,
    ).eligible


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
