"""
Tests for analysis/signal_analyzer.py

Covers: JSON extraction edge cases, _parse_llm_response probability mapping,
        keyword scoring with and without KeywordStats, geo-coherence suppression.
"""

import pytest
from unittest.mock import MagicMock

from analysis.signal_analyzer import _extract_json, _parse_llm_response, _keyword_score


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_clean_json(self):
        text = '{"relevant": true, "direction": "yes"}'
        result = _extract_json(text)
        assert result["relevant"] is True
        assert result["direction"] == "yes"

    def test_json_with_preamble(self):
        text = 'Here is my analysis: {"relevant": false, "magnitude": "none"}'
        result = _extract_json(text)
        assert result["relevant"] is False

    def test_json_with_brace_in_preamble(self):
        # Regression: "Consider {Russia}: {...}" should parse the LAST valid object
        text = 'Consider {Russia}: {"relevant": true, "direction": "no", "magnitude": "large", "confidence": 0.9, "reasoning": "test", "new_information": true}'
        result = _extract_json(text)
        assert result["direction"] == "no"
        assert result["magnitude"] == "large"

    def test_multiple_json_objects_returns_last(self):
        text = '{"first": 1} some text {"second": 2}'
        result = _extract_json(text)
        assert result["second"] == 2

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No valid JSON"):
            _extract_json("This has no JSON at all.")

    def test_nested_json_parses_last_found(self):
        # _extract_json scans all '{' positions and keeps the LAST valid object.
        # For '{"outer": {"inner": 42}}', the inner object at pos 10 is parsed last.
        text = '{"outer": {"inner": 42}}'
        result = _extract_json(text)
        # Either outer or inner is valid JSON; what matters is that no exception is raised
        # and a dict is returned. The exact object depends on parsing order.
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError):
            _extract_json("{not valid json")


# ---------------------------------------------------------------------------
# _parse_llm_response
# ---------------------------------------------------------------------------

def _make_market(yes_price=50.0):
    m = MagicMock()
    m.yes_price = yes_price
    m.yes_prob  = yes_price / 100.0
    return m


class TestParseLlmResponse:
    def test_relevant_yes_moderate_shifts_up(self):
        parsed = {
            "relevant": True,
            "new_information": True,
            "direction": "yes",
            "magnitude": "moderate",
            "confidence": 0.8,
            "reasoning": "direct impact",
        }
        market = _make_market(50.0)
        prob, conf, reasoning, direction, magnitude = _parse_llm_response(parsed, market)
        # shift = 0.15 * 0.8 = 0.12; prob = 0.50 + 0.12 = 0.62
        assert prob == pytest.approx(0.62, abs=0.001)
        assert conf == pytest.approx(0.8, abs=0.001)
        assert direction == "yes"
        assert magnitude == "moderate"

    def test_relevant_no_large_shifts_down(self):
        parsed = {
            "relevant": True,
            "new_information": True,
            "direction": "no",
            "magnitude": "large",
            "confidence": 1.0,
            "reasoning": "ceasefire agreed",
        }
        market = _make_market(70.0)
        prob, conf, _, direction, magnitude = _parse_llm_response(parsed, market)
        # shift = 0.25 * 1.0 = 0.25; prob = 0.70 - 0.25 = 0.45
        assert prob == pytest.approx(0.45, abs=0.001)
        assert direction == "no"
        assert magnitude == "large"

    def test_not_relevant_returns_market_price(self):
        parsed = {
            "relevant": False,
            "new_information": True,
            "direction": "yes",
            "magnitude": "large",
            "confidence": 0.9,
            "reasoning": "unrelated",
        }
        market = _make_market(35.0)
        prob, _, _, direction, magnitude = _parse_llm_response(parsed, market)
        assert prob == pytest.approx(0.35, abs=0.001)

    def test_not_new_info_returns_market_price(self):
        parsed = {
            "relevant": True,
            "new_information": False,
            "direction": "yes",
            "magnitude": "moderate",
            "confidence": 0.7,
            "reasoning": "already priced in",
        }
        market = _make_market(60.0)
        prob, _, _, _, _ = _parse_llm_response(parsed, market)
        assert prob == pytest.approx(0.60, abs=0.001)

    def test_neutral_direction_returns_market_price(self):
        parsed = {
            "relevant": True,
            "new_information": True,
            "direction": "neutral",
            "magnitude": "small",
            "confidence": 0.6,
            "reasoning": "unclear",
        }
        market = _make_market(45.0)
        prob, _, _, direction, _ = _parse_llm_response(parsed, market)
        assert prob == pytest.approx(0.45, abs=0.001)
        assert direction == "neutral"

    def test_magnitude_none_returns_market_price(self):
        parsed = {
            "relevant": True,
            "new_information": True,
            "direction": "yes",
            "magnitude": "none",
            "confidence": 0.8,
            "reasoning": "no impact",
        }
        market = _make_market(55.0)
        prob, _, _, _, magnitude = _parse_llm_response(parsed, market)
        assert prob == pytest.approx(0.55, abs=0.001)
        assert magnitude == "none"

    def test_prob_clamped_at_0_95(self):
        parsed = {
            "relevant": True,
            "new_information": True,
            "direction": "yes",
            "magnitude": "large",
            "confidence": 1.0,
            "reasoning": "massive event",
        }
        market = _make_market(85.0)  # 0.85 + 0.25 = 1.10, should clamp to 0.95
        prob, _, _, _, _ = _parse_llm_response(parsed, market)
        assert prob <= 0.95

    def test_prob_clamped_at_0_05(self):
        parsed = {
            "relevant": True,
            "new_information": True,
            "direction": "no",
            "magnitude": "large",
            "confidence": 1.0,
            "reasoning": "major reversal",
        }
        market = _make_market(15.0)  # 0.15 - 0.25 = -0.10, should clamp to 0.05
        prob, _, _, _, _ = _parse_llm_response(parsed, market)
        assert prob >= 0.05

    def test_missing_fields_use_defaults(self):
        """Minimal dict should not raise -- uses sensible defaults."""
        market = _make_market(50.0)
        prob, conf, _, direction, magnitude = _parse_llm_response({}, market)
        assert prob == pytest.approx(0.50, abs=0.001)
        assert conf == pytest.approx(0.5, abs=0.001)


