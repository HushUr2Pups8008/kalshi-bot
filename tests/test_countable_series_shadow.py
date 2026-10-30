"""Countable-series shadow: a free probability beside the ask, never a queue."""

from __future__ import annotations

import json
import math
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from utils import countable_series_shadow as shadow
from utils.countable_series_shadow import (
    fetch_text,
    parse_contract,
    record_countable_shadow,
    yes_probability,
)

TRUTH_RULE = (
    "If the number of Truth Social posts by Donald Trump from Oct 18, 2026 "
    "to Oct 24, 2026 is above 240, then the market resolves to Yes."
)
ACTION_RULE = (
    "If Donald Trump has taken at least 6 presidential actions from Oct 18, 2026 "
    "through Oct 24, 2026, then the market resolves to Yes."
)
BETWEEN_RULE = (
    "If the number of Truth Social posts from Oct 4, 2026 to Oct 10, 2026 "
    "is between 120 and 139, then the market resolves to Yes."
)


def test_negative_binomial_is_the_overdispersed_law() -> None:
    """Variance above the mean is why this is not a Poisson. One-day geometric P(X>=1) is 1/2."""
    model, probability = yes_probability(0, 1, 1, 2, {"how": "gt", "strike": 0})
    assert model == "nb"
    assert probability == pytest.approx(0.5)
    two_days = yes_probability(0, 2, 1, 2, {"how": "gt", "strike": 0})
    assert two_days is not None and two_days[1] == pytest.approx(0.75)


def test_poisson_only_when_the_sample_is_not_overdispersed() -> None:
    model, probability = yes_probability(0, 1, 1, 1, {"how": "gt", "strike": 0})
    assert model == "poisson"
    assert probability == pytest.approx(1 - math.exp(-1))


def test_a_count_already_through_the_strike_is_certain() -> None:
    model, probability = yes_probability(10, 3, 1, 4, {"how": "gt", "strike": 5})
    assert model == "nb"
    assert probability == 1


def test_rules_name_the_window_and_the_strike() -> None:
    truth = parse_contract(TRUTH_RULE, "greater")
    action = parse_contract(ACTION_RULE, "greater_or_equal")
    band = parse_contract(BETWEEN_RULE, "between")
    assert truth is not None and truth["how"] == "gt" and truth["strike"] == 240
    assert action is not None and action["how"] == "ge" and action["strike"] == 6
    assert band is not None and band["how"] == "between" and band["low"] == 120 and band["high"] == 139
    assert parse_contract("no dates here") is None
    weekly = parse_contract(
        "If the President signs above 1 executive orders during Aug 9, 2026 to Aug 15, 2026, "
        "then the market resolves to Yes.",
        "greater",
    )
    assert weekly is not None and weekly["how"] == "gt" and weekly["strike"] == 1
    assert weekly["start"] == date(2026, 8, 9) and weekly["end"] == date(2026, 8, 15)
    both = parse_contract(
        "from Oct 18, 2026 to Oct 24, 2026 is above 240. Recorded on Oct 25, 2026.",
        "greater",
    )
    assert both is not None and both["start"] == date(2026, 10, 18) and both["end"] == date(2026, 10, 24)
    daily = parse_contract(
        "Resolves Yes if the number of Truth Social posts by Donald Trump "
        "on Oct 3, 2026 is between 15 and 19.",
        "between",
    )
    assert daily is not None and daily["start"] == daily["end"] == date(2026, 10, 3)
    assert daily["how"] == "between" and daily["low"] == 15 and daily["high"] == 19
    below = parse_contract(
        "Resolves Yes if the number of Truth Social posts by Donald Trump on Oct 3, 2026 is below 10.",
        "",
    )
    assert below is not None and below["how"] == "lt" and below["strike"] == 10
    model, probability = yes_probability(0, 1, 1, 2, {"how": "lt", "strike": 1})
    assert model == "nb" and probability == pytest.approx(0.5)
    certain = yes_probability(5, 1, 1, 2, {"how": "lt", "strike": 5})
    assert certain is not None and certain[1] == 0


