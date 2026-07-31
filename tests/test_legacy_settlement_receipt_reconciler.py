from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import reconcile_legacy_paper_receipts as reconciler
from scripts.audit_open_paper_settlements import (
    AuditSnapshotArtifact,
    SettlementAuditReport,
    SettlementAuditRow,
    _load_open_rows_from_quiescent_snapshot,
)
from trading.legacy_settlement_receipts import build_legacy_settlement_receipt
from trading.paper_trader import PaperTrader
from trading.settlement import MarketOutcome, SettlementObservation, build_settlement_observation
from trading.settlement_store import SettlementStore
from trading.venue import MarketRef, Venue


OBSERVED_AT = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
APPLIED_AT = OBSERVED_AT + timedelta(minutes=1)


def _legacy_root(path: Path) -> None:
    trader = PaperTrader(db_path=path, startup_context="test")
    trader._conn.close()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE bot_state SET value='100.00' WHERE key='notional_bankroll'"
        )
        conn.execute(
            """
            INSERT INTO paper_trades (
                trade_id, ts, ticker, venue, venue_market_id, identity_status,
                market_title, side, contracts, price_cents, cost_dollars,
                estimated_prob, entry_price_cents, edge, kelly_dollars,
                capped_dollars, signal_headline, signal_source, keywords_matched,
                reasoning, fee_net_accounting_version
            ) VALUES (
                'legacy-trade-1', '2026-07-30T12:00:00+00:00',
                'legacy-exact-market', 'polymarket_us', '42', 'mapped',
                'Legacy exact market', 'yes', 2, 40, 0.80,
                0.61, 40, 0.21, 1.0, 1.0, 'legacy signal', 'legacy:test',
                '[]', 'legacy receipt fixture', NULL
            )
            """
        )


def _receipt(*, outcome: MarketOutcome = MarketOutcome.YES):
    market_ref = MarketRef(Venue.POLYMARKET_US, "42", "legacy-exact-market")
    observation = build_settlement_observation(
        market_ref=market_ref,
        outcome=outcome,
        authoritative_outcome={"outcome": outcome.value},
        authoritative_payload={"id": "42", "settled": True, "slug": market_ref.alias},
        observed_at=OBSERVED_AT,
        effective_at=OBSERVED_AT - timedelta(minutes=1),
        rules_version="test-rules-v1",
        source_id="test-authoritative-source",
    )
    return build_legacy_settlement_receipt("legacy-trade-1", observation)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _root_snapshot(path: Path) -> tuple[object, ...]:
    with sqlite3.connect(path) as conn:
        schema = tuple(
            conn.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        )
        trades = tuple(conn.execute("SELECT * FROM paper_trades ORDER BY trade_id"))
        observations = tuple(
            conn.execute(
                "SELECT * FROM paper_settlement_observations ORDER BY observation_sha256"
            )
        )
    return schema, trades, observations


class _FakeSource:
    def __init__(self, observation: SettlementObservation | None) -> None:
        self.observation = observation
        self.calls: list[tuple[MarketRef, SettlementObservation | None]] = []

    async def get_settlement_exact(
        self,
        market_ref: MarketRef,
        *,
        prior_observation: SettlementObservation | None,
    ) -> SettlementObservation | None:
        self.calls.append((market_ref, prior_observation))
        return self.observation


