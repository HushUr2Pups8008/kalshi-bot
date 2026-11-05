"""Free settlement-source reader for a homepage or an empty PDF.

A Truth Social count comes from the Factbase counter. A White House action
count comes from the existing presidential-actions archive parser. Either
result is priced only by that existing calculator. A failed fetch, a missing
count, or any other contract returns no evidence. This module does not call
xAI, does not place orders, and does not invent a probability.
"""

from __future__ import annotations

import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from analysis import truth_social_forecast
from analysis.truth_social_forecast import (
    TRUTH_SOCIAL_RANGE_PROBABILITY_METRIC,
    TruthSocialCountObservation,
    parse_truth_social_count_contract,
    truth_social_range_probability,
)
from utils.event_news_research import (
    event_news_executed_side_skip_reason,
    event_news_favorite_side,
    event_news_has_structured_official_metric,
)
from utils.logger import get_logger

log = get_logger("event_news_grok_source")

_NO_EDGE_SKIPS = frozenset({"no_edge", "negative_net_edge_after_costs"})


def event_news_grok_source_skip_reason(
    *,
    market: Any,
    evidence: list[Any] | None,
    bad_urls: tuple[str, ...] | list[str],
    skip_reason: str | None = None,
    cache_only: bool = False,
) -> str | None:
    """Why this book must not fetch. None means a free count may be tried."""
    if cache_only:
        return "cache_only"
    if event_news_has_structured_official_metric(evidence):
        return "structured_count"
    urls = tuple(str(url).strip() for url in bad_urls if str(url).strip())
    if not urls:
        return "no_bad_url"
    if str(skip_reason or "").strip() in _NO_EDGE_SKIPS:
        return str(skip_reason).strip()
    side, _cents = event_news_favorite_side(market)
    if side is None:
        return "favorite_side_missing"
    executed = event_news_executed_side_skip_reason(market, side)
    if executed:
        return executed
    return None


