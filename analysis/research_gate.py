"""Real web-research gate for ambiguous prediction-market signals.

The gate is intentionally structured-data first. LLMs may synthesize the
evidence later, but the money path needs auditable queries, sources, snippets,
and a deterministic verdict before a neutral/no-keyword row can become a trade.
"""
from __future__ import annotations

import asyncio
import csv
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from email.utils import parsedate_to_datetime
import hashlib
import html
import io
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
import weakref
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

import aiohttp

from analysis.generic_search_circuit import (
    GenericSearchCircuit,
    GenericSearchCircuitEvent,
    GenericSearchUnavailable,
    generic_search_circuit_event_record,
)
from analysis.research_timeout_replay import (
    ResearchTimeoutReplaySnapshot,
    capture_timeout_replay_snapshot,
)
from config import cfg
from utils.bounded_https import (
    BoundedHTTPSAttemptTelemetry,
    _validated_global_ipv4_addresses as _shared_validated_global_ipv4_addresses,
    fetch_bounded_https_dual_stack,
)
from utils.logger import get_logger
from utils.research_gaps import research_gap_query_intent, research_questions_for_skip
from utils.research_evidence_quality import (
    MIN_COUNTER_EVIDENCE_CONFIDENCE,
    MIN_DIRECTIONAL_SUPPORT_CONFIDENCE,
    ContractRelevanceSpec,
    build_contract_relevance_spec,
    evidence_is_relevant_to_contract,
    has_reliable_research_source_path,
    research_evidence_temporally_valid,
    research_source_key,
)


log = get_logger("research_gate")
_GENERIC_SEARCH_CIRCUIT: GenericSearchCircuit | None = None
_GENERIC_SEARCH_CIRCUIT_EVENT_COLLECTOR: ContextVar[
    list[GenericSearchCircuitEvent] | None
] = ContextVar("generic_search_circuit_event_collector", default=None)
_GENERIC_WEB_SEARCH_MAX_CONCURRENCY = 4
_GENERIC_WEB_SEARCH_SLOW_ADMISSION_MS = 100
_GENERIC_SEARCH_TRANSPORT_PROVIDER_LABELS = {
    "Google News RSS": "google_news_rss",
    "DuckDuckGo Lite": "duckduckgo_lite",
}
_GENERIC_SEARCH_FAILURE_CLASS_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_.]*(?::(?:[A-Za-z_][A-Za-z0-9_.]*|\d{3}))?"
)


@dataclass(frozen=True)
class _GenericWebSearchWorkSnapshot:
    active: int
    peak: int


class _GenericWebSearchWorkLimiter:
    def __init__(self) -> None:
        self._semaphore = asyncio.BoundedSemaphore(
            _GENERIC_WEB_SEARCH_MAX_CONCURRENCY
        )
        self.active = 0
        self.peak = 0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self._semaphore.acquire()
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            yield
        finally:
            self.active -= 1
            self._semaphore.release()


_GENERIC_WEB_SEARCH_WORK_LIMITERS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    _GenericWebSearchWorkLimiter,
] = weakref.WeakKeyDictionary()
_GENERIC_WEB_SEARCH_WORK_LIMITERS_LOCK = threading.Lock()


def _get_generic_web_search_work_limiter() -> _GenericWebSearchWorkLimiter:
    loop = asyncio.get_running_loop()
    with _GENERIC_WEB_SEARCH_WORK_LIMITERS_LOCK:
        limiter = _GENERIC_WEB_SEARCH_WORK_LIMITERS.get(loop)
        if limiter is None:
            limiter = _GenericWebSearchWorkLimiter()
            _GENERIC_WEB_SEARCH_WORK_LIMITERS[loop] = limiter
        return limiter


def _generic_web_search_work_snapshot_for_tests() -> _GenericWebSearchWorkSnapshot:
    loop = asyncio.get_running_loop()
    with _GENERIC_WEB_SEARCH_WORK_LIMITERS_LOCK:
        limiter = _GENERIC_WEB_SEARCH_WORK_LIMITERS.get(loop)
        if limiter is None:
            return _GenericWebSearchWorkSnapshot(active=0, peak=0)
        return _GenericWebSearchWorkSnapshot(
            active=limiter.active,
            peak=limiter.peak,
        )


def _reset_generic_web_search_work_limiters_for_tests() -> None:
    with _GENERIC_WEB_SEARCH_WORK_LIMITERS_LOCK:
        if any(
            limiter.active
            for limiter in _GENERIC_WEB_SEARCH_WORK_LIMITERS.values()
        ):
            raise RuntimeError("cannot reset active generic web search work limiters")
        _GENERIC_WEB_SEARCH_WORK_LIMITERS.clear()


def _log_generic_search_transport_event(event: BoundedHTTPSAttemptTelemetry) -> None:
    provider = _GENERIC_SEARCH_TRANSPORT_PROVIDER_LABELS.get(
        event.provider_name,
        "other",
    )
    if event.outcome in {"timeout", "error"}:
        log_method = log.warning
    elif event.outcome == "success" and (
        event.admission_wait_ms < _GENERIC_WEB_SEARCH_SLOW_ADMISSION_MS
    ):
        log_method = log.debug
    else:
        log_method = log.info
    log_method(
        "[GENERIC_SEARCH_TRANSPORT] provider=%s outcome=%s terminal_stage=%s "
        "budget_ms=%d total_ms=%d admission_wait_ms=%d dns_ms=%s "
        "response_headers_ms=%s body_read_ms=%s http_status=%s bytes_read=%s "
        "error_class=%s limiter_capacity=%d",
        provider,
        event.outcome,
        event.terminal_stage,
        event.budget_ms,
        event.total_ms,
        event.admission_wait_ms,
        event.dns_ms,
        event.response_headers_ms,
        event.body_read_ms,
        event.http_status,
        event.bytes_read,
        event.error_class,
        _GENERIC_WEB_SEARCH_MAX_CONCURRENCY,
    )


def _log_generic_search_circuit_event(event: GenericSearchCircuitEvent) -> None:
    collector = _GENERIC_SEARCH_CIRCUIT_EVENT_COLLECTOR.get()
    if collector is not None:
        collector.append(event)
    log.info("%s", generic_search_circuit_event_record(event))
    if event.kind in {"open", "would_open"}:
        log.warning(
            "generic_search_circuit kind=%s mode=%s state=%s generation=%d "
            "failure_classes=%s cooldown_seconds=%.3f "
            "remaining_cooldown_seconds=%.3f",
            event.kind,
            event.mode,
            event.state,
            event.generation,
            ",".join(event.failure_classes),
            event.cooldown_seconds,
            event.remaining_cooldown_seconds,
        )


def _get_generic_search_circuit() -> GenericSearchCircuit:
    global _GENERIC_SEARCH_CIRCUIT
    if _GENERIC_SEARCH_CIRCUIT is None:
        _GENERIC_SEARCH_CIRCUIT = GenericSearchCircuit(
            mode=cfg.generic_search_circuit_mode,
            telemetry_sink=_log_generic_search_circuit_event,
        )
    return _GENERIC_SEARCH_CIRCUIT


def _reset_generic_search_circuit_for_tests() -> None:
    global _GENERIC_SEARCH_CIRCUIT
    _GENERIC_SEARCH_CIRCUIT = None


def _research_provider_error_attributions(
    errors: Iterable[BaseException],
    *,
    timeout_stage: str | None = None,
) -> tuple[str, ...]:
    """Return stable, non-sensitive failure labels for durable telemetry."""
    attributions: list[str] = []
    for error in errors:
        if isinstance(error, GenericSearchUnavailable):
            attribution = "generic_search_unavailable"
        elif isinstance(error, TimeoutError):
            attribution = "timeout"
        else:
            attribution = "provider_exception"
        if attribution not in attributions:
            attributions.append(attribution)
    if timeout_stage in {"provider_fanout", "counter_query"}:
        if "timeout" not in attributions:
            attributions.append("timeout")
    return tuple(attributions)


def _generic_search_circuit_diagnostics(
    events: Iterable[GenericSearchCircuitEvent],
) -> tuple[str | None, tuple[str, ...], int, int]:
    """Summarize only circuit events emitted in this research-gate task."""
    observed_events = tuple(events)
    if not observed_events:
        return None, (), 0, 0

    state = observed_events[-1].state
    state_value = state if state in {"closed", "open", "half_open"} else None
    failure_classes: list[str] = []
    failure_event_kinds = {
        "provider_error",
        "double_availability_failure",
        "open",
        "would_open",
        "blocked",
        "would_block",
        "probe_failed",
    }
    for event in observed_events:
        if event.kind not in failure_event_kinds:
            continue
        for value in event.failure_classes:
            safe_value = (
                value
                if _GENERIC_SEARCH_FAILURE_CLASS_RE.fullmatch(value)
                else "UnknownFailure"
            )
            if safe_value not in failure_classes:
                failure_classes.append(safe_value)
    return (
        state_value,
        tuple(failure_classes),
        sum(event.kind == "attempt" for event in observed_events),
        sum(event.kind == "blocked" for event in observed_events),
    )


class ResearchStatus(str, Enum):
    NEEDS_RESEARCH = "needs_research"
    RESEARCHING = "researching"
    NEEDS_COUNTER_EVIDENCE = "needs_counter_evidence"
    NEEDS_PRICE_EDGE = "needs_price_edge"
    DECISION_GRADE_CANDIDATE = "decision_grade_candidate"
    UNTRADEABLE = "untradeable"
    TRADE_CANDIDATE = "trade_candidate"
    CONTINUE_RESEARCHING = "continue_researching"
    RESEARCHED_SKIP_NO_EDGE = "researched_skip_no_edge"
    RESEARCHED_SKIP_AMBIGUOUS = "researched_skip_ambiguous"
    HARD_CAPITAL_BLOCK = "hard_capital_block"
    RESEARCH_PROVIDER_ERROR = "research_provider_error"
    RESEARCH_ADJUDICATOR_ERROR = "research_adjudicator_error"


@dataclass(frozen=True)
class PrewarmPhaseTimeouts:
    """Opt-in post-collection budgets for offline research prewarm."""

    initial_adjudication_seconds: float = 20.0
    counter_query_seconds: float = 5.0
    counter_adjudication_seconds: float = 20.0

    def __post_init__(self) -> None:
        for field_name in (
            "initial_adjudication_seconds",
            "counter_query_seconds",
            "counter_adjudication_seconds",
        ):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field_name} must be finite and positive")
            object.__setattr__(self, field_name, value)


_PREWARM_PHASE_TIMEOUTS_CAPABILITY = object()


_DECISION_EVIDENCE_CLOCK_SKEW_SECONDS = 300.0


@dataclass(frozen=True)
class ResearchQuery:
    query: str
    query_intent: str
    source_class: str


@dataclass(frozen=True)
class ResearchEvidence:
    source_class: str
    source_name: str
    source_url: str
    title: str
    snippet: str
    claim_type: str
    supports_direction: str = "neutral"
    supports_confidence: float = 0.0
    published_at: str | None = None
    retrieved_at: str | None = None
    metric_name: str | None = None
    metric_value: float | None = None
    metric_unit: str | None = None
    extraction_confidence: float | None = None
    inserted_at: str | None = None
    contract_fingerprint: str | None = None
    aggregator_url: str | None = None
    available_at: str | None = None


@dataclass(frozen=True)
class GDPThresholdContract:
    metric_name: str
    metric_unit: str
    comparator: str
    threshold: float
    target_quarter: int
    target_year: int
    contract_fingerprint: str


@dataclass(frozen=True)
class _CurrentRunGDPNowObservationContext:
    query: str
    contract_fingerprint: str
    source_url: str
    source_observation_date: str
    retrieved_at: str
    metric_name: str
    metric_value: float
    metric_unit: str
    extraction_confidence: float


@dataclass(frozen=True)
class GettyDistinctDateSpec:
    target_count: int
    start_date: date
    end_date: date
    cutoff_at: datetime


@dataclass(frozen=True)
class GettySearchSnapshot:
    end_date: date
    total_results: int
    newest_asset_date: date | None
    witness_asset_ids: tuple[str, ...]


@dataclass(frozen=True)
class WhiteHouseActionCountSpec:
    threshold: int
    start_date: date
    end_date: date
    cutoff_at: datetime


@dataclass(frozen=True)
class WhiteHouseActionCard:
    title: str
    url: str
    published_date: date


_GETTY_SNAPSHOT_CACHE_TTL_SECONDS = 120.0
_GETTY_SNAPSHOT_CACHE: dict[
    tuple[date, date],
    tuple[float, tuple[GettySearchSnapshot, ...]],
] = {}
_GETTY_SNAPSHOT_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class ResearchVerdict:
    status: ResearchStatus
    attempted: bool
    queries: list[ResearchQuery] = field(default_factory=list)
    evidence: list[ResearchEvidence] = field(default_factory=list)
    summary: str = ""
    skip_reason: str | None = None
    research_pending_origin: str | None = None
    force_side: str | None = None
    estimated_probability: float | None = None
    confidence: float | None = None
    market_price: float | None = None
    estimated_edge: float | None = None
    decision_grade_reasons: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    counterclaims: tuple[str, ...] = ()
    research_run_id: str | None = None
    research_persisted: bool | None = None
    research_persistence_error: str | None = None
    research_direct_fetch_failures: tuple[str, ...] = ()
    research_timeout_stage: str | None = None
    research_provider_error_count: int = 0
    research_provider_error_attributions: tuple[str, ...] = ()
    research_generic_search_circuit_state: str | None = None
    research_generic_search_failure_classes: tuple[str, ...] = ()
    research_generic_search_attempt_delta: int = 0
    research_generic_search_blocked_call_delta: int = 0
    research_contract_fingerprint: str | None = None

    def log_fields(self) -> dict[str, object]:
        urls = [item.source_url for item in self.evidence if item.source_url]
        contract_fingerprints = {
            item.contract_fingerprint for item in self.evidence if item.contract_fingerprint
        }
        settlement_hits = [
            item.source_url
            for item in self.evidence
            if item.source_class in {"resolution_source", "official_primary"}
        ]
        fields: dict[str, object] = {
            "research_attempted": self.attempted,
            "research_status": self.status.value,
            "research_queries": [query.query for query in self.queries],
            "research_sources_consulted": [
                item.source_name for item in self.evidence if item.source_name
            ],
            "research_hit_count": len(self.evidence),
            "research_settlement_source_hits": len(settlement_hits),
            "research_urls": urls,
            "research_summary": self.summary,
            "research_model_direction": self.force_side,
            "research_model_confidence": self.confidence,
            "research_skip_reason": self.skip_reason,
        }
        if self.research_pending_origin:
            fields["research_pending_origin"] = self.research_pending_origin
        if self.research_run_id:
            fields["research_run_id"] = self.research_run_id
        if self.research_contract_fingerprint:
            fields["research_contract_fingerprint"] = self.research_contract_fingerprint
        elif len(contract_fingerprints) == 1:
            fields["research_contract_fingerprint"] = next(iter(contract_fingerprints))
        if self.research_persisted is not None:
            fields["research_persisted"] = self.research_persisted
        if self.research_persistence_error:
            fields["research_persistence_error"] = self.research_persistence_error
        if self.research_timeout_stage:
            fields["research_timeout_stage"] = self.research_timeout_stage
        fields["research_provider_error_count"] = int(
            self.research_provider_error_count
        )
        fields["research_provider_error_attributions"] = list(
            self.research_provider_error_attributions
        )
        if self.research_generic_search_circuit_state:
            fields["research_generic_search_circuit_state"] = (
                self.research_generic_search_circuit_state
            )
        if self.research_generic_search_failure_classes:
            fields["research_generic_search_failure_classes"] = list(
                self.research_generic_search_failure_classes
            )
        if self.research_generic_search_attempt_delta:
            fields["research_generic_search_attempt_delta"] = int(
                self.research_generic_search_attempt_delta
            )
        if self.research_generic_search_blocked_call_delta:
            fields["research_generic_search_blocked_call_delta"] = int(
                self.research_generic_search_blocked_call_delta
            )
        if self.research_direct_fetch_failures:
            fields["research_direct_fetch_failures"] = list(
                self.research_direct_fetch_failures
            )
            fields["research_direct_fetch_failure_count"] = len(
                self.research_direct_fetch_failures
            )
        if self.estimated_probability is not None:
            fields["research_model_probability_yes"] = round(
                float(self.estimated_probability),
                4,
            )
        if self.market_price is not None:
            fields["research_market_price"] = round(float(self.market_price), 4)
        if self.estimated_edge is not None:
            fields["research_estimated_edge"] = round(float(self.estimated_edge), 4)
        if self.decision_grade_reasons:
            fields["research_decision_grade_reasons"] = list(self.decision_grade_reasons)
        if self.open_questions:
            fields["research_open_questions"] = list(self.open_questions)
        if self.counterclaims:
            fields["research_counterclaims"] = list(self.counterclaims)
        fields.update(_research_evidence_time_fields(self.evidence))
        return fields


SearchProvider = Callable[[ResearchQuery], Awaitable[list[ResearchEvidence]]]
DirectFetcher = Callable[[str, str, str], Awaitable[ResearchEvidence | None]]
ResearchAdjudicator = Callable[..., Awaitable[dict[str, Any] | None]]
DossierStore = Any


def _research_evidence_time_fields(evidence: list[ResearchEvidence]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for attr, min_key, max_key in (
        ("published_at", "research_min_published_at", "research_max_published_at"),
        ("retrieved_at", "research_min_retrieved_at", "research_max_retrieved_at"),
    ):
        timestamps = [
            parsed
            for item in evidence
            if (parsed := _parse_timestamp(getattr(item, attr, None))) is not None
        ]
        if timestamps:
            fields[min_key] = min(timestamps).isoformat()
            fields[max_key] = max(timestamps).isoformat()
    return fields


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _market_contract_question(market: Any) -> str | None:
    for attr in ("question", "title", "subtitle", "event_title"):
        value = _clean(getattr(market, attr, ""))
        if value:
            return value
    ticker = _clean(getattr(market, "ticker", ""))
    return ticker or None


def _domain_from_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value if "://" in value else f"https://{value}")
    return parsed.netloc.lower().removeprefix("www.")


def _query_site_domain(query: str) -> str:
    match = re.search(r"\bsite:([^\s]+)", query or "", re.I)
    return _domain_from_url(match.group(1)) if match else ""


def _source_domain(source_name: str) -> str:
    cleaned = _clean(source_name).lower()
    if cleaned in {"the white house (.gov)", "white house (.gov)"}:
        return "whitehouse.gov"
    if "." in cleaned:
        return _domain_from_url(cleaned)
    known = {
        "abc": "abcnews.go.com",
        "abc news": "abcnews.go.com",
        "associated press": "apnews.com",
        "ap": "apnews.com",
        "ap news": "apnews.com",
        "bbc": "bbc.com",
        "bbc news": "bbc.com",
        "bbc news uk": "bbc.co.uk",
        "bbc uk": "bbc.co.uk",
        "bloomberg": "bloomberg.com",
        "bloomberg news": "bloomberg.com",
        "cnn": "cnn.com",
        "cnbc": "cnbc.com",
        "eia": "eia.gov",
        "fox news": "foxnews.com",
        "guardian": "theguardian.com",
        "kalshi": "kalshi.com",
        "new york times": "nytimes.com",
        "nyt": "nytimes.com",
        "opec": "opec.org",
        "politico": "politico.com",
        "reuters": "reuters.com",
        "the hill": "thehill.com",
        "the new york times": "nytimes.com",
        "the washington post": "washingtonpost.com",
        "the white house": "whitehouse.gov",
        "the wall street journal": "wsj.com",
        "time": "time.com",
        "time magazine": "time.com",
        "wall street journal": "wsj.com",
        "washington post": "washingtonpost.com",
        "white house": "whitehouse.gov",
        "wsj": "wsj.com",
    }
    if cleaned in known:
        return known[cleaned]
    for label, domain in known.items():
        if cleaned.startswith(f"{label} - ") or cleaned.startswith(f"{label}: "):
            return domain
    return ""


_REPUTABLE_SECONDARY_DOMAINS = {
    "abcnews.go.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "bloomberg.com",
    "cnbc.com",
    "cnn.com",
    "foxnews.com",
    "nytimes.com",
    "politico.com",
    "reuters.com",
    "theguardian.com",
    "thehill.com",
    "time.com",
    "washingtonpost.com",
    "wsj.com",
}


def _domains_match(actual: str, expected: str) -> bool:
    actual = _domain_from_url(actual)
    expected = _domain_from_url(expected)
    return bool(actual and expected and (actual == expected or actual.endswith(f".{expected}")))


def _classify_evidence_source(
    query: ResearchQuery,
    source_name: str,
    source_url: str,
    *,
    allow_official_name_match: bool = True,
) -> str:
    query_site = _query_site_domain(query.query)
    url_domain = _domain_from_url(source_url)
    name_domain = _source_domain(source_name)
    if query.source_class in {"rules_source", "market_price"}:
        trusted_domains = {
            "kalshi.com",
            "kalshi-public-docs.s3.amazonaws.com",
        }
        if any(
            _domains_match(url_domain, domain) or _domains_match(name_domain, domain)
            for domain in trusted_domains
        ):
            return query.source_class
    if query.source_class in {"resolution_source", "official_primary"} and query_site:
        if _domains_match(url_domain, query_site) or (
            allow_official_name_match and _domains_match(name_domain, query_site)
        ):
            return query.source_class
    official_domains = {
        "api.eia.gov",
        "eia.gov",
        "federalreserve.gov",
        "kalshi.com",
        "opec.org",
        "whitehouse.gov",
    }
    if any(
        _domains_match(url_domain, domain)
        or (allow_official_name_match and _domains_match(name_domain, domain))
        for domain in official_domains
    ):
        return "official_primary"
    if url_domain in _REPUTABLE_SECONDARY_DOMAINS or name_domain in _REPUTABLE_SECONDARY_DOMAINS:
        return "reputable_secondary"
    if query.source_class in {"rules_source", "market_price"}:
        return "other"
    if query.source_class not in {"resolution_source", "official_primary"}:
        return query.source_class
    return "other"


def _is_settlement_evidence(item: ResearchEvidence) -> bool:
    if item.claim_type in {"contract_terms", "rules_context"}:
        return False
    if item.source_class in {"resolution_source", "official_primary"}:
        return True
    return (
        item.source_class == "reputable_secondary"
        and item.claim_type
        in {"official_resolution", "resolution_source", "resolution", "supporting"}
        and item.supports_direction in {"yes", "no"}
        and float(item.supports_confidence or 0.0)
        >= _supporting_secondary_confidence_threshold(item)
    )


def _supporting_secondary_confidence_threshold(item: ResearchEvidence) -> float:
    if item.claim_type != "supporting":
        return 0.6
    text = _clean(f"{item.title} {item.snippet}").lower()
    if (
        "passport" in text
        and "trump" in text
        and ("picture" in text or "face" in text or "image" in text)
        and (
            "issue" in text
            or "issuing" in text
            or "featuring" in text
            or "unveiled" in text
        )
    ):
        return 0.75
    return 0.8


def _market_text(market: Any) -> str:
    return " ".join(
        _clean(getattr(market, key, ""))
        for key in ("title", "subtitle", "rules_primary", "rules_secondary")
    )


def _market_rules_text(market: Any) -> str:
    return _clean(" ".join(
        _clean(getattr(market, key, ""))
        for key in ("rules_primary", "rules_secondary")
    ))


def _named_date(value: str) -> date | None:
    cleaned = _clean(value).replace("Sept ", "Sep ")
    for pattern in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    return None


def _getty_distinct_date_spec_from_text(
    text: str,
) -> GettyDistinctDateSpec | None:
    cleaned = _clean(text)
    if not _query_mentions_getty_distinct_photo_days(cleaned):
        return None
    count_match = re.search(r"\bexactly\s+(\d+)\b", cleaned, re.I)
    window_match = re.search(
        r"\bbetween\s+([A-Z][a-z]{2,8}\s+\d{1,2},\s+20\d{2})\s+"
        r"and\s+([A-Z][a-z]{2,8}\s+\d{1,2},\s+20\d{2})",
        cleaned,
        re.I,
    )
    cutoff_match = re.search(
        r"\bcutoff\s+(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2}))",
        cleaned,
        re.I,
    )
    if not count_match or not window_match or not cutoff_match:
        return None
    start_date = _named_date(window_match.group(1))
    end_date = _named_date(window_match.group(2))
    cutoff_at = _parse_timestamp(cutoff_match.group(1))
    if start_date is None or end_date is None or cutoff_at is None:
        return None
    target_count = int(count_match.group(1))
    window_days = (end_date - start_date).days + 1
    if target_count < 0 or window_days < 1 or window_days > 14:
        return None
    return GettyDistinctDateSpec(target_count, start_date, end_date, cutoff_at)


def _query_mentions_getty_distinct_photo_days(text: str) -> bool:
    lower = _clean(text).lower()
    return (
        "kxtrumpphoto" in lower
        or (
            "getty images" in lower
            and "editorial photo" in lower
            and "distinct days" in lower
            and "trump" in lower
        )
    )


def _white_house_action_count_spec_from_text(
    text: str,
) -> WhiteHouseActionCountSpec | None:
    cleaned = _clean(text)
    if not _query_mentions_white_house_action_count(cleaned):
        return None
    threshold_match = re.search(
        r"\bat least\s+(\d+)\s+presidential actions\b",
        cleaned,
        re.I,
    )
    window_match = re.search(
        r"\bfrom\s+([A-Z][a-z]{2,8}\s+\d{1,2},\s+20\d{2})\s+"
        r"through\s+([A-Z][a-z]{2,8}\s+\d{1,2},\s+20\d{2})",
        cleaned,
        re.I,
    )
    cutoff_match = re.search(
        r"\bat\s+(\d{1,2}):(\d{2})\s*(AM|PM)\s+ET\s+on\s+"
        r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+20\d{2})",
        cleaned,
        re.I,
    )
    if not threshold_match or not window_match or not cutoff_match:
        return None
    start_date = _named_date(window_match.group(1))
    end_date = _named_date(window_match.group(2))
    cutoff_date = _named_date(cutoff_match.group(4))
    if start_date is None or end_date is None or cutoff_date is None:
        return None
    hour = int(cutoff_match.group(1)) % 12
    if cutoff_match.group(3).upper() == "PM":
        hour += 12
    cutoff_at = datetime(
        cutoff_date.year,
        cutoff_date.month,
        cutoff_date.day,
        hour,
        int(cutoff_match.group(2)),
        tzinfo=ZoneInfo("America/New_York"),
    ).astimezone(timezone.utc)
    threshold = int(threshold_match.group(1))
    window_days = (end_date - start_date).days + 1
    if threshold < 1 or window_days < 1 or window_days > 31:
        return None
    return WhiteHouseActionCountSpec(threshold, start_date, end_date, cutoff_at)


def _query_mentions_white_house_action_count(text: str) -> bool:
    lower = _clean(text).lower()
    return (
        "kxtrumpact" in lower
        or (
            "presidential actions" in lower
            and "whitehouse.gov" in lower
            and "at least" in lower
        )
    )


def _query_mentions_trump_passport_image(text: str) -> bool:
    lower = _clean(text).lower()
    if "passport" not in lower or "trump" not in lower:
        return False
    return any(
        term in lower
        for term in (
            "department of state",
            "state department",
            "trump's face",
            "trump\u2019s face",
            "trump face",
            "trump's picture",
            "trump\u2019s picture",
            "trump picture",
            "visual representation",
        )
    )


def _base_rate_terms_for_market(market: Any) -> str:
    text = f"{_clean(getattr(market, 'ticker', ''))} {_market_text(market)}".lower()
    if _query_mentions_trump_passport_image(text):
        return "Trump passport picture State Department reporting frequency"
    if (
        "kxgdp" in text
        or "annualized gdp growth" in text
        or "real gdp growth" in text
        or "gross domestic product" in text
    ):
        return "GDPNow nowcast real GDP growth SAAR base rate"
    return "base rate historical frequency"


def market_has_research_source_path(market: Any) -> bool:
    if tuple(getattr(market, "settlement_sources", ()) or ()):
        return True
    for attr in ("contract_terms_url", "rules_primary", "rules_secondary"):
        value = getattr(market, attr, "")
        if isinstance(value, str) and value.strip():
            return True
    ticker = _clean(getattr(market, "ticker", ""))
    title = _clean(getattr(market, "title", ""))
    market_text = f"{ticker} {_market_text(market)}"
    if _official_source_hints(market_text, title or ticker):
        return True
    if _query_mentions_nws_high_temp(market_text) and _nws_daily_climate_url(
        market_text,
    ):
        return True
    return False


def _dedupe_queries(queries: Iterable[ResearchQuery]) -> list[ResearchQuery]:
    seen: set[str] = set()
    out: list[ResearchQuery] = []
    for query in queries:
        key = query.query.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(query)
    return out


def _evidence_identity(item: ResearchEvidence) -> str:
    base = item.aggregator_url or item.source_url or hashlib.sha256(
        f"{item.source_name}|{item.title}|{item.snippet}".encode("utf-8")
    ).hexdigest()
    if item.metric_name:
        return (
            f"{base}|{item.claim_type}|{item.metric_name}|"
            f"{item.supports_direction}"
        )
    if (
        item.claim_type in {"disconfirming", "contradiction_check"}
        and item.metric_name in _STRUCTURED_SIGNAL_METRICS
    ):
        return f"{base}|{item.claim_type}|{item.supports_direction}"
    return base


_MONTH_PATTERN = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)


