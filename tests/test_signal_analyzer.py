"""
Tests for analysis/signal_analyzer.py

Covers: JSON extraction edge cases, probability mapping, keyword scoring,
        geo-coherence suppression, and estimator fallback behaviour.
"""

import asyncio
import dataclasses
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import analysis.signal_analyzer as signal_analyzer
from analysis.signal_analyzer import (
    _OLLAMA_FAILURE_THRESHOLD,
    _OLLAMA_PROBE_INTERVAL,
    _build_llm_meta_kwargs,
    _extract_json,
    _llm_meta,
    _ollama_build_payload,
    _ollama_check_circuit,
    _ollama_estimate_detailed,
    _ollama_extract_and_validate,
    _ollama_post,
    _ollama_record_failure,
    _parse_llm_response,
    _keyword_score,
    estimate_probability,
    keyword_estimate,
)


def _detail_to_kwargs(detail_mock):
    """Convert a SignalAnalysisDetail-form mock call back to a kwargs-style
    dict for assertion compatibility with tests written against the prior
    46-kwarg signature. Includes only fields whose value is not None — matches
    the prior semantic that absent optional kwargs were not present in `kwargs`.
    """
    detail = detail_mock.call_args.args[0]
    return {
        f.name: getattr(detail, f.name)
        for f in dataclasses.fields(detail)
        if getattr(detail, f.name) is not None
    }


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

    def test_magnitude_none_with_low_confidence_returns_market_price(self):
        """Low-confidence directional + magnitude=none stays as the LLM
        emitted it: prob = market.yes_prob unchanged, magnitude = "none".

        PROFIT-LLM-002 (2026-05-24): the magnitude-bump for the converse
        consistency rule fires only when confidence >= 0.6. Below that
        threshold, the LLM's "none" is preserved to avoid converting
        low-conviction directionals into spurious shifts.
        """
        parsed = {
            "relevant": True,
            "new_information": True,
            "direction": "yes",
            "magnitude": "none",
            "confidence": 0.55,  # below 0.6 floor
            "reasoning": "weak signal",
        }
        market = _make_market(55.0)
        prob, _, _, _, magnitude = _parse_llm_response(parsed, market)
        assert prob == pytest.approx(0.55, abs=0.001)
        assert magnitude == "none"

    def test_magnitude_none_with_high_confidence_bumped_to_small(self):
        """PROFIT-LLM-002 (2026-05-24): when the LLM returns
        direction in {yes, no} with confidence >= 0.6 BUT
        magnitude="none", reconcile the internally-inconsistent output
        by bumping magnitude to "small". This restores the minimum-
        credible shift (8 pp * confidence) when the LLM is confident
        enough in a direction.

        Load-bearing failure mode: the 2026-05-24 7-day funnel found
        10 LLM responses on KXTXRUNOFFENDORSE markets with
        direction="no", confidence=0.95, magnitude="none" — the
        Paxton-over-Cornyn endorsement was the resolution-grade news
        for the JCOR outcome contracts, the LLM read it correctly,
        but magnitude="none" silently zeroed out the shift and no
        trade emitted. Without this test, a future revert of the
        magnitude bump would silently reintroduce that miss pattern.
        """
        parsed = {
            "relevant": True,
            "new_information": True,
            "direction": "no",
            "magnitude": "none",
            "confidence": 0.95,
            "reasoning": "Trump endorsed Paxton, not Cornyn",
        }
        market = _make_market(55.0)
        prob, _, _, direction, magnitude = _parse_llm_response(parsed, market)
        # Bumped to "small" → shift = 0.08 * 0.95 = 0.076 → prob = 0.55 - 0.076 = 0.474
        assert prob == pytest.approx(0.474, abs=0.001), (
            "expected magnitude bump from 'none' to 'small' for "
            "direction=no, confidence=0.95"
        )
        assert direction == "no"
        assert magnitude == "small", (
            "expected magnitude bumped from 'none' to 'small'. If this "
            "fails, PROFIT-LLM-002 magnitude-bump has regressed."
        )

    def test_magnitude_none_with_neutral_direction_not_bumped(self):
        """The magnitude bump fires ONLY when direction in {yes, no}.
        With direction='neutral' (LLM signaled no actionable view),
        the bump must NOT fire — magnitude stays "none" and prob is
        unchanged. This prevents the bump from inadvertently turning
        true-neutral responses into directional bets."""
        parsed = {
            "relevant": True,
            "new_information": True,
            "direction": "neutral",
            "magnitude": "none",
            "confidence": 0.95,
            "reasoning": "no clear direction",
        }
        market = _make_market(55.0)
        prob, _, _, direction, magnitude = _parse_llm_response(parsed, market)
        assert prob == pytest.approx(0.55, abs=0.001)
        assert direction == "neutral"
        assert magnitude == "none", (
            "neutral direction must NEVER trigger the magnitude bump"
        )

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


from tests._helpers import make_news as _make_news  # noqa: E402


def _make_full_market(
    *,
    title="Will Iran attack U.S. forces in 2026?",
    subtitle="Resolves YES if Iran attacks U.S. forces before Dec 31, 2026.",
    yes_price=50.0,
    series_ticker="KXIRAN",
):
    market = MagicMock()
    market.ticker = "KXTEST-25DEC31"
    market.title = title
    market.subtitle = subtitle
    market.yes_price = yes_price
    market.yes_prob = yes_price / 100.0
    market.series_ticker = series_ticker
    market.close_time = "2026-12-31T23:59:59Z"
    return market


class TestKeywordEstimate:
    def test_geo_coherence_suppresses_cross_country_signal(self):
        news = _make_news("Iran missile strike hits regional target")
        market = _make_full_market(
            title="Will Russia invade Ukraine in 2026?",
            subtitle="Resolves YES if Russia invades Ukraine before Dec 31, 2026.",
            series_ticker="KXUKR",
        )

        prob, side, keywords, reasoning = keyword_estimate(news, market)
        assert prob == pytest.approx(market.yes_prob, abs=1e-9)
        assert side == 0.05
        assert keywords
        assert "Geo-entity mismatch" in reasoning

    @pytest.mark.parametrize(
        ("headline", "expected_direction"),
        [
            ("Missile strike prompts fears of wider conflict", "yes"),
            ("Ceasefire agreement signed after peace deal", "no"),
        ],
    )
    def test_keyword_estimate_produces_directional_signal(self, headline, expected_direction):
        news = _make_news(headline)
        market = _make_full_market()

        prob, side, keywords, reasoning = keyword_estimate(news, market)
        assert keywords
        assert side == expected_direction
        assert "Keyword analysis found" in reasoning

    def test_keyword_estimate_returns_no_signal_when_no_keywords(self):
        news = _make_news("Quarterly corporate earnings beat expectations")
        market = _make_full_market()

        prob, side, keywords, reasoning = keyword_estimate(news, market)
        assert prob == pytest.approx(market.yes_prob, abs=1e-9)
        assert side == "no"
        assert keywords == []


