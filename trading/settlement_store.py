"""Unwired durable settlement schema and storage primitives."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

_SQLITE_CONNECT = sqlite3.connect

SETTLEMENT_SCHEMA_VERSION = 1
SETTLEMENT_EVENT_VERSION = 1

SETTLEMENT_PAPER_TRADE_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "terminal_state",
        "TEXT CHECK (terminal_state IS NULL OR terminal_state IN ('won','lost','void'))",
    ),
    (
        "settlement_observation_sha256",
        "TEXT REFERENCES paper_settlement_observations(observation_sha256)",
    ),
    ("settled_at", "TEXT"),
    ("gross_payout_cents", "TEXT"),
    ("gross_pnl_cents", "TEXT"),
)

SETTLEMENT_PAPER_TRADE_COLUMNS_SQL = "".join(
    f"    {name} {definition},\n"
    for name, definition in SETTLEMENT_PAPER_TRADE_COLUMNS
)

_TABLE_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "paper_settlement_schema_meta",
        """
        CREATE TABLE paper_settlement_schema_meta (
            schema_version INTEGER PRIMARY KEY,
            ddl_sha256 TEXT NOT NULL UNIQUE,
            migration_plan_sha256 TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """,
    ),
    (
        "paper_settlement_observations",
        """
        CREATE TABLE paper_settlement_observations (
            observation_sha256 TEXT PRIMARY KEY,
            venue TEXT NOT NULL,
            venue_market_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('yes','no','void')),
            authoritative_outcome_json TEXT NOT NULL,
            canonical_payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            effective_at TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            source_id TEXT NOT NULL,
            refund_cents_per_contract TEXT,
            refunds_entry_fee INTEGER CHECK (
                refunds_entry_fee IS NULL OR refunds_entry_fee IN (0, 1)
            ),
            supersedes_observation_sha256 TEXT REFERENCES
                paper_settlement_observations(observation_sha256),
            applied_trade_count INTEGER NOT NULL CHECK (applied_trade_count > 0),
            bankroll_before_cents TEXT NOT NULL,
            gross_payout_cents TEXT NOT NULL,
            bankroll_after_cents TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            UNIQUE (venue, venue_market_id, observation_sha256)
        )
        """,
    ),
    (
        "paper_settlement_quarantine",
        """
        CREATE TABLE paper_settlement_quarantine (
            quarantine_id TEXT PRIMARY KEY,
            observation_sha256 TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            venue TEXT NOT NULL,
            venue_market_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            details_json TEXT NOT NULL,
            open_row_set_sha256 TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            UNIQUE (observation_sha256, reason_code, open_row_set_sha256)
        )
        """,
    ),
    (
        "paper_settlement_outbox",
        """
        CREATE TABLE paper_settlement_outbox (
            outbox_id TEXT PRIMARY KEY,
            event_version INTEGER NOT NULL CHECK (event_version = 1),
            event_kind TEXT NOT NULL,
            observation_sha256 TEXT NOT NULL REFERENCES
                paper_settlement_observations(observation_sha256),
            trade_id TEXT NOT NULL REFERENCES paper_trades(trade_id),
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
    ),
    (
        "paper_settlement_outbox_requirements",
        """
        CREATE TABLE paper_settlement_outbox_requirements (
            outbox_id TEXT NOT NULL REFERENCES paper_settlement_outbox(outbox_id),
            consumer_name TEXT NOT NULL,
            PRIMARY KEY (outbox_id, consumer_name)
        )
        """,
    ),
    (
        "paper_settlement_consumer_receipts",
        """
        CREATE TABLE paper_settlement_consumer_receipts (
            consumer_name TEXT NOT NULL,
            outbox_id TEXT NOT NULL,
            processed_at TEXT NOT NULL,
            result_sha256 TEXT NOT NULL,
            PRIMARY KEY (consumer_name, outbox_id),
            FOREIGN KEY (outbox_id, consumer_name) REFERENCES
                paper_settlement_outbox_requirements(outbox_id, consumer_name)
        )
        """,
    ),
    (
        "paper_settlement_delivery_claims",
        """
        CREATE TABLE paper_settlement_delivery_claims (
            consumer_name TEXT NOT NULL,
            outbox_id TEXT NOT NULL,
            claim_token TEXT NOT NULL,
            lease_expires_at TEXT NOT NULL,
            attempt_count INTEGER NOT NULL CHECK (attempt_count > 0),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (consumer_name, outbox_id),
            FOREIGN KEY (outbox_id, consumer_name) REFERENCES
                paper_settlement_outbox_requirements(outbox_id, consumer_name)
        )
        """,
    ),
)

