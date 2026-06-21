"""Unit tests for tasks/stats/edge_series — the self-maintaining news-edge set.

The bot's tradeable surface is a handful of event-driven political/geopolitical
series. A hand-frozen list rots (an administration changes, Kalshi retires a
series) and silently wastes retrieval budget. This module derives the active
edge set from a rolling window of OPPORTUNITY history so series auto-promote
when they start producing and auto-age-out when they stop — mirroring
feeds/subreddit_discovery's candidate + ZERO_SIGNAL_POSTS suppression and
analysis/match_feedback's recomputed-artifact pattern.
"""

from datetime import datetime, timedelta, timezone

from tasks.stats.edge_series import (
    compute_edge_series,
    load_edge_series,
    write_edge_series,
    active_edge_series,
    DEFAULT_WINDOW_DAYS,
)

NOW = datetime(2026, 5, 30, tzinfo=timezone.utc)


def _ev(series, days_ago):
    return (series, (NOW - timedelta(days=days_ago)).isoformat())


# ── compute: promotion + aging ────────────────────────────────────────────────

def test_compute_promotes_series_meeting_min_opps_in_window():
    events = [_ev("KXTRUMPIRAN", d) for d in (1, 2, 3)]  # 3 recent opps
    entries = compute_edge_series(events, now=NOW, min_opps=3)
    assert "KXTRUMPIRAN" in entries
    assert entries["KXTRUMPIRAN"]["opportunities"] == 3


def test_compute_excludes_series_below_min_opps():
    events = [_ev("KXTRUMPIRAN", 1), _ev("KXTRUMPIRAN", 2)]  # only 2
    entries = compute_edge_series(events, now=NOW, min_opps=3)
    assert "KXTRUMPIRAN" not in entries


def test_compute_ages_out_opportunities_outside_window():
    # 4 opportunities but all older than the window -> not promoted.
    old = [_ev("KXMOCTRUMP25", DEFAULT_WINDOW_DAYS + 5) for _ in range(4)]
    entries = compute_edge_series(old, now=NOW, window_days=DEFAULT_WINDOW_DAYS, min_opps=3)
    assert "KXMOCTRUMP25" not in entries


# ── active_edge_series: seed fallback + aging + pinning ────────────────────────

def test_active_falls_back_to_seed_on_cold_start(tmp_path):
    # No artifact written -> cold start -> use the static seed.
    path = tmp_path / "news_edge_series.json"
    seed = frozenset({"KXTRUMPIRAN", "KXTRUMPCHINA"})
    assert active_edge_series(path=path, seed=seed, now=NOW) == seed


def test_active_ignores_seed_once_artifact_exists(tmp_path):
    # Artifact exists with a fresh series -> the dead seed series is NOT resurrected.
    path = tmp_path / "news_edge_series.json"
    write_edge_series(
        {"KXNEWHOTNESS": {"opportunities": 5, "last_seen_utc": _ev("x", 1)[1]}},
        path,
    )
    seed = frozenset({"KXTRUMPIRAN"})  # yesterday's edge, no longer producing
    active = active_edge_series(path=path, seed=seed, now=NOW)
    assert active == frozenset({"KXNEWHOTNESS"})
    assert "KXTRUMPIRAN" not in active


def test_active_ages_out_stale_artifact_entries(tmp_path):
    path = tmp_path / "news_edge_series.json"
    write_edge_series(
        {"KXDEADSERIES": {"opportunities": 9,
                          "last_seen_utc": (NOW - timedelta(days=DEFAULT_WINDOW_DAYS + 10)).isoformat()}},
        path,
    )
    # Artifact exists but the only entry is stale -> no prioritization (safe:
    # falls back to pure open-interest ranking, NOT to the seed).
    assert active_edge_series(path=path, seed=frozenset({"KXTRUMPIRAN"}), now=NOW) == frozenset()


def test_active_keeps_pinned_series_regardless_of_age(tmp_path):
    path = tmp_path / "news_edge_series.json"
    write_edge_series(
        {"KXOPERATORPIN": {"opportunities": 0, "pinned": True,
                           "last_seen_utc": (NOW - timedelta(days=999)).isoformat()}},
        path,
    )
    assert "KXOPERATORPIN" in active_edge_series(path=path, seed=frozenset(), now=NOW)


def test_load_returns_empty_dict_when_absent(tmp_path):
    assert load_edge_series(tmp_path / "missing.json") == {}
