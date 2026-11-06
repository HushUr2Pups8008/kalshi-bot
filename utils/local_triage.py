"""Paper-only market screen for local settlement classification.

The screen is deterministic and provisional. It does not estimate a
probability, admit a live candidate, or place an order. Missing depth stays
missing.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

CLASSES = (
    "countable",
    "lookup",
    "market_price",
    "opinion",
    "sports",
    "other",
    "ambiguous",
)
_CONTENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "countable",
        (
            "count",
            "counts",
            "counted",
            "number of",
            "at least",
            "tweet",
            "tweets",
            "post",
            "posts",
        ),
    ),
    (
        "lookup",
        (
            "federal register",
            "bureau of labor",
            "white house",
            "congress.gov",
            "as published by",
            "official source",
            "official record",
            "official website",
        ),
    ),
    (
        "market_price",
        (
            "closing price",
            "settlement price",
            "share price",
            "market price",
            "index level",
        ),
    ),
    (
        "opinion",
        (
            "poll",
            "polls",
            "approval rating",
            "survey",
            "surveys",
            "opinion",
        ),
    ),
)
_MVE_TYPES = {"mve", "multivariate", "multivariate_event"}


def _decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    return value


def _cents(raw: Any) -> int | None:
    """Dollar price to integer cents. ``0.42`` is 42. A value above 1 dollar is not a quote."""
    value = _decimal(raw)
    if value is None:
        return None
    cents = (value * Decimal("100")).to_integral_value(ROUND_HALF_UP)
    number = int(cents)
    if number < 0 or number > 100:
        return None
    return number


def _quote(market: Mapping[str, Any], dollar_key: str, legacy_key: str) -> int | None:
    if dollar_key in market and market.get(dollar_key) not in (None, ""):
        return _cents(market.get(dollar_key))
    legacy = market.get(legacy_key)
    if legacy in (None, ""):
        return None
    if isinstance(legacy, str) and "." in legacy:
        return _cents(legacy)
    value = _decimal(legacy)
    if value is None:
        return None
    number = int(value)
    if Decimal(number) != value or number < 0 or number > 100:
        return None
    return number


def _first_size(market: Mapping[str, Any], keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        if key in market and market.get(key) not in (None, ""):
            return _decimal(market.get(key))
    return None


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _decimal_setting(raw: str | None, default: Decimal, name: str) -> Decimal:
    if raw is None or not str(raw).strip():
        return default
    value = _decimal(str(raw).strip())
    if value is None:
        raise ValueError(f"{name} is not a finite number")
    return value


def _int_setting(raw: str | None, default: int, name: str) -> int:
    if raw is None or not str(raw).strip():
        return default
    value = _decimal(str(raw).strip())
    if value is None or value != int(value):
        raise ValueError(f"{name} is not an integer")
    return int(value)


@dataclass(frozen=True)
class DiscoveryFilters:
    """Starting screen. Every bound is configurable. Defaults are the v0.62 screen."""

    min_volume_24h: Decimal = Decimal("500")
    min_open_interest: Decimal = Decimal("1000")
    max_spread_cents: int = 3
    min_close_hours: int = 6
    max_close_days: int = 14
    min_yes_ask_cents: int = 10
    max_yes_ask_cents: int = 90

    def __post_init__(self) -> None:
        if self.min_volume_24h < 0 or self.min_open_interest < 0:
            raise ValueError("volume and open interest floors must be non-negative")
        if self.max_spread_cents < 0:
            raise ValueError("max spread must be non-negative")
        if self.min_close_hours < 0 or self.max_close_days < 0:
            raise ValueError("close window must be non-negative")
        if not 0 <= self.min_yes_ask_cents <= self.max_yes_ask_cents <= 100:
            raise ValueError("yes ask band must sit inside 0 to 100 cents")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DiscoveryFilters:
        env = os.environ if environ is None else environ
        return cls(
            min_volume_24h=_decimal_setting(
                env.get("LOCAL_TRIAGE_MIN_VOLUME_24H"), Decimal("500"), "LOCAL_TRIAGE_MIN_VOLUME_24H"
            ),
            min_open_interest=_decimal_setting(
                env.get("LOCAL_TRIAGE_MIN_OPEN_INTEREST"), Decimal("1000"), "LOCAL_TRIAGE_MIN_OPEN_INTEREST"
            ),
            max_spread_cents=_int_setting(env.get("LOCAL_TRIAGE_MAX_SPREAD_CENTS"), 3, "LOCAL_TRIAGE_MAX_SPREAD_CENTS"),
            min_close_hours=_int_setting(env.get("LOCAL_TRIAGE_MIN_CLOSE_HOURS"), 6, "LOCAL_TRIAGE_MIN_CLOSE_HOURS"),
            max_close_days=_int_setting(env.get("LOCAL_TRIAGE_MAX_CLOSE_DAYS"), 14, "LOCAL_TRIAGE_MAX_CLOSE_DAYS"),
            min_yes_ask_cents=_int_setting(
                env.get("LOCAL_TRIAGE_MIN_YES_ASK_CENTS"), 10, "LOCAL_TRIAGE_MIN_YES_ASK_CENTS"
            ),
            max_yes_ask_cents=_int_setting(
                env.get("LOCAL_TRIAGE_MAX_YES_ASK_CENTS"), 90, "LOCAL_TRIAGE_MAX_YES_ASK_CENTS"
            ),
        )


def is_mve_payload(market: Mapping[str, Any]) -> bool:
    """True when the payload itself carries a multivariate marker.

    The request already sends ``mve_filter=exclude``. A marker that still
    arrives is rejected. An empty marker is not a payload.
    """
    for key, value in market.items():
        if not str(key).lower().startswith("mve_"):
            continue
        if value in (None, "", [], {}):
            continue
        return True
    kind = str(market.get("market_type") or "").strip().lower()
    return kind in _MVE_TYPES


def _source_labels(raw: Any) -> tuple[str, ...]:
    if raw in (None, "", [], {}):
        return ()
    rows = raw if isinstance(raw, (list, tuple)) else (raw,)
    labels: list[str] = []
    for item in rows:
        if isinstance(item, str):
            label = " ".join(item.split())
        elif isinstance(item, Mapping):
            label = " ".join(
                str(
                    item.get("name")
                    or item.get("label")
                    or item.get("source")
                    or item.get("title")
                    or item.get("url")
                    or ""
                ).split()
            )
        else:
            label = " ".join(str(getattr(item, "label", "") or "").split())
        if label:
            labels.append(label)
    return tuple(labels)


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _blank_depth() -> dict[str, None]:
    return {
        "yes_bid_size": None,
        "yes_ask_size": None,
        "no_bid_size": None,
        "no_ask_size": None,
    }


def rule_version(rules_primary: str, rules_secondary: str, labels: tuple[str, ...]) -> str:
    if not rules_primary.strip() and not rules_secondary.strip() and not any(labels):
        return "missing"
    blob = "\n".join((rules_primary, rules_secondary, "|".join(labels)))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def screen_market(
    market: Mapping[str, Any],
    filters: DiscoveryFilters,
    *,
    now: datetime,
) -> dict[str, Any]:
    """First failing reason wins. A passing row has an empty rejection reason."""
    ticker = str(market.get("ticker") or "").strip()
    event = str(market.get("event_ticker") or "").strip()
    series = str(market.get("series_ticker") or "").strip().upper()
    if not series and ticker:
        series = ticker.split("-", 1)[0].upper()
    yes_bid = _quote(market, "yes_bid_dollars", "yes_bid")
    yes_ask = _quote(market, "yes_ask_dollars", "yes_ask")
    no_bid = _quote(market, "no_bid_dollars", "no_bid")
    no_ask = _quote(market, "no_ask_dollars", "no_ask")
    volume = _first_size(market, ("volume_24h_fp", "volume_24h"))
    open_interest = _first_size(market, ("open_interest_fp", "open_interest"))
    close_at = _parse_time(market.get("close_time"))
    spread = None if yes_bid is None or yes_ask is None else yes_ask - yes_bid
    labels = _source_labels(market.get("settlement_sources") or market.get("settlement_source_urls"))
    rules = str(market.get("rules_primary") or "")
    secondary = str(market.get("rules_secondary") or "")
    row: dict[str, Any] = {
        "ticker": ticker,
        "event_ticker": event,
        "series_ticker": series,
        "title": str(market.get("title") or ""),
        "category": str(market.get("category") or ""),
        "yes_bid_cents": yes_bid,
        "yes_ask_cents": yes_ask,
        "no_bid_cents": no_bid,
        "no_ask_cents": no_ask,
        "spread_cents": spread,
        "volume_24h": _money(volume),
        "open_interest": _money(open_interest),
        "close_time": close_at.strftime("%Y-%m-%dT%H:%M:%SZ") if close_at else None,
        "rules_primary": rules,
        "rules_secondary": secondary,
        "settlement_sources": list(labels),
        "rule_version": "missing",
        "deterministic_class": "",
        "deterministic_ambiguity": "",
        "rejection_reason": "",
        "provisional": True,
        **_blank_depth(),
    }
    status = str(market.get("status") or "").strip().lower()
    reason = _first_reason(
        filters,
        now=now,
        status=status,
        market=market,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        spread=spread,
        volume=volume,
        open_interest=open_interest,
        close_at=close_at,
    )
    row["rejection_reason"] = reason
    row["_volume"] = volume
    return row


def _first_reason(
    filters: DiscoveryFilters,
    *,
    now: datetime,
    status: str,
    market: Mapping[str, Any],
    yes_bid: int | None,
    yes_ask: int | None,
    no_bid: int | None,
    no_ask: int | None,
    spread: int | None,
    volume: Decimal | None,
    open_interest: Decimal | None,
    close_at: datetime | None,
) -> str:
    if status != "active":
        return "not_active"
    if is_mve_payload(market):
        return "mve_payload"
    if yes_bid is None or yes_ask is None or no_bid is None or no_ask is None:
        return "missing_quote"
    if spread is None or spread < 0:
        return "crossed"
    if volume is None:
        return "volume_missing"
    if volume < filters.min_volume_24h:
        return "volume_below_min"
    if open_interest is None:
        return "open_interest_missing"
    if open_interest < filters.min_open_interest:
        return "open_interest_below_min"
    if spread > filters.max_spread_cents:
        return "spread_above_max"
    if close_at is None:
        return "close_missing"
    if close_at < now + timedelta(hours=filters.min_close_hours):
        return "close_too_soon"
    if close_at > now + timedelta(days=filters.max_close_days):
        return "close_too_late"
    if yes_ask < filters.min_yes_ask_cents or yes_ask > filters.max_yes_ask_cents:
        return "yes_ask_outside_band"
    return ""


def keep_best_per_event(rows: list[dict[str, Any]]) -> None:
    """Keep the tightest spread, then the highest volume. Ties break by ticker."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["rejection_reason"]:
            continue
        key = row["event_ticker"] or row["ticker"]
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        group.sort(
            key=lambda row: (
                int(row["spread_cents"]),
                -1 * (row["_volume"] or Decimal("0")),
                row["ticker"],
            )
        )
        for loser in group[1:]:
            loser["rejection_reason"] = "not_best_in_event"


