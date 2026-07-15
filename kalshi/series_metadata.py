"""Typed Kalshi series metadata used by shadow-only targeting.

The normalized shape is diagnostic input only. It must not affect admission,
probability, readiness, execution, or trading decisions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib.parse import urlparse


@dataclass(frozen=True)
class SettlementSource:
    label: str
    url: str = ""
    domain: str = ""


@dataclass(frozen=True)
class KalshiSeriesMetadata:
    series_ticker: str
    title: str = ""
    category: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    settlement_sources: tuple[SettlementSource, ...] = field(default_factory=tuple)
    contract_terms_url: str = ""
    rules_primary: str = ""
    rules_secondary: str = ""
    fee_multiplier: str = ""
    fee_type: str = ""
    can_close_early: bool | None = None
    raw_payload: Mapping[str, Any] = field(default_factory=dict)
    fee_multiplier_decimal: Decimal | None = None
    metadata_updated_at: datetime | None = None
    raw_payload_hash: str = ""
    snapshot_at: datetime | None = None


def _domain_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.netloc or parsed.path).lower().removeprefix("www.")


def _as_tags(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts = raw.replace(",", " ").split()
    elif isinstance(raw, (list, tuple, set)):
        parts = [str(part) for part in raw if str(part).strip()]
    else:
        parts = []
    seen: set[str] = set()
    tags: list[str] = []
    for part in parts:
        tag = " ".join(str(part).strip().split())
        key = tag.lower()
        if tag and key not in seen:
            tags.append(tag)
            seen.add(key)
    return tuple(tags)


def _source_from_raw(raw: Any) -> SettlementSource | None:
    if isinstance(raw, str):
        label = " ".join(raw.strip().split())
        return SettlementSource(label=label) if label else None
    if not isinstance(raw, Mapping):
        return None
    label = str(
        raw.get("name")
        or raw.get("label")
        or raw.get("source")
        or raw.get("title")
        or ""
    ).strip()
    url = str(raw.get("url") or raw.get("source_url") or raw.get("href") or "").strip()
    domain = str(raw.get("domain") or "").strip().lower().removeprefix("www.")
    if not domain:
        domain = _domain_from_url(url)
    if not label and domain:
        label = domain
    if not label and not url:
        return None
    return SettlementSource(label=" ".join(label.split()), url=url, domain=domain)


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _payload_hash(payload: Mapping[str, Any]) -> str:
    def json_safe(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(item) for item in value]
        if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > 12_000:
            sign = "-" if value < 0 else ""
            return {"__oversized_int_hex__": f"{sign}{abs(value):x}"}
        return value

    canonical = json.dumps(
        json_safe(payload), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_series_payload(
    payload: Mapping[str, Any],
    *,
    snapshot_at: datetime | None = None,
) -> KalshiSeriesMetadata:
    """Normalize a Kalshi ``/series/{ticker}`` or list-entry payload."""

    inner = payload.get("series", payload)
    if not isinstance(inner, Mapping):
        raise ValueError("series payload missing series object")

    raw_sources = inner.get("settlement_sources") or inner.get("settlement_source_urls") or ()
    sources = tuple(
        source
        for raw_source in (raw_sources if isinstance(raw_sources, (list, tuple)) else (raw_sources,))
        if (source := _source_from_raw(raw_source)) is not None
    )
    return KalshiSeriesMetadata(
        series_ticker=str(inner.get("series_ticker") or inner.get("ticker") or "").upper(),
        title=str(inner.get("title") or inner.get("name") or ""),
        category=str(inner.get("category") or ""),
        tags=_as_tags(inner.get("tags") or inner.get("tag") or inner.get("event_tags")),
        settlement_sources=sources,
        contract_terms_url=str(inner.get("contract_terms_url") or ""),
        rules_primary=str(inner.get("rules_primary") or ""),
        rules_secondary=str(inner.get("rules_secondary") or ""),
        fee_multiplier=str(inner.get("fee_multiplier") or ""),
        fee_type=str(inner.get("fee_type") or ""),
        can_close_early=(
            bool(inner["can_close_early"]) if "can_close_early" in inner else None
        ),
        raw_payload=dict(inner),
        fee_multiplier_decimal=_optional_decimal(inner.get("fee_multiplier")),
        metadata_updated_at=_optional_datetime(inner.get("last_updated_ts")),
        raw_payload_hash=_payload_hash(inner),
        snapshot_at=snapshot_at or datetime.now(timezone.utc),
    )


def normalize_series_list(payloads: list[Mapping[str, Any]]) -> dict[str, KalshiSeriesMetadata]:
    normalized: dict[str, KalshiSeriesMetadata] = {}
    for payload in payloads:
        meta = normalize_series_payload(payload)
        if meta.series_ticker:
            normalized[meta.series_ticker] = meta
    return normalized
