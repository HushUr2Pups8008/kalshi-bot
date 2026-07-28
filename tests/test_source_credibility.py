"""
Tests for analysis/source_credibility.py

Covers: neutral vs active multiplier behaviour and recorded outcome updates.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from config import CREDIBILITY_MIN_SAMPLE
import tasks.stats.source_credibility as source_credibility_module
from tasks.stats.source_credibility import (
    SourceCredibility,
    record_outcome_in_transaction,
)

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

    def test_get_multiplier_ignores_telemetry_value_without_canonical_delivery(
        self,
        credibility,
    ):
        credibility._conn.execute(
            """
            INSERT INTO source_credibility (source, wins, losses, total, accuracy, multiplier, last_updated)
            VALUES ('Reuters', 8, 2, 10, 0.8, 1.3, ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        credibility._conn.commit()

        assert credibility.get_multiplier("Reuters") == 1.0

    def test_get_multiplier_caches_verified_canonical_aggregate(self, credibility, monkeypatch):
        loader = MagicMock(
            return_value={
                "Reuters": (
                    float(CREDIBILITY_MIN_SAMPLE),
                    float(CREDIBILITY_MIN_SAMPLE),
                    CREDIBILITY_MIN_SAMPLE,
                )
            }
        )
        monkeypatch.setattr(
            credibility,
            "_load_canonical_outcomes",
            loader,
            raising=False,
        )

        assert credibility.get_multiplier("Reuters") == 1.5
        assert credibility.get_multiplier("Reuters") == 1.5
        loader.assert_called_once()

    def test_canonical_multiplier_time_decays_stale_wins(self, credibility, monkeypatch):
        now = datetime.now(timezone.utc)
        events = []
        for index in range(10):
            events.append(
                SimpleNamespace(
                    trade_id=f"old-win-{index}",
                    payload_json=json.dumps(
                        {
                            "trade_id": f"old-win-{index}",
                            "signal_source": "Reuters",
                            "won": True,
                            "settled_at": (now - timedelta(days=365)).isoformat(),
                        }
                    ),
                )
            )
        for index in range(5):
            events.append(
                SimpleNamespace(
                    trade_id=f"recent-loss-{index}",
                    payload_json=json.dumps(
                        {
                            "trade_id": f"recent-loss-{index}",
                            "signal_source": "Reuters",
                            "won": False,
                            "settled_at": now.isoformat(),
                        }
                    ),
                )
            )

        class DeliveredStore:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def canonical_delivery_complete_outbox_payloads(self, *, now):
                return tuple(events)

        monkeypatch.setattr(source_credibility_module, "SettlementStore", DeliveredStore)

        assert credibility.get_multiplier("Reuters") < 1.0


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


def test_transactional_outcomes_are_order_independent_and_keep_latest_timestamp(
    tmp_path,
):
    older = (False, "2026-07-14T20:00:00+00:00")
    newer = (True, "2026-07-14T22:00:00+00:00")

    def apply_events(db_path: Path, events: tuple[tuple[bool, str], ...]) -> tuple:
        tracker = SourceCredibility(db_path=db_path)
        for was_correct, settled_at in events:
            record_outcome_in_transaction(
                tracker._conn,
                source="wire:test-source",
                was_correct=was_correct,
                updated_at=settled_at,
            )
        tracker._conn.commit()
        row = tracker._conn.execute(
            "SELECT * FROM source_credibility WHERE source='wire:test-source'"
        ).fetchone()
        result = tuple(row)
        tracker._conn.close()
        return result

    chronological = apply_events(tmp_path / "chronological.db", (older, newer))
    reverse_delivery = apply_events(tmp_path / "reverse.db", (newer, older))

    assert chronological == reverse_delivery
    assert chronological[-1] == newer[1]