def test_shadow_row_uses_the_fixture_feed_and_skips_deleted_posts(tmp_path: Path) -> None:
    """The row sits beside the ask. Deleted posts and other handles are not the count."""
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        rows = []
        for day, count in (("11", 1), ("12", 8), ("13", 1), ("14", 8), ("15", 1), ("16", 8), ("17", 1)):
            for _ in range(count):
                rows.append({
                    "date": f"2026-10-{day}T15:00:00+00:00",
                    "handle": "realDonaldTrump",
                    "platform": "Truth Social",
                    "deleted_flag": False,
                    "type": "Social",
                })
        rows.append({
            "date": "2026-08-01T15:00:00+00:00",
            "handle": "realDonaldTrump",
            "platform": "Truth Social",
            "deleted_flag": False,
            "type": "Social",
        })
        rows.append({
            "date": "2026-10-18T15:00:00+00:00",
            "handle": "realDonaldTrump",
            "platform": "Truth Social",
            "deleted_flag": True,
            "type": "Social",
        })
        rows.append({
            "date": "2026-10-18T16:00:00+00:00",
            "handle": "someoneelse",
            "platform": "Truth Social",
            "deleted_flag": False,
            "type": "Social",
        })
        rows.append({
            "date": "2026-10-18T17:00:00+00:00",
            "handle": "realDonaldTrump",
            "platform": "Truth Social",
            "deleted_flag": False,
            "type": "Social",
        })
        return json.dumps({"data": rows})

    market = {
        "ticker": "KXTRUTHSOCIAL-26OCT24-T240",
        "series_ticker": "KXTRUTHSOCIAL",
        "rules_primary": TRUTH_RULE,
        "strike_type": "greater",
        "yes_ask_cents": 61,
    }
    path = tmp_path / "shadow.jsonl"
    written = record_countable_shadow(
        [market, {"ticker": "KXTRUMPSAY-26OCT19-GOLF", "series": "KXTRUMPSAY", "rules": TRUTH_RULE}],
        now=datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc),
        path=path,
        fetch=fetch,
        sleeper=lambda _seconds: None,
    )
    assert written == 1
    assert len(calls) == 1
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["yes_ask"] == 61
    assert row["so_far"] == 1
    assert row["model"] == "nb"
    assert row["probability"] is not None
    assert 0 <= float(row["probability"]) <= 1
    assert float(row["probability"]) < 0.05
    assert row["minute_counter"] is False
    assert row["reason"] is None
    assert "api.x.ai" not in Path(shadow.__file__).read_text(encoding="utf-8")
    assert "place_limit_order" not in Path(shadow.__file__).read_text(encoding="utf-8")


def test_feed_and_detail_failures_write_a_row_and_do_not_raise(tmp_path: Path) -> None:
    path = tmp_path / "shadow.jsonl"

    def boom(_url: str) -> str:
        raise OSError("down")

    market = {"ticker": "KXTRUMPACT-26OCT18-T6", "series": "KXTRUMPACT", "rules": ACTION_RULE, "yes_ask": 42}
    assert record_countable_shadow(
        [market],
        now=datetime(2026, 10, 20, tzinfo=timezone.utc),
        path=path,
        fetch=boom,
        sleeper=lambda _seconds: None,
    ) == 1
    failed = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert failed["probability"] is None and failed["reason"] == "feed"

    def detail(_ticker: str) -> object:
        raise RuntimeError("hidden")

    empty = {"ticker": "KXTRUTHSOCIAL-26OCT24-T240", "series_ticker": "KXTRUTHSOCIAL", "rules_primary": ""}
    assert record_countable_shadow(
        [empty],
        now=datetime(2026, 10, 20, tzinfo=timezone.utc),
        path=path,
        fetch=boom,
        rules_for=detail,
        sleeper=lambda _seconds: None,
    ) == 1
    second = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    assert second["reason"] == "detail"
    assert second["probability"] is None


def test_fetch_refuses_a_non_official_host() -> None:
    with pytest.raises(ValueError):
        fetch_text("https://example.com/feed")


