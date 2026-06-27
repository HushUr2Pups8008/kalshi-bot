"""Real web-research gate for ambiguous prediction-market signals.

The gate is intentionally structured-data first. LLMs may synthesize the
evidence later, but the money path needs auditable queries, sources, snippets,
and a deterministic verdict before a neutral/no-keyword row can become a trade.
"""
from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable


class ResearchStatus(str, Enum):
    TRADE_CANDIDATE = "trade_candidate"
    CONTINUE_RESEARCHING = "continue_researching"
    RESEARCHED_SKIP_NO_EDGE = "researched_skip_no_edge"
    RESEARCHED_SKIP_AMBIGUOUS = "researched_skip_ambiguous"
    HARD_CAPITAL_BLOCK = "hard_capital_block"
    RESEARCH_PROVIDER_ERROR = "research_provider_error"
    RESEARCH_ADJUDICATOR_ERROR = "research_adjudicator_error"


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


@dataclass(frozen=True)
class ResearchVerdict:
    status: ResearchStatus
    attempted: bool
    queries: list[ResearchQuery] = field(default_factory=list)
    evidence: list[ResearchEvidence] = field(default_factory=list)
    summary: str = ""
    skip_reason: str | None = None
    force_side: str | None = None
    estimated_probability: float | None = None
    confidence: float | None = None

    def log_fields(self) -> dict[str, object]:
        urls = [item.source_url for item in self.evidence if item.source_url]
        settlement_hits = [
            item.source_url
            for item in self.evidence
            if item.source_class in {"resolution_source", "official_primary"}
        ]
        return {
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


SearchProvider = Callable[[ResearchQuery], Awaitable[list[ResearchEvidence]]]
DirectFetcher = Callable[[str, str, str], Awaitable[ResearchEvidence | None]]
ResearchAdjudicator = Callable[..., Awaitable[dict[str, Any] | None]]
DossierStore = Any


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _domain_from_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value if "://" in value else f"https://{value}")
    return parsed.netloc.lower().removeprefix("www.")


def _query_site_domain(query: str) -> str:
    match = re.search(r"\bsite:([^\s]+)", query or "", re.I)
    return _domain_from_url(match.group(1)) if match else ""


def _source_domain(source_name: str) -> str:
    cleaned = _clean(source_name).lower()
    if "." in cleaned:
        return _domain_from_url(cleaned)
    known = {
        "associated press": "apnews.com",
        "ap": "apnews.com",
        "bloomberg": "bloomberg.com",
        "eia": "eia.gov",
        "kalshi": "kalshi.com",
        "opec": "opec.org",
        "reuters": "reuters.com",
    }
    return known.get(cleaned, "")


def _domains_match(actual: str, expected: str) -> bool:
    actual = _domain_from_url(actual)
    expected = _domain_from_url(expected)
    return bool(actual and expected and (actual == expected or actual.endswith(f".{expected}")))


def _classify_evidence_source(query: ResearchQuery, source_name: str, source_url: str) -> str:
    query_site = _query_site_domain(query.query)
    url_domain = _domain_from_url(source_url)
    name_domain = _source_domain(source_name)
    if query.source_class in {"resolution_source", "official_primary"} and query_site:
        if _domains_match(url_domain, query_site) or _domains_match(name_domain, query_site):
            return query.source_class
    official_domains = {
        "api.eia.gov",
        "eia.gov",
        "federalreserve.gov",
        "kalshi.com",
        "opec.org",
        "whitehouse.gov",
    }
    if any(_domains_match(url_domain, domain) for domain in official_domains):
        return "official_primary"
    if url_domain in {"reuters.com", "apnews.com", "bloomberg.com"} or name_domain in {
        "reuters.com",
        "apnews.com",
        "bloomberg.com",
    }:
        return "reputable_secondary"
    if query.source_class not in {"resolution_source", "official_primary"}:
        return query.source_class
    return "other"


def _market_text(market: Any) -> str:
    return " ".join(
        _clean(getattr(market, key, ""))
        for key in ("title", "subtitle", "rules_primary", "rules_secondary")
    )


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
    return _clean(" ".join(part for part in parts if part))[:limit]


