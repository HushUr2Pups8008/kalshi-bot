"""
Tests for analysis/market_matcher.py

Covers: Jaccard similarity (_similarity) correctness, token set behaviour,
        stopword removal, punctuation stripping.
"""

import pytest

from analysis.market_matcher import _tokenize, _similarity


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
