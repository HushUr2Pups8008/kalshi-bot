"""Tests for polymarket/domain_key.py (PROFIT-VENUE-PARITY V03 canonical PM family key).

These pin the derivation Wave 2 wires into series_ticker. WHY each assertion:
the whole point is replacing the single 'polymarket_us' bucket (which lets one
market's false positives poison every PM market) with per-contest buckets — so
the load-bearing properties are: same contest/different outcome -> SAME key,
different contest -> DIFFERENT key, leading 'polymarket_us' segment preserved
(coarse readers still match), and Kalshi tickers rejected (they have a real
series_ticker and must not be routed here).
"""
from __future__ import annotations

import pytest

from polymarket.domain_key import pm_domain_key


def test_strips_iso_date_to_family_stem():
    assert pm_domain_key("ewc-usse-me-2026-11-03-dem") == "polymarket_us:ewc-usse-me"
    assert pm_domain_key("paccc-usse-midterms-2026-11-03-rep") == "polymarket_us:paccc-usse-midterms"
    assert pm_domain_key("enwc-usgubp-ga-2026-06-16-rep-burjon") == "polymarket_us:enwc-usgubp-ga"


def test_same_contest_different_outcome_collapses_to_one_family():
    # The reason per-family bucketing is safe: YES/DEM vs NO/REP of the same
    # contest are one market family and must share learned feedback.
    dem = pm_domain_key("ewc-usse-me-2026-11-03-dem")
    rep = pm_domain_key("ewc-usse-me-2026-11-03-rep")
    assert dem == rep == "polymarket_us:ewc-usse-me"


def test_different_contests_get_different_families():
    # The reason the collapse was HARMFUL: distinct contests must NOT share a
    # bucket (Maine Senate vs Iowa Governor are unrelated subject domains).
    me_senate = pm_domain_key("ewc-usse-me-2026-11-03-dem")
    ia_gov = pm_domain_key("ewc-usgub-ia-2026-11-03-dem")
    assert me_senate != ia_gov


def test_leading_venue_segment_preserved_for_coarse_readers():
    # V18/V21/dedupe readers do startswith('polymarket_us'); every key must keep it.
    assert pm_domain_key("ewc-usse-me-2026-11-03-dem").startswith("polymarket_us")


def test_no_iso_date_uses_full_slug_family_without_bare_bucket():
    # Do not degrade to the legacy single-bucket behavior that poisons PM state.
    assert pm_domain_key("some-undated-slug") == "polymarket_us:some-undated-slug"


def test_empty_slug_rejected_instead_of_bare_bucket():
    with pytest.raises(ValueError):
        pm_domain_key("")


def test_kalshi_ticker_rejected():
    with pytest.raises(ValueError):
        pm_domain_key("KXTRUMPIRAN-26JUN01")


def test_never_collides_with_kalshi_keyspace():
    # Kalshi keys are bare uppercase prefixes (KXTRUMPIRAN); PM keys always carry
    # the 'polymarket_us:' namespace -> the two key-spaces can never collide.
    assert pm_domain_key("ewc-usse-me-2026-11-03-dem").startswith("polymarket_us:")
