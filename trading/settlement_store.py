"""Unwired durable settlement schema and storage primitives."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from trading.settlement import (
    MarketOutcome,
    SettlementObservation,
    VoidRefundContract,
    canonical_payload_json,
    validate_observation_transition,
)
from trading.venue import MarketRef, Venue

_SQLITE_CONNECT = sqlite3.connect

SETTLEMENT_SCHEMA_VERSION = 1
SETTLEMENT_EVENT_VERSION = 1
PAPER_TRADE_SETTLED_EVENT_KIND = "paper_trade_settled"
PAPER_TRADE_SETTLED_VOID_REQUIREMENTS = ("paper_trade_log",)
PAPER_TRADE_SETTLED_DIRECTIONAL_REQUIREMENTS = (
    "paper_trade_log",
    "source_credibility",
    "calibration_state",
    "keyword_outcomes",
)

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
            venue TEXT NOT NULL CHECK (venue IN ('kalshi','polymarket_us')),
            venue_market_id TEXT NOT NULL CHECK (
                length(trim(venue_market_id)) > 0
            ),
            alias TEXT NOT NULL CHECK (length(trim(alias)) > 0),
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


class _CallbackTransactionControlError(RuntimeError, AttributeError):
    pass


class _CallbackTransactionGuard:
    __slots__ = ("blocked",)

    def __init__(self) -> None:
        self.blocked = False

    def reset(self) -> None:
        self.blocked = False

    def authorize(
        self,
        action_code: int,
        _arg1: str | None,
        _arg2: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action_code in {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}:
            self.blocked = True
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def reject(self) -> None:
        self.blocked = True
        raise _CallbackTransactionControlError(
            "callback transaction control is forbidden"
        )


class _SettlementEffectCursor:
    __slots__ = ("__cursor", "__guard")

    def __init__(
        self,
        cursor: sqlite3.Cursor,
        guard: _CallbackTransactionGuard,
    ) -> None:
        self.__cursor = cursor
        self.__guard = guard

    def __enter__(self) -> "_SettlementEffectCursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def description(self):
        return self.__cursor.description

    @property
    def lastrowid(self):
        return self.__cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self.__cursor.rowcount

    def __iter__(self):
        while (row := self.__cursor.fetchone()) is not None:
            yield row

    def close(self) -> None:
        self.__cursor.close()

    def execute(self, sql: str, parameters: object = ()) -> "_SettlementEffectCursor":
        try:
            self.__cursor.execute(sql, parameters)
        except sqlite3.DatabaseError as exc:
            if self.__guard.blocked:
                raise RuntimeError(
                    "callback transaction control is forbidden"
                ) from exc
            raise
        return self

    def executemany(
        self,
        sql: str,
        parameters: object,
    ) -> "_SettlementEffectCursor":
        try:
            self.__cursor.executemany(sql, parameters)
        except sqlite3.DatabaseError as exc:
            if self.__guard.blocked:
                raise RuntimeError(
                    "callback transaction control is forbidden"
                ) from exc
            raise
        return self

    def fetchall(self):
        return self.__cursor.fetchall()

    def fetchmany(self, size: int | None = None):
        if size is None:
            return self.__cursor.fetchmany()
        return self.__cursor.fetchmany(size)

    def fetchone(self):
        return self.__cursor.fetchone()


class _SettlementEffectConnection:
    __slots__ = ("__connection", "__guard")

    _FORBIDDEN_ATTRIBUTES = frozenset(
        {
            "commit",
            "rollback",
            "set_authorizer",
            "executescript",
        }
    )

    def __init__(
        self,
        connection: sqlite3.Connection,
        guard: _CallbackTransactionGuard,
    ) -> None:
        self.__connection = connection
        self.__guard = guard

    def __getattr__(self, name: str):
        if name in self._FORBIDDEN_ATTRIBUTES:
            self.__guard.reject()
        raise AttributeError(name)

    def cursor(self) -> _SettlementEffectCursor:
        return _SettlementEffectCursor(self.__connection.cursor(), self.__guard)

    def execute(self, sql: str, parameters: object = ()) -> _SettlementEffectCursor:
        try:
            cursor = self.__connection.execute(sql, parameters)
        except sqlite3.DatabaseError as exc:
            if self.__guard.blocked:
                raise RuntimeError(
                    "callback transaction control is forbidden"
                ) from exc
            raise
        return _SettlementEffectCursor(cursor, self.__guard)

    def executemany(
        self,
        sql: str,
        parameters: object,
    ) -> _SettlementEffectCursor:
        try:
            cursor = self.__connection.executemany(sql, parameters)
        except sqlite3.DatabaseError as exc:
            if self.__guard.blocked:
                raise RuntimeError(
                    "callback transaction control is forbidden"
                ) from exc
            raise
        return _SettlementEffectCursor(cursor, self.__guard)


@dataclass(frozen=True)
class StoreCheck:
    ok: bool
    failures: tuple[str, ...]
    metrics: dict[str, int | str | bool]


@dataclass(frozen=True)
class PaperTradeSettledOutboxContract:
    outbox_id: str
    event_version: int
    event_kind: str
    observation_sha256: str
    trade_id: str
    payload_json: str
    created_at: str
    requirements: tuple[str, ...]


def settlement_keyword_directions() -> dict[str, str]:
    """Return the current producer keyword-direction contract."""
    import config as config_module

    return {
        keyword: signal["direction"]
        for signal in config_module.GEOPOLITICAL_SIGNALS
        for keyword in signal["keywords"]
    }


def paper_trade_settled_outbox_contract(
    observation: SettlementObservation,
    trade: Mapping[str, object],
    *,
    created_at: str,
    keyword_directions: Mapping[str, str],
) -> PaperTradeSettledOutboxContract:
    """Build the canonical durable event for one settled paper trade."""
    event_identity = {
        "event_kind": PAPER_TRADE_SETTLED_EVENT_KIND,
        "event_version": SETTLEMENT_EVENT_VERSION,
        "observation_sha256": observation.observation_sha256,
        "trade_id": trade["trade_id"],
    }
    outbox_id = hashlib.sha256(
        canonical_payload_json(event_identity).encode("utf-8")
    ).hexdigest()
    resolved_yes = (
        bool(trade["resolved_yes"])
        if trade["resolved_yes"] is not None
        else None
    )
    keywords = json.loads(str(trade["keywords_matched"] or "[]"))
    if not isinstance(keywords, list):
        raise ValueError("keywords_matched must encode a list")
    keyword_outcomes = []
    for keyword in keywords:
        direction = keyword_directions.get(keyword, str(trade["side"]))
        correct = None
        if resolved_yes is not None:
            correct = (direction == "yes") == resolved_yes
        keyword_outcomes.append(
            {"keyword": keyword, "direction": direction, "correct": correct}
        )

    payload = {
        "alias": observation.market_ref.alias,
        "event_kind": PAPER_TRADE_SETTLED_EVENT_KIND,
        "event_version": SETTLEMENT_EVENT_VERSION,
        "outbox_id": outbox_id,
        "gross_payout_cents": _settlement_decimal_text(
            trade["gross_payout_cents"]
        ),
        "gross_pnl_cents": _settlement_decimal_text(trade["gross_pnl_cents"]),
        "observation_sha256": observation.observation_sha256,
        "outcome": observation.outcome.value,
        "ticker": trade["ticker"],
        "side": trade["side"],
        "trade_id": trade["trade_id"],
        "venue": observation.market_ref.venue.value,
        "venue_market_id": observation.market_ref.venue_market_id,
        "resolved_yes": resolved_yes,
        "terminal_state": trade["terminal_state"],
        "won": trade["won"],
        "settled_at": created_at,
        "signal_source": trade["signal_source"],
        "series_ticker": trade["series_ticker"],
        "entry_ts": trade["entry_ts"],
        "estimated_prob": trade["estimated_prob"],
        "entry_price_cents": trade["entry_price_cents"],
        "cost_dollars": trade["cost_dollars"],
        "llm_magnitude": trade["llm_magnitude"],
        "llm_confidence": trade["llm_confidence"],
        "keyword_outcomes": keyword_outcomes,
        "lane_estimates": {
            "fast": trade["fast_lane_p"],
            "accumulation": trade["accumulation_p"],
            "structural": trade["structural_p"],
        },
    }
    requirements = (
        PAPER_TRADE_SETTLED_VOID_REQUIREMENTS
        if observation.outcome is MarketOutcome.VOID
        else PAPER_TRADE_SETTLED_DIRECTIONAL_REQUIREMENTS
    )
    return PaperTradeSettledOutboxContract(
        outbox_id=outbox_id,
        event_version=SETTLEMENT_EVENT_VERSION,
        event_kind=PAPER_TRADE_SETTLED_EVENT_KIND,
        observation_sha256=observation.observation_sha256,
        trade_id=str(trade["trade_id"]),
        payload_json=canonical_payload_json(payload),
        created_at=created_at,
        requirements=requirements,
    )


def _persisted_keyword_directions(
    payload_json: str,
    trade: Mapping[str, object],
) -> dict[str, str]:
    """Recover and validate the immutable keyword-direction event snapshot."""
    payload = json.loads(payload_json)
    if not isinstance(payload, dict) or "resolved_yes" not in payload:
        raise ValueError("outbox payload must encode a resolved event object")
    resolved_yes = payload["resolved_yes"]
    if resolved_yes is not None and type(resolved_yes) is not bool:
        raise ValueError("resolved_yes must be boolean or null")

    keywords = json.loads(str(trade["keywords_matched"] or "[]"))
    keyword_outcomes = payload.get("keyword_outcomes")
    if (
        not isinstance(keywords, list)
        or any(not isinstance(keyword, str) for keyword in keywords)
        or not isinstance(keyword_outcomes, list)
        or len(keyword_outcomes) != len(keywords)
    ):
        raise ValueError("keyword outcomes must match the stored trade keywords")

    directions: dict[str, str] = {}
    for keyword, outcome in zip(keywords, keyword_outcomes):
        if not isinstance(outcome, dict) or set(outcome) != {
            "keyword",
            "direction",
            "correct",
        }:
            raise ValueError("keyword outcome must use the canonical event shape")
        direction = outcome["direction"]
        if outcome["keyword"] != keyword or direction not in {"yes", "no"}:
            raise ValueError("keyword outcome does not match the stored trade")
        expected_correct = (
            None if resolved_yes is None else (direction == "yes") == resolved_yes
        )
        if outcome["correct"] is not expected_correct:
            raise ValueError("keyword outcome correctness is inconsistent")
        previous_direction = directions.get(keyword)
        if previous_direction is not None and previous_direction != direction:
            raise ValueError("duplicate keyword directions are inconsistent")
        directions[keyword] = direction
    return directions


def settlement_result_sha256(outbox_id: str, consumer_name: str) -> str:
    """Return the deterministic result identity for one consumer requirement."""
    return hashlib.sha256(f"{outbox_id}:{consumer_name}".encode()).hexdigest()


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
        self._callback_guard = _CallbackTransactionGuard()
        self._connection_facade = _SettlementEffectConnection(
            self._conn,
            self._callback_guard,
        )

    def __enter__(self) -> "SettlementStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def connection(self) -> _SettlementEffectConnection:
        return self._connection_facade

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
        apply: Callable[[_SettlementEffectConnection, PendingRequirement], object],
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
            guard = self._callback_guard
            guard.reset()
            self._conn.set_authorizer(guard.authorize)
            try:
                callback_result = apply(self._connection_facade, requirement)
                if inspect.isawaitable(callback_result):
                    close = getattr(callback_result, "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:  # noqa: BLE001 - still reject callback
                            pass
                    raise TypeError("callback must be synchronous")
                if guard.blocked:
                    raise RuntimeError(
                        "callback transaction control is forbidden"
                    )
            finally:
                self._conn.set_authorizer(None)
            if not self._conn.in_transaction:
                raise RuntimeError("callback transaction control is forbidden")

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

        foreign_key_violations = _foreign_key_violation_count(self._conn)
        metrics["foreign_key_violations"] = foreign_key_violations
        if foreign_key_violations:
            failures.append("foreign_keys")

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
        foreign_key_violations = _foreign_key_violation_count(self._conn)
        metrics["foreign_key_violations"] = foreign_key_violations
        if foreign_key_violations:
            failures.append("foreign_keys")
        observations = self._conn.execute(
            """
            SELECT observation_sha256, venue, venue_market_id, alias, outcome,
                   authoritative_outcome_json, canonical_payload_json,
                   payload_sha256, observed_at, effective_at, applied_at,
                   rules_version, source_id, supersedes_observation_sha256,
                   refund_cents_per_contract, refunds_entry_fee,
                   applied_trade_count,
                   bankroll_before_cents, gross_payout_cents,
                   bankroll_after_cents
            FROM paper_settlement_observations
            ORDER BY observation_sha256
            """
        ).fetchall()
        linked_trades = 0
        invalid_observation_identities = 0
        invalid_observation_timestamps = 0
        invalid_observation_semantics = 0
        invalid_trade_identity = 0
        linked_trade_alias_drifts = 0
        invalid_trade_financials = 0
        invalid_trade_outcomes = 0
        invalid_trade_timestamps = 0
        invalid_settlement_outboxes = 0
        invalid_settlement_outbox_requirements = 0
        observation_applied_at: dict[str, datetime] = {}
        reconstructed_observations: dict[
            str, tuple[SettlementObservation, datetime]
        ] = {}
        expected_outbox_inputs: dict[
            tuple[str, str], tuple[SettlementObservation, dict[str, object], str]
        ] = {}
        for observation in observations:
            observation_id = observation["observation_sha256"]
            identity_valid = _valid_market_identity(
                observation["venue"],
                observation["venue_market_id"],
                observation["alias"],
            )
            if not identity_valid:
                failures.append(f"observation_identity:{observation_id}")
                invalid_observation_identities += 1

            timestamps_valid = False
            try:
                observed_at = _parse_datetime(observation["observed_at"])
                effective_at = _parse_datetime(observation["effective_at"])
                applied_at = _parse_datetime(observation["applied_at"])
                if not effective_at <= observed_at <= applied_at:
                    raise ValueError("invalid observation timestamp order")
                observation_applied_at[observation_id] = applied_at
                timestamps_valid = True
            except ValueError:
                failures.append(f"observation_timestamps:{observation_id}")
                invalid_observation_timestamps += 1

            if identity_valid and timestamps_valid:
                try:
                    reconstructed = _reconstruct_observation(
                        observation,
                        observed_at=observed_at,
                        effective_at=effective_at,
                    )
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    failures.append(f"observation_semantics:{observation_id}")
                    invalid_observation_semantics += 1
                else:
                    reconstructed_observations[observation_id] = (
                        reconstructed,
                        applied_at,
                    )
            reconstructed_entry = reconstructed_observations.get(observation_id)
            canonical_observation = (
                reconstructed_entry[0] if reconstructed_entry is not None else None
            )
            trades = self._conn.execute(
                """
                SELECT trade_id, ticker, venue, venue_market_id, side,
                       contracts, price_cents, cost_dollars, pnl_dollars,
                       gross_payout_cents,
                       gross_pnl_cents, resolved, resolved_yes,
                       identity_status, terminal_state, settled_at, resolved_ts,
                       ts AS entry_ts, estimated_prob, entry_price_cents,
                       signal_source, series_ticker, llm_magnitude,
                       llm_confidence, keywords_matched, fast_lane_p,
                       accumulation_p, structural_p
                FROM paper_trades
                WHERE settlement_observation_sha256=?
                ORDER BY trade_id
                """,
                (observation_id,),
            ).fetchall()
            linked_trades += len(trades)
            if len(trades) != observation["applied_trade_count"]:
                failures.append(f"trade_count:{observation_id}")

            trade_payout = Decimal("0")
            noncanonical_amount = False
            for row in trades:
                trade_key = f"{observation_id}:{row['trade_id']}"
                if (
                    not _valid_market_identity(
                        row["venue"], row["venue_market_id"], row["ticker"]
                    )
                    or row["venue"] != observation["venue"]
                    or row["venue_market_id"] != observation["venue_market_id"]
                ):
                    failures.append(f"trade_identity:{trade_key}")
                    invalid_trade_identity += 1
                if row["ticker"] != observation["alias"]:
                    linked_trade_alias_drifts += 1

                trade_timestamps_invalid = False
                try:
                    settled_at = _parse_datetime(row["settled_at"])
                    resolved_ts = _parse_datetime(row["resolved_ts"])
                    applied_at = observation_applied_at.get(observation_id)
                    if applied_at is not None and (
                        settled_at != resolved_ts or settled_at != applied_at
                    ):
                        trade_timestamps_invalid = True
                except ValueError:
                    trade_timestamps_invalid = True
                if trade_timestamps_invalid:
                    failures.append(f"trade_timestamps:{trade_key}")
                    invalid_trade_timestamps += 1

                financials_invalid = False
                try:
                    contracts = _parse_legacy_decimal(row["contracts"])
                    price_cents = _parse_legacy_decimal(row["price_cents"])
                    gross_payout = _parse_decimal(row["gross_payout_cents"])
                    gross_pnl = _parse_decimal(row["gross_pnl_cents"])
                    cost_cents = _parse_legacy_decimal(row["cost_dollars"]) * 100
                    if (
                        contracts != contracts.to_integral_value()
                        or contracts <= 0
                    ):
                        financials_invalid = True
                    if (
                        price_cents != price_cents.to_integral_value()
                        or price_cents < 1
                        or price_cents > 99
                    ):
                        financials_invalid = True
                    if cost_cents != contracts * price_cents:
                        financials_invalid = True

                    if canonical_observation is None:
                        financials_invalid = True
                    elif canonical_observation.outcome is MarketOutcome.VOID:
                        void_refund = canonical_observation.void_refund
                        if void_refund is None:
                            raise ValueError("void observation is missing refund semantics")
                        expected_payout = (
                            contracts * void_refund.refund_cents_per_contract
                        )
                        if gross_payout != expected_payout:
                            financials_invalid = True
                    else:
                        expected_payout = (
                            contracts * 100
                            if row["side"] == canonical_observation.outcome.value
                            else Decimal("0")
                        )
                        if gross_payout != expected_payout:
                            financials_invalid = True
                    if gross_pnl != gross_payout - cost_cents:
                        financials_invalid = True
                    if row["pnl_dollars"] is None or (
                        _parse_legacy_decimal(row["pnl_dollars"]) * 100
                        != gross_pnl
                    ):
                        financials_invalid = True
                    trade_payout += gross_payout
                except ValueError:
                    financials_invalid = True
                    noncanonical_amount = True
                if financials_invalid:
                    failures.append(f"trade_financials:{trade_key}")
                    invalid_trade_financials += 1

                expected_resolved_yes: int | None
                if canonical_observation is None:
                    expected_resolved_yes = None
                    expected_terminal_state = None
                elif canonical_observation.outcome is MarketOutcome.VOID:
                    expected_resolved_yes = None
                    expected_terminal_state = "void"
                else:
                    expected_resolved_yes = int(
                        canonical_observation.outcome is MarketOutcome.YES
                    )
                    expected_terminal_state = (
                        "won"
                        if row["side"] == canonical_observation.outcome.value
                        else "lost"
                    )
                if (
                    row["resolved"] != 1
                    or row["resolved_yes"] != expected_resolved_yes
                    or row["side"] not in {"yes", "no"}
                    or row["terminal_state"] != expected_terminal_state
                ):
                    failures.append(f"trade_outcome:{trade_key}")
                    invalid_trade_outcomes += 1

                if canonical_observation is not None:
                    trade_event = dict(row)
                    trade_event["won"] = (
                        None
                        if canonical_observation.outcome is MarketOutcome.VOID
                        else row["terminal_state"] == "won"
                    )
                    expected_outbox_inputs[(observation_id, row["trade_id"])] = (
                        canonical_observation,
                        trade_event,
                        str(observation["applied_at"]),
                    )

            try:
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
                noncanonical_amount = True
            if noncanonical_amount:
                failures.append(f"noncanonical_amount:{observation_id}")
            else:
                if trade_payout != observation_payout:
                    failures.append(f"trade_payout:{observation_id}")
                if bankroll_before + observation_payout != bankroll_after:
                    failures.append(f"bankroll:{observation_id}")
            if any(
                row["resolved"] != 1
                or row["identity_status"] != "mapped"
                or row["terminal_state"] not in {"won", "lost", "void"}
                or not row["settled_at"]
                for row in trades
            ):
                failures.append(f"linked_trade_state:{observation_id}")

        observation_histories: dict[
            tuple[Venue, str], list[tuple[SettlementObservation, datetime]]
        ] = {}
        for reconstructed, applied_at in reconstructed_observations.values():
            history_key = (
                reconstructed.market_ref.venue,
                reconstructed.market_ref.venue_market_id,
            )
            observation_histories.setdefault(history_key, []).append(
                (reconstructed, applied_at)
            )

        invalid_supersessions: set[str] = set()
        for history in observation_histories.values():
            history.sort(key=lambda item: (item[1], item[0].observation_sha256))
            previous: tuple[SettlementObservation, datetime] | None = None
            for current, current_applied_at in history:
                supersession_invalid = False
                if previous is None:
                    try:
                        validate_observation_transition(None, current)
                    except (RuntimeError, ValueError):
                        supersession_invalid = True
                else:
                    previous_observation, previous_applied_at = previous
                    try:
                        validate_observation_transition(previous_observation, current)
                    except (RuntimeError, ValueError):
                        supersession_invalid = True
                    if (
                        previous_observation.market_ref != current.market_ref
                        or previous_applied_at >= current_applied_at
                    ):
                        supersession_invalid = True
                if supersession_invalid:
                    invalid_supersessions.add(current.observation_sha256)
                previous = (current, current_applied_at)

        for observation_id in sorted(invalid_supersessions):
            failures.append(f"observation_supersession:{observation_id}")

        outboxes = self._conn.execute(
            """
            SELECT outbox_id, event_version, event_kind, observation_sha256,
                   trade_id, payload_json, created_at
            FROM paper_settlement_outbox
            ORDER BY outbox_id
            """
        ).fetchall()
        outboxes_by_link: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for outbox in outboxes:
            link = (outbox["observation_sha256"], outbox["trade_id"])
            outboxes_by_link.setdefault(link, []).append(outbox)

        requirements_by_outbox: dict[str, set[str]] = {}
        requirement_rows = self._conn.execute(
            """
            SELECT outbox_id, consumer_name
            FROM paper_settlement_outbox_requirements
            ORDER BY outbox_id, consumer_name
            """
        ).fetchall()
        for requirement in requirement_rows:
            requirements_by_outbox.setdefault(requirement["outbox_id"], set()).add(
                requirement["consumer_name"]
            )

        for link, expected_input in sorted(expected_outbox_inputs.items()):
            linked_outboxes = outboxes_by_link.get(link, [])
            if len(linked_outboxes) != 1:
                failures.append(f"outbox_count:{link[0]}:{link[1]}")
                invalid_settlement_outboxes += 1
                continue

            outbox = linked_outboxes[0]
            outbox_contract_invalid = False
            observation, trade_event, expected_created_at = expected_input
            try:
                keyword_directions = _persisted_keyword_directions(
                    str(outbox["payload_json"]),
                    trade_event,
                )
                expected = paper_trade_settled_outbox_contract(
                    observation,
                    trade_event,
                    created_at=expected_created_at,
                    keyword_directions=keyword_directions,
                )
            except (KeyError, RuntimeError, TypeError, ValueError):
                expected = None
                outbox_contract_invalid = True
            try:
                created_at = _parse_datetime(outbox["created_at"])
            except ValueError:
                outbox_contract_invalid = True
            else:
                applied_at = observation_applied_at.get(link[0])
                if applied_at is None or created_at != applied_at:
                    outbox_contract_invalid = True
            if expected is None or (
                outbox["outbox_id"] != expected.outbox_id
                or outbox["event_version"] != expected.event_version
                or outbox["event_kind"] != expected.event_kind
                or outbox["observation_sha256"] != expected.observation_sha256
                or outbox["trade_id"] != expected.trade_id
                or outbox["payload_json"] != expected.payload_json
            ):
                outbox_contract_invalid = True
            if outbox_contract_invalid:
                failures.append(f"outbox_contract:{outbox['outbox_id']}")
                invalid_settlement_outboxes += 1

            actual_requirements = requirements_by_outbox.get(
                outbox["outbox_id"], set()
            )
            expected_requirements = (
                PAPER_TRADE_SETTLED_VOID_REQUIREMENTS
                if observation.outcome is MarketOutcome.VOID
                else PAPER_TRADE_SETTLED_DIRECTIONAL_REQUIREMENTS
            )
            if actual_requirements != set(expected_requirements):
                failures.append(f"outbox_requirements:{outbox['outbox_id']}")
                invalid_settlement_outbox_requirements += 1

        for link, linked_outboxes in sorted(outboxes_by_link.items()):
            if link in expected_outbox_inputs:
                continue
            for outbox in linked_outboxes:
                failures.append(f"outbox_unlinked:{outbox['outbox_id']}")
                invalid_settlement_outboxes += 1

        metrics["linked_trades"] = linked_trades
        metrics["invalid_observation_identities"] = invalid_observation_identities
        metrics["invalid_observation_timestamps"] = invalid_observation_timestamps
        metrics["invalid_observation_semantics"] = invalid_observation_semantics
        metrics["invalid_observation_supersessions"] = len(invalid_supersessions)
        metrics["invalid_linked_trade_identity"] = invalid_trade_identity
        metrics["linked_trade_alias_drifts"] = linked_trade_alias_drifts
        metrics["invalid_linked_trade_financials"] = invalid_trade_financials
        metrics["invalid_linked_trade_outcomes"] = invalid_trade_outcomes
        metrics["invalid_linked_trade_timestamps"] = invalid_trade_timestamps
        metrics["settlement_outboxes"] = len(outboxes)
        metrics["invalid_settlement_outboxes"] = invalid_settlement_outboxes
        metrics["invalid_settlement_outbox_requirements"] = (
            invalid_settlement_outbox_requirements
        )

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

        receipts = self._conn.execute(
            """
            SELECT consumer_name, outbox_id, processed_at, result_sha256
            FROM paper_settlement_consumer_receipts
            ORDER BY consumer_name, outbox_id
            """
        ).fetchall()
        invalid_receipts = 0
        for receipt in receipts:
            receipt_invalid = False
            receipt_key = f"{receipt['consumer_name']}:{receipt['outbox_id']}"
            try:
                _parse_datetime(receipt["processed_at"])
            except ValueError:
                failures.append(f"receipt_processed_at:{receipt_key}")
                receipt_invalid = True
            if receipt["result_sha256"] != settlement_result_sha256(
                receipt["outbox_id"], receipt["consumer_name"]
            ):
                failures.append(f"receipt_result_sha256:{receipt_key}")
                receipt_invalid = True
            if receipt_invalid:
                invalid_receipts += 1
        metrics["consumer_receipts"] = len(receipts)
        metrics["invalid_consumer_receipts"] = invalid_receipts

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


def _foreign_key_violation_count(conn: sqlite3.Connection) -> int:
    return len(conn.execute("PRAGMA foreign_key_check").fetchall())


def _reconstruct_observation(
    row: sqlite3.Row,
    *,
    observed_at: datetime,
    effective_at: datetime,
) -> SettlementObservation:
    outcome = MarketOutcome(row["outcome"])
    void_refund = None
    if outcome is MarketOutcome.VOID:
        if row["refunds_entry_fee"] not in {0, 1}:
            raise ValueError("void observation requires an explicit fee refund flag")
        void_refund = VoidRefundContract(
            refund_cents_per_contract=_parse_decimal(
                row["refund_cents_per_contract"]
            ),
            refunds_entry_fee=bool(row["refunds_entry_fee"]),
        )
    elif (
        row["refund_cents_per_contract"] is not None
        or row["refunds_entry_fee"] is not None
    ):
        raise ValueError("directional observation cannot carry void refund fields")

    return SettlementObservation(
        market_ref=MarketRef(
            Venue(row["venue"]),
            row["venue_market_id"],
            row["alias"],
        ),
        outcome=outcome,
        authoritative_outcome_json=row["authoritative_outcome_json"],
        canonical_payload_json=row["canonical_payload_json"],
        observed_at=observed_at,
        effective_at=effective_at,
        rules_version=row["rules_version"],
        source_id=row["source_id"],
        payload_sha256=row["payload_sha256"],
        observation_sha256=row["observation_sha256"],
        void_refund=void_refund,
        supersedes_observation_sha256=row["supersedes_observation_sha256"],
    )


def _valid_market_identity(venue: object, market_id: object, alias: object) -> bool:
    return (
        venue in {Venue.KALSHI.value, Venue.POLYMARKET_US.value}
        and isinstance(market_id, str)
        and bool(market_id.strip())
        and isinstance(alias, str)
        and bool(alias.strip())
    )


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


def _parse_legacy_decimal(value: object) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("legacy amount must be numeric")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("legacy amount must be numeric") from exc
    if not parsed.is_finite():
        raise ValueError("legacy amount must be finite")
    return parsed


def _settlement_decimal_text(value: object) -> str:
    parsed = _parse_legacy_decimal(value)
    if parsed == 0:
        return "0"
    return format(parsed.normalize(), "f")