def _write_audit_report(snapshot_path: Path, receipt) -> Path:
    snapshot_sha256 = _sha256(snapshot_path)
    snapshot = _load_open_rows_from_quiescent_snapshot(
        snapshot_path,
        expected_sha256=snapshot_sha256,
    )
    observation = receipt.observation
    report = SettlementAuditReport(
        db_path=str(snapshot_path),
        generated_at=OBSERVED_AT.isoformat(),
        fetched_markets=1,
        rows=(
            SettlementAuditRow(
                trade_id=receipt.trade_id,
                ticker=observation.market_ref.alias,
                venue=observation.market_ref.venue.value,
                canonical_market_id=observation.market_ref.venue_market_id,
                identity_status="mapped",
                quarantine_reason=None,
                snapshot_close_time=None,
                status="authoritative_terminal",
                outcome=observation.outcome.value,
                source_id=observation.source_id,
                rules_version=observation.rules_version,
                observed_at=observation.observed_at.isoformat(),
                effective_at=observation.effective_at.isoformat(),
                payload_sha256=observation.payload_sha256,
                observation_sha256=observation.observation_sha256,
                receipt_bundle=receipt.to_dict(),
            ),
        ),
        snapshot_artifacts=(
            AuditSnapshotArtifact(
                name=snapshot_path.name,
                size=snapshot_path.stat().st_size,
                sha256=snapshot_sha256,
            ),
        ),
        open_rows_sha256=snapshot.open_rows_sha256,
    )
    report_path = snapshot_path.with_suffix(".audit.json")
    report_path.write_text(report.to_json(), encoding="utf-8")
    return report_path


def _inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "paper_trades.db"
    snapshot_path = tmp_path / "paper_trades.snapshot.db"
    _legacy_root(db_path)
    shutil.copy2(db_path, snapshot_path)
    monkeypatch.setattr(reconciler, "LEGACY_ROOT_DB", db_path)
    receipt = _receipt()
    audit_report_path = _write_audit_report(snapshot_path, receipt)
    review = reconciler.load_reviewed_legacy_receipt(
        audit_report_path,
        trade_id=receipt.trade_id,
        snapshot_db=snapshot_path,
        expected_snapshot_sha256=_sha256(snapshot_path),
        expected_audit_report_sha256=_sha256(audit_report_path),
    )
    return (
        db_path,
        snapshot_path,
        review,
        _sha256(db_path),
        _sha256(snapshot_path),
    )


def test_plan_is_read_only_and_attests_root_snapshot_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, snapshot_path, review, root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    before = _root_snapshot(db_path)

    plan = reconciler.plan_legacy_receipt_reconciliation(
        db_path,
        snapshot_db=snapshot_path,
        review=review,
        expected_root_sha256=root_sha256,
        expected_snapshot_sha256=snapshot_sha256,
    )

    assert plan["mode"] == "plan"
    assert plan["trade_id"] == review.receipt.trade_id
    assert plan["receipt_sha256"] == review.receipt.receipt_sha256
    assert plan["open_rows_sha256"]
    assert plan["audit_report_file_sha256"] == review.audit_report_file_sha256
    assert _root_snapshot(db_path) == before


