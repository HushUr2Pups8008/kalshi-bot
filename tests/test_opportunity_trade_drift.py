"""Regression coverage for the opportunity-vs-trade drift watcher.

The watcher correlates JSONL ``OPPORTUNITY`` events against ``paper_trades``
DB rows, both filtered to the post-clean-start window, and surfaces the
"silent attrition" condition where the executor logs candidates but none
reach the trade table.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from unittest.mock import patch

from scripts.observability.opportunity_trade_drift import (
    DEFAULT_CLEAN_START_TS,
    WatcherError,
    _build_argparser,
    _count_opportunities,
    _iter_jsonl_files,
    _parse_iso,
    collect_report,
    format_human_report,
    main,
)

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "observability"
    / "opportunity_trade_drift.py"
)

CLEAN_START = "2026-05-13T00:02:37Z"
CLEAN_START_DT = datetime(2026, 5, 13, 0, 2, 37, tzinfo=timezone.utc)

# ── DB helpers ────────────────────────────────────────────────────────────────


def _init_db(path: Path, *, with_bot_state: bool = True) -> None:
    """Build a minimal paper_trades.db compatible with the watcher.

    The watcher only reads ``bot_state`` (for sentinel existence) + ``ts``
    and ``ticker`` from ``paper_trades``.
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


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _opportunity(ts: str, ticker: str = "KX-TICKER") -> dict:
    return {
        "type": "OPPORTUNITY",
        "ts": ts,
        "ticker": ticker,
        "edge": 0.1,
    }


def _ts(minutes_after_clean: int) -> str:
    """ISO timestamp N minutes after CLEAN_START."""
    return (CLEAN_START_DT + timedelta(minutes=minutes_after_clean)).isoformat()


def _ts_before(minutes_before_clean: int) -> str:
    """ISO timestamp N minutes BEFORE CLEAN_START."""
    return (CLEAN_START_DT - timedelta(minutes=minutes_before_clean)).isoformat()


# ── Unit tests: collect_report ────────────────────────────────────────────────


def test_normal_when_ratio_exceeds_threshold(tmp_path):
    """10 OPPORTUNITY + 2 PAPER_TRADE post-clean → ratio 0.2 > 0.05 → NORMAL."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)
    for i in range(2):
        _insert_trade(db, trade_id=f"t-{i}", ts=_ts(i * 5 + 1), ticker="KX-A")

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(10)])

    now_ts = _ts(60 * 10)  # 10 hours elapsed
    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=now_ts,
    )
    assert report["verdict"] == "NORMAL", report
    assert report["opportunity_count"] == 10
    assert report["paper_trade_count"] == 2
    assert report["drift_ratio"] == pytest.approx(0.2, abs=1e-4)


def test_alarm_when_zero_trades_after_window(tmp_path):
    """5 OPPORTUNITY, 0 PAPER_TRADE, 10h elapsed → ALARM."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(5)])

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["verdict"] == "ALARM", report
    assert report["paper_trade_count"] == 0
    assert report["opportunity_count"] == 5


def test_degraded_when_ratio_under_threshold(tmp_path):
    """100 OPPORTUNITY, 2 PAPER_TRADE, 10h → ratio 0.02 < 0.05 → DEGRADED."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)
    for i in range(2):
        _insert_trade(db, trade_id=f"t-{i}", ts=_ts(i * 5 + 1), ticker="KX-A")

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(100)])

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["verdict"] == "DEGRADED", report
    assert report["drift_ratio"] == pytest.approx(0.02, abs=1e-4)


def test_insufficient_data_when_few_opportunities(tmp_path):
    """1 OPPORTUNITY, 0 PAPER_TRADE → INSUFFICIENT_DATA (< min 3)."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(1))])

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["verdict"] == "INSUFFICIENT_DATA", report


def test_insufficient_window_when_too_early(tmp_path):
    """10 OPPORTUNITY, 0 PAPER_TRADE, 1h elapsed (< 6h) → INSUFFICIENT_WINDOW."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(10)])

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60),  # 60 minutes = 1 hour
    )
    assert report["verdict"] == "INSUFFICIENT_WINDOW", report


def test_excludes_records_before_clean_start(tmp_path):
    """JSONL with pre-clean OPPORTUNITY records must not be counted."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    records = [
        _opportunity(_ts_before(60)),  # before clean start
        _opportunity(_ts_before(30)),  # before clean start
        _opportunity(_ts(1)),           # after clean start — the only one that counts
    ]
    _write_jsonl(live_log, records)

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["opportunity_count"] == 1
    assert report["verdict"] == "INSUFFICIENT_DATA"


