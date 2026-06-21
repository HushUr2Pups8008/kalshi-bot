"""Tests for `scripts/simulations/lever_a1_classifier_counterfactual.py`.

Pre-staged during the PROFIT-PHASE2-001 soak per the Lever A Stage A.1 spec
§4 sizing methodology. The harness pins:

  1. The canonical-source distribution under the *post-fix* classifier
     (the reference implementation `classify_post_fix` in the harness).
  2. The historical pre-fix → post-fix delta — every previously-
     misclassified canonical source flips to `official` / `news` correctly.
  3. Production parity: the live `_source_class_for_evidence` matches
     the reference `classify_post_fix` on every canonical source after
     the Wave-1 Lever A.1 deploy.
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
    """Hardcoded reference of the *pre-Wave-1* classifier.

    Mirrors `main.py:_source_class_for_evidence` as it stood before the
    PROFIT-EDGE-004 Lever A.1 token-list expansion. Used here so the
    historical pre-fix → post-fix delta tests remain meaningful after
    the production classifier landed.
    """
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


def test_post_fix_canonical_sources_match_expected_distribution():
    """Pin the post-fix distribution on the canonical source list. The
    reference implementation in the harness must produce this exact split.

    PROFIT-EDGE-006 (2026-05-24): 3 sources moved from `other` → `regional`
    (Iran International, Times of Israel, The Kyiv Independent). The
    `other` bucket drops from 6 → 3; a new `regional` bucket holds 3.
    """
    post = distribution(_CANONICAL_SOURCES, classify_post_fix)
    # Post-PROFIT-EDGE-006 canonical-source-list distribution:
    #   official: Department of War, UN News, Press releases (EC), IAEA,
    #             White House                                                  = 5
    #   news:     Defense News, Breaking Defense, Reuters, Associated Press,
    #             BBC, Politico, Al Jazeera, The Guardian, France 24,
    #             Defense One, Trump Iran deal - Google News,
    #             Iran ceasefire - BingNews                                    = 12
    #   social:   r/worldnews, r/politics                                      = 2
    #   market:   price_fade                                                   = 1
    #   regional: Iran International, Times of Israel, The Kyiv Independent    = 3
    #   other:    bellingcat, Some Random Blog, anonymous wire                 = 3
    # Total: 26.
    assert sum(post.values()) == len(_CANONICAL_SOURCES) == 26
    assert post["official"] == 5, f"expected 5 official; got {post['official']} = {dict(post)}"
    assert post["news"] == 12, f"expected 12 news; got {post['news']} = {dict(post)}"
    assert post["social"] == 2
    assert post["market"] == 1
    assert post["regional"] == 3, (
        f"PROFIT-EDGE-006: expected 3 regional; got {post.get('regional', 0)} = {dict(post)}"
    )
    assert post["other"] == 3, (
        f"PROFIT-EDGE-006: expected 3 other (down from 6); got {post['other']} = {dict(post)}"
    )


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


def test_pre_fix_to_post_fix_delta_recovers_nine_sources():
    """Aggregate lift on the canonical source list, cumulative through
    PROFIT-EDGE-006: 9 sources move out of `other` total.
      - 4 → official (Lever A.1: Department of War, UN News, Press
        releases (EC), IAEA)
      - 2 → news (Lever A.1: Defense News, Breaking Defense)
      - 3 → regional (PROFIT-EDGE-006: Iran International, Times of
        Israel, The Kyiv Independent)
    `official` +4, `news` +2, `regional` +3, `other` -9.
    """
    pre = distribution(_CANONICAL_SOURCES, classify_pre_fix)
    post = distribution(_CANONICAL_SOURCES, classify_post_fix)
    assert post["official"] - pre["official"] == 4, (
        f"official delta must be +4; got {post['official'] - pre['official']}"
    )
    assert post["news"] - pre["news"] == 2, (
        f"news delta must be +2; got {post['news'] - pre['news']}"
    )
    assert post.get("regional", 0) - pre.get("regional", 0) == 3, (
        f"regional delta must be +3 (PROFIT-EDGE-006); got "
        f"{post.get('regional', 0) - pre.get('regional', 0)}"
    )
    assert pre["other"] - post["other"] == 9, (
        f"other delta must be -9 (4 official + 2 news + 3 regional); "
        f"got {pre['other'] - post['other']}"
    )


_MISCLASSIFIED_SOURCES_TODAY: tuple[str, ...] = (
    "Department of War News Feed",
    "UN News - Global perspective Human stories",
    "Press releases - RSS",
    "Top Stories From the International Atomic Energy Agency",
    "Defense News",
    "Breaking Defense",
)


@pytest.mark.parametrize("source", _MISCLASSIFIED_SOURCES_TODAY, ids=list(_MISCLASSIFIED_SOURCES_TODAY))
def test_production_classifier_matches_reference_post_fix_for_misclassified_sources(source: str):
    """Post-Wave-1 Lever A.1 deploy: production classifier output must
    equal the reference post-fix classifier output for the 6 sources whose
    pre/post outputs diverged before the deploy."""
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


# ── PROFIT-EDGE-006 — source-class taxonomy expansion ───────────────────────────
#
# Audit of 24-day live event log surfaced 2,843 events from 39 distinct
# sources currently bucketed as `other` despite being legitimate news.
# Top offenders (events in log): Times of Israel=836, Kyiv Post=778,
# Kyiv Independent=444, Iran International=143, AOL=126, WaPo=107, MSN=47.
#
# Fix: introduce `regional` class for foreign-bureau outlets + expand the
# `news` token list to cover major US/UK publications previously missed.
# `regional` is distinct from `news` so a dossier mixing US-domestic +
# foreign regional sources surfaces 2 classes for G2, reflecting genuine
# independence of coverage.


_PROFIT_EDGE_006_REGIONAL_SOURCES: tuple[str, ...] = (
    "The Times of Israel",
    "Kyiv Post",
    "The Kyiv Independent",
    "Iran International",
    "Anadolu Ajansı",
    "Shafaq News",
    "Asia Times",
    "Haaretz",
    "Jerusalem Post",
)

_PROFIT_EDGE_006_NEWS_ADDITIONS: tuple[str, ...] = (
    "The Washington Post",
    "The New York Times",
    "USA Today",
    "MSN",
    "AOL.com",
    "Newsweek",
    "The Independent",
    "Bloomberg",
    "Axios",
    "The Hill",
    "CNN",
    "ABC News",
    "NBC News",
    "CBS News",
    "Fox News",
    "The Atlantic",
)


@pytest.mark.parametrize("source", _PROFIT_EDGE_006_REGIONAL_SOURCES,
                         ids=list(_PROFIT_EDGE_006_REGIONAL_SOURCES))
def test_profit_edge_006_regional_sources_classified_as_regional(source: str):
    """Each named regional-bureau source must classify as `regional`,
    not `news` or `other`. The point of the new class is to surface as
    a distinct class in G2's diversity check; misclassifying as `news`
    would defeat the purpose."""
    assert classify_production(source) == "regional", (
        f"PROFIT-EDGE-006: {source!r} must classify as `regional`; "
        f"got {classify_production(source)!r}"
    )


@pytest.mark.parametrize("source", _PROFIT_EDGE_006_NEWS_ADDITIONS,
                         ids=list(_PROFIT_EDGE_006_NEWS_ADDITIONS))
def test_profit_edge_006_news_additions_classified_as_news(source: str):
    """Each newly-added US/UK news source must classify as `news`,
    not the pre-fix `other` fallback. Captures the 29% misclassification
    rate observed in 24-day live log audit."""
    assert classify_production(source) == "news", (
        f"PROFIT-EDGE-006: {source!r} must classify as `news`; "
        f"got {classify_production(source)!r}"
    )


def test_profit_edge_006_g2_diversity_dossier_mixing_us_and_regional_passes():
    """Load-bearing contract: a dossier with mixed US-domestic news and
    foreign-bureau regional sources must surface 2 distinct classes for
    G2's diversity check. Pre-fix, both bucketed as `news` or `other`
    (single class) → G2 fail. Post-fix, `news` + `regional` (2 classes)
    → G2 pass. This is the mechanism by which PROFIT-EDGE-006 recovers
    historical G2-blocked BDs."""
    us_news_class = classify_production("The Washington Post")
    regional_class = classify_production("The Times of Israel")
    assert us_news_class == "news"
    assert regional_class == "regional"
    assert us_news_class != regional_class, (
        "G2 will fail if these collapse to the same class — defeats the "
        "purpose of the PROFIT-EDGE-006 split"
    )


def test_profit_edge_006_pre_fix_misclassifies_named_sources():
    """Documents the bug shape: the pre-fix classifier (no PROFIT-EDGE-006
    extensions) bucketed all the named sources as `other`."""
    for src in (_PROFIT_EDGE_006_REGIONAL_SOURCES
                + _PROFIT_EDGE_006_NEWS_ADDITIONS):
        assert classify_pre_fix(src) == "other", (
            f"pre-PROFIT-EDGE-006 classifier should bucket {src!r} as "
            f"`other`; got {classify_pre_fix(src)!r}"
        )