_INDEX_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "paper_settlement_receipts_outbox_idx",
        "CREATE INDEX paper_settlement_receipts_outbox_idx "
        "ON paper_settlement_consumer_receipts(outbox_id, consumer_name)",
    ),
    (
        "paper_settlement_claims_lease_idx",
        "CREATE INDEX paper_settlement_claims_lease_idx "
        "ON paper_settlement_delivery_claims(lease_expires_at, outbox_id)",
    ),
    (
        "paper_trades_settlement_observation_idx",
        "CREATE INDEX paper_trades_settlement_observation_idx "
        "ON paper_trades(settlement_observation_sha256)",
    ),
)

_APPEND_ONLY_TABLES = (
    "paper_settlement_observations",
    "paper_settlement_quarantine",
    "paper_settlement_outbox",
    "paper_settlement_outbox_requirements",
    "paper_settlement_consumer_receipts",
)


def _immutable_trigger_statements() -> tuple[tuple[str, str], ...]:
    statements: list[tuple[str, str]] = []
    for table in _APPEND_ONLY_TABLES:
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
    return tuple(statements)


SETTLEMENT_TARGET_STATEMENTS = (
    _TABLE_STATEMENTS + _INDEX_STATEMENTS + _immutable_trigger_statements()
)
SETTLEMENT_MIGRATION_STATEMENTS = tuple(
    (
        name,
        f"ALTER TABLE paper_trades ADD COLUMN {name} {definition}",
    )
    for name, definition in SETTLEMENT_PAPER_TRADE_COLUMNS
) + SETTLEMENT_TARGET_STATEMENTS

_DDL_CONTRACT = "\n".join(
    f"-- {name}\n{sql.strip()};" for name, sql in SETTLEMENT_MIGRATION_STATEMENTS
)
SETTLEMENT_DDL_SHA256 = hashlib.sha256(_DDL_CONTRACT.encode("utf-8")).hexdigest()
FRESH_SCHEMA_PLAN_SHA256 = hashlib.sha256(
    b"paper-settlement-schema:fresh-database:v1"
).hexdigest()

_DECIMAL_TEXT = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?")
_SHA256_TEXT = re.compile(r"[0-9a-f]{64}")

_TARGET_OBJECT_TYPES = {
    **{name: "table" for name, _statement in _TABLE_STATEMENTS},
    **{name: "index" for name, _statement in _INDEX_STATEMENTS},
    **{name: "trigger" for name, _statement in _immutable_trigger_statements()},
}
_TERMINAL_STATE_CHECK_SQL = (
    "check (terminal_state is null or "
    "terminal_state in ('won','lost','void'))"
)


@dataclass(frozen=True)
class PendingRequirement:
    outbox_id: str
    consumer_name: str
    event_version: int
    event_kind: str
    payload_json: str
    created_at: str


@dataclass(frozen=True)
class StoreCheck:
    ok: bool
    failures: tuple[str, ...]
    metrics: dict[str, int | str | bool]


