from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from polymarket.horizon_selection import select_polymarket_horizon_band
from polymarket.models import PolymarketMarket
from trading.venue import Venue


def _market(*, close_time: str, **overrides) -> PolymarketMarket:
    values = {
        "venue": Venue.POLYMARKET_US,
        "market_id": "will-example-event-happen-2026",
        "title": "Will example event happen in 2026?",
        "question": "Will example event happen in 2026?",
        "subtitle": "",
        "category": "politics",
        "status": "open",
        "yes_ask_cents": 42,
        "no_ask_cents": 59,
        "volume_dollars": 1000.0,
        "open_interest_dollars": 100.0,
        "close_time": close_time,
        "is_binary": True,
    }
    values.update(overrides)
    return PolymarketMarket(**values)


def test_select_polymarket_horizon_band_filters_to_exact_disjoint_window():
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    markets = [
        _market(
            market_id="exactly-14d",
            close_time=(now + timedelta(days=14)).isoformat(),
        ),
        _market(
            market_id="barely-over-14d",
            close_time=(now + timedelta(days=14, seconds=1)).isoformat(),
        ),
        _market(
            market_id="exactly-30d",
            close_time=(now + timedelta(days=30)).isoformat(),
        ),
        _market(
            market_id="over-30d",
            close_time=(now + timedelta(days=30, seconds=1)).isoformat(),
        ),
        _market(
            market_id="closed-market",
            close_time=(now + timedelta(days=20)).isoformat(),
            status="closed",
        ),
        _market(
            market_id="suppressed-market",
            close_time=(now + timedelta(days=20)).isoformat(),
            category="sports",
            resolution_source="https://example.test/resolution",
        ),
        _market(
            market_id="malformed-close-time",
            close_time="not-a-timestamp",
        ),
    ]

    selected = select_polymarket_horizon_band(
        markets,
        now=now,
        lower_exclusive_days=14.0,
        upper_inclusive_days=30.0,
    )

    assert [market.market_id for market in selected] == [
        "barely-over-14d",
        "exactly-30d",
    ]


def test_select_polymarket_horizon_band_rejects_invalid_bounds():
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="ordered finite bounds"):
        select_polymarket_horizon_band(
            [],
            now=now,
            lower_exclusive_days=30.0,
            upper_inclusive_days=14.0,
        )
