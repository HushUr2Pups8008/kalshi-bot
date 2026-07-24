"""Dormant v3 evidence-store schema for fee-net shadow validation.

This module deliberately exposes schema initialization only. It does not read or
write the v1/v2 shadow store, does not register runtime capture, and provides no
candidate, execution, settlement, or promotion writer.

No evidence row is accepted until an atomic append implementation verifies
receipt authenticity, fee-net economics, and hash-chain continuity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Final

from scripts.edge_replay.fee_net_shadow_protocol import (
    DEFAULT_PROTOCOL_PATH,
    FeeNetShadowProtocolError,
    SHIPPED_PROTOCOL_SHA256,
    canonical_protocol_hash,
    load_fee_net_shadow_protocol,
)


CAPITAL_GUARD_SHADOW_V3_SCHEMA_VERSION: Final = 3
CAPITAL_GUARD_SHADOW_V3_DB: Final = Path("data/capital_guard_shadow_v3.db")
_BUSY_TIMEOUT_MS: Final = 5_000
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_PROTOCOL_UTC_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SQLITE_CONNECT = sqlite3.connect


class CapitalGuardShadowV3SchemaError(RuntimeError):
    """The isolated v3 evidence schema is partial, drifted, or unbound."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _normalize_schema_sql(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().rstrip(";").lower().split())


def _immutable_triggers(
    unique_keys: Mapping[str, Sequence[Sequence[str]]],
) -> tuple[tuple[str, str], ...]:
    statements: list[tuple[str, str]] = []
    for table, table_unique_keys in unique_keys.items():
        for operation in ("update", "delete"):
            name = f"immutable_{table}_{operation}"
            statements.append(
                (
                    name,
                    f"""
                    CREATE TRIGGER {name}
                    BEFORE {operation.upper()} ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, '{table} is append-only');
                    END
                    """,
                )
            )
        conflict_predicates = " OR ".join(
            "(" + " AND ".join(f"{column} = NEW.{column}" for column in key) + ")" for key in table_unique_keys
        )
        name = f"immutable_{table}_insert"
        statements.append(
            (
                name,
                f"""
                CREATE TRIGGER {name}
                BEFORE INSERT ON {table}
                WHEN EXISTS (SELECT 1 FROM {table} WHERE {conflict_predicates})
                BEGIN
                    SELECT RAISE(ABORT, '{table} is append-only');
                END
                """,
            )
        )
    return tuple(statements)