def build_research_queries(news: Any, market: Any) -> list[ResearchQuery]:
    """Build source-classed web queries from market rules and trigger evidence."""

    title = _clean(getattr(market, "title", ""))
    headline = _clean(getattr(news, "headline", ""))
    rules = _market_text(market)
    combined = f"{title} {headline} {rules}"
    ticker = _clean(getattr(market, "ticker", ""))

    queries: list[ResearchQuery] = []
    for source in getattr(market, "settlement_sources", ()) or ():
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
    has_settlement_query = any(
        query.source_class in {"resolution_source", "official_primary"} for query in queries
    )
    if not has_settlement_query:
        terms_domain = _domain_from_url(_clean(getattr(market, "contract_terms_url", "")))
        if terms_domain:
            queries.append(
                ResearchQuery(
                    query=f"site:{terms_domain} {title or ticker}",
                    query_intent="resolution_source",
                    source_class="resolution_source",
                )
            )
        else:
            rules_fragment = _query_fragment(title or ticker, rules, "official resolution source")
            if rules_fragment:
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

    if headline:
        queries.append(
            ResearchQuery(
                query=f'"{headline[:140]}"',
                query_intent="trigger_article",
                source_class="trigger_source",
            )
        )
    if title:
        queries.append(
            ResearchQuery(
                query=title,
                query_intent="broad_context",
                source_class="reputable_secondary",
            )
        )
    return _dedupe_queries(queries)


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
) -> ResearchVerdict:
    queries = list(queries or [])
    if _direction_reason_conflict(model_direction, model_reason):
        return ResearchVerdict(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Research direction conflicts with reasoning; more evidence required.",
            skip_reason="direction_reason_conflict",
        )

    if not evidence:
        return ResearchVerdict(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="No web evidence retrieved; source frontier is not exhausted.",
            skip_reason="no_research_hits",
        )

    settlement_hits = [
        item
        for item in evidence
        if item.source_class in {"resolution_source", "official_primary"}
    ]
    if not settlement_hits:
        return ResearchVerdict(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Missing settlement-aligned or official evidence.",
            skip_reason="missing_resolution_source",
        )

    if len({item.source_url for item in evidence if item.source_url}) < 2:
        return ResearchVerdict(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Insufficient independent corroboration.",
            skip_reason="insufficient_corroboration",
        )

    side = model_direction if model_direction in {"yes", "no"} else None
    conf = max(0.0, min(1.0, float(model_confidence or 0.0)))
    if side is None or conf <= 0.0:
        return ResearchVerdict(
            status=ResearchStatus.RESEARCHED_SKIP_AMBIGUOUS,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Research did not produce a directional probability.",
            skip_reason="ambiguous_direction",
        )

    p_yes = _coerce_probability(estimated_probability_yes)
    if p_yes is None:
        return ResearchVerdict(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Research did not produce an explicit YES resolution probability.",
            skip_reason="missing_estimated_probability",
        )
    if (side == "yes" and p_yes <= 0.5) or (side == "no" and p_yes >= 0.5):
        return ResearchVerdict(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Research probability conflicts with the adjudicated direction.",
            skip_reason="probability_direction_conflict",
        )
    yes_ask = yes_ask if yes_ask is not None else 1.0
    no_ask = no_ask if no_ask is not None else 1.0
    executable_ask = yes_ask if side == "yes" else no_ask
    if live_mode and (executable_ask <= 0.03 or executable_ask >= 0.97):
        return ResearchVerdict(
            status=ResearchStatus.HARD_CAPITAL_BLOCK,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Executable price is in live tail-risk band.",
            skip_reason="no_trade_capital_protection",
        )

    spread_buffer = 0.01
    min_edge = 0.04 if live_mode else 0.02
    yes_edge = p_yes - yes_ask - spread_buffer
    no_edge = (1.0 - p_yes) - no_ask - spread_buffer
    if side == "yes" and yes_edge >= min_edge:
        return ResearchVerdict(
            status=ResearchStatus.TRADE_CANDIDATE,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Research supports YES and executable net edge clears threshold.",
            force_side="yes",
            estimated_probability=p_yes,
            confidence=conf,
        )
    if side == "no" and no_edge >= min_edge:
        return ResearchVerdict(
            status=ResearchStatus.TRADE_CANDIDATE,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Research supports NO and executable net edge clears threshold.",
            force_side="no",
            estimated_probability=p_yes,
            confidence=conf,
        )
    return ResearchVerdict(
        status=ResearchStatus.RESEARCHED_SKIP_NO_EDGE,
        attempted=True,
        queries=queries,
        evidence=evidence,
        summary="Research completed but neither side clears executable net edge.",
        skip_reason="negative_net_edge_after_costs",
    )


