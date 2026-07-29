"""Canonical final price and contract terms for paper and live execution."""

from __future__ import annotations

import math
from dataclasses import dataclass

from analysis.kelly import contracts_from_dollars


@dataclass(frozen=True)
class FinalExecutionTerms:
    price_cents: int
    contracts: int
    cost_dollars: float


def final_execution_terms(
    *,
    capped_dollars: float,
    executed_price_cents: int,
) -> FinalExecutionTerms:
    """Return the exact executable terms shared by paper and live paths."""

    if (
        isinstance(executed_price_cents, bool)
        or not isinstance(executed_price_cents, int)
        or not 0 < executed_price_cents < 100
    ):
        raise ValueError("executed price must be an integer between 1 and 99 cents")
    if isinstance(capped_dollars, bool):
        raise ValueError("capped dollars must be finite and positive")
    try:
        normalized_capped_dollars = float(capped_dollars)
    except (TypeError, ValueError) as exc:
        raise ValueError("capped dollars must be finite and positive") from exc
    if not math.isfinite(normalized_capped_dollars) or normalized_capped_dollars <= 0:
        raise ValueError("capped dollars must be finite and positive")
    contracts = contracts_from_dollars(normalized_capped_dollars, float(executed_price_cents))
    if contracts <= 0:
        raise ValueError("final execution has zero contracts")
    cost_dollars = contracts * executed_price_cents / 100.0
    if cost_dollars > normalized_capped_dollars:
        raise ValueError("final execution cost exceeds capped dollars")
    return FinalExecutionTerms(
        price_cents=executed_price_cents,
        contracts=contracts,
        cost_dollars=cost_dollars,
    )