class TestEstimateProbability:
    @pytest.fixture(autouse=True)
    def _clear_llm_dedup_cache(self):
        """PROFIT-ALIGN-010 (2026-05-25): the runtime LLM dedup cache is a
        module-level OrderedDict. Between tests in this class, identical
        (headline, market_title, market_price_bucket) inputs would otherwise
        hit cached results from previous tests — breaking the isolation
        contract that each test mocks its own LLM behavior."""
        from analysis.llm_dedup_cache import clear
        clear()
        yield
        clear()

    @pytest.mark.asyncio
    async def test_no_keyword_headline_can_use_llm_when_available(self, monkeypatch):
        news = _make_news("Quarterly corporate earnings beat expectations")
        market = _make_full_market()

        async def _fake_llm(*args, **kwargs):
            return (
                (0.64, 0.85, "LLM found relevant directional information", "yes", "moderate"),
                {"attempted": True, "status": "ollama_success", "provider": "ollama", "result_used": True},
            )

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _fake_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            result = await estimate_probability(news, market)

        assert result == (
            0.64,
            0.85,
            [],
            f"[LLM] LLM found relevant directional information (LLM: {0.64:.3f}, Keywords(ref): {market.yes_prob:.3f})",
            "yes",
            "moderate",
            0.85,
        )
        detail_mock.assert_called_once()
        detail = detail_mock.call_args.args[0]
        assert detail.ticker == market.ticker
        assert detail.source == news.source
        assert detail.headline == news.headline
        assert detail.method == "llm"
        assert detail.keywords == []
        assert detail.keyword_contributions == []
        assert detail.base_probability == market.yes_prob
        assert detail.final_probability == 0.64
        assert detail.market_price == market.yes_prob
        assert detail.llm_direction == "yes"
        assert detail.llm_magnitude == "moderate"
        assert detail.llm_confidence == 0.85
        assert detail.llm_attempted is True
        assert detail.llm_result_used is True
        assert detail.llm_result_status == "ollama_success"
        assert detail.llm_provider == "ollama"
        assert detail.llm_latency_ms is None
        assert detail.llm_total_stage_ms is None
        assert detail.llm_queue_wait_ms is None
        assert detail.llm_http_round_trip_ms is None
        assert detail.llm_parse_ms is None
        assert detail.llm_http_status is None
        assert detail.llm_contention_observed is None
        assert detail.llm_in_flight_at_entry is None
        assert detail.llm_routing_passed is True
        assert detail.llm_probability_movement == pytest.approx(0.14)
        assert detail.llm_useful is True
        assert detail.pre_llm_would_block_and_useful is False

    @pytest.mark.asyncio
    async def test_non_probe_llm_call_logs_prompt_and_raw_response_at_debug(self, monkeypatch, caplog):
        news = _make_news("Quarterly corporate earnings beat expectations")
        market = _make_full_market()

        async def _fake_llm(*args, **kwargs):
            return (
                (0.64, 0.85, "LLM found relevant directional information", "yes", "moderate"),
                {
                    "attempted": True,
                    "status": "ollama_success",
                    "provider": "ollama",
                    "result_used": True,
                    "prompt": "SYSTEM:\nPrompt\n\nUSER:\nQuestion",
                    "raw_response": '{"direction":"yes","magnitude":"moderate"}',
                },
            )

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _fake_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail"), \
             caplog.at_level("DEBUG", logger="signal_analyzer"):
            await estimate_probability(news, market)

        records = [
            json.loads(record.getMessage())
            for record in caplog.records
            if record.getMessage().startswith("{")
        ]
        prompt_records = [record for record in records if record.get("type") == "LLM_PROMPT_RESPONSE"]

        assert len(prompt_records) == 1
        assert prompt_records[0]["market_ticker"] == market.ticker
        assert prompt_records[0]["provider"] == "ollama"
        assert prompt_records[0]["status"] == "ollama_success"
        assert prompt_records[0]["prompt"] == "SYSTEM:\nPrompt\n\nUSER:\nQuestion"
        assert prompt_records[0]["raw_response"] == '{"direction":"yes","magnitude":"moderate"}'

    @pytest.mark.asyncio
    async def test_startup_probe_does_not_log_prompt_or_raw_response(self, monkeypatch, caplog):
        news = _make_news("Startup probe")
        market = _make_full_market()

        async def _fake_llm(*args, **kwargs):
            return (
                (0.64, 0.85, "Synthetic startup probe result", "yes", "moderate"),
                {
                    "attempted": True,
                    "status": "startup_probe_success",
                    "provider": "startup_probe",
                    "result_used": True,
                    "prompt": "probe prompt",
                    "raw_response": "probe response",
                },
            )

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _fake_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail"), \
             caplog.at_level("DEBUG", logger="signal_analyzer"):
            await estimate_probability(news, market, is_startup_probe=True)

        assert "LLM_PROMPT_RESPONSE" not in caplog.text

    @pytest.mark.asyncio
    async def test_no_keyword_headline_uses_keyword_gate_when_llm_unavailable(self, monkeypatch):
        news = _make_news("Quarterly corporate earnings beat expectations")
        market = _make_full_market()

        async def _no_llm(*args, **kwargs):
            return (
                None,
                {"attempted": True, "status": "ollama_timeout", "provider": "ollama", "result_used": False},
            )

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _no_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            result = await estimate_probability(news, market)

        assert result == (
            market.yes_prob,
            0.1,
            [],
            "No relevant keywords found -- no signal.",
            None,
            None,
            None,
        )
        detail_mock.assert_called_once()
        detail = detail_mock.call_args.args[0]
        assert detail.ticker == market.ticker
        assert detail.source == news.source
        assert detail.headline == news.headline
        assert detail.method == "keyword_gate"
        assert detail.keywords == []
        assert detail.keyword_contributions == []
        assert detail.base_probability == market.yes_prob
        assert detail.final_probability == market.yes_prob
        assert detail.market_price == market.yes_prob
        assert detail.llm_attempted is True
        assert detail.llm_result_used is False
        assert detail.llm_result_status == "ollama_timeout"
        assert detail.llm_provider == "ollama"
        assert detail.llm_latency_ms is None
        assert detail.llm_total_stage_ms is None
        assert detail.llm_queue_wait_ms is None
        assert detail.llm_http_round_trip_ms is None
        assert detail.llm_parse_ms is None
        assert detail.llm_http_status is None
        assert detail.llm_contention_observed is None
        assert detail.llm_in_flight_at_entry is None
        assert detail.llm_routing_passed is True
        assert detail.llm_routing_reason is None

    @pytest.mark.asyncio
    async def test_llm_result_takes_precedence_when_available(self, monkeypatch):
        news = _make_news("Missile strike prompts fears of wider conflict")
        market = _make_full_market()

        async def _fake_llm(*args, **kwargs):
            return (
                (0.72, 0.9, "LLM says this is market-moving", "yes", "moderate"),
                {"attempted": True, "status": "anthropic_success", "provider": "anthropic", "result_used": True},
            )

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _fake_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            prob, confidence, keywords, reasoning, llm_dir, llm_mag, llm_conf = \
                await estimate_probability(news, market)

        assert prob == pytest.approx(0.72)
        assert confidence == pytest.approx(0.9)
        assert keywords
        assert reasoning.startswith("[LLM]")
        assert llm_dir == "yes"
        assert llm_mag == "moderate"
        assert llm_conf == pytest.approx(0.9)
        detail_mock.assert_called_once()
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["method"] == "llm"
        assert kwargs["base_probability"] == pytest.approx(market.yes_prob)
        assert kwargs["final_probability"] == pytest.approx(0.72)
        assert kwargs["market_price"] == pytest.approx(market.yes_prob)
        assert kwargs["llm_direction"] == "yes"
        assert kwargs["llm_magnitude"] == "moderate"
        assert kwargs["llm_confidence"] == pytest.approx(0.9)
        assert kwargs["llm_attempted"] is True
        assert kwargs["llm_result_used"] is True
        assert kwargs["llm_result_status"] == "anthropic_success"
        assert kwargs["llm_provider"] == "anthropic"
        assert kwargs["llm_routing_passed"] is True
        assert "llm_routing_reason" not in kwargs
        assert kwargs["keywords"]
        assert kwargs["keyword_contributions"]

    @pytest.mark.asyncio
    async def test_keyword_fallback_used_when_llm_unavailable(self, monkeypatch):
        news = _make_news("Missile strike prompts fears of wider conflict")
        market = _make_full_market()

        async def _no_llm(*args, **kwargs):
            return (
                None,
                {"attempted": False, "status": "no_provider_available", "provider": None, "result_used": False},
            )

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _no_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            prob, confidence, keywords, reasoning, llm_dir, llm_mag, llm_conf = \
                await estimate_probability(news, market)

        assert keywords
        assert prob != pytest.approx(market.yes_prob)
        assert confidence > 0.3
        assert reasoning.startswith("Keyword analysis found")
        assert llm_dir is None
        assert llm_mag is None
        assert llm_conf is None
        detail_mock.assert_called_once()
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["method"] == "keyword"
        assert kwargs["base_probability"] == pytest.approx(market.yes_prob)
        assert kwargs["final_probability"] == pytest.approx(prob)
        assert kwargs["market_price"] == pytest.approx(market.yes_prob)
        assert kwargs["llm_attempted"] is False
        assert kwargs["llm_result_used"] is False
        assert kwargs["llm_result_status"] == "no_provider_available"
        assert kwargs.get("llm_provider") is None
        assert kwargs["llm_routing_passed"] is True
        assert kwargs.get("llm_routing_reason") is None
        assert kwargs["keywords"]
        assert kwargs["keyword_contributions"]

    @pytest.mark.asyncio
    async def test_routing_filter_skips_llm_and_logs_keyword_fallback(self, monkeypatch):
        news = _make_news("Missile strike prompts fears of wider conflict")
        market = _make_full_market(yes_price=50.0)
        monkeypatch.setattr(signal_analyzer.cfg, "enable_llm_routing_filter", True)
        monkeypatch.setattr(signal_analyzer.cfg, "llm_allowed_price_bands", [(0.0, 0.35), (0.65, 1.0)])
        monkeypatch.setattr(signal_analyzer.cfg, "llm_excluded_price_bands", [])

        async def _should_not_run(*args, **kwargs):
            raise AssertionError("llm_estimate_detailed should not run when routing excludes the price band")

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _should_not_run)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock, \
             patch("analysis.signal_analyzer.trade_log.log_llm_skipped_routing") as skip_mock:
            prob, confidence, keywords, reasoning, llm_dir, llm_mag, llm_conf = await estimate_probability(news, market)

        assert keywords
        assert prob != pytest.approx(market.yes_prob)
        assert confidence > 0.3
        assert reasoning.startswith("Keyword analysis found")
        assert llm_dir is None and llm_mag is None and llm_conf is None
        skip_mock.assert_called_once_with(
            ticker=market.ticker,
            source=news.source,
            headline=news.headline,
            reason="price_band_excluded",
            market_price=market.yes_prob,
        )
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["method"] == "keyword"
        assert kwargs["llm_attempted"] is False
        assert kwargs["llm_result_used"] is False
        assert kwargs["llm_result_status"] == "llm_skipped_routing_price_band_excluded"
        assert kwargs.get("llm_provider") is None
        assert kwargs["llm_routing_passed"] is False
        assert kwargs["llm_routing_reason"] == "price_band_excluded"

    @pytest.mark.asyncio
    async def test_routing_filter_disabled_keeps_llm_path_unchanged(self, monkeypatch):
        news = _make_news("Quarterly corporate earnings beat expectations")
        market = _make_full_market(yes_price=50.0)
        monkeypatch.setattr(signal_analyzer.cfg, "enable_llm_routing_filter", False)

        async def _fake_llm(*args, **kwargs):
            return (
                (0.64, 0.85, "LLM found relevant directional information", "yes", "moderate"),
                {"attempted": True, "status": "ollama_success", "provider": "ollama", "result_used": True},
            )

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _fake_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock, \
             patch("analysis.signal_analyzer.trade_log.log_llm_skipped_routing") as skip_mock:
            result = await estimate_probability(news, market)

        assert result[0] == pytest.approx(0.64)
        skip_mock.assert_not_called()
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["method"] == "llm"
        assert kwargs["llm_routing_passed"] is True
        assert "llm_routing_reason" not in kwargs

    @pytest.mark.asyncio
    async def test_match_meta_logs_would_block_without_keywords(self, monkeypatch):
        news = _make_news("Quarterly corporate earnings beat expectations")
        market = _make_full_market()
        match_meta = {
            "pre_llm_quality_pass": False,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 0.2,
            "pre_llm_gate_reason": "weak_semantic_overlap",
        }

        async def _fake_llm(*args, **kwargs):
            return (
                (0.64, 0.85, "LLM found relevant directional information", "yes", "moderate"),
                {"attempted": True, "status": "ollama_success", "provider": "ollama", "result_used": True},
            )

        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_mode", "any_hit")
        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _fake_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            await estimate_probability(news, market, match_meta=match_meta)

        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["pre_llm_quality_pass"] is False
        assert kwargs["pre_llm_semantic_overlap_count"] == 1
        assert kwargs["pre_llm_semantic_overlap_ratio"] == pytest.approx(0.2)
        assert kwargs["pre_llm_would_block"] is True
        assert kwargs["pre_llm_keyword_override"] is False
        assert kwargs["pre_llm_keyword_override_mode"] == "any_hit"
        assert kwargs["pre_llm_keyword_signal_strength"] == pytest.approx(0.0)
        assert kwargs["pre_llm_gate_reason"] == "weak_semantic_overlap"
        assert kwargs["method"] == "llm"

    @pytest.mark.asyncio
    async def test_pre_llm_gate_suppresses_weak_match_no_keyword_when_enabled(self, monkeypatch):
        news = _make_news("Quarterly corporate earnings beat expectations")
        market = _make_full_market()
        match_meta = {
            "pre_llm_quality_pass": False,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 0.2,
            "pre_llm_gate_reason": "weak_semantic_overlap",
        }

        async def _should_not_run(*args, **kwargs):
            raise AssertionError("llm_estimate_detailed should not run when pre-LLM gate is enforced")

        monkeypatch.setattr(signal_analyzer.cfg, "enable_pre_llm_match_gate", True)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_diagnostics_only", False)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_mode", "disabled")
        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _should_not_run)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            result = await estimate_probability(news, market, match_meta=match_meta)

        assert result == (
            market.yes_prob,
            0.1,
            [],
            "No relevant keywords found -- no signal.",
            None,
            None,
            None,
        )
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["method"] == "keyword_gate"
        assert kwargs["llm_attempted"] is False
        assert kwargs["llm_result_used"] is False
        assert kwargs["llm_result_status"] == "llm_skipped_match_quality_gate"
        assert kwargs["pre_llm_would_block"] is True
        assert kwargs["pre_llm_keyword_override"] is False
        assert kwargs["pre_llm_keyword_override_mode"] == "disabled"
        assert kwargs["pre_llm_gate_enforced"] is True

    @pytest.mark.asyncio
    async def test_match_meta_logs_keyword_override_when_keywords_present(self, monkeypatch):
        news = _make_news("Ceasefire agreement signed after peace deal")
        market = _make_full_market()
        match_meta = {
            "pre_llm_quality_pass": False,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 0.2,
            "pre_llm_gate_reason": "weak_semantic_overlap",
        }

        async def _no_llm(*args, **kwargs):
            return (
                None,
                {"attempted": False, "status": "no_provider_available", "provider": None, "result_used": False},
            )

        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_mode", "any_hit")
        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _no_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            await estimate_probability(news, market, match_meta=match_meta)

        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["keywords"]
        assert kwargs["pre_llm_quality_pass"] is False
        assert kwargs["pre_llm_would_block"] is False
        assert kwargs["pre_llm_keyword_override"] is True
        assert kwargs["pre_llm_keyword_override_mode"] == "any_hit"
        assert kwargs["pre_llm_gate_reason"] == "weak_semantic_overlap"
        assert kwargs["method"] == "keyword"

    @pytest.mark.asyncio
    async def test_pre_llm_gate_allows_llm_when_keyword_override_enabled(self, monkeypatch):
        news = _make_news("Ceasefire agreement signed after peace deal")
        market = _make_full_market()
        match_meta = {
            "pre_llm_quality_pass": False,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 0.2,
            "pre_llm_gate_reason": "weak_semantic_overlap",
        }

        async def _fake_llm(*args, **kwargs):
            return (
                (0.64, 0.85, "LLM found relevant directional information", "yes", "moderate"),
                {"attempted": True, "status": "ollama_success", "provider": "ollama", "result_used": True},
            )

        monkeypatch.setattr(signal_analyzer.cfg, "enable_pre_llm_match_gate", True)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_diagnostics_only", False)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_mode", "any_hit")
        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _fake_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            result = await estimate_probability(news, market, match_meta=match_meta)

        assert result[0] == pytest.approx(0.64)
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["method"] == "llm"
        assert kwargs["pre_llm_keyword_override"] is True
        assert kwargs["pre_llm_would_block"] is False
        assert "pre_llm_gate_enforced" not in kwargs

    @pytest.mark.asyncio
    async def test_pre_llm_gate_min_signal_override_requires_threshold(self, monkeypatch):
        news = _make_news("Peace deal update")
        market = _make_full_market()
        match_meta = {
            "pre_llm_quality_pass": False,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 0.2,
            "pre_llm_gate_reason": "insufficient_semantic_overlap",
        }

        monkeypatch.setattr(signal_analyzer.cfg, "enable_pre_llm_match_gate", True)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_diagnostics_only", False)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_mode", "min_signal")
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_min_signal", 0.15)
        monkeypatch.setattr(
            "analysis.signal_analyzer.keyword_estimate",
            lambda *args, **kwargs: (0.60, "yes", ["peace deal"], "keyword"),
        )
        monkeypatch.setattr(
            "analysis.signal_analyzer._keyword_contributions",
            lambda *args, **kwargs: [{"keyword": "peace deal", "direction": "yes", "weight": 0.1}],
        )

        async def _should_not_run(*args, **kwargs):
            raise AssertionError("llm_estimate_detailed should not run below min-signal override threshold")

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _should_not_run)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            result = await estimate_probability(news, market, match_meta=match_meta)

        assert result[0] == pytest.approx(0.60)
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["method"] == "keyword"
        assert kwargs["pre_llm_keyword_override"] is False
        assert kwargs["pre_llm_keyword_override_mode"] == "min_signal"
        assert kwargs["pre_llm_keyword_signal_strength"] == pytest.approx(0.1)
        assert kwargs["pre_llm_gate_enforced"] is True

    @pytest.mark.asyncio
    async def test_pre_llm_gate_min_signal_override_allows_llm_when_threshold_met(self, monkeypatch):
        news = _make_news("Peace deal update")
        market = _make_full_market()
        match_meta = {
            "pre_llm_quality_pass": False,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 0.2,
            "pre_llm_gate_reason": "insufficient_semantic_overlap",
        }

        monkeypatch.setattr(signal_analyzer.cfg, "enable_pre_llm_match_gate", True)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_diagnostics_only", False)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_mode", "min_signal")
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_min_signal", 0.05)
        monkeypatch.setattr(
            "analysis.signal_analyzer.keyword_estimate",
            lambda *args, **kwargs: (0.60, "yes", ["peace deal"], "keyword"),
        )
        monkeypatch.setattr(
            "analysis.signal_analyzer._keyword_contributions",
            lambda *args, **kwargs: [{"keyword": "peace deal", "direction": "yes", "weight": 0.1}],
        )

        async def _fake_llm(*args, **kwargs):
            return (
                (0.64, 0.85, "LLM found relevant directional information", "yes", "moderate"),
                {"attempted": True, "status": "ollama_success", "provider": "ollama", "result_used": True},
            )

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _fake_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            result = await estimate_probability(news, market, match_meta=match_meta)

        assert result[0] == pytest.approx(0.64)
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["pre_llm_keyword_override"] is True
        assert kwargs["pre_llm_keyword_override_mode"] == "min_signal"
        assert kwargs["pre_llm_keyword_signal_strength"] == pytest.approx(0.1)
        assert "pre_llm_gate_enforced" not in kwargs

    @pytest.mark.asyncio
    async def test_pre_llm_gate_disabled_override_mode_never_overrides(self, monkeypatch):
        news = _make_news("Peace deal update")
        market = _make_full_market()
        match_meta = {
            "pre_llm_quality_pass": False,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 0.2,
            "pre_llm_gate_reason": "insufficient_semantic_overlap",
        }

        monkeypatch.setattr(signal_analyzer.cfg, "enable_pre_llm_match_gate", True)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_diagnostics_only", False)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_mode", "disabled")
        monkeypatch.setattr(
            "analysis.signal_analyzer.keyword_estimate",
            lambda *args, **kwargs: (0.75, "yes", ["peace deal"], "keyword"),
        )
        monkeypatch.setattr(
            "analysis.signal_analyzer._keyword_contributions",
            lambda *args, **kwargs: [{"keyword": "peace deal", "direction": "yes", "weight": 0.25}],
        )

        async def _should_not_run(*args, **kwargs):
            raise AssertionError("llm_estimate_detailed should not run when override mode is disabled")

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _should_not_run)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            result = await estimate_probability(news, market, match_meta=match_meta)

        assert result[0] == pytest.approx(0.75)
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["method"] == "keyword"
        assert kwargs["pre_llm_keyword_override"] is False
        assert kwargs["pre_llm_keyword_override_mode"] == "disabled"
        assert kwargs["pre_llm_gate_enforced"] is True

    @pytest.mark.asyncio
    async def test_pre_llm_gate_all_required_blocks_when_not_every_group_hit(self, monkeypatch):
        news = _make_news("Trump comments on Iran talks")
        market = _make_full_market()
        match_meta = {
            "pre_llm_quality_pass": False,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 0.1,
            "pre_llm_gate_reason": "insufficient_semantic_overlap",
        }

        # Two-group fixture: text hits only the first group, so all_required should block.
        fake_signals = [
            {"keywords": ["trump"], "direction": "yes", "strength": 0.1},
            {"keywords": ["ceasefire"], "direction": "no", "strength": 0.1},
        ]
        monkeypatch.setattr(signal_analyzer, "GEOPOLITICAL_SIGNALS", fake_signals)
        monkeypatch.setattr(signal_analyzer.cfg, "enable_pre_llm_match_gate", True)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_diagnostics_only", False)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_mode", "all_required")
        monkeypatch.setattr(
            "analysis.signal_analyzer.keyword_estimate",
            lambda *args, **kwargs: (0.55, "yes", ["trump"], "keyword"),
        )
        monkeypatch.setattr(
            "analysis.signal_analyzer._keyword_contributions",
            lambda *args, **kwargs: [{"keyword": "trump", "direction": "yes", "weight": 0.05}],
        )

        async def _should_not_run(*args, **kwargs):
            raise AssertionError("llm_estimate_detailed should not run when all_required is not satisfied")

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _should_not_run)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            result = await estimate_probability(news, market, match_meta=match_meta)

        assert result[0] == pytest.approx(0.55)
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["method"] == "keyword"
        assert kwargs["pre_llm_keyword_override"] is False
        assert kwargs["pre_llm_keyword_override_mode"] == "all_required"
        assert kwargs["pre_llm_gate_enforced"] is True

    @pytest.mark.asyncio
    async def test_pre_llm_gate_all_required_allows_when_every_group_hit(self, monkeypatch):
        news = _make_news("Trump ceasefire announced")
        market = _make_full_market()
        match_meta = {
            "pre_llm_quality_pass": False,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 0.2,
            "pre_llm_gate_reason": "insufficient_semantic_overlap",
        }

        # Two-group fixture: text hits both groups, so all_required should allow.
        fake_signals = [
            {"keywords": ["trump"], "direction": "yes", "strength": 0.1},
            {"keywords": ["ceasefire"], "direction": "no", "strength": 0.1},
        ]
        monkeypatch.setattr(signal_analyzer, "GEOPOLITICAL_SIGNALS", fake_signals)
        monkeypatch.setattr(signal_analyzer.cfg, "enable_pre_llm_match_gate", True)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_diagnostics_only", False)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_mode", "all_required")
        monkeypatch.setattr(
            "analysis.signal_analyzer.keyword_estimate",
            lambda *args, **kwargs: (0.60, "yes", ["trump", "ceasefire"], "keyword"),
        )
        monkeypatch.setattr(
            "analysis.signal_analyzer._keyword_contributions",
            lambda *args, **kwargs: [
                {"keyword": "trump", "direction": "yes", "weight": 0.05},
                {"keyword": "ceasefire", "direction": "no", "weight": 0.05},
            ],
        )

        async def _fake_llm(*args, **kwargs):
            return (
                (0.64, 0.85, "LLM ran because override fired", "yes", "moderate"),
                {
                    "attempted": True,
                    "status": "ok",
                    "provider": "ollama",
                    "result_used": True,
                },
            )

        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _fake_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            result = await estimate_probability(news, market, match_meta=match_meta)

        assert result[0] == pytest.approx(0.64)
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["pre_llm_keyword_override"] is True
        assert kwargs["pre_llm_keyword_override_mode"] == "all_required"

    def test_count_matched_signal_groups_returns_zero_when_no_keyword_present(self):
        assert signal_analyzer._count_matched_signal_groups("nothing interesting here") == 0

    def test_count_matched_signal_groups_counts_distinct_groups(self, monkeypatch):
        fake_signals = [
            {"keywords": ["alpha", "alpha-plus"], "direction": "yes", "strength": 0.1},
            {"keywords": ["beta"], "direction": "no", "strength": 0.1},
            {"keywords": ["gamma"], "direction": "yes", "strength": 0.1},
        ]
        monkeypatch.setattr(signal_analyzer, "GEOPOLITICAL_SIGNALS", fake_signals)
        # text hits the first group twice (should count group only once) and the second group once
        assert signal_analyzer._count_matched_signal_groups("alpha alpha-plus and beta") == 2
        # text hits every group
        assert signal_analyzer._count_matched_signal_groups("alpha beta gamma") == 3

    @pytest.mark.asyncio
    async def test_pre_llm_gate_disabled_does_not_suppress(self, monkeypatch):
        news = _make_news("Quarterly corporate earnings beat expectations")
        market = _make_full_market()
        match_meta = {
            "pre_llm_quality_pass": False,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 0.2,
            "pre_llm_gate_reason": "weak_semantic_overlap",
        }

        async def _fake_llm(*args, **kwargs):
            return (
                (0.64, 0.85, "LLM found relevant directional information", "yes", "moderate"),
                {"attempted": True, "status": "ollama_success", "provider": "ollama", "result_used": True},
            )

        monkeypatch.setattr(signal_analyzer.cfg, "enable_pre_llm_match_gate", False)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_diagnostics_only", False)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_mode", "disabled")
        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _fake_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            result = await estimate_probability(news, market, match_meta=match_meta)

        assert result[0] == pytest.approx(0.64)
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["method"] == "llm"
        assert kwargs["pre_llm_would_block"] is True
        assert "pre_llm_gate_enforced" not in kwargs

    @pytest.mark.asyncio
    async def test_pre_llm_gate_diagnostics_only_does_not_suppress(self, monkeypatch):
        news = _make_news("Quarterly corporate earnings beat expectations")
        market = _make_full_market()
        match_meta = {
            "pre_llm_quality_pass": False,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 0.2,
            "pre_llm_gate_reason": "weak_semantic_overlap",
        }

        async def _fake_llm(*args, **kwargs):
            return (
                (0.64, 0.85, "LLM found relevant directional information", "yes", "moderate"),
                {"attempted": True, "status": "ollama_success", "provider": "ollama", "result_used": True},
            )

        monkeypatch.setattr(signal_analyzer.cfg, "enable_pre_llm_match_gate", True)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_diagnostics_only", True)
        monkeypatch.setattr(signal_analyzer.cfg, "pre_llm_match_gate_keyword_override_mode", "disabled")
        monkeypatch.setattr("analysis.signal_analyzer.llm_estimate_detailed", _fake_llm)
        with patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail") as detail_mock:
            result = await estimate_probability(news, market, match_meta=match_meta)

        assert result[0] == pytest.approx(0.64)
        kwargs = _detail_to_kwargs(detail_mock)
        assert kwargs["method"] == "llm"
        assert kwargs["pre_llm_would_block"] is True
        assert "pre_llm_gate_enforced" not in kwargs


