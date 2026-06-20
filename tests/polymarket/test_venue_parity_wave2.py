"""Efficacy + no-harm pins for venue-parity Wave 2 (V03 root + V07 gate prereq).

These assert the BEHAVIOR the fix is supposed to deliver — not just that code runs:
- V03: distinct Polymarket contests get distinct series_ticker (the collapse into
  one 'polymarket_us' bucket — which poisoned every PM market's feedback — is gone).
- V03 open-risk #2: the learn-side key and the apply-side key resolve identically
  (a silent key-space split would mean learning never converges).
- V07: PM match_meta carries the GENERIC match_score key so the L2-a
  false_positive_neutral score-gate fires for PM exactly as for Kalshi.
"""
from __future__ import annotations

from analysis.match_feedback import is_market_defining_token, market_prefix_for
from polymarket.candidate_adapter import PolymarketExecutionMarket
from polymarket.domain_key import pm_domain_key
from polymarket.models import PolymarketMarket
from polymarket.paper_runtime import PolymarketMatchMeta
from trading.venue import Venue


def _pm(market_id: str) -> PolymarketMarket:
    return PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id=market_id,
        title="t",
        status="open",
        yes_ask_cents=40,
        no_ask_cents=61,
        volume_dollars=0.0,
        open_interest_dollars=0.0,
        close_time="2026-12-31T23:59:59Z",
    )


def test_pm_series_ticker_is_per_family_not_venue_constant():
    """V03: distinct PM contests must NOT share series_ticker (the collapse that
    poisoned every PM market's feedback bucket); same contest/diff outcome share."""
    me = _pm("ewc-usse-me-2026-11-03-dem")
    ia = _pm("ewc-usgub-ia-2026-11-03-dem")
    assert me.series_ticker == "polymarket_us:ewc-usse-me"
    assert me.series_ticker != "polymarket_us"
    assert me.series_ticker != ia.series_ticker
    assert me.series_ticker == _pm("ewc-usse-me-2026-11-03-rep").series_ticker
    # No-harm: still namespaced so coarse startswith('polymarket_us') readers match.
    assert me.series_ticker.startswith("polymarket_us")


def test_pm_domain_key_no_iso_slug_still_uses_non_bare_family_bucket():
    assert pm_domain_key("custom-polymarket-market") == "polymarket_us:custom-polymarket-market"


def test_learn_apply_market_prefix_consistency():
    """V03 open-risk #2: the LEARN-side key (PolymarketMarket, fed to match
    feedback) and the APPLY-side key (PolymarketExecutionMarket recorded on the
    trade) MUST resolve to the SAME market_prefix, or the token-weight key-space
    silently splits and learning never converges."""
    mid = "ewc-usgub-ks-2026-11-03-dem"
    pm = _pm(mid)
    exec_market = PolymarketExecutionMarket(
        venue=Venue.POLYMARKET_US,
        ticker=mid,
        series_ticker=pm.series_ticker,
        title="t",
        subtitle="",
        status="open",
        yes_price=40,
        no_price=61,
        yes_ask_cents=40,
        no_ask_cents=61,
        volume_dollars=0.0,
        open_interest_dollars=0.0,
        close_time="2026-12-31T23:59:59Z",
    )
    assert market_prefix_for(pm) == market_prefix_for(exec_market) == pm_domain_key(mid)
    assert market_prefix_for(pm) == "polymarket_us:ewc-usgub-ks"


def test_match_meta_emits_generic_match_score_for_l2a_gate():
    """V07: PM match_meta must carry the GENERIC match_score key (not only the
    polymarket_* alias) so signal_analyzer's MATCH_LLM_REVIEW carries it and the
    L2-a false_positive_neutral score-gate fires for PM. Without it, every PM
    neutral verdict mapped to 0.0-marginal and poisoned the bucket."""
    meta = PolymarketMatchMeta(
        venue="polymarket_us", match_score=0.1875, matched_tokens=["iran"]
    ).as_dict()
    assert meta["match_score"] == 0.1875
    assert meta["match_score"] == meta["polymarket_match_score"]
    assert meta["matched_tokens"] == ["iran"]


def test_defining_token_guard_ignores_pm_venue_segment():
    """V08: the per-family PM prefix carries the constant 'polymarket_us' venue
    segment in EVERY key. The defining-token guard must NOT treat a generic
    substring of that segment ('market', 'poly') as a market-defining token —
    else those generic bridge tokens get pinned at weight 1.0 across ALL PM
    families and can never be downweighted. Only the contest family stem counts.
    Kalshi prefixes (no 'polymarket_us:' segment) are unaffected."""
    pm_prefix = "polymarket_us:ewc-usse-me"
    # Generic substrings of the venue segment are NOT defining.
    assert is_market_defining_token("market", pm_prefix) is False
    assert is_market_defining_token("poly", pm_prefix) is False
    # A token from the contest family stem IS defining.
    assert is_market_defining_token("usse", pm_prefix) is True
    # No-harm: Kalshi subject-token detection is unchanged.
    assert is_market_defining_token("iran", "KXVISITIRAN") is True
    assert is_market_defining_token("market", "KXMARKETCAP") is True