def test_excludes_paper_trades_before_clean_start(tmp_path):
    """paper_trades rows with ts < clean_start_ts must not be counted."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)
    # Row before clean start
    _insert_trade(db, trade_id="pre-1", ts=_ts_before(60), ticker="KX-A")
    # Row after clean start
    _insert_trade(db, trade_id="post-1", ts=_ts(5), ticker="KX-B")

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(5)])

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["paper_trade_count"] == 1


def test_archive_files_are_combined_with_live(tmp_path):
    """Opportunities split across live + archive partition → both counted."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(3)])

    archive_file = tmp_path / "archive" / "2026" / "05" / "2026-05-13.jsonl"
    _write_jsonl(archive_file, [_opportunity(_ts(i + 100)) for i in range(3)])

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["opportunity_count"] == 6


def test_malformed_jsonl_line_increments_parse_errors(tmp_path):
    """`{not json` does not crash; is counted as parse_error."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    live_log.parent.mkdir(parents=True, exist_ok=True)
    with live_log.open("w", encoding="utf-8") as fh:
        fh.write("{not json\n")
        fh.write(json.dumps(_opportunity(_ts(1))) + "\n")

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["parse_errors"] == 1
    assert report["opportunity_count"] == 1


def test_blank_jsonl_lines_are_skipped(tmp_path):
    """Empty and whitespace-only lines do NOT increment parse_errors."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    live_log.parent.mkdir(parents=True, exist_ok=True)
    with live_log.open("w", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write("   \n")
        fh.write(json.dumps(_opportunity(_ts(1))) + "\n")

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["parse_errors"] == 0


def test_records_without_ts_are_parse_errors(tmp_path):
    """A record missing the ``ts`` field counts as parse_error."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    live_log.parent.mkdir(parents=True, exist_ok=True)
    with live_log.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "OPPORTUNITY", "ticker": "KX-A"}) + "\n")

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["parse_errors"] == 1


def test_records_with_unparseable_ts_are_parse_errors(tmp_path):
    """`"ts": "not-a-date"` counts as parse_error."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    live_log.parent.mkdir(parents=True, exist_ok=True)
    with live_log.open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"type": "OPPORTUNITY", "ts": "not-a-date", "ticker": "KX-A"})
            + "\n"
        )

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["parse_errors"] == 1


def test_records_of_other_types_are_ignored(tmp_path):
    """Records with ``type`` != ``OPPORTUNITY`` are not counted as opportunities."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(
        live_log,
        [
            {"type": "SIGNAL", "ts": _ts(1), "ticker": "KX-A"},
            {"type": "PAPER_TRADE", "ts": _ts(2), "ticker": "KX-B"},
            _opportunity(_ts(3)),  # the only OPPORTUNITY
        ],
    )

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["opportunity_count"] == 1


def test_watcher_error_when_db_missing(tmp_path):
    """Nonexistent DB path → raises WatcherError."""
    db = tmp_path / "does_not_exist.db"
    live_log = tmp_path / "live" / "trades.jsonl"

    with pytest.raises(WatcherError, match="not found"):
        collect_report(
            db_path=db,
            trade_log_live=live_log,
            trade_log_archive_dir=tmp_path / "archive",
            clean_start_ts=CLEAN_START,
            min_opportunities=3,
            max_hours_without_trade=6.0,
            drift_ratio_threshold=0.05,
            now_ts=_ts(60 * 10),
        )


def test_watcher_error_when_bot_state_missing(tmp_path):
    """DB exists but no bot_state table → raises WatcherError."""
    db = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                ticker TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    live_log = tmp_path / "live" / "trades.jsonl"
    with pytest.raises(WatcherError, match="bot_state"):
        collect_report(
            db_path=db,
            trade_log_live=live_log,
            trade_log_archive_dir=tmp_path / "archive",
            clean_start_ts=CLEAN_START,
            min_opportunities=3,
            max_hours_without_trade=6.0,
            drift_ratio_threshold=0.05,
            now_ts=_ts(60 * 10),
        )


def test_distinct_tickers_count_is_correct(tmp_path):
    """3 PAPER_TRADE rows on 2 distinct tickers → distinct count = 2."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)
    _insert_trade(db, trade_id="t-1", ts=_ts(1), ticker="KX-ALPHA")
    _insert_trade(db, trade_id="t-2", ts=_ts(2), ticker="KX-BETA")
    _insert_trade(db, trade_id="t-3", ts=_ts(3), ticker="KX-ALPHA")  # duplicate ticker

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(5)])

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["paper_trade_count"] == 3
    assert report["paper_trade_distinct_tickers"] == 2