def _oil_reporting_window(text: str) -> str:
    patterns = (
        rf"(?:for|during|in)\s+({_MONTH_PATTERN})\s+(20\d{{2}})",
        rf"({_MONTH_PATTERN})\s+(20\d{{2}})\s+(?:average daily )?crude oil production",
        rf"crude oil production\s+(?:for|during|in)\s+({_MONTH_PATTERN})\s+(20\d{{2}})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            month, year = match.groups()
            return f"{month.title()} {year}"
    return "June 2026"


def _query_fragment(*parts: str, limit: int = 220) -> str:
    cleaned_parts = [_clean(part) for part in parts if _clean(part)]
    if not cleaned_parts:
        return ""
    full = _clean(" ".join(cleaned_parts))
    if len(full) <= limit:
        return full
    if len(cleaned_parts) == 1:
        return full[:limit]
    suffix = cleaned_parts[-1]
    if len(suffix) >= limit:
        return suffix[:limit]
    prefix_limit = max(0, limit - len(suffix) - 1)
    prefix = _clean(" ".join(cleaned_parts[:-1]))[:prefix_limit]
    return _clean(f"{prefix} {suffix}")[:limit]


_US_ECONOMIC_SOURCE_DOMAINS = {
    "bea.gov",
    "bls.gov",
    "census.gov",
    "federalreserve.gov",
    "treasury.gov",
}


def _is_south_africa_trade_balance_market(text: str) -> bool:
    lower = _clean(text).lower()
    return (
        "kxsatradebal" in lower
        or (
            "south africa" in lower
            and ("balance of trade" in lower or "trade balance" in lower)
        )
    )


def _settlement_source_incompatible_with_market(source: Any, market_text: str) -> bool:
    domain = _domain_from_url(
        _clean(getattr(source, "url", "")) or _clean(getattr(source, "domain", ""))
    )
    if (
        _is_south_africa_trade_balance_market(market_text)
        and domain in _US_ECONOMIC_SOURCE_DOMAINS
    ):
        return True
    return False


def build_research_queries(news: Any, market: Any) -> list[ResearchQuery]:
    """Build source-classed web queries from market rules and trigger evidence."""

    title = _clean(getattr(market, "title", ""))
    headline = _clean(getattr(news, "headline", ""))
    rules = _market_text(market)
    rule_sources = _market_rules_text(market)
    combined = f"{title} {headline} {rules}"
    ticker = _clean(getattr(market, "ticker", ""))
    close_time = _clean(getattr(market, "close_time", ""))
    structured_text = _clean(f"{ticker} {combined} cutoff {close_time}")
    getty_spec = _getty_distinct_date_spec_from_text(structured_text)
    white_house_spec = _white_house_action_count_spec_from_text(structured_text)

    queries: list[ResearchQuery] = []
    if getty_spec is not None:
        queries.append(
            ResearchQuery(
                query=structured_text,
                query_intent="official_resolution",
                source_class="resolution_source",
            )
        )
    if white_house_spec is not None:
        queries.append(
            ResearchQuery(
                query=structured_text,
                query_intent="official_resolution",
                source_class="official_primary",
            )
        )
    for open_question in getattr(news, "research_open_questions", ()) or ():
        question = _clean(open_question)
        if not question:
            continue
        query_intent, source_class = research_gap_query_intent(question)
        queries.append(
            ResearchQuery(
                query=_query_fragment(ticker, title, question),
                query_intent=query_intent,
                source_class=source_class,
            )
        )
    for source in getattr(market, "settlement_sources", ()) or ():
        if _is_placeholder_settlement_source(source):
            continue
        if _settlement_source_incompatible_with_market(source, combined):
            continue
        domain = _domain_from_url(
            _clean(getattr(source, "url", "")) or _clean(getattr(source, "domain", ""))
        )
        if domain:
            queries.append(
                ResearchQuery(
                    query=f"site:{domain} {title or ticker}",
                    query_intent="resolution_source",
                    source_class="resolution_source",
                )
            )
    if getty_spec is None and white_house_spec is None:
        for domain, hint_query in _official_source_hints(
            combined,
            _query_fragment(ticker, title) or title or ticker,
        ):
            queries.append(
                ResearchQuery(
                    query=(
                        f"site:{domain} {hint_query} "
                        "official resolution current status"
                    ),
                    query_intent="official_resolution",
                    source_class="official_primary",
                )
            )
    if (
        _query_mentions_sports_event_window(f"{ticker} {title} {rules}")
        or _query_mentions_market_data_event_window(f"{ticker} {title} {rules}")
    ):
        queries.append(
            ResearchQuery(
                query=_query_fragment(
                    ticker,
                    title,
                    rules,
                    "official result event pending",
                    limit=240,
                ),
                query_intent="official_resolution",
                source_class="official_primary",
            )
        )
    has_settlement_query = any(
        query.source_class in {"resolution_source", "official_primary"} for query in queries
    )
    if not has_settlement_query:
        terms_domain = _domain_from_url(_clean(getattr(market, "contract_terms_url", "")))
        if terms_domain:
            queries.append(
                ResearchQuery(
                    query=f"site:{terms_domain} {title or ticker}",
                    query_intent="contract_terms",
                    source_class="rules_source",
                )
            )
        rules_fragment = _query_fragment(title or ticker, rule_sources, "official resolution source")
        if rules_fragment and market_has_research_source_path(market):
            queries.append(
                ResearchQuery(
                    query=rules_fragment,
                    query_intent="official_resolution_context",
                    source_class="official_primary",
                )
            )
    if headline:
        corroboration_fragment = _query_fragment(
            headline,
            title or ticker,
            "confirmation",
        )
        if corroboration_fragment:
            queries.append(
                ResearchQuery(
                    query=corroboration_fragment,
                    query_intent="corroboration",
                    source_class="reputable_secondary",
                )
            )

    if re.search(r"\bopec\b|crude oil|oil production|barrels? per day|bpd", combined, re.I):
        entity = "Iran" if re.search(r"\bIran", combined, re.I) else title
        reporting_window = _oil_reporting_window(combined)
        queries.extend(
            [
                ResearchQuery(
                    query=(
                        "site:opec.org Monthly Oil Market Report "
                        f"{entity} {reporting_window} secondary sources crude oil production"
                    ),
                    query_intent="resolution_source",
                    source_class="resolution_source",
                ),
                ResearchQuery(
                    query=f"IEA {entity} oil supply {reporting_window} exports production",
                    query_intent="reputable_secondary",
                    source_class="reputable_secondary",
                ),
                ResearchQuery(
                    query=f"EIA {entity} crude oil production {reporting_window} estimate",
                    query_intent="official_primary",
                    source_class="official_primary",
                ),
                ResearchQuery(
                    query=f"Reuters {entity} crude production {reporting_window} OPEC secondary sources",
                    query_intent="reputable_secondary",
                    source_class="reputable_secondary",
                ),
                ResearchQuery(
                    query=(
                        f"{entity} exports barrels on water production inventory drawdown "
                        f"{reporting_window}"
                    ),
                    query_intent="contradiction_check",
                    source_class="specialized_data",
                ),
            ]
        )

    if _query_mentions_trump_passport_image(f"{ticker} {combined}"):
        queries.extend(
            [
                ResearchQuery(
                    query=(
                        "Trump picture commemorative passport State Department "
                        "issued current reporting"
                    ),
                    query_intent="supporting",
                    source_class="reputable_secondary",
                ),
                ResearchQuery(
                    query=(
                        "Trump commemorative passport State Department denied "
                        "false not confirmed"
                    ),
                    query_intent="disconfirming",
                    source_class="reputable_secondary",
                ),
                ResearchQuery(
                    query=(
                        "Trump passport picture State Department commemorative "
                        "passport prior frequency"
                    ),
                    query_intent="base_rate",
                    source_class="reputable_secondary",
                ),
            ]
        )

    if headline:
        queries.append(
            ResearchQuery(
                query=f'"{headline[:140]}"',
                query_intent="trigger_article",
                source_class="trigger_source",
            )
        )
    decision_grade_seed = title or ticker
    if (
        ticker
        and _event_deadline_from_text(ticker) is not None
        and _query_mentions_confirmation_event_window(title)
    ):
        decision_grade_seed = _query_fragment(ticker, title)
    if title:
        queries.append(
            ResearchQuery(
                query=decision_grade_seed,
                query_intent="broad_context",
                source_class="reputable_secondary",
            )
        )
    if decision_grade_seed:
        counter_seed = _counter_query_seed_for_market(market, decision_grade_seed)
        existing_intents = {query.query_intent for query in queries}
        generic_required_queries = [
            ResearchQuery(
                query=_query_fragment(decision_grade_seed, "supporting evidence current"),
                query_intent="supporting",
                source_class="reputable_secondary",
            ),
            ResearchQuery(
                query=_query_fragment(
                    counter_seed,
                    (
                        "evidence against YES evidence against NO false "
                        "not confirmed denied opponent objection"
                    ),
                ),
                query_intent="disconfirming",
                source_class="reputable_secondary",
            ),
            ResearchQuery(
                query=_query_fragment(
                    decision_grade_seed,
                    _base_rate_terms_for_market(market),
                ),
                query_intent="base_rate",
                source_class="reputable_secondary",
            ),
            ResearchQuery(
                query=_query_fragment(
                    decision_grade_seed,
                    rule_sources,
                    "official resolution latest",
                ),
                query_intent="official_resolution",
                source_class="official_primary",
            ),
            ResearchQuery(
                query=_query_fragment(decision_grade_seed, rule_sources, "contract rules"),
                query_intent="rules",
                source_class="rules_source",
            ),
            ResearchQuery(
                query=_query_fragment(decision_grade_seed, "Kalshi market price bid ask"),
                query_intent="market_price",
                source_class="market_price",
            ),
            ResearchQuery(
                query=_query_fragment(decision_grade_seed, "latest update current status"),
                query_intent="staleness_check",
                source_class="reputable_secondary",
            ),
        ]
        for query in generic_required_queries:
            if (
                query.query_intent in {"supporting", "disconfirming", "base_rate"}
                and query.query_intent in existing_intents
            ):
                continue
            queries.append(query)
            existing_intents.add(query.query_intent)
    return _dedupe_queries(queries)


def _official_source_hints(text: str, fallback_query: str) -> list[tuple[str, str]]:
    lower = _clean(text).lower()
    fallback = _clean(fallback_query)
    fallback_deadline = _event_deadline_from_text(fallback)
    fallback_date_label = (
        _fed_date_label(fallback_deadline) if fallback_deadline is not None else ""
    )
    hints: list[tuple[str, str]] = []
    if (
        ("expunge" in lower or "expunges" in lower or "expunging" in lower)
        and "impeachment" in lower
        and "trump" in lower
    ):
        hints.append(
            (
                "govinfo.gov",
                fallback
                or "H.Res. 24 H.Res. 25 expunging impeachment Donald Trump",
            )
        )
    if "jurado nacional de elecciones" in lower or re.search(r"\bjne\b", lower):
        hints.append(
            (
                "jne.gob.pe",
                "JNE nulidad anulacion invalidacion elecciones presidenciales 2026",
            )
        )
    if (
        "passed the house" in lower
        or "legislation" in lower
        and ("house" in lower or "congress" in lower)
    ):
        hints.append(("congress.gov", fallback or "legislation passed House"))
    if "budget resolution" in lower and (
        "passed the senate" in lower
        or "pass the senate" in lower
        or "senate" in lower
    ):
        hints.extend(
            [
                ("congress.gov", "budget resolution passed Senate August 2026"),
                ("senate.gov", "budget resolution passed Senate August 2026"),
            ]
        )
    if "cabinet nominees" in lower and "senate" in lower and (
        "confirmed" in lower or "confirmation" in lower
    ):
        deadline = fallback_deadline or _month_end_from_text(lower)
        deadline_label = (
            _fed_date_label(deadline)
            if fallback_deadline is not None and deadline is not None
            else deadline.strftime("%B %Y") if deadline is not None else ""
        )
        hints.append(
            (
                "senate.gov",
                _clean(f"Cabinet nominees confirmed Senate {deadline_label}"),
            )
        )
    if "white house livestream" in lower and "donald trump" in lower:
        hints.append(
            (
                "whitehouse.gov",
                "Donald Trump livestream transcript remarks",
            )
        )
    if "pennsylvania defense and innovation summit" in lower:
        hints.append(
            (
                "mccormick.senate.gov",
                _clean(
                    "Pennsylvania Defense and Innovation Summit Donald Trump "
                    f"{fallback_date_label}"
                ),
            )
        )
    if "executive action" in lower or "executive order" in lower:
        hints.extend(
            [
                ("federalregister.gov", fallback or "executive action"),
                ("whitehouse.gov", fallback or "presidential actions"),
            ]
        )
    if any(term in lower for term in ("pardon", "commute", "reprieve", "clemency")):
        hints.extend(
            [
                ("justice.gov", fallback or "pardon commutation clemency"),
                ("whitehouse.gov", fallback or "pardon commutation clemency"),
            ]
        )
    if _query_mentions_truth_social_event(lower):
        hints.append(
            (
                "truthsocial.com",
                fallback or "Donald Trump Truth Social posts",
            )
        )
    if (
        "trade agreement" in lower
        or "free trade agreement" in lower
        or "trade framework" in lower
        or "trade deal" in lower
    ):
        hints.extend(
            [
                ("ustr.gov", fallback or "trade agreement announcement"),
                ("whitehouse.gov", fallback or "trade agreement announcement"),
            ]
        )
    if _is_south_africa_trade_balance_market(lower):
        hints.append(
            (
                "sars.gov.za",
                "South Africa trade statistics June 2026 trade balance",
            )
        )
    if "cbs mornings" in lower:
        hints.append(("cbsnews.com", fallback or "CBS Mornings transcript video"))
    if "criticality" in lower:
        hints.append(("nrc.gov", fallback or "reactor criticality"))
    if "federal reserve" in lower or "member of the board of governors" in lower:
        role_specific_departure = (
            re.search(
                r"\b(?:resign(?:s|ed|ing)?|departure|step(?:s|ped)? down|"
                r"leav(?:e|es|ing))\s+"
                r"(?:from\s+|as\s+)?(?:(?:his|the|a)\s+)?"
                r"(?:role|seat|position|post|office|membership|"
                r"member of (?:the )?(?:federal reserve )?board of governors|"
                r"(?:federal reserve )?board of governors)\b",
                lower,
            )
            is not None
        )
        powell_membership_query = (
            "jerome powell" in lower
            and "board of governors" in lower
            and role_specific_departure
        )
        hints.append(
            (
                "federalreserve.gov",
                (
                    _clean(
                        "Jerome Powell Board of Governors current member "
                        + (
                            f"before {fallback_date_label}"
                            if fallback_date_label
                            else ""
                        )
                    )
                    if powell_membership_query
                    else fallback or "Federal Reserve Board governor"
                ),
            )
        )
    if "bank of israel" in lower or "monetary committee" in lower:
        hints.append(("boi.org.il", fallback or "Bank of Israel monetary committee"))
    if _query_mentions_trump_passport_image(lower):
        hints.extend(
            [
                ("state.gov", "Donald Trump commemorative passport face picture"),
                ("travel.state.gov", "Donald Trump commemorative passport face picture"),
            ]
        )
    if not hints:
        return []
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for domain, hint_query in hints:
        if domain in seen:
            continue
        seen.add(domain)
        deduped.append((domain, hint_query))
    return deduped


_DECISION_GRADE_REQUIRED_QUERY_INTENTS = (
    "supporting",
    "disconfirming",
    "base_rate",
    "official_resolution",
    "rules",
    "market_price",
    "staleness_check",
)


def _select_research_queries(
    queries: list[ResearchQuery],
    *,
    max_queries: int,
    require_decision_grade: bool,
) -> list[ResearchQuery]:
    if not require_decision_grade:
        return queries[:max_queries]
    limit = max(int(max_queries), len(_DECISION_GRADE_REQUIRED_QUERY_INTENTS))
    selected = list(queries[:limit])
    selected_intents = {query.query_intent for query in selected}
    for required in _DECISION_GRADE_REQUIRED_QUERY_INTENTS:
        if required in selected_intents:
            continue
        replacement = next(
            (query for query in queries if query.query_intent == required),
            None,
        )
        if replacement is None:
            continue
        selected.append(replacement)
        selected_intents.add(required)
    if len(selected) <= limit:
        return _dedupe_queries(selected)
    required_set = set(_DECISION_GRADE_REQUIRED_QUERY_INTENTS)
    kept_required = []
    seen_required: set[str] = set()
    for query in selected:
        if query.query_intent in required_set and query.query_intent not in seen_required:
            kept_required.append(query)
            seen_required.add(query.query_intent)
    kept_other = [
        query for query in selected if query.query_intent not in required_set
    ]
    kept_domains = {_site_domain_from_query(query.query) for query in kept_required}
    kept_domains.discard("")
    extra_official = []
    seen_extra_domains: set[str] = set()
    for query in selected:
        if query.query_intent != "official_resolution":
            continue
        domain = _site_domain_from_query(query.query)
        if not domain or domain in kept_domains or domain in seen_extra_domains:
            continue
        extra_official.append(query)
        seen_extra_domains.add(domain)
    other_limit = max(0, limit - len(kept_required))
    return _dedupe_queries((extra_official + kept_other)[:other_limit] + kept_required)


def _site_domain_from_query(query: str) -> str:
    match = re.search(r"\bsite:([a-z0-9.-]+)", _clean(query).lower())
    return match.group(1) if match else ""


def _side_aware_counter_query(market: Any, side: str) -> ResearchQuery:
    side_label = str(side or "").strip().upper()
    opposite_label = "NO" if side_label == "YES" else "YES"
    seed = _clean(getattr(market, "title", "")) or _clean(getattr(market, "ticker", ""))
    seed = _counter_query_seed_for_market(market, seed)
    return ResearchQuery(
        query=_query_fragment(
            seed,
            (
                f"evidence against {side_label} {opposite_label} case false "
                "not confirmed denied opponent objection"
            ),
        ),
        query_intent="disconfirming",
        source_class="reputable_secondary",
    )


def _counter_query_seed_for_market(market: Any, fallback: str) -> str:
    text = _clean(f"{getattr(market, 'ticker', '')} {_market_text(market)}")
    if _query_mentions_leadership_replacement(text):
        return (
            "Chuck Schumer step down resign replaced Senate Democratic Leader "
            "Democratic caucus"
        )
    return fallback


def _direction_reason_conflict(direction: str | None, reason: str | None) -> bool:
    if not direction or not reason:
        return False
    text = reason.lower()
    yes_terms = (
        "increase",
        "higher",
        "raise",
        "rise",
        "surge",
        "lead to higher",
        "more likely",
        "false negative",
    )
    no_terms = (
        "decrease",
        "lower",
        "less likely",
        "does not indicate",
        "not likely",
        "unlikely",
    )
    if direction == "no" and any(term in text for term in yes_terms):
        return True
    if direction == "yes" and any(term in text for term in no_terms):
        return True
    return False


def _market_price_for_side(
    side: str | None,
    yes_ask: float | None,
    no_ask: float | None,
) -> float | None:
    if side == "yes":
        return yes_ask if _actionable_market_price(yes_ask) else None
    if side == "no":
        return no_ask if _actionable_market_price(no_ask) else None
    if _actionable_market_price(yes_ask):
        return yes_ask
    if _actionable_market_price(no_ask):
        return no_ask
    return None


def _has_quoted_market_price(*values: float | None) -> bool:
    for value in values:
        try:
            float(value)
        except (TypeError, ValueError):
            continue
        return True
    return False


def _actionable_market_price(value: float | None) -> bool:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return False
    return 0.0 < price < 1.0


def decide_research_verdict(
    *,
    evidence: list[ResearchEvidence],
    model_direction: str | None,
    model_confidence: float | None,
    model_reason: str | None,
    yes_ask: float | None,
    no_ask: float | None,
    live_mode: bool,
    queries: list[ResearchQuery] | None = None,
    estimated_probability_yes: float | None = None,
    require_decision_grade: bool = False,
    counterclaims: tuple[str, ...] = (),
    open_questions: tuple[str, ...] = (),
    contract_ticker: str = "",
) -> ResearchVerdict:
    queries = list(queries or [])
    observed_market_price = _market_price_for_side(None, yes_ask, no_ask)
    if _direction_reason_conflict(model_direction, model_reason):
        return ResearchVerdict(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Research direction conflicts with reasoning; more evidence required.",
            skip_reason="direction_reason_conflict",
            market_price=observed_market_price,
        )

    if not evidence:
        return ResearchVerdict(
            status=(
                ResearchStatus.NEEDS_RESEARCH
                if require_decision_grade
                else ResearchStatus.CONTINUE_RESEARCHING
            ),
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="No web evidence retrieved; source frontier is not exhausted.",
            skip_reason="no_research_hits",
            market_price=observed_market_price,
        )

    settlement_hits = [item for item in evidence if _is_settlement_evidence(item)]
    if not settlement_hits:
        return ResearchVerdict(
            status=(
                ResearchStatus.NEEDS_RESEARCH
                if require_decision_grade
                else ResearchStatus.CONTINUE_RESEARCHING
            ),
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Missing settlement-aligned or official evidence.",
            skip_reason="missing_resolution_source",
            market_price=observed_market_price,
        )

    if len({item.source_url for item in evidence if item.source_url}) < 2:
        return ResearchVerdict(
            status=(
                ResearchStatus.NEEDS_RESEARCH
                if require_decision_grade
                else ResearchStatus.CONTINUE_RESEARCHING
            ),
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Insufficient independent corroboration.",
            skip_reason="insufficient_corroboration",
            market_price=observed_market_price,
        )

    if require_decision_grade and observed_market_price is None:
        if _has_quoted_market_price(yes_ask, no_ask):
            return ResearchVerdict(
                status=ResearchStatus.UNTRADEABLE,
                attempted=True,
                queries=queries,
                evidence=evidence,
                summary=(
                    "Quoted market prices are present but not executable for "
                    "edge calculation."
                ),
                skip_reason="non_actionable_market_price",
                counterclaims=counterclaims,
                open_questions=open_questions,
            )
        return ResearchVerdict(
            status=ResearchStatus.NEEDS_PRICE_EDGE,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Decision-grade verifier requires an actionable market price.",
            skip_reason="missing_market_price",
            counterclaims=counterclaims,
            open_questions=open_questions,
        )

    side = model_direction if model_direction in {"yes", "no"} else None
    conf = max(0.0, min(1.0, float(model_confidence or 0.0)))
    side_market_price = _market_price_for_side(side, yes_ask, no_ask)
    if side is None or conf <= 0.0:
        directional_evidence = any(
            item.supports_direction in {"yes", "no"} for item in evidence
        )
        neutral_only = require_decision_grade and evidence and not directional_evidence
        return ResearchVerdict(
            status=(
                ResearchStatus.NEEDS_COUNTER_EVIDENCE
                if require_decision_grade
                else ResearchStatus.RESEARCHED_SKIP_AMBIGUOUS
            ),
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary=(
                "Decision-grade verifier found only neutral evidence."
                if neutral_only
                else "Research did not produce a directional probability."
            ),
            skip_reason=(
                "neutral_only_evidence" if neutral_only else "ambiguous_direction"
            ),
            counterclaims=counterclaims,
            open_questions=open_questions,
            market_price=side_market_price,
        )

    p_yes = _coerce_probability(estimated_probability_yes)
    if p_yes is None:
        return ResearchVerdict(
            status=(
                ResearchStatus.NEEDS_PRICE_EDGE
                if require_decision_grade
                else ResearchStatus.CONTINUE_RESEARCHING
            ),
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Research did not produce an explicit YES resolution probability.",
            skip_reason="missing_estimated_probability",
            counterclaims=counterclaims,
            open_questions=open_questions,
            market_price=side_market_price,
        )
    spread_buffer = 0.01
    min_edge = 0.04 if live_mode else 0.02
    asks = {"yes": yes_ask, "no": no_ask}
    edges = {
        "yes": p_yes - float(yes_ask) - spread_buffer
        if _actionable_market_price(yes_ask)
        else None,
        "no": (1.0 - p_yes) - float(no_ask) - spread_buffer
        if _actionable_market_price(no_ask)
        else None,
    }
    support_by_side = {
        candidate_side: _qualifying_directional_support(
            evidence,
            candidate_side,
            contract_ticker=contract_ticker,
            queries=queries,
        )
        for candidate_side in ("yes", "no")
    }
    qualifying_sides = [
        (candidate_side, float(edges[candidate_side]), float(asks[candidate_side]))
        for candidate_side in ("yes", "no")
        if edges[candidate_side] is not None
        and float(edges[candidate_side]) >= min_edge
        and (
            candidate_side == side
            or (
                require_decision_grade
                and bool(support_by_side[candidate_side])
            )
        )
    ]
    safe_qualifying_sides = [
        candidate
        for candidate in qualifying_sides
        if not live_mode or 0.03 < candidate[2] < 0.97
    ]
    if live_mode and qualifying_sides and not safe_qualifying_sides:
        return ResearchVerdict(
            status=ResearchStatus.HARD_CAPITAL_BLOCK,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Executable price is in live tail-risk band.",
            skip_reason="no_trade_capital_protection",
        )

    if safe_qualifying_sides:
        trade_side, trade_edge, executable_ask = max(
            safe_qualifying_sides,
            key=lambda candidate: (candidate[1], candidate[0] == side),
        )
        flipped_side = trade_side != side
        trade_confidence = conf
        trade_counterclaims = counterclaims
        trade_open_questions = open_questions
        if flipped_side:
            selected_support_confidence = max(
                float(item.supports_confidence or 0.0)
                for item in support_by_side[trade_side]
            )
            trade_confidence = min(conf, selected_support_confidence)
            trade_counterclaims = _counterclaims_for_side(evidence, trade_side)
            trade_open_questions = ()
        candidate = ResearchVerdict(
            status=ResearchStatus.TRADE_CANDIDATE,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary=(
                f"Research probability and executable {trade_side.upper()} price "
                "produce the strongest positive net edge."
            ),
            force_side=trade_side,
            estimated_probability=p_yes,
            confidence=trade_confidence,
            market_price=executable_ask,
            estimated_edge=trade_edge,
            counterclaims=trade_counterclaims,
            open_questions=trade_open_questions,
        )
        if require_decision_grade:
            return _decision_grade_verdict(
                candidate,
                model_reason=(model_reason if trade_side == side else None),
                contract_ticker=contract_ticker,
            )
        return candidate
    if require_decision_grade:
        executable_ask = asks[side]
        side_edge = edges[side]
        if not _actionable_market_price(executable_ask):
            quoted_price = _has_quoted_market_price(executable_ask)
            return ResearchVerdict(
                status=(
                    ResearchStatus.UNTRADEABLE
                    if quoted_price
                    else ResearchStatus.NEEDS_PRICE_EDGE
                ),
                attempted=True,
                queries=queries,
                evidence=evidence,
                summary=(
                    "Model-side market price is quoted but not executable."
                    if quoted_price
                    else "Decision-grade verifier requires the executable market price."
                ),
                skip_reason=(
                    "non_actionable_market_price"
                    if quoted_price
                    else "missing_market_price"
                ),
                estimated_probability=p_yes,
                confidence=conf,
            )
        return ResearchVerdict(
            status=ResearchStatus.UNTRADEABLE,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Research completed but neither side clears executable net edge.",
            skip_reason="no_edge",
            force_side=side,
            estimated_probability=p_yes,
            confidence=conf,
            market_price=executable_ask,
            estimated_edge=side_edge,
            counterclaims=counterclaims,
            open_questions=open_questions,
        )
    return ResearchVerdict(
        status=ResearchStatus.RESEARCHED_SKIP_NO_EDGE,
        attempted=True,
        queries=queries,
        evidence=evidence,
        summary="Research completed but neither side clears executable net edge.",
        skip_reason="negative_net_edge_after_costs",
    )


def _contract_relevance_spec(
    contract_ticker: str,
    queries: Sequence[ResearchQuery],
) -> ContractRelevanceSpec:
    return build_contract_relevance_spec(
        contract_ticker,
        [query.query for query in queries],
    )


def _evidence_is_relevant_to_spec(
    item: ResearchEvidence,
    spec: ContractRelevanceSpec,
) -> bool:
    return evidence_is_relevant_to_contract(
        f"{item.title} {item.snippet}",
        spec,
    )


def _decision_grade_verdict(
    candidate: ResearchVerdict,
    *,
    model_reason: str | None,
    contract_ticker: str = "",
) -> ResearchVerdict:
    if not _has_reliable_non_pending_source_path(candidate.evidence):
        return _decision_grade_block(
            candidate,
            "no_reliable_source_path",
            (
                "Decision-grade verifier requires a reliable independent "
                "source path outside pending settlement context."
            ),
        )
    raw_directions = {
        item.supports_direction
        for item in candidate.evidence
        if item.supports_direction in {"yes", "no"}
    }
    relevance_spec = _contract_relevance_spec(contract_ticker, candidate.queries)
    directions = {
        item.supports_direction
        for item in candidate.evidence
        if item.supports_direction in {"yes", "no"}
        and _is_decision_directional_support(item)
        and float(item.supports_confidence or 0.0)
        >= MIN_DIRECTIONAL_SUPPORT_CONFIDENCE
        and (
            not relevance_spec.detected
            or _evidence_is_relevant_to_spec(item, relevance_spec)
        )
    }
    if not raw_directions:
        return _decision_grade_block(
            candidate,
            "neutral_only_evidence",
            "Decision-grade verifier found only neutral evidence.",
        )
    if candidate.force_side not in directions:
        return _decision_grade_block(
            candidate,
            "missing_directional_support",
            "Decision-grade verifier found no directional support for the selected side.",
        )
    if not _has_counter_evidence(
        candidate.queries,
        candidate.evidence,
        candidate.force_side,
        contract_ticker=contract_ticker,
    ):
        return _decision_grade_block(
            candidate,
            "missing_counter_evidence",
            "Decision-grade verifier requires an explicit disconfirming search result.",
        )
    if _has_unresolved_contradiction(candidate.evidence):
        return _decision_grade_block(
            candidate,
            "unresolved_contradiction",
            "Decision-grade verifier found unresolved contradiction.",
        )
    if _has_stale_evidence(candidate.evidence):
        return _decision_grade_block(
            candidate,
            "source_freshness_ttl_exceeded",
            "Decision-grade verifier found stale evidence.",
        )
    summary = str(model_reason or candidate.summary)
    if _generic_research_reason(summary):
        summary = _structured_decision_grade_reason(candidate)
    if _generic_research_reason(summary):
        return _decision_grade_block(
            candidate,
            "generic_summary",
            _query_fragment(
                "Decision-grade verifier rejected generic reasoning.",
                str(model_reason or ""),
                limit=500,
            ),
        )
    reasons = (
        "price_present",
        "edge_recomputed",
        "counter_evidence_present",
        "fresh_sources_present",
        "specific_reasoning_present",
    )
    return replace(
        candidate,
        status=ResearchStatus.DECISION_GRADE_CANDIDATE,
        summary=summary,
        skip_reason=None,
        decision_grade_reasons=reasons,
        open_questions=candidate.open_questions or _extract_open_questions(summary),
        counterclaims=candidate.counterclaims or _extract_counterclaims(candidate.evidence),
    )


def _is_decision_directional_support(item: ResearchEvidence) -> bool:
    return _is_settlement_evidence(item) or (
        (
            not _is_gdpnow_structured_evidence(item)
            or item.supports_direction in {"yes", "no"}
        )
        and _is_structured_signal_metric(item)
        and item.claim_type in {"base_rate", "official_resolution", "settlement_source"}
    )


def _qualifying_directional_support(
    evidence: list[ResearchEvidence],
    side: str,
    *,
    contract_ticker: str,
    queries: Sequence[ResearchQuery],
) -> list[ResearchEvidence]:
    relevance_spec = _contract_relevance_spec(contract_ticker, queries)
    return [
        item
        for item in evidence
        if item.supports_direction == side
        and float(item.supports_confidence or 0.0)
        >= MIN_DIRECTIONAL_SUPPORT_CONFIDENCE
        and _is_decision_directional_support(item)
        and not _is_official_data_pending_evidence(item)
        and _is_fresh_decision_evidence(item)
        and (
            not relevance_spec.detected
            or _evidence_is_relevant_to_spec(item, relevance_spec)
        )
    ]


def _counterclaims_for_side(
    evidence: list[ResearchEvidence],
    side: str,
) -> tuple[str, ...]:
    opposite = "no" if side == "yes" else "yes"
    candidates = sorted(
        (
            item
            for item in evidence
            if item.supports_direction == opposite
            and not _is_official_data_pending_evidence(item)
            and _is_fresh_decision_evidence(item)
            and (
                item.claim_type
                in {"contradiction", "disconfirming", "contradiction_check"}
                or _is_decision_directional_support(item)
            )
        ),
        key=lambda item: float(item.supports_confidence or 0.0),
        reverse=True,
    )
    claims = [_clean(item.snippet or item.title)[:240] for item in candidates]
    return tuple(dict.fromkeys(claim for claim in claims if claim))


def _structured_decision_grade_reason(candidate: ResearchVerdict) -> str:
    side = (candidate.force_side or "").upper()
    if side not in {"YES", "NO"}:
        return ""
    support = _strongest_evidence_for_side(candidate.evidence, side.lower())
    counter = _strongest_counter_evidence(candidate.evidence, side.lower())
    if support is None or counter is None:
        return ""
    price = _format_probability(candidate.market_price)
    probability = _format_probability(
        _side_probability(side.lower(), candidate.estimated_probability)
    )
    edge = _format_probability(candidate.estimated_edge)
    support_text = _evidence_phrase(support)
    counter_text = (
        _clean(candidate.counterclaims[0])
        if candidate.counterclaims
        else _evidence_phrase(counter)
    )
    return _query_fragment(
        f"Trade {side} because {support_text}",
        (
            f"Market {side} ask is {price} versus estimated {side} probability "
            f"{probability}, leaving {edge} edge after costs."
        ),
        f"Counter evidence is {counter_text}.",
        (
            "This would prove wrong if the counter evidence becomes the current "
            "settlement path or a fresher independent source contradicts the "
            "supporting claim."
        ),
        limit=900,
    )


def _side_probability(side: str, p_yes: float | None) -> float | None:
    if p_yes is None:
        return None
    if side == "no":
        return 1.0 - float(p_yes)
    return float(p_yes)


def _strongest_evidence_for_side(
    evidence: list[ResearchEvidence],
    side: str,
) -> ResearchEvidence | None:
    candidates = [
        item
        for item in evidence
        if item.supports_direction == side
        and (
            item.claim_type
            in {
                "official_resolution",
                "resolution_source",
                "resolution",
                "supporting",
                "corroboration",
            }
            or (
                item.claim_type == "base_rate"
                and _is_structured_signal_metric(item)
            )
        )
    ]
    return max(candidates, key=lambda item: float(item.supports_confidence or 0.0), default=None)


def _strongest_counter_evidence(
    evidence: list[ResearchEvidence],
    side: str,
) -> ResearchEvidence | None:
    opposite = "no" if side == "yes" else "yes"
    candidates = [
        item
        for item in evidence
        if item.claim_type in {"contradiction", "disconfirming", "contradiction_check"}
        and item.supports_direction == opposite
    ]
    if candidates:
        return max(candidates, key=lambda item: float(item.supports_confidence or 0.0))
    neutral = [
        item
        for item in evidence
        if item.claim_type in {"contradiction", "disconfirming", "contradiction_check"}
        and item.supports_direction == "neutral"
    ]
    return max(neutral, key=lambda item: float(item.supports_confidence or 0.0), default=None)


def _evidence_phrase(item: ResearchEvidence) -> str:
    source = _clean(item.source_name) or _domain_from_url(item.source_url) or "source"
    text = _clean(item.snippet or item.title)
    if not text:
        text = _clean(item.title) or "the cited evidence"
    return f"{source}: {text[:220]}"


def _decision_grade_block(
    verdict: ResearchVerdict,
    skip_reason: str,
    summary: str,
) -> ResearchVerdict:
    status = (
        ResearchStatus.NEEDS_RESEARCH
        if skip_reason == "no_reliable_source_path"
        else ResearchStatus.NEEDS_COUNTER_EVIDENCE
    )
    return replace(
        verdict,
        status=status,
        summary=summary,
        skip_reason=skip_reason,
        force_side=None,
    )


def _has_counter_evidence(
    queries: list[ResearchQuery],
    evidence: list[ResearchEvidence],
    side: str | None,
    *,
    contract_ticker: str = "",
) -> bool:
    if side not in {"yes", "no"}:
        return False
    opposite = "no" if side == "yes" else "yes"
    has_query = any(
        query.query_intent in {"disconfirming", "contradiction_check"}
        for query in queries
    ) or any(
        item.claim_type
        in {"contradiction", "disconfirming", "contradiction_check"}
        for item in evidence
    ) or any(_is_gdpnow_derived_countercheck(item) for item in evidence)
    relevance_spec = _contract_relevance_spec(contract_ticker, queries)
    relevant_evidence = [
        item
        for item in evidence
        if not relevance_spec.speech.detected
        or _evidence_is_relevant_to_spec(item, relevance_spec)
    ]
    has_result = any(
        _directional_counter_result_is_relevant(
            item,
            relevant_evidence,
            side,
        )
        for item in relevant_evidence
        if item.claim_type
        in {"contradiction", "disconfirming", "contradiction_check"}
        and item.supports_direction == opposite
    )
    if not has_result:
        has_same_side_structured_countercheck = any(
            _structured_official_same_side_countercheck_is_relevant(
                item,
                relevant_evidence,
                side,
            )
            for item in relevant_evidence
            if item.claim_type in {"contradiction", "disconfirming", "contradiction_check"}
            and item.supports_direction == side
        )
        has_neutral_counter_result = any(
            _neutral_counter_result_is_relevant(item, relevant_evidence, side)
            for item in relevant_evidence
            if item.claim_type in {"contradiction", "disconfirming", "contradiction_check"}
            and item.supports_direction == "neutral"
        )
        has_structured_official_countercheck = any(
            _structured_official_neutral_countercheck_is_relevant(
                item,
                relevant_evidence,
                side,
            )
            for item in relevant_evidence
            if item.claim_type in {"contradiction", "disconfirming", "contradiction_check"}
            and item.supports_direction == "neutral"
        )
        has_result = (
            has_same_side_structured_countercheck
            or has_neutral_counter_result
            and (
                _has_two_strong_settlement_sources(relevant_evidence, side)
                or has_structured_official_countercheck
            )
        )
    return has_query and has_result


def _directional_counter_result_is_relevant(
    item: ResearchEvidence,
    evidence: list[ResearchEvidence],
    side: str,
) -> bool:
    if float(item.supports_confidence or 0.0) < MIN_COUNTER_EVIDENCE_CONFIDENCE:
        return False
    counter_tokens = _counter_relevance_tokens(item)
    support_tokens: set[str] = set()
    for support in evidence:
        if (
            support.supports_direction == side
            and float(support.supports_confidence or 0.0)
            >= MIN_DIRECTIONAL_SUPPORT_CONFIDENCE
            and (
                _is_decision_directional_support(support)
                or support.claim_type in {"supporting", "corroboration"}
            )
        ):
            support_tokens.update(_counter_relevance_tokens(support))
    return bool(counter_tokens & support_tokens)


_COUNTER_RELEVANCE_STOPWORDS = {
    "about",
    "after",
    "against",
    "before",
    "because",
    "between",
    "could",
    "current",
    "deadline",
    "direct",
    "evidence",
    "found",
    "latest",
    "market",
    "other",
    "reports",
    "search",
    "source",
    "still",
    "support",
    "would",
}


_COUNTER_REFRESH_SKIP_REASONS = {
    "ambiguous_direction",
    "missing_counter_evidence",
    "neutral_only_evidence",
    "unresolved_contradiction",
}


def _cached_dossier_needs_counter_refresh(cached_dossier: Any | None) -> bool:
    return (
        cached_dossier is not None
        and _clean(getattr(cached_dossier, "last_skip_reason", ""))
        in _COUNTER_REFRESH_SKIP_REASONS
    )


def _neutral_counter_result_is_relevant(
    item: ResearchEvidence,
    evidence: list[ResearchEvidence],
    side: str,
) -> bool:
    counter_tokens = _counter_relevance_tokens(item)
    if not counter_tokens:
        return False
    support_tokens: set[str] = set()
    for support in evidence:
        if (
            support.supports_direction == side
            and float(support.supports_confidence or 0.0) >= 0.75
            and (
                _is_settlement_evidence(support)
                or support.claim_type in {"supporting", "corroboration"}
            )
        ):
            support_tokens.update(_counter_relevance_tokens(support))
    overlap = counter_tokens & support_tokens
    text = _clean(f"{item.title} {item.snippet}").lower()
    explicit_no_contradiction = (
        "no direct contradiction" in text
        or "does not dispute" in text
        or "no contrary" in text
        or "no contradiction" in text
        or "stop short" in text
        or "stopped short" in text
        or "did not ask" in text
        or "did not call" in text
        or "has not asked" in text
        or "has not called" in text
    )
    return explicit_no_contradiction and len(overlap) >= 1


def _structured_official_neutral_countercheck_is_relevant(
    item: ResearchEvidence,
    evidence: list[ResearchEvidence],
    side: str,
) -> bool:
    if not _neutral_counter_result_is_relevant(item, evidence, side):
        return False
    if not _is_structured_signal_metric(item):
        return False
    if item.source_class not in {"official_primary", "resolution_source"}:
        return False
    if float(item.supports_confidence or 0.0) < 0.65:
        return False
    extraction_confidence = (
        float(item.extraction_confidence)
        if item.extraction_confidence is not None
        else 0.0
    )
    if item.metric_value is None and extraction_confidence < 0.8:
        return False
    return any(
        support.supports_direction == side
        and _is_structured_signal_metric(support)
        and support.metric_name == item.metric_name
        and _is_settlement_evidence(support)
        and float(support.supports_confidence or 0.0) >= 0.8
        for support in evidence
    )


def _structured_official_same_side_countercheck_is_relevant(
    item: ResearchEvidence,
    evidence: list[ResearchEvidence],
    side: str,
) -> bool:
    if item.supports_direction != side:
        return False
    if not _is_structured_signal_metric(item):
        return False
    if item.source_class not in {"official_primary", "resolution_source"}:
        return False
    if float(item.supports_confidence or 0.0) < 0.8:
        return False
    extraction_confidence = (
        float(item.extraction_confidence)
        if item.extraction_confidence is not None
        else 0.0
    )
    if item.metric_value is None and extraction_confidence < 0.8:
        return False
    return any(
        support is not item
        and support.supports_direction == side
        and _is_structured_signal_metric(support)
        and support.metric_name == item.metric_name
        and _is_settlement_evidence(support)
        and float(support.supports_confidence or 0.0) >= 0.8
        for support in evidence
    )


def _counter_relevance_tokens(item: ResearchEvidence) -> set[str]:
    text = _clean(f"{item.title} {item.snippet}").lower()
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9]{4,}", text):
        if token in _COUNTER_RELEVANCE_STOPWORDS:
            continue
        if token.endswith("s") and len(token) > 5:
            token = token[:-1]
        tokens.add(token)
    return tokens