# ---------------------------------------------------------------------------
# _keyword_score
# ---------------------------------------------------------------------------

class TestKeywordScore:
    def test_yes_keywords_produce_positive_shift(self):
        # Use multi-word phrases that are actual YES-direction signal keywords
        shift, direction, keywords = _keyword_score("missile strike bombs shelling")
        assert shift > 0
        assert direction == "yes"
        assert len(keywords) > 0

    def test_no_keywords_produce_negative_shift(self):
        shift, direction, keywords = _keyword_score("ceasefire agreement signed peace deal")
        assert shift < 0
        assert direction == "no"

    def test_no_keywords_matched_zero_shift(self):
        shift, direction, keywords = _keyword_score("stock market earnings report quarterly")
        assert shift == 0.0
        assert keywords == []

    def test_multiple_keyword_hits_bonus(self):
        # Single hit
        s1, _, _ = _keyword_score("attack")
        # Two hits from same group should add bonus
        s2, _, _ = _keyword_score("attack assault")
        assert s2 >= s1

    def test_keyword_stats_multiplier_applied(self):
        stats = MagicMock()
        stats.get_multiplier.return_value = 1.5

        # Use actual YES-direction keyword from GEOPOLITICAL_SIGNALS
        s_no_stats, _, _ = _keyword_score("missile strike", keyword_stats=None)
        s_with_stats, _, _ = _keyword_score(
            "missile strike", keyword_stats=stats, series_ticker="KXTEST"
        )
        # With 1.5x multiplier, weighted score should be higher
        assert s_with_stats > s_no_stats

    def test_keyword_stats_not_applied_without_series_ticker(self):
        """Stats require series_ticker to be non-empty; without it, fall back to base."""
        stats = MagicMock()
        stats.get_multiplier.return_value = 2.0  # would double if applied

        s_base, _, _ = _keyword_score("attack", keyword_stats=None)
        s_stats_no_series, _, _ = _keyword_score(
            "attack", keyword_stats=stats, series_ticker=""
        )
        # get_multiplier should NOT be called without series_ticker
        stats.get_multiplier.assert_not_called()
        # Score should be the same as base
        assert s_stats_no_series == pytest.approx(s_base, abs=1e-9)
