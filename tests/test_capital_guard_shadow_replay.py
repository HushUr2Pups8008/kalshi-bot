from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3

import pytest

from scripts.capital_guard_shadow_replay import (
    _safe_output_path,
    build_replay_report,
    main,
)
from tests.test_capital_guard_shadow import (
    NOW,
    _append_candidate,
    candidate,
    capture_attempt,
    observation,
)
from trading.capital_guard_shadow import (
    CapitalGuardShadowStore,
    CapitalGuardShadowReplaySnapshotError,
    ShadowSettlement,
    canonical_json,
    read_capital_guard_shadow_replay_snapshot,
)


def _store(tmp_path: Path) -> CapitalGuardShadowStore:
    store = CapitalGuardShadowStore(tmp_path / "capital_guard_shadow.db")
    store.initialize(applied_at=NOW)
    return store


def _candidate_at(
    *,
    decision_key: str,
    lifecycle_id: str,
    venue_market_id: str,
    at,
    failures: tuple[str, ...] = ("G7_open_exposure_drawdown",),
):
    record = candidate(
        decision_key=decision_key,
        lifecycle_id=lifecycle_id,
        venue_market_id=venue_market_id,
        failures=failures,
    )
    return replace(
        record,
        decision_at=at,
        captured_at=at + timedelta(seconds=1),
        book_observed_at=at - timedelta(milliseconds=1),
    )


def _append_settlement(
    store: CapitalGuardShadowStore,
    *,
    candidate_id: str,
    observation_sha256: str,
    outcome: str,
    settled_at,
    gross_payout: Decimal,
) -> None:
    store.append_settlement(
        ShadowSettlement(
            candidate_id=candidate_id,
            observation_sha256=observation_sha256,
            outcome=outcome,
            settled_at=settled_at,
            gross_payout=gross_payout,
            settlement_fee=Decimal("0"),
            settlement_refund=Decimal("0"),
            net_payout=gross_payout,
            details_json=canonical_json({"schema_version": 1}),
        )
    )


def _sidecars(path: Path) -> dict[str, bytes]:
    paths = [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]
    return {str(item): item.read_bytes() for item in paths if item.exists()}


