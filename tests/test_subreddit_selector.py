"""
Tests for feeds/subreddit_selector.py — focused on the DB-write helpers
that were previously silently swallowing exceptions (PROFIT-OBS-006).
"""
from __future__ import annotations

import sqlite3
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from feeds.subreddit_selector import (
    _mark_candidate_suppressed,
    _sync_suppressed_candidates,
    _update_probe_ts,
)


# ---------------------------------------------------------------------------
# Shared schema / fixture helpers
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS subreddit_candidates (
    sub TEXT PRIMARY KEY,
    status TEXT DEFAULT 'candidate',
    probe_count INTEGER DEFAULT 0,
    last_probed TIMESTAMP
);
"""


def _make_db(path: Path, sub: str = "test_sub") -> None:
    """Create a subreddit_candidates table at *path* and insert one row."""
    conn = sqlite3.connect(path)
    conn.execute(_CREATE_TABLE)
    conn.execute(
        "INSERT INTO subreddit_candidates (sub, status, probe_count) VALUES (?, 'candidate', 0)",
        (sub,),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Happy-path tests (protect against regressions in the success path)
# ---------------------------------------------------------------------------


def test_update_probe_ts_increments_probe_count(tmp_path):
    db = tmp_path / "paper_trades.db"
    _make_db(db, "worldnews")

    _update_probe_ts(db, "worldnews")

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT probe_count, last_probed FROM subreddit_candidates WHERE sub = ?",
        ("worldnews",),
    ).fetchone()
    conn.close()
    assert row[0] == 1, "probe_count should be 1 after first call"
    assert row[1] is not None, "last_probed should be set"


def test_mark_candidate_suppressed_sets_status(tmp_path):
    db = tmp_path / "paper_trades.db"
    _make_db(db, "politics")

    _mark_candidate_suppressed(db, "politics")

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT status FROM subreddit_candidates WHERE sub = ?",
        ("politics",),
    ).fetchone()
    conn.close()
    assert row[0] == "suppressed"


def test_sync_suppressed_candidates_updates_backlog_status(tmp_path):
    db = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db)
    conn.execute(_CREATE_TABLE)
    conn.executemany(
        "INSERT INTO subreddit_candidates (sub, status, probe_count) VALUES (?, 'candidate', ?)",
        [("dead_sub", 3), ("live_sub", 1)],
    )
    conn.commit()
    conn.close()

    class FakeSourceStats:
        def is_suppressed(self, source: str) -> bool:
            return source == "r/dead_sub"

    updated = _sync_suppressed_candidates(db, FakeSourceStats())

    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT sub, status FROM subreddit_candidates").fetchall())
    conn.close()
    assert updated == 1
    assert rows == {"dead_sub": "suppressed", "live_sub": "candidate"}


def test_sync_suppressed_candidates_suppresses_max_probe_no_signal(tmp_path, monkeypatch):
    db = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db)
    conn.execute(_CREATE_TABLE)
    conn.execute(
        """
        CREATE TABLE source_stats (
            source TEXT PRIMARY KEY,
            posts_seen INTEGER DEFAULT 0,
            signals INTEGER DEFAULT 0,
            opportunities INTEGER DEFAULT 0,
            trades INTEGER DEFAULT 0
        )
        """
    )
    conn.executemany(
        "INSERT INTO subreddit_candidates (sub, status, probe_count) VALUES (?, 'candidate', ?)",
        [("stale_sub", 3), ("signal_sub", 3), ("fresh_sub", 2)],
    )
    conn.execute(
        """
        INSERT INTO source_stats
            (source, posts_seen, signals, opportunities, trades)
        VALUES ('r/signal_sub', 10, 1, 0, 0)
        """
    )
    conn.commit()
    conn.close()

    class FakeSourceStats:
        def is_suppressed(self, source: str) -> bool:
            return False

    monkeypatch.setenv("SUBREDDIT_CANDIDATE_MAX_PROBES", "3")

    updated = _sync_suppressed_candidates(db, FakeSourceStats())

    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT sub, status FROM subreddit_candidates").fetchall())
    conn.close()
    assert updated == 1
    assert rows == {
        "stale_sub": "suppressed",
        "signal_sub": "candidate",
        "fresh_sub": "candidate",
    }


# ---------------------------------------------------------------------------
# Failure-path tests: log + re-raise (PROFIT-OBS-006)
# ---------------------------------------------------------------------------


def test_update_probe_ts_logs_and_raises_on_db_failure(caplog):
    """sqlite3.connect failure must log ERROR with table/sub context and re-raise."""
    with patch(
        "feeds.subreddit_selector.sqlite3.connect",
        side_effect=sqlite3.OperationalError("disk full"),
    ):
        with caplog.at_level(logging.ERROR, logger="subreddit_selector"):
            with pytest.raises(sqlite3.OperationalError):
                _update_probe_ts(Path("/does/not/matter"), "flaky_sub")

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "Expected at least one ERROR-level log record"
    msg = errors[0].getMessage()
    assert "subreddit_candidates" in msg, "Log must name the table"
    assert "flaky_sub" in msg, "Log must include the sub value"


def test_mark_candidate_suppressed_logs_and_raises_on_db_failure(caplog):
    """sqlite3.connect failure must log ERROR with table/sub context and re-raise."""
    with patch(
        "feeds.subreddit_selector.sqlite3.connect",
        side_effect=sqlite3.OperationalError("disk full"),
    ):
        with caplog.at_level(logging.ERROR, logger="subreddit_selector"):
            with pytest.raises(sqlite3.OperationalError):
                _mark_candidate_suppressed(Path("/does/not/matter"), "bad_sub")

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "Expected at least one ERROR-level log record"
    msg = errors[0].getMessage()
    assert "subreddit_candidates" in msg, "Log must name the table"
    assert "bad_sub" in msg, "Log must include the sub value"
