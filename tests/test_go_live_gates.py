"""Go-live drawdown gate must reconcile to mark-to-market equity (P4).

The live-cutover readiness gate (``main._check_go_live_gates``) historically
computed drawdown on RAW notional free-cash:
``(cfg.bankroll - notional) / cfg.bankroll``. The paper bankroll model deducts
each open trade's FULL entry cost at placement, so notional understates true
equity whenever open positions retain value — inflating the drawdown number.

The AUTHORITATIVE daily section-8 report (PROFIT-DRAWDOWN-001c, #144) already
gates on MARK-TO-MARKET equity = ``notional + marked_value`` using
``scripts.mark_open_positions.compute_open_position_marks``. Two different
drawdown numbers for ONE live-cutover decision is a latent hazard at the 20%
boundary. This module pins the gate onto the SAME MTM basis the report uses.

Fail-closed invariants preserved (the safe direction for go-live):
  * unpriced positions count as $0 marked value (lower equity -> higher
    drawdown -> more likely to FAIL) — exactly as the report does;
  * if marking raises / fails entirely, the gate FAILS (never passes on error).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main


def _paper(*, notional: float, resolved_trades: list[dict]):
    """A PaperTrader test double exposing only what the gate reads."""
    paper = MagicMock()
    paper.get_notional_bankroll.return_value = notional
    paper.get_all_trades.return_value = resolved_trades
    return paper


def _passing_resolved(n: int = 30, win_rate: float = 0.60) -> list[dict]:
    """Resolved trades that clear min_resolved and win_rate, isolating the
    drawdown gate as the only variable under test."""
    wins = int(round(n * win_rate))
    trades = []
    for i in range(n):
        trades.append(
            {"resolved": True, "pnl_dollars": (1.0 if i < wins else -1.0)}
        )
    return trades


def _cfg(*, bankroll=50.0, min_resolved=20, min_win_rate=0.52, max_dd=0.20):
    return SimpleNamespace(
        bankroll=bankroll,
        go_live_min_resolved=min_resolved,
        go_live_min_win_rate=min_win_rate,
        go_live_max_drawdown_pct=max_dd,
    )


def _drawdown_failure(failures: list[str]) -> str | None:
    for f in failures:
        if f.startswith("Drawdown"):
            return f
    return None


def test_gate_uses_mtm_equity_not_raw_notional_basis():
    """(a) MTM-basis vs raw-notional-basis — the load-bearing distinction.

    notional=18.5 on a $50 start is a 63% RAW-notional drawdown (would FAIL).
    But open positions mark to $19.50, lifting MTM equity to $38.00 -> a 24%
    drawdown. The gate MUST report the MTM-derived 24%, NOT the inflated 63%.
    Pre-fix the gate read raw notional and this assertion fails.
    """
    paper = _paper(notional=18.5, resolved_trades=_passing_resolved())
    marks = {"marked_value": 19.5, "unpriced_count": 0}
    with patch.object(main, "cfg", _cfg()), patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        return_value=marks,
    ):
        failures = main._check_go_live_gates(paper)

    dd = _drawdown_failure(failures)
    # MTM equity = 18.5 + 19.5 = 38.0 -> drawdown = (50-38)/50 = 24.0%.
    assert dd is not None, "24% MTM drawdown must still exceed the 20% cap"
    assert "24.0%" in dd, f"gate must report MTM-basis 24.0%, got: {dd!r}"
    # Guard against the pre-fix raw-notional number leaking through.
    assert "63" not in dd, f"raw-notional 63% leaked into gate: {dd!r}"


def test_gate_passes_drawdown_when_mtm_equity_clears_cap():
    """MTM equity high enough -> drawdown gate does NOT fire, even though the
    raw-notional number alone would have failed it. Confirms the basis swap
    actually changes the verdict (a test that can flip under the bug)."""
    paper = _paper(notional=20.0, resolved_trades=_passing_resolved())
    # 20 + 25 = 45 -> drawdown (50-45)/50 = 10% < 20% cap -> passes.
    marks = {"marked_value": 25.0, "unpriced_count": 0}
    with patch.object(main, "cfg", _cfg()), patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        return_value=marks,
    ):
        failures = main._check_go_live_gates(paper)

    assert _drawdown_failure(failures) is None, (
        f"10% MTM drawdown must clear the 20% cap; failures={failures!r}"
    )


def test_unpriced_positions_are_worthless_fail_closed():
    """(b) FAIL-CLOSED: unpriced positions contribute $0 to marked_value, so
    equity stays low and drawdown stays HIGH. compute_open_position_marks
    already excludes unpriced positions from marked_value (worth $0); the gate
    must honor that conservative number and FAIL, not inflate equity by the
    unknown entry cost."""
    paper = _paper(notional=8.0, resolved_trades=_passing_resolved())
    # One priced position worth 2.0; unpriced positions worth $0 -> equity 10.0
    # -> drawdown (50-10)/50 = 80% >> 20% -> FAIL.
    marks = {"marked_value": 2.0, "unpriced_count": 3}
    with patch.object(main, "cfg", _cfg()), patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        return_value=marks,
    ):
        failures = main._check_go_live_gates(paper)

    dd = _drawdown_failure(failures)
    assert dd is not None, "unpriced-as-$0 must keep equity low and FAIL the gate"
    assert "80.0%" in dd, f"unpriced must count $0 (80% dd), got: {dd!r}"


def test_gate_fails_when_marking_raises_never_passes_on_error():
    """(d) marking error -> gate FAILS. The live-cutover gate must never pass
    when it cannot establish MTM equity. The section-8 REPORT may fall back to
    notional offline, but the GATE is stricter: an unknowable equity is treated
    as gate failure, not as 'no drawdown'."""
    paper = _paper(notional=40.0, resolved_trades=_passing_resolved())
    with patch.object(main, "cfg", _cfg()), patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        side_effect=RuntimeError("kalshi REST exploded"),
    ):
        failures = main._check_go_live_gates(paper)

    # Even though 40/50 = 20% raw notional would be a borderline pass, the
    # marking failure itself must surface as a FAIL reason.
    assert any("mark" in f.lower() or "drawdown" in f.lower() for f in failures), (
        f"marking failure must produce a gate failure, got: {failures!r}"
    )


def test_gate_fails_when_marking_returns_none():
    """compute_open_position_marks returns None when the DB is missing. The
    gate must treat an absent MTM snapshot as failure, not pass blindly."""
    paper = _paper(notional=40.0, resolved_trades=_passing_resolved())
    with patch.object(main, "cfg", _cfg()), patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        return_value=None,
    ):
        failures = main._check_go_live_gates(paper)

    assert any("mark" in f.lower() or "drawdown" in f.lower() for f in failures), (
        f"missing MTM snapshot must FAIL the gate, got: {failures!r}"
    )


def test_gate_remains_not_ready_today_on_mtm_drawdown():
    """(c) The gate MUST stay NOT-READY today. On the live numbers the
    peak-to-trough MTM drawdown (~25.9%) still exceeds the 20% cap, AND the
    resolved-trade count is below minimum. This pins the live-cutover decision:
    flipping to live today must remain blocked after the basis change."""
    # 25.9% drawdown: equity = 50 * (1 - 0.259) = 37.05; notional 18.0 + marks 19.05.
    paper = _paper(
        notional=18.0,
        resolved_trades=_passing_resolved(n=5),  # 5 < 20 min_resolved
    )
    marks = {"marked_value": 19.05, "unpriced_count": 0}
    with patch.object(main, "cfg", _cfg()), patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        return_value=marks,
    ):
        failures = main._check_go_live_gates(paper)

    assert failures, "go-live must remain NOT-READY today"
    # Either the drawdown gate (25.9% > 20%) or min_resolved must block it.
    assert _drawdown_failure(failures) is not None or any(
        "Resolved trades" in f for f in failures
    ), f"a blocking gate must fire today; failures={failures!r}"


def test_other_gates_untouched_min_resolved_and_win_rate():
    """min_resolved and win_rate gates retain their existing semantics — the
    P4 change touches ONLY the drawdown basis, nothing else."""
    paper = _paper(
        notional=60.0,  # above start -> no drawdown
        resolved_trades=_passing_resolved(n=3, win_rate=0.0),  # too few + losing
    )
    marks = {"marked_value": 0.0, "unpriced_count": 0}
    with patch.object(main, "cfg", _cfg()), patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        return_value=marks,
    ):
        failures = main._check_go_live_gates(paper)

    assert any("Resolved trades: 3 < minimum 20" in f for f in failures), (
        f"min_resolved gate must still fire; failures={failures!r}"
    )
    assert any("Win rate" in f for f in failures), (
        f"win_rate gate must still fire; failures={failures!r}"
    )