def test_zero_opportunity_quiet_day(tmp_path):
    """0 OPPORTUNITY + hours >= max → still INSUFFICIENT_DATA (quiet news day)."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [])  # empty

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["verdict"] == "INSUFFICIENT_DATA"
    assert report["drift_ratio"] is None


def test_live_log_missing_is_treated_as_zero_records(tmp_path):
    """Missing live trade-log path → fine, zero records from that source."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "nonexistent" / "trades.jsonl"  # does not exist

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["opportunity_count"] == 0
    assert report["verdict"] == "INSUFFICIENT_DATA"


def test_archive_dir_missing_is_treated_as_zero_records(tmp_path):
    """Missing archive dir → fine, zero records from archive."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(5)])

    archive_dir = tmp_path / "nonexistent_archive"  # does not exist

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=archive_dir,
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["opportunity_count"] == 5


# ── CLI integration tests ─────────────────────────────────────────────────────


def test_cli_human_report_exits_zero_with_alarm_verdict(tmp_path):
    """ALARM verdict → exit code 0 (not 2), 'ALARM' appears in stdout."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(5)])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--db",
            str(db),
            "--trade-log-live",
            str(live_log),
            "--trade-log-archive-dir",
            str(tmp_path / "archive"),
            "--clean-start-ts",
            CLEAN_START,
            "--now",
            _ts(60 * 10),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ALARM" in result.stdout


def test_cli_json_output_is_valid_and_has_expected_keys(tmp_path):
    """--json flag emits parseable JSON with all required keys."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)
    _insert_trade(db, trade_id="t-1", ts=_ts(1), ticker="KX-A")

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(10)])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--db",
            str(db),
            "--trade-log-live",
            str(live_log),
            "--trade-log-archive-dir",
            str(tmp_path / "archive"),
            "--clean-start-ts",
            CLEAN_START,
            "--now",
            _ts(60 * 10),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    expected_keys = {
        "db_path",
        "trade_log_live",
        "trade_log_archive_dir",
        "clean_start_ts",
        "now_ts",
        "hours_elapsed",
        "opportunity_count",
        "paper_trade_count",
        "paper_trade_distinct_tickers",
        "drift_ratio",
        "parse_errors",
        "min_opportunities",
        "max_hours_without_trade",
        "drift_ratio_threshold",
        "verdict",
        "reason",
    }
    assert set(payload.keys()) == expected_keys, f"Unexpected keys: {set(payload.keys()) ^ expected_keys}"


def test_cli_exits_two_on_watcher_error(tmp_path):
    """Missing DB path → exit code 2, error line on stderr."""
    db = tmp_path / "definitely_missing.db"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--db",
            str(db),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "ERROR" in result.stderr


def test_cli_uses_now_override_for_deterministic_window(tmp_path):
    """--now flag controls hours_elapsed in output."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [])

    # 2 hours after clean start
    now_ts = _ts(120)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--db",
            str(db),
            "--trade-log-live",
            str(live_log),
            "--trade-log-archive-dir",
            str(tmp_path / "archive"),
            "--clean-start-ts",
            CLEAN_START,
            "--now",
            now_ts,
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["hours_elapsed"] == pytest.approx(2.0, abs=0.01)


# ── Additional coverage: format_human_report and in-process main ──────────────


def test_format_human_report_includes_all_key_labels(tmp_path):
    """format_human_report output contains all expected label strings."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)
    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(5)])

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    output = format_human_report(report)
    assert "verdict" in output.lower()
    assert "ALARM" in output
    assert "reason" in output.lower()
    assert "opportunity_count" in output.lower()
    assert "drift_ratio" in output.lower()


def test_format_human_report_normal_has_no_reason_line(tmp_path):
    """NORMAL verdict → no 'reason' line emitted (reason is None)."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)
    for i in range(2):
        _insert_trade(db, trade_id=f"t-{i}", ts=_ts(i + 1), ticker="KX-A")

    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(10)])

    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=tmp_path / "archive",
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["verdict"] == "NORMAL"
    output = format_human_report(report)
    assert "reason" not in output.lower()


