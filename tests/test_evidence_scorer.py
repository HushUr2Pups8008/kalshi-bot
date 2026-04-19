"""Tests for analysis/evidence_scorer.py — S2.3."""

from __future__ import annotations

import pytest

from analysis.evidence_scorer import (
    NGRAM_OVERLAP_THRESHOLD,
    ngram_overlap,
    score_evidence,
    source_quality,
)
from analysis.evidence_types import Evidence


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _evidence(
    evidence_id: str = "ev-1",
    source_class: str = "news",
    headline: str = "Market moves on new data",
    implied_probability: float = 0.65,
    content_hash: str = "abc123",
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        market_ticker="KXTEST-1",
        source="reuters",
        source_class=source_class,
        headline=headline,
        ingested_ts="2026-04-19T12:00:00+00:00",
        implied_probability=implied_probability,
        content_hash=content_hash,
    )


# ── ngram_overlap ─────────────────────────────────────────────────────────────

class TestNgramOverlap:
    def test_identical_headlines_return_one(self):
        h = "Federal Reserve cuts interest rates by 50 basis points"
        assert ngram_overlap(h, h) == pytest.approx(1.0)

    def test_completely_different_return_zero(self):
        assert ngram_overlap("apple orange banana", "car truck bus") == pytest.approx(0.0)

    def test_partial_overlap(self):
        a = "Federal Reserve cuts rates today"
        b = "Federal Reserve raises rates next month"
        score = ngram_overlap(a, b)
        assert 0.0 < score < 1.0

    def test_short_headline_below_ngram_size(self):
        # Headlines with fewer than 3 words fall back to unigrams — should not raise.
        score = ngram_overlap("yes", "yes vote")
        assert 0.0 <= score <= 1.0

    def test_empty_string_returns_zero(self):
        assert ngram_overlap("", "something here") == pytest.approx(0.0)

    def test_case_insensitive(self):
        assert ngram_overlap(
            "Fed Cuts Rates", "fed cuts rates"
        ) == pytest.approx(1.0)

    def test_punctuation_stripped(self):
        assert ngram_overlap(
            "Fed cuts rates!", "Fed cuts rates"
        ) == pytest.approx(1.0)


# ── source_quality ────────────────────────────────────────────────────────────

class TestSourceQuality:
    def test_official_highest(self):
        assert source_quality("official") > source_quality("news")

    def test_news_above_social(self):
        assert source_quality("news") > source_quality("social")

    def test_social_above_other(self):
        assert source_quality("social") > source_quality("other")

    def test_unknown_class_returns_default(self):
        q = source_quality("satellite_image")
        assert 0.0 < q <= source_quality("other")

    def test_all_known_classes_in_range(self):
        for cls in ("official", "news", "analysis", "market", "social", "other"):
            q = source_quality(cls)
            assert 0.0 < q <= 1.0, f"Quality out of range for {cls}: {q}"


# ── score_evidence — independence ─────────────────────────────────────────────

class TestIndependence:
    def test_first_evidence_is_independent(self):
        score = score_evidence(_evidence(), [])
        assert score.is_independent is True

    def test_different_class_no_overlap_is_independent(self):
        recent = [_evidence(source_class="news", headline="Something unrelated at all")]
        new_ev = _evidence(source_class="official", headline="Totally different government ruling")
        score = score_evidence(new_ev, recent)
        assert score.is_independent is True

    def test_same_class_not_independent(self):
        recent = [_evidence(source_class="news", headline="Completely different topic")]
        new_ev = _evidence(source_class="news", headline="Completely different topic too")
        score = score_evidence(new_ev, recent)
        assert score.is_independent is False

    def test_different_class_high_overlap_not_independent(self):
        headline = "Federal Reserve cuts rates by 50 basis points today"
        recent = [_evidence(source_class="news", headline=headline)]
        new_ev = _evidence(source_class="official", headline=headline)
        score = score_evidence(new_ev, recent)
        assert score.is_independent is False

    def test_overlap_just_below_threshold_is_independent(self):
        # Nearly identical but different class and overlap < threshold.
        a = "the bank of england raised interest rates unexpectedly"
        b = "the federal reserve cut interest rates unexpectedly"
        overlap = ngram_overlap(a, b)
        if overlap < NGRAM_OVERLAP_THRESHOLD:
            recent = [_evidence(source_class="news", headline=a)]
            new_ev = _evidence(source_class="official", headline=b)
            score = score_evidence(new_ev, recent)
            assert score.is_independent is True


