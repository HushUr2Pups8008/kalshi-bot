import sqlite3
from datetime import UTC, datetime, timedelta

from scripts.edge_replay.ingestion_freshness_check import check_freshness


def test_check_freshness_passes_when_evidence_is_recent(tmp_path):
    db_path = tmp_path / "evidence_store.db"
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE evidence (ingested_ts TEXT)")
        conn.execute("INSERT INTO evidence VALUES (?)", ((now - timedelta(hours=2)).isoformat(),))
        conn.commit()
    finally:
        conn.close()

    result = check_freshness(db_path, max_age_hours=6, now=now)

    assert result["ok"] is True
    assert result["age_hours"] == 2.0


def test_check_freshness_fails_when_evidence_is_stale(tmp_path):
    db_path = tmp_path / "evidence_store.db"
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE evidence (ingested_ts TEXT)")
        conn.execute("INSERT INTO evidence VALUES (?)", ((now - timedelta(hours=12)).isoformat(),))
        conn.commit()
    finally:
        conn.close()

    result = check_freshness(db_path, max_age_hours=6, now=now)

    assert result["ok"] is False
    assert result["age_hours"] == 12.0