class _FakeResponse:
    def __init__(self, *, status=200, text="", exc_on_enter=None):
        self.status = status
        self._text = text
        self._exc_on_enter = exc_on_enter

    async def __aenter__(self):
        if self._exc_on_enter is not None:
            raise self._exc_on_enter
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text


class _FakeClientSession:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        return self._response

    def get(self, *args, **kwargs):
        return self._response


class _FakeTimeout:
    def __init__(self, total):
        self.total = total


def _fake_aiohttp_module(response):
    class _ClientConnectorError(Exception):
        pass

    return SimpleNamespace(
        ClientSession=lambda: _FakeClientSession(response),
        ClientTimeout=lambda total: _FakeTimeout(total),
        ClientConnectorError=_ClientConnectorError,
    )


class TestOllamaClassification:
    @pytest.mark.asyncio
    async def test_ollama_success_metadata_includes_prompt_and_raw_response(self, monkeypatch):
        news = _make_news("Headline", body="Summary body")
        market = _make_full_market()
        raw_response = '{"relevant": true, "new_information": true, "direction": "yes", "magnitude": "small", "confidence": 0.5, "reasoning": "direct"}'
        response = _FakeResponse(
            status=200,
            text=json.dumps({"choices": [{"message": {"content": raw_response}}]}),
        )
        monkeypatch.setitem(sys.modules, "aiohttp", _fake_aiohttp_module(response))
        monkeypatch.setattr(signal_analyzer, "_ollama_consecutive_failures", 0)
        monkeypatch.setattr(signal_analyzer, "_ollama_down_until", 0.0)

        result, meta = await _ollama_estimate_detailed(news, market)

        assert result is not None
        assert meta["status"] == "ollama_success"
        assert "SYSTEM:" in meta["prompt"]
        assert "USER:" in meta["prompt"]
        assert "NEWS HEADLINE: Headline" in meta["prompt"]
        assert meta["raw_response"] == raw_response

    @pytest.mark.asyncio
    async def test_ollama_http_4xx_classified(self, monkeypatch):
        news = _make_news("Headline")
        market = _make_full_market()
        response = _FakeResponse(status=422, text='{"error":"bad request"}')
        monkeypatch.setitem(sys.modules, "aiohttp", _fake_aiohttp_module(response))
        monkeypatch.setattr(signal_analyzer, "_ollama_consecutive_failures", 0)
        monkeypatch.setattr(signal_analyzer, "_ollama_down_until", 0.0)

        result, meta = await _ollama_estimate_detailed(news, market)

        assert result is None
        assert meta["status"] == "ollama_http_4xx"
        assert meta["http_status"] == 422

    @pytest.mark.asyncio
    async def test_ollama_unavailable_classified(self, monkeypatch):
        news = _make_news("Headline")
        market = _make_full_market()
        fake_aiohttp = _fake_aiohttp_module(_FakeResponse())
        response = _FakeResponse(exc_on_enter=fake_aiohttp.ClientConnectorError())
        fake_aiohttp.ClientSession = lambda: _FakeClientSession(response)
        monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)
        monkeypatch.setattr(signal_analyzer, "_ollama_consecutive_failures", 0)
        monkeypatch.setattr(signal_analyzer, "_ollama_down_until", 0.0)

        result, meta = await _ollama_estimate_detailed(news, market)

        assert result is None
        assert meta["status"] == "ollama_unavailable"

    @pytest.mark.asyncio
    async def test_ollama_empty_response_classified(self, monkeypatch):
        news = _make_news("Headline")
        market = _make_full_market()
        response = _FakeResponse(status=200, text="")
        monkeypatch.setitem(sys.modules, "aiohttp", _fake_aiohttp_module(response))
        monkeypatch.setattr(signal_analyzer, "_ollama_consecutive_failures", 0)
        monkeypatch.setattr(signal_analyzer, "_ollama_down_until", 0.0)

        result, meta = await _ollama_estimate_detailed(news, market)

        assert result is None
        assert meta["status"] == "ollama_empty_response"

    @pytest.mark.asyncio
    async def test_ollama_malformed_response_classified(self, monkeypatch):
        news = _make_news("Headline")
        market = _make_full_market()
        response = _FakeResponse(status=200, text='{"choices":[{"message":{"content":')
        monkeypatch.setitem(sys.modules, "aiohttp", _fake_aiohttp_module(response))
        monkeypatch.setattr(signal_analyzer, "_ollama_consecutive_failures", 0)
        monkeypatch.setattr(signal_analyzer, "_ollama_down_until", 0.0)

        result, meta = await _ollama_estimate_detailed(news, market)

        assert result is None
        assert meta["status"] == "ollama_malformed_response"

    @pytest.mark.asyncio
    async def test_ollama_parse_failure_classified(self, monkeypatch):
        news = _make_news("Headline")
        market = _make_full_market()
        response = _FakeResponse(status=200, text='{"choices":[{"message":{"content":"not json"}}]}')
        monkeypatch.setitem(sys.modules, "aiohttp", _fake_aiohttp_module(response))
        monkeypatch.setattr(signal_analyzer, "_ollama_consecutive_failures", 0)
        monkeypatch.setattr(signal_analyzer, "_ollama_down_until", 0.0)

        result, meta = await _ollama_estimate_detailed(news, market)

        assert result is None
        assert meta["status"] == "ollama_parse_failure"

    @pytest.mark.asyncio
    async def test_ollama_timeout_classified(self, monkeypatch):
        news = _make_news("Headline")
        market = _make_full_market()
        response = _FakeResponse(exc_on_enter=asyncio.TimeoutError())
        monkeypatch.setitem(sys.modules, "aiohttp", _fake_aiohttp_module(response))
        monkeypatch.setattr(signal_analyzer, "_ollama_consecutive_failures", 0)
        monkeypatch.setattr(signal_analyzer, "_ollama_down_until", 0.0)

        result, meta = await _ollama_estimate_detailed(news, market)

        assert result is None
        assert meta["status"] == "ollama_timeout"