def event_news_grok_source_evidence(
    *,
    market: Any,
    evidence: list[Any] | None = None,
    bad_urls: tuple[str, ...] | list[str] = (),
    yes_ask: float | None = None,
    no_ask: float | None = None,
    skip_reason: str | None = None,
    cache_only: bool = False,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> tuple[Any, ...]:
    """Return priced structured evidence, or nothing. Never invents p."""
    del yes_ask, no_ask, environ, post
    ticker = str(getattr(market, "ticker", "") or "")
    try:
        return _source_evidence(
            market=market,
            evidence=evidence,
            bad_urls=bad_urls,
            skip_reason=skip_reason,
            cache_only=cache_only,
            now=now,
        )
    except Exception:
        _log_source("timeout", ticker, 0, "request_error")
        return ()


async def event_news_grok_source_reader(**kwargs: Any) -> tuple[Any, ...]:
    """Async wrapper for the research gate. HTTP stays off the analysis import."""
    import asyncio

    return await asyncio.to_thread(event_news_grok_source_evidence, **kwargs)


def _source_evidence(
    *,
    market: Any,
    evidence: list[Any] | None,
    bad_urls: tuple[str, ...] | list[str],
    skip_reason: str | None,
    cache_only: bool,
    now: datetime | None,
) -> tuple[Any, ...]:
    ticker = str(getattr(market, "ticker", "") or "")
    skip = event_news_grok_source_skip_reason(
        market=market,
        evidence=evidence,
        bad_urls=bad_urls,
        skip_reason=skip_reason,
        cache_only=cache_only,
    )
    if skip is not None:
        _log_source("skipped", ticker, 0, skip)
        return ()

    started = time.monotonic()
    try:
        priced = _free_settlement_evidence(market, now=now)
    except Exception:
        _log_source("timeout", ticker, _elapsed_ms(started), "fetch_error")
        return ()
    if not priced:
        _log_source("no_fact", ticker, _elapsed_ms(started), "no_free_count")
        return ()
    _log_source(
        "fact",
        ticker,
        _elapsed_ms(started),
        str(getattr(priced[0], "metric_name", "") or "named_count"),
    )
    return priced


def _free_settlement_evidence(
    market: Any,
    *,
    now: datetime | None,
) -> tuple[Any, ...]:
    """One priced item from a free counter, or nothing. No model call."""
    query = _market_query(market)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    contract = parse_truth_social_count_contract(query)
    if contract is not None:
        observation = truth_social_forecast.fetch_truth_social_count(query)
        if observation is None or not observation.complete:
            return ()
        fact_url = str(observation.source_url or "").strip()
        if not _path_specific_https(fact_url):
            return ()
        priced = _truth_social_evidence(
            contract,
            int(observation.count),
            fact_url,
            clock,
        )
        return () if priced is None else (priced,)

    from analysis.research_gate import (
        ResearchQuery,
        _white_house_action_count_spec_from_text,
        _white_house_presidential_actions_search,
    )

    if _white_house_action_count_spec_from_text(query) is None:
        return ()
    found = _white_house_presidential_actions_search(
        ResearchQuery(
            query=query,
            query_intent="official_count",
            source_class="official_primary",
        ),
        now=clock,
    )
    for item in found:
        if str(getattr(item, "metric_name", "") or "") != "white_house_action_range_probability":
            continue
        probability = getattr(item, "metric_value", None)
        confidence = getattr(item, "supports_confidence", None)
        fact_url = str(getattr(item, "source_url", "") or "").strip()
        if isinstance(probability, bool) or not isinstance(probability, (int, float)):
            continue
        if not 0.0 < float(probability) < 1.0:
            continue
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            continue
        if float(confidence) < 0.65:
            continue
        if not _path_specific_https(fact_url):
            continue
        return (item,)
    return ()


def _truth_social_evidence(contract: Any, count: int, fact_url: str, now: datetime) -> Any | None:
    observation = TruthSocialCountObservation(
        count=count,
        deleted_count=0,
        window_start=contract.week_start,
        window_end=contract.week_start + timedelta(days=6),
        lower=contract.lower,
        upper=contract.upper,
        complete=True,
        pages=0,
        source_url=fact_url,
        observed_at=now.astimezone(timezone.utc).isoformat(),
    )
    forecast = truth_social_range_probability(observation, now=now)
    if forecast is None:
        return None
    probability, state, confidence = forecast
    return _range_evidence(
        metric_name=TRUTH_SOCIAL_RANGE_PROBABILITY_METRIC,
        probability=probability,
        confidence=confidence,
        fact_url=fact_url,
        title=f"Named Truth Social count {count}",
        snippet=(
            f"Named official count is {count} for {observation.window_start.isoformat()} "
            f"through {observation.window_end.isoformat()} ({state}). "
            f"Range calculator YES probability {probability:.3f}. Source {fact_url}."
        ),
        now=now,
    )


def _range_evidence(
    *,
    metric_name: str,
    probability: float,
    confidence: float,
    fact_url: str,
    title: str,
    snippet: str,
    now: datetime,
) -> Any | None:
    if not 0.0 < float(probability) < 1.0:
        return None
    if float(confidence) < 0.65:
        return None
    from analysis.research_gate import ResearchEvidence

    direction = "yes" if float(probability) >= 0.5 else "no"
    retrieved = now.astimezone(timezone.utc).isoformat()
    domain = urllib.parse.urlparse(fact_url).netloc or fact_url
    return ResearchEvidence(
        source_class="official_primary",
        source_name=domain,
        source_url=fact_url,
        title=title,
        snippet=snippet,
        claim_type="official_resolution",
        supports_direction=direction,
        supports_confidence=float(confidence),
        published_at=retrieved,
        retrieved_at=retrieved,
        metric_name=metric_name,
        metric_value=float(probability),
        metric_unit="probability",
        extraction_confidence=0.96,
    )


def _market_query(market: Any) -> str:
    parts = [
        getattr(market, "ticker", ""),
        getattr(market, "title", ""),
        getattr(market, "subtitle", ""),
        getattr(market, "rules_primary", ""),
        getattr(market, "rules_secondary", ""),
    ]
    return " ".join(str(part) for part in parts if part)


def _path_specific_https(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    return (parsed.path or "").strip() not in {"", "/"}


def _elapsed_ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000))


def _log_source(result: str, ticker: str, elapsed_ms: int, reason: str) -> None:
    log.info(
        "[EVENT_NEWS_GROK_SOURCE] result=%s ticker=%s elapsed_ms=%s reason=%s",
        result,
        ticker,
        elapsed_ms,
        reason,
    )
