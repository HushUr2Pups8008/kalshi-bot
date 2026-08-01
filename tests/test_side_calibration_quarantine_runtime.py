import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tasks.side_calibration_quarantine import (
    SideCalibrationCandidateDecision,
    SideCalibrationPrequeueBookDecision,
    SideCalibrationQuarantineDecisionContext,
    SideCalibrationStartupConfig,
    build_side_calibration_quarantine_runtime,
)
from trading.runtime_paper_cohort_attestation import RuntimePaperCohortAttestation
from trading.side_calibration_quarantine import (
    SideCalibrationFeeContext,
    SideCalibrationMarketContract,
    SideCalibrationProvenance,
    SideCalibrationSizingProvenance,
)


UTC = timezone.utc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _available(identifier: str) -> SideCalibrationProvenance:
    return SideCalibrationProvenance(
        state="available",
        identifier=identifier,
        payload_sha256=_sha256(identifier),
    )


def _attestation(*, manifest_bound: bool, cohort_kind: str) -> RuntimePaperCohortAttestation:
    return RuntimePaperCohortAttestation(
        pid=123,
        started_utc=datetime(2026, 8, 1, 17, tzinfo=UTC),
        cohort_id="paper-cohort-20260801",
        cohort_kind=cohort_kind,
        db_path_relative_to_storage_root="paper_trades.db",
        manifest_bound=manifest_bound,
        cohort_identity=("paper-cohort-identity" if manifest_bound else None),
        manifest_sha256=(_sha256("manifest") if manifest_bound else None),
    )


def _context() -> SideCalibrationQuarantineDecisionContext:
    decision_at = datetime(2026, 8, 1, 17, 5, tzinfo=UTC)
    return SideCalibrationQuarantineDecisionContext(
        decision_at=decision_at,
        candidate=SideCalibrationCandidateDecision(
            capture_id="capture-20260801-001",
            lifecycle_id="lifecycle-20260801-001",
            venue="kalshi",
            ticker="KXTEST-26AUG01",
            native_market_id="KXTEST-26AUG01",
            settlement_alias="KXTEST-26AUG01",
            side="yes",
            model_yes_probability=Decimal("0.62"),
            selected_side_probability=Decimal("0.62"),
            executed_price=Decimal("0.45"),
            derived_gross_edge=Decimal("0.17"),
            reported_gross_edge=Decimal("0.17"),
            sizing=SideCalibrationSizingProvenance(
                state="available",
                method="fractional_kelly",
                requested_stake_dollars=Decimal("12.50"),
                payload_sha256=_sha256("sizing"),
            ),
            fee_context=SideCalibrationFeeContext(
                state="available",
                fee_role="taker",
                fee_schedule_sha256=_sha256("fee-schedule"),
                provenance_sha256=_sha256("fee-provenance"),
            ),
            market_contract=SideCalibrationMarketContract(
                state="available",
                canonical_contract="Will the test condition resolve YES?",
                question="Will the test condition resolve YES?",
                market_snapshot_sha256=_sha256("market"),
                scheduled_close_at=decision_at + timedelta(hours=1),
                scheduled_settlement_at=decision_at + timedelta(hours=2),
            ),
        ),
        evidence_ids=("evidence-1", "evidence-2"),
        research_provenance=_available("research-1"),
        dossier_provenance=SideCalibrationProvenance(
            state="not_applicable",
            detail="non-research candidate",
        ),
        run_provenance=_available("run-1"),
        contract_provenance=_available("contract-1"),
        prequeue_book_provenance=SideCalibrationPrequeueBookDecision(
            status="available",
            venue="kalshi",
            requested_market_id="KXTEST-26AUG01",
            native_market_id="KXTEST-26AUG01",
            book_market_id="KXTEST-26AUG01",
            book_observed_at=decision_at - timedelta(seconds=1),
            book_payload_hash=_sha256("book"),
            reason=None,
        ),
    )


def _runtime(tmp_path, *, attestation: RuntimePaperCohortAttestation):
    return build_side_calibration_quarantine_runtime(
        db_path=tmp_path / "quarantine.db",
        startup_config=SideCalibrationStartupConfig(
            feature_enabled=True,
            is_paper_trading=True,
            live_trading_enabled=False,
            paper_cohort_id=attestation.cohort_id,
            paper_cohort_kind=attestation.cohort_kind,
        ),
        software_version="test-version",
        cohort_attestation=attestation,
        kalshi_reader=object(),
        polymarket_reader=object(),
    )


@pytest.mark.asyncio
async def test_runtime_sink_persists_complete_frozen_context_as_candidate(tmp_path):
    runtime = _runtime(tmp_path, attestation=_attestation(manifest_bound=True, cohort_kind="active"))

    result = await runtime.sink.capture(_context())

    assert result.status == "inserted"
    assert result.disposition == "candidate"
    assert result.unscorable_reasons == ()


@pytest.mark.asyncio
async def test_runtime_sink_records_legacy_unbound_cohort_as_unscorable(tmp_path):
    runtime = _runtime(tmp_path, attestation=_attestation(manifest_bound=False, cohort_kind="legacy"))

    result = await runtime.sink.capture(_context())

    assert result.status == "unscorable"
    assert result.disposition == "unscorable"
    assert "invalid_paper_cohort" in result.unscorable_reasons