class TestRuntimeKeywordDisable:
    """Runtime-disabled keywords must not contribute to the keyword
    score even though they remain in GEOPOLITICAL_SIGNALS.
    """

    def test_runtime_disabled_keyword_skipped_in_keyword_score(self, monkeypatch):
        from utils import runtime_overrides as ro
        from utils.runtime_overrides import (
            DisabledKeyword, OverridesState, PredictedEffect,
        )
        from datetime import datetime, timezone
        now = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)

        # "ceasefire" is a real keyword in GEOPOLITICAL_SIGNALS (config.py).
        fake_state = OverridesState(
            version=1, updated_at=now, updated_by="test", mode="real",
            applied_disabled_keywords=[
                DisabledKeyword(
                    keyword="ceasefire", reason="test", confidence=0.9,
                    decided_at=now, decided_by="test",
                    decision_id="gd_2026-05-02_0099", expires_at=None,
                    predicted_effect=PredictedEffect(
                        metric="m", baseline=0, predicted_post_change=0, evaluate_at=now,
                    ),
                )
            ],
        )

        class FakeReader:
            def snapshot(self):
                return fake_state

        monkeypatch.setattr(ro, "_global_reader", FakeReader())

        from analysis.signal_analyzer import _keyword_score
        # Signature: (net_shift, dominant, matched_keywords)
        _shift, _direction, matched_keywords = _keyword_score(
            "Israel announces ceasefire today"
        )
        assert "ceasefire" not in matched_keywords

    def test_runtime_disabled_keyword_skipped_in_count_matched_signal_groups(
        self, monkeypatch
    ):
        """The all_required override mode counts how many signal groups have
        at least one keyword hit. A runtime-disabled keyword must NOT
        contribute a hit to its group -- otherwise the override mode would
        treat a disabled keyword as still evidence.
        """
        from utils import runtime_overrides as ro
        from utils.runtime_overrides import (
            DisabledKeyword, OverridesState, PredictedEffect,
        )
        from datetime import datetime, timezone
        now = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)

        fake_state = OverridesState(
            version=1, updated_at=now, updated_by="test", mode="real",
            applied_disabled_keywords=[
                DisabledKeyword(
                    keyword="ceasefire", reason="test", confidence=0.9,
                    decided_at=now, decided_by="test",
                    decision_id="gd_2026-05-02_0100", expires_at=None,
                    predicted_effect=PredictedEffect(
                        metric="m", baseline=0, predicted_post_change=0, evaluate_at=now,
                    ),
                )
            ],
        )

        class FakeReader:
            def snapshot(self):
                return fake_state

        monkeypatch.setattr(ro, "_global_reader", FakeReader())

        from analysis.signal_analyzer import _count_matched_signal_groups
        # A headline whose only hit is the disabled keyword should not
        # register a matched group.
        groups_before = _count_matched_signal_groups("benign text with no signals")
        groups_with_only_disabled = _count_matched_signal_groups(
            "ceasefire announced"  # only the disabled keyword matches
        )
        # The ceasefire-only headline should register zero MORE groups than
        # the benign one (since ceasefire is disabled).
        assert groups_with_only_disabled == groups_before

    def test_runtime_disabled_keyword_skipped_in_contributions(self, monkeypatch):
        """Observability path (_keyword_contributions) must also hide
        disabled keywords -- otherwise the diagnostic lies about what
        the scorer actually used.
        """
        from utils import runtime_overrides as ro
        from utils.runtime_overrides import (
            DisabledKeyword, OverridesState, PredictedEffect,
        )
        from datetime import datetime, timezone
        now = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)

        fake_state = OverridesState(
            version=1, updated_at=now, updated_by="test", mode="real",
            applied_disabled_keywords=[
                DisabledKeyword(
                    keyword="ceasefire", reason="test", confidence=0.9,
                    decided_at=now, decided_by="test",
                    decision_id="gd_2026-05-02_0101", expires_at=None,
                    predicted_effect=PredictedEffect(
                        metric="m", baseline=0, predicted_post_change=0, evaluate_at=now,
                    ),
                )
            ],
        )

        class FakeReader:
            def snapshot(self):
                return fake_state

        monkeypatch.setattr(ro, "_global_reader", FakeReader())

        from analysis.signal_analyzer import _keyword_contributions
        contributions = _keyword_contributions("Israel announces ceasefire today")
        for contribution in contributions:
            assert contribution["keyword"] != "ceasefire", (
                f"disabled keyword leaked into contributions: {contribution}"
            )


