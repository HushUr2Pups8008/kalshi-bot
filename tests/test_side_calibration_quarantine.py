from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.side_calibration_quarantine import (
    SideCalibrationCapture,
    SideCalibrationFeeContext,
    SideCalibrationMarketContract,
    SideCalibrationPaperCohort,
    SideCalibrationPolicy,
    SideCalibrationProvenance,
    SideCalibrationQuarantineError,
    SideCalibrationQuarantineStore,
    SideCalibrationSizingProvenance,
)


UTC = timezone.utc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _available(identifier: str) -> SideCalibrationProvenance:
    return SideCalibrationProvenance(
        state="available",
        identifier=identifier,
        payload_sha256=_sha256(f"payload:{identifier}"),
    )


def _sizing() -> SideCalibrationSizingProvenance:
    return SideCalibrationSizingProvenance(
        state="available",
        method="fractional_kelly",
        requested_stake_dollars=Decimal("12.50"),
        payload_sha256=_sha256("sizing"),
    )


def _fee_context() -> SideCalibrationFeeContext:
    return SideCalibrationFeeContext(
        state="available",
        fee_role="taker",
        fee_schedule_sha256=_sha256("fee-schedule"),
        provenance_sha256=_sha256("fee-provenance"),
    )


def _cohort() -> SideCalibrationPaperCohort:
    return SideCalibrationPaperCohort(
        cohort_id="active-20260801",
        cohort_kind="active",
        cohort_identity="cohort-identity-20260801",
        manifest_sha256=_sha256("cohort-manifest"),
    )


def _policy() -> SideCalibrationPolicy:
    return SideCalibrationPolicy(
        policy_id="paper-side-calibration-quarantine",
        policy_version="2026-08-01",
        schema_version=1,
        payload_sha256=_sha256("quarantine-policy"),
    )


def _market_contract() -> SideCalibrationMarketContract:
    return SideCalibrationMarketContract(
        state="available",
        canonical_contract="Will the test condition resolve YES?",
        question="Will the test condition resolve YES?",
        market_snapshot_sha256=_sha256("market-snapshot"),
        scheduled_close_at=datetime(2026, 8, 1, 18, 0, tzinfo=UTC),
        scheduled_settlement_at=datetime(2026, 8, 2, 18, 0, tzinfo=UTC),
    )


def _capture(**overrides: object) -> SideCalibrationCapture:
    decision_at = datetime(2026, 8, 1, 17, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "capture_id": "decision-20260801-001",
        "lifecycle_id": "lifecycle-20260801-001",
        "decision_at": decision_at,
        "captured_at": decision_at + timedelta(seconds=1),
        "venue": "kalshi",
        "ticker": "KXTEST-26AUG01",
        "native_market_id": "KXTEST-26AUG01",
        "side": "yes",
        "model_yes_probability": Decimal("0.62"),
        "selected_side_probability": Decimal("0.62"),
        "executed_price": Decimal("0.45"),
        "derived_gross_edge": Decimal("0.17"),
        "reported_gross_edge": Decimal("0.18"),
        "sizing": _sizing(),
        "book_observed_at": decision_at - timedelta(seconds=2),
        "book_payload_sha256": _sha256("book"),
        "evidence_ids": ("evidence-2", "evidence-1"),
        "research_provenance": _available("research-1"),
        "dossier_provenance": SideCalibrationProvenance(
            state="not_applicable", detail="fast-lane candidate"
        ),
        "run_provenance": _available("run-1"),
        "contract_provenance": _available("contract-1"),
        "fee_context": _fee_context(),
        "market_contract": _market_contract(),
        "paper_cohort": _cohort(),
        "quarantine_policy": _policy(),
        "software_provenance": _available("git:deadbeef"),
        "config_provenance": _available("config:paper-defaults"),
    }
    values.update(overrides)
    return SideCalibrationCapture(**values)  # type: ignore[arg-type]


