from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from scripts.edge_replay import fee_net_shadow_protocol as protocol_module
from trading.capital_guard_shadow import CapitalGuardShadowStore
import trading.capital_guard_shadow_v3 as v3_module
from trading.capital_guard_shadow_v3 import (
    CAPITAL_GUARD_SHADOW_V3_DDL_SHA256,
    CAPITAL_GUARD_SHADOW_V3_SCHEMA_VERSION,
    CapitalGuardShadowV3SchemaError,
    CapitalGuardShadowV3Store,
    capital_guard_shadow_v3_schema_contract_matches,
)


UTC = timezone.utc
NOW = datetime(2026, 7, 24, 16, 0, tzinfo=UTC)
TABLES = {
    "capital_guard_shadow_v3_schema_meta",
    "capital_guard_shadow_v3_protocol_bindings",
    "capital_guard_shadow_v3_candidates",
    "capital_guard_shadow_v3_llm_provenance",
    "capital_guard_shadow_v3_paper_executions",
    "capital_guard_shadow_v3_authoritative_settlements",
    "capital_guard_shadow_v3_fee_receipts",
    "capital_guard_shadow_v3_evaluations",
    "capital_guard_shadow_v3_evidence_records",
}
PRIMARY_KEYS = {
    "capital_guard_shadow_v3_schema_meta": "schema_version",
    "capital_guard_shadow_v3_protocol_bindings": "binding_id",
    "capital_guard_shadow_v3_candidates": "candidate_id",
    "capital_guard_shadow_v3_llm_provenance": "candidate_id",
    "capital_guard_shadow_v3_paper_executions": "execution_id",
    "capital_guard_shadow_v3_authoritative_settlements": "settlement_id",
    "capital_guard_shadow_v3_fee_receipts": "fee_receipt_id",
    "capital_guard_shadow_v3_evaluations": "evaluation_id",
    "capital_guard_shadow_v3_evidence_records": "record_id",
}


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _objects(path: Path) -> tuple[tuple[str, str, str], ...]:
    with sqlite3.connect(path) as conn:
        return tuple(
            (str(kind), str(name), str(sql))
            for kind, name, sql in conn.execute(
                "SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        )


def _foreign_key_shapes(
    conn: sqlite3.Connection,
    table: str,
) -> set[tuple[str, tuple[tuple[str, str], ...]]]:
    grouped: dict[int, list[tuple[int, str, str, str]]] = {}
    for key_id, sequence, target, source_column, target_column, *_rest in conn.execute(
        f"PRAGMA foreign_key_list({table})"
    ):
        grouped.setdefault(int(key_id), []).append((int(sequence), str(target), str(source_column), str(target_column)))
    return {
        (
            entries[0][1],
            tuple((source, target) for _sequence, _table, source, target in sorted(entries)),
        )
        for entries in grouped.values()
    }


def _payload() -> dict[str, object]:
    return json.loads(protocol_module.DEFAULT_PROTOCOL_PATH.read_text(encoding="utf-8"))


def _write_rehashed(path: Path, payload: dict[str, object]) -> None:
    payload["protocol_hash"] = protocol_module.canonical_protocol_hash(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _insert_immutable_rows(path: Path, *, include_llm: bool = True) -> None:
    sha = _sha
    timestamp = "2026-07-24T16:00:00.000000Z"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """
            INSERT INTO capital_guard_shadow_v3_candidates (
                candidate_id, binding_id, candidate_payload_sha256, decision_at_utc,
                venue, venue_market_id, market_family, selected_side,
                gate_outcomes_json, gate_payload_sha256, book_source,
                executable_book_json, book_payload_sha256, fee_provenance_json,
                fee_provenance_sha256, captured_at_utc
            ) VALUES (?, 1, ?, ?, 'kalshi', 'KXTEST-V3', 'KXTEST', 'no', ?, ?,
                      'rest_detail', ?, ?, ?, ?, ?)
            """,
            (
                "candidate-v3",
                sha("candidate"),
                timestamp,
                "{}",
                sha("gate"),
                "{}",
                sha("book"),
                "{}",
                sha("fee"),
                timestamp,
            ),
        )
        if include_llm:
            conn.execute(
                """
                INSERT INTO capital_guard_shadow_v3_llm_provenance (
                    candidate_id, llm_capture_id, llm_model_id, direction,
                    prompt_sha256, input_sha256, output_sha256, payload_json,
                    payload_sha256, captured_at_utc
                ) VALUES ('candidate-v3', 'capture-v3', 'test-model', 'no', ?, ?, ?, ?, ?, ?)
                """,
                (sha("prompt"), sha("input"), sha("output"), "{}", sha("llm"), timestamp),
            )
        conn.execute(
            """
            INSERT INTO capital_guard_shadow_v3_paper_executions (
                execution_id, candidate_id, paper_account_id_sha256, paper_order_id,
                paper_fill_id, execution_payload_json, execution_payload_sha256,
                executed_at_utc, venue_market_id, side, quantity,
                entry_price_dollars, gross_entry_debit_dollars
            ) VALUES ('execution-v3', 'candidate-v3', ?, 'order-v3', 'fill-v3', ?, ?, ?,
                      'KXTEST-V3', 'no', 1, '0.50', '0.50')
            """,
            (sha("account"), "{}", sha("execution"), timestamp),
        )
        conn.execute(
            """
            INSERT INTO capital_guard_shadow_v3_authoritative_settlements (
                settlement_id, candidate_id, source_id, rules_version,
                authoritative_observation_sha256, authoritative_payload_sha256,
                settlement_payload_json, settlement_payload_sha256,
                settlement_economics_contract_sha256, outcome, settled_at_utc,
                payout_per_contract_dollars
            ) VALUES ('settlement-v3', 'candidate-v3', 'kalshi-api', 'rules-v1', ?, ?, ?, ?, ?,
                      'no', ?, '1.00')
            """,
            (
                sha("observation"),
                sha("authoritative-payload"),
                "{}",
                sha("settlement-payload"),
                sha("economics-contract"),
                timestamp,
            ),
        )
        conn.execute(
            """
            INSERT INTO capital_guard_shadow_v3_fee_receipts (
                fee_receipt_id, candidate_id, settlement_id, execution_id, paper_account_id_sha256,
                receipt_payload_json, receipt_payload_sha256, fee_receipt_sha256,
                gross_payout_dollars, settlement_fee_dollars, settlement_refund_dollars,
                net_payout_dollars, received_at_utc
            ) VALUES ('receipt-v3', 'candidate-v3', 'settlement-v3', 'execution-v3', ?, ?, ?, ?,
                      '1.00', '0.00', '0.00', '1.00', ?)
            """,
            (sha("account"), "{}", sha("receipt-payload"), sha("receipt"), timestamp),
        )
        conn.execute(
            """
            INSERT INTO capital_guard_shadow_v3_evaluations (
                evaluation_id, candidate_id, execution_id, settlement_id, fee_receipt_id,
                evaluation_payload_json, evaluation_payload_sha256,
                gross_entry_debit_dollars, entry_fee_dollars, net_entry_debit_dollars,
                gross_payout_dollars, settlement_fee_dollars, settlement_refund_dollars,
                net_payout_dollars, gross_pnl_dollars, fee_net_pnl_dollars, evaluated_at_utc
            ) VALUES ('evaluation-v3', 'candidate-v3', 'execution-v3', 'settlement-v3', 'receipt-v3',
                      ?, ?, '0.50', '0.00', '0.50', '1.00', '0.00', '0.00', '1.00', '0.50', '0.50', ?)
            """,
            ("{}", sha("evaluation"), timestamp),
        )
        conn.execute(
            """
            INSERT INTO capital_guard_shadow_v3_evidence_records (
                record_id, record_hash, previous_record_hash, chain_position, binding_id,
                candidate_id, llm_capture_id, execution_id, settlement_id, fee_receipt_id, evaluation_id,
                evidence_json, recorded_at_utc
            ) VALUES ('record-v3', ?, NULL, 1, 1, 'candidate-v3', 'capture-v3', 'execution-v3',
                      'settlement-v3', 'receipt-v3', 'evaluation-v3', ?, ?)
            """,
            (sha("record"), "{}", timestamp),
        )
        conn.commit()


def test_v3_constructor_performs_zero_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "missing" / "capital_guard_shadow_v3.db"

    def unexpected_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("constructor connected to SQLite")

    monkeypatch.setattr(v3_module, "_SQLITE_CONNECT", unexpected_connect)
    store = CapitalGuardShadowV3Store(db_path)

    assert store.db_path == db_path
    assert store.create_if_missing is False
    assert not db_path.parent.exists()


def test_v3_missing_path_fails_closed_without_creating_files(tmp_path: Path) -> None:
    db_path = tmp_path / "missing" / "capital_guard_shadow_v3.db"
    store = CapitalGuardShadowV3Store(db_path)

    with pytest.raises(sqlite3.OperationalError, match="unable to open database"):
        store.initialize(applied_at=NOW)

    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_v3_initializes_exact_schema_locked_binding_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "capital_guard_shadow_v3.db"
    store = CapitalGuardShadowV3Store(db_path, create_if_missing=True)
    loaded = protocol_module.load_fee_net_shadow_protocol()

    store.initialize(applied_at=NOW)
    store.initialize(applied_at=NOW + timedelta(days=1))

    with sqlite3.connect(db_path) as conn:
        objects = {
            (str(kind), str(name))
            for kind, name in conn.execute(
                "SELECT type, name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        }
        meta = conn.execute(
            "SELECT schema_version, ddl_sha256, applied_at FROM capital_guard_shadow_v3_schema_meta"
        ).fetchone()
        binding = conn.execute(
            """
            SELECT protocol_id, protocol_hash, manifest_json, manifest_sha256,
                   approval_ref, cohort_start_utc, cohort_end_utc, bound_at_utc
            FROM capital_guard_shadow_v3_protocol_bindings
            """
        ).fetchone()
        assert capital_guard_shadow_v3_schema_contract_matches(conn) is True

    assert {name for kind, name in objects if kind == "table"} == TABLES
    assert len([name for kind, name in objects if kind == "trigger"]) == len(TABLES) * 3
    assert meta == (
        CAPITAL_GUARD_SHADOW_V3_SCHEMA_VERSION,
        CAPITAL_GUARD_SHADOW_V3_DDL_SHA256,
        "2026-07-24T16:00:00.000000Z",
    )
    assert binding is not None
    assert binding[0] == loaded.protocol_id
    assert binding[1] == loaded.protocol_hash
    assert binding[3] == hashlib.sha256(str(binding[2]).encode("utf-8")).hexdigest()
    assert binding[4:8] == (
        loaded.approval_ref,
        loaded.cohort_start_utc,
        loaded.cohort_end_utc,
        "2026-07-24T16:00:00.000000Z",
    )


def test_v3_store_connections_enable_integrity_pragmas(tmp_path: Path) -> None:
    store = CapitalGuardShadowV3Store(tmp_path / "capital_guard_shadow_v3.db", create_if_missing=True)
    store.initialize(applied_at=NOW)

    conn = store._connect()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert conn.execute("PRAGMA recursive_triggers").fetchone() == (1,)
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        conn.close()


def test_v3_rejects_partial_rogue_and_v2_databases_without_repair(tmp_path: Path) -> None:
    partial = tmp_path / "partial.db"
    with sqlite3.connect(partial) as conn:
        conn.execute("CREATE TABLE capital_guard_shadow_v3_candidates (candidate_id TEXT PRIMARY KEY)")
    partial_before = _objects(partial)
    with pytest.raises(CapitalGuardShadowV3SchemaError, match="schema drift"):
        CapitalGuardShadowV3Store(partial, create_if_missing=True).initialize(applied_at=NOW)
    assert _objects(partial) == partial_before

    valid = tmp_path / "valid.db"
    store = CapitalGuardShadowV3Store(valid, create_if_missing=True)
    store.initialize(applied_at=NOW)
    with sqlite3.connect(valid) as conn:
        conn.execute("CREATE TABLE rogue(value TEXT)")
        assert capital_guard_shadow_v3_schema_contract_matches(conn) is False
    valid_before = _objects(valid)
    with pytest.raises(CapitalGuardShadowV3SchemaError, match="schema drift"):
        store.initialize(applied_at=NOW)
    assert _objects(valid) == valid_before

    v2_path = tmp_path / "capital_guard_shadow_v2.db"
    CapitalGuardShadowStore(v2_path).initialize(applied_at=NOW)
    v2_before = _objects(v2_path)
    with pytest.raises(CapitalGuardShadowV3SchemaError, match="schema drift"):
        CapitalGuardShadowV3Store(v2_path, create_if_missing=True).initialize(applied_at=NOW)
    assert _objects(v2_path) == v2_before


def test_v3_refuses_existing_empty_or_symlinked_databases_without_repair(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty-v3.db"
    empty_path.touch()
    empty_before = empty_path.read_bytes()
    with pytest.raises(CapitalGuardShadowV3SchemaError, match="schema drift"):
        CapitalGuardShadowV3Store(empty_path, create_if_missing=True).initialize(applied_at=NOW)
    assert empty_path.read_bytes() == empty_before

    v2_path = tmp_path / "v2-target.db"
    CapitalGuardShadowStore(v2_path).initialize(applied_at=NOW)
    v2_before = _objects(v2_path)
    symlink_path = tmp_path / "v3-link.db"
    symlink_path.symlink_to(v2_path)
    with pytest.raises(CapitalGuardShadowV3SchemaError, match="symlink"):
        CapitalGuardShadowV3Store(symlink_path, create_if_missing=True).initialize(applied_at=NOW)
    assert _objects(v2_path) == v2_before

    target_dir = tmp_path / "target-dir"
    target_dir.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(target_dir, target_is_directory=True)
    with pytest.raises(CapitalGuardShadowV3SchemaError, match="symlink"):
        CapitalGuardShadowV3Store(
            linked_parent / "capital_guard_shadow_v3.db",
            create_if_missing=True,
        ).initialize(applied_at=NOW)
    assert not (target_dir / "capital_guard_shadow_v3.db").exists()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["acceptance"].update({"minimum_unique_market_ids": 31}),  # type: ignore[index,union-attr]
            "code-pinned",
        ),
        (
            lambda payload: payload["provenance"].update({"shadow_schema_version": 2}),  # type: ignore[index,union-attr]
            "requires v3",
        ),
    ],
)
def test_v3_rejects_unpinned_or_legacy_protocol_before_opening_database(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    payload = _payload()
    mutate(payload)  # type: ignore[operator]
    protocol_path = tmp_path / "protocol.json"
    _write_rehashed(protocol_path, payload)
    db_path = tmp_path / "missing" / "capital_guard_shadow_v3.db"

    with pytest.raises(protocol_module.FeeNetShadowProtocolError, match=message):
        CapitalGuardShadowV3Store(db_path, create_if_missing=True).initialize(
            protocol_path=protocol_path,
            applied_at=NOW,
        )

    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_v3_rejects_protocol_change_between_load_and_binding_before_opening_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol_path = tmp_path / "protocol.json"
    _write_rehashed(protocol_path, _payload())
    original_loader = v3_module.load_fee_net_shadow_protocol

    def load_then_swap(path: Path) -> protocol_module.FeeNetShadowProtocol:
        loaded = original_loader(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        acceptance = payload["acceptance"]
        assert isinstance(acceptance, dict)
        acceptance["minimum_unique_market_ids"] = 31
        _write_rehashed(path, payload)
        return loaded

    monkeypatch.setattr(v3_module, "load_fee_net_shadow_protocol", load_then_swap)
    db_path = tmp_path / "missing" / "capital_guard_shadow_v3.db"

    with pytest.raises(protocol_module.FeeNetShadowProtocolError, match="protocol"):
        CapitalGuardShadowV3Store(db_path, create_if_missing=True).initialize(
            protocol_path=protocol_path,
            applied_at=NOW,
        )

    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_v3_rejects_declared_hash_change_between_load_and_binding_before_opening_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol_path = tmp_path / "protocol.json"
    _write_rehashed(protocol_path, _payload())
    original_loader = v3_module.load_fee_net_shadow_protocol

    def load_then_change_declared_hash(path: Path) -> protocol_module.FeeNetShadowProtocol:
        loaded = original_loader(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["protocol_hash"] = "0" * 64
        path.write_text(json.dumps(payload), encoding="utf-8")
        return loaded

    monkeypatch.setattr(v3_module, "load_fee_net_shadow_protocol", load_then_change_declared_hash)
    db_path = tmp_path / "missing" / "capital_guard_shadow_v3.db"

    with pytest.raises(protocol_module.FeeNetShadowProtocolError, match="protocol"):
        CapitalGuardShadowV3Store(db_path, create_if_missing=True).initialize(
            protocol_path=protocol_path,
            applied_at=NOW,
        )

    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_v3_schema_matcher_rejects_a_self_consistent_unpinned_binding(tmp_path: Path) -> None:
    db_path = tmp_path / "unpinned-v3.db"
    payload = _payload()
    acceptance = payload["acceptance"]
    assert isinstance(acceptance, dict)
    acceptance["minimum_unique_market_ids"] = 31
    payload["protocol_hash"] = protocol_module.canonical_protocol_hash(payload)
    manifest_json = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )

    with sqlite3.connect(db_path) as conn:
        for _name, statement in v3_module.CAPITAL_GUARD_SHADOW_V3_TARGET_STATEMENTS:
            conn.execute(statement)
        conn.execute(
            """
            INSERT INTO capital_guard_shadow_v3_schema_meta (schema_version, ddl_sha256, applied_at)
            VALUES (?, ?, ?)
            """,
            (
                CAPITAL_GUARD_SHADOW_V3_SCHEMA_VERSION,
                CAPITAL_GUARD_SHADOW_V3_DDL_SHA256,
                "2026-07-24T16:00:00.000000Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO capital_guard_shadow_v3_protocol_bindings (
                binding_id, protocol_id, protocol_hash, manifest_json, manifest_sha256,
                approval_ref, cohort_start_utc, cohort_end_utc, bound_at_utc
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["protocol_id"],
                payload["protocol_hash"],
                manifest_json,
                _sha(manifest_json),
                payload["approval_ref"],
                payload["cohort_window"]["start_utc"],  # type: ignore[index,union-attr]
                payload["cohort_window"]["end_utc"],  # type: ignore[index,union-attr]
                "2026-07-24T16:00:00.000000Z",
            ),
        )
        assert capital_guard_shadow_v3_schema_contract_matches(conn) is False


def test_v3_schema_compositely_binds_economics_and_evidence_to_one_candidate(tmp_path: Path) -> None:
    db_path = tmp_path / "capital_guard_shadow_v3.db"
    CapitalGuardShadowV3Store(db_path, create_if_missing=True).initialize(applied_at=NOW)

    with sqlite3.connect(db_path) as conn:
        llm_foreign_keys = _foreign_key_shapes(conn, "capital_guard_shadow_v3_llm_provenance")
        receipt_foreign_keys = _foreign_key_shapes(conn, "capital_guard_shadow_v3_fee_receipts")
        evaluation_foreign_keys = _foreign_key_shapes(conn, "capital_guard_shadow_v3_evaluations")
        evidence_foreign_keys = _foreign_key_shapes(conn, "capital_guard_shadow_v3_evidence_records")

    assert (
        "capital_guard_shadow_v3_candidates",
        (("candidate_id", "candidate_id"), ("direction", "selected_side")),
    ) in llm_foreign_keys
    assert (
        "capital_guard_shadow_v3_paper_executions",
        (("candidate_id", "candidate_id"), ("execution_id", "execution_id")),
    ) in receipt_foreign_keys
    assert (
        "capital_guard_shadow_v3_authoritative_settlements",
        (("candidate_id", "candidate_id"), ("settlement_id", "settlement_id")),
    ) in receipt_foreign_keys
    for target, key in (
        ("capital_guard_shadow_v3_paper_executions", "execution_id"),
        ("capital_guard_shadow_v3_authoritative_settlements", "settlement_id"),
        ("capital_guard_shadow_v3_fee_receipts", "fee_receipt_id"),
    ):
        assert (target, (("candidate_id", "candidate_id"), (key, key))) in evaluation_foreign_keys
    assert (
        "capital_guard_shadow_v3_candidates",
        (("binding_id", "binding_id"), ("candidate_id", "candidate_id")),
    ) in evidence_foreign_keys
    assert (
        "capital_guard_shadow_v3_llm_provenance",
        (("candidate_id", "candidate_id"), ("llm_capture_id", "llm_capture_id")),
    ) in evidence_foreign_keys
    for target, key in (
        ("capital_guard_shadow_v3_paper_executions", "execution_id"),
        ("capital_guard_shadow_v3_authoritative_settlements", "settlement_id"),
        ("capital_guard_shadow_v3_fee_receipts", "fee_receipt_id"),
        ("capital_guard_shadow_v3_evaluations", "evaluation_id"),
    ):
        assert (target, (("candidate_id", "candidate_id"), (key, key))) in evidence_foreign_keys


def test_v3_rejects_llm_direction_that_differs_from_candidate(tmp_path: Path) -> None:
    db_path = tmp_path / "capital_guard_shadow_v3.db"
    CapitalGuardShadowV3Store(db_path, create_if_missing=True).initialize(applied_at=NOW)
    _insert_immutable_rows(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """
            INSERT INTO capital_guard_shadow_v3_candidates (
                candidate_id, binding_id, candidate_payload_sha256, decision_at_utc,
                venue, venue_market_id, market_family, selected_side,
                gate_outcomes_json, gate_payload_sha256, book_source,
                executable_book_json, book_payload_sha256, fee_provenance_json,
                fee_provenance_sha256, captured_at_utc
            )
            SELECT ?, binding_id, candidate_payload_sha256, decision_at_utc,
                   venue, ?, market_family, selected_side,
                   gate_outcomes_json, gate_payload_sha256, book_source,
                   executable_book_json, book_payload_sha256, fee_provenance_json,
                   fee_provenance_sha256, captured_at_utc
            FROM capital_guard_shadow_v3_candidates
            WHERE candidate_id = 'candidate-v3'
            """,
            ("candidate-direction-v3", "KXTEST-DIRECTION-V3"),
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO capital_guard_shadow_v3_llm_provenance (
                    candidate_id, llm_capture_id, llm_model_id, direction,
                    prompt_sha256, input_sha256, output_sha256, payload_json,
                    payload_sha256, captured_at_utc
                ) VALUES (?, 'capture-direction-v3', 'test-model', 'yes', ?, ?, ?, '{}', ?, ?)
                """,
                (
                    "candidate-direction-v3",
                    _sha("prompt-direction"),
                    _sha("input-direction"),
                    _sha("output-direction"),
                    _sha("llm-direction"),
                    "2026-07-24T16:00:00.000000Z",
                ),
            )


def test_v3_evidence_cannot_bypass_llm_provenance(tmp_path: Path) -> None:
    db_path = tmp_path / "capital_guard_shadow_v3.db"
    CapitalGuardShadowV3Store(db_path, create_if_missing=True).initialize(applied_at=NOW)

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        _insert_immutable_rows(db_path, include_llm=False)


def test_v3_schema_matcher_rejects_unverified_evidence_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "capital_guard_shadow_v3.db"
    store = CapitalGuardShadowV3Store(db_path, create_if_missing=True)
    store.initialize(applied_at=NOW)
    _insert_immutable_rows(db_path)

    with sqlite3.connect(db_path) as conn:
        assert capital_guard_shadow_v3_schema_contract_matches(conn) is False


def test_v3_schema_matcher_requires_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "capital_guard_shadow_v3.db"
    CapitalGuardShadowV3Store(db_path, create_if_missing=True).initialize(applied_at=NOW)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
        assert capital_guard_shadow_v3_schema_contract_matches(conn) is False


def test_v3_every_table_rejects_raw_updates_and_deletes(tmp_path: Path) -> None:
    db_path = tmp_path / "capital_guard_shadow_v3.db"
    store = CapitalGuardShadowV3Store(db_path, create_if_missing=True)
    store.initialize(applied_at=NOW)
    _insert_immutable_rows(db_path)

    with sqlite3.connect(db_path) as conn:
        for table, primary_key in PRIMARY_KEYS.items():
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(f"UPDATE {table} SET {primary_key} = {primary_key}")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(f"DELETE FROM {table}")

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO capital_guard_shadow_v3_protocol_bindings (
                    binding_id, protocol_id, protocol_hash, manifest_json, manifest_sha256,
                    approval_ref, cohort_start_utc, cohort_end_utc, bound_at_utc
                ) VALUES (2, 'other', ?, '{}', ?, 'other', ?, ?, ?)
                """,
                (
                    _sha("other"),
                    _sha("manifest"),
                    "2026-01-01T00:00:00.000000Z",
                    "2026-01-02T00:00:00.000000Z",
                    "2026-01-03T00:00:00.000000Z",
                ),
            )
