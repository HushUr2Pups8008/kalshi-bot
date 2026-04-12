"""
Tests for feeds/dedup.py

Covers: normalization, duplicate detection, TTL eviction, and max-cache bounds.
"""

from unittest.mock import patch

import pytest

from feeds.dedup import HeadlineDedup, _normalize


class TestNormalize:
    def test_normalize_lowercases_and_sorts_unique_tokens(self):
        normalized = _normalize("Iran Attack! Iran attack?")
        assert normalized == "attack iran"

    def test_normalize_strips_punctuation(self):
        normalized = _normalize("Ceasefire, deal signed.")
        assert normalized == "ceasefire deal signed"


class TestHeadlineDedup:
    def test_first_headline_is_not_duplicate(self):
        dedup = HeadlineDedup()
        assert dedup.is_duplicate("Russia launches new strike") is False

    def test_exact_repeat_is_duplicate(self):
        dedup = HeadlineDedup()
        dedup.is_duplicate("Russia launches new strike")
        assert dedup.is_duplicate("Russia launches new strike") is True

    def test_reordered_tokens_are_duplicate_after_normalization(self):
        dedup = HeadlineDedup()
        dedup.is_duplicate("Iran missile strike")
        assert dedup.is_duplicate("strike missile iran") is True

    def test_distinct_headline_is_not_duplicate(self):
        dedup = HeadlineDedup()
        dedup.is_duplicate("Russia launches new strike")
        assert dedup.is_duplicate("Central bank holds interest rates steady") is False

    def test_empty_normalized_headline_is_not_duplicate(self):
        dedup = HeadlineDedup()
        assert dedup.is_duplicate("!!!") is False

    def test_ttl_expiry_allows_reuse_of_old_headline(self):
        dedup = HeadlineDedup(ttl=10)
        with patch("feeds.dedup.time.monotonic", side_effect=[100.0, 100.0, 105.0, 111.0, 111.0]):
            assert dedup.is_duplicate("Iran missile strike") is False
            assert dedup.is_duplicate("Iran missile strike") is True
            assert dedup.is_duplicate("Iran missile strike") is False

    def test_max_cache_evicts_oldest_headline(self):
        dedup = HeadlineDedup(max_cache=2, ttl=10_000)
        with patch("feeds.dedup.time.monotonic", side_effect=[1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0]):
            assert dedup.is_duplicate("Headline one") is False
            assert dedup.is_duplicate("Headline two") is False
            assert dedup.is_duplicate("Headline three") is False
            # "Headline one" should have been evicted when the third was inserted.
            assert dedup.is_duplicate("Headline one") is False

    @pytest.mark.parametrize(
        ("first", "second", "threshold", "expected_duplicate"),
        [
            ("Iran missile strike", "Iran missile strike", 100, True),
            ("Iran missile strike", "Iran missile strikes", 100, False),
            ("Iran missile strike", "Iran missile strikes", 80, True),
        ],
    )
    def test_threshold_controls_duplicate_sensitivity(self, first, second, threshold, expected_duplicate):
        dedup = HeadlineDedup(threshold=threshold, ttl=10_000)
        dedup.is_duplicate(first)
        assert dedup.is_duplicate(second) is expected_duplicate