# ---------------------------------------------------------------------------
# P1-02 Stage 3c.1 — pure-helper extraction tests
# ---------------------------------------------------------------------------

class TestOllamaBuildPayload:
    """`_ollama_build_payload` is a pure projection of news+market into the
    OpenAI-compatible Chat Completions request body. Tests pin the exact
    shape and load-bearing CLAUDE.md gotchas (no `think`, no
    `repetition_penalty` — both are Ollama-native fields that 422 the
    /v1/chat/completions endpoint).
    """

    def _market(self):
        return _make_full_market()

    def _news(self):
        return _make_news("Inflation reading prompts rate cut")

    def test_payload_required_keys(self, monkeypatch):
        monkeypatch.setattr(signal_analyzer.cfg, "ollama_model", "qwen2.5:7b")
        payload = _ollama_build_payload(self._news(), self._market())
        assert payload["model"] == "qwen2.5:7b"
        assert payload["max_tokens"] == 256
        assert payload["temperature"] == 0
        assert isinstance(payload["messages"], list)
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"

    def test_payload_omits_ollama_native_fields(self, monkeypatch):
        """`think` and `repetition_penalty` must NOT appear — they 422 the
        OpenAI-compat endpoint. CLAUDE.md gotcha."""
        monkeypatch.setattr(signal_analyzer.cfg, "ollama_model", "qwen2.5:7b")
        payload = _ollama_build_payload(self._news(), self._market())
        assert "think" not in payload
        assert "repetition_penalty" not in payload

    def test_payload_user_msg_contains_market_context(self, monkeypatch):
        monkeypatch.setattr(signal_analyzer.cfg, "ollama_model", "qwen2.5:7b")
        market = self._market()
        news = self._news()
        payload = _ollama_build_payload(news, market)
        user_content = payload["messages"][1]["content"]
        assert market.title in user_content
        assert news.headline in user_content


