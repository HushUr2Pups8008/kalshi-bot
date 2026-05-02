"""Shared helpers for behavioural-simulation harnesses.

Origin: extracted from the PROFIT-EDGE-001/002/003 simulations on 2026-04-26.
Pure functions only — no I/O, no DB access. Network-free.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from kalshi import KalshiMarket


@dataclass(frozen=True)
class LLMPositiveEvent:
    """An LLM-positive signal event used as a regression-anchor across
    simulations. The five canonical events were diagnosed on 2026-04-26
    in the PROFIT-EDGE-001 systematic-debugging investigation: across 9
    days of v0.29.54 paper-mode operation, these were the only signals
    where ``llm_useful=True`` with non-trivial probability movement.

    fast_p / fast_conf are the values the LLM was *about* to emit before
    the line-688 ``if not keywords:`` kill rejected them. After the
    EDGE-001 fix in v0.29.56 these reach the blender; after EDGE-002 +
    EDGE-003 (v0.29.57 + v0.29.58) they clear the readiness gate.
    """

    name: str
    ticker: str
    series: str
    title: str
    close_time: str
    fast_p: float
    fast_conf: float
    side: str  # "yes" | "no"


# Canonical anchor events. Add to this list as new diagnostic events are
# captured by future systematic-debugging sessions. Each entry should be
# traceable to a specific dated diagnosis in the debt log.
LLM_POSITIVE_EVENTS_2026_04_26: tuple[LLMPositiveEvent, ...] = (
    LLMPositiveEvent(
        name="Event 1: KXSBUDGETRES-APR28 (ICE funding)",
        ticker="KXSBUDGETRES-26APR-APR28",
        series="KXSBUDGETRES",
        title="Budget resolution / continuing resolution / ICE funding",
        close_time="2026-04-28T23:59:59Z",
        fast_p=0.568,
        fast_conf=0.85,
        side="yes",
    ),
    LLMPositiveEvent(
        name="Event 2: KXSBUDGETRES-APR25 (ICE funding)",
        ticker="KXSBUDGETRES-26APR-APR25",
        series="KXSBUDGETRES",
        title="Budget resolution / continuing resolution / ICE funding",
        close_time="2026-04-25T23:59:59Z",
        fast_p=0.568,
        fast_conf=0.85,
        side="yes",
    ),
    LLMPositiveEvent(
        name="Event 3: KXTRUMPIRAN (Trump dispatching)",
        ticker="KXTRUMPIRAN-26MAY01",
        series="KXTRUMPIRAN",
        title="Iran diplomacy / kinetic events",
        close_time="2026-05-01T23:59:59Z",
        fast_p=0.568,
        fast_conf=0.85,
        side="yes",
    ),
    LLMPositiveEvent(
        name="Event 4: KXPSL-PZA (cricket — should be sport-blocked)",
        ticker="KXPSL-26-PZA",
        series="KXPSL",
        title="Pakistan Super League cricket",
        close_time="2026-05-15T23:59:59Z",
        fast_p=0.568,
        fast_conf=0.85,
        side="yes",
    ),
    LLMPositiveEvent(
        name="Event 5: KXTRUMPIRAN (talks stall)",
        ticker="KXTRUMPIRAN-26MAY01",
        series="KXTRUMPIRAN",
        title="Iran diplomacy / kinetic events",
        close_time="2026-05-01T23:59:59Z",
        fast_p=0.432,
        fast_conf=0.85,
        side="no",
    ),
)


# Canonical news headlines that produced each LLM-positive signal in the
# 9-day v0.29.54 paper-mode window (2026-04-18 → 2026-04-26 MacBook archive).
# Sourced by reading SIGNAL_ANALYSIS_DETAIL records with ``llm_useful=True``
# (or, for KXPSL where llm_useful was True with magnitude="none", the
# only llm_useful=True record on the target ticker) from
# ``mac_archive/macbook_2026-05-01_import/logs/trades/archive/2026/04/``.
#
# Keyed by event ``name`` rather than ``ticker`` because Events 3 and 5
# share ``KXTRUMPIRAN-26MAY01`` — a ticker-keyed dict cannot represent
# both. Any simulation that needs the headline for an event should look
# it up by ``event.name``.
# Production market titles for the same five events — captured from the
# ``MATCH_DIAGNOSTIC`` records adjacent to each LLM-positive signal in the
# MacBook archive. These are the *real* titles the matcher saw at the
# moment each headline was scored, which is what makes the audit faithful
# to production scoring instead of the generic placeholder titles in
# :class:`LLMPositiveEvent.title`. Keyed by ``ticker`` because two of the
# five events share ``KXTRUMPIRAN-26MAY01`` *and* therefore share the same
# market title — a ticker-keyed dict is the natural fit for titles.
LLM_POSITIVE_EVENT_MARKET_TITLES_2026_04_26: dict[str, str] = {
    "KXSBUDGETRES-26APR-APR28": "Will a budget resolution passed the Senate before Apr 28, 2026?",
    "KXSBUDGETRES-26APR-APR25": "Will a budget resolution passed the Senate before Apr 25, 2026?",
    "KXTRUMPIRAN-26MAY01": "Will Donald Trump visit Iran before May 1, 2026?",
    "KXPSL-26-PZA": "Will Peshawar Zalmi win the Pakistan Super League?",
}


LLM_POSITIVE_EVENT_HEADLINES_2026_04_26: dict[str, str] = {
    "Event 1: KXSBUDGETRES-APR28 (ICE funding)":
        "US Senate passes ICE funding resolution after ‘vote-a-rama’: What’s next?",
    "Event 2: KXSBUDGETRES-APR25 (ICE funding)":
        "US Senate passes ICE funding resolution after ‘vote-a-rama’: What’s next?",
    "Event 3: KXTRUMPIRAN (Trump dispatching)":
        "Trump said dispatching Witkoff, Kushner for talks with Iran FM in Pakistan in coming days",
    "Event 4: KXPSL-PZA (cricket — should be sport-blocked)":
        "PSL 11: Babar, Bracewell lead Zalmi to massive total against Qalandars - Geo Super",
    "Event 5: KXTRUMPIRAN (talks stall)":
        "US-Iran Peace Talks Stall as Trump Scraps Diplomatic Visit - Global Banking & Finance Review®",
}


def regime_confidence(weights: Iterable[float]) -> float:
    """``1 - H(weights) / log(N)`` — matches ``tasks.blend_task._regime_confidence``.

    Reproduced here to avoid importing the task module at simulation-script
    import time (which would pull in the asyncio store dependency).
    """
    nonzero = [w for w in weights if w > 0]
    if not nonzero:
        return 0.0
    n = len(list(weights))
    if n <= 1:
        return 1.0
    entropy = -sum(w * math.log(w) for w in nonzero)
    max_entropy = math.log(n)
    return max(0.0, min(1.0, 1.0 - entropy / max_entropy))


def synthetic_market(event: LLMPositiveEvent, *, yes_price_cents: float = 50.0) -> KalshiMarket:
    """Build a KalshiMarket matching an anchor event for use in simulations."""
    return KalshiMarket(
        ticker=event.ticker,
        title=event.title,
        yes_bid=yes_price_cents - 1,
        yes_ask=yes_price_cents + 1,
        yes_price=yes_price_cents,
        volume=1000,
        open_interest=500,
        close_time=event.close_time,
        status="active",
        series_ticker=event.series,
    )
