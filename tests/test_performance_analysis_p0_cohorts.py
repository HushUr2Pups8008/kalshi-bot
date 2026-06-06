import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import scripts.performance_analysis as pa


SENTINEL = "2026-05-01T00:00:00+00:00"


def _create_db(path: Path, *, sentinel: str | None = SENTINEL) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT)")
        if sentinel is not None:
            conn.execute(
                "INSERT INTO bot_state (key, value) VALUES (?, ?)",
                ("p0_price_fix_deployed_ts", sentinel),
            )
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY,
                ts TEXT,
                series_ticker TEXT,
                resolved INTEGER,
                pnl_dollars REAL,
                match_score REAL,
                contracts INTEGER,
                kelly_contracts INTEGER,
                price_cents REAL,
                cost_dollars REAL,
                resolved_yes INTEGER,
                side TEXT
            )
            """
        )
        rows = [
            ("pre-win", "2026-04-30T10:00:00+00:00", 1.0, 1),
            ("pre-win-2", "2026-04-30T11:00:00+00:00", 1.0, 1),
            ("post-loss", "2026-05-01T10:00:00+00:00", -1.0, 0),
            ("post-loss-2", "2026-05-01T11:00:00+00:00", -1.0, 0),
        ]
        conn.executemany(
            """
            INSERT INTO paper_trades (
                trade_id, ts, series_ticker, resolved, pnl_dollars, match_score,
                contracts, kelly_contracts, price_cents, cost_dollars,
                resolved_yes, side
            )
            VALUES (?, ?, 'KXGOV', 1, ?, 0.15, 5, 2, 50, 2.5, ?, 'yes')
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_lifetime_sections_split_pre_and_post_p0_cohorts(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    _create_db(db_path)
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    series = pa.section_per_series_win_rate()
    match_score = pa.section_match_score_calibration()
    kelly = pa.section_kelly_shadow()

    assert "P0 cohort boundary: 2026-05-01T00:00:00+00:00" in series
    assert "Pre-P0 (frozen) cohort:" in series
    assert "Post-P0 cohort:" in series
    assert "KXGOV" in series
    assert "100.0%" in series
    assert "$+2.00" in series
    assert "0.0%" in series
    assert "$-2.00" in series
    assert "KXGOV      4" not in series

    assert "Pre-P0 (frozen) cohort:" in match_score
    assert "Post-P0 cohort:" in match_score
    assert "0.10-0.20" in match_score
    assert "100.0%" in match_score
    assert "$+2.00" in match_score
    assert "0.0%" in match_score
    assert "$-2.00" in match_score
    assert "0.10-0.20        4" not in match_score

    assert "Pre-P0 (frozen) cohort:" in kelly
    assert "Post-P0 cohort:" in kelly
    assert "Flat-5 (actual) vs Kelly shadow sizing (2 resolved trades):" in kelly
    assert "Flat-5 (actual) vs Kelly shadow sizing (4 resolved trades):" not in kelly


def test_lifetime_sections_fail_soft_when_p0_boundary_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    _create_db(db_path, sentinel=None)
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    outputs = [
        pa.section_per_series_win_rate(),
        pa.section_match_score_calibration(),
        pa.section_kelly_shadow(),
    ]

    for output in outputs:
        assert "P0 cohort boundary missing" in output
        assert "not reporting blended lifetime aggregates" in output
        assert "KXGOV" not in output
        assert "4 resolved trades" not in output


def test_load_db_state_tolerates_missing_bot_state_table(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE paper_trades (trade_id TEXT PRIMARY KEY, ts TEXT)")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    assert pa.load_db_state() == {}


def test_lifetime_sections_preserve_schema_migration_messages(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE paper_trades (trade_id TEXT PRIMARY KEY, ts TEXT)")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    assert "series_ticker column not found" in pa.section_per_series_win_rate()
    assert "match_score column not found" in pa.section_match_score_calibration()
    assert "kelly_contracts column not found" in pa.section_kelly_shadow()


def test_lifetime_sections_fail_soft_when_p0_boundary_malformed(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    _create_db(db_path, sentinel="not-a-timestamp")
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    output = pa.section_per_series_win_rate()

    assert "P0 cohort boundary missing" in output
    assert "not reporting blended lifetime aggregates" in output
    assert "KXGOV" not in output


def test_p0_boundary_normalizes_offset_to_utc_before_cohort_split(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    _create_db(db_path, sentinel="2026-05-01T00:00:00-06:00")
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO paper_trades (
                trade_id, ts, series_ticker, resolved, pnl_dollars, match_score,
                contracts, kelly_contracts, price_cents, cost_dollars,
                resolved_yes, side
            )
            VALUES (?, ?, 'KXOFFSET', 1, 1.0, 0.15, 5, 2, 50, 2.5, 1, 'yes')
            """,
            [
                ("offset-pre-1", "2026-05-01T05:30:00+00:00"),
                ("offset-pre-2", "2026-05-01T05:45:00+00:00"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    output = pa.section_per_series_win_rate()
    pre_section = output.split("Pre-P0 (frozen) cohort:", 1)[1].split("Post-P0 cohort:", 1)[0]
    post_section = output.split("Post-P0 cohort:", 1)[1]

    assert "P0 cohort boundary: 2026-05-01T06:00:00+00:00" in output
    assert "KXOFFSET" in pre_section
    assert "KXOFFSET" not in post_section
    assert "Post-P0 cohort:" in output


def test_p0_boundary_normalizes_z_suffix_to_stored_utc_format(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    _create_db(db_path, sentinel="2026-05-01T00:00:00Z")
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    output = pa.section_per_series_win_rate()

    assert "P0 cohort boundary: 2026-05-01T00:00:00+00:00" in output
    assert "$+2.00" in output
    assert "$-2.00" in output


def test_load_db_trades_filters_to_requested_window(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE paper_trades (trade_id TEXT PRIMARY KEY, ts TEXT, resolved INTEGER)"
        )
        conn.executemany(
            "INSERT INTO paper_trades (trade_id, ts, resolved) VALUES (?, ?, 1)",
            [
                ("old", "2026-05-06T23:59:59+00:00"),
                ("in-window", "2026-05-07T00:00:00+00:00"),
                ("late", "2026-06-07T00:00:00+00:00"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    rows = pa.load_db_trades(
        datetime(2026, 5, 7, tzinfo=timezone.utc),
        datetime(2026, 6, 6, 23, 59, 59, tzinfo=timezone.utc),
    )

    assert [row["trade_id"] for row in rows] == ["in-window"]
    assert len(pa.load_db_trades()) == 3


def test_golive_readiness_uses_persisted_trade_start_bankroll():
    db_trades = []
    for i in range(20):
        db_trades.append(
            {
                "trade_id": f"t-{i}",
                "resolved": 1,
                "pnl_dollars": 1.0 if i < 11 else -1.0,
                "notional_bankroll_before": 50.0 if i == 0 else None,
            }
        )

    output = pa.section_golive_readiness(
        db_trades,
        {"notional_bankroll": "40.25"},
    )

    assert "Drawdown        : 19.5% / 20% max  [PASS]" in output
    assert "Notional bankroll : $40.25 (started $50.00)" in output
    assert "drawdown" not in output.split("OVERALL:", 1)[1]


def test_skip_breakdown_surfaces_raw_unclassified_reasons():
    entries = [
        {"type": "SKIPPED", "ticker": "KX1", "reason": "market closed: close_time_elapsed"},
        {"type": "SKIPPED", "ticker": "KX1", "reason": "market closed: close_time_elapsed"},
        {"type": "SKIPPED", "ticker": "KX2", "reason": "missing orderbook best ask"},
        {"type": "SKIPPED", "ticker": "KX3", "reason": "cooldown active for ticker"},
    ]

    output = pa.section_skip_breakdown(entries)

    assert "Unclassified raw skip reasons:" in output
    assert "2  market closed: close_time_elapsed" in output
    assert "1  missing orderbook best ask" in output


def test_skip_breakdown_classifies_admission_gate_reasons_as_non_controllable():
    entries = [
        {"type": "SKIPPED", "ticker": "KX1", "reason": "G1_blended_confidence"},
        {"type": "SKIPPED", "ticker": "KX2", "reason": "G6_recency_score"},
        {"type": "SKIPPED", "ticker": "KX3", "reason": "series_correlation_in_window"},
        {"type": "SKIPPED", "ticker": "KX4", "reason": "cooldown active for ticker"},
    ]

    output = pa.section_skip_breakdown(entries)

    assert "confidence gate" in output
    assert "recency gate" in output
    assert "series correlation gate" in output
    assert "KX1" not in output.split("Most-skipped tickers (controllable skips only):", 1)[1]
    assert "KX4" in output.split("Most-skipped tickers (controllable skips only):", 1)[1]


def test_candidate_subreddits_summarizes_and_caps_large_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE subreddit_candidates (
                sub TEXT PRIMARY KEY,
                discovered_ts TEXT NOT NULL,
                discovered_via TEXT NOT NULL,
                probe_count INTEGER DEFAULT 0,
                last_probed TEXT,
                status TEXT DEFAULT 'candidate'
            )
            """
        )
        rows = [
            (
                f"sub{i:03d}",
                "2026-05-01T00:00:00+00:00",
                "query",
                i % 4,
                "2026-05-02T00:00:00+00:00",
                "candidate" if i < 8 else "suppressed",
            )
            for i in range(12)
        ]
        conn.executemany(
            """
            INSERT INTO subreddit_candidates
                (sub, discovered_ts, discovered_via, probe_count, last_probed, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pa, "DB_PATH", db_path)
    monkeypatch.setenv("SUBREDDIT_CANDIDATE_MAX_PROBES", "3")

    output = pa.section_candidate_subreddits(max_rows=5)

    assert "candidate: 8, suppressed: 4" in output
    assert "Max-probe no-signal eligible: 2" in output
    assert "Showing 5 of 12 rows" in output
    assert "r/sub003" in output
    assert "r/sub011" not in output
