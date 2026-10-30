"""KXEOWEEK backtest stops on a bad week and prices the existing 60-day fit."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from scripts.countable_series_backtest import (
    ET,
    SETTLED,
    Stop,
    bucket,
    days_left,
    fetch_json,
    known,
    pick_candle,
    prior,
    quote_edge,
    score_markets,
    ticker_prefix,
    week_ending,
    week_mismatches,
)


def _settled_dates() -> list[date]:
    found: list[date] = []
    for saturday, count in SETTLED:
        sunday = saturday - timedelta(days=6)
        found.extend(sunday + timedelta(days=index) for index in range(count))
    return found


def test_eleven_weeks_match_and_a_miss_stops() -> None:
    """The Federal Register buckets are the settlement check. One wrong week stops the fit."""
    counts = bucket(_settled_dates())
    assert week_mismatches(counts) == []
    counts[date(2026, 8, 15)] = 9
    bad = week_mismatches(counts)
    assert bad[0] == (date(2026, 8, 15), 1, 9)


def test_ticker_uses_the_saturday_label() -> None:
    assert date(2026, 8, 15).weekday() == 5
    assert week_ending(date(2026, 8, 9)) == date(2026, 8, 15)
    assert ticker_prefix(date(2026, 8, 15)) == "KXEOWEEK-26AUG15"
    assert ticker_prefix(date(2026, 9, 5)) == "KXEOWEEK-26SEP05"


def test_sunday_open_excludes_that_day_and_wednesday_noon_includes_it() -> None:
    sunday = date(2026, 8, 9)
    signed = [sunday, sunday + timedelta(days=3)]
    assert known(signed, sunday, datetime(2026, 8, 9, 0, tzinfo=ET)) == 0
    assert known(signed, sunday, datetime(2026, 8, 12, 12, tzinfo=ET)) == 2
    assert days_left(sunday, datetime(2026, 8, 9, 0, tzinfo=ET)) == 7
    assert days_left(sunday, datetime(2026, 8, 12, 12, tzinfo=ET)) == 3.5


def test_prior_uses_sixty_days_and_ignores_the_2025_burst() -> None:
    """2025 was a higher regime. The shadow window must not fit on it."""
    sunday = date(2026, 8, 9)
    burst = [date(2025, 1, 1) + timedelta(days=index) for index in range(40)]
    assert prior(burst, sunday) == (0.0, 0.0)
    recent = [sunday - timedelta(days=index) for index in range(1, 61)]
    mean, _var = prior(burst + recent, sunday)
    assert mean == pytest.approx(1.0)


def test_fee_net_uses_the_quadratic_taker_quote_at_zero_and_one() -> None:
    """The scorecard quote refuses probability 0 and 1. The fee itself does not depend on it."""
    edge, fee = quote_edge(1.0, 40)
    assert fee > 0
    assert edge == Decimal("1.000000") - Decimal("0.40") - fee
    inside, inside_fee = quote_edge(0.6, 40)
    assert inside_fee == fee
    assert inside == Decimal("0.600000") - Decimal("0.40") - fee


def test_a_later_hour_is_not_the_snapshot() -> None:
    """Sunday open is the close at that timestamp. The next hour is a different print."""
    target = 1_700_000_000
    assert pick_candle([{"end_period_ts": target + 3600}], target) is None
    chosen = pick_candle([{"end_period_ts": target}, {"end_period_ts": target + 3600}], target)
    assert chosen is not None and chosen["end_period_ts"] == target


def test_finalized_result_that_disagrees_with_the_count_stops() -> None:
    signing = [date(2026, 8, 9) - timedelta(days=index) for index in range(1, 61)]
    signing.extend(_settled_dates())
    market = {
        "ticker": "KXEOWEEK-26AUG15-1",
        "strike_type": "greater",
        "floor_strike": 1,
        "result": "yes",
    }
    with pytest.raises(Stop):
        score_markets(signing, [market], lambda _ticker, _start, _end: [])


def test_sunday_and_wednesday_candles_are_scored() -> None:
    sunday = datetime(2026, 8, 9, tzinfo=ET)
    wednesday = sunday + timedelta(days=3, hours=12)
    signing = [sunday.date() - timedelta(days=index) for index in range(1, 61)]
    signing.extend(_settled_dates())

    def candles(_ticker: str, _start: int, _end: int) -> list[dict]:
        return [
            {
                "end_period_ts": int(sunday.timestamp()),
                "yes_ask": {"close_dollars": "0.4000"},
                "yes_bid": {"close_dollars": "0.3200"},
            },
            {
                "end_period_ts": int(wednesday.timestamp()),
                "yes_ask": {"close_dollars": "0.5500"},
                "yes_bid": {"close_dollars": "0.4700"},
            },
        ]

    market = {
        "ticker": "KXEOWEEK-26AUG15-1",
        "strike_type": "greater",
        "floor_strike": 1,
        "result": "no",
    }
    scored = score_markets(signing, [market], candles)
    for name in ("sunday_open", "wednesday"):
        row = scored["rows"][name][0]
        assert row["missing"] is False
        assert row["outcome"] == 0
        assert 0 <= row["p"] <= 1
        assert isinstance(row["edge"], Decimal)
        assert isinstance(row["realized"], Decimal)


def test_fetch_refuses_a_host_outside_the_two_public_apis() -> None:
    with pytest.raises(ValueError, match="host"):
        fetch_json("https://example.com/documents.json")
