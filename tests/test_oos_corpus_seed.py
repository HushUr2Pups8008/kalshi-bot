"""Tests for the OOS corpus seed extractor."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.edge_replay import oos_corpus_seed


def _make_paper_trades_db(path: Path) -> None:
    """Build a minimal paper_trades schema mirror at ``path``."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id     TEXT PRIMARY KEY,
                ts           TEXT NOT NULL,
                ticker       TEXT NOT NULL,
                side         TEXT NOT NULL,
                price_cents  INTEGER NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?, ?)",
            [
                ("t1", "2026-05-20T00:00:00Z", "KX-A", "yes", 50),
                ("t2", "2026-05-23T21:21:00Z", "KX-B", "yes", 50),
                ("t3", "2026-05-23T21:22:16Z", "KX-C", "yes", 55),
                ("t4", "2026-05-24T00:00:00Z", "KX-D", "no", 45),
                ("t5", "2026-05-25T12:00:00Z", "KX-E", "yes", 60),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_default_window_includes_only_post_cohort_start(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    out = tmp_path / "seed.jsonl"
    _make_paper_trades_db(db)

    summary = oos_corpus_seed.extract_seed(
        db_path=db,
        start_ts=oos_corpus_seed.DEFAULT_START_TS,
        end_ts=None,
        cohort_tag=oos_corpus_seed.DEFAULT_COHORT_TAG,
        output_path=out,
    )

    assert summary["row_count"] == 3
    assert summary["window_start_utc"] == "2026-05-23T21:22:16Z"
    assert summary["window_end_utc"] is None
    assert summary["cohort_tag"] == "POST_V030_2_OOS_SEED"

    rows = [json.loads(line) for line in out.read_text().splitlines()]
    trade_ids = [row["trade_id"] for row in rows]
    assert trade_ids == ["t3", "t4", "t5"], "pre-cohort rows must be excluded"


def test_each_row_carries_cohort_metadata(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    out = tmp_path / "seed.jsonl"
    _make_paper_trades_db(db)

    oos_corpus_seed.extract_seed(
        db_path=db,
        start_ts="2026-05-23T21:22:16Z",
        end_ts="2026-05-25T00:00:00Z",
        cohort_tag="POST_V030_2_OOS_SEED",
        output_path=out,
    )

    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert all(row["cohort_tag"] == "POST_V030_2_OOS_SEED" for row in rows)
    assert all(row["corpus_window_start_utc"] == "2026-05-23T21:22:16Z" for row in rows)
    assert all(row["corpus_window_end_utc"] == "2026-05-25T00:00:00Z" for row in rows)
    assert all(row["oos_seed_version"] == 1 for row in rows)


def test_end_ts_filter_is_exclusive(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    out = tmp_path / "seed.jsonl"
    _make_paper_trades_db(db)

    summary = oos_corpus_seed.extract_seed(
        db_path=db,
        start_ts="2026-05-23T21:22:16Z",
        end_ts="2026-05-24T00:00:00Z",
        cohort_tag="POST_V030_2_OOS_SEED",
        output_path=out,
    )

    assert summary["row_count"] == 1
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows[0]["trade_id"] == "t3"


def test_extractor_is_read_only(tmp_path: Path) -> None:
    """A seed extraction must not modify the source DB."""
    db = tmp_path / "paper_trades.db"
    out = tmp_path / "seed.jsonl"
    _make_paper_trades_db(db)

    pre_mtime = db.stat().st_mtime
    pre_rows = sqlite3.connect(db).execute(
        "SELECT COUNT(*) FROM paper_trades"
    ).fetchone()[0]

    oos_corpus_seed.extract_seed(
        db_path=db,
        start_ts="2026-05-23T21:22:16Z",
        end_ts=None,
        cohort_tag="POST_V030_2_OOS_SEED",
        output_path=out,
    )

    post_mtime = db.stat().st_mtime
    post_rows = sqlite3.connect(db).execute(
        "SELECT COUNT(*) FROM paper_trades"
    ).fetchone()[0]
    assert pre_mtime == post_mtime, "DB mtime changed; extractor wrote to source"
    assert pre_rows == post_rows


def test_idempotent_overwrite(tmp_path: Path) -> None:
    """Re-running with identical inputs produces identical output."""
    db = tmp_path / "paper_trades.db"
    out = tmp_path / "seed.jsonl"
    _make_paper_trades_db(db)

    oos_corpus_seed.extract_seed(
        db_path=db,
        start_ts="2026-05-23T21:22:16Z",
        end_ts=None,
        cohort_tag="POST_V030_2_OOS_SEED",
        output_path=out,
    )
    first = out.read_text()

    oos_corpus_seed.extract_seed(
        db_path=db,
        start_ts="2026-05-23T21:22:16Z",
        end_ts=None,
        cohort_tag="POST_V030_2_OOS_SEED",
        output_path=out,
    )
    second = out.read_text()

    assert first == second


def test_missing_db_returns_exit_code_2(tmp_path: Path) -> None:
    out = tmp_path / "seed.jsonl"
    exit_code = oos_corpus_seed.main(
        ["--db", str(tmp_path / "no_such.db"), "--output", str(out)]
    )
    assert exit_code == 2


def test_missing_ts_column_returns_exit_code_2(tmp_path: Path) -> None:
    db = tmp_path / "bad.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE paper_trades (id TEXT)")
        conn.commit()
    finally:
        conn.close()
    out = tmp_path / "seed.jsonl"
    exit_code = oos_corpus_seed.main(["--db", str(db), "--output", str(out)])
    assert exit_code == 2


def test_iso_z_normalization(tmp_path: Path) -> None:
    assert (
        oos_corpus_seed._normalize_iso8601_z("2026-05-23T21:22:16Z")
        == "2026-05-23T21:22:16Z"
    )
    assert (
        oos_corpus_seed._normalize_iso8601_z("2026-05-23T15:22:16-06:00")
        == "2026-05-23T21:22:16Z"
    )


def test_open_ended_window_includes_post_start_rows_with_no_upper_bound(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper_trades.db"
    out = tmp_path / "seed.jsonl"
    _make_paper_trades_db(db)

    summary = oos_corpus_seed.extract_seed(
        db_path=db,
        start_ts="2026-05-23T21:22:16Z",
        end_ts=None,
        cohort_tag="POST_V030_2_OOS_SEED",
        output_path=out,
    )

    assert summary["row_count"] == 3
    assert summary["window_end_utc"] is None