def test_snapshot_is_read_only_and_selects_only_the_current_correction_head(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record = _candidate_at(
        decision_key="replay-correction",
        lifecycle_id="replay-correction",
        venue_market_id="REPLAY-CORRECTION",
        at=NOW,
    )
    candidate_id = _append_candidate(store, record).candidate_id
    first = observation(
        venue_market_id=record.venue_market_id,
        outcome="yes",
        observed_at=NOW + timedelta(minutes=1),
    )
    first_sha = store.append_observation(
        first, candidate_ids=(candidate_id,)
    ).observation_sha256
    corrected = observation(
        venue_market_id=record.venue_market_id,
        outcome="no",
        observed_at=NOW + timedelta(minutes=2),
        supersedes=first_sha,
    )
    corrected_sha = store.append_observation(
        corrected, candidate_ids=(candidate_id,)
    ).observation_sha256
    before = _sidecars(store.db_path)

    snapshot = read_capital_guard_shadow_replay_snapshot(store.db_path)

    after = _sidecars(store.db_path)
    assert after == before
    assert snapshot.candidates[0].current_observation is not None
    assert snapshot.candidates[0].current_observation.observation_sha256 == corrected_sha
    assert snapshot.candidates[0].current_observation.outcome == "no"


def test_snapshot_copies_source_sidecars_without_touching_them(tmp_path: Path) -> None:
    store = _store(tmp_path)
    shm = store.db_path.with_name(store.db_path.name + "-shm")
    shm.write_bytes(b"not a replay input")
    before = _sidecars(store.db_path)

    snapshot = read_capital_guard_shadow_replay_snapshot(store.db_path)

    assert snapshot.candidates == ()
    assert _sidecars(store.db_path) == before


def test_snapshot_reads_nonempty_wal_without_touching_source(tmp_path: Path) -> None:
    store = _store(tmp_path)
    wal = store.db_path.with_name(store.db_path.name + "-wal")
    with sqlite3.connect(store.db_path, isolation_level=None) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        store.append_capture_attempt(capture_attempt(candidate()))
        assert wal.stat().st_size > 0
        before = _sidecars(store.db_path)

        snapshot = read_capital_guard_shadow_replay_snapshot(store.db_path)

        assert snapshot.candidates == ()
        assert _sidecars(store.db_path) == before


def test_snapshot_rejects_an_active_rollback_journal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    journal = store.db_path.with_name(store.db_path.name + "-journal")
    journal.write_bytes(b"active journal")

    with pytest.raises(CapitalGuardShadowReplaySnapshotError, match="rollback journal"):
        read_capital_guard_shadow_replay_snapshot(store.db_path)


def test_report_uses_decision_time_oos_boundary_and_is_always_fail_closed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    start = NOW + timedelta(days=1)
    in_window = _candidate_at(
        decision_key="replay-start",
        lifecycle_id="replay-start",
        venue_market_id="REPLAY-START",
        at=start,
    )
    at_end = _candidate_at(
        decision_key="replay-end",
        lifecycle_id="replay-end",
        venue_market_id="REPLAY-END",
        at=start + timedelta(days=1),
    )
    _append_candidate(store, in_window)
    _append_candidate(store, at_end)

    report = build_replay_report(
        read_capital_guard_shadow_replay_snapshot(store.db_path),
        oos_start=start,
        oos_end=start + timedelta(days=1),
    )

    assert report["coverage"]["oos_candidate_rows"] == 1
    assert report["coverage"]["sole_g7_candidate_rows"] == 1
    assert report["promotion"] == {
        "eligible": False,
        "failure_reasons": [
            "committed_settlement_economics_contract_missing",
            "settlement_correction_cashflow_contract_missing",
            "post_entry_executable_mark_contract_missing",
            "preregistered_counterfactual_baseline_manifest_missing",
            "multi_fill_fee_state_contract_missing",
        ],
    }


def test_report_keeps_other_gate_rows_as_diagnostics_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sole_g7 = _candidate_at(
        decision_key="replay-g7",
        lifecycle_id="replay-g7",
        venue_market_id="REPLAY-G7",
        at=NOW,
    )
    other_gate = _candidate_at(
        decision_key="replay-g3-g7",
        lifecycle_id="replay-g3-g7",
        venue_market_id="REPLAY-G3-G7",
        at=NOW + timedelta(seconds=1),
        failures=("G3_disagreement_score", "G7_open_exposure_drawdown"),
    )
    _append_candidate(store, sole_g7)
    _append_candidate(store, other_gate)

    report = build_replay_report(
        read_capital_guard_shadow_replay_snapshot(store.db_path),
        oos_start=NOW,
        oos_end=NOW + timedelta(days=1),
    )

    assert report["coverage"]["sole_g7_candidate_rows"] == 1
    assert report["coverage"]["diagnostic_nonsole_g7_rows"] == 1
    scopes = [row["scope"] for row in report["candidate_diagnostics"]]
    assert scopes == ["sole_g7_candidate", "nonsole_g7_diagnostic"]


def test_report_marks_financial_settlement_as_unverified_without_reading_amounts(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record = _candidate_at(
        decision_key="replay-settlement",
        lifecycle_id="replay-settlement",
        venue_market_id="REPLAY-SETTLEMENT",
        at=NOW,
    )
    candidate_id = _append_candidate(store, record).candidate_id
    head = observation(
        venue_market_id=record.venue_market_id,
        outcome="yes",
        observed_at=NOW + timedelta(minutes=1),
    )
    head_sha = store.append_observation(
        head, candidate_ids=(candidate_id,)
    ).observation_sha256
    _append_settlement(
        store,
        candidate_id=candidate_id,
        observation_sha256=head_sha,
        outcome="yes",
        settled_at=NOW + timedelta(minutes=2),
        gross_payout=record.executable_quantity,
    )

    report = build_replay_report(
        read_capital_guard_shadow_replay_snapshot(store.db_path),
        oos_start=NOW,
        oos_end=NOW + timedelta(days=1),
    )

    diagnostic = report["candidate_diagnostics"][0]
    assert diagnostic["current_head_status"] == (
        "terminal_head_with_unverified_financial_settlement"
    )
    assert diagnostic["financial_settlement_present"] is True
    serialized = json.dumps(report, sort_keys=True)
    assert "pnl" not in serialized.lower()
    assert "gross_payout" not in serialized
    assert "settlement_fee" not in serialized


def test_report_uses_current_head_diagnostic_after_correction(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate_at(
        decision_key="replay-current-head",
        lifecycle_id="replay-current-head",
        venue_market_id="REPLAY-CURRENT-HEAD",
        at=NOW,
    )
    candidate_id = _append_candidate(store, record).candidate_id
    first = observation(
        venue_market_id=record.venue_market_id,
        outcome="yes",
        observed_at=NOW + timedelta(minutes=1),
    )
    first_sha = store.append_observation(
        first, candidate_ids=(candidate_id,)
    ).observation_sha256
    corrected = observation(
        venue_market_id=record.venue_market_id,
        outcome="no",
        observed_at=NOW + timedelta(minutes=2),
        supersedes=first_sha,
    )
    corrected_sha = store.append_observation(
        corrected, candidate_ids=(candidate_id,)
    ).observation_sha256

    report = build_replay_report(
        read_capital_guard_shadow_replay_snapshot(store.db_path),
        oos_start=NOW,
        oos_end=NOW + timedelta(days=1),
    )

    diagnostic = report["candidate_diagnostics"][0]
    assert diagnostic["observation_sha256"] == corrected_sha
    assert diagnostic["current_head_status"] == "terminal_head_without_financial_settlement"
    assert diagnostic["financial_settlement_present"] is False


def test_output_path_rejects_ledger_sidecars_and_hardlink_aliases(tmp_path: Path) -> None:
    store = _store(tmp_path)
    protected = [
        store.db_path,
        store.db_path.with_name(store.db_path.name + "-wal"),
        store.db_path.with_name(store.db_path.name + "-shm"),
        store.db_path.with_name(store.db_path.name + "-journal"),
    ]
    for path in protected[1:]:
        path.write_bytes(b"sidecar")
    for path in protected:
        with pytest.raises(ValueError, match="shadow ledger or SQLite sidecar"):
            _safe_output_path(store.db_path, path)

    hardlink = tmp_path / "ledger-hardlink.json"
    os.link(store.db_path, hardlink)
    with pytest.raises(ValueError, match="alias"):
        _safe_output_path(store.db_path, hardlink)


def test_cli_writes_safe_report_without_mutating_source(tmp_path: Path) -> None:
    store = _store(tmp_path)
    output = tmp_path / "reports" / "replay.json"
    before = _sidecars(store.db_path)

    assert main(
        [
            "--db",
            str(store.db_path),
            "--oos-start",
            NOW.isoformat(),
            "--oos-end",
            (NOW + timedelta(days=1)).isoformat(),
            "--output",
            str(output),
        ]
    ) == 0

    assert _sidecars(store.db_path) == before
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["mode"] == "read_only_oos_prerequisite_report"
    assert report["promotion"]["eligible"] is False
