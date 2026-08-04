from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from polymarket import paper_runtime
from polymarket.horizon_selection import (
    _is_pre_admission_matchable_market as _selector_pre_admission_matchable_market,
    select_polymarket_horizon_band,
)
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
            market_id="literal-14.000001d",
            close_time=(now + timedelta(days=14.000001)).isoformat(),
        ),
        _market(
            market_id="exactly-30d",
            close_time=(now + timedelta(days=30)).isoformat(),
        ),
        _market(
            market_id="literal-30.000001d",
            close_time=(now + timedelta(days=30.000001)).isoformat(),
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
        "literal-14.000001d",
        "exactly-30d",
    ]


def test_select_polymarket_horizon_band_shares_runtime_pre_admission_policy():
    close_time = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc).isoformat()
    world_market = _market(
        market_id="world-market",
        close_time=close_time,
        category="world",
        resolution_source="https://example.test/source",
        event_title="World government coalition update",
        series_title="International politics",
        tags=("world", "government"),
    )
    policy_market = _market(
        market_id="policy-market",
        close_time=close_time,
        category="policy",
        resolution_source="https://example.test/source",
        event_title="Policy platform update",
        series_title="Domestic agenda",
        tags=("policy",),
    )

    assert _selector_pre_admission_matchable_market(world_market) is (
        paper_runtime._is_pre_admission_matchable_market(world_market)
    )
    assert _selector_pre_admission_matchable_market(policy_market) is (
        paper_runtime._is_pre_admission_matchable_market(policy_market)
    )


def test_select_polymarket_horizon_band_skips_malformed_records_fail_closed():
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    good_market = _market(
        market_id="good-market",
        close_time=(now + timedelta(days=20)).isoformat(),
    )

    def tradeable() -> bool:
        return True

    malformed_records = [
        None,
        SimpleNamespace(
            venue=Venue.POLYMARKET_US,
            is_tradeable=tradeable,
            category=None,
            resolution_source="https://example.test/source",
            volume_dollars=1000.0,
            open_interest_dollars=100.0,
            event_title="Malformed category",
            series_title="Series",
            tags=("world",),
            close_time=(now + timedelta(days=20)).isoformat(),
        ),
        SimpleNamespace(
            venue=Venue.POLYMARKET_US,
            is_tradeable=tradeable,
            category="world",
            resolution_source="https://example.test/source",
            volume_dollars="1000.0",
            open_interest_dollars=100.0,
            event_title="Malformed volume",
            series_title="Series",
            tags=("world",),
            close_time=(now + timedelta(days=20)).isoformat(),
        ),
        SimpleNamespace(
            venue=Venue.POLYMARKET_US,
            is_tradeable=tradeable,
            category="world",
            resolution_source="https://example.test/source",
            volume_dollars=1000.0,
            open_interest_dollars=100.0,
            event_title="Malformed tags",
            series_title="Series",
            tags=None,
            close_time=(now + timedelta(days=20)).isoformat(),
        ),
    ]

    selected = select_polymarket_horizon_band(
        [good_market, *malformed_records],
        now=now,
        lower_exclusive_days=14.0,
        upper_inclusive_days=30.0,
    )

    assert [market.market_id for market in selected] == ["good-market"]


@pytest.mark.parametrize(
    ("lower_exclusive_days", "upper_inclusive_days"),
    [
        (30.0, 14.0),
        (14.0, 14.0),
        (-1.0, 0.0),
        (-2.0, -1.0),
        (0.0, 0.0),
        (False, 14.0),
        (0.0, True),
        (0.0, float("inf")),
        (0.0, float("-inf")),
        (float("nan"), 14.0),
    ],
)
def test_select_polymarket_horizon_band_rejects_invalid_bounds(
    lower_exclusive_days: float,
    upper_inclusive_days: float,
):
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError):
        select_polymarket_horizon_band(
            [],
            now=now,
            lower_exclusive_days=lower_exclusive_days,
            upper_inclusive_days=upper_inclusive_days,
        )


def test_select_polymarket_horizon_band_rejects_naive_clock_before_iteration():
    with pytest.raises(ValueError):
        select_polymarket_horizon_band(
            [],
            now=datetime(2026, 8, 1, 12, 0),
            lower_exclusive_days=0.0,
            upper_inclusive_days=14.0,
        )


def test_select_polymarket_horizon_band_accepts_zero_lower_bound():
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    market = _market(
        market_id="one-day-market",
        close_time=(now + timedelta(days=1)).isoformat(),
    )

    selected = select_polymarket_horizon_band(
        [market],
        now=now,
        lower_exclusive_days=0.0,
        upper_inclusive_days=14.0,
    )

    assert [item.market_id for item in selected] == ["one-day-market"]
