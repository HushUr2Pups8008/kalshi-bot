"""Countable-series shadow: a free probability beside the ask, never a queue."""

from __future__ import annotations

import json
import math
import urllib.request
from datetime import date, datetime, timedelta, timezone
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

        def one(stamp: str, **extra: object) -> dict[str, object]:
            row: dict[str, object] = {
                "date": stamp, "handle": "realDonaldTrump", "platform": "Truth Social",
                "deleted_flag": False, "type": "Social",
            }
            row.update(extra)
            return row

        counts = (("11", 1), ("12", 8), ("13", 1), ("14", 8), ("15", 1), ("16", 8), ("17", 1))
        rows = [one(f"2026-10-{day}T15:00:00+00:00") for day, count in counts for _ in range(count)]
        rows += [
            one("2026-08-01T15:00:00+00:00"),
            one("2026-10-18T15:00:00+00:00", deleted_flag=True),
            one("2026-10-18T16:00:00+00:00", handle="someoneelse"),
            one("2026-10-18T17:00:00+00:00"),
        ]
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
    assert "hour_probability" not in row
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
    """Wednesday keeps the higher count. A proclamation is not an executive order."""
    calls: list[str] = []

    def item(day: date, link: str, category: str) -> str:
        stamp = datetime(day.year, day.month, day.day, 18, tzinfo=shadow.ET).strftime("%a, %d %b %Y %H:%M:%S %z")
        return f"<item><link>{link}</link><pubDate>{stamp}</pubDate><category>{category}</category></item>"

    def register_page() -> str:
        results = [
            {"executive_order_number": "14400", "signing_date": f"2026-08-0{day}"}
            for day in (1, 2, 3, 4, 6)
        ]
        results.append({"executive_order_number": "14410", "signing_date": "2026-08-10"})
        return json.dumps({"count": len(results), "results": results})

    def fetch(url: str) -> str:
        calls.append(url)
        if "federalregister.gov" in url:
            return register_page()
        rows = (
            item(date(2026, 8, 10), "https://www.whitehouse.gov/eo/published", "Executive Orders"),
            item(date(2026, 8, 11), "https://www.whitehouse.gov/eo/unpublished", "Executive Orders"),
            item(date(2026, 8, 11), "https://www.whitehouse.gov/eo/proclamation", "Proclamations"),
            item(date(2026, 8, 1), "https://www.whitehouse.gov/eo/old", "Executive Orders"),
        )
        return "<rss><channel>" + "".join(rows) + "</channel></rss>"

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
        now=datetime(2026, 8, 12, 16, tzinfo=timezone.utc),
        path=path,
        fetch=fetch,
        sleeper=lambda _seconds: None,
    )
    assert written == 1
    assert [url for url in calls if "federalregister.gov" in url]
    assert shadow.ACTION_URL in calls
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["feed"] == "federal_register"
    assert row["clock"] == "wednesday"
    assert row["so_far"] == 2
    assert row["yes_ask"] == 40
    assert row["reason"] is None
    contract = parse_contract(str(market["rules_primary"]), "greater")
    posts = [datetime(2026, 8, day, 12, tzinfo=shadow.ET) for day in (1, 2, 3, 4, 6, 10)]
    fitted = shadow._fit(posts, date(2026, 8, 9), False, date(2026, 8, 9) - timedelta(days=60))
    assert contract is not None and fitted is not None
    days = float(row["days_left"])
    # WHY: the Register has the Aug 10 order only. The RSS also has Aug 11, which is not published yet.
    assert row["register_probability"] == f"{yes_probability(1, days, fitted[0], fitted[1], contract)[1]:.6f}"
    assert row["probability"] == f"{yes_probability(2, days, fitted[0], fitted[1], contract)[1]:.6f}"
    assert row["probability"] != row["register_probability"]
    assert "hour_probability" not in row
    assert "api.x.ai" not in Path(shadow.__file__).read_text(encoding="utf-8")
    assert "place_limit_order" not in Path(shadow.__file__).read_text(encoding="utf-8")

    def register_only(url: str) -> str:
        if "whitehouse.gov" in url:
            raise OSError("rss")
        return register_page()

    assert record_countable_shadow(
        [market],
        now=datetime(2026, 8, 12, 16, tzinfo=timezone.utc),
        path=path,
        fetch=register_only,
        sleeper=lambda _seconds: None,
    ) == 1
    fallen = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert fallen["so_far"] == 1
    assert fallen["probability"] == fallen["register_probability"]
    assert fallen["reason"] is None
    wednesday = datetime(2026, 8, 12, 16, 0, tzinfo=timezone.utc)
    assert shadow._clock(wednesday) == "wednesday"
    assert shadow._clock(wednesday, "KXTRUMPACT") == "wednesday"
    sunday = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)
    assert shadow._clock(sunday, "KXEOWEEK") == "sunday_open"
    assert shadow._clock(datetime(2026, 10, 23, 22, tzinfo=timezone.utc), "KXTRUMPACT") == "friday_evening"
    assert shadow._clock(datetime(2026, 10, 23, 22, tzinfo=timezone.utc), "KXTRUTHSOCIAL") == "other"
    assert shadow._clock(datetime(2026, 10, 24, 16, tzinfo=timezone.utc), "KXTRUMPACT") == "saturday"
    assert shadow._clock(datetime(2026, 10, 24, 16, tzinfo=timezone.utc)) == "other"


