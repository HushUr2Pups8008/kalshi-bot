"""Regression coverage for the POST_FIX_NEW readiness watcher.

The watcher reports the count of paper_trades rows after a clean-start
timestamp, distinguishing them from rows in the failed-start carve-out
window between the `p0_price_fix_deployed_ts` sentinel and the
operator-supplied clean-start timestamp.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.edge_replay.post_fix_new_readiness_status import (
    DEFAULT_CLEAN_START_TS,
    ReadinessError,
    _parse_iso,
    collect_readiness,
    main,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "edge_replay"
    / "post_fix_new_readiness_status.py"
)


def _init_db(path: Path, *, sentinel_ts: str | None) -> None:
    """Build a minimal paper_trades.db compatible with the watcher.

    The watcher only reads `bot_state` + `ts` and `ticker` from
    `paper_trades`, so we declare the columns it touches plus a small
    smattering of NOT-NULL columns mirrored from the production schema.
    """
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE bot_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE paper_trades (
                trade_id         TEXT PRIMARY KEY,
                ts               TEXT NOT NULL,
                ticker           TEXT NOT NULL,
                market_title     TEXT NOT NULL DEFAULT '',
                side             TEXT NOT NULL DEFAULT 'yes',
                contracts        INTEGER NOT NULL DEFAULT 1,
                price_cents      INTEGER NOT NULL DEFAULT 0,
                cost_dollars     REAL NOT NULL DEFAULT 0,
                estimated_prob   REAL NOT NULL DEFAULT 0,
                market_yes_price REAL NOT NULL DEFAULT 0,
                edge             REAL NOT NULL DEFAULT 0,
                kelly_dollars    REAL NOT NULL DEFAULT 0,
                capped_dollars   REAL NOT NULL DEFAULT 0,
                signal_headline  TEXT NOT NULL DEFAULT '',
                signal_source    TEXT NOT NULL DEFAULT '',
                keywords_matched TEXT NOT NULL DEFAULT '[]',
                reasoning        TEXT NOT NULL DEFAULT ''
            );
            """
        )
        if sentinel_ts is not None:
            conn.execute(
                "INSERT INTO bot_state (key, value) VALUES (?, ?)",
                ("p0_price_fix_deployed_ts", sentinel_ts),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_trade(db: Path, *, trade_id: str, ts: str, ticker: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO paper_trades (trade_id, ts, ticker) VALUES (?, ?, ?)",
            (trade_id, ts, ticker),
        )
        conn.commit()
    finally:
        conn.close()


SENTINEL = "2026-05-12T23:50:04.422696+00:00"
CLEAN_START = "2026-05-13T00:02:37Z"


def test_missing_sentinel_reports_not_ready_with_clear_reason(tmp_path):
    db = tmp_path / "paper_trades.db"
    _init_db(db, sentinel_ts=None)
    report = collect_readiness(
        db_path=db,
        clean_start_ts=CLEAN_START,
        min_trades=10,
        min_tickers=3,
    )
    assert report["readiness"] == "NOT_READY"
    assert report["sentinel_ts"] is None
    assert "sentinel" in (report["reason"] or "").lower()
    # Even with no sentinel, carve-out count is 0 (we never compute it
    # against a missing boundary) and post-clean-start counts are honest.
    assert report["carve_out_row_count"] == 0
    assert report["post_clean_start_row_count"] == 0


def test_carve_out_rows_are_counted_separately_and_excluded_from_readiness(tmp_path):
    db = tmp_path / "paper_trades.db"
    _init_db(db, sentinel_ts=SENTINEL)
    # Two rows in the carve-out window (sentinel <= ts < clean_start).
    _insert_trade(db, trade_id="t-carve-1", ts="2026-05-12T23:51:00+00:00", ticker="KX-A")
    _insert_trade(db, trade_id="t-carve-2", ts="2026-05-13T00:01:00+00:00", ticker="KX-B")
    # One row post-clean-start.
    _insert_trade(db, trade_id="t-clean-1", ts="2026-05-13T00:05:00+00:00", ticker="KX-C")

    report = collect_readiness(
        db_path=db,
        clean_start_ts=CLEAN_START,
        min_trades=10,
        min_tickers=3,
    )
    assert report["carve_out_row_count"] == 2
    assert report["post_clean_start_row_count"] == 1
    assert report["post_clean_start_distinct_tickers"] == 1
    # Carve-out rows must NOT have been counted toward readiness.
    assert report["readiness"] == "NOT_READY"
    assert "min_trades" in (report["reason"] or "")


def test_rows_after_clean_start_count_toward_readiness(tmp_path):
    db = tmp_path / "paper_trades.db"
    _init_db(db, sentinel_ts=SENTINEL)
    for i in range(12):
        _insert_trade(
            db,
            trade_id=f"t-clean-{i}",
            ts=(
                datetime(2026, 5, 13, 0, 5, tzinfo=timezone.utc)
                + timedelta(minutes=i * 5)
            ).isoformat(),
            ticker=f"KX-T{i % 4}",
        )
    report = collect_readiness(
        db_path=db,
        clean_start_ts=CLEAN_START,
        min_trades=10,
        min_tickers=3,
    )
    assert report["post_clean_start_row_count"] == 12
    assert report["post_clean_start_distinct_tickers"] == 4


def test_ready_when_both_thresholds_met(tmp_path):
    db = tmp_path / "paper_trades.db"
    _init_db(db, sentinel_ts=SENTINEL)
    for i in range(10):
        _insert_trade(
            db,
            trade_id=f"t-clean-{i}",
            ts=(
                datetime(2026, 5, 13, 0, 5, tzinfo=timezone.utc)
                + timedelta(minutes=i * 5)
            ).isoformat(),
            ticker=f"KX-T{i % 3}",
        )
    report = collect_readiness(
        db_path=db,
        clean_start_ts=CLEAN_START,
        min_trades=10,
        min_tickers=3,
    )
    assert report["readiness"] == "READY"
    assert report["reason"] is None


def test_json_output_has_stable_keys(tmp_path, capsys):
    db = tmp_path / "paper_trades.db"
    _init_db(db, sentinel_ts=SENTINEL)
    _insert_trade(db, trade_id="t-clean-1", ts="2026-05-13T00:05:00+00:00", ticker="KX-A")
    exit_code = main(["--db", str(db), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    expected_keys = {
        "db_path",
        "sentinel_ts",
        "clean_start_ts",
        "carve_out_start",
        "carve_out_end",
        "carve_out_row_count",
        "post_clean_start_row_count",
        "post_clean_start_distinct_tickers",
        "min_trades_required",
        "min_tickers_required",
        "readiness",
        "reason",
    }
    assert set(payload.keys()) == expected_keys, payload
    assert payload["sentinel_ts"] == SENTINEL
    assert payload["readiness"] == "NOT_READY"  # only 1 trade


def test_malformed_clean_start_timestamp_returns_nonzero_exit(tmp_path, capsys):
    db = tmp_path / "paper_trades.db"
    _init_db(db, sentinel_ts=SENTINEL)
    exit_code = main(
        ["--db", str(db), "--clean-start-ts", "not-a-real-timestamp"]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert "clean-start" in captured.err


def test_missing_db_returns_nonzero_exit(tmp_path, capsys):
    db = tmp_path / "definitely_not_a_real_db.db"  # never created
    exit_code = main(["--db", str(db)])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "ERROR" in captured.err


def test_collect_readiness_raises_when_paper_trades_table_missing(tmp_path):
    db = tmp_path / "no_paper_trades.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            "CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        )
        conn.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?)",
            ("p0_price_fix_deployed_ts", SENTINEL),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ReadinessError, match="paper_trades"):
        collect_readiness(
            db_path=db,
            clean_start_ts=CLEAN_START,
            min_trades=10,
            min_tickers=3,
        )


def test_parse_iso_accepts_z_suffix_and_normalizes_to_utc():
    parsed = _parse_iso("2026-05-13T00:02:37Z", label="clean-start")
    assert parsed.tzinfo is not None
    assert parsed.isoformat() == "2026-05-13T00:02:37+00:00"


def test_default_clean_start_constant_is_post_hotfix_bootstrap():
    """The default clean-start corresponds to the hotfix bootstrap; any
    change to this constant in production code should be deliberate.
    """
    assert DEFAULT_CLEAN_START_TS == "2026-05-13T00:02:37Z"


def test_script_runs_as_subprocess_without_writing_db(tmp_path):
    """Watcher must be invocable as a script and must not mutate the DB.

    Captures both the read-only invariant (file mtime unchanged) and
    the CLI surface (exit code 0 on a NOT_READY report).
    """
    db = tmp_path / "paper_trades.db"
    _init_db(db, sentinel_ts=SENTINEL)
    mtime_before = db.stat().st_mtime_ns

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--db", str(db), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["readiness"] == "NOT_READY"

    mtime_after = db.stat().st_mtime_ns
    assert mtime_before == mtime_after, "DB mtime changed — watcher mutated state"
