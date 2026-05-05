"""Behavioral integration test for Lever A.1+1.5 `legal` source-class.

Sibling to `tests/test_lever_a1plus1_5_evidence_scorer_legal_weight.py`
(which pins the static weight 0.65 in `_SOURCE_CLASS_QUALITY`). This
test exercises the FULL scorer flow for an Evidence(source_class="legal")
record: synthetic input → score_evidence() → EvidenceScore output, with
assertions on the legal-class weight propagating correctly.

Strict-xfail today (no `legal` entry in `_SOURCE_CLASS_QUALITY`); the
`source_quality()` lookup falls through to `_DEFAULT_QUALITY`. Flips
xpass on the A.1+1.5 deploy commit per spec §3.2.
"""

from __future__ import annotations

import pytest

from analysis.evidence_scorer import score_evidence, source_quality
from analysis.evidence_types import Evidence


_LEGAL_INTEGRATION_XFAIL_REASON = (
    "Lever A.1+1.5 `legal` source-class not yet added to "
    "`_SOURCE_CLASS_QUALITY`. The integration test exercises the full "
    "score_evidence() flow on a synthetic legal-source Evidence record; "
    "today the source_quality() lookup falls through to _DEFAULT_QUALITY. "
    "Trips on the A.1+1.5 deploy commit; remove the marker in the same hunk."
)


def _make_evidence(
    *,
    eid: str,
    source_class: str,
    headline: str,
    implied_p: float = 0.55,
    market_ticker: str = "KXSCOTUS-26JUN-RULE",
) -> Evidence:
    return Evidence(
        evidence_id=eid,
        market_ticker=market_ticker,
        source="VitalLaw.com" if source_class == "legal" else "Reuters",
        source_class=source_class,
        headline=headline,
        ingested_ts="2026-05-15T12:00:00Z",
        implied_probability=implied_p,
        content_hash=f"hash_{eid}",
    )


@pytest.mark.xfail(reason=_LEGAL_INTEGRATION_XFAIL_REASON, strict=True)
def test_legal_source_class_quality_lookup_returns_065():
    """`source_quality("legal")` must return 0.65 post-A.1+1.5 deploy.
    Today returns _DEFAULT_QUALITY (the dict has no `legal` entry; the
    `.get(source_class, _DEFAULT_QUALITY)` fallback fires)."""
    assert source_quality("legal") == pytest.approx(0.65, abs=0.001)


@pytest.mark.xfail(reason=_LEGAL_INTEGRATION_XFAIL_REASON, strict=True)
def test_score_evidence_propagates_legal_weight_when_independent():
    """Full scorer flow: a legal-class Evidence in a fresh window
    (no prior same-class records) should score with original_weight =
    legal-class quality (0.65) per BSR-7's independence + full-weight
    rule.

    Today: score_evidence falls through to _DEFAULT_QUALITY for
    source_class='legal' so original_weight ≠ 0.65.
    """
    legal_ev = _make_evidence(
        eid="ev-legal-1",
        source_class="legal",
        headline="Supreme Court ruling on FISA renewal expected next term",
    )
    score = score_evidence(legal_ev, recent_market_evidence=[])
    assert score.source_class == "legal"
    assert score.quality_score == pytest.approx(0.65, abs=0.001)
    assert score.original_weight == pytest.approx(0.65, abs=0.001)
    assert score.is_independent is True
    assert score.correlation_discount_applied is False


@pytest.mark.xfail(reason=_LEGAL_INTEGRATION_XFAIL_REASON, strict=True)
def test_score_evidence_legal_class_distinct_from_news():
    """A legal-class evidence in a window already populated by a
    news-class evidence should still score as independent (different
    classes per BSR-7 condition 1). Catches a bug where the new
    `legal` entry collides with `news` (e.g., wrong dict key, shared
    value)."""
    news_ev = _make_evidence(
        eid="ev-news-1",
        source_class="news",
        headline="Supreme Court hears FISA arguments",
        implied_p=0.50,
    )
    legal_ev = _make_evidence(
        eid="ev-legal-1",
        source_class="legal",
        headline="In-depth FISA renewal analysis from VitalLaw",
        implied_p=0.55,
    )
    score = score_evidence(legal_ev, recent_market_evidence=[news_ev])
    assert score.is_independent is True, (
        "legal-class evidence should be independent of news-class "
        "evidence (different source_class per BSR-7); a False here "
        "indicates source_class collision in the new dict."
    )
    # Quality preserved through independence.
    assert score.original_weight == pytest.approx(0.65, abs=0.001)


def test_score_evidence_two_legal_class_applies_diminishing_returns():
    """Permanent invariant (not xfail-pinned): per BSR-5, the second
    same-class evidence in a window gets weight/n regardless of which
    class. Two legal-class evidences in window: first at original
    weight q (whatever `legal` resolves to today vs post-deploy);
    second should score at original_weight ~= q / 2.

    Today: passes via _DEFAULT_QUALITY fallback (default/2 < 0.5).
    Post-A.1+1.5: passes via 0.65/2 = 0.325 < 0.5.
    Catches a regression where BSR-5's same-class division silently
    breaks during the dict edit."""
    first_legal = _make_evidence(
        eid="ev-legal-1",
        source_class="legal",
        headline="Earlier VitalLaw FISA analysis",
    )
    second_legal = _make_evidence(
        eid="ev-legal-2",
        source_class="legal",
        headline="Later VitalLaw FISA followup with different framing",
    )
    score = score_evidence(second_legal, recent_market_evidence=[first_legal])
    assert score.is_independent is False, (
        "second legal-class evidence in a window with another legal-class "
        "should be classified as same-class, not independent"
    )
    assert score.same_class_count == 1
    # Weight = 0.65 / (1 + 1) = 0.325. Use rough tolerance because BSR-7's
    # ngram-overlap branch may also fire if headlines are too similar; the
    # assertion is on the SAME-CLASS-DIVISION shape, not the exact value.
    assert score.original_weight < 0.5, (
        f"original_weight = {score.original_weight}; expected < 0.5 due to "
        f"same-class diminishing returns (0.65 / 2 = 0.325). A weight near "
        f"0.65 indicates BSR-5 didn't fire; near 0 indicates n-gram "
        f"duplicate-detection over-fired."
    )


def test_existing_class_quality_lookups_unchanged_today():
    """Positive control: existing source-class lookups (official/news/
    analysis/market) continue to return their current values. Catches
    a regression where the A.1+1.5 dict edit accidentally re-tunes
    existing entries while adding `legal`."""
    expected = {
        "official": 0.85,
        "news":     0.70,
        "analysis": 0.60,
        "market":   0.55,
    }
    for cls, val in expected.items():
        actual = source_quality(cls)
        assert actual == pytest.approx(val, abs=0.001), (
            f"source_quality({cls!r}) = {actual}; expected {val}. "
            f"A.1+1.5 deploy must not re-tune existing weights."
        )
