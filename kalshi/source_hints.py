"""Shadow-only market source hint extraction.

This module preserves settlement-source metadata as a deterministic targeting
plan. It does not affect admission, readiness, execution, or trading behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping


class SourceClass(str, Enum):
    WIRE = "wire"
    NATIONAL_PUBLISHER = "national_publisher"
    OFFICIAL_GOVERNMENT = "official_government"
    LOCAL_MASTHEAD = "local_masthead"


@dataclass(frozen=True, order=True)
class SourceHint:
    canonical_name: str
    domain: str
    source_class: SourceClass
    aliases: tuple[str, ...] = field(default_factory=tuple, compare=False)


@dataclass(frozen=True)
class MarketSourceHints:
    ticker: str
    shadow_only: bool
    hints: tuple[SourceHint, ...]
    search_queries: dict[str, list[str]]
    rejected_labels: dict[str, str]


@dataclass(frozen=True)
class SourceTarget:
    source: SourceHint
    search_queries: tuple[str, ...]
    feed_urls: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MarketSourceTargetPlan:
    ticker: str
    shadow_only: bool
    targets: tuple[SourceTarget, ...]
    rejected_labels: dict[str, str]


@dataclass(frozen=True)
class SourceTargetLogRecord:
    type: str
    ticker: str
    source: str
    domain: str
    hit: bool
    freshness_age_seconds: int | None
    shadow_only: bool


@dataclass(frozen=True)
class MarketSourceHintDiagnostics:
    ticker: str
    mode: str
    shadow_only: bool
    plan: MarketSourceTargetPlan
    counters: dict[str, dict[str, int | None]]
    log_records: list[dict[str, object]]


@dataclass
class _CounterState:
    hits: int = 0
    misses: int = 0
    freshest_age_seconds: int | None = None


@dataclass(frozen=True)
class _RegistryEntry:
    canonical_name: str
    domain: str
    source_class: SourceClass
    aliases: tuple[str, ...]


class SourceRegistry:
    """Alias-aware canonical source registry."""

    def __init__(self, entries: tuple[_RegistryEntry, ...]) -> None:
        self._entries = entries

    @classmethod
    def default(cls) -> "SourceRegistry":
        return cls(_REGISTRY)

    def lookup(self, label: str) -> SourceHint | None:
        normalized = " ".join(label.strip().split()).lower()
        for entry in self._entries:
            if any(normalized == alias.lower() for alias in entry.aliases):
                return _entry_to_hint(entry)
        return None

    def all_sources(self) -> tuple[SourceHint, ...]:
        return tuple(
            sorted(
                (_entry_to_hint(entry) for entry in self._entries),
                key=lambda h: h.canonical_name,
            )
        )


_REGISTRY: tuple[_RegistryEntry, ...] = (
    _RegistryEntry(
        "Associated Press",
        "apnews.com",
        SourceClass.WIRE,
        ("AP", "Associated Press", "The Associated Press"),
    ),
    _RegistryEntry("CNBC", "cnbc.com", SourceClass.NATIONAL_PUBLISHER, ("CNBC",)),
    _RegistryEntry("CNN", "cnn.com", SourceClass.NATIONAL_PUBLISHER, ("CNN",)),
    _RegistryEntry(
        "Department of Defense",
        "defense.gov",
        SourceClass.OFFICIAL_GOVERNMENT,
        ("Department of Defense", "DoD"),
    ),
    _RegistryEntry(
        "Department of State",
        "state.gov",
        SourceClass.OFFICIAL_GOVERNMENT,
        ("Department of State", "State Department"),
    ),
    _RegistryEntry(
        "Fox News",
        "foxnews.com",
        SourceClass.NATIONAL_PUBLISHER,
        ("Fox News", "Fox"),
    ),
    _RegistryEntry("MSNBC", "msnbc.com", SourceClass.NATIONAL_PUBLISHER, ("MSNBC",)),
    _RegistryEntry(
        "Politico", "politico.com", SourceClass.NATIONAL_PUBLISHER, ("Politico",)
    ),
    _RegistryEntry("Reuters", "reuters.com", SourceClass.WIRE, ("Reuters",)),
    _RegistryEntry(
        "Semafor", "semafor.com", SourceClass.NATIONAL_PUBLISHER, ("Semafor",)
    ),
    _RegistryEntry(
        "The Guardian",
        "theguardian.com",
        SourceClass.NATIONAL_PUBLISHER,
        ("Guardian", "The Guardian"),
    ),
    _RegistryEntry(
        "The Information",
        "theinformation.com",
        SourceClass.NATIONAL_PUBLISHER,
        ("The Information",),
    ),
    _RegistryEntry(
        "The New York Times",
        "nytimes.com",
        SourceClass.NATIONAL_PUBLISHER,
        ("NYT", "New York Times", "The New York Times"),
    ),
    _RegistryEntry(
        "The Wall Street Journal",
        "wsj.com",
        SourceClass.NATIONAL_PUBLISHER,
        ("WSJ", "Wall Street Journal", "The Wall Street Journal"),
    ),
    _RegistryEntry(
        "The Washington Post",
        "washingtonpost.com",
        SourceClass.NATIONAL_PUBLISHER,
        ("WaPo", "Washington Post", "The Washington Post"),
    ),
    _RegistryEntry(
        "White House",
        "whitehouse.gov",
        SourceClass.OFFICIAL_GOVERNMENT,
        ("White House", "the White House"),
    ),
)

_GENERIC_LABELS: Mapping[str, str] = {
    "news outlets": "generic_or_unverifiable_label",
    "social media": "generic_or_unverifiable_label",
    "local newspapers": "generic_or_unverifiable_label",
    "verifiable local mastheads": "generic_or_unverifiable_label",
    "official city records": "generic_or_unverifiable_label",
}

_PHOTO_CREDIT_RE = re.compile(
    r"\b[A-Z][A-Za-z ]+\s*/\s*(Getty(?: Images)?|AP|Reuters)\b"
)


def _contains_label(text: str, label: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9]){re.escape(label)}(?![A-Za-z0-9])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _entry_to_hint(entry: _RegistryEntry) -> SourceHint:
    return SourceHint(
        canonical_name=entry.canonical_name,
        domain=entry.domain,
        source_class=entry.source_class,
        aliases=entry.aliases,
    )


def _local_hint(label: str, domain: str) -> SourceHint:
    return SourceHint(
        canonical_name=label.strip(),
        domain=domain.strip().lower(),
        source_class=SourceClass.LOCAL_MASTHEAD,
        aliases=(label.strip(),),
    )


def canonicalize_source_label(
    label: str,
    *,
    local_mastheads: Mapping[str, str] | None = None,
) -> SourceHint | None:
    """Return a validated canonical source hint for one label, or None.

    Generic source categories, photo/wire credits, and unknown local mastheads
    are intentionally rejected because they are unsafe as source-target priors.
    """
    normalized = " ".join(label.strip().split())
    if not normalized:
        return None
    if _PHOTO_CREDIT_RE.search(normalized) or "/" in normalized:
        return None
    if normalized.lower() in _GENERIC_LABELS:
        return None

    for entry in _REGISTRY:
        for alias in entry.aliases:
            if normalized.lower() == alias.lower():
                return _entry_to_hint(entry)
            prefix = f"official statements from {alias}".lower()
            if normalized.lower() == prefix:
                return _entry_to_hint(entry)

    if local_mastheads:
        for masthead, domain in local_mastheads.items():
            if normalized.lower() == masthead.lower():
                return _local_hint(masthead, domain)

    return None


def _extract_hints(
    text: str,
    local_mastheads: Mapping[str, str] | None,
) -> tuple[SourceHint, ...]:
    by_name: dict[str, SourceHint] = {}
    for entry in _REGISTRY:
        if any(_contains_label(text, alias) for alias in entry.aliases):
            hint = _entry_to_hint(entry)
            by_name[hint.canonical_name] = hint

    if local_mastheads:
        for masthead, domain in local_mastheads.items():
            if _contains_label(text, masthead):
                hint = _local_hint(masthead, domain)
                by_name[hint.canonical_name] = hint

    return tuple(sorted(by_name.values(), key=lambda h: h.canonical_name))


def _extract_rejections(text: str) -> dict[str, str]:
    rejected: dict[str, str] = {}
    for match in _PHOTO_CREDIT_RE.finditer(text):
        rejected[match.group(0)] = "photo_credit_or_wire_credit"
    for label, reason in _GENERIC_LABELS.items():
        if _contains_label(text, label):
            rejected[label] = reason
    return dict(sorted(rejected.items()))


def _query_for(domain: str, title: str) -> list[str]:
    safe_title = title.replace('"', "'")
    return [f'site:{domain} "{safe_title}"']


def build_market_source_hints(
    *,
    ticker: str,
    title: str,
    rules_text: str = "",
    settlement_text: str = "",
    local_mastheads: Mapping[str, str] | None = None,
) -> MarketSourceHints:
    """Build a deterministic, shadow-only source-target plan for a market."""
    text = "\n".join(part for part in (rules_text, settlement_text) if part)
    hints = _extract_hints(text, local_mastheads)
    search_queries = {
        hint.canonical_name: _query_for(hint.domain, title)
        for hint in hints
    }
    return MarketSourceHints(
        ticker=ticker,
        shadow_only=True,
        hints=hints,
        search_queries=search_queries,
        rejected_labels=_extract_rejections(text),
    )


def _metadata_text(metadata: Mapping[str, str]) -> tuple[str, str]:
    """Return source-hint text from normalized public market metadata."""
    rules_parts: list[str] = []
    settlement_parts: list[str] = []
    for key, value in metadata.items():
        if not value:
            continue
        if "settlement" in key.lower():
            settlement_parts.append(value)
        else:
            rules_parts.append(value)
    return "\n".join(rules_parts), "\n".join(settlement_parts)


def build_market_source_target_plan(
    market: object,
    *,
    feed_url_builders: Mapping[str, Callable[[str], str]] | None = None,
    local_mastheads: Mapping[str, str] | None = None,
) -> MarketSourceTargetPlan:
    """Build shadow-only source-scoped query/feed targets from market metadata.

    This is a pure planning helper: it does not poll feeds, mutate counters,
    affect matching, admission, readiness, execution, or trading behavior.
    Future consumers must fail closed: use existing bounded/rate-limited/cached
    fetch paths only, treat fetch/cache/rate-limit failures as zero diagnostic
    hits, and never broaden search or create executable signals from missing or
    unvalidated hints.
    """
    metadata = getattr(market, "market_metadata", {}) or {}
    rules_text, settlement_text = _metadata_text(metadata)
    hints = build_market_source_hints(
        ticker=getattr(market, "ticker"),
        title=getattr(market, "title"),
        rules_text=rules_text,
        settlement_text=settlement_text,
        local_mastheads=local_mastheads,
    )
    builders = feed_url_builders or {}
    targets: list[SourceTarget] = []
    for hint in hints.hints:
        queries = tuple(hints.search_queries.get(hint.canonical_name, ()))
        feed_urls = tuple(
            build_url(query)
            for query in queries
            for build_url in builders.values()
        )
        targets.append(SourceTarget(source=hint, search_queries=queries, feed_urls=feed_urls))
    return MarketSourceTargetPlan(
        ticker=hints.ticker,
        shadow_only=True,
        targets=tuple(targets),
        rejected_labels=hints.rejected_labels,
    )


def build_market_source_hint_diagnostics(
    market: object,
    *,
    mode: str = "off",
    emit_records: bool = False,
    feed_url_builders: Mapping[str, Callable[[str], str]] | None = None,
    local_mastheads: Mapping[str, str] | None = None,
) -> MarketSourceHintDiagnostics:
    """Build default-off, shadow-only MarketSourceHints operator diagnostics.

    The wrapper is intentionally pure and config-independent: callers pass an
    already-validated mode and record flag. ``off`` returns an empty diagnostic
    surface without building source plans or invoking feed URL builders.
    ``shadow`` and ``advisory`` expose only in-memory diagnostics; they do not
    poll, fetch, write DBs, affect readiness/admission/scoring/routing, or
    create executable/trading signals. No hit/miss log records are emitted here
    because this helper does not perform observations.
    """
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"off", "shadow", "advisory"}:
        raise ValueError(
            "mode must be one of off|shadow|advisory, "
            f"got '{mode}'"
        )

    if normalized_mode == "off":
        plan = MarketSourceTargetPlan(
            ticker=getattr(market, "ticker"),
            shadow_only=True,
            targets=(),
            rejected_labels={},
        )
        return MarketSourceHintDiagnostics(
            ticker=plan.ticker,
            mode=normalized_mode,
            shadow_only=True,
            plan=plan,
            counters={},
            log_records=[],
        )

    plan = build_market_source_target_plan(
        market,
        feed_url_builders=feed_url_builders,
        local_mastheads=local_mastheads,
    )
    counters = SourceTargetCounters(plan)
    return MarketSourceHintDiagnostics(
        ticker=plan.ticker,
        mode=normalized_mode,
        shadow_only=True,
        plan=plan,
        counters=counters.snapshot(),
        log_records=counters.log_records() if emit_records else [],
    )


class SourceTargetCounters:
    """In-memory shadow counters for per-market source target observations."""

    def __init__(self, plan: MarketSourceTargetPlan) -> None:
        self._plan = plan
        self._sources = {
            target.source.canonical_name: target.source
            for target in plan.targets
        }
        self._state = {
            name: _CounterState()
            for name in self._sources
        }
        self._records: list[SourceTargetLogRecord] = []

    def record_query_result(
        self,
        source: str,
        *,
        hit: bool,
        age_seconds: int | None = None,
    ) -> None:
        source_hint = self._sources[source]
        state = self._state[source]
        if hit:
            state.hits += 1
            if age_seconds is not None:
                if state.freshest_age_seconds is None:
                    state.freshest_age_seconds = age_seconds
                else:
                    state.freshest_age_seconds = min(state.freshest_age_seconds, age_seconds)
        else:
            state.misses += 1
        self._records.append(
            SourceTargetLogRecord(
                type="MARKET_SOURCE_HINT_SHADOW",
                ticker=self._plan.ticker,
                source=source_hint.canonical_name,
                domain=source_hint.domain,
                hit=hit,
                freshness_age_seconds=age_seconds,
                shadow_only=True,
            )
        )

    def snapshot(self) -> dict[str, dict[str, int | None]]:
        return {
            source: {
                "hits": state.hits,
                "misses": state.misses,
                "freshest_age_seconds": state.freshest_age_seconds,
            }
            for source, state in self._state.items()
        }

    def log_records(self) -> list[dict[str, object]]:
        return [record.__dict__.copy() for record in self._records]
