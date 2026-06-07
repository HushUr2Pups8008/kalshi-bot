from __future__ import annotations

from typing import Any

from polymarket.models import PolymarketMarket
from trading.venue import Venue


def _money_value(payload: dict[str, Any] | None, default: float = 0.0) -> float:
    if not isinstance(payload, dict):
        return default
    try:
        return float(payload.get("value", default))
    except (TypeError, ValueError):
        return default


def _price_cents(outcome: dict[str, Any]) -> int | None:
    value = _money_value(outcome.get("bestAsk"), default=-1.0)
    if value < 0:
        return None
    return max(1, min(99, int(round(value * 100))))


def normalize_polymarket_market(payload: dict[str, Any]) -> PolymarketMarket:
    outcomes = payload.get("outcomes") or []
    if len(outcomes) != 2:
        raise ValueError("Polymarket integration supports binary markets only")

    by_name = {
        str(outcome.get("name", "")).strip().lower(): outcome
        for outcome in outcomes
        if isinstance(outcome, dict)
    }
    if "yes" not in by_name or "no" not in by_name:
        raise ValueError("binary Polymarket market must contain Yes and No outcomes")

    return PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id=str(payload.get("slug") or payload.get("id") or "").strip(),
        title=str(payload.get("title") or payload.get("question") or "").strip(),
        status=str(payload.get("status") or "").strip().lower(),
        yes_ask_cents=_price_cents(by_name["yes"]),
        no_ask_cents=_price_cents(by_name["no"]),
        volume_dollars=_money_value(payload.get("volume")),
        open_interest_dollars=_money_value(payload.get("openInterest")),
        close_time=str(payload.get("closeTime") or payload.get("close_time") or ""),
        is_binary=True,
    )