def test_main_in_process_human_output(tmp_path, capsys):
    """main() called in-process with human-readable output exits 0."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)
    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(5)])

    exit_code = main(
        [
            "--db",
            str(db),
            "--trade-log-live",
            str(live_log),
            "--trade-log-archive-dir",
            str(tmp_path / "archive"),
            "--clean-start-ts",
            CLEAN_START,
            "--now",
            _ts(60 * 10),
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "ALARM" in captured.out


def test_main_in_process_json_output(tmp_path, capsys):
    """main() with --json flag emits valid JSON and exits 0."""
    db = tmp_path / "paper_trades.db"
    _init_db(db)
    live_log = tmp_path / "live" / "trades.jsonl"
    _write_jsonl(live_log, [_opportunity(_ts(i + 1)) for i in range(5)])

    exit_code = main(
        [
            "--db",
            str(db),
            "--trade-log-live",
            str(live_log),
            "--trade-log-archive-dir",
            str(tmp_path / "archive"),
            "--clean-start-ts",
            CLEAN_START,
            "--now",
            _ts(60 * 10),
            "--json",
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["verdict"] == "ALARM"


def test_main_exits_two_on_watcher_error(tmp_path, capsys):
    """main() returns 2 and prints to stderr when watcher fails."""
    db = tmp_path / "missing.db"
    exit_code = main(["--db", str(db)])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "ERROR" in captured.err


def test_watcher_error_when_paper_trades_table_missing(tmp_path):
    """DB has bot_state but no paper_trades table → raises WatcherError."""
    db = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    live_log = tmp_path / "live" / "trades.jsonl"
    with pytest.raises(WatcherError, match="paper_trades"):
        collect_report(
            db_path=db,
            trade_log_live=live_log,
            trade_log_archive_dir=tmp_path / "archive",
            clean_start_ts=CLEAN_START,
            min_opportunities=3,
            max_hours_without_trade=6.0,
            drift_ratio_threshold=0.05,
            now_ts=_ts(60 * 10),
        )


def test_default_clean_start_constant_is_correct():
    """The default clean-start constant must match the post-hotfix bootstrap."""
    assert DEFAULT_CLEAN_START_TS == "2026-05-13T00:02:37Z"


def test_parse_iso_accepts_z_suffix():
    """_parse_iso tolerates trailing Z and returns UTC-aware datetime."""
    parsed = _parse_iso("2026-05-13T00:02:37Z", label="test")
    assert parsed.tzinfo is not None
    assert parsed.isoformat() == "2026-05-13T00:02:37+00:00"


# ── New tests for adversarial review fixes ────────────────────────────────────


def test_iter_jsonl_files_deduplicates_when_archive_contains_live(tmp_path):
    """FIX 1: live.jsonl inside archive/ must not be counted twice.

    When --trade-log-archive-dir is the parent of the live log, the live log
    is both passed explicitly AND found via rglob.  After deduplication the
    opportunity_count must equal the actual record count, not 2x.
    """
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    live_log = archive_dir / "live.jsonl"

    # Write 3 OPPORTUNITY records to the live log
    records = [_opportunity(_ts(i + 1)) for i in range(3)]
    _write_jsonl(live_log, records)

    # Pass both live path AND archive dir that contains that very file
    paths = _iter_jsonl_files(live_log, archive_dir)
    resolved = [p.resolve() for p in paths]
    # The live log must appear exactly once even though it's inside archive_dir
    assert resolved.count(live_log.resolve()) == 1, (
        f"Expected live log to appear once; got paths={paths}"
    )

    # Also verify end-to-end: opportunity_count must be 3, not 6
    db = tmp_path / "paper_trades.db"
    _init_db(db)
    report = collect_report(
        db_path=db,
        trade_log_live=live_log,
        trade_log_archive_dir=archive_dir,
        clean_start_ts=CLEAN_START,
        min_opportunities=3,
        max_hours_without_trade=6.0,
        drift_ratio_threshold=0.05,
        now_ts=_ts(60 * 10),
    )
    assert report["opportunity_count"] == 3, (
        f"Expected 3 (not 6 from double-count); got {report['opportunity_count']}"
    )


def test_count_opportunities_skips_unreadable_archive_file_and_increments_parse_errors(
    tmp_path,
):
    """FIX 2: OSError on path.open() is caught, increments parse_errors, continues.

    One readable archive file with 2 OPPORTUNITY records + one unreadable file.
    After the fix: opportunity_count == 2, parse_errors >= 1, no crash.
    """
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    # Good file: 2 OPPORTUNITY records
    good_file = archive_dir / "good.jsonl"
    _write_jsonl(good_file, [_opportunity(_ts(i + 1)) for i in range(2)])

    # Bad file: exists on disk but we simulate unreadability via mock
    bad_file = archive_dir / "bad.jsonl"
    _write_jsonl(bad_file, [_opportunity(_ts(10))])  # content doesn't matter

    clean_start_dt = _parse_iso(CLEAN_START, label="test")

    real_open = Path.open

    def patched_open(self, *args, **kwargs):
        if self.resolve() == bad_file.resolve():
            raise OSError("simulated permission denied")
        return real_open(self, *args, **kwargs)

    with patch.object(Path, "open", patched_open):
        opp_count, parse_errors = _count_opportunities(
            tmp_path / "nonexistent_live.jsonl",  # no live log
            archive_dir,
            clean_start_dt,
        )

    assert opp_count == 2, f"Expected 2 opportunities from good file; got {opp_count}"
    assert parse_errors >= 1, f"Expected at least 1 parse_error for unreadable file; got {parse_errors}"


def test_no_non_ascii_in_runtime_strings(tmp_path):
    """FIX 3: All runtime-emitted strings (report, format_human_report, argparse help)
    must be ASCII-encodable — no em-dashes or other non-ASCII bytes.
    """
    db = tmp_path / "paper_trades.db"
    _init_db(db)

    # Build reports for each non-WATCHER_ERROR verdict
    verdicts_setup = [
        # INSUFFICIENT_DATA: 0 opportunities
        dict(
            live_records=[],
            trades=[],
            now_offset=60 * 10,
        ),
        # INSUFFICIENT_WINDOW: enough opportunities, too early
        dict(
            live_records=[_opportunity(_ts(i + 1)) for i in range(5)],
            trades=[],
            now_offset=60,  # 1 hour
        ),
        # ALARM: enough opportunities, past window, 0 trades
        dict(
            live_records=[_opportunity(_ts(i + 1)) for i in range(5)],
            trades=[],
            now_offset=60 * 10,
        ),
        # DEGRADED: lots of opportunities, very few trades
        dict(
            live_records=[_opportunity(_ts(i + 1)) for i in range(100)],
            trades=[("t-0", _ts(1), "KX-A"), ("t-1", _ts(2), "KX-B")],
            now_offset=60 * 10,
        ),
        # NORMAL: enough trades vs opportunities
        dict(
            live_records=[_opportunity(_ts(i + 1)) for i in range(10)],
            trades=[("t-0", _ts(1), "KX-A"), ("t-1", _ts(2), "KX-B")],
            now_offset=60 * 10,
        ),
    ]

    for setup in verdicts_setup:
        # Fresh DB for each setup to avoid trade count bleed
        db = tmp_path / f"pt_{setup['now_offset']}_{len(setup['live_records'])}.db"
        _init_db(db)
        for tid, ts_val, ticker in setup["trades"]:
            _insert_trade(db, trade_id=tid, ts=ts_val, ticker=ticker)

        live_log = tmp_path / f"live_{setup['now_offset']}_{len(setup['live_records'])}.jsonl"
        _write_jsonl(live_log, setup["live_records"])

        report = collect_report(
            db_path=db,
            trade_log_live=live_log,
            trade_log_archive_dir=tmp_path / "no_archive",
            clean_start_ts=CLEAN_START,
            min_opportunities=3,
            max_hours_without_trade=6.0,
            drift_ratio_threshold=0.05,
            now_ts=_ts(setup["now_offset"]),
        )

        # reason field (if present) must be ASCII
        if report.get("reason"):
            try:
                report["reason"].encode("ascii")
            except UnicodeEncodeError as exc:
                pytest.fail(
                    f"Non-ASCII in reason for verdict {report['verdict']!r}: {exc}\n"
                    f"reason = {report['reason']!r}"
                )

        # format_human_report output must be ASCII
        human_output = format_human_report(report)
        try:
            human_output.encode("ascii")
        except UnicodeEncodeError as exc:
            pytest.fail(
                f"Non-ASCII in format_human_report for verdict {report['verdict']!r}: {exc}\n"
                f"offending output = {human_output!r}"
            )

    # argparse --help must be ASCII
    help_text = _build_argparser().format_help()
    try:
        help_text.encode("ascii")
    except UnicodeEncodeError as exc:
        pytest.fail(f"Non-ASCII in argparse help text: {exc}\nhelp = {help_text!r}")
