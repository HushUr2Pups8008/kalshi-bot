"""
Tests for analysis/market_matcher.py

Covers: tokenization, similarity scoring, and candidate matching behaviour.
"""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config as _cfg_module
from analysis.market_matcher import (
    MarketMatcher,
    _compute_pre_llm_match_meta,
    _tokenize,
    _similarity,
)
from kalshi import KalshiMarket


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_lowercases_and_splits(self):
        tokens = _tokenize("Hello World")
        assert "hello" in tokens
        assert "world" in tokens

    def test_removes_stopwords(self):
        tokens = _tokenize("will the US attack Iran")
        # common stopwords excluded
        assert "will" not in tokens
        assert "the" not in tokens
        assert "iran" in tokens

    def test_empty_string(self):
        assert _tokenize("") == set()

    def test_punctuation_stripped(self):
        tokens = _tokenize("ceasefire, deal! signed.")
        assert "ceasefire" in tokens
        assert "deal" in tokens
        assert "signed" in tokens

    def test_returns_set(self):
        assert isinstance(_tokenize("foo bar baz"), set)


# ---------------------------------------------------------------------------
# _similarity (Jaccard)
# ---------------------------------------------------------------------------

class TestSimilarity:
    def test_identical_sets(self):
        a = {"russia", "ceasefire", "war"}
        assert _similarity(a, a) == pytest.approx(1.0)

    def test_disjoint_sets(self):
        a = {"russia", "war"}
        b = {"iran", "ceasefire"}
        assert _similarity(a, b) == pytest.approx(0.0)

    def test_partial_overlap(self):
        a = {"russia", "war", "ceasefire"}
        b = {"russia", "war", "strike"}
        # base Jaccard = 2/4 = 0.5, but geo-boost adds 0.03 per boosted intersection token
        # (russia and war are both in _GEOPOLITICAL_BOOST), so score > 0.5
        score = _similarity(a, b)
        assert score > 0.5
        assert score <= 1.0

    def test_empty_sets_returns_zero(self):
        assert _similarity(set(), set()) == 0.0

    def test_one_empty_returns_zero(self):
        assert _similarity({"russia"}, set()) == 0.0

    def test_subset_partial_score(self):
        a = {"russia"}
        b = {"russia", "ukraine", "war"}
        # base Jaccard = 1/3, boosted because 'russia' is a geo token
        score = _similarity(a, b)
        assert score > 1/3
        assert score <= 1.0


def _make_market(
    ticker: str,
    title: str,
    *,
    subtitle: str = "",
    yes_price: float = 50.0,
    days_to_close: int = 7,
    status: str = "open",
    series_ticker: str = "KXTEST",
):
    close_time = (datetime.now(timezone.utc) + timedelta(days=days_to_close)).isoformat()
    return KalshiMarket(
        ticker=ticker,
        title=title,
        yes_bid=max(1.0, yes_price - 1),
        yes_ask=min(99.0, yes_price + 1),
        yes_price=yes_price,
        volume=100,
        open_interest=200,
        close_time=close_time,
        status=status,
        series_ticker=series_ticker,
        subtitle=subtitle,
        result="",
    )


from tests._helpers import make_news as _make_news  # noqa: E402


class TestMarketShape:
    def test_kalshi_market_has_backward_compatible_regime_weights_default(self):
        market_a = _make_market("KXIRAN-1", "Will Iran close the Strait of Hormuz?")
        market_b = _make_market("KXUKR-1", "Will Russia invade Ukraine?")

        assert market_a.regime_weights == {}
        assert market_b.regime_weights == {}
        assert market_a.regime_weights is not market_b.regime_weights

    def test_kalshi_market_serialization_includes_regime_weights(self):
        market = _make_market("KXIRAN-1", "Will Iran close the Strait of Hormuz?")
        market.regime_weights = {
            "fast": 0.2,
            "interpretation": 0.5,
            "structural": 0.3,
        }

        assert asdict(market)["regime_weights"] == {
            "fast": 0.2,
            "interpretation": 0.5,
            "structural": 0.3,
        }


