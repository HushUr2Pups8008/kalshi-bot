"""Regression coverage for v0.30.1 follow-up silent-50 fix in
``scripts/performance_analysis.py``.

The pre-fix code chained ``int(opp.get("market_yes_price", 50))`` in
``section_missed_opportunities`` and ``"%.0fc" % (t.get("market_yes_price") or 0)``
in ``section_placed_performance``. Both silently coerced missing prices
into displayable / computable values that contaminated the audit table.
"""
from __future__ import annotations

from scripts.performance_analysis import (
    section_missed_opportunities,
    section_placed_performance,
)


def _opportunity(ticker: str, *, market_yes_price: int | None) -> dict:
    """Synthesize a minimal OPPORTUNITY log row.

    When ``market_yes_price`` is None, the key is omitted entirely so
    that ``opp.get("market_yes_price")`` returns None (matching the
    pre-fix bug-trigger condition).
    """
    row: dict = {
        "type": "OPPORTUNITY",
        "ticker": ticker,
        "ts": "2026-05-13T00:30:00+00:00",
        "side": "yes",
        "edge": 0.05,
        "estimated_probability": 0.55,
        "signal_source": "test",
    }
    if market_yes_price is not None:
        row["market_yes_price"] = market_yes_price
    return row


def _skip(ticker: str) -> dict:
    # Use a CONTROLLABLE skip reason so `_is_controllable` returns True
    # and the skip enters the paired set evaluated by the missing-price
    # branch. `_is_controllable` treats "edge below threshold",
    # "price illiquid", "insufficient balance", and "unknown" as
    # non-controllable.
    return {
        "type": "SKIPPED",
        "ticker": ticker,
        "ts": "2026-05-13T00:30:05+00:00",
        "reason": "duplicate / same-signal prob+price match",
        "edge": 0.05,
    }


def test_section_missed_opportunities_skips_rows_with_no_market_price():
    """Pre-fix: ``int(opp.get("market_yes_price", 50))`` silently
    fabricated a 50¢ entry price when the OPPORTUNITY log row lacked
    the field, contaminating the counterfactual P&L table. Post-fix
    must skip the row and surface the count.
    """
    entries = [
        _opportunity("KX-A", market_yes_price=37),
        _opportunity("KX-B", market_yes_price=None),
        _skip("KX-A"),
        _skip("KX-B"),
    ]
    resolution_map = {
        "KX-A": {"resolved_yes": True, "result": "yes"},
        "KX-B": {"resolved_yes": False, "result": "no"},
    }
    output = section_missed_opportunities(entries, resolution_map)

    # The KX-B row had no usable price — must be excluded with explicit
    # operator-visible audit line, not silently coerced to 50¢.
    assert (
        "Excluded — missing market price : 1 "
        "(P-4 LD-2 fail-closed; no silent-50)" in output
    ), output
    # KX-A is still reported (it has a real 37¢ price).
    assert "KX-A" in output
    # The pre-fix synthetic-50 path used 50¢ as the counterfactual entry
    # price. After the fix, no row carries the synthetic value.
    assert " 50c " not in output and "\t50\t" not in output


def test_section_missed_opportunities_preserves_explicit_zero_price():
    """An explicit ``market_yes_price=0`` is a real (degenerate) price,
    not a missing value — must be passed through to the counterfactual
    table without exclusion. Only the missing/None case is rejected.
    """
    entries = [
        _opportunity("KX-Z", market_yes_price=0),
        _skip("KX-Z"),
    ]
    resolution_map = {
        "KX-Z": {"resolved_yes": False, "result": "no"},
    }
    output = section_missed_opportunities(entries, resolution_map)

    assert "Excluded — missing market price" not in output, output
    assert "KX-Z" in output


def test_section_placed_performance_renders_missing_price_as_na():
    """Pre-fix display formatter rendered missing ``market_yes_price``
    as ``"0c"`` via ``"%.0fc" % (... or 0)`` — looked like a real 0¢
    entry price. Post-fix renders it as ``"n/a"``. Explicit 0 still
    renders as ``"0c"``.

    The resolved-trades table is driven by ``db_trades`` rows that
    carry ``resolved=True``. We construct two: one with no
    ``market_yes_price`` field (must render as ``"n/a"``) and one
    with an explicit ``market_yes_price=0`` (must render as ``"0c"``).
    """
    db_trades = [
        {
            "trade_id": "t-miss",
            "ticker": "KX-MISS",
            "ts": "2026-05-13T00:30:00+00:00",
            "side": "yes",
            "edge": 0.05,
            "estimated_prob": 0.55,
            # market_yes_price intentionally absent
            "cost_dollars": 0.40,
            "pnl_dollars": 0.10,
            "resolved": True,
            "resolved_yes": True,
            "signal_source": "test",
        },
        {
            "trade_id": "t-zero",
            "ticker": "KX-ZERO",
            "ts": "2026-05-13T00:30:05+00:00",
            "side": "yes",
            "edge": 0.05,
            "estimated_prob": 0.55,
            "market_yes_price": 0,  # explicit zero — must render as "0c"
            "cost_dollars": 0.40,
            "pnl_dollars": 0.10,
            "resolved": True,
            "resolved_yes": True,
            "signal_source": "test",
        },
    ]
    entries: list[dict] = []
    resolution_map: dict = {}

    output = section_placed_performance(entries, db_trades, resolution_map)

    # KX-MISS row renders the missing-price column as "n/a".
    assert "n/a" in output, output
    # KX-ZERO row preserves the explicit "0c" — not coerced to "n/a".
    assert "0c" in output, output
