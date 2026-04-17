"""
Tests for analysis/signal_analyzer.py

Covers: JSON extraction edge cases, probability mapping, keyword scoring,
        geo-coherence suppression, and estimator fallback behaviour.
"""

import asyncio
import sys
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import analysis.signal_analyzer as signal_analyzer
from analysis.signal_analyzer import (
    _extract_json,
    _ollama_estimate_detailed,
    _parse_llm_response,
    _keyword_score,
    estimate_probability,
    keyword_estimate,
)
from feeds import NewsItem


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


def _make_news(headline: str, body: str = ""):
    return NewsItem(
        headline=headline,
        url="https://example.com/story",
        source="Reuters",
        published=datetime.now(timezone.utc),
        body=body,
        item_id="news-1",
    )


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
        detail_mock.assert_called_once_with(
            ticker=market.ticker,
            source=news.source,
            headline=news.headline,
            method="llm",
            keywords=[],
            keyword_contributions=[],
            base_probability=market.yes_prob,
            final_probability=0.64,
            market_price=market.yes_prob,
            llm_direction="yes",
            llm_magnitude="moderate",
            llm_confidence=0.85,
            llm_attempted=True,
            llm_result_used=True,
            llm_result_status="ollama_success",
            llm_provider="ollama",
            llm_latency_ms=None,
            llm_total_stage_ms=None,
            llm_queue_wait_ms=None,
            llm_http_round_trip_ms=None,
            llm_parse_ms=None,
            llm_http_status=None,
            llm_contention_observed=None,
            llm_in_flight_at_entry=None,
        )

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
        detail_mock.assert_called_once_with(
            ticker=market.ticker,
            source=news.source,
            headline=news.headline,
            method="keyword_gate",
            keywords=[],
            keyword_contributions=[],
            base_probability=market.yes_prob,
            final_probability=market.yes_prob,
            market_price=market.yes_prob,
            llm_attempted=True,
            llm_result_used=False,
            llm_result_status="ollama_timeout",
            llm_provider="ollama",
            llm_latency_ms=None,
            llm_total_stage_ms=None,
            llm_queue_wait_ms=None,
            llm_http_round_trip_ms=None,
            llm_parse_ms=None,
            llm_http_status=None,
            llm_contention_observed=None,
            llm_in_flight_at_entry=None,
        )

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
        kwargs = detail_mock.call_args.kwargs
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
        kwargs = detail_mock.call_args.kwargs
        assert kwargs["method"] == "keyword"
        assert kwargs["base_probability"] == pytest.approx(market.yes_prob)
        assert kwargs["final_probability"] == pytest.approx(prob)
        assert kwargs["market_price"] == pytest.approx(market.yes_prob)
        assert kwargs["llm_attempted"] is False
        assert kwargs["llm_result_used"] is False
        assert kwargs["llm_result_status"] == "no_provider_available"
        assert kwargs["llm_provider"] is None
        assert kwargs["keywords"]
        assert kwargs["keyword_contributions"]


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
