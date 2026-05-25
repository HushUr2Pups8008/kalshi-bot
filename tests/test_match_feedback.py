"""Tests for analysis/match_feedback.py (PROFIT-MATCH-DYNAMIC commit 3/5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from analysis.match_feedback import (
    DOWNWEIGHT_FLOOR,
    MIN_TOTAL_FOR_DOWNWEIGHT,
    TokenStats,
    aggregate_window,
    get_token_weight,
    ingest_review_events,
    load_weights,
    update_weights_from_stats,
    write_weights,
)


def _ev(token_list, prefix, verdict, day):
    return {
        "type": "MATCH_LLM_REVIEW",
        "ts": f"{day}T12:00:00+00:00",
        "ticker": f"{prefix}-26JUN01",
        "market_title": "some market",
        "market_prefix": prefix,
        "headline": "some headline",
        "source": "src",
        "matched_tokens": token_list,
        "llm_relevant": False,
        "llm_direction": "neutral" if verdict == "false_positive_neutral" else "yes",
        "llm_magnitude": "none" if verdict == "false_positive_neutral" else "small",
        "llm_confidence": 0.85,
        "verdict": verdict,
    }


class TestIngestReviewEvents:
    def test_counts_per_token_per_prefix_per_day(self, tmp_path: Path):
        db = tmp_path / "fp.db"
        events = [
            _ev(["trump"], "KXCABLEAVE", "false_positive_neutral", "2026-05-20"),
            _ev(["trump"], "KXCABLEAVE", "false_positive_neutral", "2026-05-20"),
            _ev(["trump", "iran"], "KXTRUMPIRAN", "true_positive", "2026-05-20"),
        ]
        applied = ingest_review_events(events, db_path=db)
        # 2 events × 1 token + 1 event × 2 tokens = 4 increments
        assert applied == 4
        stats = aggregate_window(window_days=30, today_utc="2026-05-21", db_path=db)
        by_key = {(s.token, s.market_prefix): s for s in stats}
        assert by_key[("trump", "KXCABLEAVE")].fp_neutral == 2
        assert by_key[("trump", "KXCABLEAVE")].true_positive == 0
        assert by_key[("trump", "KXTRUMPIRAN")].true_positive == 1
        assert by_key[("iran", "KXTRUMPIRAN")].true_positive == 1

    def test_unknown_verdict_is_skipped(self, tmp_path: Path):
        db = tmp_path / "fp.db"
        bad = dict(_ev(["x"], "KX", "false_positive_neutral", "2026-05-20"))
        bad["verdict"] = "undetermined"
        assert ingest_review_events([bad], db_path=db) == 0

    def test_missing_market_prefix_skipped(self, tmp_path: Path):
        db = tmp_path / "fp.db"
        bad = dict(_ev(["x"], "KX", "true_positive", "2026-05-20"))
        bad["market_prefix"] = ""
        assert ingest_review_events([bad], db_path=db) == 0

    def test_idempotent_re_ingest_within_same_day_increments(self, tmp_path: Path):
        """Re-running the aggregator on the same events double-counts within
        a single day. This is acceptable because the rolling window aggregator
        is called once per day in the cron design; re-running ad-hoc is a
        diagnostic action, not a state-maintenance one. Document the property
        explicitly so future-me doesn't expect dedup."""
        db = tmp_path / "fp.db"
        events = [_ev(["trump"], "KXCABLEAVE", "false_positive_neutral", "2026-05-20")]
        ingest_review_events(events, db_path=db)
        ingest_review_events(events, db_path=db)
        stats = aggregate_window(window_days=30, today_utc="2026-05-21", db_path=db)
        s = next(s for s in stats if s.token == "trump" and s.market_prefix == "KXCABLEAVE")
        assert s.fp_neutral == 2  # double-counted, by design


class TestAggregateWindow:
    def test_window_filters_old_days(self, tmp_path: Path):
        db = tmp_path / "fp.db"
        # Old day, should NOT be included in 14d window from 2026-05-30
        ingest_review_events(
            [_ev(["old"], "KX", "false_positive_neutral", "2026-05-10")],
            db_path=db,
        )
        # Recent day, IS in window
        ingest_review_events(
            [_ev(["recent"], "KX", "false_positive_neutral", "2026-05-25")],
            db_path=db,
        )
        stats = aggregate_window(window_days=14, today_utc="2026-05-30", db_path=db)
        tokens = {s.token for s in stats}
        assert "recent" in tokens
        assert "old" not in tokens

    def test_empty_db_returns_empty_list(self, tmp_path: Path):
        assert aggregate_window(db_path=tmp_path / "absent.db") == []


class TestTokenStats:
    def test_fp_rate_with_no_observations(self):
        assert TokenStats("t", "P", 0, 0).fp_rate == 0.0

    def test_fp_rate_arithmetic(self):
        s = TokenStats("t", "P", fp_neutral=8, true_positive=2)
        assert s.fp_rate == pytest.approx(0.8)
        assert s.total == 10