def _hits(text: str) -> set[str]:
    folded = text.lower()
    found: set[str] = set()
    for name, phrases in _CONTENT_PATTERNS:
        for phrase in phrases:
            if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", folded):
                found.add(name)
                break
    return found


def deterministic_class(
    row: Mapping[str, Any],
    *,
    sports_prefixes: tuple[str, ...],
) -> tuple[str, str]:
    """Provisional class. Two content hits, or title-only text, stay ambiguous.

    A sports prefix or a sports category is a strong sports label. It is still
    not settlement truth.
    """
    series = str(row.get("series_ticker") or "")
    category = str(row.get("category") or "").strip().lower()
    if category == "sports" or any(prefix and series.startswith(prefix) for prefix in sports_prefixes):
        return "sports", "none"
    rules = " ".join(
        (
            str(row.get("rules_primary") or ""),
            str(row.get("rules_secondary") or ""),
            " ".join(str(label) for label in row.get("settlement_sources") or ()),
        )
    ).strip()
    if not rules:
        return "ambiguous", "insufficient_evidence"
    found = _hits(rules)
    if len(found) > 1:
        return "ambiguous", "ambiguous"
    if len(found) == 1:
        return next(iter(found)), "none"
    return "other", "none"


def apply_deterministic_class(rows: list[dict[str, Any]], *, sports_prefixes: tuple[str, ...]) -> None:
    for row in rows:
        if row["rejection_reason"]:
            row["deterministic_class"] = ""
            row["deterministic_ambiguity"] = ""
            continue
        label, ambiguity = deterministic_class(row, sports_prefixes=sports_prefixes)
        row["deterministic_class"] = label
        row["deterministic_ambiguity"] = ambiguity
        row["rule_version"] = rule_version(
            str(row.get("rules_primary") or ""),
            str(row.get("rules_secondary") or ""),
            tuple(row.get("settlement_sources") or ()),
        )


