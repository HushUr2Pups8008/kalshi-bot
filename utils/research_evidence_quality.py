"""Shared source-identity and diversity policy for decision-grade research."""

from __future__ import annotations

from collections.abc import Collection
from urllib.parse import urlparse


OFFICIAL_RESEARCH_SOURCE_CLASSES = frozenset(
    {
        "official",
        "official_primary",
        "official_source",
        "resolution_source",
        "rules_source",
    }
)

STRUCTURED_OFFICIAL_RESEARCH_METRICS = frozenset(
    {
        "cpi_monthly_change_single_decimal",
        "gdpnow_real_gdp_growth_saar",
        "nws_daily_high_temp_f",
    }
)


def research_source_key(
    source_class: object,
    source_name: object,
    source_url: object,
) -> str:
    """Return a normalized independent-source identity, strongest field first."""
    url = str(source_url or "").strip()
    if url:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            return host
    name = str(source_name or "").strip().lower()
    if name:
        return name
    return str(source_class or "").strip().lower()


def has_reliable_research_source_path(
    *,
    source_keys: Collection[str],
    source_classes: Collection[str],
    has_structured_official_signal: bool,
) -> bool:
    """Require identity and class diversity, with one validated narrow exception."""
    if has_structured_official_signal:
        return True
    normalized_keys = _normalized_nonempty(source_keys)
    normalized_classes = _normalized_nonempty(source_classes)
    return (
        len(normalized_keys) >= 2
        and len(normalized_classes) >= 2
        and bool(normalized_classes & OFFICIAL_RESEARCH_SOURCE_CLASSES)
    )


def _normalized_nonempty(values: Collection[str]) -> set[str]:
    return {str(value or "").strip().lower() for value in values if str(value or "").strip()}
