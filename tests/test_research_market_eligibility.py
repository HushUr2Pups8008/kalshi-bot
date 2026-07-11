from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from utils.research_market_eligibility import (
    evaluate_research_market_eligibility,
    research_market_eligibility,
)


NOW = datetime(2026, 7, 11, 18, 0, tzinfo=UTC)


@pytest.mark.parametrize("status", ["open", "OPEN", " active ", "Active"])
def test_active_status_with_future_utc_close_is_eligible(status: str) -> None:
    result = evaluate_research_market_eligibility(
        status=status,
        close_time="2026-07-11T18:00:01Z",
        now=NOW,
    )

    assert result.eligible is True
    assert result.reason is None
    assert result.status == status.strip().lower()
    assert result.close_time == NOW + timedelta(seconds=1)


@pytest.mark.parametrize(
    ("close_time", "expected"),
    [
        ("2026-07-11T18:00:00Z", NOW),
        ("2026-07-11T17:59:59Z", NOW - timedelta(seconds=1)),
    ],
)
def test_close_time_must_be_strictly_future(
    close_time: str,
    expected: datetime,
) -> None:
    result = evaluate_research_market_eligibility(
        status="active",
        close_time=close_time,
        now=NOW,
    )

    assert result.eligible is False
    assert result.reason == "market_expired"
    assert result.close_time == expected


@pytest.mark.parametrize(
    "status",
    [None, "", "closed", "finalized", "settled", "resolved", "paused"],
)
def test_non_active_status_fails_closed(status: object) -> None:
    result = evaluate_research_market_eligibility(
        status=status,
        close_time="2026-07-12T00:00:00Z",
        now=NOW,
    )

    assert result.eligible is False
    assert result.reason == "market_not_active"


@pytest.mark.parametrize("close_time", [None, "", "   "])
def test_missing_close_time_fails_closed(close_time: object) -> None:
    result = evaluate_research_market_eligibility(
        status="active",
        close_time=close_time,
        now=NOW,
    )

    assert result.eligible is False
    assert result.reason == "market_close_time_missing"
    assert result.close_time is None


@pytest.mark.parametrize(
    "close_time",
    ["not-a-timestamp", "2026-07-12T00:00:00", datetime(2026, 7, 12)],
)
def test_invalid_or_naive_close_time_fails_closed(close_time: object) -> None:
    result = evaluate_research_market_eligibility(
        status="active",
        close_time=close_time,
        now=NOW,
    )

    assert result.eligible is False
    assert result.reason == "market_close_time_invalid"
    assert result.close_time is None


def test_offset_close_time_normalizes_to_utc() -> None:
    result = evaluate_research_market_eligibility(
        status="active",
        close_time="2026-07-11T13:00:01-05:00",
        now=NOW,
    )

    assert result.eligible is True
    assert result.close_time == NOW + timedelta(seconds=1)
    assert result.close_time is not None
    assert result.close_time.tzinfo is UTC


def test_aware_datetime_close_time_normalizes_to_utc() -> None:
    mountain = timezone(timedelta(hours=-6))
    result = evaluate_research_market_eligibility(
        status="open",
        close_time=datetime(2026, 7, 11, 12, 0, 1, tzinfo=mountain),
        now=NOW,
    )

    assert result.eligible is True
    assert result.close_time == NOW + timedelta(seconds=1)


def test_market_wrapper_reads_canonical_status_and_close_time() -> None:
    market = SimpleNamespace(
        status="active",
        close_time="2026-07-11T18:00:01Z",
    )

    assert research_market_eligibility(market, now=NOW).eligible is True


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        evaluate_research_market_eligibility(
            status="active",
            close_time="2026-07-12T00:00:00Z",
            now=datetime(2026, 7, 11, 18, 0),
        )
