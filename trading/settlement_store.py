"""Unwired durable settlement schema and storage primitives."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}
    objects = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type IN ('table','index','trigger')"
        )
    }
    expected_columns = {
        name for name, _definition in SETTLEMENT_PAPER_TRADE_COLUMNS
    }
    expected_objects = {name for name, _statement in SETTLEMENT_TARGET_STATEMENTS}
    return expected_columns <= columns and expected_objects <= objects


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
