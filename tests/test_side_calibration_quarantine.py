from __future__ import annotations

import sqlite3

import pytest

from trading.side_calibration_quarantine import (
    SideCalibrationCapture,
    SideCalibrationQuarantineError,
    SideCalibrationQuarantineStore,
)


def _complete_capture(**overrides: object) -> SideCalibrationCapture:
    values: dict[str, object] = {
        "capture_id": "lifecycle-20260801-001",
        "decision_facts": {
            "gross_edge": 0.06,
            "market_price": 0.44,
            "market_ticker": "KXTEST-26AUG01",
            "side": "yes",
        },
        "evidence_facts": {
            "book_hash": "a" * 64,
            "trigger": {"source": "fast_lane", "signal_id": "signal-1"},
        },
        "lifecycle_events": (
            {"event_type": "captured", "sequence": 1},
        ),
    }
    values.update(overrides)
    return SideCalibrationCapture(**values)  # type: ignore[arg-type]


def _capture_without_book_hash() -> SideCalibrationCapture:
    capture = _complete_capture()
    return SideCalibrationCapture(
        capture_id=capture.capture_id,
        decision_facts=capture.decision_facts,
        evidence_facts={"trigger": {"source": "fast_lane", "signal_id": "signal-1"}},
        lifecycle_events=capture.lifecycle_events,
    )


def test_append_candidate_is_idempotent_for_the_same_decision_payload(tmp_path):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")

    first = store.append_capture(_complete_capture())
    second = store.append_capture(_complete_capture())

    assert first.status == "inserted"
    assert second.status == "duplicate"
    assert first.candidate is not None
    assert second.candidate == first.candidate
    assert store.snapshot().candidate_count == 1


def test_conflicting_retry_is_rejected_without_mutating_existing_candidate(tmp_path):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")
    store.append_capture(_complete_capture())

    with pytest.raises(SideCalibrationQuarantineError, match="conflicting capture"):
        store.append_capture(
            _complete_capture(
                decision_facts={
                    "gross_edge": 0.07,
                    "market_price": 0.44,
                    "market_ticker": "KXTEST-26AUG01",
                    "side": "yes",
                }
            )
        )

    assert store.snapshot().candidate_count == 1


def test_incomplete_capture_is_recorded_as_attempt_not_candidate(tmp_path):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")

    result = store.append_capture(_capture_without_book_hash())

    assert result.status == "unscorable"
    assert result.attempt is not None
    assert result.attempt.unscorable_reason == "missing_book_hash"
    assert store.snapshot().attempt_count == 1
    assert store.snapshot().candidate_count == 0


def test_unscorable_capture_id_cannot_later_become_a_candidate(tmp_path):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")
    store.append_capture(_capture_without_book_hash())

    with pytest.raises(SideCalibrationQuarantineError, match="conflicting capture"):
        store.append_capture(_complete_capture())

    snapshot = store.snapshot()
    assert snapshot.attempt_count == 1
    assert snapshot.candidate_count == 0


def test_invalid_book_hash_is_recorded_as_unscorable(tmp_path):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")

    result = store.append_capture(
        _complete_capture(evidence_facts={"book_hash": "A" * 64})
    )

    assert result.status == "unscorable"
    assert result.attempt is not None
    assert result.attempt.unscorable_reason == "invalid_book_hash"
    assert store.snapshot().candidate_count == 0


def test_exact_unscorable_retry_preserves_unscorable_disposition(tmp_path):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")
    capture = _capture_without_book_hash()

    first = store.append_capture(capture)
    second = store.append_capture(capture)

    assert first.status == "unscorable"
    assert second.status == "unscorable"
    assert second.attempt == first.attempt
    assert second.candidate is None
    assert store.snapshot().attempt_count == 1


def test_payload_identity_is_canonical_across_mapping_order(tmp_path):
    store = SideCalibrationQuarantineStore(tmp_path / "quarantine.db")
    first = _complete_capture()
    reordered = _complete_capture(
        decision_facts={
            "side": "yes",
            "market_ticker": "KXTEST-26AUG01",
            "market_price": 0.44,
            "gross_edge": 0.06,
        },
        evidence_facts={
            "trigger": {"signal_id": "signal-1", "source": "fast_lane"},
            "book_hash": "a" * 64,
        },
    )

    first_result = store.append_capture(first)
    second_result = store.append_capture(reordered)

    assert second_result.status == "duplicate"
    assert second_result.payload_sha256 == first_result.payload_sha256


def test_evidence_tables_reject_update_and_delete(tmp_path):
    path = tmp_path / "quarantine.db"
    store = SideCalibrationQuarantineStore(path)
    store.append_capture(_complete_capture())

    with sqlite3.connect(path) as connection:
        for table in ("capture_attempts", "candidates", "lifecycle_events"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute(f"DELETE FROM {table}")
