"""Tests for `scripts/simulations/lever_a1_classifier_counterfactual.py`.

Pre-staged during the PROFIT-PHASE2-001 soak per the Lever A Stage A.1 spec
§4 sizing methodology. The harness pins:

  1. The canonical-source distribution under the *post-fix* classifier
     (the reference implementation `classify_post_fix` in the harness).
  2. The pre-fix → post-fix delta — every currently-misclassified canonical
     source flips to `official` / `news` correctly.
  3. xfail-strict against the live production classifier: when the
     `_source_class_for_evidence` token-list patch lands, the production
     and reference classifiers must produce identical output on every
     canonical source. This test xfails today (production is pre-fix) and
     xpasses post-fix.
"""

from __future__ import annotations

import pytest

from scripts.simulations.lever_a1_classifier_counterfactual import (
    _CANONICAL_SOURCES,
    classify_post_fix,
    distribution,
)
from main import _source_class_for_evidence as classify_production


def classify_pre_fix(source: str) -> str:
    """Frozen pre-fix classifier reference for the counterfactual delta tests."""
    source_text = (source or "").strip()
    lower = source_text.lower()
    if source_text.startswith("r/"):
        return "social"
    if lower == "price_fade" or lower.startswith("kalshi://"):
        return "market"
    if any(token in lower for token in (
        ".gov",
        "white house",
        "state department",
        "defense department",
        "federal reserve",
        "supreme court",
        "congress",
        "parliament",
        "ministry",
        "official",
    )):
        return "official"
    if source_text.endswith(" - Google News") or source_text.endswith(" - BingNews"):
        return "news"
    if any(token in lower for token in (
        "reuters",
        "associated press",
        "ap news",
        "bbc",
        "nyt",
        "guardian",
        "al jazeera",
        "france 24",
        "deutsche welle",
        "defense one",
        "foreign policy",
        "politico",
        "politics",
        "just in news",
    )):
        return "news"
    return "other"


_LEVER_A1_HARNESS_XFAIL_REASON = (
    "Lever A.1 classifier patch not yet landed. Production "
    "`main.py:_source_class_for_evidence` still uses the pre-fix token list. "
    "Once §2.1 + §2.2 expansions land per "
    "docs/superpowers/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md, "
    "production and reference classifiers must produce identical output."
)


def test_post_fix_canonical_sources_match_expected_distribution():
    """Pin the post-fix distribution on the canonical source list. The
    reference implementation in the harness must produce this exact split."""
    post = distribution(_CANONICAL_SOURCES, classify_post_fix)
    # Canonical-source list at draft time, post-fix classification:
    #   official: Department of War, UN News, Press releases (EC), IAEA,
    #             White House                                                  = 5
    #   news:     Defense News, Breaking Defense, Reuters, Associated Press,
    #             BBC, Politico, Al Jazeera, The Guardian, France 24,
    #             Defense One, Trump Iran deal - Google News,
    #             Iran ceasefire - BingNews                                    = 12
    #   social:   r/worldnews, r/politics                                      = 2
    #   market:   price_fade                                                   = 1
    #   other:    Iran International, Times of Israel, The Kyiv Independent,
    #             bellingcat, Some Random Blog, anonymous wire                 = 6
    # Total: 26. (Iran International / Times of Israel / Kyiv Independent /
    # bellingcat lack a news-token match in the current classifier — they
    # would benefit from a follow-up A.1 step but stay `other` under the
    # token-list-only fix scoped here.)
    assert sum(post.values()) == len(_CANONICAL_SOURCES) == 26
    assert post["official"] == 5, f"expected 5 official; got {post['official']} = {dict(post)}"
    assert post["news"] == 12, f"expected 12 news; got {post['news']} = {dict(post)}"
    assert post["social"] == 2
    assert post["market"] == 1
    assert post["other"] == 6


def test_pre_fix_misclassifies_six_canonical_sources_today():
    """Today the pre-fix classifier should bucket the six known-misclassified
    sources as `other` (or `news` for industry wires that miss the token list).
    This test documents the bug shape; not xfail because pre-fix behavior is
    factual today."""
    misclassified = (
        "Department of War News Feed",
        "UN News - Global perspective Human stories",
        "Press releases - RSS",
        "Top Stories From the International Atomic Energy Agency",
    )
    for src in misclassified:
        # Each lands in `other` today (the catch-all fallback).
        assert classify_pre_fix(src) == "other", (
            f"pre-fix classifier should bucket {src!r} as `other` today; got "
            f"{classify_pre_fix(src)!r}"
        )
    # The two industry wires are also `other` today (no news-token match).
    assert classify_pre_fix("Defense News") == "other"
    assert classify_pre_fix("Breaking Defense") == "other"


def test_post_fix_recovers_misclassified_sources():
    """The post-fix reference classifier moves the 4 official + 2 industry
    sources to their correct classes."""
    assert classify_post_fix("Department of War News Feed") == "official"
    assert classify_post_fix("UN News - Global perspective Human stories") == "official"
    assert classify_post_fix("Press releases - RSS") == "official"
    assert classify_post_fix("Top Stories From the International Atomic Energy Agency") == "official"
    assert classify_post_fix("Defense News") == "news"
    assert classify_post_fix("Breaking Defense") == "news"


def test_pre_fix_to_post_fix_delta_is_six_recoveries():
    """Aggregate lift on the canonical source list: 6 sources move out of
    `other` (4 → official, 2 → news). `official` count rises by 4; `news`
    count rises by 2; `other` count drops by 6."""
    pre = distribution(_CANONICAL_SOURCES, classify_pre_fix)
    post = distribution(_CANONICAL_SOURCES, classify_post_fix)
    assert post["official"] - pre["official"] == 4, (
        f"official delta must be +4; got {post['official'] - pre['official']}"
    )
    assert post["news"] - pre["news"] == 2, (
        f"news delta must be +2; got {post['news'] - pre['news']}"
    )
    assert pre["other"] - post["other"] == 6, (
        f"other delta must be -6; got {post['other'] - pre['other']}"
    )


_MISCLASSIFIED_SOURCES_TODAY: tuple[str, ...] = (
    "Department of War News Feed",
    "UN News - Global perspective Human stories",
    "Press releases - RSS",
    "Top Stories From the International Atomic Energy Agency",
    "Defense News",
    "Breaking Defense",
)


@pytest.mark.xfail(reason=_LEVER_A1_HARNESS_XFAIL_REASON, strict=True)
@pytest.mark.parametrize("source", _MISCLASSIFIED_SOURCES_TODAY, ids=list(_MISCLASSIFIED_SOURCES_TODAY))
def test_production_classifier_matches_reference_post_fix_for_misclassified_sources(source: str):
    """Once the Lever A.1 patch lands, production classifier output must
    equal the reference post-fix classifier output for the 6 sources whose
    pre/post outputs diverge today. The other ~20 canonical sources already
    classify identically pre/post and would xpass today (so they cannot be
    strict-xfail-marked)."""
    assert classify_production(source) == classify_post_fix(source)


def test_production_already_matches_reference_for_unchanged_sources():
    """Positive control: the canonical sources whose pre/post classification
    is identical today must continue to match. Catches a regression that
    breaks the existing token-list while the Lever A.1 fix is in flight."""
    unchanged = tuple(s for s in _CANONICAL_SOURCES if s not in _MISCLASSIFIED_SOURCES_TODAY)
    for src in unchanged:
        assert classify_production(src) == classify_post_fix(src), (
            f"pre/post divergence on a previously-aligned source {src!r}: "
            f"production={classify_production(src)!r} post={classify_post_fix(src)!r}"
        )
