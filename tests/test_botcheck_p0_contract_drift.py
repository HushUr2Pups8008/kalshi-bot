"""Regression coverage for the botcheck p0_contract_version drift alarm.

Audit recommendation #7 from the v0.30.0 vertical-integration audit.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.botcheck import _summarize_p0_contract_version_drift


def _build_paper_trades_db(path: Path, *, with_column: bool = True) -> None:
    """Construct a minimal paper_trades DB whose schema mirrors the
    production table closely enough for the contract-version query.
    """
    cols = [
        "trade_id TEXT PRIMARY KEY",
        "ts TEXT NOT NULL",
    ]
    if with_column:
        cols.append("p0_contract_version INTEGER DEFAULT 1")
    schema = ", ".join(cols)
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"CREATE TABLE paper_trades ({schema})")
        conn.commit()
    finally:
        conn.close()


def _insert(path: Path, trade_id: str, ts: str, version: int | None = 1) -> None:
    conn = sqlite3.connect(path)
    try:
        if version is None:
            conn.execute(
                "INSERT INTO paper_trades (trade_id, ts) VALUES (?, ?)",
                (trade_id, ts),
            )
        else:
            conn.execute(
                "INSERT INTO paper_trades (trade_id, ts, p0_contract_version) "
                "VALUES (?, ?, ?)",
                (trade_id, ts, version),
            )
        conn.commit()
    finally:
        conn.close()


def test_no_recent_events_when_table_empty(tmp_path):
    db = tmp_path / "paper_trades.db"
    _build_paper_trades_db(db)
    result = _summarize_p0_contract_version_drift(db)
    assert result == {
        "status": "no_recent_events",
        "versions": [],
        "recent_row_count": 0,
    }


def test_single_version_when_all_rows_match(tmp_path):
    db = tmp_path / "paper_trades.db"
    _build_paper_trades_db(db)
    for i in range(5):
        _insert(db, f"t{i}", f"2026-05-13T00:0{i}:00+00:00", version=1)
    result = _summarize_p0_contract_version_drift(db)
    assert result == {
        "status": "single_version",
        "versions": [1],
        "recent_row_count": 5,
    }


def test_drift_when_multiple_versions_present(tmp_path):
    db = tmp_path / "paper_trades.db"
    _build_paper_trades_db(db)
    for i in range(3):
        _insert(db, f"t{i}", f"2026-05-13T00:0{i}:00+00:00", version=1)
    for i in range(2):
        _insert(db, f"t-new-{i}", f"2026-05-13T01:0{i}:00+00:00", version=2)
    result = _summarize_p0_contract_version_drift(db)
    assert result["status"] == "drift"
    assert set(result["versions"]) == {1, 2}
    assert result["recent_row_count"] == 5


def test_unknown_when_db_missing(tmp_path):
    db = tmp_path / "nonexistent.db"
    result = _summarize_p0_contract_version_drift(db)
    assert result["status"] == "unknown"
    assert "not found" in result["reason"]


def test_unknown_when_column_missing_from_schema(tmp_path):
    db = tmp_path / "paper_trades.db"
    _build_paper_trades_db(db, with_column=False)
    result = _summarize_p0_contract_version_drift(db)
    assert result["status"] == "unknown"
    assert "p0_contract_version" in result["reason"]


def test_recent_limit_bounds_query_window(tmp_path):
    """The recent_limit parameter bounds the alarm window so historical
    drift in long-archived rows does not perpetually flag DRIFT.
    Insert 5 v1 rows (oldest), then 2 v2 rows (newest), with a recent
    limit of 2 → only the v2 rows show up.
    """
    db = tmp_path / "paper_trades.db"
    _build_paper_trades_db(db)
    for i in range(5):
        _insert(db, f"old-{i}", f"2026-05-01T00:0{i}:00+00:00", version=1)
    for i in range(2):
        _insert(db, f"new-{i}", f"2026-05-13T00:0{i}:00+00:00", version=2)
    result = _summarize_p0_contract_version_drift(db, recent_limit=2)
    assert result["status"] == "single_version"
    assert result["versions"] == [2]
    assert result["recent_row_count"] == 2