_TABLE_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "capital_guard_shadow_v3_schema_meta",
        """
        CREATE TABLE capital_guard_shadow_v3_schema_meta (
            schema_version INTEGER PRIMARY KEY CHECK (schema_version = 3),
            ddl_sha256 TEXT NOT NULL CHECK (length(ddl_sha256) = 64),
            applied_at TEXT NOT NULL
        )
        """,
    ),
    (
        "capital_guard_shadow_v3_protocol_bindings",
        """
        CREATE TABLE capital_guard_shadow_v3_protocol_bindings (
            binding_id INTEGER PRIMARY KEY CHECK (binding_id = 1),
            protocol_id TEXT NOT NULL UNIQUE,
            protocol_hash TEXT NOT NULL CHECK (length(protocol_hash) = 64),
            manifest_json TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
            approval_ref TEXT NOT NULL,
            cohort_start_utc TEXT NOT NULL,
            cohort_end_utc TEXT NOT NULL,
            bound_at_utc TEXT NOT NULL
        )
        """,
    ),
    (
        "capital_guard_shadow_v3_candidates",
        """
        CREATE TABLE capital_guard_shadow_v3_candidates (
            candidate_id TEXT PRIMARY KEY,
            binding_id INTEGER NOT NULL
                REFERENCES capital_guard_shadow_v3_protocol_bindings(binding_id),
            candidate_payload_sha256 TEXT NOT NULL CHECK (length(candidate_payload_sha256) = 64),
            decision_at_utc TEXT NOT NULL,
            venue TEXT NOT NULL CHECK (venue = 'kalshi'),
            venue_market_id TEXT NOT NULL,
            market_family TEXT NOT NULL,
            selected_side TEXT NOT NULL CHECK (selected_side IN ('yes','no')),
            gate_outcomes_json TEXT NOT NULL,
            gate_payload_sha256 TEXT NOT NULL CHECK (length(gate_payload_sha256) = 64),
            book_source TEXT NOT NULL CHECK (book_source = 'rest_detail'),
            executable_book_json TEXT NOT NULL,
            book_payload_sha256 TEXT NOT NULL CHECK (length(book_payload_sha256) = 64),
            fee_provenance_json TEXT NOT NULL,
            fee_provenance_sha256 TEXT NOT NULL CHECK (length(fee_provenance_sha256) = 64),
            captured_at_utc TEXT NOT NULL,
            UNIQUE (binding_id, candidate_id),
            UNIQUE (candidate_id, selected_side),
            UNIQUE (candidate_id, venue_market_id, selected_side),
            UNIQUE (binding_id, venue, venue_market_id)
        )
        """,
    ),
    (
        "capital_guard_shadow_v3_llm_provenance",
        """
        CREATE TABLE capital_guard_shadow_v3_llm_provenance (
            candidate_id TEXT PRIMARY KEY
                REFERENCES capital_guard_shadow_v3_candidates(candidate_id),
            llm_capture_id TEXT NOT NULL UNIQUE,
            llm_model_id TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('yes','no')),
            prompt_sha256 TEXT NOT NULL CHECK (length(prompt_sha256) = 64),
            input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
            output_sha256 TEXT NOT NULL CHECK (length(output_sha256) = 64),
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
            captured_at_utc TEXT NOT NULL,
            UNIQUE (candidate_id, llm_capture_id),
            FOREIGN KEY (candidate_id, direction)
                REFERENCES capital_guard_shadow_v3_candidates(candidate_id, selected_side)
        )
        """,
    ),
    (
        "capital_guard_shadow_v3_paper_executions",
        """
        CREATE TABLE capital_guard_shadow_v3_paper_executions (
            execution_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL UNIQUE
                REFERENCES capital_guard_shadow_v3_candidates(candidate_id),
            paper_account_id_sha256 TEXT NOT NULL CHECK (length(paper_account_id_sha256) = 64),
            paper_order_id TEXT NOT NULL,
            paper_fill_id TEXT NOT NULL,
            execution_payload_json TEXT NOT NULL,
            execution_payload_sha256 TEXT NOT NULL CHECK (length(execution_payload_sha256) = 64),
            executed_at_utc TEXT NOT NULL,
            venue_market_id TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('yes','no')),
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            entry_price_dollars TEXT NOT NULL,
            gross_entry_debit_dollars TEXT NOT NULL,
            UNIQUE (candidate_id, execution_id),
            UNIQUE (candidate_id, execution_id, paper_account_id_sha256),
            UNIQUE (candidate_id, venue_market_id, side),
            FOREIGN KEY (candidate_id, venue_market_id, side)
                REFERENCES capital_guard_shadow_v3_candidates(candidate_id, venue_market_id, selected_side),
            UNIQUE (paper_account_id_sha256, paper_fill_id)
        )
        """,
    ),
    (
        "capital_guard_shadow_v3_authoritative_settlements",
        """
        CREATE TABLE capital_guard_shadow_v3_authoritative_settlements (
            settlement_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL UNIQUE
                REFERENCES capital_guard_shadow_v3_candidates(candidate_id),
            source_id TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            authoritative_observation_sha256 TEXT NOT NULL
                CHECK (length(authoritative_observation_sha256) = 64),
            authoritative_payload_sha256 TEXT NOT NULL
                CHECK (length(authoritative_payload_sha256) = 64),
            settlement_payload_json TEXT NOT NULL,
            settlement_payload_sha256 TEXT NOT NULL CHECK (length(settlement_payload_sha256) = 64),
            settlement_economics_contract_sha256 TEXT NOT NULL
                CHECK (length(settlement_economics_contract_sha256) = 64),
            outcome TEXT NOT NULL CHECK (outcome IN ('yes','no')),
            settled_at_utc TEXT NOT NULL,
            payout_per_contract_dollars TEXT NOT NULL,
            UNIQUE (candidate_id, settlement_id)
        )
        """,
    ),
    (
        "capital_guard_shadow_v3_fee_receipts",
        """
        CREATE TABLE capital_guard_shadow_v3_fee_receipts (
            fee_receipt_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            settlement_id TEXT NOT NULL UNIQUE,
            execution_id TEXT NOT NULL UNIQUE,
            paper_account_id_sha256 TEXT NOT NULL CHECK (length(paper_account_id_sha256) = 64),
            receipt_payload_json TEXT NOT NULL,
            receipt_payload_sha256 TEXT NOT NULL CHECK (length(receipt_payload_sha256) = 64),
            fee_receipt_sha256 TEXT NOT NULL CHECK (length(fee_receipt_sha256) = 64),
            gross_payout_dollars TEXT NOT NULL,
            settlement_fee_dollars TEXT NOT NULL,
            settlement_refund_dollars TEXT NOT NULL,
            net_payout_dollars TEXT NOT NULL,
            received_at_utc TEXT NOT NULL,
            UNIQUE (candidate_id, fee_receipt_id),
            FOREIGN KEY (candidate_id, settlement_id)
                REFERENCES capital_guard_shadow_v3_authoritative_settlements(candidate_id, settlement_id),
            FOREIGN KEY (candidate_id, execution_id)
                REFERENCES capital_guard_shadow_v3_paper_executions(candidate_id, execution_id),
            FOREIGN KEY (candidate_id, execution_id, paper_account_id_sha256)
                REFERENCES capital_guard_shadow_v3_paper_executions(
                    candidate_id, execution_id, paper_account_id_sha256
                )
        )
        """,
    ),
    (
        "capital_guard_shadow_v3_evaluations",
        """
        CREATE TABLE capital_guard_shadow_v3_evaluations (
            evaluation_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL UNIQUE,
            execution_id TEXT NOT NULL UNIQUE,
            settlement_id TEXT NOT NULL UNIQUE,
            fee_receipt_id TEXT NOT NULL UNIQUE,
            evaluation_payload_json TEXT NOT NULL,
            evaluation_payload_sha256 TEXT NOT NULL CHECK (length(evaluation_payload_sha256) = 64),
            gross_entry_debit_dollars TEXT NOT NULL,
            entry_fee_dollars TEXT NOT NULL,
            net_entry_debit_dollars TEXT NOT NULL,
            gross_payout_dollars TEXT NOT NULL,
            settlement_fee_dollars TEXT NOT NULL,
            settlement_refund_dollars TEXT NOT NULL,
            net_payout_dollars TEXT NOT NULL,
            gross_pnl_dollars TEXT NOT NULL,
            fee_net_pnl_dollars TEXT NOT NULL,
            evaluated_at_utc TEXT NOT NULL,
            UNIQUE (candidate_id, evaluation_id),
            FOREIGN KEY (candidate_id, execution_id)
                REFERENCES capital_guard_shadow_v3_paper_executions(candidate_id, execution_id),
            FOREIGN KEY (candidate_id, settlement_id)
                REFERENCES capital_guard_shadow_v3_authoritative_settlements(candidate_id, settlement_id),
            FOREIGN KEY (candidate_id, fee_receipt_id)
                REFERENCES capital_guard_shadow_v3_fee_receipts(candidate_id, fee_receipt_id)
        )
        """,
    ),
    (
        "capital_guard_shadow_v3_evidence_records",
        """
        CREATE TABLE capital_guard_shadow_v3_evidence_records (
            record_id TEXT PRIMARY KEY,
            record_hash TEXT NOT NULL UNIQUE CHECK (length(record_hash) = 64),
            previous_record_hash TEXT
                REFERENCES capital_guard_shadow_v3_evidence_records(record_hash),
            chain_position INTEGER NOT NULL UNIQUE CHECK (chain_position > 0),
            binding_id INTEGER NOT NULL,
            candidate_id TEXT NOT NULL UNIQUE,
            llm_capture_id TEXT NOT NULL,
            execution_id TEXT NOT NULL UNIQUE,
            settlement_id TEXT NOT NULL UNIQUE,
            fee_receipt_id TEXT NOT NULL UNIQUE,
            evaluation_id TEXT NOT NULL UNIQUE,
            evidence_json TEXT NOT NULL,
            recorded_at_utc TEXT NOT NULL,
            FOREIGN KEY (binding_id, candidate_id)
                REFERENCES capital_guard_shadow_v3_candidates(binding_id, candidate_id),
            FOREIGN KEY (candidate_id, llm_capture_id)
                REFERENCES capital_guard_shadow_v3_llm_provenance(candidate_id, llm_capture_id),
            FOREIGN KEY (candidate_id, execution_id)
                REFERENCES capital_guard_shadow_v3_paper_executions(candidate_id, execution_id),
            FOREIGN KEY (candidate_id, settlement_id)
                REFERENCES capital_guard_shadow_v3_authoritative_settlements(candidate_id, settlement_id),
            FOREIGN KEY (candidate_id, fee_receipt_id)
                REFERENCES capital_guard_shadow_v3_fee_receipts(candidate_id, fee_receipt_id),
            FOREIGN KEY (candidate_id, evaluation_id)
                REFERENCES capital_guard_shadow_v3_evaluations(candidate_id, evaluation_id)
        )
        """,
    ),
)

