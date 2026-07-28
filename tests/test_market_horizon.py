"""Fail-closed market close-time horizon policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from config import MAX_MARKET_DAYS_TO_EXPIRY, cfg
from utils.market_horizon import evaluate_market_horizon


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def test_horizon_accepts_aware_utc_and_offset_timestamps_up_to_exact_cap():
    at_cap = evaluate_market_horizon(
        "2026-08-11T12:00:00Z",
        now=NOW,
        max_days_to_close=14,
    )
    offset = evaluate_market_horizon(
        "2026-08-05T08:00:00-04:00",
        now=NOW,
        max_days_to_close=14,
    )

    assert at_cap.eligible is True
    assert at_cap.reason is None
    assert at_cap.close_time == NOW + timedelta(days=14)
    assert at_cap.days_to_close == pytest.approx(14.0)
    assert offset.eligible is True
    assert offset.close_time == datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("close_time", "reason"),
    [
        (None, "close_time_missing"),
        ("", "close_time_missing"),
        ("not-a-date", "close_time_invalid"),
        ("2026-07-29T12:00:00", "close_time_invalid"),
        ("2026-07-28T12:00:00Z", "market_expired"),
        ("2026-07-27T12:00:00Z", "market_expired"),
        ("2026-08-11T12:00:01Z", "market_too_far"),
    ],
)
def test_horizon_rejects_missing_invalid_expired_and_too_far_close_times(
    close_time: object,
    reason: str,
):
    decision = evaluate_market_horizon(close_time, now=NOW, max_days_to_close=14)

    assert decision.eligible is False
    assert decision.reason == reason


@pytest.mark.parametrize("max_days", (0, -1, float("inf"), float("nan")))
def test_horizon_rejects_invalid_caps(max_days: float):
    with pytest.raises(ValueError, match="max_days_to_close"):
        evaluate_market_horizon("2026-08-01T12:00:00Z", now=NOW, max_days_to_close=max_days)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 7, 28, 12, 0),
        "2026-07-28T12:00:00Z",
    ],
)
def test_horizon_requires_an_aware_datetime_clock(now: object):
    with pytest.raises(ValueError, match="now"):
        evaluate_market_horizon("2026-08-01T12:00:00Z", now=now, max_days_to_close=14)


def test_config_uses_tight_horizon_only_for_active_paper_cohorts(monkeypatch):
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "legacy")
    assert cfg.paper_admission_max_days_to_close == pytest.approx(
        MAX_MARKET_DAYS_TO_EXPIRY
    )

    monkeypatch.setattr(cfg, "paper_cohort_id", "active-20260728")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    assert cfg.paper_admission_max_days_to_close == pytest.approx(14.0)

    monkeypatch.setattr(cfg, "is_paper_trading", False)
    assert cfg.paper_admission_max_days_to_close == pytest.approx(
        MAX_MARKET_DAYS_TO_EXPIRY
    )
