from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import trading.capital_guard_shadow as shadow_module
from trading.capital_guard_shadow import (
    CAPITAL_GUARD_SHADOW_DDL_SHA256,
    CAPITAL_GUARD_SHADOW_SCHEMA_VERSION,
    CapitalGuardCandidate,
    CapitalGuardCaptureAttempt,
    CapitalGuardShadowSchemaError,
    CapitalGuardShadowStore,
    ShadowEvaluation,
    ShadowSettlement,
    SettlementObservationRecord,
    capital_guard_shadow_schema_contract_matches,
    canonical_json,
)
from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    KALSHI_GENERAL_2026_07_07,
    FeeContext,
    FeeRole,
    fee_coefficient_for,
    fee_type_for_schedule,
    quote_fee,
    serialize_fee_schedule,
)
from trading.settlement import (
    MarketOutcome,
    VoidRefundContract,
    build_settlement_observation,
)
from trading.venue import MarketRef, Venue


UTC = timezone.utc
NOW = datetime(2026, 7, 15, 12, 30, tzinfo=UTC)
D = Decimal
TABLES = {
    "capital_guard_shadow_schema_meta",
    "capital_guard_shadow_capture_attempts",
    "capital_guard_shadow_candidates",
    "capital_guard_shadow_conflicts",
    "capital_guard_shadow_observations",
    "capital_guard_shadow_candidate_observations",
    "capital_guard_shadow_settlement_attempts",
    "capital_guard_shadow_settlement_quarantines",
    "capital_guard_shadow_settlements",
    "capital_guard_shadow_evaluations",
}


def _json(value: object) -> str:
    return canonical_json(value)


def _dtext(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value, "f").rstrip("0").rstrip(".")


def candidate(
    *,
    decision_key: str = "decision-1",
    lifecycle_id: str = "lifecycle-1",
    venue_market_id: str = "KXTEST-26JUL15-T50",
    side: str = "yes",
    failures: tuple[str, ...] = ("G7_open_exposure_drawdown",),
    blocker: str | None = None,
    price: Decimal = D("0.42"),
) -> CapitalGuardCandidate:
    failed_gates = {
        failure.split("_", 1)[0]
        for failure in failures
    }
    gate_inputs = {
        "schema_version": 1,
        "gates": {
            "G1": {
                "blended_confidence": "0.8",
                "regime_confidence": "0.8",
                "scaled_confidence": "0.64",
                "threshold": "0.35",
            },
            "G2": {
                "evidence_source_classes": ["official"],
                "minimum_source_classes": 1,
                "source_lane": "accumulation",
            },
            "G3": {
                "default_min_edge": "0.05",
                "disagreement_score": "0.1",
                "override_band_start": "0.15",
                "override_multiplier": "1.5",
                "threshold": "0.2",
            },
            "G4": {"regime_confidence": "0.8", "threshold": "0.2"},
            "G5": {
                "drift_suspect": False,
                "in_recovery": False,
                "source_lane": "accumulation",
            },
            "G6": {
                "recency_score": "0.5",
                "recency_threshold": "0.3",
                "settlement_source_relevant": True,
                "source_lane": "accumulation",
                "time_to_close_seconds": "3600",
            },
            "G7": {
                "intended_side": side,
                "market_liquidity_dollars": "100",
                "market_price_momentum_cents": "0",
                "max_open_exposure_drawdown_pct": "0.2",
                "minimum_market_liquidity_dollars": "0.01",
                "open_exposure_drawdown_pct": "0.3",
            },
        },
    }
    if "G3_disagreement_score" in failures:
        gate_inputs["gates"]["G3"]["disagreement_score"] = "0.3"
    gate_results = {
        "schema_version": 1,
        "gates": {
            gate: {
                "applied": True,
                "failure_reasons": [
                    failure
                    for failure in failures
                    if failure == gate or failure.startswith(f"{gate}_")
                ],
                "passed": gate not in failed_gates,
            }
            for gate in (f"G{i}" for i in range(1, 8))
        },
    }
    fee_schedule_json = serialize_fee_schedule(KALSHI_GENERAL_2026_07_07)
    fee_provenance_json = _json(
        {
            "account_precision_dollars": "0.0001",
            "accumulator_dollars": "0",
            "coefficient": _dtext(
                fee_coefficient_for(KALSHI_GENERAL_2026_07_07, FeeRole.TAKER)
            ),
            "effective_at": KALSHI_GENERAL_2026_07_07.effective_from.isoformat(),
            "fee_multiplier": "1",
            "fee_role": "taker",
            "fee_schedule": json.loads(fee_schedule_json),
            "fee_type": fee_type_for_schedule(KALSHI_GENERAL_2026_07_07),
            "schema_version": 1,
            "source_payload_sha256": "c" * 64,
            "venue": "kalshi",
        }
    )
    return CapitalGuardCandidate(
        decision_key=decision_key,
        lifecycle_id=lifecycle_id,
        decision_at=NOW,
        captured_at=NOW + timedelta(seconds=1),
        venue=Venue.KALSHI,
        venue_market_id=venue_market_id,
        market_family="weather",
        side=side,
        ordered_failures=failures,
        non_gate_blocker=blocker,
        gate_inputs_json=_json(gate_inputs),
        gate_results_json=_json(gate_results),
        identity_json=_json(
            {
                "alias": venue_market_id,
                "contract_fingerprint": "contract-v1",
                "decision_key": decision_key,
                "lifecycle_id": lifecycle_id,
                "rules_fingerprint": "rules-v1",
                "schema_version": 1,
                "settlement_fingerprint": "settlement-v1",
                "venue": "kalshi",
                "venue_market_id": venue_market_id,
            }
        ),
        executable_book_json=_json(
            {
                "asks": [
                    {"price_dollars": _dtext(price), "quantity": "7"},
                    {"price_dollars": _dtext(price + D("0.01")), "quantity": "10"},
                ],
                "bids": [{"price_dollars": _dtext(price - D("0.02")), "quantity": "8"}],
                "schema_version": 1,
                "side": side,
            }
        ),
        book_observed_at=NOW - timedelta(milliseconds=100),
        book_source="kalshi-orderbook-v2",
        book_method="fixed-point-depth-complement-v1",
        book_payload_sha256="a" * 64,
        expected_probability=D("0.55"),
        executable_price=price,
        executable_quantity=D("5"),
        gross_edge=D("0.55") - price,
        sizing_json=_json(
            {
                "bankroll_dollars": "8.76",
                "capital_at_risk_dollars": _dtext(price * D("5")),
                "capped_dollars": _dtext(price * D("5")),
                "kelly_dollars": "3",
                "kelly_fraction": "0.3424657534246575342465753425",
                "max_position_dollars": "5",
                "max_ticker_exposure_dollars": "5",
                "quantity_method": "floor_to_step",
                "quantity_step": "1",
                "requested_quantity": "5",
                "schema_version": 1,
            }
        ),
        fill_policy_json=_json(
            {
                "allow_partial": True,
                "book_payload_sha256": "a" * 64,
                "entry_request_id": f"request-{lifecycle_id}",
                "order_id": f"shadow-order-{lifecycle_id}",
                "order_type": "limit",
                "policy_id": "full-depth-v1",
                "price_limit_dollars": _dtext(price),
                "quantity": "5",
                "schema_version": 1,
                "source_code_sha256": "b" * 64,
                "time_in_force": "immediate_or_cancel",
            }
        ),
        fee_schedule_json=fee_schedule_json,
        fee_formula_type=fee_type_for_schedule(KALSHI_GENERAL_2026_07_07),
        fee_role=FeeRole.TAKER,
        fee_multiplier=D("1"),
        fee_coefficient=fee_coefficient_for(
            KALSHI_GENERAL_2026_07_07, FeeRole.TAKER
        ),
        fee_account_precision=D("0.0001"),
        fee_accumulator=D("0"),
        fee_provenance_json=fee_provenance_json,
        fee_provenance_sha256=hashlib.sha256(
            fee_provenance_json.encode("utf-8")
        ).hexdigest(),
    )


