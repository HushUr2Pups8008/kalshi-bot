"""Pre-load harness for Lever A.1+ first-feed config addition.

Spec: docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md §3.1
Status: pre-loaded during PROFIT-PHASE2-001 soak; lands as part of
A.1+1 deploy (Wave 2, post-soak day 14+). Pins that at least one
specialist-analyst URL appears in `config.py:RSS_FEEDS`.

Codex's 2026-05-03 candidate-feed sizing audit (`docs/governance/
2026-05-03-lever-a1-plus-candidate-feed-sizing.md`, commit `2a15d55`)
ranked specialist analyst as the empirically-validated first-feed
class (21 OPP + 3/3 historical PAPER_TRADE on the 13-day archive).
"""

from __future__ import annotations

import pytest


_A1PLUS_FEED_CONFIG_XFAIL_REASON = (
    "Lever A.1+1 specialist-analyst feed not yet added to config.py:RSS_FEEDS. "
    "Lands during Wave-2 post-soak deploy per the A.1+ spec §3.1; this xfail "
    "trips strictly only on that landing commit."
)


_CANDIDATE_SPECIALIST_ANALYST_URLS = (
    "warontherocks.com",
    "csis.org",
    "understandingwar.org",  # Institute for the Study of War
    "cfr.org",                # Council on Foreign Relations
    "atlanticcouncil.org",
)


@pytest.mark.xfail(reason=_A1PLUS_FEED_CONFIG_XFAIL_REASON, strict=True)
def test_at_least_one_specialist_analyst_url_in_rss_feeds():
    """Pin the A.1+1 deploy outcome: at least one specialist-analyst URL
    is added to `config.py:RSS_FEEDS`. The exact URL chosen at deploy
    time may differ from this list (operator may pick alternates if a
    candidate is paywall-gated or rate-limited at probe time), so the
    test passes if ANY canonical specialist domain appears."""
    from config import RSS_FEEDS
    matches = [
        url for url in RSS_FEEDS
        if any(domain in url for domain in _CANDIDATE_SPECIALIST_ANALYST_URLS)
    ]
    assert matches, (
        "no specialist-analyst URL detected in config.py:RSS_FEEDS. "
        "Lever A.1+1 deploy must add at least one of "
        f"{_CANDIDATE_SPECIALIST_ANALYST_URLS}. "
        "If a different specialist source was chosen at deploy time, "
        "extend `_CANDIDATE_SPECIALIST_ANALYST_URLS` in this test to "
        "include its domain rather than removing the pin."
    )


def test_existing_specialist_analyst_feeds_unchanged_today():
    """Positive control: existing specialist-analyst URLs (Kyiv Post,
    Iran International, bellingcat) remain in RSS_FEEDS today. Codex's
    audit credited these specifically for the historical 3 PAPER_TRADE
    events. Catches a regression that accidentally removes them while
    the A.1+ deploy is in flight."""
    from config import RSS_FEEDS
    existing = ("kyivindependent.com", "kyivpost.com", "iranintl.com", "bellingcat.com", "timesofisrael.com")
    found = {
        domain: any(domain in url for url in RSS_FEEDS)
        for domain in existing
    }
    # At least 3 of these should remain — the bot has been polling them
    # throughout the soak. Allow for one optional removal in case an
    # operator deliberately drops a non-performing source.
    found_count = sum(found.values())
    assert found_count >= 3, (
        f"existing specialist-analyst feeds in RSS_FEEDS: {found}. "
        f"At least 3 must remain to preserve the historical signal "
        f"that produced 3/3 PAPER_TRADE on the 13-day archive."
    )