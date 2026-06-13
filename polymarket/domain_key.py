"""Canonical per-family bucket key for Polymarket markets (PROFIT-VENUE-PARITY V03 root).

THE single source of truth for deriving a Polymarket market's family identity.
Every venue-parity consumer (match-feedback token buckets V17, keyword_outcomes
V18, calibration V15, per-family reporting V21, replay-corpus families V22) must
route through this function rather than re-deriving from the slug — open-risk #1
of the venue-parity audit is that competing derivations silently split the
learning key-space. ``analysis.series_id.resolve_family`` (when added) delegates
its Polymarket branch here.

WHY this exists: every Polymarket market currently reports
``series_ticker == 'polymarket_us'`` (the venue constant), collapsing ALL
Polymarket matcher/keyword/calibration feedback into ONE bucket. A false-positive
on any Polymarket market then poisons that token for EVERY Polymarket market, and
the ``is_market_defining_token`` rescue (a substring test against the prefix)
can never fire against the venue constant. Per-family keys fix both.

This module is PURE (no I/O, no imports beyond stdlib + the Venue enum) and is
NOT yet wired into any runtime path — Wave 2 of the migration plan threads it
through ``PolymarketMarket.series_ticker``. Landing it tested-but-unused
establishes the canonical derivation before the behavioral rekey.
"""
from __future__ import annotations

import re

from trading.venue import Venue

# Venue segment kept as the LEADING token of every key so coarse
# ``startswith('polymarket_us')`` readers (dedupe, venue reporting) still match.
_VENUE_SEGMENT = Venue.POLYMARKET_US.value  # 'polymarket_us'

# Polymarket slugs embed the event date as an ISO fragment, e.g.
#   ewc-usse-me-2026-11-03-dem  -> family stem 'ewc-usse-me'
#   paccc-usse-midterms-2026-11-03-rep -> 'paccc-usse-midterms'
#   enwc-usgubp-ga-2026-06-16-rep-burjon -> 'enwc-usgubp-ga'
# The stem (everything BEFORE the ISO date) identifies the contest/family;
# the date + trailing outcome segments distinguish instances/sides and are
# dropped so different outcomes of the same contest share one family bucket.
_ISO_DATE = re.compile(r"-\d{4}-\d{2}-\d{2}")


def pm_domain_key(market_id: str) -> str:
    """Return the canonical ``polymarket_us:<family-stem>`` key for a PM slug.

    Different outcomes/instances of the same contest collapse to one key
    (date + trailing outcome stripped); different contests yield different keys.
    A slug with no recognizable ISO date falls back to the bare venue segment
    (``'polymarket_us'``) WITHOUT raising — degrading to today's single-bucket
    behavior rather than crashing the matcher.

    Raises ``ValueError`` on a Kalshi (KX*) identifier: Kalshi families come
    from the real ``series_ticker`` and must NOT be routed here.
    """
    slug = (market_id or "").strip()
    if not slug:
        return _VENUE_SEGMENT
    if slug.upper().startswith("KX"):
        raise ValueError(
            f"pm_domain_key expects a Polymarket slug, got Kalshi-shaped {market_id!r}"
        )
    match = _ISO_DATE.search(slug)
    stem = (slug[: match.start()] if match else slug).strip("-").lower()
    return f"{_VENUE_SEGMENT}:{stem}" if stem else _VENUE_SEGMENT