def capture_attempt(
    record: CapitalGuardCandidate,
    *,
    scorable: bool = True,
    unscorable_reasons: tuple[str, ...] = (),
    requested_stake: Decimal | None = None,
    partial_artifacts_json: str | None = None,
) -> CapitalGuardCaptureAttempt:
    return CapitalGuardCaptureAttempt(
        decision_key=record.decision_key,
        lifecycle_id=record.lifecycle_id,
        decision_at=record.decision_at,
        captured_at=record.captured_at,
        venue=record.venue,
        venue_market_id=record.venue_market_id,
        market_family=record.market_family,
        side=record.side,
        ordered_failures=record.ordered_failures,
        non_gate_blocker=record.non_gate_blocker,
        target_gate="G7",
        target_failure="G7_open_exposure_drawdown",
        scorable=scorable,
        ordered_unscorable_reasons=unscorable_reasons,
        requested_stake=requested_stake,
        partial_artifacts_json=partial_artifacts_json,
    )


def _append_candidate(
    store: CapitalGuardShadowStore,
    record: CapitalGuardCandidate,
):
    store.append_capture_attempt(capture_attempt(record))
    return store.append_candidate(record)


def observation(
    *,
    outcome: str = "yes",
    venue_market_id: str = "KXTEST-26JUL15-T50",
    observed_at: datetime = NOW + timedelta(days=1),
    supersedes: str | None = None,
) -> SettlementObservationRecord:
    effective_at = observed_at - timedelta(minutes=1)
    payload = {"market_id": venue_market_id, "result": outcome}
    void_refund = (
        VoidRefundContract(D("100"), True) if outcome == "void" else None
    )
    authoritative = build_settlement_observation(
        market_ref=MarketRef(Venue.KALSHI, venue_market_id, venue_market_id),
        outcome=MarketOutcome(outcome),
        authoritative_outcome=outcome,
        authoritative_payload=payload,
        observed_at=observed_at,
        effective_at=effective_at,
        rules_version="kalshi-settlement-v1",
        source_id="kalshi-settlement-api-v1",
        void_refund=void_refund,
    )
    void_refund_json, void_refund_sha256 = shadow_module._void_refund_payload(
        void_refund
    )
    semantic_sha256 = shadow_module._source_settlement_semantic_sha256(
        authoritative,
        contract_fingerprint="contract-v1",
        rules_fingerprint="rules-v1",
        settlement_fingerprint="settlement-v1",
    )
    return SettlementObservationRecord(
        venue=Venue.KALSHI,
        venue_market_id=venue_market_id,
        alias=venue_market_id,
        contract_fingerprint="contract-v1",
        rules_fingerprint="rules-v1",
        settlement_fingerprint="settlement-v1",
        outcome=outcome,
        observed_at=observed_at,
        effective_at=effective_at,
        source_id="kalshi-settlement-api-v1",
        rules_version="kalshi-settlement-v1",
        authoritative_outcome_json=authoritative.authoritative_outcome_json,
        source_payload_json=authoritative.canonical_payload_json,
        authoritative_payload_sha256=authoritative.payload_sha256,
        authoritative_observation_sha256=authoritative.observation_sha256,
        semantic_sha256=semantic_sha256,
        void_refund_json=void_refund_json,
        void_refund_sha256=void_refund_sha256,
        supersedes_observation_sha256=supersedes,
    )


