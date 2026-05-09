"""
Tests for analysis/source_credibility.py

Covers: neutral vs active multiplier behaviour and recorded outcome updates.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tasks.stats.source_credibility import SourceCredibility

_REAL_SQLITE_CONNECT = sqlite3.connect


def _shared_memory_connect(name: str):
    db_uri = f"file:{name}-{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper = _REAL_SQLITE_CONNECT(db_uri, uri=True, check_same_thread=False)

    def _connect(*args, **kwargs):
        conn = _REAL_SQLITE_CONNECT(db_uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    return keeper, _connect


@pytest.fixture
def credibility():
    keeper, connect = _shared_memory_connect("source-credibility")
    with patch("tasks.stats.source_credibility.sqlite3.connect", side_effect=connect):
        tracker = SourceCredibility(db_path=Path(":memory:"))
        tracker._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trades (
                signal_source TEXT,
                side TEXT,
                resolved_yes INTEGER,
                resolved INTEGER,
                ts TEXT
            )
            """
        )
        tracker._conn.commit()
        yield tracker
    keeper.close()


def _insert_resolved_trade(conn, source: str, side: str, resolved_yes: bool, ts: str):
    conn.execute(
        """
        INSERT INTO paper_trades (signal_source, side, resolved_yes, resolved, ts)
        VALUES (?, ?, ?, 1, ?)
        """,
        (source, side, int(resolved_yes), ts),
    )
    conn.commit()


class TestSourceCredibilityReads:
    def test_get_multiplier_is_neutral_for_unknown_source(self, credibility):
        assert credibility.get_multiplier("Reuters") == 1.0

    def test_get_multiplier_is_neutral_below_min_sample(self, credibility):
        credibility._conn.execute(
            """
            INSERT INTO source_credibility (source, wins, losses, total, accuracy, multiplier, last_updated)
            VALUES ('Reuters', 3, 1, 4, 0.75, 1.25, ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        credibility._conn.commit()

        assert credibility.get_multiplier("Reuters") == 1.0

    def test_get_multiplier_returns_active_value_at_min_sample(self, credibility):
        credibility._conn.execute(
            """
            INSERT INTO source_credibility (source, wins, losses, total, accuracy, multiplier, last_updated)
            VALUES ('Reuters', 8, 2, 10, 0.8, 1.3, ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        credibility._conn.commit()

        assert credibility.get_multiplier("Reuters") == pytest.approx(1.3)


class TestSourceCredibilityWrites:
    def test_record_outcome_updates_raw_counts_and_multiplier(self, credibility):
        source = "Reuters"
        now = datetime.now(timezone.utc).isoformat()
        _insert_resolved_trade(credibility._conn, source, "yes", True, now)

        credibility.record_outcome(source, was_correct=True)
        stats = credibility.get_stats(source)

        assert stats is not None
        assert stats["wins"] == 1
        assert stats["losses"] == 0
        assert stats["total"] == 1
        assert 0.5 <= stats["multiplier"] <= 1.5

    def test_record_outcome_tracks_losses(self, credibility):
        source = "AP"
        now = datetime.now(timezone.utc).isoformat()
        _insert_resolved_trade(credibility._conn, source, "yes", False, now)

        credibility.record_outcome(source, was_correct=False)
        stats = credibility.get_stats(source)

        assert stats is not None
        assert stats["wins"] == 0
        assert stats["losses"] == 1
        assert stats["total"] == 1

    def test_time_decayed_accuracy_handles_invalid_timestamps(self, credibility):
        source = "BBC"
        credibility._conn.execute(
            """
            INSERT INTO paper_trades (signal_source, side, resolved_yes, resolved, ts)
            VALUES (?, 'yes', 1, 1, 'not-a-timestamp')
            """,
            (source,),
        )
        credibility._conn.commit()

        accuracy, n = credibility._time_decayed_accuracy(source)
        assert n == 1
        assert 0.0 <= accuracy <= 1.0
