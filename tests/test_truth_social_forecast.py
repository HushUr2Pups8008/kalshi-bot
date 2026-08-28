from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from analysis.truth_social_forecast import (
    TruthSocialCountObservation,
    fetch_truth_social_count,
    fetch_truth_social_phrase,
    parse_truth_social_count_contract,
    parse_truth_social_phrase_contract,
    remaining_threshold_probability,
    truth_social_phrase_probability,
    truth_social_range_probability,
)


QUERY = (
    "Will Donald Trump make between 140 and 159 Truth Social posts "
    "the week of Aug 16, 2026? KXTRUTHSOCIAL-26AUG22-B149"
)


def test_parse_truth_social_count_contract():
    contract = parse_truth_social_count_contract(QUERY)

    assert contract is not None
    assert contract.lower == 140
    assert contract.upper == 159
    assert contract.week_start == date(2026, 8, 16)
    assert contract.ticker == "KXTRUTHSOCIAL-26AUG22-B149"


def test_parse_truth_social_count_contract_rejects_unbounded_query():
    assert parse_truth_social_count_contract("Truth Social posts this week") is None


def test_parse_truth_social_count_contract_supports_threshold_market():
    contract = parse_truth_social_count_contract(
        "Will Donald Trump make above 240 Truth Social posts the week of "
        "Aug 16, 2026? KXTRUTHSOCIAL-26AUG22-T240"
    )

    assert contract is not None
    assert contract.lower == 241
    assert contract.upper == 10**9
    assert contract.week_start == date(2026, 8, 16)


def test_fetch_truth_social_count_paginates_until_week_boundary(monkeypatch):
    pages = {
        1: {
            "data": [
                {"date": "2026-08-21T12:00:00-04:00", "deleted_flag": False},
                {"date": "2026-08-20T12:00:00-04:00", "deleted_flag": True},
            ]
        },
        2: {
            "data": [
                {"date": "2026-08-15T12:00:00-04:00", "deleted_flag": False},
            ]
        },
    }

    def fake_fetch(url, *, timeout):
        page = int(parse_qs(urlparse(url).query)["page"][0])
        return pages[page]

    monkeypatch.setattr("analysis.truth_social_forecast._fetch_json", fake_fetch)
    observation = fetch_truth_social_count(QUERY)

    assert observation is not None
    assert observation.count == 2
    assert observation.deleted_count == 1
    assert observation.pages == 2
    assert observation.complete is True


def test_fetch_truth_social_count_serializes_and_caches_same_window(monkeypatch):
    calls = 0

    def fake_fetch(url, *, timeout):
        nonlocal calls
        calls += 1
        page = int(parse_qs(urlparse(url).query)["page"][0])
        if page == 1:
            return {"data": [{"date": "2026-08-21T12:00:00-04:00"}]}
        return {"data": [{"date": "2026-08-15T12:00:00-04:00"}]}

    monkeypatch.setattr("analysis.truth_social_forecast._fetch_json", fake_fetch)
    first = fetch_truth_social_count(QUERY, max_pages=7)
    second = fetch_truth_social_count(QUERY, max_pages=7)

    assert first == second
    assert calls == 2


def test_fetch_truth_social_count_fails_closed_when_window_not_covered(monkeypatch):
    monkeypatch.setattr(
        "analysis.truth_social_forecast._fetch_json",
        lambda _url, *, timeout: {
            "data": [{"date": "2026-08-20T12:00:00-04:00"}]
        },
    )

    assert fetch_truth_social_count(QUERY, max_pages=1) is None


def _observation(**overrides) -> TruthSocialCountObservation:
    payload = {
        "count": 175,
        "deleted_count": 2,
        "window_start": date(2026, 8, 23),
        "window_end": date(2026, 8, 29),
        "lower": 220,
        "upper": 240,
        "complete": True,
        "pages": 4,
        "source_url": "https://rollcall.com/wp-json/factbase/v1/twitter",
        "observed_at": "2026-08-28T11:26:00+00:00",
    }
    payload.update(overrides)
    return TruthSocialCountObservation(**payload)


def test_truth_social_range_probability_locks_closed_week_outside_range():
    probability, state, confidence = truth_social_range_probability(
        _observation(),
        now=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
    )
    assert state == "closed"
    assert probability == 0.02
    assert confidence == 0.95


def test_truth_social_range_probability_locks_when_already_above_upper():
    probability, state, confidence = truth_social_range_probability(
        _observation(count=241),
        now=datetime(2026, 8, 28, 15, tzinfo=timezone.utc),
    )
    assert state == "already_outside_range"
    assert probability == 0.02
    assert confidence == 0.95


