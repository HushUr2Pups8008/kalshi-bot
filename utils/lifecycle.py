"""Deterministic telemetry lineage and settlement-source attribution helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse


def build_lifecycle_id(
    *,
    venue: object,
    ticker: object,
    source: object,
    url: object,
    headline: object,
    published: object,
) -> str:
    """Return a stable identifier for one venue/news/market lineage."""
    payload = {
        "headline": _normalized_text(headline),
        "published": _normalized_published(published),
        "source": _normalized_text(source),
        "ticker": _normalized_text(ticker),
        "url": _normalized_text(url),
        "venue": _normalized_text(venue),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"lc-{hashlib.sha256(encoded).hexdigest()[:32]}"


def build_research_lifecycle_id(
    *,
    ticker: object,
    research_run_id: object,
    contract_fingerprint: object,
) -> str:
    """Return the stable lineage owned by one durable research admission claim."""
    payload = {
        "contract_fingerprint": _normalized_text(contract_fingerprint),
        "research_run_id": _normalized_text(research_run_id),
        "ticker": _normalized_text(ticker),
        "venue": "kalshi",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"lc-{hashlib.sha256(encoded).hexdigest()[:32]}"


def settlement_source_match(
    *,
    source: object,
    url: object,
    source_hint_domain: object,
    settlement_sources: Collection[object] | object,
) -> bool | None:
    """Compare usable news and settlement identities without truthy coercion."""
    evidence_identities: set[str] = set()
    _add_label_identity(evidence_identities, source)
    _add_url_identity(evidence_identities, url)
    _add_domain_identity(evidence_identities, source_hint_domain)

    settlement_identities: set[str] = set()
    values: Collection[object]
    if isinstance(settlement_sources, (str, bytes)) or not isinstance(
        settlement_sources, Collection
    ):
        values = (settlement_sources,)
    else:
        values = settlement_sources
    for item in values:
        if item is None:
            continue
        if isinstance(item, str):
            if not _add_url_identity(settlement_identities, item):
                if _looks_like_domain(item):
                    _add_domain_identity(settlement_identities, item)
                elif _looks_like_source_label(item):
                    _add_label_identity(settlement_identities, item)
            continue
        _add_label_identity(settlement_identities, getattr(item, "label", None))
        _add_label_identity(settlement_identities, getattr(item, "name", None))
        _add_domain_identity(settlement_identities, getattr(item, "domain", None))
        _add_url_identity(settlement_identities, getattr(item, "url", None))

    if not evidence_identities or not settlement_identities:
        return None
    return bool(evidence_identities & settlement_identities)


def strict_optional_bool(value: Any) -> bool | None:
    """Return only actual booleans; invalid transport values become unknown."""
    return value if isinstance(value, bool) else None


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalized_published(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.isoformat()
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return _normalized_text(value)


def _normalized_host(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"//{raw}")
        host = parsed.hostname or ""
    except ValueError:
        return ""
    host = host.strip().lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def _add_label_identity(target: set[str], value: object) -> bool:
    normalized = _normalized_text(value)
    if not normalized:
        return False
    target.add(f"label:{normalized}")
    return True


def _add_domain_identity(target: set[str], value: object) -> bool:
    host = _normalized_host(value)
    if not host:
        return False
    target.add(f"host:{host}")
    return True


def _add_url_identity(target: set[str], value: object) -> bool:
    raw = str(value or "").strip()
    if not raw or "://" not in raw:
        return False
    return _add_domain_identity(target, raw)


def _looks_like_domain(value: object) -> bool:
    raw = str(value or "").strip()
    return bool(raw and not any(char.isspace() for char in raw) and "." in raw)


def _looks_like_source_label(value: object) -> bool:
    normalized = _normalized_text(value)
    if not normalized or len(normalized) > 80:
        return False
    if any(mark in normalized for mark in (".", "!", "?", ";", ":")):
        return False
    return len(normalized.split()) <= 4