def test_complete_capture_freezes_candidate_and_required_facts(tmp_path):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")

    result = store.append_capture(_capture())

    assert result.status == "inserted"
    assert result.disposition == "candidate"
    assert result.candidate_id is not None
    assert result.unscorable_reasons == ()
    with sqlite3.connect(store.path) as connection:
        market_contract = connection.execute(
            "SELECT market_contract_json FROM side_calibration_candidates"
        ).fetchone()
    assert market_contract is not None
    assert "market-snapshot" not in market_contract[0]
    assert _sha256("market-snapshot") in market_contract[0]
    snapshot = store.snapshot()
    assert snapshot.attempt_count == 1
    assert snapshot.candidate_count == 1
    assert snapshot.lifecycle_event_count == 2


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"decision_at": None}, "unavailable_decision_at"),
        ({"venue": "unsupported"}, "invalid_venue"),
        ({"model_yes_probability": None}, "unavailable_model_yes_probability"),
        ({"selected_side_probability": Decimal("1.1")}, "invalid_selected_side_probability"),
        ({"executed_price": None}, "unavailable_executed_price"),
        ({"derived_gross_edge": Decimal("0.16")}, "derived_gross_edge_mismatch"),
        ({"reported_gross_edge": None}, "unavailable_reported_gross_edge"),
        ({"sizing": SideCalibrationSizingProvenance(state="unavailable", detail="missing")}, "unavailable_sizing_provenance"),
        ({"book_payload_sha256": "A" * 64}, "invalid_book_payload_sha256"),
        ({"evidence_ids": ()}, "unavailable_evidence_ids"),
        ({"research_provenance": SideCalibrationProvenance(state="unavailable", detail="source outage")}, "unavailable_research_provenance"),
        ({"dossier_provenance": SideCalibrationProvenance(state="wrong")}, "invalid_dossier_provenance"),
        ({"run_provenance": SideCalibrationProvenance(state="unavailable", detail="not persisted")}, "unavailable_run_provenance"),
        ({"contract_provenance": SideCalibrationProvenance(state="unavailable", detail="not persisted")}, "unavailable_contract_provenance"),
        ({"fee_context": SideCalibrationFeeContext(state="unavailable", detail="fee missing")}, "unavailable_fee_context"),
        ({"market_contract": SideCalibrationMarketContract(state="unavailable", detail="snapshot missing")}, "unavailable_market_contract_metadata"),
        ({"paper_cohort": replace(_cohort(), manifest_sha256="not-a-hash")}, "invalid_paper_cohort"),
        ({"quarantine_policy": replace(_policy(), policy_version=None)}, "invalid_quarantine_policy"),
        ({"software_provenance": SideCalibrationProvenance(state="unavailable", detail="unknown")}, "unavailable_software_provenance"),
        ({"config_provenance": SideCalibrationProvenance(state="unavailable", detail="unknown")}, "unavailable_config_provenance"),
    ],
    ids=[
        "decision-time",
        "venue",
        "model-probability",
        "selected-probability",
        "price",
        "derived-edge",
        "reported-edge",
        "sizing",
        "book",
        "evidence",
        "research",
        "dossier",
        "run",
        "contract",
        "fee",
        "market-contract",
        "cohort",
        "policy",
        "software",
        "config",
    ],
)
def test_incomplete_or_invalid_core_facts_remain_unscorable_attempts(
    tmp_path, override, reason
):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")

    result = store.append_capture(_capture(**override))

    assert result.status == "unscorable"
    assert result.disposition == "unscorable"
    assert reason in result.unscorable_reasons
    assert result.candidate_id is None
    assert store.snapshot().attempt_count == 1
    assert store.snapshot().candidate_count == 0


def test_cohort_and_policy_provenance_repeat_on_all_evidence_rows(tmp_path):
    path = tmp_path / "quarantine.db"
    store = SideCalibrationQuarantineStore(path)
    first = store.append_capture(_capture())
    conflict = store.append_capture(_capture(reported_gross_edge=Decimal("0.19")))

    assert first.status == "inserted"
    assert conflict.status == "conflict"
    with sqlite3.connect(path) as connection:
        expected_cohort = _canonical_cohort()
        expected_policy = _canonical_policy()
        for table in (
            "side_calibration_capture_attempts",
            "side_calibration_candidates",
            "side_calibration_lifecycle_events",
            "side_calibration_conflicts",
        ):
            values = connection.execute(
                f"SELECT DISTINCT cohort_json, policy_json FROM {table}"
            ).fetchall()
            assert values == [(expected_cohort, expected_policy)]


def test_duplicate_records_one_deterministic_replay_lifecycle_event(tmp_path):
    path = tmp_path / "quarantine.db"
    store = SideCalibrationQuarantineStore(path)
    first = store.append_capture(_capture())
    second = store.append_capture(_capture())
    third = store.append_capture(_capture())

    assert first.status == "inserted"
    assert second.status == "duplicate"
    assert second.disposition == "candidate"
    assert third == second
    with sqlite3.connect(path) as connection:
        events = connection.execute(
            "SELECT event_type FROM side_calibration_lifecycle_events ORDER BY event_type"
        ).fetchall()
    assert events == [
        ("candidate_frozen",),
        ("capture_recorded",),
        ("identical_replay",),
    ]