def _rows(path: Path, sql: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as conn:
        return [tuple(row) for row in conn.execute(sql)]


def _entry_accounting(record: CapitalGuardCandidate) -> tuple[Decimal, Decimal, Decimal]:
    schedule = KALSHI_GENERAL_2026_07_07
    gross_debit = record.executable_quantity * record.executable_price
    order_id = str(json.loads(record.fill_policy_json)["order_id"])
    quote = quote_fee(
        FeeContext(
            schedule_id=schedule,
            role=record.fee_role,
            quantity=record.executable_quantity,
            price=record.executable_price,
            signed_revenue=-gross_debit,
            order_id=order_id,
            accumulator=record.fee_accumulator,
            multiplier=record.fee_multiplier,
            coefficient=record.fee_coefficient,
            account_precision=DIRECT_ACCOUNT_PRECISION,
            timestamp=record.decision_at,
        )
    )
    return gross_debit, quote.net_fee, gross_debit + quote.net_fee


def _settled_evaluation(
    record: CapitalGuardCandidate,
    candidate_id: str,
    settlement_id: str,
    *,
    gross_payout: Decimal = D("5"),
    settlement_fee: Decimal = D("0"),
    settlement_refund: Decimal = D("0"),
) -> ShadowEvaluation:
    gross_debit, entry_fee, net_debit = _entry_accounting(record)
    net_payout = gross_payout - settlement_fee + settlement_refund
    gross_pnl = gross_payout + settlement_refund - gross_debit
    fee_net_pnl = net_payout - net_debit
    bankroll_before = D("8.76")
    bankroll_after = bankroll_before + fee_net_pnl
    return ShadowEvaluation(
        candidate_id=candidate_id,
        settlement_id=settlement_id,
        evaluated_at=NOW + timedelta(days=1, minutes=3),
        evaluation_kind="chronological-fee-net-v1",
        status="settled",
        entry_fee=entry_fee,
        gross_pnl=gross_pnl,
        settlement_fee=settlement_fee,
        settlement_refund=settlement_refund,
        fee_net_pnl=fee_net_pnl,
        bankroll_before=bankroll_before,
        bankroll_after=bankroll_after,
        open_exposure_before=net_debit,
        open_exposure_after=D("0"),
        high_water_mark=max(bankroll_before, bankroll_after),
        drawdown_after=max(D("0"), max(bankroll_before, bankroll_after) - bankroll_after),
        worst_case_loss=net_debit,
        details_json=_json({"replay_version": 1}),
    )


def test_constructor_performs_zero_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "missing" / "capital_guard_shadow.db"

    def unexpected_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("constructor connected to SQLite")

    monkeypatch.setattr(sqlite3, "connect", unexpected_connect)
    store = CapitalGuardShadowStore(db_path=db_path)

    assert store.db_path == db_path
    assert not db_path.parent.exists()


def test_existing_only_collector_mode_never_creates_missing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "path with spaces and # fragment" / "shadow?.db"
    store = CapitalGuardShadowStore(db_path=db_path, existing_only=True)

    with pytest.raises(sqlite3.OperationalError, match="unable to open database"):
        store.initialize(applied_at=NOW)

    assert not db_path.exists()
    assert not db_path.parent.exists()

    creator = CapitalGuardShadowStore(db_path=db_path)
    creator.initialize(applied_at=NOW)
    store.initialize(applied_at=NOW + timedelta(days=1))

    assert db_path.exists()


def test_initialize_is_idempotent_and_applies_exact_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "capital_guard_shadow.db"
    store = CapitalGuardShadowStore(db_path=db_path)

    store.initialize(applied_at=NOW)
    store.initialize(applied_at=NOW + timedelta(days=1))

    with sqlite3.connect(db_path) as conn:
        objects = {
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT type, name FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        }
        meta = conn.execute(
            "SELECT schema_version, ddl_sha256, applied_at "
            "FROM capital_guard_shadow_schema_meta"
        ).fetchone()
        foreign_keys = conn.execute(
            "PRAGMA foreign_key_list(capital_guard_shadow_candidate_observations)"
        ).fetchall()
        candidate_foreign_keys = conn.execute(
            "PRAGMA foreign_key_list(capital_guard_shadow_candidates)"
        ).fetchall()

    assert {name for kind, name in objects if kind == "table"} == TABLES
    assert len([1 for kind, _ in objects if kind == "trigger"]) == len(TABLES) * 2
    assert meta == (
        CAPITAL_GUARD_SHADOW_SCHEMA_VERSION,
        CAPITAL_GUARD_SHADOW_DDL_SHA256,
        "2026-07-15T12:30:00.000000Z",
    )
    assert {row[2] for row in foreign_keys} == {
        "capital_guard_shadow_candidates",
        "capital_guard_shadow_observations",
    }
    assert {row[2] for row in candidate_foreign_keys} == {
        "capital_guard_shadow_capture_attempts"
    }


def test_initialize_rejects_partial_or_drifted_schema_without_repair(tmp_path: Path) -> None:
    partial = tmp_path / "partial.db"
    with sqlite3.connect(partial) as conn:
        conn.execute(
            "CREATE TABLE capital_guard_shadow_candidates (candidate_id TEXT PRIMARY KEY)"
        )

    with pytest.raises(CapitalGuardShadowSchemaError, match="schema drift"):
        CapitalGuardShadowStore(partial).initialize(applied_at=NOW)

    assert _rows(partial, "SELECT name FROM sqlite_schema WHERE type='table'") == [
        ("capital_guard_shadow_candidates",)
    ]

    valid = tmp_path / "valid.db"
    store = CapitalGuardShadowStore(valid)
    store.initialize(applied_at=NOW)
    with sqlite3.connect(valid) as conn:
        conn.execute("CREATE TABLE rogue(value TEXT)")
    with pytest.raises(CapitalGuardShadowSchemaError, match="schema drift"):
        store.initialize(applied_at=NOW)


def test_connection_pragmas_foreign_keys_and_append_only_triggers(tmp_path: Path) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    result = _append_candidate(store, candidate())

    conn = store._connect()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert 0 < conn.execute("PRAGMA busy_timeout").fetchone()[0] < 60_000
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE capital_guard_shadow_candidates SET market_family='tampered'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM capital_guard_shadow_candidates WHERE candidate_id=?",
                (result.candidate_id,),
            )
    finally:
        conn.close()