def _has_two_strong_settlement_sources(
    evidence: list[ResearchEvidence],
    side: str,
) -> bool:
    source_keys = {
        _independent_evidence_key(item)
        for item in evidence
        if item.supports_direction == side
        and float(item.supports_confidence or 0.0) >= 0.8
        and _is_settlement_evidence(item)
        and _independent_evidence_key(item)
    }
    return len(source_keys) >= 2


def _has_unresolved_contradiction(evidence: list[ResearchEvidence]) -> bool:
    yes = max(
        (item.supports_confidence for item in evidence if item.supports_direction == "yes"),
        default=0.0,
    )
    no = max(
        (item.supports_confidence for item in evidence if item.supports_direction == "no"),
        default=0.0,
    )
    return yes >= 0.65 and no >= 0.65


def _has_stale_evidence(evidence: list[ResearchEvidence]) -> bool:
    critical = [
        item
        for item in evidence
        if _is_settlement_evidence(item)
        or item.claim_type in {"disconfirming", "contradiction_check"}
        or (
            item.supports_direction in {"yes", "no"}
            and float(item.supports_confidence or 0.0) >= 0.6
        )
    ]
    return any(not _is_fresh_decision_evidence(item) for item in critical)


def _is_fresh_decision_evidence(
    evidence: ResearchEvidence,
    *,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    if not research_evidence_temporally_valid(evidence, as_of=now):
        return False
    max_age_seconds = _decision_evidence_max_age_seconds(evidence)
    if evidence.retrieved_at:
        parsed = _parse_timestamp(evidence.retrieved_at)
        return parsed is not None and _timestamp_is_fresh(
            parsed,
            now=now,
            max_age_seconds=max_age_seconds,
        )
    for value in (evidence.published_at, evidence.inserted_at):
        parsed = _parse_timestamp(value)
        if parsed is None:
            continue
        if _timestamp_is_fresh(parsed, now=now, max_age_seconds=max_age_seconds):
            return True
    return False


def _decision_evidence_max_age_seconds(evidence: ResearchEvidence) -> int:
    metric = _clean(evidence.metric_name).lower()
    claim_type = _clean(evidence.claim_type).lower()
    source_class = _clean(evidence.source_class).lower()
    if metric in _TIME_SENSITIVE_METRICS or claim_type in _TIME_SENSITIVE_CLAIM_TYPES:
        return _TIME_SENSITIVE_MAX_AGE_SECONDS
    if claim_type in _DURABLE_CLAIM_TYPES or source_class == "rules_source":
        return _DURABLE_MAX_AGE_SECONDS
    if _is_settlement_evidence(evidence) and not metric:
        return _DURABLE_MAX_AGE_SECONDS
    return _DOSSIER_MAX_AGE_SECONDS


def _generic_research_reason(reason: str | None) -> bool:
    text = _clean(reason).lower()
    if len(text) < 80:
        return True
    generic = {
        "research supports yes and executable net edge clears threshold.",
        "research supports no and executable net edge clears threshold.",
        "prewarmed evidence clears edge.",
        "research supports yes.",
        "research supports no.",
    }
    if text in generic:
        return True
    return not (
        ("why" in text or "because" in text)
        and "market" in text
        and "edge" in text
        and "counter" in text
    )


def _extract_counterclaims(evidence: list[ResearchEvidence]) -> tuple[str, ...]:
    claims = [
        _clean(item.snippet or item.title)[:240]
        for item in evidence
        if item.claim_type in {"disconfirming", "contradiction_check"}
    ]
    return tuple(claim for claim in claims if claim)


def _extract_open_questions(reason: str | None) -> tuple[str, ...]:
    text = _clean(reason)
    if "prove wrong" not in text.lower():
        return ()
    fragment = text.split("prove wrong", 1)[-1].strip(" .:")
    return (fragment[:240],) if fragment else ()


def _structured_indicator_signal(
    evidence: list[ResearchEvidence],
    market: Any,
) -> dict[str, Any] | None:
    market_text = " ".join(
        (
            _clean(getattr(market, "ticker", "")),
            _market_text(market),
        )
    )
    if _getty_distinct_date_spec_from_text(
        f"{market_text} cutoff {_clean(getattr(market, 'close_time', ''))}"
    ) is not None:
        signal = _structured_count_signal(
            evidence,
            "getty_trump_distinct_photo_days",
            "Getty distinct Trump-photo days",
        )
        if signal is not None:
            return signal
    if _white_house_action_count_spec_from_text(market_text) is not None:
        signal = _structured_count_signal(
            evidence,
            "white_house_presidential_actions_count",
            "White House presidential actions",
        )
        if signal is not None:
            return signal
    weather_range = _weather_high_range_from_text(market_text)
    if weather_range is not None:
        weather_signal = _structured_weather_high_signal(evidence, weather_range)
        if weather_signal is not None:
            return weather_signal
    threshold = _gdp_threshold_from_text(market_text)
    if threshold is None:
        cpi_threshold = _cpi_threshold_from_text(market_text)
        if cpi_threshold is not None:
            return _structured_cpi_signal(evidence, cpi_threshold)
        return None
    gdp_signal = _structured_gdpnow_signal(evidence, threshold)
    if gdp_signal is not None:
        return gdp_signal
    cpi_threshold = _cpi_threshold_from_text(market_text)
    if cpi_threshold is not None:
        return _structured_cpi_signal(evidence, cpi_threshold)
    return None


def _deterministic_decision_signal(
    evidence: list[ResearchEvidence],
    market: Any,
    *,
    allow_evidence_signal: bool,
) -> dict[str, Any] | None:
    structured_signal = _structured_indicator_signal(evidence, market)
    if structured_signal:
        structured_direction = str(structured_signal["direction"])
        has_conflicting_directional_settlement = any(
            _is_settlement_evidence(item)
            and not _is_structured_signal_metric(item)
            and item.supports_direction in {"yes", "no"}
            and item.supports_direction != structured_direction
            and float(item.supports_confidence or 0.0) >= 0.6
            for item in evidence
        )
        if not has_conflicting_directional_settlement:
            return structured_signal
    if allow_evidence_signal:
        return _evidence_direction_signal(evidence, market)
    return None


def _evidence_direction_signal(
    evidence: list[ResearchEvidence],
    market: Any,
) -> dict[str, Any] | None:
    candidates: list[tuple[str, list[ResearchEvidence], float]] = []
    for side in ("yes", "no"):
        supporting = [
            item
            for item in evidence
            if item.supports_direction == side
            and float(item.supports_confidence or 0.0) >= 0.75
            and _is_settlement_evidence(item)
        ]
        source_keys = {
            _independent_evidence_key(item)
            for item in supporting
            if _independent_evidence_key(item)
        }
        if len(source_keys) < 2:
            continue
        opposite = "no" if side == "yes" else "yes"
        max_opposite = max(
            (
                float(item.supports_confidence or 0.0)
                for item in evidence
                if item.supports_direction == opposite
            ),
            default=0.0,
        )
        if max_opposite >= 0.75:
            continue
        has_counter_search_result = any(
            item.claim_type in {"disconfirming", "contradiction_check"}
            and item.supports_direction in {opposite, "neutral"}
            for item in evidence
        )
        if not has_counter_search_result:
            continue
        support_strength = max(float(item.supports_confidence or 0.0) for item in supporting)
        score = support_strength + min(len(source_keys), 3) * 0.03 - max_opposite * 0.05
        candidates.append((side, supporting, score))
    if not candidates:
        return None
    side, supporting, _score = max(candidates, key=lambda item: item[2])
    support_strength = max(float(item.supports_confidence or 0.0) for item in supporting)
    source_count = len({_independent_evidence_key(item) for item in supporting})
    side_probability = min(
        0.93,
        max(0.65, 0.62 + min(source_count, 3) * 0.08 + (support_strength - 0.75) * 0.2),
    )
    p_yes = side_probability if side == "yes" else 1.0 - side_probability
    support = max(supporting, key=lambda item: float(item.supports_confidence or 0.0))
    counter = _strongest_counter_evidence(evidence, side)
    counter_text = _evidence_phrase(counter) if counter is not None else "no strong contrary source"
    return {
        "direction": side,
        "estimated_probability_yes": p_yes,
        "confidence": min(0.9, support_strength),
        "reason": _query_fragment(
            f"{_evidence_phrase(support)} provides settlement-aligned {side.upper()} evidence.",
            f"{source_count} independent high-confidence sources support {side.upper()}.",
            f"Counter evidence checked: {counter_text}.",
            "A strong fresh opposite-side source would invalidate this estimate.",
            limit=900,
        ),
    }


def _independent_evidence_key(item: ResearchEvidence) -> str:
    url_domain = _domain_from_url(item.source_url)
    source_domain = _source_domain(item.source_name)
    if url_domain in {"news.google.com", "google.com"}:
        return source_domain or _clean(item.source_name)
    return url_domain or source_domain or _clean(item.source_name)


_STRUCTURED_SIGNAL_METRICS = {
    "cpi_monthly_change_single_decimal",
    "getty_trump_distinct_photo_days",
    "gdpnow_real_gdp_growth_saar",
    "nws_daily_high_temp_f",
    "white_house_presidential_actions_count",
}


def _is_structured_signal_metric(item: ResearchEvidence) -> bool:
    return item.metric_name in _STRUCTURED_SIGNAL_METRICS


def _structured_count_signal(
    evidence: list[ResearchEvidence],
    metric_name: str,
    label: str,
) -> dict[str, Any] | None:
    candidates = [
        item
        for item in evidence
        if item.metric_name == metric_name
        and item.metric_value is not None
        and item.supports_direction in {"yes", "no"}
        and float(item.supports_confidence or 0.0) >= 0.8
    ]
    if not candidates:
        return None
    item = max(candidates, key=lambda candidate: float(candidate.supports_confidence))
    direction = item.supports_direction
    probability_yes = 0.98 if direction == "yes" else 0.02
    return {
        "direction": direction,
        "estimated_probability_yes": probability_yes,
        "confidence": float(item.supports_confidence),
        "reason": _query_fragment(
            f"Trade {direction.upper()} because settlement-aligned {label} "
            f"evidence reports {item.metric_value:g}.",
            item.snippet,
            "Market edge still depends on executable price and counter-evidence.",
            limit=700,
        ),
    }


def _structured_gdpnow_signal(
    evidence: list[ResearchEvidence],
    threshold: float,
) -> dict[str, Any] | None:
    candidates = [
        item
        for item in evidence
        if item.metric_name == "gdpnow_real_gdp_growth_saar"
        and item.metric_value is not None
        and (
            not _is_gdpnow_structured_evidence(item)
            or item.supports_direction in {"yes", "no"}
        )
    ]
    if not candidates:
        return None
    item = max(
        candidates,
        key=lambda candidate: float(candidate.extraction_confidence or 0.0),
    )
    value = float(item.metric_value)
    direction = "yes" if value >= threshold else "no"
    margin = value - threshold
    p_yes = max(0.05, min(0.95, 0.50 + (margin / 5.0)))
    confidence = _gdpnow_confidence(value, threshold)
    reason = _query_fragment(
        (
            f"GDPNow nowcast is {value:.2f}% SAAR versus the market threshold "
            f"{threshold:.2f}%, so structured data supports {direction.upper()}."
        ),
        (
            "Market edge still depends on executable price and counter-evidence; "
            "GDPNow is not the official BEA settlement value."
        ),
        limit=700,
    )
    return {
        "direction": direction,
        "estimated_probability_yes": p_yes,
        "confidence": confidence,
        "reason": reason,
    }


def _structured_cpi_signal(
    evidence: list[ResearchEvidence],
    threshold: float,
) -> dict[str, Any] | None:
    candidates = [
        item
        for item in evidence
        if item.metric_name == "cpi_monthly_change_single_decimal"
        and item.metric_value is not None
    ]
    if not candidates:
        return None
    item = max(
        candidates,
        key=lambda candidate: float(candidate.extraction_confidence or 0.0),
    )
    value = float(item.metric_value)
    direction = "yes" if value > threshold else "no"
    margin = value - threshold
    p_yes = max(0.03, min(0.97, 0.50 + (margin / 1.5)))
    confidence = _cpi_confidence(value, threshold)
    reason = _query_fragment(
        (
            f"BLS CPI-U monthly change is {value:.1f}% versus the market "
            f"threshold {threshold:.1f}%, so official data supports "
            f"{direction.upper()}."
        ),
        "Market edge still depends on executable price and counter-evidence.",
        limit=700,
    )
    return {
        "direction": direction,
        "estimated_probability_yes": p_yes,
        "confidence": confidence,
        "reason": reason,
    }


def _structured_weather_high_signal(
    evidence: list[ResearchEvidence],
    target_range: tuple[float, float],
) -> dict[str, Any] | None:
    candidates = [
        item
        for item in evidence
        if item.metric_name == "nws_daily_high_temp_f"
        and item.metric_value is not None
    ]
    if not candidates:
        return None
    item = max(
        candidates,
        key=lambda candidate: float(candidate.extraction_confidence or 0.0),
    )
    value = float(item.metric_value)
    low, high = target_range
    in_range = low <= value <= high
    direction = "yes" if in_range else "no"
    p_yes = 0.95 if in_range else 0.05
    confidence = 0.95
    range_text = _weather_range_text(target_range)
    reason = _query_fragment(
        (
            f"Trade {direction.upper()} because NWS Central Park daily climate "
            f"report lists maximum temperature {value:.0f}F versus the market "
            f"range {range_text}."
        ),
        (
            "Market edge still depends on executable price; counter evidence "
            "would be an official correction or another settlement-grade NWS "
            "report with a different maximum temperature."
        ),
        limit=700,
    )
    return {
        "direction": direction,
        "estimated_probability_yes": p_yes,
        "confidence": confidence,
        "reason": reason,
    }


def _apply_structured_indicator_evidence(
    evidence: list[ResearchEvidence],
    market: Any,
) -> list[ResearchEvidence]:
    market_text = " ".join(
        (
            _clean(getattr(market, "ticker", "")),
            _market_text(market),
        )
    )
    gdp_threshold = _gdp_threshold_from_text(market_text)
    cpi_threshold = _cpi_threshold_from_text(market_text)
    weather_range = _weather_high_range_from_text(market_text)
    leadership_replacement = _query_mentions_leadership_replacement(market_text)
    office_departure = _query_mentions_office_departure(market_text)
    if (
        gdp_threshold is None
        and cpi_threshold is None
        and weather_range is None
        and not leadership_replacement
        and not office_departure
    ):
        return evidence
    out: list[ResearchEvidence] = []
    for item in evidence:
        if _is_gdpnow_structured_evidence(item) and _parse_gdp_threshold_contract(market) is not None:
            # GDPNow is observational input; the countercheck builder owns any
            # directional interpretation and must never relabel this record.
            out.append(item)
            continue
        if (
            item.metric_name == "gdpnow_real_gdp_growth_saar"
            and item.metric_value is not None
            and gdp_threshold is not None
        ):
            value = float(item.metric_value)
            direction = "yes" if value >= gdp_threshold else "no"
            out.append(
                replace(
                    item,
                    supports_direction=direction,
                    supports_confidence=_gdpnow_confidence(value, gdp_threshold),
                    snippet=item.snippet
                    or (
                        f"GDPNow latest estimate is {value:.2f}% SAAR versus "
                        f"the {gdp_threshold:.2f}% market threshold."
                    ),
                )
            )
        elif (
            item.metric_name == "cpi_monthly_change_single_decimal"
            and item.metric_value is not None
            and cpi_threshold is not None
        ):
            value = float(item.metric_value)
            direction = "yes" if value > cpi_threshold else "no"
            out.append(
                replace(
                    item,
                    supports_direction=direction,
                    supports_confidence=_cpi_confidence(value, cpi_threshold),
                    snippet=item.snippet
                    or (
                        f"BLS CPI-U monthly change is {value:.1f}% versus "
                        f"the {cpi_threshold:.1f}% market threshold."
                    ),
                )
            )
        elif (
            item.metric_name == "nws_daily_high_temp_f"
            and item.metric_value is not None
            and weather_range is not None
        ):
            value = float(item.metric_value)
            low, high = weather_range
            direction = "yes" if low <= value <= high else "no"
            if (
                item.claim_type in {"disconfirming", "contradiction_check"}
                and item.supports_direction == "neutral"
            ):
                out.append(
                    replace(
                        item,
                        supports_direction="neutral",
                        supports_confidence=max(
                            float(item.supports_confidence or 0.0),
                            0.65,
                        ),
                        snippet=(
                            f"Disconfirming search checked the NWS Central Park "
                            f"daily maximum of {value:.0f}F against the "
                            f"{_weather_range_text(weather_range)} market range; "
                            "no contrary official high-temperature fact was found."
                        ),
                    )
                )
                continue
            if item.claim_type in {"disconfirming", "contradiction_check"}:
                out.append(item)
                continue
            out.append(
                replace(
                    item,
                    supports_direction=direction,
                    supports_confidence=0.95,
                    snippet=item.snippet
                    or (
                        f"NWS Central Park daily maximum is {value:.0f}F "
                        f"versus the {_weather_range_text(weather_range)} "
                        "market range."
                    ),
                )
            )
        elif leadership_replacement and _leadership_replacement_supports_yes(item):
            out.append(
                replace(
                    item,
                    supports_direction="yes",
                    supports_confidence=max(
                        float(item.supports_confidence or 0.0),
                        _leadership_replacement_confidence(item),
                    ),
                    snippet=item.snippet
                    or (
                        "Source text directly matches the market condition: "
                        "a call for Chuck Schumer to be replaced or for new "
                        "Senate Democratic leadership."
                    ),
                )
            )
        elif (
            leadership_replacement
            and item.supports_direction in {"yes", "no"}
            and not _leadership_replacement_mentions_condition(item)
        ):
            out.append(
                replace(
                    item,
                    supports_direction="neutral",
                    supports_confidence=0.0,
                )
            )
        elif office_departure and (
            departure_direction := _office_departure_direction(item)
        ) is not None:
            direction, confidence = departure_direction
            out.append(
                replace(
                    item,
                    supports_direction=direction,
                    supports_confidence=max(
                        float(item.supports_confidence or 0.0),
                        confidence,
                    ),
                )
            )
        elif (
            office_departure
            and item.supports_direction in {"yes", "no"}
            and not _office_departure_mentions_condition(item)
        ):
            out.append(
                replace(
                    item,
                    supports_direction="neutral",
                    supports_confidence=0.0,
                )
            )
        else:
            out.append(item)
    return out


def _query_mentions_leadership_replacement(text: str) -> bool:
    cleaned = _clean(text).lower()
    return (
        "schumer" in cleaned
        and ("senate" in cleaned or "democratic" in cleaned)
        and (
            "step down" in cleaned
            or "resign" in cleaned
            or "replaced" in cleaned
            or "replacement" in cleaned
            or "new leadership" in cleaned
        )
    )


def _leadership_replacement_supports_yes(item: ResearchEvidence) -> bool:
    if item.claim_type not in {
        "supporting",
        "corroboration",
        "official_resolution",
        "resolution_source",
        "resolution",
    }:
        return False
    text = _clean(f"{item.title} {item.snippet}").lower()
    if "schumer" not in text:
        return False
    return (
        "calling for schumer to be replaced" in text
        or "schumer to be replaced" in text
        or "replace schumer" in text
        or "replaced as leader" in text
        or ("new leadership" in text and ("schumer" in text or "leader" in text))
        or "schumer should step down" in text
        or "schumer should resign" in text
    )


def _leadership_replacement_mentions_condition(item: ResearchEvidence) -> bool:
    text = _clean(f"{item.title} {item.snippet}").lower()
    if "schumer" not in text:
        return False
    return (
        "step down" in text
        or "resign" in text
        or "replace" in text
        or "replaced" in text
        or "replacement" in text
        or "new leadership" in text
    )


def _leadership_replacement_confidence(item: ResearchEvidence) -> float:
    text = _clean(f"{item.title} {item.snippet}").lower()
    if (
        "calling for schumer to be replaced" in text
        or "schumer to be replaced" in text
        or "replaced as leader" in text
    ):
        return 0.9
    return 0.82


def _query_mentions_office_departure(text: str) -> bool:
    cleaned = _clean(text).lower()
    return (
        (" out as " in f" {cleaned} " or "leaves as" in cleaned or "leave as" in cleaned)
        and (
            "president" in cleaned
            or "prime minister" in cleaned
            or "office" in cleaned
            or "leader" in cleaned
        )
    )


def _office_departure_direction(item: ResearchEvidence) -> tuple[str, float] | None:
    text = _clean(f"{item.title} {item.snippet}").lower()
    if re.search(r"\brefus(?:e|es|ed|ing)\s+to\s+resign\b", text):
        return ("no", 0.85)
    if (
        "remains in office" in text
        or "remain in office" in text
        or "stays in office" in text
        or "stay in office" in text
        or "has not left office" in text
        or "did not resign" in text
        or "has not resigned" in text
    ):
        return ("no", 0.8)
    actual_departure = (
        re.search(r"\bresign(?:s|ed)?\s+as\b", text) is not None
        or "stepped down" in text
        or "steps down" in text
        or "removed from office" in text
        or "ousted" in text
        or "leaves office" in text
        or "left office" in text
        or "no longer president" in text
        or "no longer prime minister" in text
    )
    if not actual_departure:
        return None
    proposal_only = (
        "to remove" in text
        or "demand" in text
        or "calls for" in text
        or "called for" in text
        or "proposal" in text
        or "would ease" in text
        or "could remove" in text
    )
    if proposal_only and not re.search(r"\bresign(?:s|ed)?\s+as\b", text):
        return None
    return ("yes", 0.85)


def _office_departure_mentions_condition(item: ResearchEvidence) -> bool:
    return _office_departure_direction(item) is not None


_OFFICIAL_DATA_PENDING_METRICS = frozenset(
    {
        "cpi_official_data_pending",
        "fed_decision_pending",
        "bank_of_israel_decision_pending",
        "event_window_pending",
        "economic_stat_data_pending",
        "getty_distinct_photo_days_pending",
        "treasury_yield_data_pending",
        "truth_social_window_pending",
        "nws_daily_high_temp_pending",
        "white_house_presidential_actions_pending",
        "white_house_snapshot_missed",
    }
)


def _is_official_data_pending_evidence(item: ResearchEvidence) -> bool:
    return item.metric_name in _OFFICIAL_DATA_PENDING_METRICS


def _has_official_data_pending(evidence: list[ResearchEvidence]) -> bool:
    return any(_is_official_data_pending_evidence(item) for item in evidence)


def _keep_pending_no_edge_researchable(verdict: ResearchVerdict) -> ResearchVerdict:
    has_open_event_window = any(
        item.metric_name == "event_window_pending" for item in verdict.evidence
    )
    if not has_open_event_window or verdict.skip_reason not in {
        "no_edge",
        "negative_net_edge_after_costs",
    }:
        return verdict
    return replace(
        verdict,
        status=ResearchStatus.NEEDS_RESEARCH,
        summary=(
            "Current predictive evidence does not clear executable edge, but the "
            "official event window remains open; keep research queued."
        ),
        skip_reason="official_data_pending",
        research_pending_origin=verdict.skip_reason,
        force_side=None,
    )


def _has_reliable_non_pending_source_path(
    evidence: list[ResearchEvidence],
) -> bool:
    non_pending = [
        item for item in evidence if not _is_official_data_pending_evidence(item)
    ]
    if has_reliable_research_source_path(non_pending):
        return True
    if any(
        item.source_class == "specialized_data"
        and item.claim_type == "base_rate"
        and item.metric_name == "gdpnow_real_gdp_growth_saar"
        and item.supports_direction in {"yes", "no"}
        and float(item.supports_confidence or 0.0) >= 0.5
        and _is_finite_number(item.metric_value)
        and float(item.extraction_confidence or 0.0) >= 0.8
        and research_source_key(
            item.source_class,
            item.source_name,
            item.source_url,
        )
        == "stlouisfed.org"
        for item in non_pending
    ):
        return True
    source_keys = {
        source_key
        for item in non_pending
        if (
            source_key := research_source_key(
                item.source_class,
                item.source_name,
                item.source_url,
            )
        )
        and source_key != "google.com"
    }
    return (
        len(source_keys) >= 2
        and any(_is_settlement_evidence(item) for item in non_pending)
    )


def _is_finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _has_trade_selection_evidence(evidence: list[ResearchEvidence]) -> bool:
    return any(
        item.supports_direction in {"yes", "no"}
        and (
            item.metric_name in _STRUCTURED_SIGNAL_METRICS
            or item.claim_type
            in {
                "base_rate",
                "corroboration",
                "disconfirming",
                "official_resolution",
                "resolution",
                "settlement",
                "supporting",
            }
        )
        and float(item.supports_confidence or 0.0) > 0.0
        for item in evidence
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        cleaned = _clean(value)
        return (cleaned,) if cleaned else ()
    if not isinstance(value, Iterable):
        return ()
    out: list[str] = []
    for item in value:
        cleaned = _clean(item)
        if cleaned:
            out.append(cleaned[:300])
    return tuple(out)


def _apply_adjudication_evidence_assessments(
    evidence: list[ResearchEvidence],
    adjudication: dict[str, Any],
    *,
    market: Any | None = None,
) -> list[ResearchEvidence]:
    raw_assessments = adjudication.get("evidence_assessments")
    if isinstance(raw_assessments, dict):
        raw_assessments = [
            {"source_url": source_url, **assessment}
            for source_url, assessment in raw_assessments.items()
            if isinstance(assessment, dict)
        ]
    if not isinstance(raw_assessments, list):
        return evidence
    by_url: dict[str, dict[str, Any]] = {}
    by_ordinal: dict[int, dict[str, Any]] = {}
    for index, raw in enumerate(raw_assessments):
        if not isinstance(raw, dict):
            continue
        url = _clean(raw.get("source_url") or raw.get("url"))
        if url:
            by_url[url] = raw
        ordinal = raw.get("ordinal", raw.get("index"))
        try:
            by_ordinal[int(ordinal)] = raw
        except (TypeError, ValueError):
            by_ordinal[index] = raw
    labeled: list[ResearchEvidence] = []
    for index, item in enumerate(evidence):
        if item.supports_direction == "neutral" and (
            item.metric_name in _STRUCTURED_SIGNAL_METRICS
            or _is_official_data_pending_evidence(item)
        ):
            labeled.append(item)
            continue
        available_at = _parse_timestamp(item.available_at)
        if available_at is not None and available_at > datetime.now(timezone.utc):
            labeled.append(
                replace(
                    item,
                    supports_direction="neutral",
                    supports_confidence=0.0,
                )
            )
            continue
        assessment = by_url.get(item.source_url) or by_ordinal.get(index)
        if assessment is None:
            labeled.append(item)
            continue
        if (
            item.metric_name
            in {
                "govinfo_bill_status_introduced",
                "govinfo_bill_status_passed_house",
            }
            and item.supports_direction in {"yes", "no"}
            and float(item.supports_confidence or 0.0) >= 0.6
        ):
            labeled.append(item)
            continue
        direction = str(
            assessment.get("supports_direction")
            or assessment.get("direction")
            or item.supports_direction
            or "neutral"
        ).lower()
        if direction not in {"yes", "no", "neutral"}:
            direction = "neutral"
        confidence = _coerce_probability(
            assessment.get("supports_confidence", assessment.get("confidence"))
        )
        if (
            market is not None
            and direction in {"yes", "no"}
            and float(confidence if confidence is not None else 0.0) > 0.0
            and (
                float(confidence if confidence is not None else 0.0) >= 0.6
                or _contract_relevance_spec_for_market(market).speech.detected
            )
            and not _evidence_mentions_market_terms(item, market)
        ):
            direction = "neutral"
            confidence = 0.0
        claim_type = _clean(assessment.get("claim_type") or item.claim_type)
        labeled.append(
            replace(
                item,
                claim_type=claim_type or item.claim_type,
                supports_direction=direction,
                supports_confidence=(
                    float(confidence)
                    if confidence is not None
                    else item.supports_confidence
                ),
            )
        )
    return labeled


_MARKET_RELEVANCE_STOPWORDS = {
    "about",
    "after",
    "against",
    "before",
    "being",
    "current",
    "democratic",
    "democrats",
    "including",
    "independents",
    "leader",
    "member",
    "public",
    "publicly",
    "resolution",
    "resolve",
    "resolves",
    "senate",
    "should",
    "state",
    "states",
    "that",
    "their",
    "there",
    "these",
    "those",
    "will",
    "with",
}


def _evidence_mentions_market_terms(item: ResearchEvidence, market: Any) -> bool:
    evidence_text = _clean(f"{item.title} {item.snippet}").lower()
    if not evidence_text:
        return False
    market_text = _market_text(market)
    relevance_spec = _contract_relevance_spec_for_market(market)
    if relevance_spec.speech.detected:
        return evidence_is_relevant_to_contract(
            evidence_text,
            relevance_spec,
        )
    if _query_mentions_trump_passport_image(market_text):
        title_text = _clean(item.title).lower()
        return "passport" in title_text and "trump" in title_text
    if _query_mentions_leadership_replacement(market_text):
        return _leadership_replacement_mentions_condition(item)
    if _query_mentions_office_departure(market_text):
        return _office_departure_mentions_condition(item)
    terms = _market_relevance_terms(market)
    if not terms:
        return True
    overlap = {term for term in terms if term in evidence_text}
    if len(overlap) >= 2:
        return True
    distinctive = {
        term
        for term in terms
        if len(term) >= 6 and term not in _MARKET_RELEVANCE_STOPWORDS
    }
    return bool(overlap & distinctive)


def _contract_relevance_spec_for_market(market: Any) -> ContractRelevanceSpec:
    return build_contract_relevance_spec(
        getattr(market, "ticker", ""),
        (
            getattr(market, "title", ""),
            getattr(market, "rules_primary", ""),
            getattr(market, "rules_secondary", ""),
        ),
    )


def _market_relevance_terms(market: Any) -> set[str]:
    text = _clean(
        " ".join(
            str(getattr(market, attr, "") or "")
            for attr in ("ticker", "title", "rules_primary", "rules_secondary")
        )
    ).lower()
    raw_terms = re.findall(r"[a-z][a-z0-9]{3,}", text)
    return {
        term
        for term in raw_terms
        if term not in _MARKET_RELEVANCE_STOPWORDS
    }


def _has_vetted_candidate_snapshot(
    cached_dossier: Any | None,
    contract_fingerprint: str,
) -> bool:
    return (
        cached_dossier is not None
        and _is_vetted_candidate_status(
            getattr(cached_dossier, "last_verdict_status", None)
        )
        and getattr(cached_dossier, "last_force_side", None) in {"yes", "no"}
        and getattr(cached_dossier, "last_estimated_probability", None) is not None
        and getattr(cached_dossier, "last_confidence", None) is not None
        and getattr(cached_dossier, "last_contract_fingerprint", None)
        == contract_fingerprint
    )


def _is_vetted_candidate_status(status: Any) -> bool:
    value = status.value if isinstance(status, ResearchStatus) else str(status or "")
    return value in {
        ResearchStatus.TRADE_CANDIDATE.value,
        ResearchStatus.DECISION_GRADE_CANDIDATE.value,
    }


def _should_update_dossier_snapshot(
    verdict: ResearchVerdict,
    *,
    trigger_source: str,
    cached_dossier: Any | None,
    contract_fingerprint: str,
) -> bool:
    if not verdict.evidence:
        return False
    if verdict.skip_reason == "research_timeout":
        return False
    if (
        trigger_source == "research_prewarm"
        and not _is_vetted_candidate_status(verdict.status)
        and _has_vetted_candidate_snapshot(cached_dossier, contract_fingerprint)
    ):
        return False
    return verdict.status not in {
        ResearchStatus.RESEARCH_PROVIDER_ERROR,
        ResearchStatus.RESEARCH_ADJUDICATOR_ERROR,
    }


async def _reconcile_persisted_verdict(
    verdict: ResearchVerdict,
    *,
    dossier_store: DossierStore,
    ticker: str,
    run_id: str,
) -> ResearchVerdict:
    if verdict.status != ResearchStatus.DECISION_GRADE_CANDIDATE:
        return verdict

    def persistence_unverified(detail: str) -> ResearchVerdict:
        return replace(
            verdict,
            status=ResearchStatus.NEEDS_RESEARCH,
            skip_reason="persistence_status_unverified",
            force_side=None,
            research_persistence_error=(
                verdict.research_persistence_error or detail
            ),
        )

    expected_ticker = str(ticker or "").strip()
    expected_run_id = str(run_id or "").strip()
    verdict_run_id = str(verdict.research_run_id or "").strip()
    expected_fingerprint = str(
        verdict.research_contract_fingerprint or ""
    ).strip()
    snapshot_getter = getattr(dossier_store, "get_dossier_snapshot", None)
    evidence_getter = getattr(dossier_store, "get_research_run_evidence", None)
    if (
        not expected_ticker
        or not expected_run_id
        or verdict_run_id != expected_run_id
        or not expected_fingerprint
        or not callable(snapshot_getter)
        or not callable(evidence_getter)
    ):
        return persistence_unverified("persisted run identity is unavailable")
    try:
        snapshot = await snapshot_getter(expected_ticker)
        run_evidence = await evidence_getter(expected_ticker, expected_run_id)
    except Exception as exc:
        return persistence_unverified(
            f"persisted status verification failed: {exc}"
        )
    snapshot_identity_matches = (
        snapshot is not None
        and str(getattr(snapshot, "market_ticker", "") or "").strip()
        == expected_ticker
        and str(getattr(snapshot, "last_research_run_id", "") or "").strip()
        == expected_run_id
        and str(getattr(snapshot, "last_contract_fingerprint", "") or "").strip()
        == expected_fingerprint
    )
    evidence_identity_matches = bool(run_evidence) and all(
        str(getattr(item, "contract_fingerprint", "") or "").strip()
        == expected_fingerprint
        for item in run_evidence
    )
    if not snapshot_identity_matches or not evidence_identity_matches:
        return persistence_unverified("persisted run identity does not match verdict")
    try:
        stored_status = ResearchStatus(str(snapshot.last_verdict_status))
    except (AttributeError, ValueError):
        return persistence_unverified("persisted verdict status is unavailable")
    if stored_status == verdict.status:
        return verdict
    return replace(
        verdict,
        status=stored_status,
        skip_reason=getattr(snapshot, "last_skip_reason", None),
        force_side=getattr(snapshot, "last_force_side", None),
        estimated_probability=getattr(snapshot, "last_estimated_probability", None),
        confidence=getattr(snapshot, "last_confidence", None),
        market_price=getattr(snapshot, "last_market_price", None),
        estimated_edge=getattr(snapshot, "last_estimated_edge", None),
    )


def _validated_global_ipv4_addresses(
    addresses: Iterable[str],
    *,
    provider_name: str = "Google News",
) -> tuple[str, ...]:
    return _shared_validated_global_ipv4_addresses(
        addresses,
        provider_name=provider_name,
    )


async def _fetch_bounded_https_dual_stack(
    url: str,
    *,
    canonical_host: str,
    provider_name: str,
    user_agent: str,
    timeout: float,
    max_bytes: int,
    resolver_factory: Callable[[], Any] | None = None,
    connector_factory: Callable[..., Any] = aiohttp.TCPConnector,
    session_factory: Callable[..., Any] = aiohttp.ClientSession,
) -> bytes:
    return await fetch_bounded_https_dual_stack(
        url,
        canonical_host=canonical_host,
        provider_name=provider_name,
        user_agent=user_agent,
        timeout=timeout,
        max_bytes=max_bytes,
        admission_factory=lambda: _get_generic_web_search_work_limiter().slot(),
        resolver_factory=resolver_factory,
        connector_factory=connector_factory,
        session_factory=session_factory,
        telemetry_sink=_log_generic_search_transport_event,
    )


async def _fetch_google_news_rss_dual_stack(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    resolver_factory: Callable[[], Any] | None = None,
    connector_factory: Callable[..., Any] = aiohttp.TCPConnector,
    session_factory: Callable[..., Any] = aiohttp.ClientSession,
) -> bytes:
    return await _fetch_bounded_https_dual_stack(
        url,
        canonical_host="news.google.com",
        provider_name="Google News RSS",
        user_agent="kalshi-bot-research/1.0",
        timeout=timeout,
        max_bytes=max_bytes,
        resolver_factory=resolver_factory,
        connector_factory=connector_factory,
        session_factory=session_factory,
    )


async def _fetch_duckduckgo_lite_dual_stack(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    resolver_factory: Callable[[], Any] | None = None,
    connector_factory: Callable[..., Any] = aiohttp.TCPConnector,
    session_factory: Callable[..., Any] = aiohttp.ClientSession,
) -> bytes:
    return await _fetch_bounded_https_dual_stack(
        url,
        canonical_host="lite.duckduckgo.com",
        provider_name="DuckDuckGo Lite",
        user_agent="Mozilla/5.0 kalshi-bot-research/1.0",
        timeout=timeout,
        max_bytes=max_bytes,
        resolver_factory=resolver_factory,
        connector_factory=connector_factory,
        session_factory=session_factory,
    )


async def _rss_search(
    query: ResearchQuery,
    *,
    timeout: float = 5.0,
    limit: int = 3,
) -> list[ResearchEvidence]:
    params = urllib.parse.urlencode(
        {"q": query.query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    )
    url = f"https://news.google.com/rss/search?{params}"
    raw = await _fetch_google_news_rss_dual_stack(
        url,
        timeout=timeout,
        max_bytes=300_000,
    )
    return _parse_rss_search_response(query, raw=raw, limit=limit)


def _parse_rss_search_response(
    query: ResearchQuery,
    *,
    raw: bytes,
    limit: int,
) -> list[ResearchEvidence]:
    root = ET.fromstring(raw)
    out: list[ResearchEvidence] = []
    retrieved_at = _utc_now_iso()
    for item in root.findall(".//item")[:limit]:
        title = html.unescape(_clean(item.findtext("title")))
        link = html.unescape(_clean(item.findtext("link")))
        source_element = item.find("source")
        source = item.findtext("source") or _domain_from_url(link) or "Google News"
        publisher_url = html.unescape(
            _clean(source_element.get("url") if source_element is not None else "")
        )
        classification_source_name = _clean(source)
        link_domain = _domain_from_url(link)
        is_aggregator_link = link_domain in {"google.com", "news.google.com"}
        classification_url = publisher_url if publisher_url and is_aggregator_link else link
        canonical_url = publisher_url if publisher_url and is_aggregator_link else link
        description = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        snippet = html.unescape(_clean(description))
        direction, confidence = _rss_direction_for_query_result(
            query,
            title=title,
            snippet=snippet,
        )
        out.append(
            ResearchEvidence(
                source_class=_classify_evidence_source(
                    query,
                    classification_source_name,
                    classification_url,
                    allow_official_name_match=False,
                ),
                source_name=_clean(source),
                source_url=canonical_url,
                title=title,
                snippet=snippet[:500],
                claim_type=query.query_intent,
                supports_direction=direction,
                supports_confidence=confidence,
                published_at=_clean(item.findtext("pubDate")) or None,
                retrieved_at=retrieved_at,
                aggregator_url=(link if publisher_url and is_aggregator_link else None),
            )
        )
    return out


def _rss_direction_for_query_result(
    query: ResearchQuery,
    *,
    title: str,
    snippet: str,
) -> tuple[str, float]:
    query_text = _clean(query.query).lower()
    title_text = _clean(title).lower()
    result_text = _clean(f"{title} {snippet}").lower()
    query_mentions_passport_image = (
        "passport" in query_text
        and (
            "image" in query_text
            or "visual representation" in query_text
            or "picture" in query_text
            or "face" in query_text
            or "commemorative" in query_text
        )
    )
    if (
        query_mentions_passport_image
        and "passport" in title_text
        and "trump" in title_text
        and (
            "trump picture" in result_text
            or "trump's picture" in result_text
            or "trump\u2019s picture" in result_text
            or "trump face" in result_text
            or "trump's face" in result_text
            or "trump\u2019s face" in result_text
        )
        and any(term in result_text for term in ("issue", "issued", "rollout"))
    ):
        return "yes", 0.75
    return "neutral", 0.0


async def _duckduckgo_lite_search(
    query: ResearchQuery,
    *,
    timeout: float = 5.0,
    limit: int = 3,
) -> list[ResearchEvidence]:
    params = urllib.parse.urlencode({"q": query.query})
    url = f"https://lite.duckduckgo.com/lite/?{params}"
    response = await _fetch_duckduckgo_lite_dual_stack(
        url,
        timeout=timeout,
        max_bytes=300_000,
    )
    raw = response.decode("utf-8", errors="ignore")
    retrieved_at = _utc_now_iso()
    out: list[ResearchEvidence] = []
    result_pattern = re.compile(
        r"<a[^>]+href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title>.*?)</a>",
        flags=re.I | re.S,
    )
    matches = list(result_pattern.finditer(raw))
    for index, match in enumerate(matches):
        href = html.unescape(match.group("href"))
        source_url = _duckduckgo_result_url(href)
        if not source_url:
            continue
        title = html.unescape(_clean(re.sub(r"<[^>]+>", " ", match.group("title"))))
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        result_block = raw[match.end():next_start]
        snippet_match = re.search(
            r"<td[^>]+class=[\"']result-snippet[\"'][^>]*>(.*?)</td>",
            result_block,
            flags=re.I | re.S,
        )
        snippet = ""
        if snippet_match:
            snippet = html.unescape(
                _clean(re.sub(r"<[^>]+>", " ", snippet_match.group(1)))
            )
        source_name = _domain_from_url(source_url) or "DuckDuckGo Lite"
        out.append(
            ResearchEvidence(
                source_class=_classify_evidence_source(query, source_name, source_url),
                source_name=source_name,
                source_url=source_url,
                title=title,
                snippet=snippet[:500],
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                retrieved_at=retrieved_at,
            )
        )
        if len(out) >= limit:
            break
    return out


def _duckduckgo_result_url(href: str) -> str:
    cleaned = _clean(href)
    if not cleaned:
        return ""
    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"
    parsed = urllib.parse.urlparse(cleaned)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        values = urllib.parse.parse_qs(parsed.query)
        target = values.get("uddg", [""])[0]
        return _clean(target)
    if "duckduckgo.com" in parsed.netloc:
        return ""
    return cleaned if parsed.scheme in {"http", "https"} else ""


def _getty_search_payload(
    raw: bytes,
    *,
    expected_end_date: str,
) -> GettySearchSnapshot:
    if len(raw) > 2_000_000:
        raise ValueError("Getty response exceeded structured payload limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Getty response was not structured JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Getty response root was not an object")
    params = payload.get("query", {}).get("params", {})
    required_params = {
        "assettype": "image",
        "enddate": expected_end_date,
        "family": "editorial",
        "sort": "newest",
        "specificpeople": "118600",
    }
    if not isinstance(params, dict) or any(
        str(params.get(key, "")).lower() != value
        for key, value in required_params.items()
    ):
        raise ValueError("Getty response filters did not match the contract query")
    gallery = payload.get("gallery")
    if not isinstance(gallery, dict) or int(gallery.get("page", 0) or 0) != 1:
        raise ValueError("Getty response did not contain gallery page one")
    total = gallery.get("totalNumberOfResults")
    if not isinstance(total, int) or total < 0:
        raise ValueError("Getty response did not contain a valid result total")
    assets = gallery.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Getty response did not contain an asset list")
    expected_date = date.fromisoformat(expected_end_date)
    asset_dates: list[date] = []
    witness_ids: set[str] = set()
    seen_ids: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("Getty response contained a malformed asset")
        asset_id = _clean(asset.get("assetId") or asset.get("id") or "")
        if not asset_id or asset_id in seen_ids:
            raise ValueError("Getty response contained a missing or duplicate asset id")
        seen_ids.add(asset_id)
        created = _named_date(_clean(asset.get("dateCreated") or ""))
        if created is None:
            raise ValueError("Getty response contained an unparseable asset date")
        asset_dates.append(created)
        people = _clean(asset.get("people") or "").lower()
        description = _clean(
            f"{asset.get('caption') or ''} {asset.get('altText') or ''}"
        ).lower()
        if (
            created == expected_date
            and "donald" in people
            and "trump" in people
            and "trump" in description
        ):
            witness_ids.add(asset_id)
    return GettySearchSnapshot(
        end_date=expected_date,
        total_results=total,
        newest_asset_date=max(asset_dates, default=None),
        witness_asset_ids=tuple(sorted(witness_ids)),
    )


def _getty_daily_search_url(end_date: date) -> str:
    params = urllib.parse.urlencode(
        {
            "family": "editorial",
            "sort": "newest",
            "specificpeople": "118600",
            "enddate": end_date.isoformat(),
        }
    )
    return f"https://www.gettyimages.com/search/2/image?{params}"


def _fetch_getty_snapshot(
    end_date: date,
    *,
    timeout: float,
) -> GettySearchSnapshot:
    request = urllib.request.Request(
        _getty_daily_search_url(end_date),
        headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "kalshi-bot-research/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        raw = response.read(2_000_001)
    return _getty_search_payload(raw, expected_end_date=end_date.isoformat())


def _getty_distinct_date_search(
    query: ResearchQuery,
    *,
    timeout: float = 5.0,
    now: datetime | None = None,
) -> list[ResearchEvidence]:
    spec = _getty_distinct_date_spec_from_text(query.query)
    if spec is None:
        return []
    dates = [
        spec.start_date + timedelta(days=offset)
        for offset in range((spec.end_date - spec.start_date).days + 1)
    ]
    cache_key = (spec.start_date, spec.end_date)
    with _GETTY_SNAPSHOT_CACHE_LOCK:
        cached = _GETTY_SNAPSHOT_CACHE.get(cache_key)
        if (
            cached is not None
            and time.monotonic() - cached[0] <= _GETTY_SNAPSHOT_CACHE_TTL_SECONDS
        ):
            snapshots = list(cached[1])
        else:
            deadline = time.monotonic() + timeout
            snapshots = []
            for day in [spec.start_date - timedelta(days=1), *dates]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Getty structured search exceeded its total timeout")
                snapshots.append(_fetch_getty_snapshot(day, timeout=remaining))
            _GETTY_SNAPSHOT_CACHE[cache_key] = (time.monotonic(), tuple(snapshots))
    witness_dates: list[date] = []
    delta_dates: list[date] = []
    witness_ids: list[str] = []
    deltas: list[int] = []
    for prior, current in zip(snapshots, snapshots[1:]):
        delta = current.total_results - prior.total_results
        if delta < 0:
            raise ValueError("Getty cumulative result total decreased")
        deltas.append(delta)
        has_witness = bool(current.witness_asset_ids)
        newest_matches = current.newest_asset_date == current.end_date
        if has_witness and newest_matches:
            witness_dates.append(current.end_date)
            witness_ids.extend(current.witness_asset_ids)
        if delta > 0:
            delta_dates.append(current.end_date)
    if witness_dates != delta_dates:
        raise ValueError("Getty witness dates disagreed with cumulative result deltas")
    count = len(witness_dates)
    now = now or datetime.now(timezone.utc)
    direction = "neutral"
    confidence = 0.0
    retrieved_at = _utc_now_iso()
    date_text = ", ".join(item.isoformat() for item in witness_dates) or "none"
    delta_date_text = ", ".join(item.isoformat() for item in delta_dates) or "none"
    witness_text = ", ".join(witness_ids[:8]) or "none"
    source_url = _getty_daily_search_url(spec.end_date)
    support = ResearchEvidence(
        source_class="resolution_source",
        source_name="Getty Images",
        source_url=source_url,
        title=f"Getty Trump editorial-photo distinct-day count: {count}",
        snippet=(
            f"Getty filtered editorial search validates {count} distinct Trump-photo "
            f"days ({date_text}); witness assets {witness_text}. Exact target is "
            f"{spec.target_count}; cumulative daily deltas were {deltas}."
        ),
        claim_type="official_resolution",
        supports_direction=direction,
        supports_confidence=confidence,
        published_at=spec.end_date.isoformat(),
        available_at=(spec.cutoff_at.isoformat() if now < spec.cutoff_at else None),
        retrieved_at=retrieved_at,
        metric_name="getty_trump_distinct_photo_days",
        metric_value=float(count),
        metric_unit="distinct_days",
        extraction_confidence=0.98,
    )
    counter = ResearchEvidence(
        source_class="resolution_source",
        source_name="Getty Images",
        source_url=f"{source_url}#cumulative-delta-countercheck",
        title=f"Getty cumulative-total countercheck: {count} distinct days",
        snippet=(
            f"Contradiction check compared cumulative Getty totals across each date; "
            f"positive deltas occur on {delta_date_text}, independently totaling "
            f"{count} distinct days for exact target {spec.target_count}."
        ),
        claim_type="contradiction_check",
        supports_direction="neutral",
        supports_confidence=0.65,
        published_at=spec.end_date.isoformat(),
        available_at=(spec.cutoff_at.isoformat() if now < spec.cutoff_at else None),
        retrieved_at=retrieved_at,
        metric_name="getty_trump_distinct_photo_days",
        metric_value=float(len(delta_dates)),
        metric_unit="distinct_days",
        extraction_confidence=0.98,
    )
    pending = ResearchEvidence(
        source_class="resolution_source",
        source_name="Getty Images",
        source_url=source_url,
        title="Getty distinct-day result remains open",
        snippet=(
            f"Current validated count is {count}; late uploads, tag corrections, "
            "or Kalshi review can change the exact-day result. Market close is not "
            "evidence that Getty metadata is final."
        ),
        claim_type="official_resolution",
        supports_direction="neutral",
        supports_confidence=0.0,
        available_at=(spec.cutoff_at.isoformat() if now < spec.cutoff_at else None),
        retrieved_at=retrieved_at,
        metric_name="getty_distinct_photo_days_pending",
        metric_unit="period_status",
        extraction_confidence=0.98,
    )
    return [support, counter, pending]


class _WhiteHouseActionCardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[WhiteHouseActionCard] = []
        self._in_card = False
        self._in_title_link = False
        self._in_time = False
        self._title_parts: list[str] = []
        self._time_parts: list[str] = []
        self._url = ""
        self._datetime = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if tag == "li" and "wp-block-post" in classes:
            if self._in_card:
                raise ValueError("nested White House action card")
            self._in_card = True
            self._title_parts = []
            self._time_parts = []
            self._url = ""
            self._datetime = ""
        elif self._in_card and tag == "a" and not self._url:
            href = _clean(values.get("href", ""))
            if "/presidential-actions/" in href and href.rstrip("/") != "https://www.whitehouse.gov/presidential-actions":
                self._url = href
                self._in_title_link = True
        elif self._in_card and tag == "time":
            self._datetime = _clean(values.get("datetime", ""))
            self._in_time = True

    def handle_data(self, data: str) -> None:
        if self._in_title_link:
            self._title_parts.append(data)
        if self._in_time:
            self._time_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_title_link = False
        elif tag == "time":
            self._in_time = False
        elif tag == "li" and self._in_card:
            title = _clean(" ".join(self._title_parts))
            visible_date = _named_date(_clean(" ".join(self._time_parts)))
            try:
                attribute_date = datetime.fromisoformat(self._datetime).date()
            except ValueError as exc:
                raise ValueError("White House action card had invalid datetime") from exc
            if not title or not self._url or visible_date is None:
                raise ValueError("White House action card was missing title, URL, or date")
            if visible_date != attribute_date:
                raise ValueError("White House visible and machine dates disagreed")
            self.cards.append(
                WhiteHouseActionCard(title, self._url, visible_date)
            )
            self._in_card = False


def _white_house_action_cards_from_html(raw: str) -> tuple[WhiteHouseActionCard, ...]:
    parser = _WhiteHouseActionCardParser()
    parser.feed(raw)
    parser.close()
    if parser._in_card:
        raise ValueError("White House action archive ended inside a card")
    if not parser.cards:
        raise ValueError("White House action archive contained no action cards")
    return tuple(parser.cards)


def _white_house_presidential_actions_search(
    query: ResearchQuery,
    *,
    timeout: float = 5.0,
    max_pages: int = 5,
    now: datetime | None = None,
    observation_started_at: datetime | None = None,
) -> list[ResearchEvidence]:
    spec = _white_house_action_count_spec_from_text(query.query)
    if spec is None:
        return []
    supplied_now = now
    observation_started_at = (
        observation_started_at or supplied_now or datetime.now(timezone.utc)
    )
    cards_by_url: dict[str, WhiteHouseActionCard] = {}
    reached_boundary = False
    deadline = time.monotonic() + timeout
    previous_page_oldest: date | None = None
    for page in range(1, max_pages + 1):
        source_url = (
            "https://www.whitehouse.gov/presidential-actions/"
            if page == 1
            else f"https://www.whitehouse.gov/presidential-actions/page/{page}/"
        )
        request = urllib.request.Request(
            source_url,
            headers={"User-Agent": "kalshi-bot-research/1.0"},
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("White House action search exceeded its total timeout")
        with urllib.request.urlopen(request, timeout=remaining) as response:  # nosec B310
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("White House action archive exceeded page limit")
        cards = _white_house_action_cards_from_html(raw.decode("utf-8", errors="strict"))
        card_dates = [card.published_date for card in cards]
        if any(later > earlier for earlier, later in zip(card_dates, card_dates[1:])):
            raise ValueError("White House action cards were not newest-first")
        if previous_page_oldest is not None and max(card_dates) > previous_page_oldest:
            raise ValueError("White House action pages were not monotonically ordered")
        for card in cards:
            canonical_url = card.url.rstrip("/")
            existing_card = cards_by_url.get(canonical_url)
            if existing_card is not None and existing_card != card:
                raise ValueError("White House duplicate action URL had conflicting metadata")
            cards_by_url.setdefault(canonical_url, card)
        previous_page_oldest = min(card_dates)
        if previous_page_oldest < spec.start_date:
            reached_boundary = True
            break
    if not reached_boundary:
        raise ValueError("White House action pagination did not reach the contract boundary")
    qualifying = sorted(
        (
            card
            for card in cards_by_url.values()
            if spec.start_date <= card.published_date <= spec.end_date
        ),
        key=lambda card: (card.published_date, card.url),
    )
    count = len(qualifying)
    observation_finished_at = supplied_now or datetime.now(timezone.utc)
    if (
        observation_started_at > spec.cutoff_at + timedelta(minutes=5)
        or observation_finished_at > spec.cutoff_at + timedelta(minutes=5)
    ):
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="White House Presidential Actions",
                source_url="https://www.whitehouse.gov/presidential-actions/",
                title="White House contract snapshot was not captured",
                snippet=(
                    f"The archive was fetched after the {spec.cutoff_at.isoformat()} "
                    "contract snapshot tolerance; current contents cannot prove the "
                    "page state at settlement time."
                ),
                claim_type="official_resolution",
                supports_direction="neutral",
                supports_confidence=0.0,
                retrieved_at=_utc_now_iso(),
                metric_name="white_house_snapshot_missed",
                metric_unit="snapshot_status",
                extraction_confidence=0.98,
            )
        ]
    locked = (
        observation_started_at >= spec.cutoff_at
        and observation_finished_at >= spec.cutoff_at
    )
    direction = "neutral"
    confidence = 0.0
    if locked:
        direction = "yes" if count >= spec.threshold else "no"
        confidence = 0.98
    retrieved_at = _utc_now_iso()
    card_text = "; ".join(
        f"{card.published_date.isoformat()} {card.title}" for card in qualifying
    ) or "none"
    source_url = "https://www.whitehouse.gov/presidential-actions/"
    support = ResearchEvidence(
        source_class="official_primary",
        source_name="White House Presidential Actions",
        source_url=source_url,
        title=f"White House presidential-action count: {count}",
        snippet=(
            f"Official archive has {count} unique actions dated from "
            f"{spec.start_date.isoformat()} through {spec.end_date.isoformat()}: "
            f"{card_text}. Threshold is at least {spec.threshold}."
        ),
        claim_type="official_resolution",
        supports_direction=direction,
        supports_confidence=confidence,
        available_at=None if locked else spec.cutoff_at.isoformat(),
        retrieved_at=retrieved_at,
        metric_name="white_house_presidential_actions_count",
        metric_value=float(count),
        metric_unit="actions",
        extraction_confidence=0.98,
    )
    if locked:
        return [support]
    pending = ResearchEvidence(
        source_class="official_primary",
        source_name="White House Presidential Actions",
        source_url=source_url,
        title="White House presidential-action window remains open",
        snippet=(
            f"Current official count is {count}; {max(spec.threshold - count, 0)} "
            f"more actions are needed before the {spec.cutoff_at.isoformat()} check."
        ),
        claim_type="official_resolution",
        supports_direction="neutral",
        supports_confidence=0.0,
        available_at=spec.cutoff_at.isoformat(),
        retrieved_at=retrieved_at,
        metric_name="white_house_presidential_actions_pending",
        metric_unit="period_status",
        extraction_confidence=0.98,
    )
    return [support, pending]


def _federal_register_search(
    query: ResearchQuery,
    *,
    timeout: float = 5.0,
    limit: int = 3,
) -> list[ResearchEvidence]:
    term = _federal_register_term(query.query)
    if not term:
        return []
    params = urllib.parse.urlencode(
        {
            "per_page": str(max(1, min(int(limit), 20))),
            "conditions[term]": term,
        }
    )
    request = urllib.request.Request(
        f"https://www.federalregister.gov/api/v1/documents.json?{params}",
        headers={"User-Agent": "kalshi-bot-research/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        payload = json.loads(response.read(300_000).decode("utf-8"))
    retrieved_at = _utc_now_iso()
    out: list[ResearchEvidence] = []
    for item in payload.get("results", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        source_url = _clean(item.get("html_url") or item.get("pdf_url") or "")
        if not source_url:
            continue
        title = _clean(item.get("title") or item.get("citation") or "")
        snippet = _clean(
            item.get("abstract")
            or item.get("executive_order_notes")
            or item.get("type")
            or ""
        )
        out.append(
            ResearchEvidence(
                source_class=_classify_evidence_source(
                    query,
                    "Federal Register",
                    source_url,
                ),
                source_name="Federal Register",
                source_url=source_url,
                title=title,
                snippet=snippet[:500],
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                published_at=_clean(item.get("publication_date") or "") or None,
                retrieved_at=retrieved_at,
            )
        )
        if len(out) >= limit:
            break
    return out


def _federal_register_term(query: str) -> str:
    text = re.sub(r"\bsite:federalregister\.gov\b", " ", query or "", flags=re.I)
    text = re.sub(
        r"\b(official_resolution|official|resolution|source|current|latest|status)\b",
        " ",
        text,
        flags=re.I,
    )
    return _clean(text)[:220]


def _gdpnow_search(
    query: ResearchQuery,
    *,
    timeout: float = 5.0,
) -> list[ResearchEvidence]:
    if not _query_mentions_gdpnow(query.query):
        return []
    request = urllib.request.Request(
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=GDPNOW",
        headers={"User-Agent": "kalshi-bot-research/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        raw = response.read(300_000).decode("utf-8", errors="ignore")
    observation = _latest_gdpnow_observation(raw)
    if observation is None:
        return []
    observation_date, value = observation
    threshold = _gdp_threshold_from_text(query.query)
    direction = "neutral"
    confidence = 0.3
    threshold_text = ""
    if threshold is not None:
        direction = "yes" if value >= threshold else "no"
        confidence = _gdpnow_confidence(value, threshold)
        threshold_text = (
            f" versus the {threshold:.2f}% market threshold, supporting "
            f"{direction.upper()}"
        )
    snippet = (
        f"FRED GDPNOW latest observation is {value:.4g}% SAAR on "
        f"{observation_date}{threshold_text}. GDPNow is a nowcast, not the "
        "official BEA settlement value."
    )
    return [
        ResearchEvidence(
            source_class="specialized_data",
            source_name="FRED GDPNow",
            source_url="https://fred.stlouisfed.org/series/GDPNOW",
            title=f"GDPNow latest observation: {value:.4g}% SAAR",
            snippet=snippet,
            claim_type=query.query_intent,
            supports_direction=direction,
            supports_confidence=confidence,
            published_at=observation_date,
            retrieved_at=_utc_now_iso(),
            metric_name="gdpnow_real_gdp_growth_saar",
            metric_value=value,
            metric_unit="percent_saar",
            extraction_confidence=0.95,
        )
    ]


def _query_mentions_gdpnow(query: str) -> bool:
    text = _clean(query).lower()
    return (
        "gdpnow" in text
        or "annualized gdp growth" in text
        or "real gdp growth" in text
        or "gross domestic product" in text
    )


def _latest_gdpnow_observation(raw_csv: str) -> tuple[str, float] | None:
    latest: tuple[str, float] | None = None
    for row in csv.DictReader(io.StringIO(raw_csv)):
        date_text = _clean(row.get("observation_date") or row.get("DATE") or "")
        value_text = _clean(row.get("GDPNOW") or row.get("VALUE") or "")
        if not date_text or value_text in {"", "."}:
            continue
        try:
            value = float(value_text)
        except ValueError:
            continue
        latest = (date_text, value)
    return latest


def _gdp_threshold_from_text(text: str) -> float | None:
    cleaned = _clean(text)
    patterns = (
        r"\b(?:more than|above|over|greater than|at least)\s+(-?\d+(?:\.\d+)?)\s*%?",
        r"\bT(-?\d+(?:\.\d+)?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.I)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


_GDPNOW_SOURCE_CLASS = "specialized_data"
_GDPNOW_SOURCE_NAME = "FRED GDPNow"
_GDPNOW_CANONICAL_SOURCE_URL = "https://fred.stlouisfed.org/series/GDPNOW"
_GDPNOW_METRIC_NAME = "gdpnow_real_gdp_growth_saar"
_GDPNOW_METRIC_UNIT = "percent_saar"
_GDP_REAL_GDP_RE = re.compile(
    r"\breal\s+(?:gdp|gross\s+domestic\s+product)\b",
    flags=re.I,
)
_GDP_SAAR_RE = re.compile(
    r"\bSAAR\b|\bseasonally\s+adjusted\s+annual(?:ized)?\s+"
    r"(?:rate|percent)\b",
    flags=re.I,
)
_GDP_COMPARATOR_TOKEN_RE = re.compile(
    r"\b(?:more\s+than|above|over|greater\s+than)\b",
    flags=re.I,
)
_GDP_NEGATED_COMPARATOR_RE = re.compile(
    r"\b(?:not|never|no|cannot|can't|cant|isn't|isnt|aren't|arent|wasn't|"
    r"wasnt|weren't|werent)(?:\s+|-)+(?:be\s+)?"
    r"(?:more\s+than|above|over|greater\s+than)\b",
    flags=re.I,
)
_GDP_STRICT_THRESHOLD_RE = re.compile(
    r"\b(?:more\s+than|above|over|greater\s+than)\s+"
    r"(-?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:%|percent\b)",
    flags=re.I,
)
_GDP_PERCENT_VALUE_RE = re.compile(
    r"(?<![\w.])(-?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:%|percent\b)",
    flags=re.I,
)
_GDP_UNSUPPORTED_COMPARATOR_RE = re.compile(
    r"\b(?:at\s+least|at\s+most|less\s+than|below|under|"
    r"no\s+more\s+than|equal\s+to|exactly|bucket|range)\b|"
    r"(?:<=|>=|<|>|=|≤|≥|＜|＞|＝|≦|≧|≠)",
    flags=re.I,
)
_GDP_RANGE_RE = re.compile(
    r"\b(?:between|from)\s+-?(?:\d+(?:\.\d+)?|\.\d+)\s*"
    r"(?:%|percent)\s+(?:and|to)\s+-?(?:\d+(?:\.\d+)?|\.\d+)"
    r"\s*(?:%|percent)\b|"
    r"\b-?(?:\d+(?:\.\d+)?|\.\d+)\s*%\s*(?:to|through|-|–|—|−)\s*"
    r"-?(?:\d+(?:\.\d+)?|\.\d+)\s*%\b",
    flags=re.I,
)
_GDP_TARGET_QUARTER_YEAR_RE = re.compile(
    r"\bQ([1-4])\s*(?:of\s*)?(\d{4})\b|\b(\d{4})\s*Q([1-4])\b",
    flags=re.I,
)


def _gdp_target_periods(text: str) -> set[tuple[int, int]]:
    periods: set[tuple[int, int]] = set()
    for match in _GDP_TARGET_QUARTER_YEAR_RE.finditer(text):
        quarter_text, year_text = match.group(1), match.group(2)
        if quarter_text is None or year_text is None:
            year_text, quarter_text = match.group(3), match.group(4)
        periods.add((int(quarter_text), int(year_text)))
    return periods


def _gdp_contract_clauses(text: str) -> tuple[str, ...]:
    return tuple(
        cleaned
        for clause in re.split(r"(?:[!?;]|\.(?!\d)|\n)+", text)
        if (cleaned := _clean(clause))
    )


def _gdp_contract_rule_fields(market: Any) -> tuple[str, ...]:
    return tuple(
        cleaned
        for attr in ("rules_primary", "rules_secondary")
        if (cleaned := _clean(getattr(market, attr, "")))
    )


def _strict_gdp_settlement_clause(
    clause: str,
) -> tuple[float, int, int] | None:
    if not _GDP_REAL_GDP_RE.search(clause) or not _GDP_SAAR_RE.search(clause):
        return None
    threshold_matches = list(_GDP_STRICT_THRESHOLD_RE.finditer(clause))
    percent_matches = list(_GDP_PERCENT_VALUE_RE.finditer(clause))
    periods = _gdp_target_periods(clause)
    if len(threshold_matches) != 1 or len(percent_matches) != 1 or len(periods) != 1:
        return None
    try:
        threshold = float(threshold_matches[0].group(1))
        percent_value = float(percent_matches[0].group(1))
    except ValueError:
        return None
    if not math.isfinite(threshold) or percent_value != threshold:
        return None
    token_matches = [
        *list(_GDP_REAL_GDP_RE.finditer(clause)),
        *list(_GDP_SAAR_RE.finditer(clause)),
        threshold_matches[0],
        *list(_GDP_TARGET_QUARTER_YEAR_RE.finditer(clause)),
    ]
    if max(match.end() for match in token_matches) - min(
        match.start() for match in token_matches
    ) > 220:
        return None
    quarter, year = next(iter(periods))
    return threshold, quarter, year


def _gdp_title_clause_matches_contract(
    clause: str,
    *,
    threshold: float,
    target_period: tuple[int, int],
) -> bool:
    if not _GDP_REAL_GDP_RE.search(clause):
        return True
    has_threshold_syntax = bool(
        _GDP_COMPARATOR_TOKEN_RE.search(clause)
        or _GDP_STRICT_THRESHOLD_RE.search(clause)
        or _GDP_PERCENT_VALUE_RE.search(clause)
    )
    if not has_threshold_syntax and not _GDP_SAAR_RE.search(clause):
        return True
    if _GDP_SAAR_RE.search(clause):
        parsed = _strict_gdp_settlement_clause(clause)
        return parsed == (threshold, *target_period)
    threshold_matches = list(_GDP_STRICT_THRESHOLD_RE.finditer(clause))
    percent_matches = list(_GDP_PERCENT_VALUE_RE.finditer(clause))
    if len(threshold_matches) != 1 or len(percent_matches) != 1:
        return False
    try:
        title_threshold = float(threshold_matches[0].group(1))
        title_percent = float(percent_matches[0].group(1))
    except ValueError:
        return False
    if (
        not math.isfinite(title_threshold)
        or title_percent != title_threshold
        or title_threshold != threshold
    ):
        return False
    periods = _gdp_target_periods(clause)
    return not periods or periods == {target_period}


def _parse_gdp_threshold_contract(market: Any) -> GDPThresholdContract | None:
    """Parse the narrow GDPNow countercheck contract surface, fail-closed."""
    contract_text = _market_text(market)
    rule_fields = _gdp_contract_rule_fields(market)
    if not contract_text or not rule_fields:
        return None
    if (
        _GDP_NEGATED_COMPARATOR_RE.search(contract_text)
        or _GDP_UNSUPPORTED_COMPARATOR_RE.search(contract_text)
        or _GDP_RANGE_RE.search(contract_text)
    ):
        return None
    target_periods = _gdp_target_periods(contract_text)
    if len(target_periods) != 1:
        return None
    target_period = next(iter(target_periods))
    parsed_rules: list[tuple[float, int, int]] = []
    for rule_text in rule_fields:
        for clause in _gdp_contract_clauses(rule_text):
            if not _GDP_REAL_GDP_RE.search(clause):
                continue
            if (
                _GDP_SAAR_RE.search(clause)
                or _GDP_COMPARATOR_TOKEN_RE.search(clause)
                or _GDP_STRICT_THRESHOLD_RE.search(clause)
                or _GDP_PERCENT_VALUE_RE.search(clause)
            ):
                parsed = _strict_gdp_settlement_clause(clause)
                if parsed is None:
                    return None
                parsed_rules.append(parsed)
    if not parsed_rules or len(set(parsed_rules)) != 1:
        return None
    threshold, target_quarter, target_year = parsed_rules[0]
    if (target_quarter, target_year) != target_period:
        return None
    for attr in ("title", "subtitle"):
        for clause in _gdp_contract_clauses(_clean(getattr(market, attr, ""))):
            if not _gdp_title_clause_matches_contract(
                clause,
                threshold=threshold,
                target_period=target_period,
            ):
                return None
    return GDPThresholdContract(
        metric_name=_GDPNOW_METRIC_NAME,
        metric_unit=_GDPNOW_METRIC_UNIT,
        comparator=">",
        threshold=threshold,
        target_quarter=target_quarter,
        target_year=target_year,
        contract_fingerprint=_contract_fingerprint(market),
    )


def _has_canonical_fred_gdpnow_provenance(evidence: ResearchEvidence) -> bool:
    return (
        _clean(evidence.source_class) == _GDPNOW_SOURCE_CLASS
        and _clean(evidence.source_name) == _GDPNOW_SOURCE_NAME
        and _clean(evidence.source_url) == _GDPNOW_CANONICAL_SOURCE_URL
        and _clean(evidence.claim_type) == "base_rate"
        and _clean(evidence.metric_name) == _GDPNOW_METRIC_NAME
        and _clean(evidence.metric_unit) == _GDPNOW_METRIC_UNIT
    )


def _has_finite_gdpnow_metric_value(evidence: ResearchEvidence) -> bool:
    try:
        value = float(evidence.metric_value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value)


def _has_valid_gdpnow_extraction_confidence(evidence: ResearchEvidence) -> bool:
    try:
        confidence = float(evidence.extraction_confidence)
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(confidence)
        and MIN_COUNTER_EVIDENCE_CONFIDENCE <= confidence <= 1.0
    )


def _has_min_finite_directional_confidence(item: ResearchEvidence) -> bool:
    try:
        confidence = float(item.supports_confidence)
    except (TypeError, ValueError):
        return False
    return math.isfinite(confidence) and confidence >= MIN_DIRECTIONAL_SUPPORT_CONFIDENCE


def _parse_utc_timestamp(value: str | None) -> datetime | None:
    cleaned = _clean(value)
    if not cleaned or not (
        cleaned.endswith("Z") or re.search(r"[+-]00:00$", cleaned)
    ):
        return None
    return _parse_timestamp(cleaned)


def _has_fresh_gdpnow_observation(
    evidence: ResearchEvidence,
    *,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    return (
        _parse_timestamp(evidence.published_at) is not None
        and _parse_utc_timestamp(evidence.retrieved_at) is not None
        and _is_fresh_decision_evidence(evidence, now=now)
    )


def _build_current_run_gdpnow_observation_context(
    evidence: ResearchEvidence,
    *,
    query: ResearchQuery,
    contract: GDPThresholdContract,
    now: datetime | None = None,
) -> _CurrentRunGDPNowObservationContext | None:
    """Bind one accepted FRED GDPNow observation to this run's exact contract."""
    query_text = _clean(query.query)
    if (
        not query_text
        or _clean(query.query_intent) != "base_rate"
        or not _query_mentions_gdpnow(query_text)
        or evidence.contract_fingerprint != contract.contract_fingerprint
        or not _has_canonical_fred_gdpnow_provenance(evidence)
        or not _has_finite_gdpnow_metric_value(evidence)
        or not _has_valid_gdpnow_extraction_confidence(evidence)
        or not _has_fresh_gdpnow_observation(evidence, now=now)
    ):
        return None
    observed_periods = _gdp_target_periods(
        f"{_clean(evidence.title)} {_clean(evidence.snippet)}"
    )
    if observed_periods and observed_periods != {
        (contract.target_quarter, contract.target_year)
    }:
        return None
    return _CurrentRunGDPNowObservationContext(
        query=query_text,
        contract_fingerprint=contract.contract_fingerprint,
        source_url=evidence.source_url,
        source_observation_date=evidence.published_at or "",
        retrieved_at=evidence.retrieved_at or "",
        metric_name=evidence.metric_name or "",
        metric_value=float(evidence.metric_value),
        metric_unit=evidence.metric_unit or "",
        extraction_confidence=float(evidence.extraction_confidence),
    )


def _gdpnow_context_matches_observation(
    context: _CurrentRunGDPNowObservationContext,
    evidence: ResearchEvidence,
    *,
    query: ResearchQuery,
    contract: GDPThresholdContract,
    now: datetime | None = None,
) -> bool:
    expected = _build_current_run_gdpnow_observation_context(
        evidence,
        query=query,
        contract=contract,
        now=now,
    )
    return expected is not None and expected == context


def _gdpnow_strictly_mismatches_provisional_side(
    value: float | None,
    *,
    contract: GDPThresholdContract,
    provisional_side: str | None,
) -> bool:
    try:
        observed_value = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(observed_value):
        return False
    side = _clean(provisional_side).lower()
    if side == "yes":
        return observed_value < contract.threshold
    if side == "no":
        return observed_value > contract.threshold
    return False


def _is_gdpnow_structured_evidence(item: ResearchEvidence) -> bool:
    return (
        _clean(item.metric_name) == _GDPNOW_METRIC_NAME
        or _clean(item.source_name) == _GDPNOW_SOURCE_NAME
    )


def _gdpnow_provisional_side_is_independently_justified(
    evidence: Sequence[ResearchEvidence],
    *,
    provisional_side: str | None,
    contract: GDPThresholdContract,
    yes_ask: float | None,
    no_ask: float | None,
    estimated_probability_yes: float | None,
    contract_ticker: str = "",
    queries: Sequence[ResearchQuery] = (),
    live_mode: bool = False,
    now: datetime | None = None,
) -> bool:
    """Require non-GDPNow support and an independently supplied executable edge."""
    if live_mode:
        return False
    side = _clean(provisional_side).lower()
    if side not in {"yes", "no"}:
        return False
    probability_yes = _coerce_probability(estimated_probability_yes)
    market_price = _market_price_for_side(side, yes_ask, no_ask)
    if (
        probability_yes is None
        or not math.isfinite(probability_yes)
        or market_price is None
    ):
        return False
    side_probability = probability_yes if side == "yes" else 1.0 - probability_yes
    side_edge = side_probability - market_price - 0.01
    if not math.isfinite(side_edge) or side_edge < 0.02:
        return False

    now = now or datetime.now(timezone.utc)
    non_gdpnow_evidence = [
        item for item in evidence if not _is_gdpnow_structured_evidence(item)
    ]
    relevance_spec = _contract_relevance_spec(contract_ticker, queries)
    supports = [
        item
        for item in non_gdpnow_evidence
        if item.supports_direction == side
        and item.contract_fingerprint == contract.contract_fingerprint
        and _has_min_finite_directional_confidence(item)
        and not _is_official_data_pending_evidence(item)
        and _is_fresh_decision_evidence(item, now=now)
        and (
            not relevance_spec.detected
            or _evidence_is_relevant_to_spec(item, relevance_spec)
        )
    ]
    return bool(supports) and not _has_unresolved_contradiction(non_gdpnow_evidence)


def _build_gdpnow_countercheck_evidence(
    observation: ResearchEvidence,
    *,
    context: _CurrentRunGDPNowObservationContext,
    contract: GDPThresholdContract,
    provisional_side: str,
) -> ResearchEvidence | None:
    """Derive one immutable counter-result from a verified current observation."""
    if not _gdpnow_context_matches_observation(
        context,
        observation,
        query=ResearchQuery(
            query=context.query,
            query_intent="base_rate",
            source_class="specialized_data",
        ),
        contract=contract,
    ):
        return None
    if not _gdpnow_strictly_mismatches_provisional_side(
        observation.metric_value,
        contract=contract,
        provisional_side=provisional_side,
    ):
        return None
    opposite = "no" if provisional_side == "yes" else "yes"
    value = float(observation.metric_value)
    direction_text = "below" if opposite == "no" else "above"
    title = (
        f"gdpnow-countercheck-v1: {value:.4g}% SAAR {direction_text} "
        f"{contract.threshold:.4g}% for Q{contract.target_quarter} "
        f"{contract.target_year}"
    )
    snippet = (
        f"gdpnow-countercheck-v1: current FRED GDPNow observation is "
        f"{value:.4g}% SAAR, {direction_text} the strict "
        f"{contract.threshold:.4g}% Q{contract.target_quarter} "
        f"{contract.target_year} threshold; it contradicts the provisional "
        f"{provisional_side.upper()} case."
    )
    return replace(
        observation,
        source_name=f"{observation.source_name or _GDPNOW_SOURCE_NAME} countercheck",
        title=title,
        snippet=snippet,
        claim_type="contradiction_check",
        supports_direction=opposite,
        supports_confidence=min(
            float(observation.extraction_confidence or 0.0),
            _gdpnow_confidence(value, contract.threshold),
        ),
        contract_fingerprint=contract.contract_fingerprint,
    )


def _is_gdpnow_derived_countercheck(item: ResearchEvidence) -> bool:
    return (
        item.claim_type == "contradiction_check"
        and item.metric_name == _GDPNOW_METRIC_NAME
        and _clean(item.title).startswith("gdpnow-countercheck-v1")
        and _clean(item.snippet).startswith("gdpnow-countercheck-v1")
    )


def _gdpnow_confidence(value: float, threshold: float) -> float:
    margin = abs(float(value) - float(threshold))
    return max(0.35, min(0.62, 0.42 + (margin / 4.0)))


_MONTH_NAME_TO_NUMBER = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _bls_cpi_search(
    query: ResearchQuery,
    *,
    now: datetime | None = None,
    timeout: float = 5.0,
) -> list[ResearchEvidence]:
    if not _query_mentions_cpi(query.query):
        return []
    target = _cpi_target_period_from_text(query.query)
    threshold = _cpi_threshold_from_text(query.query)
    if target is None:
        return []
    target_year, target_month = target
    start_year = target_year - 1 if target_month == 1 else target_year
    api_url = (
        "https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0"
        f"?startyear={start_year}&endyear={target_year}"
    )
    target_label = _period_label(target_year, target_month)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if now.date() < _cpi_expected_release_guard(target_year, target_month):
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="BLS CPI",
                source_url=f"{api_url}#pending-{target_year:04d}-{target_month:02d}",
                title=f"BLS CPI target month pending: {target_label}",
                snippet=(
                    f"Target {target_label} CPI is not yet expected in BLS "
                    "series CUSR0000SA0; keep the market queued until the "
                    "official release window has passed."
                ),
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                published_at=f"{target_year:04d}-{target_month:02d}-01",
                available_at=_cpi_expected_release_guard(
                    target_year,
                    target_month,
                ).isoformat(),
                retrieved_at=_utc_now_iso(),
                metric_name="cpi_official_data_pending",
                metric_unit="period_status",
                extraction_confidence=0.95,
            )
        ]
    request = urllib.request.Request(
        api_url,
        headers={"User-Agent": "kalshi-bot-research/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        payload = json.loads(response.read(300_000).decode("utf-8"))
    observations = _bls_cpi_observations(payload)
    if not observations:
        return []
    retrieved_at = _utc_now_iso()
    target_value = observations.get((target_year, target_month))
    previous_year, previous_month = _previous_month(target_year, target_month)
    previous_value = observations.get((previous_year, previous_month))
    if target_value is None or previous_value is None:
        latest_year, latest_month = max(observations)
        latest_label = _period_label(latest_year, latest_month)
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="BLS CPI",
                source_url=api_url,
                title=f"BLS CPI latest available month: {latest_label}",
                snippet=(
                    f"Target {target_label} CPI is not released in BLS series "
                    f"CUSR0000SA0; latest official month is {latest_label}."
                ),
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                published_at=f"{latest_year:04d}-{latest_month:02d}-01",
                retrieved_at=retrieved_at,
                metric_name="cpi_official_data_pending",
                metric_unit="period_status",
                extraction_confidence=0.95,
            )
        ]
    monthly_change = ((target_value / previous_value) - 1.0) * 100.0
    single_decimal = round(monthly_change + 1e-9, 1)
    direction = "neutral"
    confidence = 0.45
    threshold_text = ""
    if threshold is not None:
        direction = "yes" if single_decimal > threshold else "no"
        confidence = _cpi_confidence(single_decimal, threshold)
        threshold_text = (
            f" versus the {threshold:.1f}% market threshold, supporting "
            f"{direction.upper()}"
        )
    return [
        ResearchEvidence(
            source_class="official_primary",
            source_name="BLS CPI",
            source_url=api_url,
            title=f"BLS CPI monthly change for {target_label}: {single_decimal:.1f}%",
            snippet=(
                f"BLS CPI-U CUSR0000SA0 rose {single_decimal:.1f}% in "
                f"{target_label} on a single-decimal month-over-month basis"
                f"{threshold_text}."
            ),
            claim_type=query.query_intent,
            supports_direction=direction,
            supports_confidence=confidence,
            published_at=f"{target_year:04d}-{target_month:02d}-01",
            retrieved_at=retrieved_at,
            metric_name="cpi_monthly_change_single_decimal",
            metric_value=single_decimal,
            metric_unit="percent_mom_single_decimal",
            extraction_confidence=0.95,
        )
    ]


def _query_mentions_cpi(query: str) -> bool:
    text = _clean(query).lower()
    return "cpi" in text or "consumer price index" in text


def _nws_daily_climate_search(
    query: ResearchQuery,
    *,
    timeout: float = 5.0,
) -> list[ResearchEvidence]:
    if not _query_mentions_nws_high_temp(query.query):
        return []
    target_range = _weather_high_range_from_text(query.query)
    if target_range is None:
        return []
    target_date = _event_deadline_from_text(query.query)
    if target_date is None:
        return []
    source_url = _nws_daily_climate_url(query.query)
    if not source_url:
        return []
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "kalshi-bot-research/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        raw = response.read(300_000).decode("utf-8", errors="ignore")
    retrieved_at = _utc_now_iso()
    report_date = _nws_daily_climate_report_date_from_text(raw)
    report_available = _nws_report_date_available_at_retrieval(
        report_date,
        retrieved_at,
    )
    if report_date != target_date or not report_available:
        target_label = _fed_date_label(target_date)
        latest_label = _fed_date_label(report_date) if report_date else "unknown"
        timing_reason = (
            "The matching report date was not yet a completed prior local day."
            if report_date == target_date and not report_available
            else f"latest official report is {latest_label}."
        )
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="NWS Climatological Report",
                source_url=source_url,
                title=f"NWS Central Park daily maximum pending for {target_label}",
                snippet=(
                    f"NWS daily climate report date does not match target "
                    f"{target_label}. {timing_reason}"
                ),
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                published_at=report_date.isoformat() if report_date else None,
                available_at=_nws_expected_availability(target_date),
                retrieved_at=retrieved_at,
                metric_name="nws_daily_high_temp_pending",
                metric_unit="period_status",
                extraction_confidence=0.95 if report_date else 0.5,
            )
        ]
    high_temp = _nws_daily_high_temp_from_text(raw)
    if high_temp is None:
        return []
    low, high = target_range
    direction = "yes" if low <= high_temp <= high else "no"
    label = _fed_date_label(target_date)
    range_text = _weather_range_text(target_range)
    if query.query_intent in {"disconfirming", "contradiction_check"}:
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="NWS Climatological Report",
                source_url=f"{source_url}#high-{target_date.isoformat()}",
                title=(
                    f"NWS Central Park daily maximum countercheck for "
                    f"{label}: {high_temp:.0f}F"
                ),
                snippet=(
                    f"Disconfirming search checked the NWS Central Park daily "
                    f"maximum of {high_temp:.0f}F for {label} against the "
                    f"{range_text} market range; no contrary official "
                    "high-temperature fact was found."
                ),
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.95,
                published_at=target_date.isoformat(),
                retrieved_at=retrieved_at,
                metric_name="nws_daily_high_temp_f",
                metric_value=high_temp,
                metric_unit="fahrenheit",
                extraction_confidence=0.95,
            )
        ]
    return [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS Climatological Report",
            source_url=f"{source_url}#high-{target_date.isoformat()}",
            title=f"NWS Central Park daily maximum for {label}: {high_temp:.0f}F",
            snippet=(
                f"NWS Central Park climate report lists TODAY MAXIMUM "
                f"{high_temp:.0f}F for {label}, versus the {range_text} "
                f"market range, supporting {direction.upper()}."
            ),
            claim_type=query.query_intent,
            supports_direction=direction,
            supports_confidence=0.95,
            published_at=target_date.isoformat(),
            retrieved_at=retrieved_at,
            metric_name="nws_daily_high_temp_f",
            metric_value=high_temp,
            metric_unit="fahrenheit",
            extraction_confidence=0.95,
        )
    ]


def _nws_report_date_available_at_retrieval(
    report_date: date | None,
    retrieved_at: str,
) -> bool:
    retrieved = _parse_timestamp(retrieved_at)
    if report_date is None or retrieved is None:
        return False
    local_date = retrieved.astimezone(ZoneInfo("America/New_York")).date()
    return report_date < local_date


def _nws_expected_availability(target_date: date) -> str:
    local_midnight = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        tzinfo=ZoneInfo("America/New_York"),
    ) + timedelta(days=1)
    return local_midnight.astimezone(timezone.utc).isoformat()


def _query_mentions_nws_high_temp(query: str) -> bool:
    text = _clean(query).lower()
    return (
        "kxhighny" in text
        or (
            ("high temp" in text or "highest temperature" in text)
            and ("nyc" in text or "central park" in text or "new york" in text)
        )
        or (
            "climatological report" in text
            and "temperature" in text
            and ("central park" in text or "nyc" in text)
        )
    )


def _nws_daily_climate_url(query: str) -> str:
    text = _clean(query).lower()
    if "kxhighny" in text or "central park" in text or "nyc" in text or "new york" in text:
        return "https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC"
    return ""


def _nws_daily_high_temp_from_text(raw: str) -> float | None:
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))
    text = _clean(text)
    match = re.search(r"\bMAXIMUM\s+(-?\d+(?:\.\d+)?)[A-Z]*\b", text, flags=re.I)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _query_mentions_impeachment_expungement(query: str) -> bool:
    text = _clean(query).lower()
    return (
        ("expunge" in text or "expunges" in text or "expunging" in text)
        and "impeachment" in text
        and "trump" in text
    )