# ── score_evidence — duplicate detection ─────────────────────────────────────

class TestDuplicateDetection:
    def test_same_hash_is_duplicate(self):
        recent = [_evidence(evidence_id="ev-old", content_hash="hash1")]
        new_ev = _evidence(evidence_id="ev-new", content_hash="hash1")
        score = score_evidence(new_ev, recent)
        assert score.is_duplicate is True

    def test_same_hash_different_id_is_duplicate(self):
        recent = [_evidence(evidence_id="ev-1", content_hash="deadbeef")]
        new_ev = _evidence(evidence_id="ev-2", content_hash="deadbeef")
        assert score_evidence(new_ev, recent).is_duplicate is True

    def test_different_hash_not_duplicate(self):
        recent = [_evidence(evidence_id="ev-1", content_hash="aaa")]
        new_ev = _evidence(evidence_id="ev-2", content_hash="bbb")
        assert score_evidence(new_ev, recent).is_duplicate is False

    def test_duplicate_is_not_independent(self):
        recent = [_evidence(evidence_id="ev-old", source_class="news", content_hash="h")]
        new_ev = _evidence(evidence_id="ev-new", source_class="official", content_hash="h")
        score = score_evidence(new_ev, recent)
        assert score.is_duplicate is True
        assert score.is_independent is False


# ── score_evidence — weight and diminishing returns ───────────────────────────

class TestWeightAndDiminishingReturns:
    def test_independent_weight_equals_quality(self):
        score = score_evidence(_evidence(source_class="news"), [])
        assert score.original_weight == pytest.approx(score.quality_score)

    def test_independent_no_correlation_discount(self):
        score = score_evidence(_evidence(), [])
        assert score.correlation_discount_applied is False

    def test_second_same_class_has_half_weight(self):
        recent = [_evidence(evidence_id="ev-1", source_class="news",
                            headline="Different headline entirely here")]
        new_ev = _evidence(evidence_id="ev-2", source_class="news",
                           headline="Another completely different topic")
        score = score_evidence(new_ev, recent)
        assert score.same_class_count == 1
        assert score.original_weight == pytest.approx(score.quality_score / 2)

    def test_third_same_class_has_third_weight(self):
        recent = [
            _evidence(evidence_id="ev-1", source_class="social", headline="First topic"),
            _evidence(evidence_id="ev-2", source_class="social", headline="Second topic"),
        ]
        new_ev = _evidence(evidence_id="ev-3", source_class="social", headline="Third topic")
        score = score_evidence(new_ev, recent)
        assert score.same_class_count == 2
        assert score.original_weight == pytest.approx(score.quality_score / 3)

    def test_same_class_applies_correlation_discount(self):
        recent = [_evidence(source_class="news", headline="Something else")]
        new_ev = _evidence(source_class="news", headline="Another thing")
        score = score_evidence(new_ev, recent)
        assert score.correlation_discount_applied is True

    def test_same_class_count_zero_when_no_recent(self):
        score = score_evidence(_evidence(source_class="official"), [])
        assert score.same_class_count == 0


# ── score_evidence — field pass-through ──────────────────────────────────────

class TestFieldPassthrough:
    def test_evidence_id_carried(self):
        score = score_evidence(_evidence(evidence_id="ev-xyz"), [])
        assert score.evidence_id == "ev-xyz"

    def test_source_class_carried(self):
        score = score_evidence(_evidence(source_class="official"), [])
        assert score.source_class == "official"

    def test_implied_probability_carried(self):
        score = score_evidence(_evidence(implied_probability=0.73), [])
        assert score.implied_probability == pytest.approx(0.73)

    def test_quality_score_matches_source_class(self):
        score = score_evidence(_evidence(source_class="official"), [])
        assert score.quality_score == pytest.approx(source_quality("official"))
