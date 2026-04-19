import sqlite3
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = REPO_ROOT / "docs" / "evidence_store_schema.sql"
SCHEMA_MD = REPO_ROOT / "docs" / "evidence_store_schema.md"


def _connect_with_schema() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


def _insert_dossier(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO dossiers (
            market_ticker, dossier_version, current_estimate, confidence,
            prior_estimate, created_ts, updated_ts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "KXTEST-26DEC31",
            0,
            0.50,
            0.10,
            0.50,
            "2026-04-18T00:00:00+00:00",
            "2026-04-18T00:00:00+00:00",
        ),
    )


def _insert_evidence(conn: sqlite3.Connection, evidence_id: str = "ev-1") -> None:
    conn.execute(
        """
        INSERT INTO evidence (
            evidence_id, market_ticker, source, source_class, headline,
            published_ts, ingested_ts, content_hash, headline_ngram_fingerprint,
            is_duplicate, correlation_discount_applied, update_type,
            quality_score, original_weight, dossier_version_before,
            dossier_version_after
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence_id,
            "KXTEST-26DEC31",
            "Reuters",
            "wire",
            "Test headline",
            "2026-04-18T00:01:00+00:00",
            "2026-04-18T00:01:05+00:00",
            f"hash-{evidence_id}",
            "test headline",
            0,
            0,
            "state",
            0.75,
            0.80,
            0,
            1,
        ),
    )


def _insert_dossier_update(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO dossier_updates (
            market_ticker, dossier_version, created_ts, trigger_evidence_id,
            prior_estimate, new_estimate, update_delta, confidence_before,
            confidence_after, update_type, llm_called, drift_suspect, in_recovery
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "KXTEST-26DEC31",
            1,
            "2026-04-18T00:01:10+00:00",
            "ev-1",
            0.50,
            0.56,
            0.06,
            0.10,
            0.22,
            "state",
            0,
            0,
            0,
        ),
    )


def test_evidence_store_schema_creates_required_tables_and_indexes():
    conn = _connect_with_schema()

    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    indexes = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }

    assert {"dossiers", "evidence", "dossier_updates", "dossier_update_evidence"} <= tables
    assert {
        "idx_evidence_market_ingested",
        "idx_evidence_market_source_class_ingested",
        "idx_evidence_market_content_hash",
        "idx_evidence_market_version_after",
        "idx_dossier_updates_market_created",
        "idx_dossier_update_evidence_evidence",
    } <= indexes


def test_evidence_requires_parent_dossier():
    conn = _connect_with_schema()

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _insert_evidence(conn)


def test_evidence_id_is_unique_and_identity_fields_are_immutable():
    conn = _connect_with_schema()
    _insert_dossier(conn)
    _insert_evidence(conn)

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert_evidence(conn)

    with pytest.raises(sqlite3.IntegrityError, match="immutable evidence identity"):
        conn.execute(
            "UPDATE evidence SET evidence_id = ? WHERE evidence_id = ?",
            ("ev-renamed", "ev-1"),
        )

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM evidence WHERE evidence_id = ?", ("ev-1",))


def test_dossier_update_links_to_trigger_and_contributing_evidence():
    conn = _connect_with_schema()
    _insert_dossier(conn)
    _insert_evidence(conn)
    _insert_dossier_update(conn)
    conn.execute(
        """
        INSERT INTO dossier_update_evidence (
            market_ticker, dossier_version, evidence_id
        )
        VALUES (?, ?, ?)
        """,
        ("KXTEST-26DEC31", 1, "ev-1"),
    )

    rows = conn.execute(
        """
        SELECT due.evidence_id
        FROM dossier_update_evidence due
        JOIN dossier_updates du
          ON du.market_ticker = due.market_ticker
         AND du.dossier_version = due.dossier_version
        JOIN evidence e
          ON e.market_ticker = due.market_ticker
         AND e.evidence_id = due.evidence_id
        WHERE due.market_ticker = ?
        ORDER BY du.created_ts, due.evidence_id
        """,
        ("KXTEST-26DEC31",),
    ).fetchall()

    assert [row["evidence_id"] for row in rows] == ["ev-1"]


def test_contributing_evidence_cannot_cross_market_boundaries():
    conn = _connect_with_schema()
    _insert_dossier(conn)
    _insert_evidence(conn)
    _insert_dossier_update(conn)
    conn.execute(
        """
        INSERT INTO dossiers (
            market_ticker, dossier_version, confidence, created_ts, updated_ts
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "KXOTHER-26DEC31",
            0,
            0.0,
            "2026-04-18T00:00:00+00:00",
            "2026-04-18T00:00:00+00:00",
        ),
    )

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(
            """
            INSERT INTO dossier_update_evidence (
                market_ticker, dossier_version, evidence_id
            )
            VALUES (?, ?, ?)
            """,
            ("KXOTHER-26DEC31", 1, "ev-1"),
        )


def test_schema_artifacts_keep_evidence_store_separate_from_paper_trades_db():
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    md = SCHEMA_MD.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS paper_trades" not in sql
    assert "REFERENCES paper_trades" not in sql
    assert "data/evidence_store.db" in md
    assert "data/paper_trades.db" in md