def enable_and_verify_foreign_keys(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    if row is None or int(row[0]) != 1:
        raise RuntimeError("SQLite foreign key enforcement is unavailable")


def settlement_schema_contract_signature(conn: sqlite3.Connection) -> str:
    """Return the normalized structural signature of the target schema."""
    payload = _settlement_schema_contract_payload(conn)
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def settlement_schema_contract_matches(conn: sqlite3.Connection) -> bool:
    """Verify target SQL, columns, keys, indexes, and foreign keys exactly."""
    try:
        return settlement_schema_contract_signature(
            conn
        ) == _expected_settlement_schema_contract_signature()
    except sqlite3.DatabaseError:
        return False


def canonical_entry_schema_ready(conn: sqlite3.Connection) -> bool:
    """Require the settlement contract plus the canonical identity columns."""
    if not settlement_schema_contract_matches(conn):
        return False
    try:
        columns = {
            str(row[1]): (str(row[2]).upper(), int(row[3]), int(row[5]))
            for row in conn.execute("PRAGMA table_info(paper_trades)")
        }
    except sqlite3.DatabaseError:
        return False
    return columns.get("venue_market_id") == ("TEXT", 0, 0) and columns.get(
        "identity_status"
    ) == ("TEXT", 0, 0)


@lru_cache(maxsize=1)
def _expected_settlement_schema_contract_signature() -> str:
    conn = _SQLITE_CONNECT(":memory:")
    try:
        paper_columns = ",\n".join(
            f"{name} {definition}"
            for name, definition in SETTLEMENT_PAPER_TRADE_COLUMNS
        )
        conn.execute(
            f"CREATE TABLE paper_trades (trade_id TEXT PRIMARY KEY, {paper_columns})"
        )
        for _name, statement in SETTLEMENT_TARGET_STATEMENTS:
            conn.execute(statement)
        return settlement_schema_contract_signature(conn)
    finally:
        conn.close()


def _settlement_schema_contract_payload(
    conn: sqlite3.Connection,
) -> dict[str, object]:
    objects: dict[str, object] = {}
    for name, expected_type in _TARGET_OBJECT_TYPES.items():
        rows = conn.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_schema
            WHERE name=?
            """,
            (name,),
        ).fetchall()
        if len(rows) != 1:
            objects[name] = {"object_count": len(rows)}
            continue
        row = rows[0]
        object_type = str(row[0])
        details: dict[str, object] = {
            "type": object_type,
            "table": str(row[2]),
            "sql": _normalize_schema_sql(row[3]),
        }
        if object_type == expected_type == "table":
            details["columns"] = _table_columns(conn, name)
            details["foreign_keys"] = _table_foreign_keys(conn, name)
        elif object_type == expected_type == "index":
            details["definition"] = _index_definition(conn, name, str(row[2]))
            details["columns"] = _index_columns(conn, name)
        objects[name] = details

    paper_trade_columns = {
        row["name"]: row
        for row in _table_columns(conn, "paper_trades")
        if row["name"] in {name for name, _ in SETTLEMENT_PAPER_TRADE_COLUMNS}
    }
    paper_trade_foreign_keys = [
        row
        for row in _table_foreign_keys(conn, "paper_trades")
        if row["from"] in paper_trade_columns
    ]
    paper_trade_row = conn.execute(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name='paper_trades'"
    ).fetchone()
    paper_trade_schema_sql = paper_trade_row[0] if paper_trade_row is not None else None
    paper_trade_sql = _normalize_schema_sql(
        paper_trade_schema_sql
    )
    return {
        "objects": objects,
        "paper_trades": {
            "columns": paper_trade_columns,
            "column_clauses": _settlement_paper_trade_column_clauses(
                paper_trade_schema_sql
            ),
            "foreign_keys": paper_trade_foreign_keys,
            "terminal_state_check": _TERMINAL_STATE_CHECK_SQL in paper_trade_sql,
        },
    }


def _normalize_schema_sql(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().rstrip(";").lower().split())


def _settlement_paper_trade_column_clauses(value: object) -> dict[str, str]:
    """Extract exact added-column clauses independent of base-table layout."""
    if not isinstance(value, str):
        return {}
    start = value.find("(")
    end = value.rfind(")")
    if start < 0 or end <= start:
        return {}
    target_names = {name for name, _definition in SETTLEMENT_PAPER_TRADE_COLUMNS}
    clauses: dict[str, str] = {}
    for clause in _split_top_level_sql_list(value[start + 1 : end]):
        normalized = _normalize_schema_sql(clause)
        for name in target_names:
            if normalized == name or normalized.startswith(f"{name} "):
                clauses[name] = normalized
                break
    return clauses


def _split_top_level_sql_list(value: str) -> list[str]:
    clauses: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(value):
        char = value[index]
        current.append(char)
        if quote == "[":
            if char == "]":
                quote = None
        elif quote is not None:
            if char == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    current.append(value[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "[":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            current.pop()
            clauses.append("".join(current).strip())
            current = []
        index += 1
    if current:
        clauses.append("".join(current).strip())
    return clauses


def _table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    return [
        {
            "name": str(row[1]),
            "type": str(row[2]).upper(),
            "not_null": int(row[3]),
            "default": row[4],
            "primary_key": int(row[5]),
            "hidden": int(row[6]),
        }
        for row in conn.execute(f"PRAGMA table_xinfo({table})")
    ]


def _table_foreign_keys(
    conn: sqlite3.Connection,
    table: str,
) -> list[dict[str, object]]:
    rows = [
        {
            "sequence": int(row[1]),
            "table": str(row[2]),
            "from": str(row[3]),
            "to": str(row[4]),
            "on_update": str(row[5]),
            "on_delete": str(row[6]),
            "match": str(row[7]),
        }
        for row in conn.execute(f"PRAGMA foreign_key_list({table})")
    ]
    return sorted(
        rows,
        key=lambda row: (
            row["from"],
            row["sequence"],
            row["table"],
            row["to"],
        ),
    )


def _index_definition(
    conn: sqlite3.Connection,
    index: str,
    table: str,
) -> dict[str, object]:
    for row in conn.execute(f"PRAGMA index_list({table})"):
        if row[1] == index:
            return {
                "unique": int(row[2]),
                "origin": str(row[3]),
                "partial": int(row[4]),
            }
    return {}


def _index_columns(conn: sqlite3.Connection, index: str) -> list[dict[str, object]]:
    return [
        {
            "sequence": int(row[0]),
            "name": row[2],
            "descending": int(row[3]),
            "collation": row[4],
            "key": int(row[5]),
        }
        for row in conn.execute(f"PRAGMA index_xinfo({index})")
    ]


def initialize_fresh_settlement_schema(
    conn: sqlite3.Connection,
    *,
    applied_at: str | None = None,
) -> None:
    """Install the target schema on a newly created PaperTrader database."""
    for _name, statement in SETTLEMENT_TARGET_STATEMENTS:
        conn.execute(statement)
    conn.execute(
        """
        INSERT INTO paper_settlement_schema_meta (
            schema_version, ddl_sha256, migration_plan_sha256, applied_at
        ) VALUES (?, ?, ?, ?)
        """,
        (
            SETTLEMENT_SCHEMA_VERSION,
            SETTLEMENT_DDL_SHA256,
            FRESH_SCHEMA_PLAN_SHA256,
            applied_at or datetime.now(timezone.utc).isoformat(),
        ),
    )


class SettlementStore:
    """Connection-scoped, unwired access to durable settlement state."""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path).expanduser().resolve()
        self._conn = sqlite3.connect(
            self._db_path,
            isolation_level=None,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        enable_and_verify_foreign_keys(self._conn)

    def __enter__(self) -> "SettlementStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def pending_requirements(self) -> tuple[PendingRequirement, ...]:
        rows = self._conn.execute(
            """
            SELECT r.outbox_id, r.consumer_name, o.event_version, o.event_kind,
                   o.payload_json, o.created_at
            FROM paper_settlement_outbox_requirements AS r
            JOIN paper_settlement_outbox AS o ON o.outbox_id = r.outbox_id
            LEFT JOIN paper_settlement_consumer_receipts AS receipt
              ON receipt.outbox_id = r.outbox_id
             AND receipt.consumer_name = r.consumer_name
            WHERE receipt.outbox_id IS NULL
            ORDER BY o.created_at, r.outbox_id, r.consumer_name
            """
        ).fetchall()
        return tuple(PendingRequirement(*tuple(row)) for row in rows)

    def is_outbox_drained(self, outbox_id: str) -> bool:
        row = self._conn.execute(
            """
            SELECT EXISTS(
                       SELECT 1 FROM paper_settlement_outbox WHERE outbox_id = ?
                   ) AS event_exists,
                   EXISTS(
                       SELECT 1
                       FROM paper_settlement_outbox_requirements AS r
                       LEFT JOIN paper_settlement_consumer_receipts AS receipt
                         ON receipt.outbox_id = r.outbox_id
                        AND receipt.consumer_name = r.consumer_name
                       WHERE r.outbox_id = ? AND receipt.outbox_id IS NULL
                   ) AS has_pending
            """,
            (outbox_id, outbox_id),
        ).fetchone()
        return bool(row["event_exists"]) and not bool(row["has_pending"])

    def acquire_claim(
        self,
        consumer_name: str,
        outbox_id: str,
        *,
        claim_token: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        _require_aware(now, "now")
        if not claim_token.strip():
            raise ValueError("claim_token is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_text = now.isoformat()
        lease_text = (now + timedelta(seconds=lease_seconds)).isoformat()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            receipt = self._conn.execute(
                """
                SELECT 1 FROM paper_settlement_consumer_receipts
                WHERE consumer_name=? AND outbox_id=?
                """,
                (consumer_name, outbox_id),
            ).fetchone()
            if receipt is not None:
                self._conn.commit()
                return False
            claim = self._conn.execute(
                """
                SELECT claim_token, lease_expires_at, attempt_count
                FROM paper_settlement_delivery_claims
                WHERE consumer_name=? AND outbox_id=?
                """,
                (consumer_name, outbox_id),
            ).fetchone()
            if claim is None:
                cursor = self._conn.execute(
                    """
                    INSERT INTO paper_settlement_delivery_claims (
                        consumer_name, outbox_id, claim_token, lease_expires_at,
                        attempt_count, updated_at
                    )
                    SELECT ?, ?, ?, ?, 1, ?
                    WHERE EXISTS (
                        SELECT 1 FROM paper_settlement_outbox_requirements
                        WHERE consumer_name=? AND outbox_id=?
                    )
                    """,
                    (
                        consumer_name,
                        outbox_id,
                        claim_token,
                        lease_text,
                        now_text,
                        consumer_name,
                        outbox_id,
                    ),
                )
                acquired = cursor.rowcount == 1
            else:
                expires_at = _parse_datetime(claim["lease_expires_at"])
                if expires_at > now:
                    acquired = False
                else:
                    cursor = self._conn.execute(
                        """
                        UPDATE paper_settlement_delivery_claims
                        SET claim_token=?, lease_expires_at=?,
                            attempt_count=attempt_count+1, updated_at=?
                        WHERE consumer_name=? AND outbox_id=? AND claim_token=?
                        """,
                        (
                            claim_token,
                            lease_text,
                            now_text,
                            consumer_name,
                            outbox_id,
                            claim["claim_token"],
                        ),
                    )
                    acquired = cursor.rowcount == 1
            self._conn.commit()
            return acquired
        except Exception:
            self._conn.rollback()
            raise

    def claim_state(
        self,
        consumer_name: str,
        outbox_id: str,
        *,
        now: datetime,
    ) -> str | None:
        _require_aware(now, "now")
        row = self._conn.execute(
            """
            SELECT lease_expires_at FROM paper_settlement_delivery_claims
            WHERE consumer_name=? AND outbox_id=?
            """,
            (consumer_name, outbox_id),
        ).fetchone()
        if row is None:
            return None
        return "active" if _parse_datetime(row[0]) > now else "expired"

    def record_receipt(
        self,
        consumer_name: str,
        outbox_id: str,
        *,
        claim_token: str,
        processed_at: datetime,
        result_sha256: str,
    ) -> bool:
        _require_aware(processed_at, "processed_at")
        if _SHA256_TEXT.fullmatch(result_sha256) is None:
            raise ValueError("result_sha256 must be lowercase SHA-256")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self._conn.execute(
                """
                SELECT result_sha256 FROM paper_settlement_consumer_receipts
                WHERE consumer_name=? AND outbox_id=?
                """,
                (consumer_name, outbox_id),
            ).fetchone()
            if existing is not None:
                if existing[0] != result_sha256:
                    raise RuntimeError("receipt result drift")
                self._conn.commit()
                return False
            claim = self._conn.execute(
                """
                SELECT claim_token, lease_expires_at
                FROM paper_settlement_delivery_claims
                WHERE consumer_name=? AND outbox_id=?
                """,
                (consumer_name, outbox_id),
            ).fetchone()
            if claim is None or claim["claim_token"] != claim_token:
                raise RuntimeError("receipt requires the active claim token")
            if _parse_datetime(claim["lease_expires_at"]) <= processed_at:
                raise RuntimeError("receipt claim lease expired")
            self._conn.execute(
                """
                INSERT INTO paper_settlement_consumer_receipts (
                    consumer_name, outbox_id, processed_at, result_sha256
                ) VALUES (?, ?, ?, ?)
                """,
                (consumer_name, outbox_id, processed_at.isoformat(), result_sha256),
            )
            self._conn.execute(
                """
                DELETE FROM paper_settlement_delivery_claims
                WHERE consumer_name=? AND outbox_id=? AND claim_token=?
                """,
                (consumer_name, outbox_id, claim_token),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    def complete_claim(
        self,
        consumer_name: str,
        outbox_id: str,
        *,
        claim_token: str,
        processed_at: datetime,
        result_sha256: str,
        apply: Callable[[sqlite3.Connection, PendingRequirement], None],
    ) -> bool:
        """Apply a same-database effect and receipt under one transaction."""
        _require_aware(processed_at, "processed_at")
        if _SHA256_TEXT.fullmatch(result_sha256) is None:
            raise ValueError("result_sha256 must be lowercase SHA-256")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self._conn.execute(
                """
                SELECT result_sha256 FROM paper_settlement_consumer_receipts
                WHERE consumer_name=? AND outbox_id=?
                """,
                (consumer_name, outbox_id),
            ).fetchone()
            if existing is not None:
                if existing[0] != result_sha256:
                    raise RuntimeError("receipt result drift")
                self._conn.commit()
                return False

            claim = self._conn.execute(
                """
                SELECT claim_token, lease_expires_at
                FROM paper_settlement_delivery_claims
                WHERE consumer_name=? AND outbox_id=?
                """,
                (consumer_name, outbox_id),
            ).fetchone()
            if claim is None or claim["claim_token"] != claim_token:
                raise RuntimeError("receipt requires the active claim token")
            if _parse_datetime(claim["lease_expires_at"]) <= processed_at:
                raise RuntimeError("receipt claim lease expired")

            row = self._conn.execute(
                """
                SELECT r.outbox_id, r.consumer_name, o.event_version,
                       o.event_kind, o.payload_json, o.created_at
                FROM paper_settlement_outbox_requirements AS r
                JOIN paper_settlement_outbox AS o ON o.outbox_id = r.outbox_id
                WHERE r.consumer_name=? AND r.outbox_id=?
                """,
                (consumer_name, outbox_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("claim requirement is missing")
            requirement = PendingRequirement(*tuple(row))
            apply(self._conn, requirement)

            self._conn.execute(
                """
                INSERT INTO paper_settlement_consumer_receipts (
                    consumer_name, outbox_id, processed_at, result_sha256
                ) VALUES (?, ?, ?, ?)
                """,
                (consumer_name, outbox_id, processed_at.isoformat(), result_sha256),
            )
            self._conn.execute(
                """
                DELETE FROM paper_settlement_delivery_claims
                WHERE consumer_name=? AND outbox_id=? AND claim_token=?
                """,
                (consumer_name, outbox_id, claim_token),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    def readiness(self, *, pre_cutover: bool) -> StoreCheck:
        failures: list[str] = []
        metrics: dict[str, int | str | bool] = {}
        integrity = tuple(
            str(row[0]) for row in self._conn.execute("PRAGMA integrity_check")
        )
        metrics["integrity"] = ",".join(integrity)
        if integrity != ("ok",):
            failures.append("integrity")

        schema_objects_ok = _schema_objects_ready(self._conn)
        metrics["schema_objects_ok"] = schema_objects_ok
        if not schema_objects_ok:
            failures.append("schema_objects")
            return StoreCheck(False, tuple(failures), metrics)

        meta = self._conn.execute(
            """
            SELECT schema_version, ddl_sha256, migration_plan_sha256
            FROM paper_settlement_schema_meta
            """
        ).fetchall()
        schema_meta_ok = (
            len(meta) == 1
            and meta[0]["schema_version"] == SETTLEMENT_SCHEMA_VERSION
            and meta[0]["ddl_sha256"] == SETTLEMENT_DDL_SHA256
            and _SHA256_TEXT.fullmatch(meta[0]["migration_plan_sha256"] or "")
            is not None
        )
        metrics["schema_meta_ok"] = schema_meta_ok
        if not schema_meta_ok:
            failures.append("schema_meta")

        unmapped = self._conn.execute(
            """
            SELECT COUNT(*) FROM paper_trades
            WHERE resolved=0 AND (
                identity_status IS NOT 'mapped'
                OR venue_market_id IS NULL
                OR trim(venue_market_id)=''
            )
            """
        ).fetchone()[0]
        metrics["unmapped_open_rows"] = unmapped
        if unmapped:
            failures.append("open_rows_mapped")

        identity_quarantine = self._conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE identity_status='quarantined'"
        ).fetchone()[0]
        settlement_quarantine = self._conn.execute(
            "SELECT COUNT(*) FROM paper_settlement_quarantine"
        ).fetchone()[0]
        metrics["identity_quarantine"] = identity_quarantine
        metrics["settlement_quarantine"] = settlement_quarantine
        if identity_quarantine:
            failures.append("identity_quarantine")
        if settlement_quarantine:
            failures.append("settlement_quarantine")

        pending = len(self.pending_requirements())
        metrics["pending_requirements"] = pending
        if pre_cutover and pending:
            failures.append("pre_cutover_pending_outbox")
        return StoreCheck(not failures, tuple(failures), metrics)

    def conservation(self, *, now: datetime) -> StoreCheck:
        _require_aware(now, "now")
        failures: list[str] = []
        metrics: dict[str, int | str | bool] = {}
        if not _schema_objects_ready(self._conn):
            return StoreCheck(False, ("schema_objects",), {"schema_objects_ok": False})
        observations = self._conn.execute(
            """
            SELECT observation_sha256, applied_trade_count,
                   bankroll_before_cents, gross_payout_cents,
                   bankroll_after_cents
            FROM paper_settlement_observations
            ORDER BY observation_sha256
            """
        ).fetchall()
        for observation in observations:
            observation_id = observation["observation_sha256"]
            trades = self._conn.execute(
                """
                SELECT gross_payout_cents, gross_pnl_cents, resolved,
                       identity_status, terminal_state, settled_at
                FROM paper_trades
                WHERE settlement_observation_sha256=?
                ORDER BY trade_id
                """,
                (observation_id,),
            ).fetchall()
            if len(trades) != observation["applied_trade_count"]:
                failures.append(f"trade_count:{observation_id}")
            try:
                for row in trades:
                    _parse_decimal(row["gross_pnl_cents"])
                trade_payout = sum(
                    (_parse_decimal(row["gross_payout_cents"]) for row in trades),
                    Decimal("0"),
                )
                observation_payout = _parse_decimal(
                    observation["gross_payout_cents"]
                )
                bankroll_before = _parse_decimal(
                    observation["bankroll_before_cents"]
                )
                bankroll_after = _parse_decimal(
                    observation["bankroll_after_cents"]
                )
            except ValueError:
                failures.append(f"noncanonical_amount:{observation_id}")
                continue
            if any(
                row["resolved"] != 1
                or row["identity_status"] != "mapped"
                or row["terminal_state"] not in {"won", "lost", "void"}
                or not row["settled_at"]
                for row in trades
            ):
                failures.append(f"linked_trade_state:{observation_id}")
            if trade_payout != observation_payout:
                failures.append(f"trade_payout:{observation_id}")
            if bankroll_before + observation_payout != bankroll_after:
                failures.append(f"bankroll:{observation_id}")

        unresolved_links = self._conn.execute(
            """
            SELECT COUNT(*)
            FROM paper_trades AS trade
            LEFT JOIN paper_settlement_observations AS observation
              ON observation.observation_sha256 = trade.settlement_observation_sha256
            WHERE trade.resolved=1
              AND trade.identity_status='mapped'
              AND observation.observation_sha256 IS NULL
            """
        ).fetchone()[0]
        metrics["resolved_rows_without_observation"] = unresolved_links
        if unresolved_links:
            failures.append("resolved_observation_link")

        duplicate_receipts = self._conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT consumer_name, outbox_id, COUNT(*) AS count
                FROM paper_settlement_consumer_receipts
                GROUP BY consumer_name, outbox_id HAVING count > 1
            )
            """
        ).fetchone()[0]
        duplicate_claims = self._conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT consumer_name, outbox_id, COUNT(*) AS count
                FROM paper_settlement_delivery_claims
                GROUP BY consumer_name, outbox_id HAVING count > 1
            )
            """
        ).fetchone()[0]
        if duplicate_receipts:
            failures.append("duplicate_receipts")
        if duplicate_claims:
            failures.append("duplicate_claims")

        claims = self._conn.execute(
            """
            SELECT claim.consumer_name, claim.outbox_id, claim.claim_token,
                   claim.lease_expires_at, claim.attempt_count, claim.updated_at,
                   receipt.outbox_id AS received_outbox_id
            FROM paper_settlement_delivery_claims AS claim
            LEFT JOIN paper_settlement_consumer_receipts AS receipt
              ON receipt.consumer_name=claim.consumer_name
             AND receipt.outbox_id=claim.outbox_id
            """
        ).fetchall()
        for claim in claims:
            try:
                _parse_datetime(claim["lease_expires_at"])
                _parse_datetime(claim["updated_at"])
            except ValueError:
                failures.append(
                    f"claim_lease:{claim['consumer_name']}:{claim['outbox_id']}"
                )
            if not claim["claim_token"] or claim["attempt_count"] < 1:
                failures.append(
                    f"claim_contract:{claim['consumer_name']}:{claim['outbox_id']}"
                )
            if claim["received_outbox_id"] is not None:
                failures.append(
                    f"claim_after_receipt:{claim['consumer_name']}:{claim['outbox_id']}"
                )
        metrics["observations"] = len(observations)
        metrics["claims"] = len(claims)
        return StoreCheck(not failures, tuple(failures), metrics)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _schema_objects_ready(conn: sqlite3.Connection) -> bool:
    return settlement_schema_contract_matches(conn)


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    _require_aware(parsed, "timestamp")
    return parsed


def _parse_decimal(value: object) -> Decimal:
    if not isinstance(value, str) or _DECIMAL_TEXT.fullmatch(value) is None:
        raise ValueError("amount must be canonical Decimal text")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("amount must be canonical Decimal text") from exc
    if not parsed.is_finite():
        raise ValueError("amount must be finite")
    return parsed