_INDEX_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "idx_capital_guard_shadow_v3_candidates_binding_decision",
        "CREATE INDEX idx_capital_guard_shadow_v3_candidates_binding_decision "
        "ON capital_guard_shadow_v3_candidates(binding_id, decision_at_utc)",
    ),
    (
        "idx_capital_guard_shadow_v3_settlements_candidate_settled",
        "CREATE INDEX idx_capital_guard_shadow_v3_settlements_candidate_settled "
        "ON capital_guard_shadow_v3_authoritative_settlements(candidate_id, settled_at_utc)",
    ),
    (
        "idx_capital_guard_shadow_v3_fee_receipts_settlement",
        "CREATE INDEX idx_capital_guard_shadow_v3_fee_receipts_settlement "
        "ON capital_guard_shadow_v3_fee_receipts(settlement_id, received_at_utc)",
    ),
    (
        "idx_capital_guard_shadow_v3_evidence_records_chain",
        "CREATE INDEX idx_capital_guard_shadow_v3_evidence_records_chain "
        "ON capital_guard_shadow_v3_evidence_records(chain_position, recorded_at_utc)",
    ),
    (
        "idx_capital_guard_shadow_v3_evidence_records_candidate",
        "CREATE INDEX idx_capital_guard_shadow_v3_evidence_records_candidate "
        "ON capital_guard_shadow_v3_evidence_records(candidate_id, record_hash)",
    ),
)

