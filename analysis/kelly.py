"""
Kelly criterion calculations for binary prediction markets.

On Kalshi, each contract pays $1 if it resolves YES and $0 otherwise.
Buying a YES contract at price p (in dollars) risks p to win (1 - p).

Full Kelly formula for a binary bet:
  f* = (b * p_win - p_lose) / b
  where b = (1 - price) / price  (odds ratio for YES contract)

Bet sizing applies three explicit multipliers on top of full Kelly:

  f_adjusted = f_full
               * kelly_fraction      (default 0.5 -- half-Kelly conservatism)
               * confidence          (LLM self-reported confidence, 0.0-1.0)
               * source_multiplier   (per-source win-rate, 0.5-1.5)
               * time_discount       (exponential decay by days-to-close)

  kelly_dollars  = f_adjusted * bankroll
  capped_dollars = min(kelly_dollars, max_bet_dollars)

The caller computes max_bet_dollars as a percentage of bankroll (e.g. 15%)
rather than a fixed hard cap, so the ceiling grows with the bankroll.
"""

import math

from utils.logger import get_logger

log = get_logger("kelly")


def _time_discount(
    days: float,
    half_life: float = 14.0,
    floor: float = 0.20,
) -> float:
    """Exponential time discount for capital locked in long-duration bets.

    Returns 1.0 for markets closing within 3 days (full sizing).
    Decays toward `floor` for longer durations using a half-life of
    `half_life` days measured from the 3-day mark.

    Args:
        days:      Days until market close. Use 14.0 (default) if unknown.
        half_life: Days after the 3-day buffer at which discount reaches 0.5.
                   Default 14 days -- a 17-day market gets ~50% discount.
        floor:     Minimum discount factor (never goes below this).
                   Default 0.20 -- very long markets still get 20% of sizing.

    Examples (half_life=14, floor=0.20):
        0-3 days -> 1.00
        7 days   -> 0.83
        14 days  -> 0.65
        21 days  -> 0.51
        30 days  -> 0.38
        60 days  -> 0.20 (floor)
    """
    if days <= 3.0:
        return 1.0
    raw = math.exp(-math.log(2.0) / half_life * (days - 3.0))
    return max(floor, raw)


def kelly_bet(
    estimated_probability: float,
    market_price_cents: float,
    bankroll: float,
    kelly_fraction: float = 0.5,
    max_bet_dollars: float = 75.0,
    min_bet_dollars: float = 2.0,
    min_edge: float = 0.04,
    source_multiplier: float = 1.0,
    confidence: float = 1.0,
    days_to_close: float = 14.0,
    time_discount_half_life: float = 14.0,
    time_discount_floor: float = 0.20,
) -> tuple[float, float, float]:
    """
    Calculate the Kelly-optimal bet size with confidence and time discounting.

    Args:
        estimated_probability:   Our estimate that the market resolves YES (0-1).
        market_price_cents:      Current YES price in cents (1-99).
        bankroll:                Notional (or live) bankroll in dollars.
        kelly_fraction:          Fraction of full Kelly (default 0.5 = half-Kelly).
        max_bet_dollars:         Dynamic cap -- caller passes cfg.dynamic_max_bet(bankroll).
        min_bet_dollars:         Minimum bet floor in dollars.
        min_edge:                Minimum edge to place any bet.
        source_multiplier:       Credibility multiplier from SourceCredibility (0.5-1.5).
        confidence:              LLM self-reported confidence (0.0-1.0). Scales the
                                 Kelly fraction directly -- 0.95 confidence -> 95% of
                                 the otherwise-sized bet. Defaults to 1.0 (no reduction).
        days_to_close:           Days until market closes. Used to compute time_discount.
                                 Defaults to 14.0 (moderate discount) when unknown.
        time_discount_half_life: Half-life for time discount decay in days (default 14).
        time_discount_floor:     Minimum time discount factor (default 0.20).

    Returns:
        (kelly_fraction_raw, kelly_dollars, capped_dollars)
        All three are 0.0 if no bet should be placed.
    """
    market_price = market_price_cents / 100.0
    edge         = estimated_probability - market_price

    if abs(edge) < min_edge:
        return 0.0, 0.0, 0.0

    if edge > 0:
        p_win  = estimated_probability
        p_lose = 1.0 - estimated_probability
        price  = market_price
    else:
        p_win  = 1.0 - estimated_probability
        p_lose = estimated_probability
        price  = 1.0 - market_price

    if price <= 0 or price >= 1:
        return 0.0, 0.0, 0.0

    b      = (1.0 - price) / price
    f_full = (b * p_win - p_lose) / b

    if f_full <= 0:
        return 0.0, 0.0, 0.0

    # Clamp confidence to [0, 1] -- guard against LLM returning out-of-range values
    confidence_clamped = max(0.0, min(1.0, confidence))

    # Time discount: long-duration bets tie up capital, reduce sizing accordingly
    t_discount = _time_discount(days_to_close, time_discount_half_life, time_discount_floor)

    # Composite fractional Kelly: half-Kelly * confidence * source * time
    f_fractional  = f_full * kelly_fraction * confidence_clamped * source_multiplier * t_discount
    kelly_dollars = f_fractional * bankroll

    # Never bet below minimum floor
    if kelly_dollars < min_bet_dollars:
        return 0.0, 0.0, 0.0

    capped_dollars = min(kelly_dollars, max_bet_dollars)

    log.debug(
        "Kelly: p_win=%.3f price=%.2fc edge=%.3f b=%.2f "
        "f_full=%.4f kelly_frac=%.2f conf=%.2f src_mult=%.2f t_disc=%.2f(%.1fd) "
        "f_adj=%.4f kelly=$%.2f capped=$%.2f",
        p_win, market_price_cents, edge, b,
        f_full, kelly_fraction, confidence_clamped, source_multiplier,
        t_discount, days_to_close,
        f_fractional, kelly_dollars, capped_dollars,
    )

    return f_fractional, kelly_dollars, capped_dollars


def contracts_from_dollars(dollars: float, price_cents: float) -> int:
    """
    Convert a dollar amount to an integer number of Kalshi contracts.
    Rounds down to stay within budget.
    """
    if price_cents <= 0:
        return 0
    price_dollars = price_cents / 100.0
    return max(1, int(dollars / price_dollars))


def expected_pnl(
    contracts: int,
    price_cents: float,
    estimated_probability: float,
    side: str,
) -> float:
    """Expected P&L for a position in dollars (can be negative)."""
    cost  = contracts * price_cents / 100.0
    p_win = estimated_probability if side == "yes" else 1.0 - estimated_probability
    return p_win * contracts - cost
