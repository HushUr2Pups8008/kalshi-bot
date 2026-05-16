"""Regression coverage for the POST_FIX_NEW readiness watcher.

The watcher reports the clean-start cohort, excludes failed-start carve-out
rows, and enforces the PROFIT-EDGE-012 resume gate on production-proxy
completeness plus 4-axis admissions.
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

    The watcher reads `bot_state`, `ts`, `ticker`, and the replay/proxy
    fields needed for the POST_FIX_NEW resume gate, so we declare those
    plus a small smattering of NOT-NULL columns mirrored from the
    production schema.
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
                estimated_prob   REAL DEFAULT 0,
                market_yes_price REAL,
                entry_price_cents REAL DEFAULT 50,
                edge             REAL DEFAULT 0,
                kelly_dollars    REAL NOT NULL DEFAULT 0,
                capped_dollars   REAL NOT NULL DEFAULT 0,
                signal_headline  TEXT NOT NULL DEFAULT '',
                signal_source    TEXT NOT NULL DEFAULT '',
                keywords_matched TEXT NOT NULL DEFAULT '[]',
                reasoning        TEXT NOT NULL DEFAULT '',
                series_ticker    TEXT DEFAULT 'KX-SERIES',
                signal_type      TEXT DEFAULT 'blend',
                news_class       TEXT DEFAULT 'news',
                market_family    TEXT DEFAULT 'politics',
                resolved_yes     INTEGER DEFAULT 1,
                llm_confidence   REAL DEFAULT 0.7,
                readiness_admitted INTEGER DEFAULT 1
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


def _init_runtime_schema_db(path: Path, *, sentinel_ts: str | None) -> None:
    """Build a DB with the current production paper_trades columns only."""
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
                entry_price_cents REAL NOT NULL DEFAULT 50,
                edge             REAL NOT NULL DEFAULT 0,
                kelly_dollars    REAL NOT NULL DEFAULT 0,
                capped_dollars   REAL NOT NULL DEFAULT 0,
                signal_headline  TEXT NOT NULL DEFAULT '',
                signal_source    TEXT NOT NULL DEFAULT '',
                keywords_matched TEXT NOT NULL DEFAULT '[]',
                reasoning        TEXT NOT NULL DEFAULT '',
                resolved_yes     INTEGER,
                series_ticker    TEXT,
                signal_type      TEXT DEFAULT 'news',
                llm_confidence   REAL
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


def _insert_trade(
    db: Path,
    *,
    trade_id: str,
    ts: str,
    ticker: str,
    signal_source: str = "source-a",
    market_family: str = "politics",
    signal_type: str = "blend",
    news_class: str = "news",
    readiness_admitted: int = 1,
    complete: bool = True,
) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            INSERT INTO paper_trades (
                trade_id, ts, ticker, signal_source, market_family,
                signal_type, news_class, readiness_admitted,
                entry_price_cents, edge, estimated_prob, llm_confidence,
                resolved_yes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                ts,
                ticker,
                signal_source,
                market_family,
                signal_type,
                news_class,
                readiness_admitted,
                50.0 if complete else None,
                0.03 if complete else None,
                0.55 if complete else None,
                0.7 if complete else None,
                1 if complete else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_runtime_trade(
    db: Path,
    *,
    trade_id: str,
    ts: str,
    ticker: str,
    estimated_prob: float = 0.65,
    llm_confidence: float = 0.9,
    signal_source: str = "source-a",
    series_ticker: str = "KX-SERIES",
    signal_type: str = "news",
) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            INSERT INTO paper_trades (
                trade_id, ts, ticker, estimated_prob, entry_price_cents, edge,
                signal_source, resolved_yes, series_ticker, signal_type,
                llm_confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                ts,
                ticker,
                estimated_prob,
                50.0,
                0.03,
                signal_source,
                1,
                series_ticker,
                signal_type,
                llm_confidence,
            ),
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
    assert report["post_clean_start_production_proxy_complete_rows"] == 12
    assert report["production_proxy_completeness_ratio"] == 1.0


def test_ready_when_full_resume_gate_met(tmp_path):
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
    assert report["max_4axis_bin_admissions"] == 10
    assert report["qualifying_4axis_bin_count"] == 1


def test_incomplete_production_proxy_rows_do_not_credit_resume_floor(tmp_path):
    db = tmp_path / "paper_trades.db"
    _init_db(db, sentinel_ts=SENTINEL)
    for i in range(12):
        _insert_trade(
            db,
            trade_id=f"t-incomplete-{i}",
            ts=(
                datetime(2026, 5, 13, 0, 5, tzinfo=timezone.utc)
                + timedelta(minutes=i * 5)
            ).isoformat(),
            ticker=f"KX-T{i % 4}",
            complete=False,
        )
    report = collect_readiness(
        db_path=db,
        clean_start_ts=CLEAN_START,
        min_trades=10,
        min_tickers=3,
    )
    assert report["post_clean_start_row_count"] == 12
    assert report["post_clean_start_production_proxy_complete_rows"] == 0
    assert report["production_proxy_completeness_ratio"] == 0.0
    assert report["readiness"] == "NOT_READY"
    assert "production-proxy-complete" in (report["reason"] or "")


def test_not_ready_when_no_four_axis_bin_has_enough_admissions(tmp_path):
    db = tmp_path / "paper_trades.db"
    _init_db(db, sentinel_ts=SENTINEL)
    for i in range(12):
        _insert_trade(
            db,
            trade_id=f"t-bin-{i}",
            ts=(
                datetime(2026, 5, 13, 0, 5, tzinfo=timezone.utc)
                + timedelta(minutes=i * 5)
            ).isoformat(),
            ticker=f"KX-T{i % 4}",
            signal_source=f"source-{i}",
        )
    report = collect_readiness(
        db_path=db,
        clean_start_ts=CLEAN_START,
        min_trades=10,
        min_tickers=3,
        min_bin_admissions=10,
    )
    assert report["post_clean_start_production_proxy_complete_rows"] == 12
    assert report["max_4axis_bin_admissions"] == 1
    assert report["qualifying_4axis_bin_count"] == 0
    assert report["readiness"] == "NOT_READY"
    assert "4-axis bin" in (report["reason"] or "")


def test_not_ready_when_completeness_ratio_below_floor(tmp_path):
    db = tmp_path / "paper_trades.db"
    _init_db(db, sentinel_ts=SENTINEL)
    for i in range(9):
        _insert_trade(
            db,
            trade_id=f"t-complete-{i}",
            ts=(
                datetime(2026, 5, 13, 0, 5, tzinfo=timezone.utc)
                + timedelta(minutes=i * 5)
            ).isoformat(),
            ticker=f"KX-T{i % 3}",
            complete=True,
        )
    for i in range(2):
        _insert_trade(
            db,
            trade_id=f"t-incomplete-{i}",
            ts=(
                datetime(2026, 5, 13, 1, 5, tzinfo=timezone.utc)
                + timedelta(minutes=i * 5)
            ).isoformat(),
            ticker=f"KX-I{i}",
            complete=False,
        )
    report = collect_readiness(
        db_path=db,
        clean_start_ts=CLEAN_START,
        min_trades=9,
        min_tickers=3,
        min_bin_admissions=9,
        min_completeness_ratio=0.95,
    )
    assert report["post_clean_start_row_count"] == 11
    assert report["post_clean_start_production_proxy_complete_rows"] == 9
    assert report["production_proxy_completeness_ratio"] < 0.95
    assert report["readiness"] == "NOT_READY"
    assert "completeness" in (report["reason"] or "")


def test_runtime_schema_fallback_axes_can_qualify_4axis_bin(tmp_path):
    db = tmp_path / "paper_trades.db"
    _init_runtime_schema_db(db, sentinel_ts=SENTINEL)
    for i in range(10):
        _insert_runtime_trade(
            db,
            trade_id=f"t-runtime-{i}",
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
        min_bin_admissions=10,
    )
    assert report["readiness"] == "READY"
    assert report["max_4axis_bin_admissions"] == 10
    assert report["top_4axis_bins"][0]["market_family"] == "KX-SERIES"
    assert report["top_4axis_bins"][0]["news_class"] == "unknown"


def test_runtime_schema_uses_replay_admission_thresholds_when_flag_absent(tmp_path):
    db = tmp_path / "paper_trades.db"
    _init_runtime_schema_db(db, sentinel_ts=SENTINEL)
    for i in range(10):
        _insert_runtime_trade(
            db,
            trade_id=f"t-neutral-{i}",
            ts=(
                datetime(2026, 5, 13, 0, 5, tzinfo=timezone.utc)
                + timedelta(minutes=i * 5)
            ).isoformat(),
            ticker=f"KX-T{i % 3}",
            estimated_prob=0.55,
            llm_confidence=0.9,
        )
    report = collect_readiness(
        db_path=db,
        clean_start_ts=CLEAN_START,
        min_trades=10,
        min_tickers=3,
        min_bin_admissions=10,
    )
    assert report["post_clean_start_production_proxy_complete_rows"] == 10
    assert report["max_4axis_bin_admissions"] == 0
    assert report["readiness"] == "NOT_READY"
    assert "4-axis bin" in (report["reason"] or "")


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
        "post_clean_start_production_proxy_complete_rows",
        "production_proxy_completeness_ratio",
        "min_production_proxy_complete_required",
        "min_completeness_ratio_required",
        "min_4axis_bin_admissions_required",
        "qualifying_4axis_bin_count",
        "max_4axis_bin_admissions",
        "top_4axis_bins",
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
