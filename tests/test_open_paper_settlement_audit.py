from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from polymarket.settlement_reconciler import SettlementNotFound
from scripts import audit_open_paper_settlements as audit_module
from scripts.audit_open_paper_settlements import (
    PaperSettlementAuditSnapshotError,
    audit_database as _audit_database,
)
from trading.legacy_settlement_receipts import LegacySettlementReceipt
from trading.settlement import (
    MarketOutcome,
    SettlementDriftError,
    build_settlement_observation,
)
from trading.venue import MarketRef, Venue


NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


class FakeSettlementSource:
    def __init__(self, responses: dict[tuple[str, str], object]) -> None:
        self.responses = responses
        self.calls: list[MarketRef] = []

    async def get_settlement_exact(self, market_ref: MarketRef, *, prior_observation):
        assert prior_observation is None
        self.calls.append(market_ref)
        response = self.responses[(market_ref.venue_market_id, market_ref.alias)]
        if isinstance(response, BaseException):
            raise response
        return response


def _create_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE paper_trades (
            trade_id TEXT PRIMARY KEY,
            ticker TEXT,
            venue TEXT,
            resolved INTEGER NOT NULL DEFAULT 0,
            venue_market_id TEXT,
            identity_status TEXT,
            quarantine_reason TEXT,
            market_snapshot TEXT,
            terminal_state TEXT,
            settlement_observation_sha256 TEXT,
            settled_at TEXT,
            gross_payout_cents INTEGER,
            gross_pnl_cents INTEGER,
            resolved_ts TEXT,
            resolved_yes INTEGER,
            pnl_dollars REAL
        )
        """
    )
    conn.commit()
    conn.close()


def _insert(
    path: Path,
    trade_id: object,
    *,
    ticker: object = "exact-market",
    venue: object = "polymarket_us",
    resolved: int = 0,
    venue_market_id: object = "42",
    identity_status: object = "mapped",
    quarantine_reason: object = None,
    close_time: str | None = "2027-01-01T00:00:00Z",
    terminal_state: object = None,
    settlement_observation_sha256: object = None,
    settled_at: object = None,
    gross_payout_cents: object = None,
    gross_pnl_cents: object = None,
    resolved_ts: object = None,
    resolved_yes: object = None,
    pnl_dollars: object = None,
) -> None:
    snapshot = None if close_time is None else json.dumps({"close_time": close_time})
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO paper_trades (
            trade_id, ticker, venue, resolved, venue_market_id, identity_status,
            quarantine_reason, market_snapshot, terminal_state,
            settlement_observation_sha256, settled_at, gross_payout_cents,
            gross_pnl_cents, resolved_ts, resolved_yes, pnl_dollars
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_id,
            ticker,
            venue,
            resolved,
            venue_market_id,
            identity_status,
            quarantine_reason,
            snapshot,
            terminal_state,
            settlement_observation_sha256,
            settled_at,
            gross_payout_cents,
            gross_pnl_cents,
            resolved_ts,
            resolved_yes,
            pnl_dollars,
        ),
    )
    conn.commit()
    conn.close()


def _artifact_hashes(path: Path) -> dict[str, str]:
    result = {}
    for candidate in (
        path,
        path.with_name(path.name + "-journal"),
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
    ):
        if candidate.exists():
            result[candidate.name] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return result


async def _audit_snapshot(
    db: Path,
    source: FakeSettlementSource,
    *,
    now: datetime = NOW,
):
    return await _audit_database(
        db,
        source,
        now=now,
        snapshot_sha256=_artifact_hashes(db)[db.name],
    )


@pytest.mark.asyncio
async def test_audit_rejects_an_unattested_input_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)

    with pytest.raises(PaperSettlementAuditSnapshotError, match="requires a snapshot SHA-256"):
        await _audit_database(
            db,
            FakeSettlementSource({}),
            now=NOW,
            snapshot_sha256=None,
        )


@pytest.mark.asyncio
async def test_audit_rejects_a_snapshot_hash_mismatch_without_fetching(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    before = _artifact_hashes(db)
    source = FakeSettlementSource({})

    with pytest.raises(PaperSettlementAuditSnapshotError, match="does not match"):
        await _audit_database(db, source, now=NOW, snapshot_sha256="0" * 64)

    assert source.calls == []
    assert _artifact_hashes(db) == before


@pytest.mark.asyncio
async def test_audit_rejects_a_malformed_attested_snapshot_without_fetching(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    db.write_bytes(b"not a SQLite database")
    before = _artifact_hashes(db)
    source = FakeSettlementSource({})

    with pytest.raises(PaperSettlementAuditSnapshotError, match="failed SQLite integrity check"):
        await _audit_database(
            db,
            source,
            now=NOW,
            snapshot_sha256=before[db.name],
        )

    assert source.calls == []
    assert _artifact_hashes(db) == before


def _terminal_observation(market_id: str = "42", alias: str = "exact-market"):
    market_ref = MarketRef(Venue.POLYMARKET_US, market_id, alias)
    return build_settlement_observation(
        market_ref=market_ref,
        outcome=MarketOutcome.YES,
        authoritative_outcome={"outcome": "yes"},
        authoritative_payload={"id": market_id, "slug": alias, "settlement": 1},
        observed_at=NOW,
        effective_at=NOW - timedelta(minutes=1),
        rules_version="test-rules-v1",
        source_id="test-authoritative-source",
    )


def _db_state(path: Path) -> list[tuple[str, int, str | None]]:
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT trade_id, resolved, terminal_state FROM paper_trades ORDER BY trade_id").fetchall()
    conn.close()
    return rows


@pytest.mark.asyncio
async def test_audit_groups_duplicate_lots_emits_receipt_provenance_and_never_writes(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "trade-1")
    _insert(db, "trade-2")
    before = _artifact_hashes(db)
    observation = _terminal_observation()
    source = FakeSettlementSource({("42", "exact-market"): observation})

    report = await _audit_snapshot(db, source, now=NOW)

    assert _artifact_hashes(db) == before
    assert _db_state(db) == [("trade-1", 0, None), ("trade-2", 0, None)]
    assert source.calls == [MarketRef(Venue.POLYMARKET_US, "42", "exact-market")]
    assert report.read_only is True
    assert report.resolution_applied is False
    assert report.fetched_markets == 1
    assert [row.status for row in report.rows] == [
        "authoritative_terminal",
        "authoritative_terminal",
    ]
    for row in report.rows:
        assert row.outcome == "yes"
        assert row.source_id == observation.source_id
        assert row.rules_version == observation.rules_version
        assert row.observed_at == observation.observed_at.isoformat()
        assert row.effective_at == observation.effective_at.isoformat()
        assert row.payload_sha256 == observation.payload_sha256
        assert row.observation_sha256 == observation.observation_sha256
        assert row.receipt_bundle is not None
        assert row.receipt_bundle["schema_version"] == 1
        assert row.receipt_bundle["trade_id"] == row.trade_id
        assert row.receipt_bundle["venue"] == "polymarket_us"
        assert row.receipt_bundle["venue_market_id"] == "42"
        assert row.receipt_bundle["alias"] == "exact-market"
        assert row.receipt_bundle["canonical_payload_json"] == (
            observation.canonical_payload_json
        )
        assert row.receipt_bundle["observation_sha256"] == (
            observation.observation_sha256
        )
        receipt = LegacySettlementReceipt.from_dict(row.receipt_bundle)
        assert receipt.trade_id == row.trade_id
        assert receipt.observation == observation
    payload = json.loads(report.to_json())
    assert payload["read_only"] is True
    assert payload["resolution_applied"] is False


@pytest.mark.asyncio
async def test_audit_binds_report_to_stable_source_artifacts_and_open_rows(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "trade-1")
    source = FakeSettlementSource({("42", "exact-market"): None})

    first = await _audit_snapshot(db, source, now=NOW)
    second = await _audit_snapshot(db, source, now=NOW)
    later = await _audit_snapshot(db, source, now=NOW + timedelta(hours=1))

    first_payload = first.to_dict()
    second_payload = second.to_dict()
    assert len(first_payload["snapshot_artifacts"]) == 1
    assert first_payload["snapshot_artifacts"][0]["name"] == db.name
    assert first_payload["snapshot_artifacts"][0]["size"] > 0
    assert len(first_payload["snapshot_artifacts"][0]["sha256"]) == 64
    assert len(first_payload["open_rows_sha256"]) == 64
    assert len(first_payload["report_sha256"]) == 64
    assert first_payload["open_rows_sha256"] == second_payload["open_rows_sha256"]
    assert first_payload["report_sha256"] == second_payload["report_sha256"]
    assert first_payload["report_sha256"] == later.to_dict()["report_sha256"]

    _insert(db, "trade-2", ticker="second-market", venue_market_id="43")
    changed = await _audit_snapshot(
        db,
        FakeSettlementSource({("42", "exact-market"): None, ("43", "second-market"): None}),
        now=NOW,
    )
    changed_payload = changed.to_dict()
    assert changed_payload["open_rows_sha256"] != first_payload["open_rows_sha256"]
    assert changed_payload["report_sha256"] != first_payload["report_sha256"]


@pytest.mark.asyncio
async def test_audit_never_treats_expired_snapshot_close_as_a_terminal_receipt(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "expired", close_time="2026-06-30T23:00:00Z")
    source = FakeSettlementSource({("42", "exact-market"): None})

    report = await _audit_snapshot(db, source, now=NOW)

    row = report.rows[0]
    assert row.status == "expired_snapshot_pending_receipt"
    assert row.snapshot_close_time == "2026-06-30T23:00:00Z"
    assert row.outcome is None
    assert row.source_id is None
    assert row.payload_sha256 is None
    assert row.receipt_bundle is None
    assert _db_state(db) == [("expired", 0, None)]


@pytest.mark.asyncio
async def test_audit_rejects_snapshot_drift_during_immutable_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "trade")
    original_load_open_rows = audit_module._load_open_rows

    def mutate_after_read(conn: sqlite3.Connection):
        rows = original_load_open_rows(conn)
        with sqlite3.connect(db) as writer:
            writer.execute("UPDATE paper_trades SET ticker='changed' WHERE trade_id='trade'")
        return rows

    monkeypatch.setattr(audit_module, "_load_open_rows", mutate_after_read)

    with pytest.raises(
        PaperSettlementAuditSnapshotError,
        match="caller-attested snapshot changed while the audit read it",
    ):
        await _audit_snapshot(db, FakeSettlementSource({("42", "exact-market"): None}), now=NOW)


@pytest.mark.asyncio
async def test_audit_rejects_wal_transition_without_mutating_source_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "trade")
    original_load_open_rows = audit_module._load_open_rows
    writers: list[sqlite3.Connection] = []
    transition_artifacts: dict[str, str] = {}

    def create_wal_during_read(conn: sqlite3.Connection):
        rows = original_load_open_rows(conn)
        writer = sqlite3.connect(db)
        writers.append(writer)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE paper_trades SET ticker='changed' WHERE trade_id='trade'")
        writer.commit()
        transition_artifacts.update(_artifact_hashes(db))
        return rows

    monkeypatch.setattr(audit_module, "_load_open_rows", create_wal_during_read)
    try:
        with pytest.raises(PaperSettlementAuditSnapshotError, match="active WAL"):
            await _audit_snapshot(db, FakeSettlementSource({("42", "exact-market"): None}), now=NOW)
        assert _artifact_hashes(db) == transition_artifacts
    finally:
        for writer in writers:
            writer.close()


@pytest.mark.asyncio
async def test_audit_marks_nontext_market_snapshot_invalid_without_fetching(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "blob-snapshot")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE paper_trades SET market_snapshot=? WHERE trade_id='blob-snapshot'",
            (sqlite3.Binary(b"not-json"),),
        )
    source = FakeSettlementSource({("42", "exact-market"): None})

    report = await _audit_snapshot(db, source, now=NOW)

    assert source.calls == []
    assert report.rows[0].status == "invalid_snapshot"
    assert report.rows[0].outcome is None
    assert len(report.to_dict()["open_rows_sha256"]) == 64


@pytest.mark.asyncio
async def test_audit_skips_invalid_unmapped_quarantined_and_other_venue_rows(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "invalid", venue_market_id="not-numeric")
    _insert(db, "unmapped", venue_market_id=None, identity_status=None)
    _insert(
        db,
        "quarantined",
        identity_status="quarantined",
        quarantine_reason="identity_mismatch",
    )
    _insert(db, "other-venue", venue="kalshi")
    source = FakeSettlementSource({})

    report = await _audit_snapshot(db, source, now=NOW)

    assert source.calls == []
    assert {row.trade_id: row.status for row in report.rows} == {
        "invalid": "invalid_identity",
        "unmapped": "unmapped_identity",
        "quarantined": "quarantined",
        "other-venue": "unsupported_venue",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "status"),
    [
        (SettlementNotFound("missing"), "not_found"),
        (SettlementDriftError("identity mismatch"), "settlement_drift"),
        (TimeoutError("source slow"), "transport_error"),
    ],
)
async def test_audit_fails_closed_on_nonterminal_or_untrusted_source_results(
    tmp_path: Path,
    response: BaseException,
    status: str,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "trade")
    source = FakeSettlementSource({("42", "exact-market"): response})

    report = await _audit_snapshot(db, source, now=NOW)

    row = report.rows[0]
    assert row.status == status
    assert row.outcome is None
    assert row.observation_sha256 is None
    assert _db_state(db) == [("trade", 0, None)]


@pytest.mark.asyncio
async def test_audit_rejects_a_terminal_observation_for_a_different_market(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "trade")
    source = FakeSettlementSource({("42", "exact-market"): _terminal_observation("43")})

    report = await _audit_snapshot(db, source, now=NOW)

    row = report.rows[0]
    assert row.status == "settlement_drift"
    assert row.outcome is None
    assert row.observation_sha256 is None
    assert _db_state(db) == [("trade", 0, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trade_id", "kwargs"),
    [
        ("null-ticker", {"ticker": None}),
        ("blob-ticker", {"ticker": sqlite3.Binary(b"exact-market")}),
        ("padded-ticker", {"ticker": " exact-market "}),
        ("null-venue", {"venue": None}),
        ("padded-venue", {"venue": " polymarket_us "}),
        ("null-market-id", {"venue_market_id": None}),
        ("blob-market-id", {"venue_market_id": sqlite3.Binary(b"42")}),
        ("padded-market-id", {"venue_market_id": " 42 "}),
        ("blob-identity-status", {"identity_status": sqlite3.Binary(b"mapped")}),
        ("padded-identity-status", {"identity_status": " mapped "}),
    ],
)
async def test_audit_rejects_malformed_identity_values_without_fetching(
    tmp_path: Path,
    trade_id: str,
    kwargs: dict[str, object],
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, trade_id, **kwargs)
    source = FakeSettlementSource({})

    report = await _audit_snapshot(db, source, now=NOW)

    assert source.calls == []
    assert report.rows[0].status == "invalid_identity"


@pytest.mark.asyncio
async def test_audit_reports_each_duplicate_null_trade_id_without_fetching(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, None)
    _insert(db, None)
    source = FakeSettlementSource({})

    report = await _audit_snapshot(db, source, now=NOW)

    assert source.calls == []
    assert len(report.rows) == 2
    assert [row.trade_id for row in report.rows] == [None, None]
    assert [row.status for row in report.rows] == ["invalid_identity", "invalid_identity"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected_fields"),
    [
        ({"terminal_state": "settled"}, ("terminal_state",)),
        (
            {"settlement_observation_sha256": "a" * 64},
            ("settlement_observation_sha256",),
        ),
    ],
)
async def test_audit_rejects_inconsistent_unresolved_terminal_state_without_fetching(
    tmp_path: Path,
    kwargs: dict[str, object],
    expected_fields: tuple[str, ...],
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "corrupt-terminal", **kwargs)
    source = FakeSettlementSource({})

    report = await _audit_snapshot(db, source, now=NOW)

    assert source.calls == []
    assert report.rows[0].status == "inconsistent_persisted_state"
    assert report.rows[0].persisted_terminal_fields == expected_fields


@pytest.mark.asyncio
async def test_audit_rejects_an_active_wal_without_touching_source_sidecars(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    writer = sqlite3.connect(db)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute(
        """
        INSERT INTO paper_trades (
            trade_id, ticker, venue, resolved, venue_market_id, identity_status,
            market_snapshot
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "wal-trade",
            "exact-market",
            "polymarket_us",
            0,
            "42",
            "mapped",
            json.dumps({"close_time": "2027-01-01T00:00:00Z"}),
        ),
    )
    writer.commit()
    before = _artifact_hashes(db)
    assert {db.name, f"{db.name}-wal", f"{db.name}-shm"} <= set(before)
    try:
        with pytest.raises(PaperSettlementAuditSnapshotError, match="active WAL"):
            await _audit_snapshot(
                db,
                FakeSettlementSource({("42", "exact-market"): None}),
                now=NOW,
            )
        after = _artifact_hashes(db)
        assert after == before
    finally:
        writer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "error_message"),
    [
        ("-journal", "active rollback journal"),
        ("-shm", "shared-memory sidecar"),
    ],
)
async def test_audit_rejects_nonquiescent_input_sidecars_without_writing(
    tmp_path: Path,
    suffix: str,
    error_message: str,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    db.with_name(db.name + suffix).write_bytes(b"not a quiescent SQLite snapshot")
    before = _artifact_hashes(db)

    with pytest.raises(PaperSettlementAuditSnapshotError, match=error_message):
        await _audit_snapshot(db, FakeSettlementSource({}), now=NOW)

    assert _artifact_hashes(db) == before


def test_parse_args_requires_an_explicit_snapshot_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["audit_open_paper_settlements.py", "--snapshot-sha256", "0" * 64])

    with pytest.raises(SystemExit) as exc_info:
        audit_module._parse_args()

    assert exc_info.value.code == 2
