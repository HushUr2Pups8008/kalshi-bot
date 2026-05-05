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
    class-level audit credited these specifically for the historical
    21 OPP events. Catches a regression that accidentally removes them
    while the A.1+ deploy is in flight.

    NOTE: per-source audit (`docs/governance/2026-05-03-lever-a1-plus-
    specialist-analyst-per-source-sizing.md`) found 0/18 PAPER_TRADE
    conversion across this list — they generate match candidates but
    do not clear EV threshold. The 3/3 historical PAPER_TRADE all came
    from `VitalLaw.com` (see the `test_vital_law_feed_present_post_a1plus`
    xfail below, which pins re-onboarding of the missing load-bearing
    source). Keep this positive control as a regression net for the
    OPP-volume sources, not the PAPER_TRADE-producing source."""
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
        f"At least 3 must remain to preserve the historical OPP-volume "
        f"signal on the 13-day archive."
    )


_VITAL_LAW_XFAIL_REASON = (
    "Lever A.1+1 vital-law re-onboarding pending. Per-source audit "
    "(docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-"
    "per-source-sizing.md) found `VitalLaw.com` produced 3/3 of the "
    "13-day archive's PAPER_TRADE in the specialist_analyst class. "
    "VitalLaw was polled on the Mac archive instance but is NOT in "
    "the canonical config.py:RSS_FEEDS. Re-onboarding it (or any "
    "vital_law-niche analogue: Politico Pro / Lawfare / Just Security "
    "/ Reuters Legal / SCOTUSblog) is a high-priority A.1+1 deploy "
    "candidate. This xfail trips strictly on the re-onboarding commit."
)


@pytest.mark.xfail(reason=_VITAL_LAW_XFAIL_REASON, strict=True)
def test_vital_law_or_legal_analyst_feed_present_post_a1plus():
    """Pin the post-A.1+1 outcome that at least one vital_law-niche
    legal/regulatory analyst feed appears in `config.py:RSS_FEEDS`.

    `VitalLaw.com` is the load-bearing source: 100% of historical
    PAPER_TRADE on the 13-day Mac archive came from it. The current
    canonical config has no vital_law-niche feed. The operator may
    pick `VitalLaw.com` itself, or a vital-law-class analogue — the
    test passes if ANY canonical legal-analyst domain appears.

    Allowed domains include `VitalLaw.com` (the original source), plus
    the analogues recommended in the per-source audit. If a different
    legal-analyst source is chosen at deploy time, extend
    `_LEGAL_ANALYST_DOMAINS` rather than removing the pin."""
    from config import RSS_FEEDS
    matches = [
        url for url in RSS_FEEDS
        if any(domain in url.lower() for domain in _LEGAL_ANALYST_DOMAINS)
    ]
    assert matches, (
        "no vital_law-niche / legal-analyst URL detected in "
        "config.py:RSS_FEEDS. Lever A.1+1 should re-onboard at least "
        f"one of {_LEGAL_ANALYST_DOMAINS}, since VitalLaw.com produced "
        "100% of historical PAPER_TRADE in the specialist_analyst class. "
        "If a different legal-analyst source was chosen at deploy time, "
        "extend `_LEGAL_ANALYST_DOMAINS` in this test to include its "
        "domain rather than removing the pin."
    )


_LEGAL_ANALYST_DOMAINS = (
    "vitallaw.com",
    "vital-law.com",
    "politicopro.com",
    "politico.com/rss/legal",
    "politico.com/news/legal",
    "lawfaremedia.org",
    "justsecurity.org",
    "scotusblog.com",
    "reuters.com/legal",
    "reutersagency.com/en/reutersbest/legal",
)


_BRANCH_C_PRIMARY_DOMAINS = (
    "lawfaremedia.org",
    "justsecurity.org",
)


@pytest.mark.xfail(reason=_VITAL_LAW_XFAIL_REASON, strict=True)
def test_branch_c_primary_open_rss_legal_analyst_feed_present_post_a1plus():
    """Pin Branch C deploy selection from the 2026-05-05 rubric:
    when passive Google News/VitalLaw observation produces zero legal-niche
    PAPER_TRADE, first active legal-analyst RSS deploy should choose at
    least one open-RSS national-security-law source before narrower or
    paywalled options."""
    from config import RSS_FEEDS

    matches = [
        url for url in RSS_FEEDS
        if any(domain in url.lower() for domain in _BRANCH_C_PRIMARY_DOMAINS)
    ]
    assert matches, (
        "no Branch C primary legal-analyst open-RSS URL detected in "
        "config.py:RSS_FEEDS. Per selection rubric, first active Branch C "
        f"deploy should include at least one of {_BRANCH_C_PRIMARY_DOMAINS}; "
        "extend `_BRANCH_C_PRIMARY_DOMAINS` only if Claude's rubric selects "
        "a different first-tier legal-analyst source."
    )