def test_truth_social_range_probability_locks_above_threshold_market():
    probability, state, confidence = truth_social_range_probability(
        _observation(count=241, lower=241, upper=10**9),
        now=datetime(2026, 8, 28, 15, tzinfo=timezone.utc),
    )
    assert state == "already_above_threshold"
    assert probability == 0.98


def test_truth_social_range_probability_fails_closed_on_thin_sample():
    assert (
        truth_social_range_probability(
            _observation(count=3),
            now=datetime(2026, 8, 23, 8, tzinfo=ZoneInfo("America/New_York")),
        )
        is None
    )


def test_truth_social_range_probability_open_week_uses_run_rate():
    probability, state, confidence = truth_social_range_probability(
        _observation(),
        now=datetime(2026, 8, 28, 11, 26, tzinfo=timezone.utc),
    )
    assert state == "open_run_rate"
    # Unhaircut weekday cloning put this print at ~0.84. The 0.80 remaining
    # rate haircut exists so a Friday-Saturday slowdown cannot mint that
    # lock. If this climbs back above 0.70 the haircut was removed.
    assert 0.35 < probability < 0.70
    assert confidence >= 0.82


def test_parse_truth_social_phrase_contract_splits_slash_alternates():
    contract = parse_truth_social_phrase_contract(
        'Will Trump say "Golf / Golfer / Golfing" before Aug 31, 2026? '
        "KXTRUMPSAY-26AUG31-GOLF"
    )
    assert contract is not None
    assert contract.phrases == ("Golf", "Golfer", "Golfing")
    assert contract.window_start == date(2026, 8, 1)
    assert contract.window_end == date(2026, 8, 31)
    assert contract.ticker == "KXTRUMPSAY-26AUG31-GOLF"


def test_remaining_threshold_probability_locks_when_already_there():
    probability, state, confidence = remaining_threshold_probability(
        count=6, threshold=6, elapsed_days=4.0, remaining_days=2.0
    )
    assert probability == 0.98
    assert state == "already_above_threshold"
    assert confidence == 0.95


def test_fetch_truth_social_phrase_hits_barack_hussein_obama(monkeypatch):
    pages = {
        1: {
            "data": [
                {
                    "date": "2026-08-07T18:55:00-04:00",
                    "text": (
                        "Two Judges, one appointed by Barack Hussein Obama, "
                        "the other by Sleepy Joe Biden"
                    ),
                    "post_url": "https://truthsocial.com/@realDonaldTrump/1",
                    "deleted_flag": False,
                },
                {
                    "date": "2026-07-31T12:00:00-04:00",
                    "text": "older than the window",
                    "deleted_flag": False,
                },
            ]
        }
    }

    def fake_fetch(url, *, timeout):
        page = int(parse_qs(urlparse(url).query)["page"][0])
        return pages[page]

    monkeypatch.setattr("analysis.truth_social_forecast._fetch_json", fake_fetch)
    observation = fetch_truth_social_phrase(
        'Will Trump say "Barack Hussein Obama" before Aug 31, 2026? '
        "KXTRUMPSAY-26AUG31-BARA"
    )
    assert observation is not None
    assert observation.hit is True
    assert observation.matched_phrase == "Barack Hussein Obama"
    probability, state, confidence = truth_social_phrase_probability(observation)
    assert state == "phrase_hit"
    assert probability == 0.98
    assert confidence == 0.95


def test_fetch_truth_social_phrase_returns_hit_without_full_month_scan(monkeypatch):
    pages = {
        1: {
            "data": [
                {
                    "date": "2026-08-28T09:00:00-04:00",
                    "text": "unrelated",
                    "deleted_flag": False,
                },
                {
                    "date": "2026-08-10T16:00:00-04:00",
                    "text": "Playing golf with Gary Player",
                    "post_url": "https://truthsocial.com/@realDonaldTrump/golf",
                    "deleted_flag": False,
                },
            ]
        }
    }
    calls = {"n": 0}

    def fake_fetch(url, *, timeout):
        calls["n"] += 1
        page = int(parse_qs(urlparse(url).query)["page"][0])
        return pages.get(page, {"data": []})

    monkeypatch.setattr("analysis.truth_social_forecast._fetch_json", fake_fetch)
    observation = fetch_truth_social_phrase(
        'Will Trump say "Golf / Golfer / Golfing" before Aug 31, 2026? '
        "KXTRUMPSAY-26AUG31-GOLF"
    )
    assert observation is not None
    assert observation.hit is True
    assert observation.matched_phrase == "Golf"
    assert calls["n"] == 1