def _rss_search(query: ResearchQuery, *, timeout: float = 5.0, limit: int = 3) -> list[ResearchEvidence]:
    params = urllib.parse.urlencode({"q": query.query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    url = f"https://news.google.com/rss/search?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "kalshi-bot-research/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        raw = response.read(300_000)
    root = ET.fromstring(raw)
    out: list[ResearchEvidence] = []
    retrieved_at = _utc_now_iso()
    for item in root.findall(".//item")[:limit]:
        title = html.unescape(_clean(item.findtext("title")))
        link = html.unescape(_clean(item.findtext("link")))
        source = item.findtext("source") or _domain_from_url(link) or "Google News"
        description = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        snippet = html.unescape(_clean(description))
        out.append(
            ResearchEvidence(
                source_class=_classify_evidence_source(query, _clean(source), link),
                source_name=_clean(source),
                source_url=link,
                title=title,
                snippet=snippet[:500],
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                published_at=_clean(item.findtext("pubDate")) or None,
                retrieved_at=retrieved_at,
            )
        )
    return out


async def default_search_provider(query: ResearchQuery) -> list[ResearchEvidence]:
    return await asyncio.to_thread(_rss_search, query)


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
    if not cleaned_url:
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
        targets.append((terms_url, "resolution_source", "contract_terms"))
    for source in getattr(market, "settlement_sources", ()) or ():
        url = _clean(getattr(source, "url", ""))
        if url:
            targets.append((url, "resolution_source", "settlement_source"))
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for url, source_class, claim_type in targets:
        if url in seen:
            continue
        seen.add(url)
        out.append((url, source_class, claim_type))
    return out


def _research_prompt(
    *,
    news: Any,
    market: Any,
    queries: list[ResearchQuery],
    evidence: list[ResearchEvidence],
) -> str:
    lines = [
        "You are adjudicating a prediction-market research dossier.",
        "Use only the cited evidence below. Return JSON with keys: direction, estimated_probability_yes, confidence, reason.",
        "direction must be yes, no, or neutral. estimated_probability_yes and confidence are 0.0 to 1.0.",
        "estimated_probability_yes is the probability the market resolves YES, not confidence in your conclusion.",
        "If evidence is missing, contradictory, or not settlement-aligned, use neutral.",
        "",
        f"MARKET TICKER: {_clean(getattr(market, 'ticker', ''))}",
        f"MARKET TITLE: {_clean(getattr(market, 'title', ''))}",
        f"RULES PRIMARY: {_clean(getattr(market, 'rules_primary', ''))}",
        f"RULES SECONDARY: {_clean(getattr(market, 'rules_secondary', ''))}",
        f"TRIGGER HEADLINE: {_clean(getattr(news, 'headline', ''))}",
        f"TRIGGER SOURCE: {_clean(getattr(news, 'source', ''))}",
        "",
        "QUERIES:",
    ]
    lines.extend(f"- [{query.query_intent}] {query.query}" for query in queries)
    lines.append("")
    lines.append("EVIDENCE:")
    for item in evidence[:12]:
        lines.append(
            "- "
            f"class={item.source_class} source={item.source_name} url={item.source_url} "
            f"title={item.title} snippet={item.snippet[:500]}"
        )
    return "\n".join(lines)


async def default_ollama_adjudicator(
    *,
    evidence: list[ResearchEvidence],
    queries: list[ResearchQuery],
    news: Any,
    market: Any,
) -> dict[str, Any] | None:
    prompt = _research_prompt(news=news, market=market, queries=queries, evidence=evidence)
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
) -> ResearchVerdict:
    queries = build_research_queries(news, market)[:max_queries]
    ticker = _clean(getattr(market, "ticker", ""))
    contract_fingerprint = _contract_fingerprint(market)
    cached_evidence: list[ResearchEvidence] = []
    if dossier_store is not None and ticker:
        try:
            cached_evidence = await dossier_store.get_recent_evidence(ticker)
        except Exception:
            cached_evidence = []
    fresh_evidence: list[ResearchEvidence] = []
    estimated_probability_yes: float | None = None
    usable_cached_evidence = _usable_cached_evidence(cached_evidence, contract_fingerprint)
    if _has_sufficient_dossier_evidence(usable_cached_evidence, contract_fingerprint):
        evidence = usable_cached_evidence
    else:
        evidence = list(usable_cached_evidence)
        existing = {item.source_url for item in evidence if item.source_url}
        provider_errors: list[Exception] = []
        fetcher = direct_fetcher or default_direct_fetcher
        for url, source_class, claim_type in _direct_source_targets(market):
            try:
                item = await asyncio.wait_for(
                    fetcher(url, source_class, claim_type),
                    timeout=research_timeout_seconds,
                )
            except TimeoutError:
                continue
            except Exception:
                continue
            if item is None:
                continue
            identity = item.source_url or hashlib.sha256(
                f"{item.source_name}|{item.title}|{item.snippet}".encode("utf-8")
            ).hexdigest()
            if identity in existing:
                continue
            existing.add(identity)
            item = replace(item, contract_fingerprint=contract_fingerprint)
            evidence.append(item)
            fresh_evidence.append(item)

        direct_domains = {
            _domain_from_url(item.source_url)
            for item in evidence
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
        ]
        provider = search_provider or default_search_provider
        try:
            evidence_nested = await asyncio.wait_for(
                asyncio.gather(
                    *(provider(query) for query in provider_queries),
                    return_exceptions=True,
                ),
                timeout=research_timeout_seconds,
            )
        except TimeoutError:
            return ResearchVerdict(
                status=ResearchStatus.CONTINUE_RESEARCHING,
                attempted=True,
                queries=queries,
                evidence=evidence,
                summary="Research search providers timed out before enough evidence was retrieved.",
                skip_reason="research_timeout",
            )
        for result in evidence_nested:
            if isinstance(result, Exception):
                provider_errors.append(result)
                continue
            for item in result:
                identity = item.source_url or hashlib.sha256(
                    f"{item.source_name}|{item.title}|{item.snippet}".encode("utf-8")
                ).hexdigest()
                if identity in existing:
                    continue
                existing.add(identity)
                item = replace(item, contract_fingerprint=contract_fingerprint)
                evidence.append(item)
                fresh_evidence.append(item)
        if provider_errors:
            return ResearchVerdict(
                status=ResearchStatus.RESEARCH_PROVIDER_ERROR,
                attempted=True,
                queries=queries,
                evidence=evidence,
                summary="Research provider failed before the source frontier could be trusted.",
                skip_reason="research_provider_error",
            )
    if evidence:
        adjudicate = adjudicator or default_ollama_adjudicator
        try:
            adjudication = await asyncio.wait_for(
                adjudicate(
                    evidence=evidence,
                    queries=queries,
                    news=news,
                    market=market,
                ),
                timeout=research_timeout_seconds,
            )
        except TimeoutError:
            return ResearchVerdict(
                status=ResearchStatus.CONTINUE_RESEARCHING,
                attempted=True,
                queries=queries,
                evidence=evidence,
                summary="Research adjudication timed out before producing a verdict.",
                skip_reason="research_timeout",
            )
        except Exception:
            return ResearchVerdict(
                status=ResearchStatus.RESEARCH_ADJUDICATOR_ERROR,
                attempted=True,
                queries=queries,
                evidence=evidence,
                summary="Research adjudicator failed before producing a verdict.",
                skip_reason="research_adjudicator_error",
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
        else:
            return ResearchVerdict(
                status=ResearchStatus.RESEARCH_ADJUDICATOR_ERROR,
                attempted=True,
                queries=queries,
                evidence=evidence,
                summary="Research adjudicator returned no parseable verdict.",
                skip_reason="research_adjudicator_error",
            )
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
    )
    if dossier_store is not None and ticker:
        run_id = "rr-" + hashlib.sha256(
            f"{ticker}|{getattr(news, 'headline', '')}|{len(evidence)}|{verdict.status.value}".encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        try:
            if hasattr(dossier_store, "record_research_run"):
                await dossier_store.record_research_run(
                    ticker,
                    run_id,
                    trigger_headline=_clean(getattr(news, "headline", "")),
                    trigger_source=_clean(getattr(news, "source", "")),
                    attempted=True,
                    summary=verdict.summary,
                    verdict_status=verdict.status.value,
                    skip_reason=verdict.skip_reason,
                    force_side=verdict.force_side,
                    estimated_probability=verdict.estimated_probability,
                    confidence=verdict.confidence,
                    queries=queries,
                    evidence=fresh_evidence,
                )
            else:
                for item in fresh_evidence:
                    await dossier_store.add_evidence(ticker, run_id, item)
        except Exception:
            pass
    return verdict


_DOSSIER_MAX_AGE_SECONDS = 6 * 60 * 60


def _usable_cached_evidence(
    evidence: list[ResearchEvidence],
    contract_fingerprint: str,
) -> list[ResearchEvidence]:
    return [
        item
        for item in evidence
        if item.contract_fingerprint == contract_fingerprint and _is_fresh_evidence(item)
    ]


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
    has_resolution = any(
        item.source_class in {"resolution_source", "official_primary"} for item in evidence
    )
    urls = {item.source_url for item in evidence if item.source_url}
    return has_resolution and len(urls) >= 2


def _is_fresh_evidence(evidence: ResearchEvidence) -> bool:
    parsed = _parse_timestamp(evidence.published_at)
    if parsed is None:
        parsed = _parse_timestamp(evidence.retrieved_at or evidence.inserted_at)
    if parsed is None:
        return False
    return (datetime.now(timezone.utc) - parsed).total_seconds() <= _DOSSIER_MAX_AGE_SECONDS


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
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