def _action_rss(day: date, link: str) -> str:
    stamp = datetime(day.year, day.month, day.day, 18, tzinfo=shadow.ET).strftime("%a, %d %b %Y %H:%M:%S %z")
    return f"<item><link>{link}</link><pubDate>{stamp}</pubDate></item>"


def test_action_pages_cover_sixty_days_and_drop_the_cap(tmp_path: Path) -> None:
    """Page 1 is the 30-item cap. Page 2 reaches the 60-day window, so the fit is not truncated."""
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        page = 1 if "paged=" not in url else int(url.rsplit("=", 1)[-1])
        origin = date(2026, 10, 20) - timedelta(days=(page - 1) * 30)
        rows = [
            _action_rss(origin - timedelta(days=offset), f"https://www.whitehouse.gov/a/{page}-{offset}")
            for offset in range(30)
        ]
        if page == 2:
            rows.append(_action_rss(date(2026, 8, 1), "https://www.whitehouse.gov/a/old"))
            rows.append(_action_rss(date(2026, 10, 18), "https://www.whitehouse.gov/a/1-2"))
        return "<rss><channel>" + "".join(rows) + "</channel></rss>"

    path = tmp_path / "shadow.jsonl"
    assert record_countable_shadow(
        [{
            "ticker": "KXTRUMPACT-26OCT24-T6",
            "series_ticker": "KXTRUMPACT",
            "rules_primary": ACTION_RULE,
            "strike_type": "greater_or_equal",
            "yes_ask_cents": 40,
        }],
        now=datetime(2026, 10, 20, 16, tzinfo=timezone.utc),
        path=path,
        fetch=fetch,
        sleeper=lambda _seconds: None,
    ) == 1
    row = json.loads(path.read_text(encoding="utf-8"))
    assert calls == [shadow.ACTION_URL, f"{shadow.ACTION_URL}?paged=2"]
    assert row["feed"] == "whitehouse_rss" and row["feed_capped"] is False and row["so_far"] == 2
    start = date(2026, 10, 18)
    need = start - timedelta(days=60)
    last = start - timedelta(days=1)
    posts = [
        datetime(need.year, need.month, need.day, 18, tzinfo=shadow.ET),
        datetime(last.year, last.month, last.day, 18, tzinfo=shadow.ET),
    ]
    full, short = shadow._fit(posts, start, False, need), shadow._fit(posts, start, True, need)
    # One event on the window's first day counts only when the feed is not capped.
    assert full is not None and short is not None
    assert full[0] == pytest.approx(2 / 60) and short[0] == pytest.approx(1 / 59)
    capped_calls: list[str] = []

    def capped(url: str) -> str:
        capped_calls.append(url)
        page = 1 if "paged=" not in url else int(url.rsplit("=", 1)[-1])
        rows = "".join(_action_rss(date(2026, 10, 1), f"https://www.whitehouse.gov/a/{page}-{i}") for i in range(30))
        return f"<rss><channel>{rows}</channel></rss>"

    assert record_countable_shadow(
        [{
            "ticker": "KXTRUMPACT-26OCT24-T6",
            "series_ticker": "KXTRUMPACT",
            "rules_primary": ACTION_RULE,
            "strike_type": "greater_or_equal",
            "yes_ask": 40,
        }],
        now=datetime(2026, 10, 20, 16, tzinfo=timezone.utc),
        path=path,
        fetch=capped,
        sleeper=lambda _seconds: None,
    ) == 1
    again = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert again["feed_capped"] is True and len(capped_calls) == shadow._ACTION_PAGES
    assert shadow._action_posts(date(2026, 1, 1), lambda _url: "<rss></rss>", None) == ([], False)


def test_trumpact_row_matches_the_flat_row_except_the_clock() -> None:
    """Friday 18:00 ET was other. The clock label is the only change, and the probability stays flat."""
    start = date(2026, 10, 18)
    day = start - timedelta(days=60)
    posts = []
    while day < start:
        if day.weekday() <= 3:
            posts.append(datetime(day.year, day.month, day.day, 12, tzinfo=shadow.ET))
        day += timedelta(days=1)
    moment = datetime(2026, 10, 23, 22, tzinfo=timezone.utc)
    market = {"ticker": "KXTRUMPACT-26OCT24-T1", "series_ticker": "KXTRUMPACT", "yes_ask_cents": 40}
    contract = {"start": start, "end": date(2026, 10, 24), "how": "ge", "strike": 1}
    action = shadow._score(market, contract, posts, False, moment)
    assert action["clock"] == "friday_evening"
    assert "hour_probability" not in action
    # WHY: these strings are the pre-#386 flat row. Only the clock label moved.
    assert action["model"] == "poisson" and action["probability"] == "0.507536"
    fitted = shadow._fit(posts, start, False, start - timedelta(days=60))
    assert fitted is not None
    flat = shadow.yes_probability(action["so_far"], float(action["days_left"]), fitted[0], fitted[1], contract)
    assert flat is not None and action["probability"] == f"{flat[1]:.6f}"
    saturday = shadow._score(
        market, contract, posts, False, datetime(2026, 10, 24, 16, tzinfo=timezone.utc),
    )
    assert saturday["clock"] == "saturday" and saturday["probability"] == "0.246731"
    assert "hour_probability" not in saturday
    truth = shadow._score(
        {"ticker": "KXTRUTHSOCIAL-26OCT24-T240", "series_ticker": "KXTRUTHSOCIAL"},
        parse_contract(TRUTH_RULE, "greater"),
        posts,
        False,
        moment,
    )
    assert truth["clock"] == "other" and "hour_probability" not in truth