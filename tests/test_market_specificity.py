"""Unit tests for analysis/market_specificity.py (ROADMAP P3.2).

Tests are grouped by concern:
- Component-level tests exercise each sub-score in isolation.
- Integration tests exercise ``compute_specificity_score`` on broad-vs-narrow
  market pairs to assert the ordering the score is designed to produce.
- Edge-case tests verify graceful handling of missing / empty fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from analysis.market_specificity import (
    _WEIGHTS,
    _days_to_close_score,
    _named_entity_density_score,
    _numeric_threshold_score,
    _specific_verb_score,
    _ticker_specificity_score,
    _token_count_score,
    compute_specificity_score,
    specificity_components,
)


@dataclass
class _FakeMarket:
    """Minimal KalshiMarket-shaped dataclass for pure tests — avoids importing
    the real KalshiMarket to keep this test file zero-coupled to kalshi/."""

    ticker: str = ""
    title: str = ""
    subtitle: str = ""
    close_time: str = ""


def _iso_in_days(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


class TestTokenCountScore:
    def test_empty_text_scores_zero(self):
        assert _token_count_score("") == 0.0

    def test_short_text_below_cap(self):
        # 5 tokens / 30 cap = 0.1667
        assert _token_count_score("will the deal be signed") == pytest.approx(5 / 30)

    def test_long_text_saturates_at_1(self):
        long_text = " ".join(["token"] * 100)
        assert _token_count_score(long_text) == 1.0

    def test_at_cap_returns_1(self):
        text = " ".join(["word"] * 30)
        assert _token_count_score(text) == 1.0


class TestSpecificVerbScore:
    def test_no_verb_scores_zero(self):
        assert _specific_verb_score("Will something happen soon") == 0.0

    def test_signed_hits(self):
        assert _specific_verb_score("Will a treaty be signed by June 1") == 1.0

    def test_tense_variant_hits(self):
        assert _specific_verb_score("Sanctions imposed on Iran") == 1.0

    def test_plural_variant_hits(self):
        assert _specific_verb_score("Congress passes legislation") == 1.0

    def test_empty_scores_zero(self):
        assert _specific_verb_score("") == 0.0


class TestNumericThresholdScore:
    def test_percentage_hits(self):
        assert _numeric_threshold_score("rates above 5%") == 1.0

    def test_dollars_hits(self):
        assert _numeric_threshold_score("imposes $1,000 fine") == 1.0

    def test_month_day_hits(self):
        assert _numeric_threshold_score("deadline is May 15 for action") == 1.0

    def test_year_hits(self):
        assert _numeric_threshold_score("by 2026 close") == 1.0

    def test_ordinal_hits(self):
        assert _numeric_threshold_score("the 15th of June") == 1.0

    def test_plain_prose_scores_zero(self):
        assert _numeric_threshold_score("Will peace be achieved") == 0.0

    def test_empty_scores_zero(self):
        assert _numeric_threshold_score("") == 0.0


class TestNamedEntityDensityScore:
    def test_empty_scores_zero(self):
        assert _named_entity_density_score("") == 0.0

    def test_no_entity_scores_zero(self):
        assert _named_entity_density_score("generic question about policy") == 0.0

    def test_one_entity(self):
        # 1 / 3 cap = 0.333...
        assert _named_entity_density_score("Iran and policy reform") == pytest.approx(1 / 3)

    def test_multiple_entities_saturate(self):
        text = "Iran Russia China NATO Trump Israel"
        assert _named_entity_density_score(text) == 1.0


class TestDaysToCloseScore:
    def test_missing_is_neutral(self):
        assert _days_to_close_score("") == 0.5

    def test_unparseable_is_neutral(self):
        assert _days_to_close_score("not a date") == 0.5

    def test_past_close_max(self):
        past = _iso_in_days(-1)
        assert _days_to_close_score(past) == 1.0

    def test_near_close_high(self):
        near = _iso_in_days(3)
        # 1 - 3/90 ≈ 0.967
        assert _days_to_close_score(near) == pytest.approx(1 - 3 / 90, abs=0.01)

    def test_far_close_zero(self):
        far = _iso_in_days(180)
        assert _days_to_close_score(far) == 0.0

    def test_at_cap_zero(self):
        # 90 days exactly is the cap; allow a small float tolerance because
        # wall-clock advances between _iso_in_days() and the score call.
        at_cap = _iso_in_days(90)
        assert _days_to_close_score(at_cap) == pytest.approx(0.0, abs=1e-6)


class TestTickerSpecificityScore:
    def test_date_encoded_ticker_hits(self):
        assert _ticker_specificity_score("KXTRUMPIRAN-26MAY01") == 1.0

    def test_dashed_date_ticker_hits(self):
        assert _ticker_specificity_score("KXMOCTRUMP25-26-APR24") == 1.0

    def test_b_prefixed_date_hits(self):
        assert _ticker_specificity_score("KXTRADEDEALCUBA-27-B260501") == 1.0

    def test_series_only_no_date_zero(self):
        assert _ticker_specificity_score("KXTRUMPENDORSE") == 0.0

    def test_empty_scores_zero(self):
        assert _ticker_specificity_score("") == 0.0


class TestComputeSpecificityScoreOrdering:
    """The key invariant: high-specificity markets score above low-specificity
    markets. These use realistic title/subtitle pairs observed in the
    Kalshi catalogue."""

    def test_narrow_scores_above_broad(self):
        broad = _FakeMarket(
            ticker="KXTRUMPIRAN",
            title="Will there be peace in the Middle East?",
            subtitle="",
            close_time=_iso_in_days(90),
        )
        narrow = _FakeMarket(
            ticker="KXIAEA-26MAY15",
            title="Will the IAEA issue a formal censure of Iran by 2026-05-15?",
            subtitle=(
                "Resolves YES if the International Atomic Energy Agency Board "
                "of Governors passes a formal censure resolution against Iran "
                "on or before May 15, 2026."
            ),
            close_time=_iso_in_days(20),
        )
        assert compute_specificity_score(narrow) > compute_specificity_score(broad)

    def test_scores_are_bounded_to_unit_interval(self):
        broad = _FakeMarket(
            title="Will there be peace?",
            close_time=_iso_in_days(365),
        )
        narrow = _FakeMarket(
            ticker="KXTEST-26MAY01",
            title="Will Congress pass H.R. 1234 by May 1, 2026?",
            subtitle=(
                "Resolves YES if the US House of Representatives passes bill "
                "H.R. 1234 with a majority vote and the Senate ratifies it "
                "before 5pm Eastern on May 1, 2026."
            ),
            close_time=_iso_in_days(5),
        )
        for market in (broad, narrow):
            s = compute_specificity_score(market)
            assert 0.0 <= s <= 1.0

    def test_very_specific_market_scores_above_half(self):
        """A market with ALL six features firing should score > 0.5."""
        market = _FakeMarket(
            ticker="KXIAEA-26MAY15",
            title="Will the IAEA issue a formal censure of Iran by May 15?",
            subtitle=(
                "Resolves YES if the Board of Governors passes a formal "
                "censure resolution against Iran with a majority vote on or "
                "before May 15, 2026 at 5pm EDT. Resolves NO otherwise."
            ),
            close_time=_iso_in_days(3),
        )
        assert compute_specificity_score(market) > 0.5

    def test_very_broad_market_scores_below_half(self):
        """A maximally-vague market with no date, no action, no entity should
        score low."""
        market = _FakeMarket(
            ticker="KXVAGUE",
            title="Will things improve?",
            subtitle="",
            close_time=_iso_in_days(365),
        )
        assert compute_specificity_score(market) < 0.5


class TestComponentsDict:
    def test_components_keys_match_weights(self):
        market = _FakeMarket()
        components = specificity_components(market)
        assert set(components.keys()) == set(_WEIGHTS.keys())

    def test_components_are_all_in_unit_interval(self):
        market = _FakeMarket(
            ticker="KXTEST-26MAY01",
            title="Will something specific happen by May 15, 2026?",
            subtitle="Resolves YES if the IAEA issues a formal censure.",
            close_time=_iso_in_days(10),
        )
        components = specificity_components(market)
        for name, value in components.items():
            assert 0.0 <= value <= 1.0, f"{name}={value} out of range"


class TestEdgeCases:
    def test_empty_market_returns_valid_score(self):
        market = _FakeMarket()
        score = compute_specificity_score(market)
        # Empty market: 0 on five features, 0.5 on days_to_close (neutral).
        # Result: 0.5 * 0.15 = 0.075
        assert score == pytest.approx(0.075, abs=0.001)

    def test_none_attributes_tolerated(self):
        # Ensure getattr(market, X, "") or "" pattern handles None gracefully.
        class _NoneyMarket:
            ticker = None
            title = None
            subtitle = None
            close_time = None

        score = compute_specificity_score(_NoneyMarket())
        assert 0.0 <= score <= 1.0

    def test_missing_subtitle_falls_back_to_title(self):
        """When subtitle is empty, the token-count feature should measure the
        title instead — otherwise markets with blank subtitles would score
        artificially low on that feature."""
        m_with_title_only = _FakeMarket(
            title="The IAEA will issue a formal censure by May 15, 2026",
            subtitle="",
            close_time=_iso_in_days(30),
        )
        m_with_empty_everything = _FakeMarket(
            title="",
            subtitle="",
            close_time=_iso_in_days(30),
        )
        assert (
            compute_specificity_score(m_with_title_only)
            > compute_specificity_score(m_with_empty_everything)
        )
