import sqlite3
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
