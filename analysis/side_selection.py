"""Pure side-selection helpers (LD-10, P-5).

Implements two-sided executable EV side selection: each side is scored
against its own executable ask. Replaces the legacy YES-midpoint-sign
heuristic at ``main.py:706-707`` which produced incorrect side selection
when YES and NO edges had different magnitudes.

No I/O. No logging. No mutation. Inputs are ``KalshiMarket`` + scalar
probability; output is a chosen ``(side, executable_cents)`` tuple or a
fail-closed sentinel.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from kalshi import KalshiMarket


class EdgeBreakdown(NamedTuple):
    yes_edge: Optional[float]
    no_edge: Optional[float]
    yes_executable_cents: Optional[int]
    no_executable_cents: Optional[int]


def compute_edge(
    market: KalshiMarket,
    blended_yes_prob: float,
) -> EdgeBreakdown:
    """Two-sided edge: each side scored against its own executable ask.

    ``YES edge = blended_yes_prob - (yes_ask_cents / 100)``
    ``NO  edge = (1 - blended_yes_prob) - (no_ask_cents / 100)``

    Raises ``ValueError`` when the market price is unavailable
    (``price_available=False``) or either ask cents field is missing
    (LD-2 / CR-C). Callers must guard via ``is_tradeable()`` or catch
    the ``ValueError`` before consuming a result. Returning sentinel
    ``None`` fields would re-introduce the silent 50¢ fallback the
    pricing-correctness roadmap explicitly prohibits.
    """
    if not market.price_available:
        raise ValueError(
            f"compute_edge: price_available=False for {market.ticker!r}"
        )
    yes_ask = market.yes_ask_cents
    no_ask = market.no_ask_cents
    if yes_ask is None or no_ask is None:
        raise ValueError(
            f"compute_edge: missing ask cents for {market.ticker!r} "
            f"(yes_ask_cents={yes_ask!r} no_ask_cents={no_ask!r})"
        )
    yes_edge = blended_yes_prob - (yes_ask / 100.0)
    no_edge = (1.0 - blended_yes_prob) - (no_ask / 100.0)
    return EdgeBreakdown(yes_edge, no_edge, yes_ask, no_ask)


def select_side(
    market: KalshiMarket,
    blended_yes_prob: float,
) -> tuple[str, int]:
    """Pick the executable side with the larger positive edge.

    Returns ``(side, executable_cents)`` where side is ``"yes"`` or
    ``"no"`` and ``executable_cents`` is the chosen side's ask.

    Fail-closed:
       - raises ``ValueError("market_not_tradeable: ...")`` when the market
         is not tradeable (unavailable, missing, or non-executable prices).
      - raises ``ValueError("no_positive_edge: ...")`` when neither side
        has positive edge.
    """
    if not market.is_tradeable():
        raise ValueError(f"market_not_tradeable: ticker={market.ticker!r}")
    edges = compute_edge(market, blended_yes_prob)
    if edges.yes_edge <= 0 and edges.no_edge <= 0:
        raise ValueError(
            f"no_positive_edge: ticker={market.ticker!r} "
            f"yes_edge={edges.yes_edge:.4f} no_edge={edges.no_edge:.4f}"
        )
    if edges.yes_edge >= edges.no_edge:
        return ("yes", edges.yes_executable_cents)
    return ("no", edges.no_executable_cents)