class TestBuildLlmMetaKwargs:
    """`_build_llm_meta_kwargs` projects the 11 shared LLM-meta fields from
    `_llm_meta()` output into SignalAnalysisDetail kwarg form.
    """

    def test_full_meta_projects_all_keys(self):
        meta = _llm_meta(
            attempted=True,
            status="ollama_success",
            provider="ollama",
            latency_ms=2500,
            total_stage_ms=3200,
            queue_wait_ms=600,
            http_round_trip_ms=2400,
            parse_ms=80,
            http_status=200,
            contention_observed=False,
            in_flight_at_entry=1,
        )
        kwargs = _build_llm_meta_kwargs(meta)
        # 12 keys: llm_attempted + 11 projected
        assert kwargs["llm_attempted"] is True
        assert kwargs["llm_result_status"] == "ollama_success"
        assert kwargs["llm_provider"] == "ollama"
        assert kwargs["llm_latency_ms"] == 2500
        assert kwargs["llm_total_stage_ms"] == 3200
        assert kwargs["llm_queue_wait_ms"] == 600
        assert kwargs["llm_http_round_trip_ms"] == 2400
        assert kwargs["llm_parse_ms"] == 80
        assert kwargs["llm_http_status"] == 200
        assert kwargs["llm_contention_observed"] is False
        assert kwargs["llm_in_flight_at_entry"] == 1
        # llm_result_used is NOT projected — set per-callsite
        assert "llm_result_used" not in kwargs

    def test_partial_meta_uses_get_for_missing_keys(self):
        """When _llm_meta is built with fewer args, missing optional fields
        propagate as None via `.get()`."""
        meta = _llm_meta(
            attempted=False,
            status="ollama_circuit_open",
            provider="ollama",
        )
        kwargs = _build_llm_meta_kwargs(meta)
        assert kwargs["llm_attempted"] is False
        assert kwargs["llm_result_status"] == "ollama_circuit_open"
        assert kwargs["llm_provider"] == "ollama"
        assert kwargs["llm_latency_ms"] is None
        assert kwargs["llm_http_status"] is None
        assert kwargs["llm_contention_observed"] is None

    def test_projection_keys_are_signal_analysis_detail_fields(self):
        """Every key produced by the helper must be a valid kwarg on
        SignalAnalysisDetail. Catches drift if either side adds a field."""
        from utils.log_records import SignalAnalysisDetail
        meta = _llm_meta(attempted=True, status="x", provider="y")
        kwargs = _build_llm_meta_kwargs(meta)
        sad_field_names = {f.name for f in dataclasses.fields(SignalAnalysisDetail)}
        for k in kwargs:
            assert k in sad_field_names, f"projection key {k!r} not in SignalAnalysisDetail"


