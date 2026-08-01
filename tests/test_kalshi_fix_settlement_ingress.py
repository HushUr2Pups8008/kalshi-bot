from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from trading.kalshi_fix_settlement_ingress import (
    KALSHI_FIX_SETTLEMENT_INGRESS_SOURCE_ID,
    KalshiFixSettlementIngressStore,
    SessionProvenance,
    VerifiedKalshiFixUMSEnvelope,
    read_kalshi_fix_settlement_ingress_snapshot,
)


def _provenance(*, account_party_id: str = "account-123") -> SessionProvenance:
    return SessionProvenance(
        verifier_name="kalshi-fix-session-verifier",
        verifier_version="v1",
        session_fingerprint="session-fingerprint-sha256:example",
        account_party_id_sha256=sha256(account_party_id.encode("utf-8")).hexdigest(),
        received_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
    )


def _message(
    *,
    report_id: str = "settle-123",
    outcome: str = "yes",
    settlement_price: str = "100.00",
    account_party_id: str = "account-123",
) -> dict[str, object]:
    """Structured UMS decoder output; ordered group lists remain ordered."""
    return {
        "35": "UMS",
        "20105": report_id,
        "55": "HIGHNY-23DEC31",
        "715": "20231231",
        "20107": outcome,
        "730": settlement_price,
        "20108": [
            {
                "20109": account_party_id,
                "20110": "24",
                "704": "100",
                "705": "0",
                "136": [
                    {
                        "137": "0.006",
                        "138": "USD",
                        "139": "4",
                        "891": "0",
                    }
                ],
            }
        ],
    }


def _envelope(
    *,
    raw_fix: bytes = b"8=FIXT.1.1\x0135=UMS\x0120105=settle-123\x01",
    message: dict[str, object] | None = None,
    provenance: SessionProvenance | None = None,
) -> VerifiedKalshiFixUMSEnvelope:
    return VerifiedKalshiFixUMSEnvelope(
        raw_fix=raw_fix,
        message=_message() if message is None else message,
        provenance=_provenance() if provenance is None else provenance,
    )


def _count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _initialized_store(path: Path) -> KalshiFixSettlementIngressStore:
    store = KalshiFixSettlementIngressStore(path)
    assert store.initialize() is True
    return store


def test_constructor_and_read_only_snapshot_never_create_database(tmp_path: Path) -> None:
    path = tmp_path / "state" / "kalshi_fix_settlement_ingress.db"

    store = KalshiFixSettlementIngressStore(path)

    assert not path.exists()
    assert store.initialize() is True
    assert path.exists()

    absent = tmp_path / "absent.db"
    absent_snapshot = read_kalshi_fix_settlement_ingress_snapshot(absent)
    assert absent_snapshot.status == "absent_non_authoritative"
    assert absent_snapshot.exists is False
    assert not absent.exists()
    assert absent_snapshot.transport_authentication == "upstream_attested_not_proven_by_ledger"
    assert absent_snapshot.raw_capture == "absent"
    assert absent_snapshot.session_provenance == "absent"
    assert absent_snapshot.pagination_coverage == "unknown"
    assert absent_snapshot.canonical_settlement_binding == "absent"
    assert absent_snapshot.fee_net_pnl == "unscorable"
    assert absent_snapshot.paper_trader_updated is False
    assert absent_snapshot.orders_changed is False
    assert absent_snapshot.promotion_eligible is False

    read_only_store = KalshiFixSettlementIngressStore(absent, existing_only=True)
    assert read_only_store.initialize() is False
    assert not absent.exists()
    with pytest.raises(RuntimeError, match="existing_only"):
        read_only_store.append_verified_envelope(_envelope())


def test_accepts_bound_ums_and_stores_economics_compatible_wrapper(tmp_path: Path) -> None:
    path = tmp_path / "ingress.db"
    store = _initialized_store(path)
    result = store.append_verified_envelope(_envelope())

    assert result.status == "accepted"
    assert result.source_id == KALSHI_FIX_SETTLEMENT_INGRESS_SOURCE_ID
    assert result.report_id == "settle-123"
    assert result.message_sha256 == sha256(result.message_json.encode("utf-8")).hexdigest()
    assert result.raw_fix_sha256 == sha256(_envelope().raw_fix).hexdigest()
    assert _count(path, "fix_settlement_reports") == 1
    assert _count(path, "fix_settlement_wire_observations") == 1

    record = store.records()[0]
    assert record.market_id == "HIGHNY-23DEC31"
    assert record.first_received_at == datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    assert json.loads(record.message_json)["35"] == "UMS"
    source_payload = json.loads(record.source_payload_json)
    assert source_payload["market_id"] == "HIGHNY-23DEC31"
    economics_message = source_payload["settlement_fee_receipt"]["message"]
    assert economics_message["Symbol"] == "HIGHNY-23DEC31"
    assert economics_message["MarketSettlementReportID"] == "settle-123"
    assert economics_message["NoMarketSettlementPartyIDs"][0]["MiscFees"][0]["MiscFeeAmt"] == "0.006"

    snapshot = read_kalshi_fix_settlement_ingress_snapshot(path)
    assert snapshot.status == "captured_non_authoritative"
    assert snapshot.schema_valid is True
    assert snapshot.report_count == 1
    assert snapshot.wire_observation_count == 1
    assert snapshot.conflict_count == 0
    assert snapshot.quarantine_count == 0
    assert snapshot.raw_capture == "present"
    assert snapshot.session_provenance == "present"
    assert snapshot.pagination_coverage == "unknown"
    assert snapshot.canonical_settlement_binding == "absent"
    assert snapshot.fee_net_pnl == "unscorable"
    assert snapshot.paper_trader_updated is False
    assert snapshot.orders_changed is False
    assert snapshot.promotion_eligible is False


