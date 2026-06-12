"""Tests for analysis/match_feedback.py (PROFIT-MATCH-DYNAMIC commit 3/5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from analysis.match_feedback import (
    DOWNWEIGHT_FLOOR,
    L2_NEUTRAL_FP_MARGINAL_MAX_SCORE,
    MIN_TOTAL_FOR_DOWNWEIGHT,
    TokenStats,
    aggregate_window,
    get_token_weight,
    ingest_review_events,
    is_market_defining_token,
    load_weights,
    market_prefix_for,
    summarize_weight_status,
    update_weights_from_stats,
    write_weights,
)


def _ev(token_list, prefix, verdict, day, match_score=None):
    ev = {
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
    if match_score is not None:
        ev["match_score"] = match_score
    return ev


def test_market_prefix_for_prefers_series_ticker_then_ticker_prefix():
    class Market:
        series_ticker = "polymarket_us"
        ticker = "ewc-usgub-ks-2026-11-03-dem"

    assert market_prefix_for(Market()) == "polymarket_us"

    class LegacyMarket:
        series_ticker = ""
        ticker = "KXNEWDEAL-JUN01"

    assert market_prefix_for(LegacyMarket()) == "KXNEWDEAL"


class TestL2ScoreGatedFalsePositive:
    """PROFIT-MATCH-003 (L2-a): a false_positive_neutral verdict only counts
    toward downweighting on a MARGINAL (low match_score) match. A neutral on a
    clearly-correct (high-score) match is 'right market, no edge from this
    headline' and must NOT poison the token's fp_rate. WHY: the loop's only
    negative signal conflated no-edge with wrong-match, flooring correct
    high-traffic markets (PROFIT-MATCH-002 was the defining-token symptom)."""

    def _fp(self, tmp_path, match_score):
        db = tmp_path / "fp.db"
        ingest_review_events(
            [_ev(["sanctions"], "KXVISITIRAN", "false_positive_neutral",
                 "2026-05-20", match_score=match_score)],
            db_path=db,
        )
        stats = aggregate_window(today_utc="2026-05-21", db_path=db)
        s = next((x for x in stats if x.token == "sanctions"), None)
        return s.fp_neutral if s else 0

    def test_high_score_neutral_not_counted(self, tmp_path: Path):
        # 0.20 >= 0.12 band -> clearly-correct match, no-edge -> NOT a downweight.
        assert self._fp(tmp_path, 0.20) == 0

    def test_marginal_score_neutral_counted(self, tmp_path: Path):
        # 0.05 < 0.12 band -> marginal/possibly-wrong match -> counts as fp.
        assert self._fp(tmp_path, 0.05) == 1

    def test_missing_score_counted_conservatively(self, tmp_path: Path):
        # No match_score on the event -> treated as marginal (prior behaviour).
        assert self._fp(tmp_path, None) == 1

    def test_boundary_score_counted(self, tmp_path: Path):
        # Exactly at the band is NOT below it -> not counted (>= skips).
        assert self._fp(tmp_path, L2_NEUTRAL_FP_MARGINAL_MAX_SCORE) == 0

    def test_true_positive_always_counted_regardless_of_score(self, tmp_path: Path):
        db = tmp_path / "fp.db"
        ingest_review_events(
            [_ev(["iran"], "KXVISITIRAN", "true_positive", "2026-05-20",
                 match_score=0.05)],
            db_path=db,
        )
        stats = aggregate_window(today_utc="2026-05-21", db_path=db)
        s = next(x for x in stats if x.token == "iran")
        assert s.true_positive == 1


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

    def test_defining_token_bypasses_floored_weight(self):
        # The loop floored KXVISITIRAN:iran to 0.10, but 'iran' is the market's
        # own ticker-defining token — get_token_weight must ignore the learned
        # weight and return 1.0 so the correct market stays matchable.
        weights = {"KXVISITIRAN:iran": {"weight": 0.10, "fp_rate": 0.97, "total": 1058}}
        assert get_token_weight("iran", "KXVISITIRAN", weights=weights) == 1.0


class TestIsMarketDefiningToken:
    """A token encoded into the series ticker is the market's subject word and
    must never be downweighted as a noise bridge. See is_market_defining_token.
    """

    @pytest.mark.parametrize(
        "token,prefix",
        [
            ("iran", "KXVISITIRAN"),
            ("tariffs", "KXNEWTARIFFS"),
            ("zelenskyy", "KXZELENSKYYOUT"),
            ("netanyahu", "KXNETANYAHUOUT"),
            ("india", "KXTARIFFRATEINDIA"),
            ("IRAN", "kxvisitiran"),  # case-insensitive
        ],
    )
    def test_defining_tokens_detected(self, token, prefix):
        assert is_market_defining_token(token, prefix) is True

    @pytest.mark.parametrize(
        "token,prefix",
        [
            ("trump", "KXCABLEAVE"),   # bridge token, not in ticker
            ("sanctions", "KXVISITIRAN"),
            ("out", "KXSCHUMEROUT"),   # len < 4: incidental fragment
            ("", "KXVISITIRAN"),       # empty token
            ("iran", ""),              # empty prefix
        ],
    )
    def test_non_defining_tokens_rejected(self, token, prefix):
        assert is_market_defining_token(token, prefix) is False


class TestSummarizeWeightStatus:
    def test_counts_pinned_provisional_automatic_and_recovered(self):
        weights = {
            "P:pinned": {"weight": 0.2, "pinned": True},
            "P:provisional": {"weight": 0.4, "pinned": False, "_seed_status": "provisional"},
            "P:auto": {"weight": 0.3, "pinned": False, "fp_rate": 0.7, "total": 12},
            "P:recovered": {"weight": 1.0, "pinned": False, "fp_rate": 0.0, "total": 20},
        }

        summary = summarize_weight_status(weights)

        assert summary["total_entries"] == 4
        assert summary["counts"] == {
            "pinned": 1,
            "provisional": 1,
            "automatic": 1,
            "recovered": 1,
        }

    def test_missing_metadata_counts_as_automatic(self):
        summary = summarize_weight_status({"P:legacy": {"weight": 0.5}})

        assert summary["counts"]["automatic"] == 1
        assert summary["entries"]["P:legacy"]["status"] == "automatic"


# ---------------------------------------------------------------------------
# PROFIT-MATCH-DYNAMIC commit 5/5 — seed file integration
# ---------------------------------------------------------------------------

class TestSeedWeightsFile:
    """Pin the committed seed weights at data/matcher_token_weights.json so
    accidental edits surface without treating the small audit sample as a
    permanent operator override."""

    SEED_PATH = Path("data/matcher_token_weights.json")

    def test_seed_file_exists_and_loads(self):
        assert self.SEED_PATH.exists(), (
            "data/matcher_token_weights.json must be checked in as the seed "
            "for the matcher feedback loop. Commit 5/5 of PROFIT-MATCH-DYNAMIC."
        )
        weights = load_weights(self.SEED_PATH)
        assert weights, "seed file loaded as empty — check JSON syntax"

    def test_seed_provisional_entries_include_audit_findings(self):
        """The 2026-05-24 per-LLM-call audit produced these specific
        (token, market_prefix) findings. Keep them as provisional cold-start
        weights, but do not pin them: 11 audited events are not enough evidence
        for a permanent override that the aggregator cannot recover from."""
        weights = load_weights(self.SEED_PATH)
        required = {
            "KXCABLEAVE:trump": 0.10,
            "KXCABLEAVE:any": 0.10,
            "KXNEWDEAL:deal": 0.25,
            "KXNEWDEAL:trump": 0.40,
        }
        for key, expected_max in required.items():
            assert key in weights, f"{key} missing from seed file"
            assert weights[key].get("pinned") is False, (
                f"{key} must stay provisional until replay or live volume "
                "justifies an operator pin"
            )
            assert weights[key].get("weight", 1.0) <= expected_max, (
                f"{key} weight relaxed beyond audit findings"
            )
            assert weights[key].get("_seed_status") == "provisional", (
                f"{key} must advertise its weak evidence basis"
            )

    def test_seed_aggregator_run_can_recover_provisional_weight(self, tmp_path):
        """New evidence must be allowed to lift a provisional audit seed."""
        seed = load_weights(self.SEED_PATH)
        # Aggregator-simulating call: pretend many recent matches show
        # KXCABLEAVE:trump as legitimate. Since the seed is no longer pinned,
        # recovery should lift it to full weight.
        from analysis.match_feedback import TokenStats
        stats = [
            TokenStats(
                token="trump", market_prefix="KXCABLEAVE",
                fp_neutral=0, true_positive=500,  # 0% FP rate → would recover
            )
        ]
        out = update_weights_from_stats(stats, weights=dict(seed),
                                         now_iso="2026-06-01T00:00:00+00:00")
        assert out["KXCABLEAVE:trump"]["weight"] == 1.0
        assert out["KXCABLEAVE:trump"]["pinned"] is False
        assert out["KXCABLEAVE:trump"]["fp_rate"] == 0.0
        assert out["KXCABLEAVE:trump"]["total"] == 500