def test_capture_attempt_retains_unscorable_denominator_evidence(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    record = candidate()
    partial_artifacts = _json(
        {
            "artifacts": {
                "executable_book": {
                    "available": False,
                    "payload_sha256": None,
                },
                "gate_inputs": {
                    "available": True,
                    "payload_sha256": "c" * 64,
                },
            },
            "schema_version": 1,
        }
    )
    attempt = capture_attempt(
        record,
        scorable=False,
        unscorable_reasons=(
            "missing_executable_book",
            "fee_schedule_unavailable",
        ),
        requested_stake=D("2.10"),
        partial_artifacts_json=partial_artifacts,
    )

    first = store.append_capture_attempt(attempt)
    retry = store.append_capture_attempt(attempt)

    assert (first.status, retry.status) == ("inserted", "identical")
    assert first.capture_attempt_id == retry.capture_attempt_id
    assert _rows(
        store.db_path,
        "SELECT claim_identity_json, gate_identity_json, scorable, "
        "ordered_failures_json, non_gate_blocker, "
        "ordered_unscorable_reasons_json, "
        "requested_stake_dollars, partial_artifacts_json "
        "FROM capital_guard_shadow_capture_attempts",
    ) == [
        (
            _json(
                {
                    "decision_at": "2026-07-15T12:30:00.000000Z",
                    "lifecycle_id": "lifecycle-1",
                    "schema_version": 1,
                    "side": "yes",
                    "venue": "kalshi",
                    "venue_market_id": "KXTEST-26JUL15-T50",
                }
            ),
            _json(
                {
                    "failure_reason": "G7_open_exposure_drawdown",
                    "gate": "G7",
                    "non_gate_blocker": None,
                    "ordered_failures": ["G7_open_exposure_drawdown"],
                    "schema_version": 1,
                }
            ),
            0,
            _json(["G7_open_exposure_drawdown"]),
            None,
            _json(["missing_executable_book", "fee_schedule_unavailable"]),
            "2.1",
            partial_artifacts,
        )
    ]
    with pytest.raises(ValueError, match="scorable capture attempt"):
        store.append_candidate(record)
    assert _rows(store.db_path, "SELECT * FROM capital_guard_shadow_candidates") == []


def test_candidate_requires_one_matching_scorable_capture_attempt(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    record = candidate()

    with pytest.raises(ValueError, match="capture attempt"):
        store.append_candidate(record)

    attempt = store.append_capture_attempt(
        capture_attempt(record, requested_stake=D("2.10"))
    )
    inserted = store.append_candidate(record)
    retry = store.append_candidate(record)

    assert (inserted.status, retry.status) == ("inserted", "identical")
    assert _rows(
        store.db_path,
        "SELECT capture_attempt_id FROM capital_guard_shadow_candidates",
    ) == [(attempt.capture_attempt_id,)]

    mismatch = candidate(lifecycle_id="stake-mismatch")
    store.append_capture_attempt(
        capture_attempt(mismatch, requested_stake=D("2.11"))
    )
    with pytest.raises(ValueError, match="requested stake"):
        store.append_candidate(mismatch)


def test_candidate_retry_is_idempotent_and_nonidentical_retry_is_conflict(tmp_path: Path) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)

    first = _append_candidate(store, candidate())
    retry = _append_candidate(store, candidate())
    changed = _append_candidate(store, candidate(price=D("0.43")))
    changed_retry = _append_candidate(store, candidate(price=D("0.43")))

    assert (first.status, retry.status) == ("inserted", "identical")
    assert first.candidate_id == retry.candidate_id
    assert changed.status == changed_retry.status == "conflict"
    assert changed.conflict_id == changed_retry.conflict_id
    assert len(_rows(store.db_path, "SELECT * FROM capital_guard_shadow_candidates")) == 1
    assert len(_rows(store.db_path, "SELECT * FROM capital_guard_shadow_conflicts")) == 1


def test_replay_eligibility_is_computed_from_exact_gate_state(tmp_path: Path) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)

    eligible = _append_candidate(store, candidate(decision_key="eligible"))
    other_gate = _append_candidate(
        store,
        candidate(
            decision_key="other",
            lifecycle_id="lifecycle-other",
            failures=("G3_disagreement_score", "G7_open_exposure_drawdown"),
        )
    )
    blocker = _append_candidate(
        store,
        candidate(
            decision_key="blocked",
            lifecycle_id="lifecycle-blocked",
            blocker="missing_fee_provenance",
        )
    )

    rows = dict(
        _rows(
            store.db_path,
            "SELECT candidate_id, replay_eligible FROM capital_guard_shadow_candidates",
        )
    )
    assert rows == {
        eligible.candidate_id: 1,
        other_gate.candidate_id: 0,
        blocker.candidate_id: 0,
    }


