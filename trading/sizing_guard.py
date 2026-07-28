"""Fail-closed paper sizing guard for unresolved historical settlement state."""

from __future__ import annotations

import math
from pathlib import Path


def effective_sizing_bankroll(
    db_path: Path | str,
    *,
    notional_bankroll: float,
    configured_starting_bankroll: float,
) -> float:
    """Return a non-inflating bankroll when settlement evidence is incomplete.

    The persisted paper bankroll is local paper-accounting state. Do not rewrite
    it here, and do not use local settlement/outbox delivery as a profit proof.
    Runtime sizing cannot grow above the configured starting bankroll until a
    separate external execution/cash-settlement ledger is designed and bound to
    this decision. That promotion path intentionally does not exist today.
    """

    del db_path
    try:
        raw = float(notional_bankroll)
        starting = float(configured_starting_bankroll)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(raw) or not math.isfinite(starting):
        return 0.0
    return min(max(0.0, raw), max(0.0, starting))