_TABLE_NAMES = tuple(name for name, _statement in _TABLE_STATEMENTS)
_EVIDENCE_TABLE_NAMES: Final = tuple(
    name
    for name in _TABLE_NAMES
    if name
    not in {
        "capital_guard_shadow_v3_schema_meta",
        "capital_guard_shadow_v3_protocol_bindings",
    }
)
_IMMUTABLE_UNIQUE_KEYS: Final = {
    "capital_guard_shadow_v3_schema_meta": (("schema_version",),),
    "capital_guard_shadow_v3_protocol_bindings": (("binding_id",), ("protocol_id",)),
    "capital_guard_shadow_v3_candidates": (
        ("candidate_id",),
        ("binding_id", "venue", "venue_market_id"),
    ),
    "capital_guard_shadow_v3_llm_provenance": (("candidate_id",), ("llm_capture_id",)),
    "capital_guard_shadow_v3_paper_executions": (
        ("execution_id",),
        ("candidate_id",),
        ("paper_account_id_sha256", "paper_fill_id"),
    ),
    "capital_guard_shadow_v3_authoritative_settlements": (("settlement_id",), ("candidate_id",)),
    "capital_guard_shadow_v3_fee_receipts": (
        ("fee_receipt_id",),
        ("settlement_id",),
        ("execution_id",),
    ),
    "capital_guard_shadow_v3_evaluations": (
        ("evaluation_id",),
        ("candidate_id",),
        ("execution_id",),
        ("settlement_id",),
        ("fee_receipt_id",),
    ),
    "capital_guard_shadow_v3_evidence_records": (
        ("record_id",),
        ("record_hash",),
        ("chain_position",),
        ("candidate_id",),
        ("execution_id",),
        ("settlement_id",),
        ("fee_receipt_id",),
        ("evaluation_id",),
    ),
}
_TRIGGER_STATEMENTS = _immutable_triggers(_IMMUTABLE_UNIQUE_KEYS)
CAPITAL_GUARD_SHADOW_V3_TARGET_STATEMENTS = _TABLE_STATEMENTS + _INDEX_STATEMENTS + _TRIGGER_STATEMENTS
_DDL_CONTRACT = "\n".join(
    f"-- {name}\n{statement.strip()};" for name, statement in CAPITAL_GUARD_SHADOW_V3_TARGET_STATEMENTS
)
CAPITAL_GUARD_SHADOW_V3_DDL_SHA256 = hashlib.sha256(_DDL_CONTRACT.encode("utf-8")).hexdigest()
_TARGET_OBJECT_TYPES = {
    **{name: "table" for name, _statement in _TABLE_STATEMENTS},
    **{name: "index" for name, _statement in _INDEX_STATEMENTS},
    **{name: "trigger" for name, _statement in _TRIGGER_STATEMENTS},
}
_TARGET_SQL = {name: _normalize_schema_sql(statement) for name, statement in CAPITAL_GUARD_SHADOW_V3_TARGET_STATEMENTS}