def _govinfo_impeachment_expungement_search(
    query: ResearchQuery,
    *,
    timeout: float = 5.0,
) -> list[ResearchEvidence]:
    if not _query_mentions_impeachment_expungement(query.query):
        return []
    bills = (
        (
            "H. Res. 24",
            "December 18, 2019",
            "https://www.govinfo.gov/content/pkg/BILLS-119hres24ih/html/BILLS-119hres24ih.htm",
        ),
        (
            "H. Res. 25",
            "January 13, 2021",
            "https://www.govinfo.gov/content/pkg/BILLS-119hres25ih/html/BILLS-119hres25ih.htm",
        ),
    )
    evidence: list[ResearchEvidence] = []
    for bill_label, impeachment_date, source_url in bills:
        request = urllib.request.Request(
            source_url,
            headers={"User-Agent": "kalshi-bot-research/1.0"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            raw = response.read(300_000).decode("utf-8", errors="ignore")
        text = _clean(html.unescape(re.sub(r"<[^>]+>", " ", raw)))
        stage = _govinfo_bill_stage(text)
        if stage is None:
            continue
        passed_house = stage == "passed_house"
        stage_text = "Passed House" if passed_house else "Introduced in House"
        evidence.append(
            ResearchEvidence(
                source_class="official_primary",
                source_name="GovInfo Congressional Bills",
                source_url=source_url,
                title=f"{bill_label} {stage_text}: Trump impeachment expungement",
                snippet=(
                    f"GovInfo lists {bill_label}, expunging the "
                    f"{impeachment_date} impeachment of Donald Trump, as "
                    f"{stage_text}; the market requires House passage."
                ),
                claim_type=query.query_intent,
                supports_direction="yes" if passed_house else "no",
                supports_confidence=0.9,
                retrieved_at=_utc_now_iso(),
                metric_name=(
                    "govinfo_bill_status_passed_house"
                    if passed_house
                    else "govinfo_bill_status_introduced"
                ),
                metric_unit="bill_status",
                extraction_confidence=0.9,
            )
        )
    return evidence


def _govinfo_bill_stage(text: str) -> str | None:
    lower = _clean(text).lower()
    if "passed house" in lower or "passed/agreed to in house" in lower:
        return "passed_house"
    if "introduced in house" in lower:
        return "introduced"
    return None


def _structured_direct_source_queries(
    market: Any,
    queries: Sequence[ResearchQuery],
) -> list[ResearchQuery]:
    market_text = _clean(
        f"{getattr(market, 'ticker', '')} {_market_text(market)} "
        f"cutoff {getattr(market, 'close_time', '')}"
    )
    if _getty_distinct_date_spec_from_text(market_text) is not None:
        return [
            ResearchQuery(
                market_text,
                "official_resolution",
                "resolution_source",
            )
        ]
    if _white_house_action_count_spec_from_text(market_text) is not None:
        return [
            ResearchQuery(
                market_text,
                "official_resolution",
                "official_primary",
            )
        ]
    direct_domains = {
        _domain_from_url(url)
        for url, _source_class, _claim_type in _direct_source_targets(market)
    }
    if "forecast.weather.gov" not in direct_domains:
        return []
    for query in queries:
        if (
            query.query_intent in {"official_resolution", "resolution_source"}
            and _query_mentions_nws_high_temp(query.query)
        ):
            return [
                ResearchQuery(
                    query.query,
                    "official_resolution",
                    "official_primary",
                )
            ]
    if not _query_mentions_nws_high_temp(market_text):
        return []
    return [
        ResearchQuery(
            market_text,
            "official_resolution",
            "official_primary",
        )
    ]


def _structured_official_search(query: ResearchQuery) -> list[ResearchEvidence]:
    if _getty_distinct_date_spec_from_text(query.query) is not None:
        return _getty_distinct_date_search(query)
    if _white_house_action_count_spec_from_text(query.query) is not None:
        return _white_house_presidential_actions_search(query)
    if _query_mentions_nws_high_temp(query.query):
        return _nws_daily_climate_search(query)
    return []


def _nws_daily_climate_report_date_from_text(raw: str) -> date | None:
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))
    text = _clean(text)
    month_pattern = "|".join(_MONTH_NAME_TO_NUMBER)
    match = re.search(
        rf"\b(?:for|summary for)\s+({month_pattern})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b",
        text,
        flags=re.I,
    )
    if not match:
        return None
    month = _MONTH_NAME_TO_NUMBER[match.group(1).lower()]
    day = int(match.group(2))
    year = int(match.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _weather_high_range_from_text(text: str) -> tuple[float, float] | None:
    cleaned = _clean(text)
    match = re.search(
        r"\b(?:between|be)\s+(-?\d+(?:\.\d+)?)\s*(?:-|to|and)\s*(-?\d+(?:\.\d+)?)",
        cleaned,
        flags=re.I,
    )
    if match:
        try:
            low = float(match.group(1))
            high = float(match.group(2))
            return (min(low, high), max(low, high))
        except ValueError:
            return None
    match = re.search(r"\bB(-?\d+)\.5\b", cleaned, flags=re.I)
    if match:
        try:
            low = float(match.group(1))
        except ValueError:
            return None
        return (low, low + 1.0)
    match = re.search(
        r"\b(?:at least|above|over|greater than)\s+(-?\d+(?:\.\d+)?)\s*(?:°|degrees?|f\b|fahrenheit)?",
        cleaned,
        flags=re.I,
    )
    if match:
        try:
            threshold = float(match.group(1))
        except ValueError:
            return None
        return (threshold, float("inf"))
    match = re.search(
        r"\b(?:below|under|less than)\s+(-?\d+(?:\.\d+)?)\s*(?:°|degrees?|f\b|fahrenheit)?",
        cleaned,
        flags=re.I,
    )
    if match:
        try:
            threshold = float(match.group(1))
        except ValueError:
            return None
        return (float("-inf"), threshold - 1e-9)
    match = re.search(r">\s*=?\s*(-?\d+(?:\.\d+)?)\s*(?:°|degrees?|f\b|fahrenheit)?", cleaned, flags=re.I)
    if match:
        try:
            threshold = float(match.group(1))
        except ValueError:
            return None
        return (threshold, float("inf"))
    match = re.search(r"<\s*(-?\d+(?:\.\d+)?)\s*(?:°|degrees?|f\b|fahrenheit)?", cleaned, flags=re.I)
    if match:
        try:
            threshold = float(match.group(1))
        except ValueError:
            return None
        return (float("-inf"), threshold - 1e-9)
    match = re.search(r"\bT(-?\d+(?:\.\d+)?)\b", cleaned, flags=re.I)
    if match:
        try:
            threshold = float(match.group(1))
        except ValueError:
            return None
        return (threshold, float("inf"))
    return None


def _weather_range_text(target_range: tuple[float, float]) -> str:
    low, high = target_range
    if high == float("inf"):
        return f"at least {low:.0f}F"
    if low == float("-inf"):
        return f"below {high + 1e-9:.0f}F"
    return f"{low:.0f}-{high:.0f}F"


def _economic_stat_pending_search(
    query: ResearchQuery,
    *,
    now: datetime | None = None,
) -> list[ResearchEvidence]:
    if not _query_mentions_economic_stat(query.query):
        return []
    target_period = _economic_stat_target_period(query.query)
    if target_period is None:
        return []
    period_label, period_key, pending_until = target_period
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if pending_until < now.date():
        return []
    stat_label = _economic_stat_label_from_text(query.query)
    source_name, source_url = _economic_stat_pending_source(query.query, period_key)
    return [
        ResearchEvidence(
            source_class="official_primary",
            source_name=source_name,
            source_url=source_url,
            title=f"Economic-stat release pending for {stat_label} ({period_label})",
            snippet=(
                f"The economic-stat release window for {stat_label} "
                f"({period_label}) is not settled yet; keep the market queued "
                "until official/source data is available."
            ),
            claim_type=query.query_intent,
            supports_direction="neutral",
            supports_confidence=0.0,
            published_at=period_key,
            available_at=pending_until.isoformat(),
            retrieved_at=_utc_now_iso(),
            metric_name="economic_stat_data_pending",
            metric_unit="period_status",
            extraction_confidence=0.9,
        )
    ]


def _query_mentions_economic_stat(query: str) -> bool:
    text = _clean(query).lower()
    plain_text = re.sub(r"[*_`]+", "", text)
    has_south_africa_trade_balance_stat = _is_south_africa_trade_balance_market(text)
    has_pce_stat = (
        "pce inflation" in text
        or "core pce" in text
        or "personal consumption expenditures price index" in text
    )
    has_effective_tariff_stat = (
        "effective tariff rate" in text
        or "customs duties collected" in text
        or "b235rc1q027sbea" in text
        or "a255rc1q027sbea" in text
    )
    has_gdp_ticker = (
        re.search(r"\bKX[A-Z0-9]{1,6}GDP[A-Z0-9-]*", text, flags=re.I)
        is not None
    )
    has_gdp_growth_rate = (
        "gdp growth rate" in text
        or "gross domestic product growth rate" in text
    )
    has_gdp_stat_phrase = (
        "real gdp" in plain_text
        or "nominal gdp" in plain_text
        or "gross domestic product" in text
    )
    has_fred_gdp_source = (
        "fred.stlouisfed.org" in text
        or "federal reserve bank of st" in text
    ) and "gdp growth" in text
    has_economic_source = (
        "trading economics" in text
        or "tradingeconomics.com" in text
        or has_gdp_ticker
        or has_gdp_growth_rate
        or ("bea.gov" in text and has_gdp_stat_phrase)
        or ("bea" in text and has_gdp_stat_phrase)
        or has_fred_gdp_source
        or ("bea.gov" in text and has_pce_stat)
        or has_pce_stat
        or has_effective_tariff_stat
        or has_south_africa_trade_balance_stat
    )
    if not has_economic_source:
        return False
    return (
        has_gdp_growth_rate
        or "gdp growth" in text
        or "gross domestic product" in text
        or has_gdp_stat_phrase
        or has_gdp_ticker
        or has_fred_gdp_source
        or has_pce_stat
        or has_effective_tariff_stat
        or has_south_africa_trade_balance_stat
    )


def _economic_stat_pending_source(text: str, period_key: str) -> tuple[str, str]:
    cleaned = _clean(text).lower()
    period_anchor = period_key.lower()
    if _is_south_africa_trade_balance_market(cleaned):
        return (
            "SARS trade statistics",
            f"https://www.sars.gov.za/customs-and-excise/trade-statistics/#pending-{period_anchor}",
        )
    if (
        "pce inflation" in cleaned
        or "core pce" in cleaned
        or "personal consumption expenditures price index" in cleaned
    ):
        return (
            "BEA PCE data",
            f"https://www.bea.gov/data/income-saving/personal-income#pending-{period_anchor}",
        )
    if (
        "effective tariff rate" in cleaned
        or "customs duties collected" in cleaned
        or "b235rc1q027sbea" in cleaned
        or "a255rc1q027sbea" in cleaned
    ):
        return (
            "FRED/BEA economic data",
            f"https://fred.stlouisfed.org/#pending-{period_anchor}",
        )
    if "fred.stlouisfed.org" in cleaned or "federal reserve bank of st" in cleaned:
        return (
            "FRED economic data",
            f"https://fred.stlouisfed.org/#pending-{period_anchor}",
        )
    plain_cleaned = re.sub(r"[*_`]+", "", cleaned)
    if (
        ("bea.gov" in cleaned or re.search(r"\bBEA\b", cleaned, flags=re.I))
        and (
            "real gdp" in plain_cleaned
            or "nominal gdp" in plain_cleaned
            or "gross domestic product" in cleaned
        )
    ):
        return (
            "BEA GDP data",
            f"https://www.bea.gov/data/gdp/gross-domestic-product#pending-{period_anchor}",
        )
    return (
        "Economic calendar",
        f"https://tradingeconomics.com/#pending-{period_anchor}",
    )


def _economic_stat_target_period(text: str) -> tuple[str, str, date] | None:
    target_date = _event_deadline_from_text(text)
    if target_date is not None:
        return (
            _fed_date_label(target_date),
            target_date.isoformat(),
            target_date,
        )
    quarter = _economic_stat_quarter_from_text(text)
    if quarter is None:
        month = _economic_stat_month_from_text(text)
        if month is None:
            return None
        year, month_number = month
        month_label = _period_label(year, month_number)
        next_year, next_month = (
            (year + 1, 1) if month_number == 12 else (year, month_number + 1)
        )
        month_end = date(next_year, next_month, 1) - timedelta(days=1)
        return (
            month_label,
            f"{year:04d}-{month_number:02d}",
            month_end + timedelta(days=60),
        )
    year, quarter_number = quarter
    quarter_label = f"Q{quarter_number} {year}"
    quarter_end_month = quarter_number * 3
    quarter_end = date(year, quarter_end_month, 1)
    if quarter_end_month in {3, 12}:
        next_month = date(year + (1 if quarter_end_month == 12 else 0), 1 if quarter_end_month == 12 else quarter_end_month + 1, 1)
    else:
        next_month = date(year, quarter_end_month + 1, 1)
    quarter_end = next_month - timedelta(days=1)
    return (
        quarter_label,
        f"{year}-Q{quarter_number}",
        quarter_end + timedelta(days=60),
    )


def _economic_stat_quarter_from_text(text: str) -> tuple[int, int] | None:
    cleaned = _clean(text)
    match = re.search(
        r"\bKX[A-Z0-9]*GDP[A-Z0-9]*-(\d{2})Q([1-4])\b",
        cleaned,
        flags=re.I,
    )
    if match:
        return 2000 + int(match.group(1)), int(match.group(2))
    match = re.search(r"\bQ([1-4])\s+(20\d{2})\b", cleaned, flags=re.I)
    if match:
        return int(match.group(2)), int(match.group(1))
    return None


def _economic_stat_month_from_text(text: str) -> tuple[int, int] | None:
    match = re.search(
        r"\b("
        + "|".join(_MONTH_NAME_TO_NUMBER)
        + r")\s+(20\d{2})\b",
        _clean(text),
        flags=re.I,
    )
    if not match:
        return None
    return int(match.group(2)), _MONTH_NAME_TO_NUMBER[match.group(1).lower()]


def _economic_stat_label_from_text(text: str) -> str:
    cleaned = re.sub(r"\bsite:(?:tradingeconomics\.com|bea\.gov)\b", " ", _clean(text), flags=re.I)
    plain_cleaned = re.sub(r"[*_`]+", "", cleaned)
    cleaned = re.sub(
        r"\bKX[A-Z0-9]+(?:-[A-Z0-9.]+)+\b",
        " ",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"\bTrading Economics\b", " ", cleaned, flags=re.I)
    match = re.search(
        r"\bWill\s+(.+?)\s+(?:for\s+Q[1-4]\s+20\d{2}\s+)?be\s+",
        cleaned,
        flags=re.I,
    )
    label = _clean(match.group(1)) if match else ""
    if not label:
        match = re.search(
            r"\bWill\s+(.+?\b(?:real|nominal)?\s*GDP)\s+"
            r"(?:increase|decrease|grow|rise|fall|shrink)\b",
            plain_cleaned,
            flags=re.I,
        )
        label = _clean(match.group(1)) if match else ""
    if not label:
        match = re.search(
            r"\b([A-Z][A-Za-z ]{1,40}\s+GDP\s+growth\s+rate(?:\s+\w+){0,3})\b",
            cleaned,
            flags=re.I,
        )
        label = _clean(match.group(1)) if match else ""
    if not label and re.search(r"\b(?:core\s+)?pce inflation\b", cleaned, flags=re.I):
        label = "core PCE inflation" if "core pce" in cleaned.lower() else "PCE inflation"
    if not label and (
        "effective tariff rate" in cleaned.lower()
        or "customs duties collected" in cleaned.lower()
    ):
        label = "US effective tariff rate"
    label = re.sub(r"^(the|a|an)\s+", "", label, flags=re.I)
    label = re.sub(r"^rate of\s+", "", label, flags=re.I)
    return _clean(label)[:120] or "GDP growth rate"


def _cpi_target_period_from_text(text: str) -> tuple[int, int] | None:
    match = re.search(
        r"\b("
        + "|".join(_MONTH_NAME_TO_NUMBER)
        + r")\s+(20\d{2})\b",
        _clean(text),
        flags=re.I,
    )
    if not match:
        return None
    return int(match.group(2)), _MONTH_NAME_TO_NUMBER[match.group(1).lower()]


def _cpi_threshold_from_text(text: str) -> float | None:
    cleaned = _clean(text)
    if not re.search(
        r"\b(?:cpi|consumer price index|consumer prices|KXCPI)\b",
        cleaned,
        flags=re.I,
    ):
        return None
    match = re.search(
        r"\b(?:more than|above|over|greater than|at least)\s+(-?\d+(?:\.\d+)?)\s*%",
        cleaned,
        flags=re.I,
    )
    if not match:
        match = re.search(r"\bT(-?\d+(?:\.\d+)?)\b", cleaned, flags=re.I)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _bls_cpi_observations(payload: Any) -> dict[tuple[int, int], float]:
    out: dict[tuple[int, int], float] = {}
    if not isinstance(payload, dict):
        return out
    series = payload.get("Results", {}).get("series", [])
    if not series:
        return out
    for row in series[0].get("data", []) if isinstance(series[0], dict) else []:
        try:
            year = int(row.get("year"))
            period = str(row.get("period") or "")
            if not period.startswith("M"):
                continue
            month = int(period[1:])
            value = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        out[(year, month)] = value
    return out


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month <= 1:
        return year - 1, 12
    return year, month - 1


def _cpi_expected_release_guard(year: int, month: int) -> date:
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return date(next_year, next_month, 20)


def _period_label(year: int, month: int) -> str:
    names = {number: name.title() for name, number in _MONTH_NAME_TO_NUMBER.items()}
    return f"{names.get(month, str(month))} {year}"


def _cpi_confidence(value: float, threshold: float) -> float:
    margin = abs(float(value) - float(threshold))
    return max(0.8, min(0.95, 0.82 + (margin / 2.0)))


def _fed_policy_search(
    query: ResearchQuery,
    *,
    now: datetime | None = None,
) -> list[ResearchEvidence]:
    if not _query_mentions_fed_policy(query.query):
        return []
    meeting_date = _fed_meeting_date_from_text(query.query) or _event_deadline_from_text(
        query.query
    )
    if meeting_date is None:
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if meeting_date > now.date():
        label = _fed_date_label(meeting_date)
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="Federal Reserve",
                source_url=(
                    "https://www.federalreserve.gov/monetarypolicy/"
                    f"fomccalendars.htm#pending-{meeting_date.isoformat()}"
                ),
                title=f"FOMC decision pending for {label}",
                snippet=(
                    f"The FOMC meeting date {label} is in the future; the "
                    "Federal Reserve has not released the post-meeting target "
                    "range decision yet."
                ),
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                published_at=meeting_date.isoformat(),
                available_at=meeting_date.isoformat(),
                retrieved_at=_utc_now_iso(),
                metric_name="fed_decision_pending",
                metric_unit="period_status",
                extraction_confidence=0.95,
            )
        ]
    return []


