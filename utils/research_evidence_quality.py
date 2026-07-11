"""Shared source-identity and diversity policy for decision-grade research."""

from __future__ import annotations

import ipaddress
from collections.abc import Collection, Mapping
from typing import Any
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

STRUCTURED_OFFICIAL_SETTLEMENT_CLAIM_TYPES = frozenset(
    {
        "corroboration",
        "official_resolution",
        "resolution",
        "settlement",
        "settlement_source",
        "supporting",
    }
)

_COUNTRY_CODE_SECOND_LEVEL_LABELS = frozenset({"ac", "co", "com", "edu", "gov", "net", "org"})


def research_source_key(
    source_class: object,
    source_name: object,
    source_url: object,
) -> str:
    """Return a normalized independent-source identity, strongest field first."""
    url = str(source_url or "").strip()
    if url:
        try:
            hostname = urlparse(url).hostname
        except ValueError:
            hostname = None
        host = _organization_domain(hostname)
        if host:
            return host
    name = str(source_name or "").strip().lower()
    if name:
        return name
    return ""


def has_reliable_research_source_path(
    evidence: Collection[object],
) -> bool:
    """Derive source diversity and the narrow structured-official exception."""
    if any(_is_structured_official_signal(item) for item in evidence):
        return True
    normalized_keys = {
        source_key
        for item in evidence
        if (
            source_key := research_source_key(
                _field(item, "source_class"),
                _field(item, "source_name"),
                _field(item, "source_url"),
            )
        )
        and source_key != "google.com"
    }
    normalized_classes = _normalized_nonempty([str(_field(item, "source_class") or "") for item in evidence])
    return (
        len(normalized_keys) >= 2
        and len(normalized_classes) >= 2
        and bool(normalized_classes & OFFICIAL_RESEARCH_SOURCE_CLASSES)
    )


def _normalized_nonempty(values: Collection[str]) -> set[str]:
    return {str(value or "").strip().lower() for value in values if str(value or "").strip()}


def _organization_domain(hostname: str | None) -> str:
    host = str(hostname or "").strip().lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return host
    suffix_size = 2
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _COUNTRY_CODE_SECOND_LEVEL_LABELS:
        suffix_size = 3
    return ".".join(labels[-suffix_size:])


def _is_structured_official_signal(evidence: object) -> bool:
    source_class = str(_field(evidence, "source_class") or "").strip().lower()
    claim_type = str(_field(evidence, "claim_type") or "").strip().lower()
    direction = str(_field(evidence, "supports_direction") or "").strip().lower()
    metric_name = str(_field(evidence, "metric_name") or "").strip()
    confidence = _as_float(_field(evidence, "supports_confidence"))
    extraction_confidence = _as_float(_field(evidence, "extraction_confidence"))
    metric_value = _field(evidence, "metric_value")
    has_metric_value = metric_value is not None and (not isinstance(metric_value, str) or bool(metric_value.strip()))
    return (
        source_class in OFFICIAL_RESEARCH_SOURCE_CLASSES
        and claim_type in STRUCTURED_OFFICIAL_SETTLEMENT_CLAIM_TYPES
        and direction in {"yes", "no"}
        and confidence >= 0.6
        and metric_name in STRUCTURED_OFFICIAL_RESEARCH_METRICS
        and (has_metric_value or extraction_confidence >= 0.8)
    )


def _field(evidence: object, name: str) -> Any:
    if isinstance(evidence, Mapping):
        return evidence.get(name)
    return getattr(evidence, name, None)


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
