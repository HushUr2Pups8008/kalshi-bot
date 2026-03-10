"""
Kelly criterion calculations for binary prediction markets.

On Kalshi, each contract pays $1 if it resolves YES and $0 otherwise.
Buying a YES contract at price p (in dollars) risks p to win (1 - p).

Full Kelly formula for a binary bet:
  f* = (b * p_win - p_lose) / b
  where b = (1 - price) / price  (odds ratio for YES contract)

We use HALF Kelly (multiply result by kelly_fraction = 0.5) for
conservatism, then cap at max_bet_dollars.
"""

from utils.logger import get_logger

log = get_logger("kelly")


def kelly_bet(
    estimated_probability: float,
    market_price_cents: float,
    bankroll: float,
    kelly_fraction: float = 0.5,
    max_bet_dollars: float = 50.0,
    min_edge: float = 0.04,
) -> tuple[float, float, float]:
    """
    Calculate the Kelly-optimal bet size for buying YES contracts.

    Args:
        estimated_probability: Our estimate that the market resolves YES (0–1).
        market_price_cents:    Current YES price on Kalshi in cents (1–99).
        bankroll:              Total available bankroll in dollars.
        kelly_fraction:        Fraction of full Kelly to use (default 0.5).
        max_bet_dollars:       Hard cap per bet in dollars.
        min_edge:              Minimum edge required to place any bet.

    Returns:
        (kelly_fraction_raw, kelly_dollars, capped_dollars)
        kelly_dollars and capped_dollars are 0 if edge is below min_edge.
    """
    market_price = market_price_cents / 100.0
    edge         = estimated_probability - market_price

    if abs(edge) < min_edge:
        return 0.0, 0.0, 0.0

    # Determine which side to bet
    if edge > 0:
        # YES is underpriced → buy YES
        p_win  = estimated_probability
        p_lose = 1.0 - estimated_probability
        price  = market_price
    else:
        # NO is underpriced → buy NO
        p_win  = 1.0 - estimated_probability
        p_lose = estimated_probability
        price  = 1.0 - market_price   # NO price in dollars

    if price <= 0 or price >= 1:
        return 0.0, 0.0, 0.0

    # Payout odds: win (1 - price) for every price risked
    b = (1.0 - price) / price

    # Full Kelly fraction of bankroll
    f_full = (b * p_win - p_lose) / b

    if f_full <= 0:
        return 0.0, 0.0, 0.0

    # Apply fractional Kelly
    f_fractional = f_full * kelly_fraction

    kelly_dollars = f_fractional * bankroll
    capped_dollars = min(kelly_dollars, max_bet_dollars)

    log.debug(
        "Kelly: p_win=%.3f price=%.2fc edge=%.3f b=%.2f "
        "f_full=%.4f f_frac=%.4f kelly=$%.2f capped=$%.2f",
        p_win, market_price_cents, edge, b,
        f_full, f_fractional, kelly_dollars, capped_dollars,
    )

    return f_fractional, kelly_dollars, capped_dollars


def contracts_from_dollars(dollars: float, price_cents: float) -> int:
    """
    Convert a dollar amount to an integer number of Kalshi contracts.

    Each contract costs price_cents / 100 dollars.
    Always rounds down to stay within budget.
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
    """
    Calculate expected P&L for a position.

    Returns expected profit in dollars (can be negative).
    """
    cost    = contracts * price_cents / 100.0
    if side == "yes":
        ev_win  = contracts * 1.0          # each contract pays $1
        p_win   = estimated_probability
    else:
        ev_win  = contracts * 1.0
        p_win   = 1.0 - estimated_probability

    ev = p_win * ev_win - cost
    return ev