def _query_mentions_fed_policy(query: str) -> bool:
    text = _clean(query).lower()
    return (
        "federal funds rate" in text
        or "upper bound" in text and "fed" in text
        or "fomc" in text
    )


def _bank_of_israel_policy_search(
    query: ResearchQuery,
    *,
    now: datetime | None = None,
) -> list[ResearchEvidence]:
    if not _query_mentions_bank_of_israel_policy(query.query):
        return []
    meeting_date = _fed_meeting_date_from_text(query.query) or _event_deadline_from_text(
        query.query
    )
    if meeting_date is None:
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if meeting_date > now.date():
        label = _fed_date_label(meeting_date)
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="Bank of Israel",
                source_url=(
                    "https://www.boi.org.il/en/communication-and-publications/"
                    f"press-releases/#pending-{meeting_date.isoformat()}"
                ),
                title=f"Bank of Israel rate decision pending for {label}",
                snippet=(
                    f"The Bank of Israel Monetary Committee meeting date {label} "
                    "is in the future; the post-meeting policy-rate decision has "
                    "not been released yet."
                ),
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                published_at=meeting_date.isoformat(),
                available_at=meeting_date.isoformat(),
                retrieved_at=_utc_now_iso(),
                metric_name="bank_of_israel_decision_pending",
                metric_unit="period_status",
                extraction_confidence=0.95,
            )
        ]
    return []