class TestOllamaCheckCircuit:
    """Circuit-breaker helper. Pure-state interactions with module globals."""

    @pytest.fixture(autouse=True)
    def _reset_circuit(self):
        signal_analyzer._ollama_consecutive_failures = 0
        signal_analyzer._ollama_down_until = 0.0
        yield
        signal_analyzer._ollama_consecutive_failures = 0
        signal_analyzer._ollama_down_until = 0.0

    @pytest.mark.asyncio
    async def test_circuit_closed_returns_proceed(self):
        may_proceed, meta = await _ollama_check_circuit()
        assert may_proceed is True
        assert meta is None

    @pytest.mark.asyncio
    async def test_circuit_open_within_window_blocks(self, monkeypatch):
        # Set down_until to far future
        signal_analyzer._ollama_down_until = 9.99e18
        may_proceed, meta = await _ollama_check_circuit()
        assert may_proceed is False
        assert meta["status"] == "ollama_circuit_open"
        assert meta["attempted"] is True

    @pytest.mark.asyncio
    async def test_probe_failure_extends_window_without_counter_bump(self, monkeypatch):
        signal_analyzer._ollama_down_until = 1.0  # past time → enter probe path
        signal_analyzer._ollama_consecutive_failures = 5

        async def _ping_fail():
            return False

        monkeypatch.setattr(signal_analyzer, "_ollama_ping", _ping_fail)
        may_proceed, meta = await _ollama_check_circuit()
        assert may_proceed is False
        assert meta["status"] == "ollama_probe_failed"
        # Counter MUST NOT increment on probe failure
        assert signal_analyzer._ollama_consecutive_failures == 5
        assert signal_analyzer._ollama_down_until > 0.0

    @pytest.mark.asyncio
    async def test_probe_success_resets_counter_and_window(self, monkeypatch):
        signal_analyzer._ollama_down_until = 1.0
        signal_analyzer._ollama_consecutive_failures = 5

        async def _ping_ok():
            return True

        monkeypatch.setattr(signal_analyzer, "_ollama_ping", _ping_ok)
        may_proceed, meta = await _ollama_check_circuit()
        assert may_proceed is True
        assert meta is None
        assert signal_analyzer._ollama_consecutive_failures == 0
        assert signal_analyzer._ollama_down_until == 0.0


class TestOllamaRecordFailure:
    """Failure-recording helper. Counter increment + circuit-open."""

    @pytest.fixture(autouse=True)
    def _reset_circuit(self):
        signal_analyzer._ollama_consecutive_failures = 0
        signal_analyzer._ollama_down_until = 0.0
        yield
        signal_analyzer._ollama_consecutive_failures = 0
        signal_analyzer._ollama_down_until = 0.0

    def test_unavailable_below_threshold_increments_only(self):
        signal_analyzer._ollama_consecutive_failures = 0
        meta = _ollama_record_failure("unavailable", 250)
        assert meta["status"] == "ollama_unavailable"
        assert meta["attempted"] is True
        assert meta["latency_ms"] == 250
        assert signal_analyzer._ollama_consecutive_failures == 1
        assert signal_analyzer._ollama_down_until == 0.0  # not yet open

    def test_timeout_below_threshold_increments_only(self):
        meta = _ollama_record_failure("timeout", 60000)
        assert meta["status"] == "ollama_timeout"
        assert signal_analyzer._ollama_consecutive_failures == 1
        assert signal_analyzer._ollama_down_until == 0.0

    def test_threshold_reached_opens_circuit(self):
        signal_analyzer._ollama_consecutive_failures = _OLLAMA_FAILURE_THRESHOLD - 1
        meta = _ollama_record_failure("unavailable", 100)
        assert signal_analyzer._ollama_consecutive_failures == _OLLAMA_FAILURE_THRESHOLD
        assert signal_analyzer._ollama_down_until > time.monotonic()
        # Circuit-open delay is _OLLAMA_PROBE_INTERVAL seconds
        delta = signal_analyzer._ollama_down_until - time.monotonic()
        assert _OLLAMA_PROBE_INTERVAL - 1 < delta <= _OLLAMA_PROBE_INTERVAL


# Need `time` for the circuit threshold test. Place at end so imports stay
# grouped at top; helper-tests above don't otherwise need it.
import time  # noqa: E402


class TestOllamaExtractAndValidate:
    """LLM-JSON parse + budget gate. Pure (modulo `cfg.ollama_stage_budget_seconds`)."""

    def _market(self):
        return _make_full_market()

    def test_success_path_returns_result_and_success_meta(self, monkeypatch):
        monkeypatch.setattr(signal_analyzer.cfg, "ollama_stage_budget_seconds", 60)
        text = '{"direction":"yes","magnitude":"moderate","confidence":0.85,"reasoning":"r"}'
        market = self._market()
        t0 = time.monotonic() - 0.5  # half a second elapsed (well under budget)
        result, meta = _ollama_extract_and_validate(text, market, t0, 200, "PROMPT")
        assert result is not None
        prob, confidence, reasoning, direction, magnitude = result
        assert direction == "yes"
        assert magnitude == "moderate"
        assert confidence == 0.85
        assert meta["status"] == "ollama_success"
        assert meta["result_used"] is True
        assert meta["http_round_trip_ms"] == 200
        assert meta["raw_response"] == text

    def test_parse_failure_returns_failure_meta(self, monkeypatch):
        monkeypatch.setattr(signal_analyzer.cfg, "ollama_stage_budget_seconds", 60)
        text = "not json at all"
        market = self._market()
        t0 = time.monotonic() - 0.1
        result, meta = _ollama_extract_and_validate(text, market, t0, 100, "PROMPT")
        assert result is None
        assert meta["status"] == "ollama_parse_failure"
        assert meta["raw_response"] == text
        assert meta["parse_ms"] is not None

    def test_budget_exceeded_returns_failure_meta(self, monkeypatch):
        # 1ms budget — guaranteed to exceed
        monkeypatch.setattr(signal_analyzer.cfg, "ollama_stage_budget_seconds", 0.001)
        text = '{"direction":"yes","magnitude":"moderate","confidence":0.85,"reasoning":"r"}'
        market = self._market()
        t0 = time.monotonic() - 1.0  # 1 second elapsed
        result, meta = _ollama_extract_and_validate(text, market, t0, 100, "PROMPT")
        assert result is None
        assert meta["status"] == "ollama_slow_budget_exceeded"


class TestOllamaPost:
    """HTTP POST + envelope-decode + content-extraction.

    Covers HTTP error branches (non-200, empty body, malformed JSON,
    response shape error, empty content). Does NOT exercise connection-level
    exceptions — those propagate to the caller's except handlers.
    """

    @pytest.mark.asyncio
    async def test_success_returns_text_and_no_early_meta(self, monkeypatch):
        # Patch aiohttp.ClientSession to return a 200 with a valid envelope.
        from contextlib import asynccontextmanager

        class _Resp:
            status = 200
            async def text(self):
                return '{"choices":[{"message":{"content":"INNER"}}]}'

        @asynccontextmanager
        async def _post(*args, **kwargs):
            yield _Resp()

        @asynccontextmanager
        async def _session():
            class _S:
                def post(self, *a, **kw):
                    return _post()
            yield _S()

        import aiohttp
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: _session())
        text, early_meta, _rt, status = await _ollama_post({"model": "x"}, "PROMPT", time.monotonic())
        assert text == "INNER"
        assert early_meta is None
        assert status == 200

    @pytest.mark.asyncio
    async def test_non_200_returns_failure_meta(self, monkeypatch):
        from contextlib import asynccontextmanager

        class _Resp:
            status = 422
            async def text(self):
                return "{\"error\": \"unrecognized field\"}"

        @asynccontextmanager
        async def _post(*args, **kwargs):
            yield _Resp()

        @asynccontextmanager
        async def _session():
            class _S:
                def post(self, *a, **kw):
                    return _post()
            yield _S()

        import aiohttp
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: _session())
        text, early_meta, _rt, status = await _ollama_post({}, "PROMPT", time.monotonic())
        assert text is None
        assert status == 422
        assert early_meta is not None
        # Status string comes from _ollama_http_status_category(422)
        assert early_meta["http_status"] == 422

    @pytest.mark.asyncio
    async def test_empty_body_returns_failure_meta(self, monkeypatch):
        from contextlib import asynccontextmanager

        class _Resp:
            status = 200
            async def text(self):
                return "   "

        @asynccontextmanager
        async def _post(*args, **kwargs):
            yield _Resp()

        @asynccontextmanager
        async def _session():
            class _S:
                def post(self, *a, **kw):
                    return _post()
            yield _S()

        import aiohttp
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: _session())
        text, early_meta, _rt, _status = await _ollama_post({}, "PROMPT", time.monotonic())
        assert text is None
        assert early_meta["status"] == "ollama_empty_response"

    @pytest.mark.asyncio
    async def test_malformed_envelope_json_returns_failure_meta(self, monkeypatch):
        from contextlib import asynccontextmanager

        class _Resp:
            status = 200
            async def text(self):
                return "this is not json"

        @asynccontextmanager
        async def _post(*args, **kwargs):
            yield _Resp()

        @asynccontextmanager
        async def _session():
            class _S:
                def post(self, *a, **kw):
                    return _post()
            yield _S()

        import aiohttp
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: _session())
        text, early_meta, _rt, _status = await _ollama_post({}, "PROMPT", time.monotonic())
        assert text is None
        assert early_meta["status"] == "ollama_malformed_response"