def touch_depth(book: Any) -> dict[str, str | None]:
    """Depth at the touch. YES ask size is the best NO bid. Missing stays missing.

    A missing side is null. Zero is not a size, and this function never
    substitutes a contract count.
    """
    def qty(levels: Any) -> str | None:
        if not levels:
            return None
        raw = getattr(levels[0], "quantity", None)
        value = _decimal(raw)
        if value is None or value <= 0:
            return None
        return format(value, "f")

    yes_bid = qty(getattr(book, "yes_bids", ()) or ())
    no_bid = qty(getattr(book, "no_bids", ()) or ())
    return {
        "yes_bid_size": yes_bid,
        "no_bid_size": no_bid,
        "yes_ask_size": no_bid,
        "no_ask_size": yes_bid,
    }


def review_class(deterministic: str, local_outcome: str, local_class: str) -> str:
    """A third label. Disagreement stays ambiguous. The stored classes stay put.

    A local failure is ``local_unavailable``. Agreement keeps the shared
    provisional class and does not promote it.
    """
    if local_outcome != "ok":
        return "local_unavailable"
    if not deterministic or deterministic not in CLASSES or local_class not in CLASSES:
        return "ambiguous"
    if deterministic == local_class:
        return deterministic
    return "ambiguous"


def public_row(row: Mapping[str, Any], *, ts: str) -> dict[str, Any]:
    """JSONL record. Internal sort keys are dropped. Depth nulls stay null."""
    kept = {
        "ts": ts,
        "ticker": row.get("ticker") or "",
        "event_ticker": row.get("event_ticker") or "",
        "series_ticker": row.get("series_ticker") or "",
        "title": row.get("title") or "",
        "category": row.get("category") or "",
        "yes_bid_cents": row.get("yes_bid_cents"),
        "yes_ask_cents": row.get("yes_ask_cents"),
        "no_bid_cents": row.get("no_bid_cents"),
        "no_ask_cents": row.get("no_ask_cents"),
        "spread_cents": row.get("spread_cents"),
        "volume_24h": row.get("volume_24h"),
        "open_interest": row.get("open_interest"),
        "close_time": row.get("close_time"),
        "yes_bid_size": row.get("yes_bid_size"),
        "yes_ask_size": row.get("yes_ask_size"),
        "no_bid_size": row.get("no_bid_size"),
        "no_ask_size": row.get("no_ask_size"),
        "rules_primary": row.get("rules_primary") or "",
        "rules_secondary": row.get("rules_secondary") or "",
        "settlement_sources": list(row.get("settlement_sources") or ()),
        "rule_version": row.get("rule_version") or "missing",
        "deterministic_class": row.get("deterministic_class") or "",
        "deterministic_ambiguity": row.get("deterministic_ambiguity") or "",
        "rejection_reason": row.get("rejection_reason") or "",
        "provisional": True,
    }
    return kept


def count_reasons(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("rejection_reason") or "")
        if not reason:
            continue
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))