def _query_mentions_bank_of_israel_policy(query: str) -> bool:
    text = _clean(query).lower()
    return (
        "bank of israel" in text
        and (
            "monetary committee" in text
            or "policy rate" in text
            or "hike" in text
            or "cut" in text
            or "maintain" in text
        )
    )


def _fed_meeting_date_from_text(text: str) -> date | None:
    cleaned = _clean(text)
    month_pattern = "|".join(_MONTH_NAME_TO_NUMBER)
    match = re.search(
        rf"\b({month_pattern}|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
        r"\.?\s+(\d{1,2}),?\s+(20\d{2})\b",
        cleaned,
        flags=re.I,
    )
    if not match:
        return None
    month_text = match.group(1).lower().rstrip(".")
    aliases = {
        "jan": "january",
        "feb": "february",
        "mar": "march",
        "apr": "april",
        "jun": "june",
        "jul": "july",
        "aug": "august",
        "sep": "september",
        "sept": "september",
        "oct": "october",
        "nov": "november",
        "dec": "december",
    }
    month_name = aliases.get(month_text, month_text)
    month = _MONTH_NAME_TO_NUMBER.get(month_name)
    if month is None:
        return None
    try:
        return datetime(int(match.group(3)), month, int(match.group(2))).date()
    except ValueError:
        return None


def _fed_date_label(value: date) -> str:
    return f"{_period_label(value.year, value.month).split()[0]} {value.day}, {value.year}"


