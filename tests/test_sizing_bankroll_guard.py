from __future__ import annotations

import sqlite3

from trading.sizing_guard import effective_sizing_bankroll


def test_legacy_positive_outcome_cannot_expand_effective_sizing_bankroll(tmp_path):
    db_path = tmp_path / "paper.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY,
                resolved INTEGER NOT NULL,
                pnl_dollars REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO paper_trades VALUES ('legacy-win', 1, 30.0)"
        )

    assert effective_sizing_bankroll(
        db_path,
        notional_bankroll=80.0,
        configured_starting_bankroll=50.0,
    ) == 50.0


def test_effective_sizing_bankroll_never_inflates_a_loss_reduced_balance(tmp_path):
    db_path = tmp_path / "paper.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE paper_trades (trade_id TEXT PRIMARY KEY, resolved INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO paper_trades VALUES ('legacy-loss', 1)")

    assert effective_sizing_bankroll(
        db_path,
        notional_bankroll=32.0,
        configured_starting_bankroll=50.0,
    ) == 32.0


def test_local_delivery_completion_cannot_expand_sizing_bankroll(tmp_path):
    assert effective_sizing_bankroll(
        tmp_path / "paper.db",
        notional_bankroll=80.0,
        configured_starting_bankroll=50.0,
    ) == 50.0


def test_nonfinite_bankroll_fails_closed_even_with_no_resolved_trades(tmp_path):
    assert effective_sizing_bankroll(
        tmp_path / "paper.db",
        notional_bankroll=float("inf"),
        configured_starting_bankroll=50.0,
    ) == 0.0