def test_unscorable_replay_stays_unscorable_and_later_completion_conflicts(tmp_path):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")
    incomplete = _capture(book_payload_sha256="not-a-sha256")

    first = store.append_capture(incomplete)
    replay = store.append_capture(incomplete)
    conflict = store.append_capture(_capture())

    assert first.status == "unscorable"
    assert replay.status == "duplicate"
    assert replay.disposition == "unscorable"
    assert replay.unscorable_reasons == first.unscorable_reasons
    assert conflict.status == "conflict"
    snapshot = store.snapshot()
    assert snapshot.attempt_count == 1
    assert snapshot.candidate_count == 0
    assert snapshot.conflict_count == 1


def test_invalid_evidence_id_member_is_persisted_as_unscorable_attempt(tmp_path):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")

    result = store.append_capture(_capture(evidence_ids=("evidence-1", 7)))  # type: ignore[arg-type]

    assert result.status == "unscorable"
    assert "invalid_evidence_ids" in result.unscorable_reasons
    assert store.snapshot().candidate_count == 0


def test_conflict_persists_conflict_and_lifecycle_without_mutating_original(tmp_path):
    path = tmp_path / "quarantine.db"
    store = SideCalibrationQuarantineStore(path)
    first = store.append_capture(_capture())
    conflict = store.append_capture(_capture(reported_gross_edge=Decimal("0.19")))
    replayed_conflict = store.append_capture(
        _capture(reported_gross_edge=Decimal("0.19"))
    )

    assert conflict.status == "conflict"
    assert conflict.disposition == "conflict"
    assert conflict.conflict_id is not None
    assert replayed_conflict == conflict
    with sqlite3.connect(path) as connection:
        candidate = connection.execute(
            "SELECT payload_sha256 FROM side_calibration_candidates"
        ).fetchone()
        conflict_rows = connection.execute(
            "SELECT conflict_id FROM side_calibration_conflicts"
        ).fetchall()
        events = connection.execute(
            "SELECT event_type FROM side_calibration_lifecycle_events ORDER BY event_type"
        ).fetchall()
    assert candidate == (first.payload_sha256,)
    assert conflict_rows == [(conflict.conflict_id,)]
    assert ("conflict_recorded",) in events


def test_canonical_ordering_is_deterministic_for_evidence_ids(tmp_path):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")

    first = store.append_capture(_capture(evidence_ids=("evidence-2", "evidence-1")))
    second = store.append_capture(_capture(evidence_ids=("evidence-1", "evidence-2")))

    assert first.payload_sha256 == second.payload_sha256
    assert second.status == "duplicate"


def test_all_evidence_tables_reject_updates_and_deletes(tmp_path):
    path = tmp_path / "quarantine.db"
    store = SideCalibrationQuarantineStore(path)
    store.append_capture(_capture())
    store.append_capture(_capture(reported_gross_edge=Decimal("0.19")))

    with sqlite3.connect(path) as connection:
        for table in (
            "side_calibration_schema_meta",
            "side_calibration_capture_attempts",
            "side_calibration_candidates",
            "side_calibration_lifecycle_events",
            "side_calibration_conflicts",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute(f"UPDATE {table} SET rowid = rowid")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute(f"DELETE FROM {table}")


@pytest.mark.parametrize("mutation", ["missing_trigger", "partial", "old", "extra"])
def test_nonempty_drifted_schemas_fail_closed(tmp_path, mutation):
    path = tmp_path / "quarantine.db"
    if mutation == "partial":
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE side_calibration_capture_attempts (capture_id TEXT)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX partial_capture_id "
                "ON side_calibration_capture_attempts(capture_id) "
                "WHERE capture_id IS NOT NULL"
            )
    elif mutation == "old":
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO schema_metadata(key, value) VALUES ('schema_version', '2')"
            )
    else:
        store = SideCalibrationQuarantineStore(path)
        with sqlite3.connect(path) as connection:
            if mutation == "missing_trigger":
                connection.execute(
                    "DROP TRIGGER immutable_side_calibration_candidates_update"
                )
            else:
                connection.execute("CREATE TABLE unexpected_side_calibration_state (id INTEGER)")
        del store

    with pytest.raises(SideCalibrationQuarantineError, match="schema drift"):
        SideCalibrationQuarantineStore(path)


def _canonical_cohort() -> str:
    return (
        '{"cohort_id":"active-20260801","cohort_identity":"cohort-identity-20260801",'
        '"cohort_kind":"active","manifest_sha256":"'
        + _sha256("cohort-manifest")
        + '"}'
    )


def _canonical_policy() -> str:
    return (
        '{"payload_sha256":"'
        + _sha256("quarantine-policy")
        + '","policy_id":"paper-side-calibration-quarantine",'
        '"policy_version":"2026-08-01","schema_version":1}'
    )