# ---------------------------------------------------------------------------
# PROFIT-MATCH-DYNAMIC (commit 2/5) — MATCH_LLM_REVIEW emission verdict logic
# ---------------------------------------------------------------------------

class TestMatchLlmReviewVerdict:
    """Pins the verdict-inference rules used by signal_analyzer to classify
    each LLM result as true_positive / false_positive_neutral / undetermined.
    Encoded inline at signal_analyzer.py (call site of log_match_llm_review).
    Mirrored here as a pure function so the boundary is testable.
    """

    @staticmethod
    def _classify(direction: str, magnitude: str, confidence: float | None) -> str | None:
        if direction in ("yes", "no"):
            return "true_positive"
        if (direction == "neutral"
                and magnitude == "none"
                and confidence is not None
                and float(confidence) >= 0.7):
            return "false_positive_neutral"
        return None

    def test_directional_yes_is_true_positive(self):
        assert self._classify("yes", "small", 0.85) == "true_positive"

    def test_directional_no_is_true_positive(self):
        assert self._classify("no", "moderate", 0.95) == "true_positive"

    def test_directional_low_confidence_still_true_positive(self):
        # If LLM commits to a side at all, matcher gets a "match was useful"
        # signal even at low confidence. Don't punish the matcher for LLM
        # uncertainty when the match itself was topic-correct.
        assert self._classify("yes", "small", 0.3) == "true_positive"

    def test_confident_neutral_none_is_false_positive(self):
        """Per PROFIT-MATCH-DYNAMIC commit 2/5: confident neutral + none is
        the LLM saying 'this match was topic-wrong'. Penalize the matcher."""
        assert self._classify("neutral", "none", 0.85) == "false_positive_neutral"
        assert self._classify("neutral", "none", 0.7) == "false_positive_neutral"
        assert self._classify("neutral", "none", 0.95) == "false_positive_neutral"

    def test_low_confidence_neutral_is_undetermined(self):
        """Below 0.7 confidence on neutral, we don't know if the match was
        bad or if the LLM was uncertain. Skip the feedback signal — don't
        punish the matcher for LLM low confidence."""
        assert self._classify("neutral", "none", 0.55) is None
        assert self._classify("neutral", "none", 0.3) is None

    def test_neutral_with_magnitude_is_undetermined(self):
        """Neutral direction but non-none magnitude is an LLM inconsistency
        (caught separately by Phase B PROFIT-LLM-002 bump). Don't feed the
        matcher loop here — different signal."""
        assert self._classify("neutral", "small", 0.95) is None


class TestLogMatchLlmReview:
    """Schema pin for the trade_log.log_match_llm_review writer."""

    def test_log_match_llm_review_writes_expected_keys(self, tmp_path, monkeypatch):
        # Redirect log root so we don't pollute prod logs
        monkeypatch.setenv("KALSHI_LOG_ROOT", str(tmp_path))
        # Force fresh logger import bound to the new root
        import importlib, utils.logger as logger_mod
        importlib.reload(logger_mod)
        tl = logger_mod.TradeLogger(tmp_path / "trades.jsonl")
        tl.log_match_llm_review(
            ticker="KXCABLEAVE-26MAY22-26JUN",
            market_title="Will any member of Trump's Cabinet leave before Jun 2026?",
            market_prefix="KXCABLEAVE",
            headline="LIVE: Trump says Iran deal not 'fully negotiated yet'",
            source="Some Outlet",
            matched_tokens=["trump"],
            llm_relevant=False,
            llm_direction="neutral",
            llm_magnitude="none",
            llm_confidence=0.85,
            verdict="false_positive_neutral",
        )
        # Read back
        import json
        path = tmp_path / "trades.jsonl"
        line = path.read_text(encoding="utf-8").strip().splitlines()[-1]
        rec = json.loads(line)
        assert rec["type"] == "MATCH_LLM_REVIEW"
        assert rec["ticker"] == "KXCABLEAVE-26MAY22-26JUN"
        assert rec["market_prefix"] == "KXCABLEAVE"
        assert rec["matched_tokens"] == ["trump"]
        assert rec["verdict"] == "false_positive_neutral"
        assert rec["llm_confidence"] == 0.85
        assert rec["llm_relevant"] is False


# ---------------------------------------------------------------------------
# PROFIT-ALIGN-002 (2026-05-25) — log_calibration_observation schema
# ---------------------------------------------------------------------------

class TestLogCalibrationObservation:
    """Schema pin for trade_log.log_calibration_observation. Emitted once
    per resolved paper trade by paper_trader._resolve_market_sync. Downstream
    aggregator computes Brier score / calibration curve per archetype."""

    def test_log_calibration_writes_expected_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KALSHI_LOG_ROOT", str(tmp_path))
        import importlib, utils.logger as logger_mod
        importlib.reload(logger_mod)
        tl = logger_mod.TradeLogger(tmp_path / "trades.jsonl")
        tl.log_calibration_observation(
            trade_id="abc123",
            ticker="KXTRUMPIRAN-26JUN01",
            market_prefix="KXTRUMPIRAN",
            side="no",
            estimated_probability=0.95,
            realized_outcome=1,
            entry_price_cents=92.0,
            pnl_dollars=0.40,
            cost_dollars=4.60,
            llm_magnitude="small",
            llm_confidence=0.85,
            signal_source="NYT > World News",
            ts_entry="2026-05-25T18:36:44+00:00",
            ts_resolved="2026-06-01T12:00:00+00:00",
        )
        import json
        line = (tmp_path / "trades.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
        rec = json.loads(line)
        assert rec["type"] == "CALIBRATION_OBSERVATION"
        assert rec["trade_id"] == "abc123"
        assert rec["market_prefix"] == "KXTRUMPIRAN"
        assert rec["side"] == "no"
        assert rec["estimated_probability"] == 0.95
        assert rec["realized_outcome"] == 1
        assert rec["pnl_dollars"] == 0.40
        assert rec["llm_magnitude"] == "small"
        assert rec["llm_confidence"] == 0.85

    def test_log_calibration_with_null_llm_fields(self, tmp_path, monkeypatch):
        """Some legacy trades may have null LLM fields. Writer must tolerate."""
        monkeypatch.setenv("KALSHI_LOG_ROOT", str(tmp_path))
        import importlib, utils.logger as logger_mod
        importlib.reload(logger_mod)
        tl = logger_mod.TradeLogger(tmp_path / "trades.jsonl")
        tl.log_calibration_observation(
            trade_id="legacy1",
            ticker="KXOLD-26MAY01",
            market_prefix="KXOLD",
            side="yes",
            estimated_probability=0.65,
            realized_outcome=0,
            entry_price_cents=55.0,
            pnl_dollars=-2.75,
            cost_dollars=2.75,
            llm_magnitude=None,
            llm_confidence=None,
            signal_source="r/test",
            ts_entry="2026-05-01T00:00:00+00:00",
            ts_resolved="2026-05-15T00:00:00+00:00",
        )
        import json
        line = (tmp_path / "trades.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
        rec = json.loads(line)
        assert rec["llm_magnitude"] is None
        assert rec["llm_confidence"] is None
        assert rec["realized_outcome"] == 0
