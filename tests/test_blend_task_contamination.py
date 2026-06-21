"""Tests for the I-10 contamination write-side hook.

Per ``docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md``
§3 I-10. The spec scopes the hook to "tasks/blend_task.py" but the
actual ``INSERT INTO paper_trades`` lives in ``trading/paper_trader.py``;
the helper lives where the INSERT is. These tests cover:

* ``PaperTrader._resolve_cohort_extension`` — fail-soft contract.
* ``paper_trades.cohort_extension`` schema migration — idempotent ALTER
  TABLE so pre-existing DBs gain the column without data loss.

Decision-logic isolation: the helper is *additive only*. These tests
deliberately do NOT cover a full trade roundtrip because the I-10
deliverable promised "no decision-logic change" — exercising the entire
trade pipeline would be out of scope and would couple this test to
unrelated trading concerns.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.edge_replay import contamination_corpus_manager as ccm
from trading.paper_trader import PaperTrader


# ---------------------------------------------------------------------------
# _resolve_cohort_extension
# ---------------------------------------------------------------------------


def test_resolve_cohort_extension_returns_none_when_sentinel_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Point the manager at an empty tmp dir — no sentinel.
    monkeypatch.setattr(
        ccm,
        "CONTAMINATION_SENTINEL_PATH",
        tmp_path / "active_contamination_window.json",
    )

    assert PaperTrader._resolve_cohort_extension() is None


def test_resolve_cohort_extension_returns_tag_when_sentinel_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "active_contamination_window.json"
    monkeypatch.setattr(ccm, "CONTAMINATION_SENTINEL_PATH", sentinel)
    ccm.open_window("EXEC-002-v2", duration_seconds=60)

    tag = PaperTrader._resolve_cohort_extension()

    assert tag is not None
    assert tag.startswith("contamination_window:EXEC-002-v2:")


def test_resolve_cohort_extension_swallows_arbitrary_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-soft: if get_active_window raises, the trade write must not
    crash. We force a synthetic crash by monkeypatching
    get_active_window to raise."""

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated sentinel corruption")

    monkeypatch.setattr(ccm, "get_active_window", boom)

    assert PaperTrader._resolve_cohort_extension() is None


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def test_cohort_extension_column_present_in_migration_list() -> None:
    """The migration list in PaperTrader._migrate_db must reference
    cohort_extension by name; this is the contract the corpus builder
    and contamination exporter rely on."""
    import inspect

    from trading import paper_trader as pt_module

    src = inspect.getsource(pt_module.PaperTrader._migrate_db)
    assert "cohort_extension" in src


def test_alter_table_idempotent(tmp_path: Path) -> None:
    """Running the ADD COLUMN twice must not corrupt the schema.

    Mirrors the production pattern: PaperTrader uses
    ``try/except sqlite3.OperationalError`` -> log-and-continue when the
    column already exists.
    """
    db = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE paper_trades (trade_id TEXT PRIMARY KEY)")
        conn.execute("ALTER TABLE paper_trades ADD COLUMN cohort_extension TEXT")
        conn.commit()
        # Second ALTER must raise the "duplicate column" OperationalError.
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            conn.execute("ALTER TABLE paper_trades ADD COLUMN cohort_extension TEXT")
        assert "duplicate column name" in str(excinfo.value).lower()
    finally:
        conn.close()


def test_paper_trader_init_adds_cohort_extension_column(tmp_path: Path) -> None:
    """Construct a PaperTrader against a fresh DB and verify the
    migration leaves the cohort_extension column present."""
    db = tmp_path / "paper_trades.db"

    PaperTrader(db_path=db, startup_context="test")

    conn = sqlite3.connect(db)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}
    finally:
        conn.close()

    assert "cohort_extension" in cols


def test_paper_trader_init_migrates_legacy_db(tmp_path: Path) -> None:
    """If a pre-I-10 DB exists without the column, PaperTrader init must
    add it without dropping existing rows."""
    db = tmp_path / "paper_trades.db"

    # Pre-create the table WITHOUT cohort_extension (legacy state).
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE paper_trades ("
            "trade_id TEXT PRIMARY KEY, "
            "ts TEXT NOT NULL, "
            "ticker TEXT NOT NULL, "
            "market_title TEXT NOT NULL, "
            "side TEXT NOT NULL, "
            "contracts INTEGER NOT NULL, "
            "price_cents INTEGER NOT NULL, "
            "cost_dollars REAL NOT NULL, "
            "estimated_prob REAL NOT NULL, "
            "entry_price_cents REAL NOT NULL, "
            "edge REAL NOT NULL, "
            "kelly_dollars REAL NOT NULL, "
            "capped_dollars REAL NOT NULL, "
            "signal_headline TEXT NOT NULL, "
            "signal_source TEXT NOT NULL, "
            "keywords_matched TEXT NOT NULL, "
            "reasoning TEXT NOT NULL, "
            "resolved INTEGER DEFAULT 0, "
            "resolved_yes INTEGER, "
            "pnl_dollars REAL"
            ")"
        )
        conn.execute(
            "INSERT INTO paper_trades "
            "(trade_id, ts, ticker, market_title, side, contracts, "
            " price_cents, cost_dollars, estimated_prob, entry_price_cents, "
            " edge, kelly_dollars, capped_dollars, signal_headline, "
            " signal_source, keywords_matched, reasoning) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-1", "2026-05-20T00:00:00Z", "KX-LEGACY-1",
                "Legacy", "yes", 1, 50, 0.5, 0.6, 50.0,
                0.05, 0.5, 0.5, "h", "src", json.dumps([]), "r",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    PaperTrader(db_path=db, startup_context="test")

    conn = sqlite3.connect(db)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}
        legacy = conn.execute(
            "SELECT trade_id, cohort_extension FROM paper_trades "
            "WHERE trade_id = 'legacy-1'"
        ).fetchone()
    finally:
        conn.close()

    assert "cohort_extension" in cols
    # Existing rows survive and the new column starts NULL.
    assert legacy is not None
    assert legacy[0] == "legacy-1"
    assert legacy[1] is None