class TestUpdateWeightsFromStats:
    def test_below_min_total_no_downweight(self):
        weights = {}
        stats = [TokenStats("t", "P", fp_neutral=MIN_TOTAL_FOR_DOWNWEIGHT - 1, true_positive=0)]
        out = update_weights_from_stats(stats, weights=weights, now_iso="2026-05-30T00:00:00+00:00")
        # No entry created when below threshold and no prior weight existed
        assert "P:t" not in out

    def test_above_threshold_applies_downweight(self):
        # fp_rate = 8/10 = 0.8 → weight = max(0.10, 1 - 0.8) = 0.20
        stats = [TokenStats("t", "P", fp_neutral=8, true_positive=2)]
        out = update_weights_from_stats(stats, weights={}, now_iso="2026-05-30T00:00:00+00:00")
        assert out["P:t"]["weight"] == pytest.approx(0.20)
        assert out["P:t"]["fp_rate"] == pytest.approx(0.80)
        assert out["P:t"]["pinned"] is False

    def test_high_fp_rate_clamped_to_floor(self):
        # fp_rate = 1.0 → weight = max(0.10, 0.0) = 0.10
        stats = [TokenStats("t", "P", fp_neutral=20, true_positive=0)]
        out = update_weights_from_stats(stats, weights={}, now_iso="now")
        assert out["P:t"]["weight"] == DOWNWEIGHT_FLOOR

    def test_recovery_resets_to_one(self):
        # Existing downweighted entry. New stats show fp_rate < RECOVERY → reset.
        weights = {"P:t": {"weight": 0.3, "fp_rate": 0.7, "total": 10, "pinned": False,
                           "updated_utc": "old"}}
        stats = [TokenStats("t", "P", fp_neutral=1, true_positive=20)]  # fp_rate = ~0.048
        out = update_weights_from_stats(stats, weights=weights, now_iso="now")
        assert out["P:t"]["weight"] == 1.0

    def test_hysteresis_band_preserves_weight(self):
        # fp_rate between RECOVERY (0.25) and ACTIVATE (0.40) → keep prior weight.
        weights = {"P:t": {"weight": 0.55, "fp_rate": 0.45, "total": 10, "pinned": False,
                           "updated_utc": "old"}}
        stats = [TokenStats("t", "P", fp_neutral=3, true_positive=7)]  # fp_rate = 0.30
        out = update_weights_from_stats(stats, weights=weights, now_iso="now")
        assert out["P:t"]["weight"] == 0.55  # unchanged

    def test_pinned_entries_never_overwritten(self):
        weights = {"P:t": {"weight": 0.05, "fp_rate": 0.99, "total": 100,
                           "pinned": True, "updated_utc": "old"}}
        # Even with strong recovery signal, pinned weight stays at 0.05.
        stats = [TokenStats("t", "P", fp_neutral=0, true_positive=100)]
        out = update_weights_from_stats(stats, weights=weights, now_iso="now")
        assert out["P:t"]["weight"] == 0.05
        assert out["P:t"]["pinned"] is True
        # But bookkeeping fields ARE updated so the operator sees current fp_rate
        assert out["P:t"]["fp_rate"] == pytest.approx(0.0)
        assert out["P:t"]["total"] == 100

    def test_data_shrunk_below_min_resets_weight(self):
        """If a previously-downweighted token loses evidence (e.g. window
        rolls forward, old days drop off), restore weight=1.0 rather than
        leaving a stale downweight."""
        weights = {"P:t": {"weight": 0.30, "fp_rate": 0.70, "total": 10, "pinned": False,
                           "updated_utc": "old"}}
        stats = [TokenStats("t", "P", fp_neutral=2, true_positive=2)]  # below MIN
        out = update_weights_from_stats(stats, weights=weights, now_iso="now")
        assert out["P:t"]["weight"] == 1.0


class TestWeightsFileRoundTrip:
    def test_load_returns_empty_when_missing(self, tmp_path: Path):
        assert load_weights(tmp_path / "absent.json") == {}

    def test_load_returns_empty_on_malformed_json(self, tmp_path: Path):
        p = tmp_path / "weights.json"
        p.write_text("{not json", encoding="utf-8")
        assert load_weights(p) == {}

    def test_write_then_load_round_trip(self, tmp_path: Path):
        p = tmp_path / "weights.json"
        data = {"P:t": {"weight": 0.3, "fp_rate": 0.7, "total": 10,
                        "pinned": False, "updated_utc": "now"}}
        write_weights(data, weights_path=p)
        assert load_weights(p) == data


class TestGetTokenWeight:
    def test_unknown_pair_returns_one(self, tmp_path: Path):
        assert get_token_weight("unknown", "KX", weights_path=tmp_path / "x") == 1.0

    def test_known_pair_returns_weight(self):
        weights = {"P:t": {"weight": 0.25, "fp_rate": 0.75, "total": 10}}
        assert get_token_weight("t", "P", weights=weights) == 0.25

    def test_corrupted_weight_falls_back_to_one(self):
        weights = {"P:t": {"weight": "not a float"}}
        assert get_token_weight("t", "P", weights=weights) == 1.0
