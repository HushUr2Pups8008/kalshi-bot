"""Immutable, local storage for explicitly reconciled Kalshi order receipts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from utils.output_paths import DB_STATE_DIR


KALSHI_EXECUTION_LEDGER_DB = DB_STATE_DIR / "live_execution_ledger.db"
SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 5_000
UNATTRIBUTED_MANUAL_SOURCE = "unattributed_manual"
HISTORICAL_CUTOFF_UNKNOWN = "historical_cutoff_unknown"


class ExecutionLedgerSchemaError(RuntimeError):
    """Raised when an existing ledger database does not match this contract."""


@dataclass(frozen=True)
class LedgerPageResult:
    order_status: str
    fill_statuses: tuple[str, ...]


_STATEMENTS = (
    """
    CREATE TABLE execution_ledger_schema_meta (
        schema_version INTEGER PRIMARY KEY,
        ddl_sha256 TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE execution_orders (
        order_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        client_order_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        outcome_side TEXT NOT NULL CHECK (outcome_side IN ('yes', 'no')),
        book_side TEXT NOT NULL CHECK (book_side IN ('bid', 'ask')),
        order_type TEXT NOT NULL,
        status TEXT NOT NULL,
        subaccount_number INTEGER CHECK (subaccount_number BETWEEN 0 AND 63),
        source_kind TEXT NOT NULL CHECK (source_kind = 'unattributed_manual'),
        fill_coverage_state TEXT NOT NULL CHECK (
            fill_coverage_state = 'historical_cutoff_unknown'
        ),
        first_collected_at TEXT NOT NULL,
        last_collected_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE execution_order_snapshots (
        order_id TEXT NOT NULL REFERENCES execution_orders(order_id),
        payload_sha256 TEXT NOT NULL,
        raw_payload_json TEXT NOT NULL,
        collected_at TEXT NOT NULL,
        PRIMARY KEY (order_id, payload_sha256)
    )
    """,
    """
    CREATE TABLE execution_fill_receipts (
        fill_id TEXT PRIMARY KEY,
        trade_id TEXT NOT NULL,
        order_id TEXT NOT NULL REFERENCES execution_orders(order_id),
        ticker TEXT NOT NULL,
        market_ticker TEXT NOT NULL,
        outcome_side TEXT NOT NULL CHECK (outcome_side IN ('yes', 'no')),
        book_side TEXT NOT NULL CHECK (book_side IN ('bid', 'ask')),
        count_fp TEXT NOT NULL,
        yes_price_dollars TEXT NOT NULL,
        no_price_dollars TEXT NOT NULL,
        fee_cost_dollars TEXT NOT NULL,
        is_taker INTEGER NOT NULL CHECK (is_taker IN (0, 1)),
        created_time TEXT,
        ts INTEGER CHECK (ts >= 0),
        subaccount_number INTEGER CHECK (subaccount_number BETWEEN 0 AND 63),
        raw_payload_json TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        collected_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE execution_conflicts (
        conflict_id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        external_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        incoming_payload_json TEXT NOT NULL,
        incoming_payload_sha256 TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        UNIQUE (kind, external_id, reason, incoming_payload_sha256)
    )
    """,
    """
    CREATE TABLE execution_quarantines (
        quarantine_id INTEGER PRIMARY KEY,
        receipt_kind TEXT NOT NULL,
        external_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        raw_payload_json TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        UNIQUE (receipt_kind, external_id, reason, payload_sha256)
    )
    """,
    """
    CREATE TRIGGER execution_ledger_schema_meta_no_update
    BEFORE UPDATE ON execution_ledger_schema_meta
    BEGIN
        SELECT RAISE(ABORT, 'execution ledger schema metadata is immutable');
    END
    """,
    """
    CREATE TRIGGER execution_ledger_schema_meta_no_delete
    BEFORE DELETE ON execution_ledger_schema_meta
    BEGIN
        SELECT RAISE(ABORT, 'execution ledger schema metadata is immutable');
    END
    """,
    """
    CREATE TRIGGER execution_orders_identity_immutable
    BEFORE UPDATE OF user_id, client_order_id, ticker, outcome_side, book_side, order_type,
        subaccount_number, source_kind, fill_coverage_state, first_collected_at
    ON execution_orders
    FOR EACH ROW
    WHEN OLD.user_id IS NOT NEW.user_id
        OR OLD.client_order_id IS NOT NEW.client_order_id
        OR OLD.ticker IS NOT NEW.ticker
        OR OLD.outcome_side IS NOT NEW.outcome_side
        OR OLD.book_side IS NOT NEW.book_side
        OR OLD.order_type IS NOT NEW.order_type
        OR OLD.subaccount_number IS NOT NEW.subaccount_number
        OR OLD.source_kind IS NOT NEW.source_kind
        OR OLD.fill_coverage_state IS NOT NEW.fill_coverage_state
        OR OLD.first_collected_at IS NOT NEW.first_collected_at
    BEGIN
        SELECT RAISE(ABORT, 'execution order identity is immutable');
    END
    """,
    """
    CREATE TRIGGER execution_orders_no_delete
    BEFORE DELETE ON execution_orders
    BEGIN
        SELECT RAISE(ABORT, 'execution orders are immutable');
    END
    """,
    """
    CREATE TRIGGER execution_order_snapshots_no_update
    BEFORE UPDATE ON execution_order_snapshots
    BEGIN
        SELECT RAISE(ABORT, 'execution order snapshots are immutable');
    END
    """,
    """
    CREATE TRIGGER execution_order_snapshots_no_delete
    BEFORE DELETE ON execution_order_snapshots
    BEGIN
        SELECT RAISE(ABORT, 'execution order snapshots are immutable');
    END
    """,
    """
    CREATE TRIGGER execution_fill_receipts_no_update
    BEFORE UPDATE ON execution_fill_receipts
    BEGIN
        SELECT RAISE(ABORT, 'execution fill receipts are immutable');
    END
    """,
    """
    CREATE TRIGGER execution_fill_receipts_no_delete
    BEFORE DELETE ON execution_fill_receipts
    BEGIN
        SELECT RAISE(ABORT, 'execution fill receipts are immutable');
    END
    """,
    """
    CREATE TRIGGER execution_conflicts_no_update
    BEFORE UPDATE ON execution_conflicts
    BEGIN
        SELECT RAISE(ABORT, 'execution conflicts are immutable');
    END
    """,
    """
    CREATE TRIGGER execution_conflicts_no_delete
    BEFORE DELETE ON execution_conflicts
    BEGIN
        SELECT RAISE(ABORT, 'execution conflicts are immutable');
    END
    """,
    """
    CREATE TRIGGER execution_quarantines_no_update
    BEFORE UPDATE ON execution_quarantines
    BEGIN
        SELECT RAISE(ABORT, 'execution quarantines are immutable');
    END
    """,
    """
    CREATE TRIGGER execution_quarantines_no_delete
    BEFORE DELETE ON execution_quarantines
    BEGIN
        SELECT RAISE(ABORT, 'execution quarantines are immutable');
    END
    """,
)
_TABLES = {
    "execution_ledger_schema_meta",
    "execution_orders",
    "execution_order_snapshots",
    "execution_fill_receipts",
    "execution_conflicts",
    "execution_quarantines",
}
_DDL_SHA256 = hashlib.sha256("\n".join(_STATEMENTS).encode("utf-8")).hexdigest()


def _normalized_sql(statement: str) -> str:
    return " ".join(statement.split())


_SCHEMA_OBJECT_NAMES = (
    "execution_ledger_schema_meta",
    "execution_orders",
    "execution_order_snapshots",
    "execution_fill_receipts",
    "execution_conflicts",
    "execution_quarantines",
    "execution_ledger_schema_meta_no_update",
    "execution_ledger_schema_meta_no_delete",
    "execution_orders_identity_immutable",
    "execution_orders_no_delete",
    "execution_order_snapshots_no_update",
    "execution_order_snapshots_no_delete",
    "execution_fill_receipts_no_update",
    "execution_fill_receipts_no_delete",
    "execution_conflicts_no_update",
    "execution_conflicts_no_delete",
    "execution_quarantines_no_update",
    "execution_quarantines_no_delete",
)
_EXPECTED_SCHEMA_OBJECTS = {
    name: ("table" if index < len(_TABLES) else "trigger", _normalized_sql(statement))
    for index, (name, statement) in enumerate(zip(_SCHEMA_OBJECT_NAMES, _STATEMENTS, strict=True))
}


class _ReceiptValidationError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class KalshiExecutionLedger:
    """A dedicated receipt store; it does not calculate or report P&L."""

    def __init__(self, db_path: Path | str = KALSHI_EXECUTION_LEDGER_DB) -> None:
        self.db_path = Path(db_path).expanduser().resolve(strict=False)

    def initialize(self, *, applied_at: str) -> None:
        timestamp = _required_text(applied_at, "applied_at")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            schema_objects = _schema_objects(conn)
            if not schema_objects:
                for statement in _STATEMENTS:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO execution_ledger_schema_meta "
                    "(schema_version, ddl_sha256, applied_at) VALUES (?, ?, ?)",
                    (SCHEMA_VERSION, _DDL_SHA256, timestamp),
                )
            self._validate_schema(conn)
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def apply_page(
        self,
        order: Mapping[str, Any],
        fills: Sequence[object],
        *,
        collected_at: str,
        source_kind: str = UNATTRIBUTED_MANUAL_SOURCE,
    ) -> LedgerPageResult:
        timestamp = _required_text(collected_at, "collected_at")
        normalized_source_kind = _source_kind(source_kind)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_schema(conn)
            order_status = self._record_order_transaction(
                conn,
                order,
                timestamp,
                source_kind=normalized_source_kind,
            )
            if order_status in {"quarantined", "conflict"}:
                result = LedgerPageResult(order_status=order_status, fill_statuses=())
            else:
                expected_order_id = _required_text(order.get("order_id"), "missing_order_id")
                fill_statuses = tuple(
                    self._record_fill_transaction(
                        conn,
                        fill,
                        timestamp,
                        expected_order_id=expected_order_id,
                    )
                    for fill in fills
                )
                result = LedgerPageResult(order_status=order_status, fill_statuses=fill_statuses)
            conn.commit()
            return result
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _record_order_transaction(
        self,
        conn: sqlite3.Connection,
        order: Mapping[str, Any],
        collected_at: str,
        *,
        source_kind: str,
    ) -> str:
        payload_json, payload_sha256 = _payload(order)
        try:
            if not isinstance(order, Mapping):
                raise _ReceiptValidationError("order_not_object")
            order_id = _required_text(order.get("order_id"), "missing_order_id")
            user_id = _required_text(order.get("user_id"), "invalid_user_id")
            client_order_id = _required_text(order.get("client_order_id"), "invalid_client_order_id")
            ticker = _required_text(order.get("ticker"), "invalid_ticker")
            outcome_side = _required_choice(
                order.get("outcome_side"),
                "invalid_outcome_side",
                allowed={"yes", "no"},
            )
            book_side = _required_choice(
                order.get("book_side"),
                "invalid_book_side",
                allowed={"bid", "ask"},
            )
            _validate_direction_pair(outcome_side, book_side)
            order_type = _required_text(order.get("type"), "invalid_order_type")
            status = _required_text(order.get("status"), "invalid_status")
            _required_fixed_decimal_text(
                order.get("yes_price_dollars"), "invalid_yes_price_dollars"
            )
            _required_fixed_decimal_text(
                order.get("no_price_dollars"), "invalid_no_price_dollars"
            )
            _required_fixed_decimal_text(order.get("fill_count_fp"), "invalid_fill_count_fp")
            _required_fixed_decimal_text(
                order.get("remaining_count_fp"), "invalid_remaining_count_fp"
            )
            _required_fixed_decimal_text(
                order.get("initial_count_fp"), "invalid_initial_count_fp"
            )
            _required_fixed_decimal_text(
                order.get("taker_fees_dollars"), "invalid_taker_fees_dollars"
            )
            _required_fixed_decimal_text(
                order.get("maker_fees_dollars"), "invalid_maker_fees_dollars"
            )
            _required_fixed_decimal_text(
                order.get("taker_fill_cost_dollars"), "invalid_taker_fill_cost_dollars"
            )
            _required_fixed_decimal_text(
                order.get("maker_fill_cost_dollars"), "invalid_maker_fill_cost_dollars"
            )
            _optional_text(order.get("created_time"), "invalid_created_time")
            _optional_text(order.get("last_update_time"), "invalid_last_update_time")
            subaccount_number = _optional_subaccount_number(
                order.get("subaccount_number"), "invalid_subaccount_number"
            )
        except _ReceiptValidationError as exc:
            self._quarantine_transaction(
                conn,
                receipt_kind="order",
                external_id=_best_effort_id(order, "order_id"),
                reason=exc.reason,
                payload_json=payload_json,
                payload_sha256=payload_sha256,
                observed_at=collected_at,
            )
            return "quarantined"

        existing_order = conn.execute(
            "SELECT user_id, client_order_id, ticker, outcome_side, book_side, order_type, "
            "subaccount_number, source_kind FROM execution_orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if existing_order is None:
            conn.execute(
                "INSERT INTO execution_orders "
                "(order_id, user_id, client_order_id, ticker, outcome_side, book_side, order_type, status, "
                "subaccount_number, source_kind, fill_coverage_state, first_collected_at, "
                "last_collected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    order_id,
                    user_id,
                    client_order_id,
                    ticker,
                    outcome_side,
                    book_side,
                    order_type,
                    status,
                    subaccount_number,
                    source_kind,
                    HISTORICAL_CUTOFF_UNKNOWN,
                    collected_at,
                    collected_at,
                ),
            )
        else:
            immutable_identity = (
                user_id,
                client_order_id,
                ticker,
                outcome_side,
                book_side,
                order_type,
                subaccount_number,
                source_kind,
            )
            if tuple(existing_order) != immutable_identity:
                self._conflict_transaction(
                    conn,
                    kind="order",
                    external_id=order_id,
                    reason="immutable_order_identity_conflict",
                    payload_json=payload_json,
                    payload_sha256=payload_sha256,
                    observed_at=collected_at,
                )
                return "conflict"
            conn.execute(
                "UPDATE execution_orders SET status = ?, last_collected_at = ? WHERE order_id = ?",
                (status, collected_at, order_id),
            )
        existing_snapshot = conn.execute(
            "SELECT 1 FROM execution_order_snapshots WHERE order_id = ? AND payload_sha256 = ?",
            (order_id, payload_sha256),
        ).fetchone()
        if existing_snapshot is not None:
            return "identical"
        conn.execute(
            "INSERT INTO execution_order_snapshots "
            "(order_id, payload_sha256, raw_payload_json, collected_at) VALUES (?, ?, ?, ?)",
            (order_id, payload_sha256, payload_json, collected_at),
        )
        return "inserted"

    def _record_fill_transaction(
        self,
        conn: sqlite3.Connection,
        fill: object,
        collected_at: str,
        *,
        expected_order_id: str,
    ) -> str:
        payload_json, payload_sha256 = _payload(fill)
        try:
            if not isinstance(fill, Mapping):
                raise _ReceiptValidationError("fill_not_object")
            fill_id = _required_text(fill.get("fill_id"), "missing_fill_id")
            trade_id = _required_text(fill.get("trade_id"), "missing_trade_id")
            if trade_id != fill_id:
                raise _ReceiptValidationError("fill_trade_id_mismatch")
            order_id = _required_text(fill.get("order_id"), "missing_order_id")
            if order_id != expected_order_id:
                raise _ReceiptValidationError("fill_order_id_mismatch")
            order_identity = conn.execute(
                "SELECT ticker, outcome_side, book_side, subaccount_number "
                "FROM execution_orders WHERE order_id = ?",
                (expected_order_id,),
            ).fetchone()
            if order_identity is None:
                raise _ReceiptValidationError("unknown_order_id")
            ticker = _required_text(fill.get("ticker"), "invalid_ticker")
            market_ticker = _required_text(
                fill.get("market_ticker"), "invalid_market_ticker"
            )
            if market_ticker != ticker:
                raise _ReceiptValidationError("fill_market_ticker_mismatch")
            outcome_side = _required_choice(
                fill.get("outcome_side"),
                "invalid_outcome_side",
                allowed={"yes", "no"},
            )
            book_side = _required_choice(
                fill.get("book_side"),
                "invalid_book_side",
                allowed={"bid", "ask"},
            )
            _validate_direction_pair(outcome_side, book_side)
            count_fp = _required_fixed_decimal_text(
                fill.get("count_fp"), "invalid_count_fp", positive=True
            )
            yes_price_dollars = _required_fixed_decimal_text(
                fill.get("yes_price_dollars"), "invalid_yes_price_dollars"
            )
            no_price_dollars = _required_fixed_decimal_text(
                fill.get("no_price_dollars"), "invalid_no_price_dollars"
            )
            fee_cost_dollars = _required_fixed_decimal_text(
                fill.get("fee_cost"), "invalid_fee_cost"
            )
            is_taker = _required_bool(fill.get("is_taker"), "invalid_is_taker")
            created_time = _optional_text(fill.get("created_time"), "invalid_created_time")
            ts = _optional_non_negative_int(fill.get("ts"), "invalid_ts")
            subaccount_number = _optional_subaccount_number(
                fill.get("subaccount_number"), "invalid_subaccount_number"
            )
            if tuple(order_identity[:3]) != (ticker, outcome_side, book_side):
                raise _ReceiptValidationError("fill_order_identity_mismatch")
            if (
                order_identity[3] is not None
                and subaccount_number is not None
                and order_identity[3] != subaccount_number
            ):
                raise _ReceiptValidationError("fill_subaccount_mismatch")
        except _ReceiptValidationError as exc:
            self._quarantine_transaction(
                conn,
                receipt_kind="fill",
                external_id=_best_effort_id(fill, "fill_id"),
                reason=exc.reason,
                payload_json=payload_json,
                payload_sha256=payload_sha256,
                observed_at=collected_at,
            )
            return "quarantined"

        existing = conn.execute(
            "SELECT payload_sha256 FROM execution_fill_receipts WHERE fill_id = ?", (fill_id,)
        ).fetchone()
        if existing is not None:
            if existing[0] == payload_sha256:
                return "identical"
            self._conflict_transaction(
                conn,
                kind="fill",
                external_id=fill_id,
                reason="payload_hash_conflict",
                payload_json=payload_json,
                payload_sha256=payload_sha256,
                observed_at=collected_at,
            )
            return "conflict"

        conn.execute(
            "INSERT INTO execution_fill_receipts "
            "(fill_id, trade_id, order_id, ticker, market_ticker, outcome_side, book_side, "
            "count_fp, yes_price_dollars, no_price_dollars, fee_cost_dollars, is_taker, "
            "created_time, ts, subaccount_number, raw_payload_json, payload_sha256, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fill_id,
                trade_id,
                order_id,
                ticker,
                market_ticker,
                outcome_side,
                book_side,
                count_fp,
                yes_price_dollars,
                no_price_dollars,
                fee_cost_dollars,
                int(is_taker),
                created_time,
                ts,
                subaccount_number,
                payload_json,
                payload_sha256,
                collected_at,
            ),
        )
        return "inserted"

    def _quarantine_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        receipt_kind: str,
        external_id: str,
        reason: str,
        payload_json: str,
        payload_sha256: str,
        observed_at: str,
    ) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO execution_quarantines "
            "(receipt_kind, external_id, reason, raw_payload_json, payload_sha256, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (receipt_kind, external_id, reason, payload_json, payload_sha256, observed_at),
        )

    def _conflict_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        kind: str,
        external_id: str,
        reason: str,
        payload_json: str,
        payload_sha256: str,
        observed_at: str,
    ) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO execution_conflicts "
            "(kind, external_id, reason, incoming_payload_json, incoming_payload_sha256, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, external_id, reason, payload_json, payload_sha256, observed_at),
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
        except BaseException:
            conn.close()
            raise
        return conn

    def _validate_schema(self, conn: sqlite3.Connection) -> None:
        if _schema_objects(conn) != _EXPECTED_SCHEMA_OBJECTS:
            raise ExecutionLedgerSchemaError("execution ledger schema drift")
        meta = conn.execute(
            "SELECT schema_version, ddl_sha256 FROM execution_ledger_schema_meta"
        ).fetchall()
        if meta != [(SCHEMA_VERSION, _DDL_SHA256)]:
            raise ExecutionLedgerSchemaError("execution ledger schema drift")


def _schema_objects(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    return {
        str(row[0]): (str(row[1]), _normalized_sql(str(row[2])))
        for row in conn.execute(
            "SELECT name, type, sql FROM sqlite_schema "
            "WHERE type IN ('table', 'trigger') AND name NOT LIKE 'sqlite_%'"
        )
    }


def _payload(value: object) -> tuple[str, str]:
    try:
        payload_json = canonical_json(value)
    except (TypeError, ValueError):
        payload_json = canonical_json({"unserializable_repr": repr(value)})
    return payload_json, hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _required_text(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _ReceiptValidationError(reason)
    return value.strip()


def _optional_text(value: object, reason: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, reason)


def _required_choice(value: object, reason: str, *, allowed: set[str]) -> str:
    result = _required_text(value, reason)
    if result not in allowed:
        raise _ReceiptValidationError(reason)
    return result


def _validate_direction_pair(outcome_side: str, book_side: str) -> None:
    if (outcome_side, book_side) not in {("yes", "bid"), ("no", "ask")}:
        raise _ReceiptValidationError("outcome_book_side_mismatch")


def _required_bool(value: object, reason: str) -> bool:
    if not isinstance(value, bool):
        raise _ReceiptValidationError(reason)
    return value


def _optional_non_negative_int(value: object, reason: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _ReceiptValidationError(reason)
    return value


def _optional_subaccount_number(value: object, reason: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 63:
        raise _ReceiptValidationError(reason)
    return value


def _required_fixed_decimal_text(value: object, reason: str, *, positive: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _ReceiptValidationError(reason)
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError):
        raise _ReceiptValidationError(reason) from None
    if not decimal.is_finite() or decimal < 0 or (positive and decimal == 0):
        raise _ReceiptValidationError(reason)
    if decimal == 0:
        return "0"
    return format(decimal.normalize(), "f")


def _source_kind(value: object) -> str:
    if value != UNATTRIBUTED_MANUAL_SOURCE:
        raise ValueError("only manually supplied order IDs are supported in this collector")
    return UNATTRIBUTED_MANUAL_SOURCE


def _best_effort_id(value: object, key: str) -> str:
    if isinstance(value, Mapping):
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate.strip()
    return ""
