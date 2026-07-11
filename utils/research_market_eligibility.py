"""Fail-closed market eligibility policy for research workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


ACTIVE_RESEARCH_MARKET_STATUSES = frozenset({"active", "open"})


@dataclass(frozen=True)
class ResearchMarketEligibility:
    eligible: bool
    reason: str | None
    status: str
    close_time: datetime | None


def evaluate_research_market_eligibility(
    *,
    status: object,
    close_time: object,
    now: datetime,
) -> ResearchMarketEligibility:
    """Evaluate canonical market status and close time without permissive defaults."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(UTC)
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in ACTIVE_RESEARCH_MARKET_STATUSES:
        return ResearchMarketEligibility(
            eligible=False,
            reason="market_not_active",
            status=normalized_status,
            close_time=None,
        )

    if close_time is None or not str(close_time).strip():
        return ResearchMarketEligibility(
            eligible=False,
            reason="market_close_time_missing",
            status=normalized_status,
            close_time=None,
        )

    normalized_close_time = _parse_utc_close_time(close_time)
    if normalized_close_time is None:
        return ResearchMarketEligibility(
            eligible=False,
            reason="market_close_time_invalid",
            status=normalized_status,
            close_time=None,
        )
    if normalized_close_time <= now_utc:
        return ResearchMarketEligibility(
            eligible=False,
            reason="market_expired",
            status=normalized_status,
            close_time=normalized_close_time,
        )
    return ResearchMarketEligibility(
        eligible=True,
        reason=None,
        status=normalized_status,
        close_time=normalized_close_time,
    )


def research_market_eligibility(
    market: Any,
    *,
    now: datetime | None = None,
) -> ResearchMarketEligibility:
    """Evaluate an object exposing canonical ``status`` and ``close_time`` fields."""
    return evaluate_research_market_eligibility(
        status=getattr(market, "status", None),
        close_time=getattr(market, "close_time", None),
        now=now or datetime.now(UTC),
    )


def _parse_utc_close_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)