def test_exact_retransmit_is_idempotent_and_new_wire_is_appended(tmp_path: Path) -> None:
    path = tmp_path / "ingress.db"
    store = _initialized_store(path)
    envelope = _envelope()

    first = store.append_verified_envelope(envelope)
    identical = store.append_verified_envelope(envelope)
    retransmit = store.append_verified_envelope(
        _envelope(raw_fix=b"8=FIXT.1.1\x0135=UMS\x018=alternate-wire\x01")
    )

    assert first.status == "accepted"
    assert identical.status == "identical"
    assert retransmit.status == "retransmit"
    assert _count(path, "fix_settlement_reports") == 1
    assert _count(path, "fix_settlement_wire_observations") == 2


def test_conflicting_message_never_overwrites_accepted_report(tmp_path: Path) -> None:
    path = tmp_path / "ingress.db"
    store = _initialized_store(path)
    store.append_verified_envelope(_envelope())

    conflict = store.append_verified_envelope(
        _envelope(
            raw_fix=b"8=FIXT.1.1\x0135=UMS\x0120105=settle-123\x0120107=no\x01",
            message=_message(outcome="no"),
        )
    )

    assert conflict.status == "conflict"
    assert _count(path, "fix_settlement_reports") == 1
    assert _count(path, "fix_settlement_conflicts") == 1
    assert json.loads(store.records()[0].message_json)["20107"] == "yes"


def test_same_symbol_distinct_report_ids_persist_as_separate_fragments(tmp_path: Path) -> None:
    path = tmp_path / "ingress.db"
    store = _initialized_store(path)

    first = store.append_verified_envelope(_envelope())
    second = store.append_verified_envelope(
        _envelope(
            raw_fix=b"8=FIXT.1.1\x0135=UMS\x0120105=settle-456\x01",
            message=_message(report_id="settle-456"),
        )
    )

    assert first.status == "accepted"
    assert second.status == "accepted"
    assert _count(path, "fix_settlement_reports") == 2
    assert tuple(record.report_id for record in store.records()) == ("settle-123", "settle-456")


def test_invalid_verified_message_and_untrusted_provenance_are_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "ingress.db"
    store = _initialized_store(path)

    invalid_message = _message()
    invalid_message["35"] = "D"
    bad_message = store.append_verified_envelope(_envelope(message=invalid_message))
    untrusted = store.append_verified_envelope(
        _envelope(
            raw_fix=b"8=FIXT.1.1\x0135=UMS\x01bad-provenance\x01",
            provenance=SessionProvenance(
                verifier_name="",
                verifier_version="v1",
                session_fingerprint="",
                account_party_id_sha256="not-a-sha256",
                received_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
            ),
        )
    )

    assert bad_message.status == "quarantined"
    assert untrusted.status == "quarantined"
    assert _count(path, "fix_settlement_reports") == 0
    assert _count(path, "fix_settlement_quarantines") == 2
    snapshot = store.snapshot()
    assert snapshot.status == "captured_non_authoritative"
    assert snapshot.raw_capture == "present"
    assert snapshot.session_provenance == "present"


def test_only_typed_verified_envelopes_cross_the_writer_boundary(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path / "ingress.db")

    for invalid in ({}, "not-an-envelope", b"raw-fix", tmp_path / "fix.log"):
        with pytest.raises(TypeError, match="VerifiedKalshiFixUMSEnvelope"):
            store.append_verified_envelope(invalid)  # type: ignore[arg-type]


def test_immutable_tables_and_tampered_schema_are_not_reported_ready(tmp_path: Path) -> None:
    path = tmp_path / "ingress.db"
    store = _initialized_store(path)
    store.append_verified_envelope(_envelope())

    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM fix_settlement_reports")
        conn.execute("DROP TRIGGER immutable_fix_settlement_reports_delete")

    snapshot = read_kalshi_fix_settlement_ingress_snapshot(path)
    assert snapshot.status == "invalid_non_authoritative"
    assert snapshot.schema_valid is False
    assert snapshot.report_count == 0
    assert snapshot.raw_capture == "absent"
    assert snapshot.session_provenance == "absent"
    assert snapshot.transport_authentication == "upstream_attested_not_proven_by_ledger"
