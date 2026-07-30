from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path

import pytest

from trading.runtime_paper_cohort_attestation import (
    RuntimePaperCohortAttestationError,
    build_runtime_paper_cohort_attestation,
    read_runtime_paper_cohort_attestation,
    write_runtime_paper_cohort_attestation,
)


@dataclass(frozen=True)
class _Cohort:
    cohort_id: str
    db_path: Path
    storage_root: Path


@dataclass(frozen=True)
class _Binding:
    cohort: _Cohort
    cohort_identity: str
    manifest_sha256: str
    cohort_type: str


def _pending_cohort(storage_root: Path) -> _Cohort:
    return _Cohort(
        cohort_id="legacy-pending-20260729",
        db_path=(
            storage_root
            / "legacy_pending_paper_cohorts"
            / "legacy-pending-20260729"
            / "paper_trades.db"
        ),
        storage_root=storage_root,
    )


def _pending_binding(cohort: _Cohort) -> _Binding:
    return _Binding(
        cohort=cohort,
        cohort_identity="a" * 32,
        manifest_sha256="b" * 64,
        cohort_type="legacy_pending",
    )


def _receipt_for_pending_cohort(storage_root: Path):
    cohort = _pending_cohort(storage_root)
    return build_runtime_paper_cohort_attestation(
        cohort,
        cohort_kind="legacy_pending",
        binding=_pending_binding(cohort),
        pid=12345,
        started_utc=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    )


def test_writes_and_reads_manifest_bound_pending_receipt(tmp_path: Path) -> None:
    storage_root = tmp_path / "data"
    receipt_path = tmp_path / "logs" / "state" / "runtime_paper_cohort_attestation.json"
    receipt = _receipt_for_pending_cohort(storage_root)

    write_runtime_paper_cohort_attestation(receipt, receipt_path)

    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload == {
        "cohort_id": "legacy-pending-20260729",
        "cohort_identity": "a" * 32,
        "cohort_kind": "legacy_pending",
        "db_path_relative_to_storage_root": (
            "legacy_pending_paper_cohorts/legacy-pending-20260729/paper_trades.db"
        ),
        "manifest_bound": True,
        "manifest_sha256": "b" * 64,
        "pid": 12345,
        "schema_version": 1,
        "started_utc": "2026-07-30T12:00:00.000000Z",
    }
    assert read_runtime_paper_cohort_attestation(
        receipt_path,
        storage_root=storage_root,
        expected_pid=12345,
    ) == receipt


def test_atomic_write_preserves_previous_receipt_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "data"
    receipt_path = tmp_path / "state" / "runtime_paper_cohort_attestation.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text('{"previous":"receipt"}\n', encoding="utf-8")
    previous_bytes = receipt_path.read_bytes()
    receipt = _receipt_for_pending_cohort(storage_root)

    import trading.runtime_paper_cohort_attestation as attestation

    def fail_replace(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(attestation.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        write_runtime_paper_cohort_attestation(receipt, receipt_path)

    assert receipt_path.read_bytes() == previous_bytes
    assert not list(receipt_path.parent.glob(".runtime_paper_cohort_attestation.json.*.tmp"))


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b'{"schema_version":1}',
        b'{"schema_version":1,"schema_version":2}',
    ],
)
def test_reader_rejects_malformed_payloads(tmp_path: Path, payload: bytes) -> None:
    receipt_path = tmp_path / "runtime_paper_cohort_attestation.json"
    receipt_path.write_bytes(payload)

    with pytest.raises(RuntimePaperCohortAttestationError):
        read_runtime_paper_cohort_attestation(
            receipt_path,
            storage_root=tmp_path / "data",
        )


def test_reader_rejects_symlink_receipt(tmp_path: Path) -> None:
    storage_root = tmp_path / "data"
    actual_path = tmp_path / "actual.json"
    write_runtime_paper_cohort_attestation(
        _receipt_for_pending_cohort(storage_root),
        actual_path,
    )
    receipt_path = tmp_path / "runtime_paper_cohort_attestation.json"
    try:
        receipt_path.symlink_to(actual_path)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(RuntimePaperCohortAttestationError, match="symlink"):
        read_runtime_paper_cohort_attestation(
            receipt_path,
            storage_root=storage_root,
        )


def test_reader_rejects_storage_root_relative_escape(tmp_path: Path) -> None:
    storage_root = tmp_path / "data"
    receipt_path = tmp_path / "runtime_paper_cohort_attestation.json"
    receipt = _receipt_for_pending_cohort(storage_root)
    payload = receipt.to_payload()
    payload["db_path_relative_to_storage_root"] = "../paper_trades.db"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimePaperCohortAttestationError, match="relative"):
        read_runtime_paper_cohort_attestation(
            receipt_path,
            storage_root=storage_root,
        )


def test_reader_rejects_receipt_for_different_pid(tmp_path: Path) -> None:
    storage_root = tmp_path / "data"
    receipt_path = tmp_path / "runtime_paper_cohort_attestation.json"
    write_runtime_paper_cohort_attestation(
        _receipt_for_pending_cohort(storage_root),
        receipt_path,
    )

    with pytest.raises(RuntimePaperCohortAttestationError, match="PID"):
        read_runtime_paper_cohort_attestation(
            receipt_path,
            storage_root=storage_root,
            expected_pid=12346,
        )
