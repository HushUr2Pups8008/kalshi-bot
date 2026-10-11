"""Politics-only Grok source reader for a homepage or an empty PDF.

Grok may name a count, vote, or status and the URL that states it. A count
becomes a probability only through the existing Truth Social or White House
calculator. A vote, a status, a timeout, or a parse failure stays
needs_counter_evidence. This module does not place orders and does not log
the API key.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import requests

from analysis.truth_social_forecast import (
    TRUTH_SOCIAL_RANGE_PROBABILITY_METRIC,
    TruthSocialCountObservation,
    parse_truth_social_count_contract,
    remaining_threshold_probability,
    truth_social_range_probability,
)
from utils.event_news_grok_veto import (
    GROK_MAX_TOKENS,
    GROK_MODEL,
    GROK_TIMEOUT_SECONDS,
    XAI_API_KEY_ENV,
    XAI_CHAT_URL,
)
from utils.event_news_research import (
    event_news_executed_side_skip_reason,
    event_news_favorite_side,
    event_news_has_structured_official_metric,
    is_event_news_paper_cohort,
)
from utils.logger import get_logger

log = get_logger("event_news_grok_source")

_SOURCE_SYSTEM = (
    "Reply with one JSON object only. Keys are kind, value, and url. "
    "kind is count, vote, or status. value is the count, vote, or status. "
    "url is the https page that states it. Do not include a probability. "
    'If unknown, reply {"kind":"none","value":"","url":""}.'
)
_SOURCE_USER = "What is the count, vote, or status, and the URL that states it?"
_NO_EDGE_SKIPS = frozenset({"no_edge", "negative_net_edge_after_costs"})
_PROBABILITY_KEYS = frozenset(
    {
        "estimated_probability",
        "estimated_probability_yes",
        "official_p",
        "p",
        "probability",
        "probability_yes",
    }
)


def event_news_grok_source_skip_reason(
    *,
    market: Any,
    evidence: list[Any] | None,
    bad_urls: tuple[str, ...] | list[str],
    skip_reason: str | None = None,
    cache_only: bool = False,
) -> str | None:
    """Why this book must not call Grok. None means the packet may be sent."""
    if cache_only:
        return "cache_only"
    if event_news_has_structured_official_metric(evidence):
        return "structured_count"
    urls = tuple(str(url).strip() for url in bad_urls if str(url).strip())
    if not urls:
        return "no_bad_url"
    if not is_event_news_paper_cohort():
        return "not_politics_cohort"
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
    del yes_ask, no_ask
    ticker = str(getattr(market, "ticker", "") or "")
    try:
        return _source_evidence(
            market=market,
            evidence=evidence,
            bad_urls=bad_urls,
            skip_reason=skip_reason,
            cache_only=cache_only,
            environ=environ,
            post=post,
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
    environ: Mapping[str, str] | None,
    post: Callable[..., Any] | None,
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
    urls = tuple(str(url).strip() for url in bad_urls if str(url).strip())
    content, failure = _post_source(
        _packet(market, urls),
        ticker=ticker,
        environ=environ,
        post=post,
        started=started,
    )
    if failure is not None:
        return ()
    parsed = _parse_source_object(content or "")
    if parsed is None:
        _log_source("timeout", ticker, _elapsed_ms(started), "parse_fail")
        return ()
    kind = parsed[0]
    if kind == "parse_fail":
        _log_source("timeout", ticker, _elapsed_ms(started), "parse_fail")
        return ()
    if kind != "count":
        _log_source("no_fact", ticker, _elapsed_ms(started), kind)
        return ()
    _kind, count, fact_url = parsed
    priced = _priced_count_evidence(
        market,
        count=count,
        fact_url=fact_url,
        bad_urls=urls,
        now=now,
    )
    if priced is None:
        _log_source("no_fact", ticker, _elapsed_ms(started), "unpriced_contract")
        return ()
    _log_source(
        "fact",
        ticker,
        _elapsed_ms(started),
        str(getattr(priced, "metric_name", "") or "named_count"),
    )
    return (priced,)


def _packet(market: Any, bad_urls: tuple[str, ...]) -> dict[str, Any]:
    return {
        "ticker": str(getattr(market, "ticker", "") or ""),
        "settlement_sources": _settlement_urls(market),
        "contract_terms_url": str(getattr(market, "contract_terms_url", "") or ""),
        "bad_url": bad_urls[0],
        "bad_urls": list(bad_urls),
    }


def _post_source(
    packet: Mapping[str, Any],
    *,
    ticker: str,
    environ: Mapping[str, str] | None,
    post: Callable[..., Any] | None,
    started: float,
) -> tuple[str | None, str | None]:
    env = os.environ if environ is None else environ
    key = str(env.get(XAI_API_KEY_ENV) or "").strip()
    if not key:
        _log_source("timeout", ticker, _elapsed_ms(started), "missing_key")
        return None, "missing_key"
    payload = {
        "model": GROK_MODEL,
        "temperature": 0,
        "max_tokens": GROK_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _SOURCE_SYSTEM},
            {
                "role": "user",
                "content": _SOURCE_USER
                + "\n"
                + json.dumps(packet, separators=(",", ":")),
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    sender = post if post is not None else requests.post
    try:
        response = sender(
            XAI_CHAT_URL,
            json=payload,
            headers=headers,
            timeout=GROK_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        _log_source("timeout", ticker, _elapsed_ms(started), "timeout")
        return None, "timeout"
    except Exception:
        _log_source("timeout", ticker, _elapsed_ms(started), "request_error")
        return None, "request_error"
    status = getattr(response, "status_code", None)
    if status != 200:
        _log_source("timeout", ticker, _elapsed_ms(started), f"http_{status}")
        return None, "http"
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except Exception:
        _log_source("timeout", ticker, _elapsed_ms(started), "parse_fail")
        return None, "parse_fail"
    return str(content), None


def _parse_source_object(content: str) -> tuple[str, int, str] | None:
    """Return (kind, count, url). kind none/vote/status has count 0 and url ''."""
    try:
        obj = _last_json_object(content)
    except ValueError:
        return None
    if any(key in obj for key in _PROBABILITY_KEYS) and "kind" not in obj:
        return ("parse_fail", 0, "")
    kind = str(obj.get("kind") or "").strip().lower()
    if kind == "none":
        return ("none", 0, "")
    if kind in {"vote", "status"}:
        return ("unpriced_kind", 0, "")
    if kind != "count":
        return ("parse_fail", 0, "") if _PROBABILITY_KEYS & set(obj) else None
    count = _count_value(obj.get("value"))
    fact_url = str(obj.get("url") or "").strip()
    if count is None or not fact_url:
        return ("unpriced_contract", 0, "")
    return ("count", count, fact_url)


def _last_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    last: dict[str, Any] | None = None
    index = 0
    while index < len(text):
        pos = text.find("{", index)
        if pos < 0:
            break
        try:
            obj, _end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            last = obj
        index = pos + 1
    if last is None:
        raise ValueError("no json object")
    return last


def _count_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    text = str(value).strip().replace(",", "")
    if not text.isdigit():
        return None
    return int(text)


def _priced_count_evidence(
    market: Any,
    *,
    count: int,
    fact_url: str,
    bad_urls: tuple[str, ...],
    now: datetime | None,
) -> Any | None:
    if not _path_specific_https(fact_url) or fact_url in bad_urls:
        return None
    query = _market_query(market)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    contract = parse_truth_social_count_contract(query)
    if contract is not None:
        return _truth_social_evidence(contract, count, fact_url, clock)
    from analysis.research_gate import _white_house_action_count_spec_from_text

    spec = _white_house_action_count_spec_from_text(query)
    if spec is None:
        return None
    return _white_house_evidence(spec, count, fact_url, clock)


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


def _white_house_evidence(spec: Any, count: int, fact_url: str, now: datetime) -> Any | None:
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    start_et = datetime.combine(spec.start_date, datetime.min.time(), tzinfo=et)
    current_et = now.astimezone(et)
    elapsed_days = (current_et - start_et).total_seconds() / 86400.0
    remaining_days = (
        spec.cutoff_at.astimezone(et) - current_et
    ).total_seconds() / 86400.0
    forecast = remaining_threshold_probability(
        count=count,
        threshold=spec.threshold,
        elapsed_days=elapsed_days,
        remaining_days=remaining_days,
    )
    if forecast is None:
        return None
    probability, state, confidence = forecast
    return _range_evidence(
        metric_name="white_house_action_range_probability",
        probability=probability,
        confidence=confidence,
        fact_url=fact_url,
        title=f"Named White House action count {count}",
        snippet=(
            f"Named official count is {count} versus threshold {spec.threshold} "
            f"({state}). Range calculator YES probability {probability:.3f}. "
            f"Source {fact_url}."
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


def _settlement_urls(market: Any) -> list[str]:
    found: list[str] = []
    for source in getattr(market, "settlement_sources", ()) or ():
        if isinstance(source, str):
            url = source
        elif isinstance(source, dict):
            url = str(source.get("url") or "")
        else:
            url = str(getattr(source, "url", "") or "")
        url = url.strip()
        if url and url not in found:
            found.append(url)
    return found


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