def test_federal_register_host_reaches_the_opener(monkeypatch) -> None:
    """The count shadow may read the public register. The opener is not called in this test."""

    def opened(_request, timeout=0):
        raise AssertionError(timeout)

    monkeypatch.setattr(urllib.request, "urlopen", opened)
    with pytest.raises(AssertionError):
        fetch_text("https://www.federalregister.gov/api/v1/documents.json")


def test_eoweek_shadow_uses_the_register_and_not_grok(tmp_path: Path) -> None:
    """A Sunday-open row is the free count. The register page is the only fetch."""
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        results = [
            {"executive_order_number": "14400", "signing_date": f"2026-08-0{day}"}
            for day in (1, 2, 3, 4, 6)
        ]
        return json.dumps({"count": len(results), "results": results})

    market = {
        "ticker": "KXEOWEEK-26AUG15-1",
        "series_ticker": "KXEOWEEK",
        "rules_primary": (
            "If the President signs above 1 executive orders during Aug 9, 2026 "
            "to Aug 15, 2026, then the market resolves to Yes."
        ),
        "strike_type": "greater",
        "yes_ask_cents": 40,
    }
    path = tmp_path / "shadow.jsonl"
    written = record_countable_shadow(
        [market],
        now=datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc),
        path=path,
        fetch=fetch,
        sleeper=lambda _seconds: None,
    )
    assert written == 1
    assert len(calls) == 1 and "federalregister.gov" in calls[0]
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["feed"] == "federal_register"
    assert row["clock"] == "sunday_open"
    assert row["so_far"] == 0
    assert row["yes_ask"] == 40
    assert row["reason"] is None
    assert 0 < float(row["probability"]) < 1
    assert "api.x.ai" not in Path(shadow.__file__).read_text(encoding="utf-8")
    assert "place_limit_order" not in Path(shadow.__file__).read_text(encoding="utf-8")
    wednesday = datetime(2026, 8, 12, 16, 0, tzinfo=timezone.utc)
    assert shadow._clock(wednesday) == "wednesday"
    sunday = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)
    assert shadow._clock(sunday, "KXEOWEEK") == "sunday_open"
    assert shadow._clock(sunday, "KXTRUTHSOCIALD") == "day_open"


def test_daily_shadow_uses_factbase_and_the_day_open_clock(tmp_path: Path) -> None:
    """A daily row is the same free feed. Midnight ET is day_open, and noon is noon."""
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        rows = [
            {
                "date": "2026-08-01T16:00:00+00:00",
                "handle": "realDonaldTrump",
                "platform": "Truth Social",
                "deleted_flag": False,
                "type": "Social",
            }
        ]
        for day in range(1, 7):
            rows.append({
                "date": f"2026-09-0{day}T16:00:00+00:00",
                "handle": "realDonaldTrump",
                "platform": "Truth Social",
                "deleted_flag": False,
                "type": "Social",
            })
        return json.dumps({"data": rows})

    market = {
        "ticker": "KXTRUTHSOCIALD-26OCT03-B17",
        "series_ticker": "KXTRUTHSOCIALD",
        "rules_primary": (
            "Resolves Yes if the number of Truth Social posts by Donald Trump "
            "on Oct 3, 2026 is between 15 and 19."
        ),
        "strike_type": "between",
        "yes_ask_cents": 40,
    }
    path = tmp_path / "shadow.jsonl"
    written = record_countable_shadow(
        [market],
        now=datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc),
        path=path,
        fetch=fetch,
        sleeper=lambda _seconds: None,
    )
    assert written == 1
    assert len(calls) == 1 and "rollcall.com" in calls[0]
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["feed"] == "factbase"
    assert row["clock"] == "day_open"
    assert row["so_far"] == 0
    assert row["yes_ask"] == 40
    assert row["reason"] is None
    noon = datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
    assert shadow._clock(noon, "KXTRUTHSOCIALD") == "noon"
    assert shadow._clock(noon, "KXEOWEEK") == "other"
