"""Strict-xfail harness for Branch C feed-selection rubric.

Spec: docs/governance/2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md.
Status: pre-loaded during PROFIT-PHASE2-001 soak; xpasses when Branch C
deploy adds the selected legal-analyst feeds to config.py.
"""

from __future__ import annotations

import pytest


_BRANCH_C_PRIMARY = "justsecurity.org"
_BRANCH_C_SECONDARY = "lawfaremedia.org"
_XFAIL_REASON = (
    "Branch C legal-analyst feeds not active yet. Just Security primary and "
    "Lawfare secondary land only if Branch A produces zero legal-niche PAPER_TRADE."
)


@pytest.mark.xfail(reason=_XFAIL_REASON, strict=True)
def test_branch_c_primary_just_security_feed_present():
    from config import RSS_FEEDS

    assert any(_BRANCH_C_PRIMARY in url.lower() for url in RSS_FEEDS), (
        "Branch C primary feed missing from config.py:RSS_FEEDS: "
        f"{_BRANCH_C_PRIMARY}. If fire-time RSS probe replaces the primary, "
        "update this harness with the revised Claude rubric before deploy."
    )


@pytest.mark.xfail(reason=_XFAIL_REASON, strict=True)
def test_branch_c_secondary_lawfare_feed_present():
    from config import RSS_FEEDS

    assert any(_BRANCH_C_SECONDARY in url.lower() for url in RSS_FEEDS), (
        "Branch C secondary feed missing from config.py:RSS_FEEDS: "
        f"{_BRANCH_C_SECONDARY}. If fire-time RSS probe replaces the secondary, "
        "update this harness with the revised Claude rubric before deploy."
    )
