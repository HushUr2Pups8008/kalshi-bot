"""
Kelly criterion calculations for binary prediction markets.

On Kalshi, each contract pays $1 if it resolves YES and $0 otherwise.
Buying a YES contract at price p (in dollars) risks p to win (1 - p).

Full Kelly formula for a binary bet:
  f* = (b * p_win - p_lose) / b
  where b = (1 - price) / price  (odds ratio for YES contract)

We use HALF Kelly (multiply result by kelly_fraction = 0.5) and then
apply a dynamic max bet cap:
  max_bet = min(hard_cap, 5% of current notional bankroll)
  floor   = $2 minimum
"""

from utils.logger import get_logger

log = get_logger("kelly")


def kelly_bet(
    estimated_probability: float,
    market_price_cents: float,
    bankroll: float,
    kelly_fraction: float = 0.5,
    max_bet_dollars: float = 25.0,
    min_bet_dollars: float = 2.0,
    min_edge: float = 0.04,
    source_multiplier: float = 1.0,
) -> tuple[float, float, float]:
    """
    Calculate the Kelly-optimal bet size.

    Args:
        estimated_probability: Our estimate that the market resolves YES (0–1).
        market_price_cents:    Current YES price in cents (1–99).
        bankroll:              Notional (or live) bankroll in dollars.
        kelly_fraction:        Fraction of full Kelly (default 0.5 = half-Kelly).
        max_bet_dollars:       Dynamic cap — caller passes cfg.dynamic_max_bet(bankroll).
        min_bet_dollars:       Minimum bet floor in dollars.
        min_edge:              Minimum edge to place any bet.
        source_multiplier:     Credibility multiplier from SourceCredibility (0.5–1.5).

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

    # Apply fractional Kelly, then source credibility multiplier
    f_fractional  = f_full * kelly_fraction * source_multiplier
    kelly_dollars = f_fractional * bankroll

    # Dynamic cap: never exceed max_bet_dollars, never below min_bet_dollars
    if kelly_dollars < min_bet_dollars:
        return 0.0, 0.0, 0.0

    capped_dollars = min(kelly_dollars, max_bet_dollars)

    log.debug(
        "Kelly: p_win=%.3f price=%.2fc edge=%.3f b=%.2f "
        "f_full=%.4f f_frac=%.4f src_mult=%.2f kelly=$%.2f capped=$%.2f",
        p_win, market_price_cents, edge, b,
        f_full, f_fractional, source_multiplier, kelly_dollars, capped_dollars,
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