def test_fast_lane_empty_g2_and_optional_g7_market_inputs_remain_scorable(
    tmp_path: Path,
) -> None:
    base = candidate()
    gate_inputs = json.loads(base.gate_inputs_json)
    gate_results = json.loads(base.gate_results_json)
    gate_inputs["gates"]["G2"]["evidence_source_classes"] = []
    for gate in ("G2", "G5", "G6"):
        gate_inputs["gates"][gate]["source_lane"] = "fast"
        gate_results["gates"][gate]["applied"] = False
    gate_inputs["gates"]["G7"]["market_liquidity_dollars"] = None
    gate_inputs["gates"]["G7"]["market_price_momentum_cents"] = None
    record = replace(
        base,
        gate_inputs_json=_json(gate_inputs),
        gate_results_json=_json(gate_results),
    )

    assert record.replay_eligible is True
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    result = _append_candidate(store, record)
    assert _rows(
        store.db_path,
        "SELECT candidate_id, replay_eligible FROM capital_guard_shadow_candidates",
    ) == [(result.candidate_id, 1)]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("decision_at", datetime(2026, 7, 15, 12, 30), "timezone-aware"),
        ("gate_inputs_json", '{"G2":{},"G1":{}}', "canonical JSON"),
        ("executable_price", D("NaN"), "finite Decimal"),
        ("book_payload_sha256", "not-a-hash", "SHA-256"),
        ("fee_provenance_sha256", "not-a-hash", "SHA-256"),
    ],
)
def test_candidate_contract_rejects_unscorable_values(
    field: str, value: object, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        replace(candidate(), **{field: value})


def test_capture_attempt_requires_target_failure_and_typed_blocker() -> None:
    record = candidate()
    attempt = capture_attempt(
        record,
        scorable=False,
        unscorable_reasons=("missing",),
    )

    with pytest.raises(ValueError, match="target failure"):
        replace(attempt, ordered_failures=("G3_disagreement_score",))
    with pytest.raises(ValueError, match="non_gate_blocker"):
        replace(attempt, non_gate_blocker="G7_open_exposure_drawdown")


def test_fee_provenance_hash_binds_persisted_canonical_artifact() -> None:
    record = candidate()
    provenance = json.loads(record.fee_provenance_json)
    provenance["source_payload_sha256"] = "d" * 64
    changed_json = _json(provenance)

    with pytest.raises(ValueError, match="does not bind"):
        replace(record, fee_provenance_json=changed_json)

    provenance["venue"] = "polymarket_us"
    wrong_venue_json = _json(provenance)
    wrong_venue_hash = hashlib.sha256(wrong_venue_json.encode("utf-8")).hexdigest()
    with pytest.raises(ValueError, match="venue does not match"):
        replace(
            record,
            fee_provenance_json=wrong_venue_json,
            fee_provenance_sha256=wrong_venue_hash,
        )


def test_observation_corrections_append_and_link_by_hash(tmp_path: Path) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    candidate_result = _append_candidate(store, candidate())

    first = store.append_observation(observation(), (candidate_result.candidate_id,))
    retry = store.append_observation(observation(), (candidate_result.candidate_id,))
    correction_record = observation(
        outcome="no",
        observed_at=NOW + timedelta(days=1, minutes=5),
        supersedes=first.observation_sha256,
    )
    correction = store.append_observation(
        correction_record, (candidate_result.candidate_id,)
    )

    assert (first.status, retry.status, correction.status) == (
        "inserted",
        "identical",
        "inserted",
    )
    assert first.observation_sha256 != correction.observation_sha256
    assert len(_rows(store.db_path, "SELECT * FROM capital_guard_shadow_observations")) == 2
    links = _rows(
        store.db_path,
        "SELECT candidate_id, observation_sha256 "
        "FROM capital_guard_shadow_candidate_observations ORDER BY observation_sha256",
    )
    assert links == sorted(
        [
            (candidate_result.candidate_id, first.observation_sha256),
            (candidate_result.candidate_id, correction.observation_sha256),
        ],
        key=lambda row: row[1],
    )


def test_observation_rejects_cross_market_links_and_invalid_correction(tmp_path: Path) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    candidate_result = _append_candidate(store, candidate())

    wrong_market = observation(venue_market_id="OTHER")
    with pytest.raises(ValueError, match="market identity"):
        store.append_observation(wrong_market, (candidate_result.candidate_id,))
    with pytest.raises(ValueError, match="superseded observation"):
        store.append_observation(
            replace(observation(), supersedes_observation_sha256="f" * 64),
            (candidate_result.candidate_id,),
        )
    assert _rows(store.db_path, "SELECT * FROM capital_guard_shadow_observations") == []


def test_state_dependent_money_lives_only_in_settlement_and_evaluation(tmp_path: Path) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    candidate_record = candidate()
    candidate_result = _append_candidate(store, candidate_record)
    observed = store.append_observation(
        observation(), (candidate_result.candidate_id,)
    )
    settlement = ShadowSettlement(
        candidate_id=candidate_result.candidate_id,
        observation_sha256=observed.observation_sha256,
        outcome="yes",
        settled_at=NOW + timedelta(days=1, minutes=2),
        gross_payout=D("5"),
        settlement_fee=D("0"),
        settlement_refund=D("0"),
        net_payout=D("5"),
        details_json=_json({"settlement_version": 1}),
    )
    settlement_result = store.append_settlement(settlement)
    evaluation = _settled_evaluation(
        candidate_record,
        candidate_result.candidate_id,
        settlement_result.settlement_id,
    )
    evaluation_result = store.append_evaluation(evaluation)

    assert settlement_result.status == evaluation_result.status == "inserted"
    with sqlite3.connect(store.db_path) as conn:
        candidate_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(capital_guard_shadow_candidates)")
        }
        settlement_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(capital_guard_shadow_settlements)")
        }
        evaluation_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(capital_guard_shadow_evaluations)")
        }
    assert not {
        "outcome",
        "fee_net_pnl_dollars",
        "gross_pnl_dollars",
        "settlement_fee_dollars",
    } & candidate_columns
    assert {
        "book_method",
        "fee_provenance_json",
        "fee_provenance_sha256",
    } <= candidate_columns
    assert {"outcome", "settlement_fee_dollars"} <= settlement_columns
    assert {"entry_fee_dollars", "fee_net_pnl_dollars"} <= evaluation_columns


