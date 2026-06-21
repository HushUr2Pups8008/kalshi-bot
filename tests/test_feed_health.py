"""Tests for tasks/stats/feed_health — RSS feed-health observability.

config.RSS_FEEDS is a hand-maintained list of feed URLs. Feeds rot silently:
a domain changes, an endpoint is deprecated, a CDN reshuffles, and the bot just
gets less news with no signal. This surfaces dead/empty/stale feeds. The pure
classification is unit-tested here; the network probe is an integration adapter.
"""

from tasks.stats.feed_health import classify_feed_health, _newest_entry_age_seconds
from datetime import datetime, timezone

WEEK = 7 * 86400


def test_unreachable_when_status_missing_or_error():
    assert classify_feed_health(status=None, entry_count=0, newest_age_seconds=None, stale_after_seconds=WEEK) == "unreachable"
    assert classify_feed_health(status=404, entry_count=0, newest_age_seconds=None, stale_after_seconds=WEEK) == "unreachable"
    assert classify_feed_health(status=500, entry_count=3, newest_age_seconds=10, stale_after_seconds=WEEK) == "unreachable"


def test_empty_when_ok_but_no_entries():
    assert classify_feed_health(status=200, entry_count=0, newest_age_seconds=None, stale_after_seconds=WEEK) == "empty"


def test_stale_when_newest_entry_older_than_threshold():
    assert classify_feed_health(status=200, entry_count=5, newest_age_seconds=10 * 86400, stale_after_seconds=WEEK) == "stale"


def test_live_when_fresh():
    assert classify_feed_health(status=200, entry_count=5, newest_age_seconds=3600, stale_after_seconds=WEEK) == "live"


def test_live_when_entries_present_but_undated():
    # No parseable dates -> can't assess staleness, but it IS producing entries.
    assert classify_feed_health(status=200, entry_count=5, newest_age_seconds=None, stale_after_seconds=WEEK) == "live"


def test_newest_entry_age_picks_smallest_age():
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    entries = [
        {"published": "2026-05-20T00:00:00+00:00"},  # 10 days
        {"published": "2026-05-29T00:00:00+00:00"},  # 1 day  (newest)
        {"published": "garbage"},
    ]
    age = _newest_entry_age_seconds(entries, now)
    assert age is not None
    assert abs(age - 1 * 86400) < 3600  # ~1 day


def test_newest_entry_age_none_when_no_dates():
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    assert _newest_entry_age_seconds([{"title": "x"}, {"published": "nope"}], now) is None