@pytest.fixture
def matcher(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    return MarketMatcher(MagicMock())


class TestFindCandidates:
    @pytest.mark.asyncio
    async def test_clear_geopolitical_headline_matches_correct_market(self, matcher):
        markets = [
            _make_market("KXUKR-1", "Will Russia invade Ukraine in 2026?"),
            _make_market("KXTAIWAN-1", "Will China blockade Taiwan in 2026?"),
        ]
        matcher._cache.get_markets = AsyncMock(return_value=markets)

        news = _make_news("Russia launches new attack on Ukraine border")
        results = await matcher.find_candidates(news)

        assert results
        assert results[0][0].ticker == "KXUKR-1"

    @pytest.mark.asyncio
    async def test_non_geopolitical_market_is_filtered_even_with_token_overlap(self, matcher):
        markets = [
            _make_market("KXSPORT-1", "Will the team attack early and win the match?", subtitle="sportsbook", series_ticker="KXSOCCER"),
            _make_market("KXIRAN-1", "Will Iran attack U.S. forces in 2026?"),
        ]
        matcher._cache.get_markets = AsyncMock(return_value=markets)

        news = _make_news("Iran attack raises fears of wider regional conflict")
        results = await matcher.find_candidates(news)

        assert results
        assert all(r[0].ticker != "KXSPORT-1" for r in results)
        assert results[0][0].ticker == "KXIRAN-1"

    @pytest.mark.asyncio
    async def test_no_match_when_headline_lacks_meaningful_overlap(self, matcher):
        markets = [
            _make_market("KXUKR-1", "Will Russia invade Ukraine in 2026?"),
            _make_market("KXIRAN-1", "Will Iran close the Strait of Hormuz in 2026?"),
        ]
        matcher._cache.get_markets = AsyncMock(return_value=markets)

        news = _make_news("Central bank signals possible interest-rate hold")
        results = await matcher.find_candidates(news)

        assert results == []

    @pytest.mark.asyncio
    async def test_named_entity_gate_allows_single_distinctive_overlap(self, matcher):
        markets = [
            _make_market("KXTRUMP-1", "Will Trump sign a tariff order this month?"),
        ]
        matcher._cache.get_markets = AsyncMock(return_value=markets)

        news = _make_news("Trump considers broader tariff action")
        results = await matcher.find_candidates(news)

        assert len(results) == 1
        assert results[0][0].ticker == "KXTRUMP-1"

    @pytest.mark.asyncio
    async def test_generic_overlap_requires_two_tokens(self, matcher):
        markets = [
            _make_market("KXWAR-1", "Will attack trigger war in the region this month?"),
        ]
        matcher._cache.get_markets = AsyncMock(return_value=markets)

        one_token_news = _make_news("War fears rise")
        two_token_news = _make_news("War attack fears rise")

        one_token_results = await matcher.find_candidates(one_token_news)
        two_token_results = await matcher.find_candidates(two_token_news)

        assert one_token_results == []
        assert len(two_token_results) == 1

    @pytest.mark.asyncio
    async def test_live_mode_uses_tighter_candidate_limit(self, matcher, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        markets = [
            _make_market(f"KXTEST-{i}", f"Will Russia attack Ukraine sector {i}?")
            for i in range(10)
        ]
        matcher._cache.get_markets = AsyncMock(return_value=markets)

        news = _make_news("Russia attack on Ukraine escalates")
        results = await matcher.find_candidates(news)

        assert len(results) <= 5

    @pytest.mark.asyncio
    async def test_match_diagnostics_logs_good_match_without_low_quality_flag(self, matcher):
        markets = [
            _make_market("KXIRAN-1", "Will Iran close the Strait of Hormuz in 2026?"),
        ]
        matcher._cache.get_markets = AsyncMock(return_value=markets)
        news = _make_news("Iran threatens Strait of Hormuz closure")

        with pytest.MonkeyPatch.context() as mp:
            from analysis import market_matcher as mm
            calls = []
            mp.setattr(mm.trade_log, "log_match_diagnostic", lambda **kwargs: calls.append(kwargs))
            results = await matcher.find_candidates(news)

        assert results
        assert len(calls) == 1
        payload = calls[0]
        assert payload["ticker"] == "KXIRAN-1"
        assert payload["source"] == "Reuters"
        assert payload["low_match_quality"] is False
        assert "iran" in payload["matched_tokens"]
        assert payload["pre_llm_semantic_overlap_count"] == 3
        assert payload["pre_llm_semantic_overlap_ratio"] == pytest.approx(0.75)
        assert payload["would_fail_pre_llm_gate"] is False
        assert "publish_ts" in payload
        assert isinstance(payload["publish_ts"], str)
        assert "age_at_match_seconds" in payload
        assert payload["age_at_match_seconds"] >= 0.0

    @pytest.mark.asyncio
    async def test_match_diagnostics_flags_single_named_entity_overlap_as_low_quality(self, matcher):
        markets = [
            _make_market("KXTRUMP-25A", "Will Trump order military action under the 25th Amendment this year?"),
        ]
        matcher._cache.get_markets = AsyncMock(return_value=markets)
        news = _make_news("Trump says Iran talks have collapsed again")

        with pytest.MonkeyPatch.context() as mp:
            from analysis import market_matcher as mm
            calls = []
            mp.setattr(mm.trade_log, "log_match_diagnostic", lambda **kwargs: calls.append(kwargs))
            results = await matcher.find_candidates(news)

        assert results
        assert len(calls) == 1
        payload = calls[0]
        assert payload["low_match_quality"] is True
        assert "single_named_entity_only" in payload["heuristic_flags"]
        assert payload["matched_tokens"] == ["trump"]
        assert payload["pre_llm_semantic_overlap_count"] == 1
        assert payload["pre_llm_semantic_overlap_ratio"] == pytest.approx(1 / 6)
        assert payload["would_fail_pre_llm_gate"] is True

    def test_compute_pre_llm_match_meta_filters_feature_stopwords(self):
        meta = _compute_pre_llm_match_meta(
            "Trump says latest Iran report after talks",
            "Will Trump report on Iran talks?",
            ["trump", "say", "report", "iran"],
        )

        assert meta["pre_llm_semantic_overlap_tokens"] == ["trump", "iran"]
        assert meta["pre_llm_semantic_overlap_count"] == 2
        assert meta["pre_llm_semantic_overlap_ratio"] == pytest.approx(0.5)
        assert meta["pre_llm_quality_pass"] is True
        assert meta["pre_llm_gate_reason"] is None
        assert meta["pre_llm_filtered_stopword_count"] == 2
        assert meta["pre_llm_filtered_generic_count"] == 0
        assert meta["pre_llm_semantic_token_types"] == {"named_entity": 2, "generic": 0}

    def test_compute_pre_llm_match_meta_filters_generic_low_information_tokens(self):
        meta = _compute_pre_llm_match_meta(
            "President Iran updates after talks",
            "Will the president discuss Iran talks?",
            [" President ", "Iran", "updates"],
        )

        assert meta["pre_llm_semantic_overlap_tokens"] == ["iran"]
        assert meta["pre_llm_semantic_overlap_count"] == 1
        assert meta["pre_llm_filtered_stopword_count"] == 1
        assert meta["pre_llm_filtered_generic_count"] == 1
        assert meta["pre_llm_semantic_token_types"] == {"named_entity": 1, "generic": 0}
        assert meta["pre_llm_gate_reason"] == "insufficient_semantic_overlap"

    @pytest.mark.asyncio
    async def test_single_named_entity_match_is_penalized_below_threshold(self, matcher, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
        markets = [
            _make_market("KXTRUMP-25A", "Will Trump order military action under the 25th Amendment this year?"),
        ]
        matcher._cache.get_markets = AsyncMock(return_value=markets)
        news = _make_news("Trump says Iran talks have collapsed again")

        with pytest.MonkeyPatch.context() as mp:
            from analysis import market_matcher as mm
            calls = []
            mp.setattr(mm.trade_log, "log_match_diagnostic", lambda **kwargs: calls.append(kwargs))
            mp.setattr(mm, "_similarity", lambda *_args, **_kwargs: 0.07)
            mp.setattr(mm, "_days_to_close", lambda *_args, **_kwargs: None)
            results = await matcher.find_candidates(news)

        assert results == []
        assert calls == []

    @pytest.mark.asyncio
    async def test_strong_multi_token_match_is_not_penalized(self, matcher):
        markets = [
            _make_market("KXUKR-1", "Will Russia invade Ukraine in 2026?"),
        ]
        matcher._cache.get_markets = AsyncMock(return_value=markets)
        news = _make_news("Russia launches new attack on Ukraine border")

        results = await matcher.find_candidates(news)

        assert results
        assert results[0][0].ticker == "KXUKR-1"


class TestMarketCacheTestTickerExclusion:
    def test_fetch_geo_markets_excludes_kxtest_tickers(self):
        rest = MagicMock()
        rest.get_all_series.return_value = [
            {"ticker": "KXIRAN", "title": "Iran"},
        ]
        rest.get_markets.return_value = ([
            _make_market("KXTEST-25DEC31", "Will test market resolve yes?"),
            _make_market("KXIRAN-1", "Will Iran close the Strait of Hormuz in 2026?", series_ticker="KXIRAN"),
        ], None)

        matcher = MarketMatcher(rest)

        markets, series_count = matcher._cache._fetch_geo_markets()

        assert series_count == 1
        assert [m.ticker for m in markets] == ["KXIRAN-1"]

    def test_fetch_geo_markets_attaches_regime_weights(self):
        rest = MagicMock()
        rest.get_all_series.return_value = [
            {"ticker": "KXIRAN", "title": "Iran"},
        ]
        rest.get_markets.return_value = ([
            _make_market(
                "KXIRAN-1",
                "Will Iran close the Strait of Hormuz in 2026?",
                series_ticker="KXIRAN",
            ),
        ], None)

        matcher = MarketMatcher(rest)

        markets, series_count = matcher._cache._fetch_geo_markets()

        assert series_count == 1
        assert len(markets) == 1
        assert set(markets[0].regime_weights) == {"fast", "interpretation", "structural"}
        assert sum(markets[0].regime_weights.values()) == pytest.approx(1.0)

    def test_fetch_all_markets_excludes_kxtest_tickers(self):
        rest = MagicMock()
        rest.get_markets.side_effect = [
            ([
                _make_market("KXTEST-25DEC31", "Will test market resolve yes?"),
                _make_market("KXNBA-1", "Will the Nuggets win tonight?", series_ticker="KXNBA"),
            ], None),
        ]

        matcher = MarketMatcher(rest)

        markets = matcher._cache._fetch_all_markets()

        assert [m.ticker for m in markets] == ["KXNBA-1"]

    def test_fetch_all_markets_attaches_regime_weights(self):
        rest = MagicMock()
        rest.get_markets.side_effect = [
            ([
                _make_market("KXNBA-1", "Will the Nuggets win tonight?", series_ticker="KXNBA"),
            ], None),
        ]

        matcher = MarketMatcher(rest)

        markets = matcher._cache._fetch_all_markets()

        assert len(markets) == 1
        assert set(markets[0].regime_weights) == {"fast", "interpretation", "structural"}
        assert sum(markets[0].regime_weights.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Low-quality match suppression
# ---------------------------------------------------------------------------

class TestLowQualityMatchSuppression:
    """Suppression is config-gated (ENABLE_LOW_QUALITY_MATCH_SUPPRESSION).
    Two suppression paths:
      Path A (original): near_threshold_score AND (minimal_overlap OR single_named_entity_only)
      Path B (new):      single_named_entity_only AND minimal_overlap [score-independent]
    Both paths also require: NOT any matched token in the market ticker.
    MATCH_DIAGNOSTIC is always logged; MATCH_SUPPRESSED only when criteria met and flag on.
    """

    @pytest.fixture
    def matcher(self):
        rest = MagicMock()
        rest.get_markets = AsyncMock(return_value=[])
        rest.get_series = AsyncMock(return_value=[])
        return MarketMatcher(rest)

    @pytest.mark.asyncio
    async def test_suppression_off_by_default_candidate_returned(self, matcher):
        """When suppression flag is off, low-quality candidates still reach caller.
        Uses patched _match_quality_flags to inject near-threshold suppression flags
        without needing a score that is genuinely near threshold in this fixture."""
        market = _make_market(
            "KXTRUMP-25A",
            "Will Trump order military action under the 25th Amendment this year?",
        )
        matcher._cache.get_markets = AsyncMock(return_value=[market])
        news = _make_news("Trump says Iran talks have collapsed again")

        import analysis.market_matcher as mm
        import config as cfg_module
        orig_flag = cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION
        suppression_flags = ["near_threshold_score", "single_named_entity_only"]
        try:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = False
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = False
            with patch("analysis.market_matcher._match_quality_flags", return_value=suppression_flags):
                results = await matcher.find_candidates(news)
        finally:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag

        assert results, "candidate should be returned when suppression is off"

    @pytest.mark.asyncio
    async def test_suppression_on_drops_single_named_entity_near_threshold(self, matcher):
        """When suppression is on and matched token is NOT in ticker -> suppressed.
        Uses KXMIL-25A (no 'trump' substring) so ticker guard does not block suppression."""
        market = _make_market(
            "KXMIL-25A",
            "Will Trump order military action under the 25th Amendment this year?",
        )
        matcher._cache.get_markets = AsyncMock(return_value=[market])
        news = _make_news("Trump says Iran talks have collapsed again")

        import analysis.market_matcher as mm
        import config as cfg_module
        orig_flag = cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION
        suppression_flags = ["near_threshold_score", "single_named_entity_only"]
        suppressed_calls = []
        orig_supp = mm.trade_log.log_match_suppressed
        mm.trade_log.log_match_suppressed = lambda **kw: suppressed_calls.append(kw)
        try:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            with patch("analysis.market_matcher._match_quality_flags", return_value=suppression_flags):
                results = await matcher.find_candidates(news)
        finally:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.trade_log.log_match_suppressed = orig_supp

        assert results == [], "suppressed candidate must not be returned to caller"
        assert len(suppressed_calls) == 1, "MATCH_SUPPRESSED must be logged"
        payload = suppressed_calls[0]
        assert payload["ticker"] == "KXMIL-25A"
        assert "single_named_entity_only" in payload["heuristic_flags"]

    @pytest.mark.asyncio
    async def test_suppression_diagnostic_always_logged(self, matcher):
        """MATCH_DIAGNOSTIC is always logged, even when the candidate is suppressed.
        Uses KXMIL-25A so the ticker guard does not block suppression."""
        market = _make_market(
            "KXMIL-25A",
            "Will Trump order military action under the 25th Amendment this year?",
        )
        matcher._cache.get_markets = AsyncMock(return_value=[market])
        news = _make_news("Trump says Iran talks have collapsed again")

        import analysis.market_matcher as mm
        import config as cfg_module
        orig_flag = cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION
        suppression_flags = ["near_threshold_score", "single_named_entity_only"]
        diag_calls = []
        orig_diag = mm.trade_log.log_match_diagnostic
        mm.trade_log.log_match_diagnostic = lambda **kw: diag_calls.append(kw)
        orig_supp = mm.trade_log.log_match_suppressed
        mm.trade_log.log_match_suppressed = lambda **kw: None
        try:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            with patch("analysis.market_matcher._match_quality_flags", return_value=suppression_flags):
                await matcher.find_candidates(news)
        finally:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.trade_log.log_match_diagnostic = orig_diag
            mm.trade_log.log_match_suppressed = orig_supp

        assert len(diag_calls) == 1, "MATCH_DIAGNOSTIC must be logged even for suppressed candidates"

    @pytest.mark.asyncio
    async def test_suppression_does_not_drop_high_quality_match(self, matcher):
        """Candidates passing threshold with multiple overlapping tokens are not suppressed."""
        market = _make_market(
            "KXUKR-1",
            "Will Russia launch a major offensive in Ukraine before July?",
        )
        matcher._cache.get_markets = AsyncMock(return_value=[market])
        # russia + ukraine overlap -> multi-token, not near_threshold
        news = _make_news("Russia launches new attack on Ukraine border")

        import analysis.market_matcher as mm
        import config as cfg_module
        original = cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION
        suppressed_calls = []
        orig_supp = mm.trade_log.log_match_suppressed
        mm.trade_log.log_match_suppressed = lambda **kw: suppressed_calls.append(kw)
        try:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            results = await matcher.find_candidates(news)
        finally:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = original
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = original
            mm.trade_log.log_match_suppressed = orig_supp

        assert results, "high-quality match must not be suppressed"
        assert suppressed_calls == [], "no MATCH_SUPPRESSED event for high-quality match"

    @pytest.mark.asyncio
    async def test_path_b_suppresses_pure_single_entity_without_near_threshold(self, matcher):
        """Path B: single-entity + minimal-overlap is suppressed even WITHOUT near_threshold_score.

        We inject flags without near_threshold_score to isolate Path B behavior.
        Under the original criteria (required near_threshold_score), this match would NOT
        have been suppressed. Under the new criteria, Path B fires on
        single_named_entity_only + minimal_overlap alone.

        Uses KXMIL-25A (no 'trump' in ticker) so the ticker guard cannot block suppression.
        """
        market = _make_market(
            "KXMIL-25A",
            "Will Trump order military action under the 25th Amendment this year?",
        )
        matcher._cache.get_markets = AsyncMock(return_value=[market])
        news = _make_news("Trump says Iran talks have collapsed again")

        import analysis.market_matcher as mm
        import config as cfg_module
        orig_flag = cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION
        # Inject Path B flags WITHOUT near_threshold_score.
        # In the old criteria this match would NOT be suppressed.
        # In the new criteria, Path B must fire.
        path_b_flags = ["low_token_overlap", "minimal_overlap", "single_named_entity_only"]
        suppressed_calls = []
        diag_calls = []
        orig_supp = mm.trade_log.log_match_suppressed
        orig_diag = mm.trade_log.log_match_diagnostic
        mm.trade_log.log_match_suppressed = lambda **kw: suppressed_calls.append(kw)
        mm.trade_log.log_match_diagnostic = lambda **kw: diag_calls.append(kw)
        try:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            with patch("analysis.market_matcher._match_quality_flags", return_value=path_b_flags):
                results = await matcher.find_candidates(news)
        finally:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.trade_log.log_match_suppressed = orig_supp
            mm.trade_log.log_match_diagnostic = orig_diag

        assert results == [], "pure single-entity match must be suppressed by Path B"
        assert len(suppressed_calls) == 1, "MATCH_SUPPRESSED must be logged"
        payload = suppressed_calls[0]
        assert payload["ticker"] == "KXMIL-25A"
        assert "single_named_entity_only" in payload["heuristic_flags"]
        assert "minimal_overlap" in payload["heuristic_flags"]
        # near_threshold_score absent confirms Path B fired, not Path A
        assert "near_threshold_score" not in payload["heuristic_flags"]
        # MATCH_DIAGNOSTIC must still be logged (observability preserved)
        assert len(diag_calls) == 1

    @pytest.mark.asyncio
    async def test_path_b_blocked_by_ticker_guard(self, matcher):
        """Path B respects the ticker guard: single-entity match is NOT suppressed
        when the matched token appears in the market ticker.

        Real-world case: 'trump' headline -> KXTRUMP-26JUN; 'trump' in 'kxtrump-26jun'
        -> candidate must survive (the market is explicitly about Trump).
        """
        market = _make_market(
            "KXTRUMP-26JUN",
            "Will Trump sign a tariff order in June?",
        )
        matcher._cache.get_markets = AsyncMock(return_value=[market])
        news = _make_news("Trump says Iran talks have collapsed again")

        import analysis.market_matcher as mm
        import config as cfg_module
        orig_flag = cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION
        suppressed_calls = []
        orig_supp = mm.trade_log.log_match_suppressed
        orig_diag = mm.trade_log.log_match_diagnostic
        mm.trade_log.log_match_suppressed = lambda **kw: suppressed_calls.append(kw)
        mm.trade_log.log_match_diagnostic = lambda **kw: None
        try:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            results = await matcher.find_candidates(news)
        finally:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.trade_log.log_match_suppressed = orig_supp
            mm.trade_log.log_match_diagnostic = orig_diag

        assert results, "token-in-ticker single-entity match must NOT be suppressed"
        assert suppressed_calls == [], "MATCH_SUPPRESSED must not be logged when token is in ticker"

    @pytest.mark.asyncio
    async def test_multi_token_overlap_not_suppressed_by_path_b(self, matcher):
        """Path B does not fire when overlap contains multiple tokens.

        'Trump announces Iran nuclear breakthrough' -> 'Will Trump sign an Iran deal?'
        overlap = {trump, iran} -> minimal_overlap=False -> Path B cannot fire.
        """
        market = _make_market(
            "KXMIL-1",
            "Will Trump sign an Iran nuclear deal in 2026?",
        )
        matcher._cache.get_markets = AsyncMock(return_value=[market])
        news = _make_news("Trump announces Iran nuclear breakthrough")

        import analysis.market_matcher as mm
        import config as cfg_module
        orig_flag = cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION
        suppressed_calls = []
        orig_supp = mm.trade_log.log_match_suppressed
        orig_diag = mm.trade_log.log_match_diagnostic
        mm.trade_log.log_match_suppressed = lambda **kw: suppressed_calls.append(kw)
        mm.trade_log.log_match_diagnostic = lambda **kw: None
        try:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            results = await matcher.find_candidates(news)
        finally:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.trade_log.log_match_suppressed = orig_supp
            mm.trade_log.log_match_diagnostic = orig_diag

        assert results, "multi-token overlap must NOT be suppressed by Path B"
        assert suppressed_calls == [], "no MATCH_SUPPRESSED for multi-token match"

    @pytest.mark.asyncio
    async def test_suppression_skips_when_token_in_ticker(self, matcher):
        """Suppression does NOT fire when the matched token appears in the market ticker.
        Prevents valid, topic-aligned weak matches from being dropped.
        Real-world case: 'iran' headline -> KXTRUMPIRAN-26MAY01 (ticker contains 'iran')."""
        market = _make_market(
            "KXTRUMPIRAN-26MAY01",
            "Will Trump reach an Iran nuclear deal before May 1?",
        )
        matcher._cache.get_markets = AsyncMock(return_value=[market])
        news = _make_news("Iran War Live Updates: U.S. to Blockade Ships From Iranian Ports")

        import analysis.market_matcher as mm
        import config as cfg_module
        orig_flag = cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION
        # Inject the first 3 suppression conditions so only the ticker guard can block suppression
        suppression_flags = ["near_threshold_score", "single_named_entity_only"]
        suppressed_calls = []
        orig_supp = mm.trade_log.log_match_suppressed
        mm.trade_log.log_match_suppressed = lambda **kw: suppressed_calls.append(kw)
        try:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = True
            with patch("analysis.market_matcher._match_quality_flags", return_value=suppression_flags):
                results = await matcher.find_candidates(news)
        finally:
            cfg_module.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.ENABLE_LOW_QUALITY_MATCH_SUPPRESSION = orig_flag
            mm.trade_log.log_match_suppressed = orig_supp

        assert results, "token-in-ticker match must not be suppressed"
        assert suppressed_calls == [], "MATCH_SUPPRESSED must not be logged when token is in ticker"
