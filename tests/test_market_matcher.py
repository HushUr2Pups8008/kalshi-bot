"""
Tests for analysis/market_matcher.py

Covers: tokenization, similarity scoring, and candidate matching behaviour.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

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