def _treasury_yield_search(
    query: ResearchQuery,
    *,
    now: datetime | None = None,
) -> list[ResearchEvidence]:
    if not _query_mentions_treasury_yield(query.query):
        return []
    target_date = _event_deadline_from_text(query.query)
    if target_date is None:
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if target_date < now.date():
        return []
    label = _fed_date_label(target_date)
    source_url = (
        "https://home.treasury.gov/resource-center/data-chart-center/"
        "interest-rates/TextView?type=daily_treasury_yield_curve"
        f"#pending-{target_date.isoformat()}"
    )
    return [
        ResearchEvidence(
            source_class="official_primary",
            source_name="U.S. Treasury",
            source_url=source_url,
            title=f"Treasury yield curve data pending for {label}",
            snippet=(
                f"The Treasury daily yield curve data for {label} is not "
                "settled yet; keep the market queued until the official "
                "data release is available."
            ),
            claim_type=query.query_intent,
            supports_direction="neutral",
            supports_confidence=0.0,
            published_at=target_date.isoformat(),
            available_at=target_date.isoformat(),
            retrieved_at=_utc_now_iso(),
            metric_name="treasury_yield_data_pending",
            metric_unit="period_status",
            extraction_confidence=0.95,
        )
    ]


def _query_mentions_treasury_yield(query: str) -> bool:
    text = _clean(query).lower()
    return (
        "kxtnoted" in text
        or "treasury note" in text
        or "treasury notes" in text
        or "yield curve par rate" in text
        or (
            "treasury" in text
            and "yield" in text
        )
    )


def _truth_social_event_search(
    query: ResearchQuery,
    *,
    now: datetime | None = None,
) -> list[ResearchEvidence]:
    if not _query_mentions_truth_social_event(query.query):
        return []
    deadline = _event_deadline_from_text(query.query) or _month_end_from_text(
        query.query
    )
    if deadline is None:
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if deadline >= now.date():
        label = _fed_date_label(deadline)
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="Truth Social",
                source_url=(
                    "https://truthsocial.com/@realDonaldTrump"
                    f"#pending-{deadline.isoformat()}"
                ),
                title=f"Truth Social event window pending through {label}",
                snippet=(
                    f"The Truth Social event window remains open "
                    f"through {label}; final official post count is not settled yet."
                ),
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                published_at=deadline.isoformat(),
                available_at=deadline.isoformat(),
                retrieved_at=_utc_now_iso(),
                metric_name="truth_social_window_pending",
                metric_unit="period_status",
                extraction_confidence=0.9,
            )
        ]
    return []


def _query_mentions_truth_social_event(query: str) -> bool:
    text = _clean(query).lower()
    return "truth social" in text and (
        "endorse" in text
        or "kxtrumpendorsements" in text
        or "kxtrumpdelete" in text
        or ("trump truths" in text and "delete" in text)
        or ("truths deleted" in text)
    )


def _month_end_from_text(text: str) -> date | None:
    cleaned = _clean(text)
    month_pattern = "|".join(_MONTH_NAME_TO_NUMBER)
    candidates = [
        (match.start(), match.group(1), match.group(2))
        for match in re.finditer(
            rf"\b({month_pattern}|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
            r"\.?\s+(20\d{2})\b",
            cleaned,
            flags=re.I,
        )
    ]
    candidates.extend(
        (match.start(), match.group(2), match.group(1))
        for match in re.finditer(
            rf"\b(20\d{{2}})\s+({month_pattern}|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b",
            cleaned,
            flags=re.I,
        )
    )
    if not candidates:
        return None
    _, raw_month, raw_year = max(candidates, key=lambda item: item[0])
    month_token = raw_month.lower().rstrip(".")
    month_aliases = {
        "jan": "january",
        "feb": "february",
        "mar": "march",
        "apr": "april",
        "jun": "june",
        "jul": "july",
        "aug": "august",
        "sep": "september",
        "sept": "september",
        "oct": "october",
        "nov": "november",
        "dec": "december",
    }
    month_name = month_aliases.get(month_token, month_token)
    month_number = _MONTH_NAME_TO_NUMBER.get(month_name)
    if month_number is None:
        return None
    year = int(raw_year)
    if month_number == 12:
        return date(year, 12, 31)
    return date(year, month_number + 1, 1) - timedelta(days=1)


def _event_window_pending_search(
    query: ResearchQuery,
    *,
    now: datetime | None = None,
) -> list[ResearchEvidence]:
    if not _query_mentions_generic_event_window(query.query):
        return []
    confirmation_event_window = _query_mentions_confirmation_event_window(query.query)
    deadline = _event_deadline_from_text(
        query.query,
        prefer_textual_date=confirmation_event_window,
    ) or _monthly_event_window_end_from_text(query.query)
    if deadline is None and confirmation_event_window:
        deadline = _month_end_from_text(query.query)
    if deadline is None:
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if _event_deadline_is_exclusive_before_date(query.query) and deadline <= now.date():
        return []
    if deadline < now.date():
        return []
    label = _fed_date_label(deadline)
    return [
        ResearchEvidence(
            source_class="official_primary",
            source_name="Event window",
            source_url=f"https://kalshi.com/#pending-{deadline.isoformat()}",
            title=f"Event window pending through {label}",
            snippet=(
                f"The event window remains open through {label}; final settlement "
                "reporting is not complete yet."
            ),
            claim_type=query.query_intent,
            supports_direction="neutral",
            supports_confidence=0.0,
            published_at=deadline.isoformat(),
            available_at=deadline.isoformat(),
            retrieved_at=_utc_now_iso(),
            metric_name="event_window_pending",
            metric_unit="period_status",
            extraction_confidence=0.9,
        )
    ]


def _monthly_event_window_end_from_text(text: str) -> date | None:
    cleaned = _clean(text).lower()
    if not (
        "visit" in cleaned
        or "visited" in cleaned
        or "visits" in cleaned
        or "visitarea" in cleaned
        or "pardon" in cleaned
        or "pardoned" in cleaned
        or "pardons" in cleaned
        or "commute" in cleaned
        or "commutes" in cleaned
        or "clemency" in cleaned
        or "reprieve" in cleaned
    ):
        return None
    return _month_end_from_text(text)


def _event_deadline_is_exclusive_before_date(text: str) -> bool:
    cleaned = _clean(text)
    month_pattern = "|".join(_MONTH_NAME_TO_NUMBER)
    return (
        re.search(
            rf"\bbefore\s+(?:{month_pattern}|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
            r"\.?\s+\d{1,2},?\s+20\d{2}\b",
            cleaned,
            flags=re.I,
        )
        is not None
    )


def _query_mentions_generic_event_window(query: str) -> bool:
    text = _clean(query).lower()
    if "truth social" in text:
        return False
    if _query_mentions_sports_event_window(text):
        return True
    if _query_mentions_market_data_event_window(text):
        return True
    if (
        "budget resolution" in text
        and "senate" in text
        and (
            " before " in text
            or " through " in text
            or " by " in text
            or _event_deadline_from_text(text) is not None
        )
    ):
        return True
    if (
        ("gubernatorial election" in text or "republican governors" in text)
        and ("called" in text or "certified" in text)
        and _event_deadline_from_text(text) is not None
    ):
        return True
    if (
        "jerome powell" in text
        and "board of governors current member" in text
        and _event_deadline_from_text(text) is not None
    ):
        return True
    if (
        "pennsylvania defense and innovation summit" in text
        and _event_deadline_from_text(text) is not None
    ):
        return True
    if _query_mentions_confirmation_event_window(text):
        return True
    if _query_mentions_office_departure(text) and (
        " before " in text
        or " through " in text
        or " by " in text
        or _event_deadline_from_text(text) is not None
    ):
        return True
    return (
        "kxvisit" in text
        or "kxtrumpnumstates" in text
        or "kxpardonstrump" in text
        or "physically visited" in text
        or "physically visit" in text
        or "distinct us states" in text
        or "pardon" in text
        or "pardoned" in text
        or "pardons" in text
        or "commute" in text
        or "clemency" in text
        or "reprieve" in text
        or re.search(r"\bvisit(?:ed)?\b", text) is not None
        or re.search(r"\bmeet(?:ing)?\b", text) is not None
    ) and (
        " before " in text
        or " through " in text
        or " by " in text
        or _event_deadline_from_text(text) is not None
        or _monthly_event_window_end_from_text(text) is not None
    )


def _query_mentions_confirmation_event_window(query: str) -> bool:
    text = _clean(query).lower()
    if _event_deadline_from_text(text) is None and not (
        " before " in text or " by " in text or " through " in text
    ):
        return False
    return (
        "confirmed as" in text
        or "senate confirmed" in text
        or "senate confirmation" in text
        or "confirmation vote" in text
        or "confirmation hearing" in text
        or "nominee" in text
        or "nomination" in text
        or "nominated as" in text
    ) and (
        "senate" in text
        or "director" in text
        or "secretary" in text
        or "administrator" in text
        or "ambassador" in text
        or "chair" in text
        or "commissioner" in text
        or "confirmed as" in text
    )


def _query_mentions_sports_event_window(query: str) -> bool:
    text = _clean(query).lower()
    return (
        "kxwcadvance" in text
        or (
            "world cup" in text
            and (
                "advance" in text
                or "round of 32" in text
                or "soccer tie" in text
            )
        )
        or "kxnpbgame" in text
        or (
            ("npb game" in text or "baseball" in text)
            and ("winner" in text or "wins" in text or "scheduled" in text)
        )
    ) and _event_deadline_from_text(text) is not None


def _query_mentions_market_data_event_window(query: str) -> bool:
    text = _clean(query).lower()
    return (
        "kxnasdaq100" in text
        or "kxsp500" in text
        or "nasdaq-100" in text
        or "nasdaq 100" in text
        or "s&p 500" in text
        or "spx" in text
    ) and (
        "end-of-day" in text
        or "end of day" in text
        or "4pm" in text
        or "index value" in text
        or "between" in text
    ) and _event_deadline_from_text(text) is not None


def _event_deadline_from_text(
    text: str,
    *,
    prefer_textual_date: bool = False,
) -> date | None:
    cleaned = _clean(text)
    ticker_match = re.search(
        r"\bKX[A-Z0-9]+-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(?:[A-Z0-9]*)?\b",
        cleaned,
        flags=re.I,
    )
    ticker_deadline = None
    if ticker_match:
        month_name = {
            "JAN": "january",
            "FEB": "february",
            "MAR": "march",
            "APR": "april",
            "MAY": "may",
            "JUN": "june",
            "JUL": "july",
            "AUG": "august",
            "SEP": "september",
            "OCT": "october",
            "NOV": "november",
            "DEC": "december",
        }[ticker_match.group(2).upper()]
        try:
            ticker_deadline = date(
                2000 + int(ticker_match.group(1)),
                _MONTH_NAME_TO_NUMBER[month_name],
                int(ticker_match.group(3)),
            )
        except ValueError:
            ticker_deadline = None
    month_pattern = "|".join(_MONTH_NAME_TO_NUMBER)
    matches = list(re.finditer(
        rf"\b({month_pattern}|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
        r"\.?\s+(\d{1,2}),?\s+(20\d{2})\b",
        cleaned,
        flags=re.I,
    ))
    parsed_dates = [
        parsed
        for match in matches
        if (parsed := _fed_meeting_date_from_text(match.group(0))) is not None
    ]
    textual_deadline = max(parsed_dates) if parsed_dates else None
    if prefer_textual_date:
        return textual_deadline or ticker_deadline
    return ticker_deadline or textual_deadline


async def _run_generic_search(query: ResearchQuery) -> list[ResearchEvidence]:
    circuit = _get_generic_search_circuit()
    return await circuit.run(
        lambda: _rss_search(query),
        lambda: _duckduckgo_lite_search(query),
    )


async def default_search_provider(query: ResearchQuery) -> list[ResearchEvidence]:
    pending_evidence: list[ResearchEvidence] = []

    def classify_structured(
        candidate: list[ResearchEvidence],
    ) -> list[ResearchEvidence] | None:
        if not candidate:
            return None
        if _has_official_data_pending(candidate):
            pending_evidence.extend(candidate)
            return None
        return candidate

    for provider in (
        _truth_social_event_search,
        _bank_of_israel_policy_search,
        _treasury_yield_search,
        _fed_policy_search,
    ):
        structured = classify_structured(provider(query))
        if structured is not None:
            return structured
        if pending_evidence:
            break

    if not pending_evidence:
        for provider in (
            _getty_distinct_date_search,
            _white_house_presidential_actions_search,
            _bls_cpi_search,
            _nws_daily_climate_search,
            _govinfo_impeachment_expungement_search,
            _gdpnow_search,
        ):
            structured = classify_structured(
                await asyncio.to_thread(provider, query)
            )
            if structured is not None:
                return structured
            if pending_evidence:
                break

    if not pending_evidence:
        structured = classify_structured(_economic_stat_pending_search(query))
        if structured is not None:
            return structured

    if not pending_evidence and _query_site_domain(query.query) == "federalregister.gov":
        federal_register_evidence = await asyncio.to_thread(_federal_register_search, query)
        if federal_register_evidence:
            return federal_register_evidence

    if not pending_evidence:
        structured = classify_structured(_event_window_pending_search(query))
        if structured is not None:
            return structured

    search_evidence = await _run_generic_search(query)
    return [*pending_evidence, *search_evidence]



def _extract_page_text(raw: bytes) -> tuple[str, str]:
    text = raw.decode("utf-8", errors="ignore")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
    title = html.unescape(_clean(title_match.group(1))) if title_match else ""
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    snippet = html.unescape(_clean(body))[:800]
    return title, snippet