def test_transaction_fault_rolls_back_and_closes_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    real_connect = shadow_module._SQLITE_CONNECT
    connections: list[sqlite3.Connection] = []

    def tracked_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(shadow_module, "_SQLITE_CONNECT", tracked_connect)

    def fail_after_insert(conn: sqlite3.Connection, record: CapitalGuardCandidate) -> None:
        store._insert_candidate(conn, record)
        raise RuntimeError("injected transaction fault")

    monkeypatch.setattr(store, "_append_candidate_transaction", fail_after_insert)
    store.append_capture_attempt(capture_attempt(candidate()))
    with pytest.raises(RuntimeError, match="injected transaction fault"):
        store.append_candidate(candidate())

    assert _rows(store.db_path, "SELECT * FROM capital_guard_shadow_candidates") == []
    assert connections
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connections[-1].execute("SELECT 1")


def test_concurrent_writers_preserve_single_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    first_store = CapitalGuardShadowStore(db_path)
    second_store = CapitalGuardShadowStore(db_path)
    first_store.initialize(applied_at=NOW)

    async def write(store: CapitalGuardShadowStore, record: CapitalGuardCandidate):
        return await asyncio.to_thread(_append_candidate, store, record)

    async def run_writers():
        return await asyncio.gather(
            write(first_store, candidate()),
            write(second_store, candidate(price=D("0.43"))),
        )

    results = asyncio.run(run_writers())

    assert sorted(result.status for result in results) == ["conflict", "inserted"]
    assert len(_rows(db_path, "SELECT * FROM capital_guard_shadow_capture_attempts")) == 1
    assert len(_rows(db_path, "SELECT * FROM capital_guard_shadow_candidates")) == 1
    assert len(_rows(db_path, "SELECT * FROM capital_guard_shadow_conflicts")) == 1


