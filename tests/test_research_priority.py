from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from utils.research_priority import (
    extract_pending_event_at,
    official_pending_retry_delay,
    research_market_priority_key,
)


@pytest.mark.parametrize(
    ("horizon", "expected"),
    [
        (timedelta(hours=12), 1800.0),
        (timedelta(days=3), 21600.0),
        (timedelta(days=14), 86400.0),
        (timedelta(days=60), 604800.0),
        (None, 1800.0),
    ],
)
def test_official_pending_retry_delay_tracks_event_horizon(horizon, expected):
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    event_at = now + horizon if horizon is not None else None

    assert official_pending_retry_delay(now=now, event_at=event_at) == expected


def test_research_market_priority_prefers_near_mid_price_over_tail_and_far_event():
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    near_mid = SimpleNamespace(
        ticker="KXNEAR-MID",
        yes_ask_cents=41,
        no_ask_cents=59,
        close_time=(now + timedelta(days=1)).isoformat(),
    )
    near_tail = SimpleNamespace(
        ticker="KXNEAR-TAIL",
        yes_ask_cents=2,
        no_ask_cents=98,
        close_time=(now + timedelta(hours=6)).isoformat(),
    )
    far_mid = SimpleNamespace(
        ticker="KXFAR-MID",
        yes_ask_cents=45,
        no_ask_cents=55,
        close_time=(now + timedelta(days=90)).isoformat(),
    )

    ordered = sorted(
        [near_tail, far_mid, near_mid],
        key=lambda market: research_market_priority_key(market, now=now),
    )

    assert [market.ticker for market in ordered] == [
        "KXNEAR-MID",
        "KXFAR-MID",
        "KXNEAR-TAIL",
    ]


def test_cpi_pending_uses_expected_release_guard_not_target_month():
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    evidence = SimpleNamespace(
        metric_name="cpi_official_data_pending",
        title="BLS CPI target month pending: June 2026",
        snippet="Target June 2026 CPI is not yet expected.",
        published_at="2026-06-01",
    )

    event_at = extract_pending_event_at([evidence], now=now)

    assert event_at == datetime(2026, 7, 20, tzinfo=timezone.utc)
    assert official_pending_retry_delay(now=now, event_at=event_at) == 86400.0