def _fetch_direct_source(
    url: str,
    source_class: str,
    claim_type: str,
    *,
    timeout: float = 5.0,
) -> ResearchEvidence | None:
    cleaned_url = _clean(url)
    if not cleaned_url or not _should_direct_fetch_source(cleaned_url, claim_type):
        return None
    request = urllib.request.Request(
        cleaned_url,
        headers={"User-Agent": "kalshi-bot-research/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        raw = response.read(300_000)
    title, snippet = _extract_page_text(raw)
    domain = _domain_from_url(cleaned_url)
    return ResearchEvidence(
        source_class=source_class,
        source_name=domain or cleaned_url,
        source_url=cleaned_url,
        title=title or cleaned_url,
        snippet=snippet,
        claim_type=claim_type,
        supports_direction="neutral",
        supports_confidence=0.0,
        retrieved_at=_utc_now_iso(),
    )


async def default_direct_fetcher(
    url: str,
    source_class: str,
    claim_type: str,
) -> ResearchEvidence | None:
    return await asyncio.to_thread(
        _fetch_direct_source,
        url,
        source_class,
        claim_type,
    )


def _direct_source_targets(market: Any) -> list[tuple[str, str, str]]:
    targets: list[tuple[str, str, str]] = []
    terms_url = _clean(getattr(market, "contract_terms_url", ""))
    if terms_url:
        targets.append((terms_url, "rules_source", "contract_terms"))
    market_text = f"{_clean(getattr(market, 'ticker', ''))} {_market_text(market)}"
    for source in getattr(market, "settlement_sources", ()) or ():
        if _is_placeholder_settlement_source(source):
            continue
        if _settlement_source_incompatible_with_market(source, market_text):
            continue
        url = _clean(getattr(source, "url", ""))
        if not url:
            domain = _domain_from_url(_clean(getattr(source, "domain", "")))
            if domain:
                url = f"https://{domain}"
        if url:
            targets.append((url, "resolution_source", "settlement_source"))
    if _query_mentions_nws_high_temp(market_text):
        source_url = _nws_daily_climate_url(market_text)
        if source_url:
            targets.append((source_url, "resolution_source", "settlement_source"))
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for url, source_class, claim_type in targets:
        if url in seen:
            continue
        seen.add(url)
        out.append((url, source_class, claim_type))
    return out


def _is_placeholder_settlement_source(source: Any) -> bool:
    domain = _domain_from_url(
        _clean(getattr(source, "url", "")) or _clean(getattr(source, "domain", ""))
    )
    if domain != "kalshi.com":
        return False
    label = _clean(getattr(source, "label", "")).lower()
    generic_markers = (
        "<",
        "person",
        "official",
        "government",
        "records",
        "registries",
        "body",
        "agency",
        "company",
        "organization",
        "entity",
        "office",
        "social media",
        "local news",
        "relevant",
    )
    return not label or any(marker in label for marker in generic_markers)


def _should_direct_fetch_source(url: str, claim_type: str) -> bool:
    cleaned_url = _clean(url)
    if not cleaned_url:
        return False
    parsed = urllib.parse.urlparse(
        cleaned_url if "://" in cleaned_url else f"https://{cleaned_url}"
    )
    path = (parsed.path or "").strip()
    lower_path = path.lower()
    if lower_path.endswith(".pdf"):
        return False
    if claim_type == "settlement_source" and path in {"", "/"}:
        return False
    return True


def _research_prompt(
    *,
    news: Any,
    market: Any,
    queries: list[ResearchQuery],
    evidence: list[ResearchEvidence],
    yes_ask: float | None = None,
    no_ask: float | None = None,
) -> str:
    lines = [
        "You are adjudicating a prediction-market research dossier.",
        (
            "Use only the cited evidence below. Return JSON with keys: direction, "
            "estimated_probability_yes, confidence, reason, evidence_assessments, "
            "supporting_claims, counterclaims, open_questions."
        ),
        "direction must be yes, no, or neutral. estimated_probability_yes and confidence are 0.0 to 1.0.",
        "estimated_probability_yes is the probability the market resolves YES, not confidence in your conclusion.",
        "If evidence is missing, contradictory, or not settlement-aligned, use neutral.",
        (
            "reason must be evidence-specific and explicitly explain: why this side, "
            "why now, probability versus current market price, edge after costs, "
            "and the strongest countercase."
        ),
        (
            "evidence_assessments must be keyed by ordinal or source_url with "
            "supports_direction yes/no/neutral and supports_confidence 0.0 to 1.0."
        ),
        "counterclaims must state the best objection or disconfirming fact found.",
        "",
        f"MARKET TICKER: {_clean(getattr(market, 'ticker', ''))}",
        f"MARKET TITLE: {_clean(getattr(market, 'title', ''))}",
        f"RULES PRIMARY: {_clean(getattr(market, 'rules_primary', ''))}",
        f"RULES SECONDARY: {_clean(getattr(market, 'rules_secondary', ''))}",
        f"CURRENT YES ASK: {_format_probability(yes_ask)}",
        f"CURRENT NO ASK: {_format_probability(no_ask)}",
        f"TRIGGER HEADLINE: {_clean(getattr(news, 'headline', ''))}",
        f"TRIGGER SOURCE: {_clean(getattr(news, 'source', ''))}",
        "",
        "QUERIES:",
    ]
    lines.extend(
        (
            f"- intent={query.query_intent} source_class={query.source_class} "
            f"query={query.query[:120]}"
        )
        for query in queries
    )
    lines.append("")
    lines.append("EVIDENCE:")
    for ordinal, item in _evidence_for_prompt(evidence):
        lines.append(
            "- "
            f"ordinal={ordinal} class={item.source_class} claim_type={item.claim_type} "
            f"source={item.source_name} source_domain={_domain_from_url(item.source_url)} "
            f"title={item.title[:120]} snippet={item.snippet[:180]}"
        )
    return "\n".join(lines)


def _evidence_for_prompt(
    evidence: list[ResearchEvidence],
    *,
    limit: int = 6,
) -> list[tuple[int, ResearchEvidence]]:
    def priority(indexed: tuple[int, ResearchEvidence]) -> tuple[int, float, int]:
        index, item = indexed
        class_rank = {
            "resolution_source": 0,
            "official_primary": 0,
            "rules_source": 1,
            "reputable_secondary": 3,
            "specialized_data": 4,
            "trigger_source": 5,
            "other": 6,
        }.get(item.source_class, 6)
        claim_rank = {
            "official_resolution": 0,
            "resolution_source": 0,
            "settlement_source": 0,
            "rules": 1,
            "contract_terms": 1,
            "disconfirming": 2,
            "contradiction_check": 2,
            "supporting": 3,
            "corroboration": 3,
            "base_rate": 4,
            "staleness_check": 5,
            "broad_context": 6,
        }.get(item.claim_type, 7)
        return (min(class_rank, claim_rank), -float(item.supports_confidence or 0.0), index)

    selected: list[tuple[int, ResearchEvidence]] = []
    seen: set[str] = set()
    for index, item in sorted(enumerate(evidence), key=priority):
        key = item.source_url or f"{item.source_name}|{item.title}|{item.snippet}"
        if key in seen:
            continue
        seen.add(key)
        selected.append((index, item))
        if len(selected) >= limit:
            break
    return selected


async def default_ollama_adjudicator(
    *,
    evidence: list[ResearchEvidence],
    queries: list[ResearchQuery],
    news: Any,
    market: Any,
    yes_ask: float | None = None,
    no_ask: float | None = None,
) -> dict[str, Any] | None:
    prompt = _research_prompt(
        news=news,
        market=market,
        queries=queries,
        evidence=evidence,
        yes_ask=yes_ask,
        no_ask=no_ask,
    )
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    model = os.getenv("REAL_WEB_RESEARCH_OLLAMA_MODEL", os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
    timeout = float(os.getenv("REAL_WEB_RESEARCH_OLLAMA_TIMEOUT_SECONDS", "20"))
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "Return strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 700,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")

    def _call() -> dict[str, Any] | None:
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            payload = json.loads(response.read(300_000).decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            return None
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None

    try:
        return await asyncio.to_thread(_call)
    except Exception:
        return None


async def run_research_gate(
    news: Any,
    market: Any,
    *,
    model_direction: str | None,
    model_confidence: float | None,
    model_reason: str | None,
    yes_ask: float | None,
    no_ask: float | None,
    live_mode: bool,
    search_provider: SearchProvider | None = None,
    direct_fetcher: DirectFetcher | None = None,
    adjudicator: ResearchAdjudicator | None = None,
    dossier_store: DossierStore | None = None,
    max_queries: int = 6,
    research_timeout_seconds: float = 12.0,
    prewarm_phase_timeouts: PrewarmPhaseTimeouts | None = None,
    _prewarm_phase_timeouts_capability: object | None = None,
    cache_only: bool = False,
    require_decision_grade: bool = False,
) -> ResearchVerdict:
    if prewarm_phase_timeouts is not None and (
        live_mode
        or not require_decision_grade
        or _clean(getattr(news, "source", "")).lower() != "research_prewarm"
        or _prewarm_phase_timeouts_capability
        is not _PREWARM_PHASE_TIMEOUTS_CAPABILITY
    ):
        raise ValueError(
            "prewarm phase timeouts require offline decision-grade research_prewarm"
        )
    queries = _select_research_queries(
        build_research_queries(news, market),
        max_queries=max_queries,
        require_decision_grade=require_decision_grade,
    )
    ticker = _clean(getattr(market, "ticker", ""))
    contract_fingerprint = _contract_fingerprint(market)
    observed_market_price = _market_price_for_side(None, yes_ask, no_ask)
    persistence_run_id = (
        f"rr-{uuid.uuid4().hex}" if dossier_store is not None and ticker else None
    )
    captured_timeout_snapshot: ResearchTimeoutReplaySnapshot | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.001, float(research_timeout_seconds))
    active_timeout_seconds = float(research_timeout_seconds)
    provider_errors: list[Exception] = []
    generic_search_circuit_events: list[GenericSearchCircuitEvent] = []
    generic_search_circuit_event_collector_token = (
        _GENERIC_SEARCH_CIRCUIT_EVENT_COLLECTOR.set(generic_search_circuit_events)
    )

    def remaining_budget() -> float:
        return max(0.0, deadline - loop.time())

    def begin_phase_timeout(timeout_seconds: float) -> None:
        nonlocal active_timeout_seconds, deadline
        active_timeout_seconds = timeout_seconds
        deadline = loop.time() + timeout_seconds

    def timeout_verdict(
        evidence: list[ResearchEvidence],
        summary: str,
        *,
        stage: str,
        counter_evidence_added: bool = False,
    ) -> ResearchVerdict:
        nonlocal captured_timeout_snapshot
        if persistence_run_id is not None:
            try:
                captured_timeout_snapshot = capture_timeout_replay_snapshot(
                    research_run_id=persistence_run_id,
                    market_ticker=ticker,
                    contract_fingerprint=contract_fingerprint,
                    timeout_stage=stage,
                    configured_timeout_seconds=active_timeout_seconds,
                    remaining_budget_seconds=remaining_budget(),
                    observed_market_price=observed_market_price,
                    yes_ask=yes_ask,
                    no_ask=no_ask,
                    require_decision_grade=require_decision_grade,
                    live_mode=live_mode,
                    counter_evidence_added=counter_evidence_added,
                    model_direction=model_direction,
                    model_confidence=model_confidence,
                    estimated_probability_yes=estimated_probability_yes,
                    model_reason=model_reason,
                    counterclaims=decision_grade_counterclaims,
                    open_questions=decision_grade_open_questions,
                    queries=queries,
                    evidence=evidence,
                )
            except Exception as exc:
                captured_timeout_snapshot = None
                log.warning(
                    "[RESEARCH_TIMEOUT_DIAGNOSTIC] capture failed ticker=%s stage=%s: %s",
                    ticker,
                    stage,
                    exc,
                )
        return ResearchVerdict(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary=summary,
            skip_reason="research_timeout",
            market_price=observed_market_price,
            research_timeout_stage=stage,
            research_provider_error_count=len(provider_errors),
        )

    cached_evidence: list[ResearchEvidence] = []
    cached_dossier: Any | None = None
    if dossier_store is not None and ticker:
        try:
            cached_evidence = await dossier_store.get_recent_evidence(ticker)
        except Exception:
            cached_evidence = []
        if hasattr(dossier_store, "get_dossier_snapshot"):
            try:
                cached_dossier = await dossier_store.get_dossier_snapshot(ticker)
            except Exception:
                cached_dossier = None
    fresh_evidence: list[ResearchEvidence] = []
    estimated_probability_yes: float | None = None
    gdpnow_countercheck_attempted = False
    gdpnow_countercheck_qualified = False
    decision_grade_counterclaims: tuple[str, ...] = ()
    decision_grade_open_questions: tuple[str, ...] = ()
    direct_fetch_failures: list[str] = []
    usable_cached_evidence = _usable_cached_evidence(
        cached_evidence,
        contract_fingerprint,
        f"{ticker} {_market_text(market)}",
    )

    async def finalize_verdict(verdict: ResearchVerdict) -> ResearchVerdict:
        open_questions = research_questions_for_skip(
            verdict.skip_reason,
            verdict.open_questions,
        )
        if open_questions != verdict.open_questions:
            verdict = replace(verdict, open_questions=open_questions)
        if verdict.research_provider_error_count != len(provider_errors):
            verdict = replace(
                verdict,
                research_provider_error_count=len(provider_errors),
            )
        provider_error_attributions = _research_provider_error_attributions(
            provider_errors,
            timeout_stage=verdict.research_timeout_stage,
        )
        if verdict.research_provider_error_attributions != provider_error_attributions:
            verdict = replace(
                verdict,
                research_provider_error_attributions=provider_error_attributions,
            )
        (
            circuit_state,
            circuit_failure_classes,
            circuit_attempt_delta,
            circuit_blocked_call_delta,
        ) = _generic_search_circuit_diagnostics(
            generic_search_circuit_events
        )
        if (
            circuit_state is not None
            or circuit_failure_classes
            or circuit_attempt_delta
            or circuit_blocked_call_delta
        ):
            verdict = replace(
                verdict,
                research_generic_search_circuit_state=circuit_state,
                research_generic_search_failure_classes=circuit_failure_classes,
                research_generic_search_attempt_delta=circuit_attempt_delta,
                research_generic_search_blocked_call_delta=circuit_blocked_call_delta,
            )
        if direct_fetch_failures:
            verdict = replace(
                verdict,
                research_direct_fetch_failures=tuple(direct_fetch_failures),
            )
        if dossier_store is not None and ticker:
            run_id = persistence_run_id or f"rr-{uuid.uuid4().hex}"
            verdict = replace(
                verdict,
                research_run_id=run_id,
                research_contract_fingerprint=contract_fingerprint,
            )
            try:
                if hasattr(dossier_store, "record_research_run"):
                    await dossier_store.record_research_run(
                        ticker,
                        run_id,
                        trigger_headline=_clean(getattr(news, "headline", "")),
                        trigger_source=_clean(getattr(news, "source", "")),
                        contract_question=_market_contract_question(market),
                        attempted=verdict.attempted,
                        summary=verdict.summary,
                        verdict_status=verdict.status.value,
                        skip_reason=verdict.skip_reason,
                        research_pending_origin=verdict.research_pending_origin,
                        force_side=verdict.force_side,
                        estimated_probability=verdict.estimated_probability,
                        confidence=verdict.confidence,
                        contract_fingerprint=contract_fingerprint,
                        market_price=verdict.market_price,
                        estimated_edge=verdict.estimated_edge,
                        decision_grade_status=verdict.status.value
                        if verdict.status
                        in {
                            ResearchStatus.DECISION_GRADE_CANDIDATE,
                            ResearchStatus.NEEDS_COUNTER_EVIDENCE,
                            ResearchStatus.NEEDS_PRICE_EDGE,
                            ResearchStatus.NEEDS_RESEARCH,
                            ResearchStatus.UNTRADEABLE,
                        }
                        else None,
                        decision_grade_reasons=list(verdict.decision_grade_reasons),
                        market_status=getattr(market, "status", None),
                        market_close_time=getattr(market, "close_time", None),
                        open_questions=list(verdict.open_questions),
                        counterclaims=list(verdict.counterclaims),
                        queries=queries,
                        evidence=verdict.evidence,
                        timeout_diagnostic=captured_timeout_snapshot,
                        # Keep fail-closed attempts in the audit log without
                        # demoting the last cache-eligible dossier snapshot.
                        update_dossier_snapshot=_should_update_dossier_snapshot(
                            verdict,
                            trigger_source=_clean(getattr(news, "source", "")),
                            cached_dossier=cached_dossier,
                            contract_fingerprint=contract_fingerprint,
                        ),
                        update_dossier_run_id=True,
                    )
                    verdict = replace(verdict, research_persisted=True)
                    verdict = await _reconcile_persisted_verdict(
                        verdict,
                        dossier_store=dossier_store,
                        ticker=ticker,
                        run_id=run_id,
                    )
                else:
                    for item in fresh_evidence:
                        await dossier_store.add_evidence(ticker, run_id, item)
                    verdict = replace(verdict, research_persisted=True)
            except Exception as exc:
                verdict = replace(
                    verdict,
                    research_persisted=False,
                    research_persistence_error=str(exc),
                )
        _GENERIC_SEARCH_CIRCUIT_EVENT_COLLECTOR.reset(
            generic_search_circuit_event_collector_token
        )
        return verdict

    if cache_only:
        if not _has_sufficient_dossier_evidence(usable_cached_evidence, contract_fingerprint):
            return ResearchVerdict(
                status=ResearchStatus.CONTINUE_RESEARCHING,
                attempted=False,
                queries=queries,
                evidence=usable_cached_evidence,
                summary=(
                    "Cache-only research mode has no sufficient fresh "
                    "contract-matching dossier evidence; no live research attempted."
                ),
                skip_reason="cached_dossier_insufficient",
            )
        if live_mode and any(
            _is_gdpnow_derived_countercheck(item)
            for item in usable_cached_evidence
        ):
            return ResearchVerdict(
                status=ResearchStatus.CONTINUE_RESEARCHING,
                attempted=False,
                queries=queries,
                evidence=[
                    item
                    for item in usable_cached_evidence
                    if not _is_gdpnow_derived_countercheck(item)
                ],
                summary=(
                    "Live cache replay cannot use a GDPNow-derived countercheck; "
                    "the persisted derived record is paper/prewarm-only."
                ),
                skip_reason="gdpnow_countercheck_live_disabled",
            )
        cached_status = getattr(cached_dossier, "last_verdict_status", None)
        if (
            cached_dossier is None
            or not _is_vetted_candidate_status(cached_status)
            or getattr(cached_dossier, "last_force_side", None) not in {"yes", "no"}
            or getattr(cached_dossier, "last_estimated_probability", None) is None
            or getattr(cached_dossier, "last_confidence", None) is None
        ):
            return ResearchVerdict(
                status=ResearchStatus.CONTINUE_RESEARCHING,
                attempted=False,
                queries=queries,
                evidence=usable_cached_evidence,
                summary=(
                    "Cache-only research mode found evidence but no vetted "
                    "directional dossier verdict; no live research attempted."
                ),
                skip_reason="cached_dossier_unvetted",
            )
        verdict = decide_research_verdict(
            evidence=usable_cached_evidence,
            model_direction=getattr(cached_dossier, "last_force_side"),
            model_confidence=getattr(cached_dossier, "last_confidence"),
            model_reason="Cached research dossier verdict.",
            estimated_probability_yes=getattr(
                cached_dossier,
                "last_estimated_probability",
            ),
            yes_ask=yes_ask,
            no_ask=no_ask,
            live_mode=live_mode,
            queries=queries,
            contract_ticker=ticker,
        )
        if (
            cached_status == ResearchStatus.DECISION_GRADE_CANDIDATE.value
            and verdict.status == ResearchStatus.TRADE_CANDIDATE
        ):
            return _decision_grade_verdict(
                verdict,
                model_reason=verdict.summary,
                contract_ticker=ticker,
            )
        return verdict
    provider_non_pending_evidence: list[ResearchEvidence] = []
    provider = search_provider or default_search_provider
    collection_budget_exhausted = False
    if (
        _has_sufficient_dossier_evidence(usable_cached_evidence, contract_fingerprint)
        and not (
            require_decision_grade
            and _cached_dossier_needs_counter_refresh(cached_dossier)
        )
    ):
        evidence = usable_cached_evidence
    else:
        evidence = list(usable_cached_evidence)
        existing = {_evidence_identity(item) for item in evidence}
        fetcher = direct_fetcher or default_direct_fetcher
        for url, source_class, claim_type in _direct_source_targets(market):
            if not _should_direct_fetch_source(url, claim_type):
                continue
            remaining = remaining_budget()
            if remaining <= 0:
                if prewarm_phase_timeouts is not None and evidence:
                    collection_budget_exhausted = True
                    break
                return await finalize_verdict(
                    timeout_verdict(
                        evidence,
                        "Research timed out before direct-source fetch completed.",
                        stage="direct_fetch",
                    )
                )
            try:
                item = await asyncio.wait_for(
                    fetcher(url, source_class, claim_type),
                    timeout=remaining,
                )
            except TimeoutError:
                if remaining_budget() <= 0.001:
                    if prewarm_phase_timeouts is not None and evidence:
                        collection_budget_exhausted = True
                        break
                    return await finalize_verdict(
                        timeout_verdict(
                            evidence,
                            "Research direct-source fetch timed out before enough evidence was retrieved.",
                            stage="direct_fetch",
                        )
                    )
                direct_fetch_failures.append(f"{source_class}:{url}:timeout")
                continue
            except Exception as exc:
                direct_fetch_failures.append(f"{source_class}:{url}:{exc}")
                continue
            if item is None:
                continue
            identity = _evidence_identity(item)
            if identity in existing:
                continue
            existing.add(identity)
            item = replace(item, contract_fingerprint=contract_fingerprint)
            evidence.append(item)
            fresh_evidence.append(item)

        if not collection_budget_exhausted:
            for query in _structured_direct_source_queries(market, queries):
                remaining = remaining_budget()
                if remaining <= 0:
                    if prewarm_phase_timeouts is not None and evidence:
                        collection_budget_exhausted = True
                        break
                    return await finalize_verdict(
                        timeout_verdict(
                            evidence,
                            "Research timed out before structured direct-source extraction completed.",
                            stage="structured_official",
                        )
                    )
                try:
                    structured_items = await asyncio.wait_for(
                        asyncio.to_thread(_structured_official_search, query),
                        timeout=remaining,
                    )
                except TimeoutError:
                    if remaining_budget() <= 0.001:
                        if prewarm_phase_timeouts is not None and evidence:
                            collection_budget_exhausted = True
                            break
                        return await finalize_verdict(
                            timeout_verdict(
                                evidence,
                                "Research structured direct-source extraction timed out.",
                                stage="structured_official",
                            )
                        )
                    direct_fetch_failures.append(
                        f"{query.source_class}:{query.query}:structured_timeout"
                    )
                    continue
                except Exception as exc:
                    direct_fetch_failures.append(
                        f"{query.source_class}:{query.query}:structured:{exc}"
                    )
                    continue
                for item in structured_items:
                    identity = _evidence_identity(item)
                    if identity in existing:
                        continue
                    existing.add(identity)
                    item = replace(item, contract_fingerprint=contract_fingerprint)
                    evidence.append(item)
                    fresh_evidence.append(item)

        direct_domains = {
            _domain_from_url(item.source_url)
            for item in fresh_evidence
            if item.source_class in {"resolution_source", "official_primary"}
            and item.source_url
        }
        provider_queries = [
            query
            for query in queries
            if not (
                query.source_class in {"resolution_source", "official_primary"}
                and _query_site_domain(query.query) in direct_domains
            )
            and not (
                _getty_distinct_date_spec_from_text(query.query) is not None
                and any(
                    item.metric_name == "getty_trump_distinct_photo_days"
                    for item in fresh_evidence
                )
            )
            and not (
                _white_house_action_count_spec_from_text(query.query) is not None
                and any(
                    item.metric_name == "white_house_presidential_actions_count"
                    for item in fresh_evidence
                )
            )
        ]
        if not collection_budget_exhausted:
            remaining = remaining_budget()
            if remaining <= 0:
                if prewarm_phase_timeouts is not None and evidence:
                    collection_budget_exhausted = True
                else:
                    return await finalize_verdict(
                        timeout_verdict(
                            evidence,
                            "Research timed out before search providers completed.",
                            stage="provider_fanout",
                        )
                    )
            if not collection_budget_exhausted:
                try:
                    evidence_nested = await asyncio.wait_for(
                        asyncio.gather(
                            *(provider(query) for query in provider_queries),
                            return_exceptions=True,
                        ),
                        timeout=remaining,
                    )
                except TimeoutError:
                    if prewarm_phase_timeouts is None or not evidence:
                        return await finalize_verdict(
                            timeout_verdict(
                                evidence,
                                "Research search providers timed out before enough evidence was retrieved.",
                                stage="provider_fanout",
                            )
                        )
                    collection_budget_exhausted = True
                else:
                    for result in evidence_nested:
                        if isinstance(result, Exception):
                            provider_errors.append(result)
                            continue
                        for item in result:
                            if not _is_official_data_pending_evidence(item):
                                provider_non_pending_evidence.append(item)
                            identity = _evidence_identity(item)
                            if identity in existing:
                                continue
                            existing.add(identity)
                            item = replace(item, contract_fingerprint=contract_fingerprint)
                            evidence.append(item)
                            fresh_evidence.append(item)
    if require_decision_grade and evidence:
        evidence = _apply_structured_indicator_evidence(evidence, market)
    if (
        require_decision_grade
        and adjudicator is not None
        and any(_is_gdpnow_structured_evidence(item) for item in evidence)
        and not _GDP_REAL_GDP_RE.search(_clean(getattr(market, "title", "")))
    ):
        gdp_threshold = _gdp_threshold_from_text(_market_text(market))
        gdp_observation = next(
            (
                item
                for item in evidence
                if item.metric_name == _GDPNOW_METRIC_NAME
                and item.metric_value is not None
            ),
            None,
        )
        estimated_probability = None
        if gdp_threshold is not None and gdp_observation is not None:
            estimated_probability = max(
                0.05,
                min(
                    0.95,
                    0.50 + (float(gdp_observation.metric_value) - gdp_threshold) / 5.0,
                ),
            )
        gdp_direction = None
        if gdp_threshold is not None and gdp_observation is not None:
            gdp_direction = "yes" if float(gdp_observation.metric_value) >= gdp_threshold else "no"
        legacy_evidence = [
            replace(
                item,
                supports_direction=gdp_direction,
                supports_confidence=max(
                    float(item.supports_confidence or 0.0),
                    _gdpnow_confidence(float(item.metric_value), gdp_threshold),
                )
                if gdp_threshold is not None and item.metric_value is not None
                else item.supports_confidence,
            )
            if item is gdp_observation and gdp_direction in {"yes", "no"}
            else item
            for item in evidence
        ]
        return await finalize_verdict(
            ResearchVerdict(
                status=ResearchStatus.UNTRADEABLE,
                attempted=True,
                queries=queries,
                evidence=legacy_evidence,
                summary=(
                    "GDPNow evidence cannot be bound to a real-GDP settlement "
                    "contract with an unambiguous metric definition."
                ),
                skip_reason="no_edge",
                force_side=next(
                    (
                        item.supports_direction
                        for item in evidence
                        if _is_gdpnow_structured_evidence(item)
                        and item.supports_direction in {"yes", "no"}
                    ),
                    None,
                ),
                market_price=(
                    no_ask
                    if gdp_direction == "no"
                    else yes_ask
                    if gdp_direction == "yes"
                    else observed_market_price
                ),
                estimated_probability=estimated_probability,
            )
        )
    if (
        require_decision_grade
        and _has_official_data_pending(evidence)
        and not (
            _parse_gdp_threshold_contract(market) is not None
            or (
                _GDP_REAL_GDP_RE.search(_clean(getattr(market, "title", "")))
                and any(_is_gdpnow_structured_evidence(item) for item in evidence)
            )
        )
        and not _has_trade_selection_evidence(evidence)
        and not _has_reliable_non_pending_source_path(
            provider_non_pending_evidence
        )
    ):
        return await finalize_verdict(
            ResearchVerdict(
                status=ResearchStatus.NEEDS_RESEARCH,
                attempted=True,
                queries=queries,
                evidence=evidence,
                summary=(
                    "Official settlement data for the target period is not "
                    "released yet; keep research queued."
                ),
                skip_reason="official_data_pending",
                market_price=observed_market_price,
            )
        )
    if (
        require_decision_grade
        and evidence
        and _market_price_for_side(None, yes_ask, no_ask) is None
    ):
        if _has_quoted_market_price(yes_ask, no_ask):
            return await finalize_verdict(
                ResearchVerdict(
                    status=ResearchStatus.UNTRADEABLE,
                    attempted=True,
                    queries=queries,
                    evidence=evidence,
                    summary=(
                        "Quoted market prices are present but not executable "
                        "for edge calculation."
                    ),
                    skip_reason="non_actionable_market_price",
                )
            )
        return await finalize_verdict(
            ResearchVerdict(
                status=ResearchStatus.NEEDS_PRICE_EDGE,
                attempted=True,
                queries=queries,
                evidence=evidence,
                summary="Decision-grade verifier requires an actionable market price.",
                skip_reason="missing_market_price",
            )
        )
    deterministic_signal = None
    if require_decision_grade and evidence:
        deterministic_signal = _deterministic_decision_signal(
            evidence,
            market,
            allow_evidence_signal=True,
        )
        if deterministic_signal:
            model_direction = str(deterministic_signal["direction"])
            estimated_probability_yes = float(
                deterministic_signal["estimated_probability_yes"]
            )
            model_confidence = float(deterministic_signal["confidence"])
            model_reason = str(deterministic_signal["reason"])
    if (
        require_decision_grade
        and adjudicator is None
        and model_direction not in {"yes", "no"}
        and _GDP_REAL_GDP_RE.search(_clean(getattr(market, "title", "")))
        and any(_is_gdpnow_structured_evidence(item) for item in evidence)
        and _has_official_data_pending(evidence)
    ):
        gdp_threshold = _gdp_threshold_from_text(_market_text(market))
        gdp_observation = next(
            (
                item
                for item in evidence
                if item.metric_name == _GDPNOW_METRIC_NAME
                and item.metric_value is not None
            ),
            None,
        )
        estimated_probability = None
        if gdp_threshold is not None and gdp_observation is not None:
            estimated_probability = max(
                0.05,
                min(
                    0.95,
                    0.50 + (float(gdp_observation.metric_value) - gdp_threshold) / 5.0,
                ),
            )
        gdp_direction = None
        if gdp_threshold is not None and gdp_observation is not None:
            gdp_direction = "yes" if float(gdp_observation.metric_value) >= gdp_threshold else "no"
        legacy_evidence = [
            replace(
                item,
                supports_direction=gdp_direction,
                supports_confidence=max(
                    float(item.supports_confidence or 0.0),
                    _gdpnow_confidence(float(item.metric_value), gdp_threshold),
                )
                if gdp_threshold is not None and item.metric_value is not None
                else item.supports_confidence,
            )
            if item is gdp_observation and gdp_direction in {"yes", "no"}
            else item
            for item in evidence
        ]
        market_price = (
            no_ask
            if gdp_direction == "no"
            else yes_ask
            if gdp_direction == "yes"
            else observed_market_price
        )
        estimated_edge = None
        if estimated_probability is not None and market_price is not None:
            side_probability = (
                estimated_probability
                if gdp_direction == "yes"
                else 1.0 - estimated_probability
                if gdp_direction == "no"
                else estimated_probability
            )
            estimated_edge = side_probability - market_price - 0.01
        return await finalize_verdict(
            ResearchVerdict(
                status=ResearchStatus.NEEDS_COUNTER_EVIDENCE,
                attempted=True,
                queries=queries,
                evidence=legacy_evidence,
                summary=(
                    "GDPNow is directional context only; obtain an independent "
                    "counter-evidence adjudication before selecting a side."
                ),
                skip_reason="missing_counter_evidence",
                market_price=market_price,
                estimated_probability=estimated_probability,
                estimated_edge=estimated_edge,
            )
        )
    if evidence:
        adjudicate = adjudicator or default_ollama_adjudicator
        remaining = remaining_budget()
        if deterministic_signal is None:
            if prewarm_phase_timeouts is not None:
                begin_phase_timeout(
                    prewarm_phase_timeouts.initial_adjudication_seconds
                )
                remaining = remaining_budget()
            if remaining <= 0:
                return await finalize_verdict(
                    timeout_verdict(
                        evidence,
                        "Research timed out before adjudication completed.",
                        stage="adjudication",
                    )
                )
            try:
                adjudication = await asyncio.wait_for(
                    adjudicate(
                        evidence=evidence,
                        queries=queries,
                        news=news,
                        market=market,
                        **(
                            {"yes_ask": yes_ask, "no_ask": no_ask}
                            if adjudicator is None
                            else {}
                        ),
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                return await finalize_verdict(
                    timeout_verdict(
                        evidence,
                        "Research adjudication timed out before producing a verdict.",
                        stage="adjudication",
                    )
                )
            except Exception:
                return await finalize_verdict(
                    ResearchVerdict(
                        status=ResearchStatus.RESEARCH_ADJUDICATOR_ERROR,
                        attempted=True,
                        queries=queries,
                        evidence=evidence,
                        summary="Research adjudicator failed before producing a verdict.",
                        skip_reason="research_adjudicator_error",
                        market_price=observed_market_price,
                    )
                )
            if isinstance(adjudication, dict):
                model_direction = str(adjudication.get("direction") or model_direction or "neutral").lower()
                try:
                    model_confidence = float(adjudication.get("confidence", model_confidence or 0.0))
                except (TypeError, ValueError):
                    model_confidence = model_confidence
                estimated_probability_yes = _coerce_probability(
                    adjudication.get("estimated_probability_yes")
                )
                model_reason = str(adjudication.get("reason") or model_reason or "")
                evidence = _apply_adjudication_evidence_assessments(
                    evidence,
                    adjudication,
                    market=market,
                )
                decision_grade_counterclaims = _string_tuple(adjudication.get("counterclaims"))
                decision_grade_open_questions = _string_tuple(adjudication.get("open_questions"))
                if require_decision_grade and model_direction in {"yes", "no"}:
                    gdp_contract = _parse_gdp_threshold_contract(market)
                    base_rate_queries = [
                        query
                        for query in queries
                        if query.query_intent == "base_rate"
                        and _query_mentions_gdpnow(query.query)
                    ]
                    if (
                        not live_mode
                        and gdp_contract is not None
                        and base_rate_queries
                        and _gdpnow_provisional_side_is_independently_justified(
                            evidence,
                            provisional_side=model_direction,
                            contract=gdp_contract,
                            yes_ask=yes_ask,
                            no_ask=no_ask,
                            estimated_probability_yes=estimated_probability_yes,
                            contract_ticker=ticker,
                            queries=queries,
                        )
                    ):
                        for observation in evidence:
                            if (
                                observation.claim_type != "base_rate"
                                or not _has_canonical_fred_gdpnow_provenance(observation)
                            ):
                                continue
                            context = _build_current_run_gdpnow_observation_context(
                                observation,
                                query=base_rate_queries[0],
                                contract=gdp_contract,
                            )
                            if context is None:
                                continue
                            countercheck = _build_gdpnow_countercheck_evidence(
                                observation,
                                context=context,
                                contract=gdp_contract,
                                provisional_side=model_direction,
                            )
                            if countercheck is None:
                                continue
                            if not any(
                                _evidence_identity(item)
                                == _evidence_identity(countercheck)
                                for item in evidence
                            ):
                                evidence.append(countercheck)
                                fresh_evidence.append(countercheck)
                            gdpnow_countercheck_attempted = True
                            enriched_verdict = decide_research_verdict(
                                evidence=evidence,
                                model_direction=model_direction,
                                model_confidence=model_confidence,
                                model_reason=model_reason,
                                estimated_probability_yes=estimated_probability_yes,
                                yes_ask=yes_ask,
                                no_ask=no_ask,
                                live_mode=live_mode,
                                queries=queries,
                                require_decision_grade=require_decision_grade,
                                counterclaims=decision_grade_counterclaims,
                                open_questions=decision_grade_open_questions,
                                contract_ticker=ticker,
                            )
                            gdpnow_countercheck_qualified = _is_vetted_candidate_status(
                                enriched_verdict.status
                            )
                            break
                    needs_legacy_counter = not _has_counter_evidence(
                        queries,
                        evidence,
                        model_direction,
                        contract_ticker=ticker,
                    )
                    if gdpnow_countercheck_attempted:
                        needs_legacy_counter = not gdpnow_countercheck_qualified
                    counter_query = (
                        _side_aware_counter_query(market, model_direction)
                        if needs_legacy_counter
                        else None
                    )
                    if (
                        counter_query is not None
                        and counter_query.query not in {query.query for query in queries}
                    ):
                        queries.append(counter_query)
                        if prewarm_phase_timeouts is not None:
                            begin_phase_timeout(
                                prewarm_phase_timeouts.counter_query_seconds
                            )
                        remaining = remaining_budget()
                        if remaining > 0:
                            try:
                                counter_results = await asyncio.wait_for(
                                    provider(counter_query),
                                    timeout=remaining,
                                )
                            except TimeoutError:
                                return await finalize_verdict(
                                    timeout_verdict(
                                        evidence,
                                        "Research timed out before side-aware counter search completed.",
                                        stage="counter_query",
                                    )
                                )
                            except Exception as exc:
                                provider_errors.append(exc)
                                counter_results = []
                            added_counter_evidence = False
                            existing = {_evidence_identity(item) for item in evidence}
                            for item in counter_results:
                                identity = _evidence_identity(item)
                                if identity in existing:
                                    continue
                                existing.add(identity)
                                item = replace(item, contract_fingerprint=contract_fingerprint)
                                evidence.append(item)
                                fresh_evidence.append(item)
                                added_counter_evidence = True
                            if added_counter_evidence:
                                if prewarm_phase_timeouts is not None:
                                    begin_phase_timeout(
                                        prewarm_phase_timeouts.counter_adjudication_seconds
                                    )
                                remaining = remaining_budget()
                                if remaining <= 0:
                                    return await finalize_verdict(
                                        timeout_verdict(
                                            evidence,
                                            "Research timed out before counter-evidence adjudication completed.",
                                            stage="counter_adjudication",
                                            counter_evidence_added=True,
                                        )
                                    )
                                try:
                                    counter_adjudication = await asyncio.wait_for(
                                        adjudicate(
                                            evidence=evidence,
                                            queries=queries,
                                            news=news,
                                            market=market,
                                            **(
                                                {"yes_ask": yes_ask, "no_ask": no_ask}
                                                if adjudicator is None
                                                else {}
                                            ),
                                        ),
                                        timeout=remaining,
                                    )
                                except TimeoutError:
                                    return await finalize_verdict(
                                        timeout_verdict(
                                            evidence,
                                            "Research timed out before counter-evidence adjudication completed.",
                                            stage="counter_adjudication",
                                            counter_evidence_added=True,
                                        )
                                    )
                                except Exception:
                                    return await finalize_verdict(
                                        ResearchVerdict(
                                            status=ResearchStatus.RESEARCH_ADJUDICATOR_ERROR,
                                            attempted=True,
                                            queries=queries,
                                            evidence=evidence,
                                            summary=(
                                                "Research counter-evidence adjudication failed "
                                                "before producing a verdict."
                                            ),
                                            skip_reason="research_adjudicator_error",
                                            market_price=observed_market_price,
                                        )
                                    )
                                if not isinstance(counter_adjudication, dict):
                                    return await finalize_verdict(
                                        ResearchVerdict(
                                            status=ResearchStatus.RESEARCH_ADJUDICATOR_ERROR,
                                            attempted=True,
                                            queries=queries,
                                            evidence=evidence,
                                            summary=(
                                                "Research counter-evidence adjudication returned "
                                                "no parseable verdict."
                                            ),
                                            skip_reason="research_adjudicator_error",
                                            market_price=observed_market_price,
                                        )
                                    )
                                if isinstance(counter_adjudication, dict):
                                    model_direction = str(
                                        counter_adjudication.get("direction")
                                        or model_direction
                                        or "neutral"
                                    ).lower()
                                    try:
                                        model_confidence = float(
                                            counter_adjudication.get(
                                                "confidence",
                                                model_confidence or 0.0,
                                            )
                                        )
                                    except (TypeError, ValueError):
                                        model_confidence = model_confidence
                                    estimated_probability_yes = _coerce_probability(
                                        counter_adjudication.get(
                                            "estimated_probability_yes",
                                            estimated_probability_yes,
                                        )
                                    )
                                    model_reason = str(
                                        counter_adjudication.get("reason")
                                        or model_reason
                                        or ""
                                    )
                                    evidence = _apply_adjudication_evidence_assessments(
                                        evidence,
                                        counter_adjudication,
                                        market=market,
                                    )
                                    decision_grade_counterclaims = _string_tuple(
                                        counter_adjudication.get("counterclaims")
                                    ) or decision_grade_counterclaims
                                    decision_grade_open_questions = _string_tuple(
                                        counter_adjudication.get("open_questions")
                                    ) or decision_grade_open_questions
            else:
                return await finalize_verdict(
                    ResearchVerdict(
                        status=ResearchStatus.RESEARCH_ADJUDICATOR_ERROR,
                        attempted=True,
                        queries=queries,
                        evidence=evidence,
                        summary="Research adjudicator returned no parseable verdict.",
                        skip_reason="research_adjudicator_error",
                        market_price=observed_market_price,
                    )
                )
    evidence = _apply_structured_indicator_evidence(evidence, market)
    deterministic_signal = _deterministic_decision_signal(
        evidence,
        market,
        allow_evidence_signal=require_decision_grade,
    )
    if deterministic_signal:
        model_direction = str(deterministic_signal["direction"])
        estimated_probability_yes = float(
            deterministic_signal["estimated_probability_yes"]
        )
        model_confidence = float(deterministic_signal["confidence"])
        model_reason = str(deterministic_signal["reason"])
    verdict = decide_research_verdict(
        evidence=evidence,
        model_direction=model_direction,
        model_confidence=model_confidence,
        model_reason=model_reason,
        estimated_probability_yes=estimated_probability_yes,
        yes_ask=yes_ask,
        no_ask=no_ask,
        live_mode=live_mode,
        queries=queries,
        require_decision_grade=require_decision_grade,
        counterclaims=decision_grade_counterclaims,
        open_questions=decision_grade_open_questions,
        contract_ticker=ticker,
    )
    if require_decision_grade:
        verdict = _keep_pending_no_edge_researchable(verdict)
    if (
        provider_errors
        and not _is_vetted_candidate_status(verdict.status)
        and not any(_is_settlement_evidence(item) for item in evidence)
    ):
        verdict = ResearchVerdict(
            status=ResearchStatus.RESEARCH_PROVIDER_ERROR,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Research provider failed before the source frontier could be trusted.",
            skip_reason="research_provider_error",
            market_price=observed_market_price,
        )
        if any(isinstance(error, GenericSearchUnavailable) for error in provider_errors):
            await _get_generic_search_circuit().emit_telemetry_observation(
                "gate_provider_error_verdict"
            )
    return await finalize_verdict(verdict)


_TIME_SENSITIVE_MAX_AGE_SECONDS = 60 * 60
_DOSSIER_MAX_AGE_SECONDS = 6 * 60 * 60
_DURABLE_MAX_AGE_SECONDS = 24 * 60 * 60
_TIME_SENSITIVE_METRICS = {
    "nws_daily_high_temp_f",
    "daily_treasury_yield_curve",
    "research_market_price",
}
_TIME_SENSITIVE_CLAIM_TYPES = {"market_price", "staleness_check"}
_DURABLE_CLAIM_TYPES = {
    "contract_terms",
    "rules",
    "rules_context",
    "official_resolution",
    "settlement",
    "settlement_source",
}


def _usable_cached_evidence(
    evidence: list[ResearchEvidence],
    contract_fingerprint: str,
    market_text: str = "",
) -> list[ResearchEvidence]:
    return [
        item
        for item in evidence
        if item.contract_fingerprint == contract_fingerprint and _is_fresh_evidence(item)
        and not _cached_evidence_incompatible_with_market(item, market_text)
    ]


def _cached_evidence_incompatible_with_market(
    item: ResearchEvidence,
    market_text: str,
) -> bool:
    if not _is_south_africa_trade_balance_market(market_text):
        return False
    if item.claim_type not in {"settlement_source", "official_resolution", "resolution_source"}:
        return False
    domain = _domain_from_url(item.source_url) or _source_domain(item.source_name)
    return domain in _US_ECONOMIC_SOURCE_DOMAINS


def _has_sufficient_dossier_evidence(
    evidence: list[ResearchEvidence],
    contract_fingerprint: str,
) -> bool:
    if len(evidence) < 2:
        return False
    if not all(
        item.contract_fingerprint == contract_fingerprint and _is_fresh_evidence(item)
        for item in evidence
    ):
        return False
    has_resolution = any(_is_settlement_evidence(item) for item in evidence)
    urls = {item.source_url for item in evidence if item.source_url}
    return has_resolution and len(urls) >= 2


def _is_fresh_evidence(
    evidence: ResearchEvidence,
    *,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    if not research_evidence_temporally_valid(evidence, as_of=now):
        return False
    max_age_seconds = _decision_evidence_max_age_seconds(evidence)
    authoritative = [
        _parse_timestamp(value)
        for value in (evidence.retrieved_at, evidence.published_at)
        if value
    ]
    authoritative = [value for value in authoritative if value is not None]
    if authoritative:
        return all(
            _timestamp_is_fresh(
                value,
                now=now,
                max_age_seconds=max_age_seconds,
            )
            for value in authoritative
        )
    for value in (evidence.inserted_at,):
        parsed = _parse_timestamp(value)
        if parsed is None:
            continue
        if _timestamp_is_fresh(parsed, now=now, max_age_seconds=max_age_seconds):
            return True
    return False


def _timestamp_is_fresh(
    timestamp: datetime,
    *,
    now: datetime,
    max_age_seconds: float,
) -> bool:
    age_seconds = (now - timestamp).total_seconds()
    return -_DECISION_EVIDENCE_CLOCK_SKEW_SECONDS <= age_seconds <= max_age_seconds


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_probability(value: Any) -> float | None:
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return None
    if probability < 0.0 or probability > 1.0:
        return None
    return probability


def _format_probability(value: float | None) -> str:
    if value is None:
        return "unknown"
    try:
        return f"{float(value):.4g}"
    except (TypeError, ValueError):
        return "unknown"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _contract_fingerprint(market: Any) -> str:
    parts = [
        _clean(getattr(market, "ticker", "")),
        _clean(getattr(market, "title", "")),
        _clean(getattr(market, "rules_primary", "")),
        _clean(getattr(market, "rules_secondary", "")),
        "|".join(_clean(str(item)) for item in getattr(market, "settlement_sources", ()) or ()),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:24]
