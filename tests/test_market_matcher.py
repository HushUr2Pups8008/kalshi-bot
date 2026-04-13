"""
Tests for analysis/market_matcher.py

Covers: tokenization, similarity scoring, and candidate matching behaviour.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config as _cfg_module
from analysis.market_matcher import MarketMatcher, _tokenize, _similarity
from feeds import NewsItem
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


def _make_news(headline: str, body: str = ""):
    return NewsItem(
        headline=headline,
        url="https://example.com/story",
        source="Reuters",
        published=datetime.now(timezone.utc),
        body=body,
        item_id="news-1",
    )


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


# ---------------------------------------------------------------------------
# Low-quality match suppression
# ---------------------------------------------------------------------------

class TestLowQualityMatchSuppression:
    """Suppression is config-gated (ENABLE_LOW_QUALITY_MATCH_SUPPRESSION).
    Criteria: near_threshold_score AND (minimal_overlap OR single_named_entity_only)
              AND NOT any matched token appears in the market ticker.
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
