from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from polymarket.settlement_reconciler import SettlementNotFound
from scripts.audit_open_paper_settlements import audit_database
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
    for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if candidate.exists():
            result[candidate.name] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return result


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

    report = await audit_database(db, source, now=NOW)

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
    payload = json.loads(report.to_json())
    assert payload["read_only"] is True
    assert payload["resolution_applied"] is False


@pytest.mark.asyncio
async def test_audit_never_treats_expired_snapshot_close_as_a_terminal_receipt(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "expired", close_time="2026-06-30T23:00:00Z")
    source = FakeSettlementSource({("42", "exact-market"): None})

    report = await audit_database(db, source, now=NOW)

    row = report.rows[0]
    assert row.status == "expired_snapshot_pending_receipt"
    assert row.snapshot_close_time == "2026-06-30T23:00:00Z"
    assert row.outcome is None
    assert row.source_id is None
    assert row.payload_sha256 is None
    assert _db_state(db) == [("expired", 0, None)]


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

    report = await audit_database(db, source, now=NOW)

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

    report = await audit_database(db, source, now=NOW)

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

    report = await audit_database(db, source, now=NOW)

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

    report = await audit_database(db, source, now=NOW)

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

    report = await audit_database(db, source, now=NOW)

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

    report = await audit_database(db, source, now=NOW)

    assert source.calls == []
    assert report.rows[0].status == "inconsistent_persisted_state"
    assert report.rows[0].persisted_terminal_fields == expected_fields


@pytest.mark.asyncio
async def test_audit_is_byte_for_byte_read_only_with_existing_wal_sidecars(
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
    source = FakeSettlementSource({("42", "exact-market"): None})
    try:
        report = await audit_database(db, source, now=NOW)
        assert _artifact_hashes(db) == before
    finally:
        writer.close()

    assert report.rows[0].status == "pending_receipt"