def test_concurrent_distinct_observation_roots_leave_one_quarantined_lineage(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "shadow.db"
    first_store = CapitalGuardShadowStore(db_path)
    second_store = CapitalGuardShadowStore(db_path)
    first_store.initialize(applied_at=NOW)
    candidate_result = _append_candidate(first_store, candidate())
    roots = (
        observation(),
        observation(
            outcome="no",
            observed_at=NOW + timedelta(days=1, minutes=1),
        ),
    )

    async def write(
        store: CapitalGuardShadowStore,
        record: SettlementObservationRecord,
    ):
        return await asyncio.to_thread(
            store.append_observation,
            record,
            (candidate_result.candidate_id,),
        )

    async def run_writers():
        return await asyncio.gather(
            write(first_store, roots[0]),
            write(second_store, roots[1]),
        )

    results = asyncio.run(run_writers())

    assert sorted(result.status for result in results) == ["conflict", "inserted"]
    assert len(_rows(db_path, "SELECT * FROM capital_guard_shadow_observations")) == 1
    assert len(
        _rows(db_path, "SELECT * FROM capital_guard_shadow_candidate_observations")
    ) == 1
    assert _rows(
        db_path,
        "SELECT entity_type FROM capital_guard_shadow_conflicts",
    ) == [("observation_root",)]


def test_canonical_database_hash_is_order_and_retry_independent(tmp_path: Path) -> None:
    first = CapitalGuardShadowStore(tmp_path / "first.db")
    second = CapitalGuardShadowStore(tmp_path / "second.db")
    first.initialize(applied_at=NOW)
    second.initialize(applied_at=NOW + timedelta(days=2))

    for record in (
        candidate(decision_key="a", lifecycle_id="a"),
        candidate(decision_key="b", lifecycle_id="b"),
    ):
        _append_candidate(first, record)
        _append_candidate(first, record)
    for record in (
        candidate(decision_key="b", lifecycle_id="b"),
        candidate(decision_key="a", lifecycle_id="a"),
    ):
        _append_candidate(second, record)

    assert first.canonical_database_sha256() == second.canonical_database_sha256()


@pytest.mark.parametrize(
    "artifact",
    ["gate_inputs", "gate_results", "book", "sizing", "fill", "identity"],
)
def test_incomplete_versioned_candidate_artifacts_are_rejected(
    artifact: str,
) -> None:
    record = candidate()
    field = {
        "gate_inputs": "gate_inputs_json",
        "gate_results": "gate_results_json",
        "book": "executable_book_json",
        "sizing": "sizing_json",
        "fill": "fill_policy_json",
        "identity": "identity_json",
    }[artifact]
    payload = json.loads(getattr(record, field))
    if artifact in {"gate_inputs", "gate_results"}:
        del payload["gates"]["G7"]
    elif artifact == "book":
        payload["asks"] = []
    elif artifact == "sizing":
        del payload["max_ticker_exposure_dollars"]
    elif artifact == "fill":
        del payload["source_code_sha256"]
    else:
        del payload["settlement_fingerprint"]

    with pytest.raises(ValueError, match="complete|schema|depth|provenance|identity"):
        replace(record, **{field: _json(payload)})


def test_canonical_identity_claim_does_not_trust_upstream_decision_key(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)

    first = _append_candidate(store, candidate(decision_key="upstream-a"))
    same_claim = _append_candidate(store, candidate(decision_key="upstream-b"))
    other_market = _append_candidate(
        store,
        candidate(decision_key="upstream-a", venue_market_id="KXTEST-OTHER")
    )
    other_lifecycle = _append_candidate(
        store,
        candidate(decision_key="upstream-a", lifecycle_id="lifecycle-2")
    )

    assert first.status == "inserted"
    assert same_claim.status == "conflict"
    assert same_claim.candidate_id == first.candidate_id
    assert other_market.status == other_lifecycle.status == "inserted"
    assert len({first.candidate_id, other_market.candidate_id, other_lifecycle.candidate_id}) == 3


@pytest.mark.parametrize(
    ("outcome", "gross_payout", "refund", "error"),
    [
        ("yes", D("0"), D("0"), "winning side"),
        ("no", D("5"), D("0"), "losing side"),
        ("void", D("1"), D("0"), "void payout"),
        ("void", D("0"), D("3"), "immutable entry debit"),
        ("yes", D("5"), D("1"), "combine positive payout"),
    ],
)
def test_settlement_rejects_impossible_side_payout_and_refund_states(
    tmp_path: Path,
    outcome: str,
    gross_payout: Decimal,
    refund: Decimal,
    error: str,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    result = _append_candidate(store, candidate())
    observed = store.append_observation(
        observation(outcome=outcome), (result.candidate_id,)
    )
    settlement = ShadowSettlement(
        candidate_id=result.candidate_id,
        observation_sha256=observed.observation_sha256,
        outcome=outcome,
        settled_at=NOW + timedelta(days=1, minutes=2),
        gross_payout=gross_payout,
        settlement_fee=D("0"),
        settlement_refund=refund,
        net_payout=gross_payout + refund,
        details_json=_json({"settlement_version": 1}),
    )

    with pytest.raises(ValueError, match=error):
        store.append_settlement(settlement)

    assert _rows(store.db_path, "SELECT * FROM capital_guard_shadow_settlements") == []


@pytest.mark.parametrize(
    "mutation",
    [
        "entry_fee",
        "gross_pnl",
        "settlement_fee",
        "settlement_refund",
        "fee_net_pnl",
        "bankroll_after",
        "open_exposure_after",
        "high_water_mark",
        "drawdown_after",
        "worst_case_loss",
    ],
)
def test_evaluation_rejects_invented_money_and_state_transitions(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    candidate_record = candidate()
    candidate_result = _append_candidate(store, candidate_record)
    observed = store.append_observation(
        observation(), (candidate_result.candidate_id,)
    )
    settlement_result = store.append_settlement(
        ShadowSettlement(
            candidate_id=candidate_result.candidate_id,
            observation_sha256=observed.observation_sha256,
            outcome="yes",
            settled_at=NOW + timedelta(days=1, minutes=2),
            gross_payout=D("5"),
            settlement_fee=D("0"),
            settlement_refund=D("0"),
            net_payout=D("5"),
            details_json=_json({"settlement_version": 1}),
        )
    )
    evaluation = _settled_evaluation(
        candidate_record,
        candidate_result.candidate_id,
        settlement_result.settlement_id,
    )
    changed = getattr(evaluation, mutation) + D("0.01")
    invented = replace(evaluation, **{mutation: changed})

    with pytest.raises(ValueError, match="reconcile|match|conservation|drawdown|risk"):
        store.append_evaluation(invented)

    assert _rows(store.db_path, "SELECT * FROM capital_guard_shadow_evaluations") == []


@pytest.mark.parametrize("status", ["open", "excluded"])
def test_non_settled_evaluations_are_rejected_until_replay_owns_transitions(
    status: str,
) -> None:
    with pytest.raises(ValueError, match="settled evaluations"):
        ShadowEvaluation(
            candidate_id="a" * 64,
            settlement_id=None,
            evaluated_at=NOW,
            evaluation_kind="chronological-fee-net-v1",
            status=status,
            entry_fee=None,
            gross_pnl=None,
            settlement_fee=None,
            settlement_refund=None,
            fee_net_pnl=None,
            bankroll_before=D("8.76"),
            bankroll_after=D("8.76"),
            open_exposure_before=D("0"),
            open_exposure_after=D("0"),
            high_water_mark=D("8.76"),
            drawdown_after=D("0"),
            worst_case_loss=D("0"),
            details_json=_json({"replay_version": 1}),
        )


def test_distinct_second_observation_root_quarantines_market_and_never_links(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    candidate_result = _append_candidate(store, candidate())
    initial = store.append_observation(
        observation(), (candidate_result.candidate_id,)
    )
    second_root = store.append_observation(
        observation(
            outcome="no",
            observed_at=NOW + timedelta(days=1, minutes=5),
        ),
        (candidate_result.candidate_id,),
    )

    assert second_root.status == "conflict"
    assert second_root.conflict_id is not None
    assert len(_rows(store.db_path, "SELECT * FROM capital_guard_shadow_observations")) == 1
    assert _rows(
        store.db_path,
        "SELECT observation_sha256 FROM capital_guard_shadow_candidate_observations",
    ) == [(initial.observation_sha256,)]
    assert _rows(
        store.db_path,
        "SELECT entity_type FROM capital_guard_shadow_conflicts",
    ) == [("observation_root",)]

    with pytest.raises(ValueError, match="ambiguous observation root"):
        store.append_settlement(
            ShadowSettlement(
                candidate_id=candidate_result.candidate_id,
                observation_sha256=initial.observation_sha256,
                outcome="yes",
                settled_at=NOW + timedelta(days=1, minutes=6),
                gross_payout=D("5"),
                settlement_fee=D("0"),
                settlement_refund=D("0"),
                net_payout=D("5"),
                details_json=_json({"settlement_version": 1}),
            )
        )


def test_observation_ambiguity_after_settlement_poisons_evaluation(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    record = candidate()
    candidate_result = _append_candidate(store, record)
    initial = store.append_observation(
        observation(), (candidate_result.candidate_id,)
    )
    settlement = store.append_settlement(
        ShadowSettlement(
            candidate_id=candidate_result.candidate_id,
            observation_sha256=initial.observation_sha256,
            outcome="yes",
            settled_at=NOW + timedelta(days=1, minutes=2),
            gross_payout=D("5"),
            settlement_fee=D("0"),
            settlement_refund=D("0"),
            net_payout=D("5"),
            details_json=_json({"settlement_version": 1}),
        )
    )
    conflict = store.append_observation(
        observation(
            outcome="no",
            observed_at=NOW + timedelta(days=1, minutes=5),
        ),
        (candidate_result.candidate_id,),
    )
    assert conflict.status == "conflict"

    with pytest.raises(ValueError, match="ambiguous observation root"):
        store.append_evaluation(
            _settled_evaluation(
                record,
                candidate_result.candidate_id,
                settlement.settlement_id,
            )
        )
    assert _rows(store.db_path, "SELECT * FROM capital_guard_shadow_evaluations") == []


def test_correction_chain_has_one_successor_and_settles_only_unambiguous_head(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    candidate_result = _append_candidate(store, candidate())
    initial = store.append_observation(
        observation(), (candidate_result.candidate_id,)
    )
    correction = store.append_observation(
        observation(
            outcome="no",
            observed_at=NOW + timedelta(days=1, minutes=5),
            supersedes=initial.observation_sha256,
        ),
        (candidate_result.candidate_id,),
    )

    stale = ShadowSettlement(
        candidate_id=candidate_result.candidate_id,
        observation_sha256=initial.observation_sha256,
        outcome="yes",
        settled_at=NOW + timedelta(days=1, minutes=6),
        gross_payout=D("5"),
        settlement_fee=D("0"),
        settlement_refund=D("0"),
        net_payout=D("5"),
        details_json=_json({"settlement_version": 1}),
    )
    with pytest.raises(ValueError, match="current observation head"):
        store.append_settlement(stale)

    branch = store.append_observation(
        observation(
            outcome="void",
            observed_at=NOW + timedelta(days=1, minutes=7),
            supersedes=initial.observation_sha256,
        ),
        (candidate_result.candidate_id,),
    )
    assert branch.status == "conflict"
    assert branch.conflict_id is not None
    assert len(_rows(store.db_path, "SELECT * FROM capital_guard_shadow_observations")) == 2

    ambiguous_head = ShadowSettlement(
        candidate_id=candidate_result.candidate_id,
        observation_sha256=correction.observation_sha256,
        outcome="no",
        settled_at=NOW + timedelta(days=1, minutes=8),
        gross_payout=D("0"),
        settlement_fee=D("0"),
        settlement_refund=D("0"),
        net_payout=D("0"),
        details_json=_json({"settlement_version": 1}),
    )
    with pytest.raises(ValueError, match="ambiguous correction chain"):
        store.append_settlement(ambiguous_head)


def test_public_schema_contract_matcher_is_exact_and_read_only(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    store = CapitalGuardShadowStore(db_path)
    store.initialize(applied_at=NOW)

    with sqlite3.connect(db_path) as conn:
        assert capital_guard_shadow_schema_contract_matches(conn) is True
        before = conn.total_changes
        assert capital_guard_shadow_schema_contract_matches(conn) is True
        assert conn.total_changes == before
        conn.execute("CREATE VIEW rogue_shadow_view AS SELECT 1 AS value")
        assert capital_guard_shadow_schema_contract_matches(conn) is False