@dataclass(frozen=True)
class _ProtocolBinding:
    protocol_id: str
    protocol_hash: str
    manifest_json: str
    manifest_sha256: str
    approval_ref: str
    cohort_start_utc: str
    cohort_end_utc: str
    bound_at_utc: str


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("timestamp must be canonical UTC text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be canonical UTC text") from exc
    if _timestamp(parsed) != value:
        raise ValueError("timestamp must be canonical UTC text")
    return value


def _parse_protocol_utc(value: object) -> str:
    if not isinstance(value, str) or _PROTOCOL_UTC_RE.fullmatch(value) is None:
        raise ValueError("protocol timestamp must be canonical UTC text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("protocol timestamp must be canonical UTC text") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError("protocol timestamp must be canonical UTC text")
    return value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _user_schema_objects(conn: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (str(kind), str(name), str(sql))
        for kind, name, sql in conn.execute(
            "SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
    )


def _path_has_symlink_component(path: Path) -> bool:
    absolute_path = path.expanduser().absolute()
    return any(candidate.is_symlink() for candidate in (absolute_path, *absolute_path.parents))


def _require_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise CapitalGuardShadowV3SchemaError(f"invalid {field_name}")
    return value


def _require_sha256(value: object, *, field_name: str) -> str:
    text = _require_text(value, field_name=field_name)
    if _SHA256_RE.fullmatch(text) is None:
        raise CapitalGuardShadowV3SchemaError(f"invalid {field_name}")
    return text


def _load_protocol_binding(protocol_path: Path, *, bound_at: datetime) -> _ProtocolBinding:
    protocol = load_fee_net_shadow_protocol(protocol_path)
    try:
        manifest = json.loads(protocol_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeeNetShadowProtocolError(f"cannot reload fee-net shadow protocol: {protocol_path}") from exc
    if not isinstance(manifest, Mapping):
        raise FeeNetShadowProtocolError("fee-net shadow protocol must be an object")
    if canonical_protocol_hash(manifest) != protocol.protocol_hash or protocol.protocol_hash != SHIPPED_PROTOCOL_SHA256:
        raise FeeNetShadowProtocolError("fee-net shadow protocol changed after validation")
    window = manifest.get("cohort_window")
    if not isinstance(window, Mapping) or (
        manifest.get("protocol_id"),
        manifest.get("protocol_hash"),
        manifest.get("approval_ref"),
        window.get("start_utc"),
        window.get("end_utc"),
    ) != (
        protocol.protocol_id,
        protocol.protocol_hash,
        protocol.approval_ref,
        protocol.cohort_start_utc,
        protocol.cohort_end_utc,
    ):
        raise FeeNetShadowProtocolError("fee-net shadow protocol changed after validation")
    manifest_json = _canonical_json(manifest)
    return _ProtocolBinding(
        protocol_id=protocol.protocol_id,
        protocol_hash=protocol.protocol_hash,
        manifest_json=manifest_json,
        manifest_sha256=_sha256(manifest_json),
        approval_ref=protocol.approval_ref,
        cohort_start_utc=protocol.cohort_start_utc,
        cohort_end_utc=protocol.cohort_end_utc,
        bound_at_utc=_timestamp(bound_at),
    )


def _binding_from_row(row: tuple[object, ...]) -> _ProtocolBinding:
    if len(row) != 8:
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift")
    protocol_id = _require_text(row[0], field_name="protocol_id")
    protocol_hash = _require_sha256(row[1], field_name="protocol_hash")
    manifest_json = _require_text(row[2], field_name="manifest_json")
    manifest_sha256 = _require_sha256(row[3], field_name="manifest_sha256")
    approval_ref = _require_text(row[4], field_name="approval_ref")
    cohort_start_utc = _parse_protocol_utc(row[5])
    cohort_end_utc = _parse_protocol_utc(row[6])
    bound_at_utc = _parse_timestamp(row[7])
    if manifest_sha256 != _sha256(manifest_json):
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift")
    try:
        manifest = json.loads(manifest_json)
    except json.JSONDecodeError as exc:
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift") from exc
    if not isinstance(manifest, Mapping) or _canonical_json(manifest) != manifest_json:
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift")
    if canonical_protocol_hash(manifest) != protocol_hash or protocol_hash != SHIPPED_PROTOCOL_SHA256:
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 protocol binding drift")
    window = manifest.get("cohort_window")
    if not isinstance(window, Mapping) or (
        manifest.get("protocol_id"),
        manifest.get("protocol_hash"),
        manifest.get("approval_ref"),
        window.get("start_utc"),
        window.get("end_utc"),
    ) != (
        protocol_id,
        protocol_hash,
        approval_ref,
        cohort_start_utc,
        cohort_end_utc,
    ):
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift")
    if cohort_start_utc >= cohort_end_utc:
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift")
    return _ProtocolBinding(
        protocol_id=protocol_id,
        protocol_hash=protocol_hash,
        manifest_json=manifest_json,
        manifest_sha256=manifest_sha256,
        approval_ref=approval_ref,
        cohort_start_utc=cohort_start_utc,
        cohort_end_utc=cohort_end_utc,
        bound_at_utc=bound_at_utc,
    )


def _validate_protocol_binding(
    conn: sqlite3.Connection,
    *,
    expected_binding: _ProtocolBinding | None,
) -> None:
    rows = conn.execute(
        """
        SELECT protocol_id, protocol_hash, manifest_json, manifest_sha256,
               approval_ref, cohort_start_utc, cohort_end_utc, bound_at_utc
        FROM capital_guard_shadow_v3_protocol_bindings
        """
    ).fetchall()
    if len(rows) != 1:
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift")
    binding = _binding_from_row(tuple(rows[0]))
    if expected_binding is not None and (
        binding.protocol_id,
        binding.protocol_hash,
        binding.manifest_json,
        binding.manifest_sha256,
        binding.approval_ref,
        binding.cohort_start_utc,
        binding.cohort_end_utc,
    ) != (
        expected_binding.protocol_id,
        expected_binding.protocol_hash,
        expected_binding.manifest_json,
        expected_binding.manifest_sha256,
        expected_binding.approval_ref,
        expected_binding.cohort_start_utc,
        expected_binding.cohort_end_utc,
    ):
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 protocol binding drift")


def _validate_schema(
    conn: sqlite3.Connection,
    *,
    expected_binding: _ProtocolBinding | None = None,
    require_wal: bool = False,
) -> None:
    objects = _user_schema_objects(conn)
    actual_types = {name: kind for kind, name, _sql in objects}
    if actual_types != _TARGET_OBJECT_TYPES:
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift")
    for kind, name, sql in objects:
        if kind != _TARGET_OBJECT_TYPES[name] or _normalize_schema_sql(sql) != _TARGET_SQL[name]:
            raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift")
    meta = conn.execute(
        "SELECT schema_version, ddl_sha256, applied_at FROM capital_guard_shadow_v3_schema_meta"
    ).fetchall()
    if len(meta) != 1 or tuple(meta[0][0:2]) != (
        CAPITAL_GUARD_SHADOW_V3_SCHEMA_VERSION,
        CAPITAL_GUARD_SHADOW_V3_DDL_SHA256,
    ):
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift")
    try:
        _parse_timestamp(meta[0][2])
    except (TypeError, ValueError) as exc:
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift") from exc
    _validate_protocol_binding(conn, expected_binding=expected_binding)
    for table in _EVIDENCE_TABLE_NAMES:
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
            raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 evidence collection is not implemented")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 foreign-key drift")
    if require_wal and conn.execute("PRAGMA journal_mode").fetchone() != ("wal",):
        raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 WAL drift")


def capital_guard_shadow_v3_schema_contract_matches(conn: sqlite3.Connection) -> bool:
    """Return whether a connection exactly matches the dormant v3 schema contract."""
    try:
        _validate_schema(conn, require_wal=True)
    except (CapitalGuardShadowV3SchemaError, sqlite3.Error, TypeError, ValueError):
        return False
    return True


class CapitalGuardShadowV3Store:
    """Fresh v3 schema provisioning only; no runtime collection or evidence writes."""

    def __init__(
        self,
        db_path: Path = CAPITAL_GUARD_SHADOW_V3_DB,
        *,
        create_if_missing: bool = False,
    ) -> None:
        self.db_path = Path(db_path)
        self.create_if_missing = create_if_missing

    def initialize(
        self,
        *,
        protocol_path: Path = DEFAULT_PROTOCOL_PATH,
        applied_at: datetime | None = None,
    ) -> None:
        """Create or verify one exact, protocol-bound v3 schema.

        The locked, code-pinned protocol is loaded before any directory or SQLite
        operation. Existing databases are validated exactly and never repaired.
        """
        timestamp = applied_at or datetime.now(timezone.utc)
        binding = _load_protocol_binding(Path(protocol_path), bound_at=timestamp)
        if _path_has_symlink_component(self.db_path):
            raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 database path must not be a symlink")
        provision_new_path = not self.db_path.exists()
        if self.create_if_missing and provision_new_path:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = _user_schema_objects(conn)
            if not existing:
                if not self.create_if_missing or not provision_new_path:
                    raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 schema drift")
                for _name, statement in CAPITAL_GUARD_SHADOW_V3_TARGET_STATEMENTS:
                    conn.execute(statement)
                conn.execute(
                    """
                    INSERT INTO capital_guard_shadow_v3_schema_meta
                        (schema_version, ddl_sha256, applied_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        CAPITAL_GUARD_SHADOW_V3_SCHEMA_VERSION,
                        CAPITAL_GUARD_SHADOW_V3_DDL_SHA256,
                        binding.bound_at_utc,
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
                        binding.protocol_id,
                        binding.protocol_hash,
                        binding.manifest_json,
                        binding.manifest_sha256,
                        binding.approval_ref,
                        binding.cohort_start_utc,
                        binding.cohort_end_utc,
                        binding.bound_at_utc,
                    ),
                )
            _validate_schema(conn, expected_binding=binding)
            conn.commit()
            if conn.execute("PRAGMA journal_mode=WAL").fetchone() != ("wal",):
                raise CapitalGuardShadowV3SchemaError("capital guard shadow v3 WAL configuration failed")
            _validate_schema(conn, expected_binding=binding, require_wal=True)
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        connect_target: str | Path
        connect_kwargs: dict[str, object] = {
            "timeout": _BUSY_TIMEOUT_MS / 1_000,
            "isolation_level": None,
        }
        if self.create_if_missing:
            connect_target = self.db_path
        else:
            connect_target = f"{self.db_path.resolve(strict=False).as_uri()}?mode=rw"
            connect_kwargs["uri"] = True
        conn = _SQLITE_CONNECT(connect_target, **connect_kwargs)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA recursive_triggers=ON")
        except BaseException:
            conn.close()
            raise
        return conn