def test_plan_revalidates_the_original_audit_report_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, snapshot_path, review, root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    review.audit_report_path.write_text(
        review.audit_report_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    before = _root_snapshot(db_path)

    with pytest.raises(reconciler.LegacyReceiptReconciliationError, match="provenance"):
        reconciler.plan_legacy_receipt_reconciliation(
            db_path,
            snapshot_db=snapshot_path,
            review=review,
            expected_root_sha256=root_sha256,
            expected_snapshot_sha256=snapshot_sha256,
        )

    assert _root_snapshot(db_path) == before


def test_cli_requires_external_audit_report_sha256() -> None:
    with pytest.raises(SystemExit):
        reconciler._parse_args(
            [
                "--db",
                "/tmp/paper_trades.db",
                "--snapshot-db",
                "/tmp/paper_trades.snapshot.db",
                "--audit-report",
                "/tmp/paper_trades.audit.json",
                "--trade-id",
                "legacy-trade-1",
                "--expected-root-sha256",
                "0" * 64,
                "--expected-snapshot-sha256",
                "1" * 64,
            ]
        )


def test_reviewed_audit_report_rejects_symlink_and_duplicate_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, snapshot_path, review, _root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    report_path = _write_audit_report(snapshot_path, review.receipt)
    report_sha256 = _sha256(report_path)
    report_symlink = tmp_path / "review-link.audit.json"
    report_symlink.symlink_to(report_path)

    with pytest.raises(reconciler.LegacyReceiptReconciliationError, match="provenance"):
        reconciler.load_reviewed_legacy_receipt(
            report_symlink,
            trade_id=review.receipt.trade_id,
            snapshot_db=snapshot_path,
            expected_snapshot_sha256=snapshot_sha256,
            expected_audit_report_sha256=report_sha256,
        )

    report_json = report_path.read_text(encoding="utf-8")
    report_path.write_text(
        report_json[:-1] + ',"read_only":true}',
        encoding="utf-8",
    )
    with pytest.raises(reconciler.LegacyReceiptReconciliationError, match="provenance"):
        reconciler.load_reviewed_legacy_receipt(
            report_path,
            trade_id=review.receipt.trade_id,
            snapshot_db=snapshot_path,
            expected_snapshot_sha256=snapshot_sha256,
            expected_audit_report_sha256=report_sha256,
        )

    report_path.write_text(
        report_json[:-1] + ',"ignored_nonfinite":NaN}',
        encoding="utf-8",
    )
    with pytest.raises(reconciler.LegacyReceiptReconciliationError, match="provenance"):
        reconciler.load_reviewed_legacy_receipt(
            report_path,
            trade_id=review.receipt.trade_id,
            snapshot_db=snapshot_path,
            expected_snapshot_sha256=snapshot_sha256,
            expected_audit_report_sha256=report_sha256,
        )

    report_path.write_text(report_json, encoding="utf-8")
    with pytest.raises(reconciler.LegacyReceiptReconciliationError, match="provenance"):
        reconciler.load_reviewed_legacy_receipt(
            report_path,
            trade_id=review.receipt.trade_id,
            snapshot_db=snapshot_path,
            expected_snapshot_sha256=snapshot_sha256,
            expected_audit_report_sha256="0" * 64,
        )


def test_reviewed_audit_report_rejects_parent_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, snapshot_path, review, _root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    original_report = _write_audit_report(snapshot_path, review.receipt)
    physical_dir = tmp_path / "physical-report-dir"
    physical_dir.mkdir()
    physical_report = physical_dir / original_report.name
    shutil.copy2(original_report, physical_report)
    alias_dir = tmp_path / "report-dir-alias"
    alias_dir.symlink_to(physical_dir, target_is_directory=True)

    with pytest.raises(reconciler.LegacyReceiptReconciliationError, match="provenance"):
        reconciler.load_reviewed_legacy_receipt(
            alias_dir / physical_report.name,
            trade_id=review.receipt.trade_id,
            snapshot_db=snapshot_path,
            expected_snapshot_sha256=snapshot_sha256,
            expected_audit_report_sha256=_sha256(physical_report),
        )


def test_apply_requires_both_network_and_write_interlocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, snapshot_path, review, root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    source = _FakeSource(review.receipt.observation)
    before = _root_snapshot(db_path)

    with pytest.raises(reconciler.LegacyReceiptReconciliationError, match="both"):
        asyncio.run(
            reconciler.apply_legacy_receipt_reconciliation(
                db_path,
                snapshot_db=snapshot_path,
                review=review,
                expected_root_sha256=root_sha256,
                expected_snapshot_sha256=snapshot_sha256,
                source=source,
                allow_network=True,
                write=False,
                applied_at=APPLIED_AT,
            )
        )
    with pytest.raises(reconciler.LegacyReceiptReconciliationError, match="both"):
        asyncio.run(
            reconciler.apply_legacy_receipt_reconciliation(
                db_path,
                snapshot_db=snapshot_path,
                review=review,
                expected_root_sha256=root_sha256,
                expected_snapshot_sha256=snapshot_sha256,
                source=source,
                allow_network=False,
                write=True,
                applied_at=APPLIED_AT,
            )
        )

    assert source.calls == []
    assert _root_snapshot(db_path) == before


def test_apply_requires_runtime_quiescence_before_source_or_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, snapshot_path, review, root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    source = _FakeSource(review.receipt.observation)
    before = _root_snapshot(db_path)

    @contextmanager
    def blocked_runtime_lock(_db_path: Path):
        raise RuntimeError("runtime lock is held")
        yield

    monkeypatch.setattr(reconciler, "_runtime_lock", blocked_runtime_lock)
    with pytest.raises(reconciler.LegacyReceiptReconciliationError, match="runtime lock"):
        asyncio.run(
            reconciler.apply_legacy_receipt_reconciliation(
                db_path,
                snapshot_db=snapshot_path,
                review=review,
                expected_root_sha256=root_sha256,
                expected_snapshot_sha256=snapshot_sha256,
                source=source,
                allow_network=True,
                write=True,
                applied_at=APPLIED_AT,
            )
        )

    assert source.calls == []
    assert _root_snapshot(db_path) == before
    assert not list(tmp_path.glob("*.legacy-receipt-*.bak"))


def test_apply_rejects_symlinked_runtime_lock_before_source_or_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, snapshot_path, review, root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    source = _FakeSource(review.receipt.observation)
    lock_path = db_path.parent / "bot_runtime.lock"
    lock_path.symlink_to(tmp_path / "redirected-runtime-lock")
    before = _root_snapshot(db_path)

    with pytest.raises(reconciler.LegacyReceiptReconciliationError, match="symlink"):
        asyncio.run(
            reconciler.apply_legacy_receipt_reconciliation(
                db_path,
                snapshot_db=snapshot_path,
                review=review,
                expected_root_sha256=root_sha256,
                expected_snapshot_sha256=snapshot_sha256,
                source=source,
                allow_network=True,
                write=True,
                applied_at=APPLIED_AT,
            )
        )

    assert source.calls == []
    assert _root_snapshot(db_path) == before
    assert not list(tmp_path.glob("*.legacy-receipt-*.bak"))


def test_apply_writes_adjacent_backup_after_fresh_source_equality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, snapshot_path, review, root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    source = _FakeSource(review.receipt.observation)

    result = asyncio.run(
        reconciler.apply_legacy_receipt_reconciliation(
            db_path,
            snapshot_db=snapshot_path,
            review=review,
            expected_root_sha256=root_sha256,
            expected_snapshot_sha256=snapshot_sha256,
            source=source,
            allow_network=True,
            write=True,
            applied_at=APPLIED_AT,
        )
    )

    backup_path = Path(str(result["backup_db"]))
    assert result["mode"] == "applied"
    assert result["applied"] is True
    assert backup_path.parent == db_path.parent
    assert backup_path.exists()
    assert _root_snapshot(backup_path) == _root_snapshot(snapshot_path)
    with sqlite3.connect(backup_path) as conn:
        integrity = tuple(conn.execute("PRAGMA integrity_check"))
        backup_bankroll = conn.execute(
            "SELECT value FROM bot_state WHERE key='notional_bankroll'"
        ).fetchone()[0]
    with sqlite3.connect(snapshot_path) as conn:
        snapshot_bankroll = conn.execute(
            "SELECT value FROM bot_state WHERE key='notional_bankroll'"
        ).fetchone()[0]
    assert integrity == (("ok",),)
    assert backup_bankroll == snapshot_bankroll
    assert source.calls == [
        (review.receipt.observation.market_ref, review.receipt.observation)
    ]
    with SettlementStore(db_path) as store:
        check = store.conservation(now=APPLIED_AT + timedelta(minutes=1))
    assert check.ok is True


def test_fresh_source_drift_rejects_before_backup_or_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, snapshot_path, review, root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    source = _FakeSource(_receipt(outcome=MarketOutcome.NO).observation)
    before = _root_snapshot(db_path)

    with pytest.raises(reconciler.LegacyReceiptReconciliationError, match="fresh source"):
        asyncio.run(
            reconciler.apply_legacy_receipt_reconciliation(
                db_path,
                snapshot_db=snapshot_path,
                review=review,
                expected_root_sha256=root_sha256,
                expected_snapshot_sha256=snapshot_sha256,
                source=source,
                allow_network=True,
                write=True,
                applied_at=APPLIED_AT,
            )
        )

    assert _root_snapshot(db_path) == before
    assert not list(tmp_path.glob("*.legacy-receipt-*.bak"))


def test_apply_rejects_dangling_backup_symlink_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, snapshot_path, review, root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    source = _FakeSource(review.receipt.observation)
    backup_path = db_path.with_name(
        f"{db_path.stem}.legacy-receipt-{review.receipt.receipt_sha256[:12]}.bak"
    )
    redirected_path = tmp_path / "redirected-backup.db"
    backup_path.symlink_to(redirected_path)
    before = _root_snapshot(db_path)

    with pytest.raises(
        reconciler.LegacyReceiptReconciliationError,
        match="refusing to overwrite existing backup",
    ):
        asyncio.run(
            reconciler.apply_legacy_receipt_reconciliation(
                db_path,
                snapshot_db=snapshot_path,
                review=review,
                expected_root_sha256=root_sha256,
                expected_snapshot_sha256=snapshot_sha256,
                source=source,
                allow_network=True,
                write=True,
                applied_at=APPLIED_AT,
            )
        )

    assert backup_path.is_symlink()
    assert not redirected_path.exists()
    assert _root_snapshot(db_path) == before
    assert not list(tmp_path.glob(".*.legacy-receipt-*.tmp"))


def test_apply_holds_writer_lock_during_preimage_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, snapshot_path, review, root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    source = _FakeSource(review.receipt.observation)
    original_backup = reconciler._write_adjacent_backup

    def assert_writer_lock(path: Path, receipt, **kwargs):
        contender = sqlite3.connect(path, isolation_level=None, timeout=0.0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("BEGIN IMMEDIATE")
        finally:
            contender.close()
        return original_backup(path, receipt, **kwargs)

    monkeypatch.setattr(reconciler, "_write_adjacent_backup", assert_writer_lock)
    result = asyncio.run(
        reconciler.apply_legacy_receipt_reconciliation(
            db_path,
            snapshot_db=snapshot_path,
            review=review,
            expected_root_sha256=root_sha256,
            expected_snapshot_sha256=snapshot_sha256,
            source=source,
            allow_network=True,
            write=True,
            applied_at=APPLIED_AT,
        )
    )

    assert result["applied"] is True


def test_apply_rechecks_root_under_write_lock_after_final_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, snapshot_path, review, root_sha256, snapshot_sha256 = _inputs(
        tmp_path,
        monkeypatch,
    )
    source = _FakeSource(review.receipt.observation)

    class _MutatingStore(SettlementStore):
        def __init__(self, path: Path) -> None:
            with sqlite3.connect(path) as conn:
                conn.execute(
                    "UPDATE bot_state SET value='99.99' WHERE key='notional_bankroll'"
                )
            super().__init__(path)

    monkeypatch.setattr(reconciler, "SettlementStore", _MutatingStore)

    with pytest.raises(
        reconciler.LegacyReceiptReconciliationError,
        match="legacy root SHA-256",
    ):
        asyncio.run(
            reconciler.apply_legacy_receipt_reconciliation(
                db_path,
                snapshot_db=snapshot_path,
                review=review,
                expected_root_sha256=root_sha256,
                expected_snapshot_sha256=snapshot_sha256,
                source=source,
                allow_network=True,
                write=True,
                applied_at=APPLIED_AT,
            )
        )

    with sqlite3.connect(db_path) as conn:
        trade = conn.execute(
            """
            SELECT resolved, settlement_observation_sha256
            FROM paper_trades WHERE trade_id=?
            """,
            (review.receipt.trade_id,),
        ).fetchone()
        observation_count = conn.execute(
            "SELECT COUNT(*) FROM paper_settlement_observations"
        ).fetchone()[0]
    assert trade == (0, None)
    assert observation_count == 0
    assert not list(tmp_path.glob("*.legacy-receipt-*.bak"))
