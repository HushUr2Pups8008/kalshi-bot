"""Immutable cutover records for legacy paper settlements without receipts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import sqlite3


LEGACY_RESOLUTION_REASON_CODE = "precanonical_unattested_resolution"
_MANIFEST_TABLE = "paper_settlement_legacy_cutover_manifest"
_EXEMPTION_TABLE = "paper_settlement_legacy_resolution_exemptions"
_MANIFEST_ID = 1
_SHA256_LENGTH = 64
_CUTOVER_SNAPSHOT_VERSION = 1
_DATABASE_CONTENT_SNAPSHOT_VERSION = 1
# Freeze only fields that establish the legacy accounting exception. This is
# deliberately not ``SELECT *``: additive paper-trade metadata must not strand
# an otherwise valid immutable exception at the next startup preflight.
_CUTOVER_SNAPSHOT_COLUMNS = (
    "trade_id",
    "ticker",
    "venue",
    "resolved",
    "resolved_yes",
    "resolved_ts",
    "venue_market_id",
    "identity_status",
    "quarantine_reason",
    "side",
    "contracts",
    "price_cents",
    "cost_dollars",
    "pnl_dollars",
    "ts",
    "estimated_prob",
    "entry_price_cents",
    "signal_source",
    "keywords_matched",
    "series_ticker",
    "llm_magnitude",
    "llm_confidence",
    "fast_lane_p",
    "accumulation_p",
    "structural_p",
    "terminal_state",
    "settlement_observation_sha256",
    "settled_at",
    "gross_payout_cents",
    "gross_pnl_cents",
)
_BOT_STATE_SNAPSHOT_KEYS = ("notional_bankroll", "go_live_confirmed")
_CANONICAL_ARTIFACT_TABLES = (
    "paper_settlement_observations",
    "paper_settlement_quarantine",
    "paper_settlement_outbox",
    "paper_settlement_outbox_requirements",
    "paper_settlement_consumer_receipts",
    "paper_settlement_delivery_claims",
)

_CUTOVER_TABLE_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        _MANIFEST_TABLE,
        f"""
        CREATE TABLE IF NOT EXISTS {_MANIFEST_TABLE} (
            manifest_id INTEGER PRIMARY KEY CHECK (manifest_id = {_MANIFEST_ID}),
            manifest_sha256 TEXT NOT NULL UNIQUE CHECK (
                length(manifest_sha256) = {_SHA256_LENGTH}
            ),
            legacy_trade_count INTEGER NOT NULL CHECK (legacy_trade_count > 0),
            created_at TEXT NOT NULL
        )
        """,
    ),
    (
        _EXEMPTION_TABLE,
        f"""
        CREATE TABLE IF NOT EXISTS {_EXEMPTION_TABLE} (
            trade_id TEXT PRIMARY KEY REFERENCES paper_trades(trade_id),
            reason_code TEXT NOT NULL CHECK (
                reason_code = '{LEGACY_RESOLUTION_REASON_CODE}'
            ),
            row_snapshot_sha256 TEXT NOT NULL CHECK (
                length(row_snapshot_sha256) = {_SHA256_LENGTH}
            ),
            row_snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
    ),
)


def _immutable_trigger_statements() -> tuple[tuple[str, str], ...]:
    statements: list[tuple[str, str]] = []
    for table, _statement in _CUTOVER_TABLE_STATEMENTS:
        for operation in ("UPDATE", "DELETE"):
            name = f"immutable_{table}_{operation.lower()}"
            statements.append(
                (
                    name,
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {name}
                    BEFORE {operation} ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, '{table} is append-only');
                    END
                    """,
                )
            )
    return tuple(statements)


LEGACY_CUTOVER_STATEMENTS = _CUTOVER_TABLE_STATEMENTS + _immutable_trigger_statements()


@dataclass(frozen=True)
class LegacySettlementCutoverEntry:
    trade_id: str
    row_snapshot_sha256: str
    row_snapshot_json: str


@dataclass(frozen=True)
class LegacySettlementCutoverPlan:
    resolved_db_uri: str
    sqlite_schema_sha256: str
    open_rows_sha256: str
    unattested_resolved_rows_sha256: str
    unattested_resolved_count: int
    non_mapped_unattested_count: int
    bot_state_snapshot_json: str
    bot_state_snapshot_sha256: str
    database_content_sha256: str
    entries: tuple[LegacySettlementCutoverEntry, ...]
    manifest_sha256: str
    state_fingerprint: str
    fingerprint: str

    @property
    def legacy_trade_count(self) -> int:
        return len(self.entries)

    def to_json(self) -> str:
        return json.dumps(asdict(self), allow_nan=False, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, value: str) -> "LegacySettlementCutoverPlan":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid legacy settlement cutover plan JSON") from exc
        expected_keys = {
            "resolved_db_uri",
            "sqlite_schema_sha256",
            "open_rows_sha256",
            "unattested_resolved_rows_sha256",
            "unattested_resolved_count",
            "non_mapped_unattested_count",
            "bot_state_snapshot_json",
            "bot_state_snapshot_sha256",
            "database_content_sha256",
            "entries",
            "manifest_sha256",
            "state_fingerprint",
            "fingerprint",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise ValueError("legacy settlement cutover plan has an invalid shape")
        raw_entries = payload["entries"]
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError("legacy settlement cutover plan has no entries")
        entries: list[LegacySettlementCutoverEntry] = []
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict) or set(raw_entry) != {
                "trade_id",
                "row_snapshot_sha256",
                "row_snapshot_json",
            }:
                raise ValueError("legacy settlement cutover plan has an invalid entry")
            try:
                trade_id = str(raw_entry["trade_id"])
                snapshot_sha256 = str(raw_entry["row_snapshot_sha256"])
                snapshot_json = str(raw_entry["row_snapshot_json"])
                canonical_snapshot = _canonical_json(json.loads(snapshot_json))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("legacy settlement cutover plan has an invalid snapshot") from exc
            if (
                not trade_id
                or canonical_snapshot != snapshot_json
                or len(snapshot_sha256) != _SHA256_LENGTH
                or hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
                != snapshot_sha256
            ):
                raise ValueError("legacy settlement cutover plan has an invalid snapshot")
            entries.append(
                LegacySettlementCutoverEntry(
                    trade_id=trade_id,
                    row_snapshot_sha256=snapshot_sha256,
                    row_snapshot_json=snapshot_json,
                )
            )
        entries_tuple = tuple(entries)
        if tuple(sorted(entry.trade_id for entry in entries_tuple)) != tuple(
            entry.trade_id for entry in entries_tuple
        ):
            raise ValueError("legacy settlement cutover plan entries must be unique and sorted")
        string_fields = (
            "resolved_db_uri",
            "sqlite_schema_sha256",
            "open_rows_sha256",
            "unattested_resolved_rows_sha256",
            "bot_state_snapshot_sha256",
            "database_content_sha256",
            "manifest_sha256",
            "state_fingerprint",
            "fingerprint",
        )
        if any(
            not isinstance(payload[field], str)
            or len(payload[field]) != _SHA256_LENGTH
            for field in string_fields
            if field != "resolved_db_uri"
        ) or not isinstance(payload["resolved_db_uri"], str):
            raise ValueError("legacy settlement cutover plan has an invalid fingerprint")
        try:
            bot_state_snapshot = _canonical_json(
                json.loads(str(payload["bot_state_snapshot_json"]))
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("legacy settlement cutover plan has an invalid bot-state snapshot") from exc
        if (
            bot_state_snapshot != payload["bot_state_snapshot_json"]
            or not isinstance(payload["bot_state_snapshot_sha256"], str)
            or hashlib.sha256(bot_state_snapshot.encode("utf-8")).hexdigest()
            != payload["bot_state_snapshot_sha256"]
        ):
            raise ValueError("legacy settlement cutover plan has an invalid bot-state snapshot")
        counts = (
            payload["unattested_resolved_count"],
            payload["non_mapped_unattested_count"],
        )
        if any(type(count) is not int or count < 0 for count in counts):
            raise ValueError("legacy settlement cutover plan has invalid counts")
        manifest_sha256 = _manifest_sha256(entries_tuple)
        if payload["manifest_sha256"] != manifest_sha256:
            raise ValueError("legacy settlement cutover plan manifest does not match entries")
        state_fingerprint = _state_fingerprint(
            sqlite_schema_sha256=payload["sqlite_schema_sha256"],
            open_rows_sha256=payload["open_rows_sha256"],
            unattested_resolved_rows_sha256=payload["unattested_resolved_rows_sha256"],
            unattested_resolved_count=payload["unattested_resolved_count"],
            non_mapped_unattested_count=payload["non_mapped_unattested_count"],
            bot_state_snapshot_sha256=payload["bot_state_snapshot_sha256"],
            database_content_sha256=payload["database_content_sha256"],
            manifest_sha256=manifest_sha256,
            entries=entries_tuple,
        )
        if payload["state_fingerprint"] != state_fingerprint:
            raise ValueError("legacy settlement cutover plan state fingerprint is invalid")
        fingerprint = _plan_fingerprint(
            resolved_db_uri=payload["resolved_db_uri"],
            state_fingerprint=state_fingerprint,
        )
        if payload["fingerprint"] != fingerprint:
            raise ValueError("legacy settlement cutover plan fingerprint is invalid")
        return cls(
            resolved_db_uri=payload["resolved_db_uri"],
            sqlite_schema_sha256=payload["sqlite_schema_sha256"],
            open_rows_sha256=payload["open_rows_sha256"],
            unattested_resolved_rows_sha256=payload[
                "unattested_resolved_rows_sha256"
            ],
            unattested_resolved_count=payload["unattested_resolved_count"],
            non_mapped_unattested_count=payload["non_mapped_unattested_count"],
            bot_state_snapshot_json=bot_state_snapshot,
            bot_state_snapshot_sha256=payload["bot_state_snapshot_sha256"],
            database_content_sha256=payload["database_content_sha256"],
            entries=entries_tuple,
            manifest_sha256=manifest_sha256,
            state_fingerprint=state_fingerprint,
            fingerprint=fingerprint,
        )


@dataclass(frozen=True)
class LegacySettlementCutoverValidation:
    ok: bool
    failures: tuple[str, ...]
    exempt_trade_ids: tuple[str, ...]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("legacy cutover row contains a non-finite numeric value")
        return value
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    raise TypeError(f"unsupported legacy cutover value type: {type(value).__name__}")


def _snapshot_json(row: sqlite3.Row) -> str:
    payload = {
        column: _normalize_json_value(row[column])
        for column in _CUTOVER_SNAPSHOT_COLUMNS
    }
    return _canonical_json(
        {
            "snapshot_version": _CUTOVER_SNAPSHOT_VERSION,
            "trade": payload,
        }
    )


def _snapshot_entry(row: sqlite3.Row) -> LegacySettlementCutoverEntry:
    snapshot_json = _snapshot_json(row)
    return LegacySettlementCutoverEntry(
        trade_id=str(row["trade_id"]),
        row_snapshot_sha256=hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
        row_snapshot_json=snapshot_json,
    )


def _manifest_sha256(entries: tuple[LegacySettlementCutoverEntry, ...]) -> str:
    return _sha256_json(
        {
            "reason_code": LEGACY_RESOLUTION_REASON_CODE,
            "schema_version": 1,
            "entries": [
                {
                    "trade_id": entry.trade_id,
                    "row_snapshot_sha256": entry.row_snapshot_sha256,
                }
                for entry in entries
            ],
        }
    )


def _state_fingerprint(
    *,
    sqlite_schema_sha256: str,
    open_rows_sha256: str,
    unattested_resolved_rows_sha256: str,
    unattested_resolved_count: int,
    non_mapped_unattested_count: int,
    bot_state_snapshot_sha256: str,
    database_content_sha256: str,
    manifest_sha256: str,
    entries: tuple[LegacySettlementCutoverEntry, ...],
) -> str:
    return _sha256_json(
        {
            "sqlite_schema_sha256": sqlite_schema_sha256,
            "open_rows_sha256": open_rows_sha256,
            "unattested_resolved_rows_sha256": unattested_resolved_rows_sha256,
            "unattested_resolved_count": unattested_resolved_count,
            "non_mapped_unattested_count": non_mapped_unattested_count,
            "bot_state_snapshot_sha256": bot_state_snapshot_sha256,
            "database_content_sha256": database_content_sha256,
            "manifest_sha256": manifest_sha256,
            "entries": [asdict(entry) for entry in entries],
        }
    )


def _plan_fingerprint(*, resolved_db_uri: str, state_fingerprint: str) -> str:
    return _sha256_json(
        {
            "resolved_db_uri": resolved_db_uri,
            "state_fingerprint": state_fingerprint,
        }
    )


def _schema_sha256(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT type, name, tbl_name, COALESCE(sql, '') AS sql
        FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return _sha256_json([tuple(row) for row in rows])


def _open_rows_sha256(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT trade_id, ticker, venue, venue_market_id, identity_status,
               resolved, ts, contracts, price_cents, cost_dollars
        FROM paper_trades
        WHERE resolved=0
        ORDER BY trade_id
        """
    ).fetchall()
    return _sha256_json([tuple(row) for row in rows])


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _database_content_sha256(conn: sqlite3.Connection) -> str:
    """Hash every user table deterministically for backup equivalence checks."""

    table_rows = conn.execute(
        """
        SELECT name
        FROM sqlite_schema
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    tables: list[dict[str, object]] = []
    for table_row in table_rows:
        table_name = str(table_row["name"])
        quoted_table = _quote_identifier(table_name)
        columns = [
            str(column_row["name"])
            for column_row in conn.execute(f"PRAGMA table_info({quoted_table})").fetchall()
        ]
        if not columns:
            raise RuntimeError(f"database snapshot cannot read columns for {table_name}")
        projection = ", ".join(_quote_identifier(column) for column in columns)
        ordering = ", ".join(_quote_identifier(column) for column in columns)
        rows = conn.execute(
            f"SELECT {projection} FROM {quoted_table} ORDER BY {ordering}"
        ).fetchall()
        tables.append(
            {
                "name": table_name,
                "columns": columns,
                "rows": [
                    [_normalize_json_value(value) for value in tuple(row)]
                    for row in rows
                ],
            }
        )
    return _sha256_json(
        {
            "snapshot_version": _DATABASE_CONTENT_SNAPSHOT_VERSION,
            "tables": tables,
        }
    )


def _bot_state_snapshot(conn: sqlite3.Connection) -> tuple[str, str]:
    """Return the reviewed sizing/go-live state without mutating it."""

    snapshot: dict[str, str | None] = {
        key: None for key in _BOT_STATE_SNAPSHOT_KEYS
    }
    if _table_exists(conn, "bot_state"):
        rows = conn.execute(
            "SELECT key, value FROM bot_state WHERE key IN (?, ?)",
            _BOT_STATE_SNAPSHOT_KEYS,
        ).fetchall()
    else:
        rows = ()
    for row in rows:
        snapshot[str(row["key"])] = str(row["value"])
    snapshot_json = _canonical_json(snapshot)
    return snapshot_json, hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()


def _unattested_resolved_scope(
    conn: sqlite3.Connection,
) -> tuple[str, int, int]:
    """Return a full legacy outcome scope, not only cutover-eligible rows."""
    rows = conn.execute(
        """
        SELECT trade.trade_id, trade.identity_status, trade.resolved_yes,
               trade.pnl_dollars, trade.ts, trade.settlement_observation_sha256
        FROM paper_trades AS trade
        LEFT JOIN paper_settlement_observations AS observation
          ON observation.observation_sha256 = trade.settlement_observation_sha256
        WHERE trade.resolved=1
          AND observation.observation_sha256 IS NULL
        ORDER BY trade.trade_id
        """
    ).fetchall()
    non_mapped = sum(
        1 for row in rows if str(row["identity_status"] or "") != "mapped"
    )
    return _sha256_json([tuple(row) for row in rows]), len(rows), non_mapped


def _unlinked_mapped_resolved_rows(
    conn: sqlite3.Connection,
) -> tuple[sqlite3.Row, ...]:
    projection = ", ".join(f"trade.{column}" for column in _CUTOVER_SNAPSHOT_COLUMNS)
    return tuple(
        conn.execute(
            f"""
            SELECT {projection}
            FROM paper_trades AS trade
            WHERE trade.resolved=1
              AND trade.identity_status='mapped'
              AND trade.settlement_observation_sha256 IS NULL
            ORDER BY trade.trade_id
            """
        ).fetchall()
    )


def _dangling_mapped_resolved_observation_trade_ids(
    conn: sqlite3.Connection,
) -> tuple[str, ...]:
    return tuple(
        str(row["trade_id"])
        for row in conn.execute(
            """
            SELECT trade.trade_id
            FROM paper_trades AS trade
            LEFT JOIN paper_settlement_observations AS observation
              ON observation.observation_sha256 = trade.settlement_observation_sha256
            WHERE trade.resolved=1
              AND trade.identity_status='mapped'
              AND trade.settlement_observation_sha256 IS NOT NULL
              AND observation.observation_sha256 IS NULL
            ORDER BY trade.trade_id
            """
        ).fetchall()
    )


def _has_partial_canonical_artifacts(row: sqlite3.Row) -> bool:
    return any(
        row[column] is not None
        for column in (
            "terminal_state",
            "settlement_observation_sha256",
            "settled_at",
            "gross_payout_cents",
            "gross_pnl_cents",
        )
    )


def _object_rows(conn: sqlite3.Connection) -> tuple[sqlite3.Row, ...]:
    names = tuple(name for name, _statement in LEGACY_CUTOVER_STATEMENTS)
    placeholders = ", ".join("?" for _ in names)
    return tuple(
        conn.execute(
            f"""
            SELECT type, name, tbl_name, COALESCE(sql, '') AS sql
            FROM sqlite_schema
            WHERE name IN ({placeholders})
            ORDER BY type, name
            """,
            names,
        ).fetchall()
    )


def _normalize_schema_sql(value: str) -> str:
    return " ".join(value.lower().replace("if not exists ", "").split())


def _cutover_contract_signature(conn: sqlite3.Connection) -> str:
    rows = [
        {
            "type": str(row["type"]),
            "name": str(row["name"]),
            "tbl_name": str(row["tbl_name"]),
            "sql": _normalize_schema_sql(str(row["sql"])),
        }
        for row in _object_rows(conn)
    ]
    return _sha256_json(rows)


@lru_cache(maxsize=1)
def _expected_cutover_contract_signature() -> str:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("CREATE TABLE paper_trades (trade_id TEXT PRIMARY KEY)")
        for _name, statement in LEGACY_CUTOVER_STATEMENTS:
            conn.execute(statement)
        return _cutover_contract_signature(conn)
    finally:
        conn.close()


def legacy_cutover_contract_matches(conn: sqlite3.Connection) -> bool:
    try:
        return _cutover_contract_signature(conn) == _expected_cutover_contract_signature()
    except sqlite3.DatabaseError:
        return False


def _cutover_objects_present(conn: sqlite3.Connection) -> bool:
    return bool(_object_rows(conn))


def _canonical_artifact_count(conn: sqlite3.Connection) -> int:
    return sum(
        int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in _CANONICAL_ARTIFACT_TABLES
    )


def plan_legacy_settlement_cutover(
    conn: sqlite3.Connection,
    db_path: Path | str,
) -> LegacySettlementCutoverPlan:
    """Capture the exact current set of unlinked mapped legacy resolutions."""
    if _cutover_objects_present(conn):
        raise RuntimeError("legacy settlement cutover already has schema objects")
    if _canonical_artifact_count(conn):
        raise RuntimeError(
            "legacy settlement cutover cannot follow canonical settlement artifacts"
        )
    if _dangling_mapped_resolved_observation_trade_ids(conn):
        raise RuntimeError("legacy settlement cutover has dangling canonical observation references")
    rows = _unlinked_mapped_resolved_rows(conn)
    if not rows:
        raise RuntimeError("legacy settlement cutover has no eligible rows")
    if any(_has_partial_canonical_artifacts(row) for row in rows):
        raise RuntimeError("legacy settlement cutover has partial canonical artifacts")
    entries = tuple(_snapshot_entry(row) for row in rows)
    manifest_sha256 = _manifest_sha256(entries)
    resolved_db_uri = Path(db_path).expanduser().resolve().as_uri()
    sqlite_schema_sha256 = _schema_sha256(conn)
    open_rows_sha256 = _open_rows_sha256(conn)
    (
        unattested_resolved_rows_sha256,
        unattested_resolved_count,
        non_mapped_unattested_count,
    ) = _unattested_resolved_scope(conn)
    bot_state_snapshot_json, bot_state_snapshot_sha256 = _bot_state_snapshot(conn)
    database_content_sha256 = _database_content_sha256(conn)
    state_fingerprint = _state_fingerprint(
        sqlite_schema_sha256=sqlite_schema_sha256,
        open_rows_sha256=open_rows_sha256,
        unattested_resolved_rows_sha256=unattested_resolved_rows_sha256,
        unattested_resolved_count=unattested_resolved_count,
        non_mapped_unattested_count=non_mapped_unattested_count,
        bot_state_snapshot_sha256=bot_state_snapshot_sha256,
        database_content_sha256=database_content_sha256,
        manifest_sha256=manifest_sha256,
        entries=entries,
    )
    return LegacySettlementCutoverPlan(
        resolved_db_uri=resolved_db_uri,
        sqlite_schema_sha256=sqlite_schema_sha256,
        open_rows_sha256=open_rows_sha256,
        unattested_resolved_rows_sha256=unattested_resolved_rows_sha256,
        unattested_resolved_count=unattested_resolved_count,
        non_mapped_unattested_count=non_mapped_unattested_count,
        bot_state_snapshot_json=bot_state_snapshot_json,
        bot_state_snapshot_sha256=bot_state_snapshot_sha256,
        database_content_sha256=database_content_sha256,
        entries=entries,
        manifest_sha256=manifest_sha256,
        state_fingerprint=state_fingerprint,
        fingerprint=_plan_fingerprint(
            resolved_db_uri=resolved_db_uri,
            state_fingerprint=state_fingerprint,
        ),
    )


def apply_legacy_settlement_cutover(
    db_path: Path | str,
    plan: LegacySettlementCutoverPlan,
    *,
    reviewed_plan_fingerprint: str | None,
    reviewed_trade_ids: tuple[str, ...],
) -> None:
    """Apply a reviewed immutable legacy cutover without changing paper trades."""
    try:
        plan = LegacySettlementCutoverPlan.from_json(plan.to_json())
    except ValueError as exc:
        raise ValueError("legacy settlement cutover plan integrity check failed") from exc
    if reviewed_plan_fingerprint != plan.fingerprint:
        raise ValueError("--apply requires the reviewed plan fingerprint")
    expected_trade_ids = tuple(entry.trade_id for entry in plan.entries)
    if tuple(sorted(set(reviewed_trade_ids))) != expected_trade_ids:
        raise ValueError("--apply requires the exact reviewed trade ids")
    resolved_path = Path(db_path).expanduser().resolve()
    if resolved_path.as_uri() != plan.resolved_db_uri:
        raise RuntimeError("resolved database path drift")
    conn = sqlite3.connect(resolved_path, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("SQLite foreign key enforcement is unavailable")
        integrity = tuple(str(row[0]) for row in conn.execute("PRAGMA integrity_check"))
        if integrity != ("ok",):
            raise RuntimeError("SQLite integrity check failed before legacy cutover")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("SQLite foreign key check failed before legacy cutover")
        conn.execute("BEGIN IMMEDIATE")
        if _cutover_objects_present(conn):
            raise RuntimeError("legacy settlement cutover already has schema objects")
        current_plan = plan_legacy_settlement_cutover(conn, resolved_path)
        if current_plan.fingerprint != plan.fingerprint:
            raise RuntimeError("legacy settlement cutover state drift")
        for _name, statement in LEGACY_CUTOVER_STATEMENTS:
            conn.execute(statement)
        created_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            f"""
            INSERT INTO {_MANIFEST_TABLE} (
                manifest_id, manifest_sha256, legacy_trade_count, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                _MANIFEST_ID,
                plan.manifest_sha256,
                plan.legacy_trade_count,
                created_at,
            ),
        )
        conn.executemany(
            f"""
            INSERT INTO {_EXEMPTION_TABLE} (
                trade_id, reason_code, row_snapshot_sha256, row_snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    entry.trade_id,
                    LEGACY_RESOLUTION_REASON_CODE,
                    entry.row_snapshot_sha256,
                    entry.row_snapshot_json,
                    created_at,
                )
                for entry in plan.entries
            ],
        )
        validation = validate_legacy_settlement_cutover(conn)
        if not validation.ok:
            raise RuntimeError(
                "legacy settlement cutover verification failed: "
                + ",".join(validation.failures)
            )
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("SQLite foreign key check failed after legacy cutover")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate_legacy_settlement_cutover(
    conn: sqlite3.Connection,
) -> LegacySettlementCutoverValidation:
    """Validate a frozen exemption set against the current unlinked rows."""
    rows = _unlinked_mapped_resolved_rows(conn)
    if _dangling_mapped_resolved_observation_trade_ids(conn):
        return LegacySettlementCutoverValidation(
            False,
            ("legacy_cutover_dangling_observation",),
            (),
        )
    objects_present = _cutover_objects_present(conn)
    if not rows and not objects_present:
        return LegacySettlementCutoverValidation(True, (), ())
    if rows and not objects_present:
        return LegacySettlementCutoverValidation(
            False,
            ("legacy_cutover_unavailable",),
            (),
        )
    if not legacy_cutover_contract_matches(conn):
        return LegacySettlementCutoverValidation(
            False,
            ("legacy_cutover_contract",),
            (),
        )
    if any(_has_partial_canonical_artifacts(row) for row in rows):
        return LegacySettlementCutoverValidation(
            False,
            ("legacy_cutover_partial_artifacts",),
            (),
        )

    manifest_rows = conn.execute(
        f"""
        SELECT manifest_id, manifest_sha256, legacy_trade_count
        FROM {_MANIFEST_TABLE}
        """
    ).fetchall()
    exemptions = conn.execute(
        f"""
        SELECT trade_id, reason_code, row_snapshot_sha256, row_snapshot_json
        FROM {_EXEMPTION_TABLE}
        ORDER BY trade_id
        """
    ).fetchall()
    if len(manifest_rows) != 1 or manifest_rows[0]["manifest_id"] != _MANIFEST_ID:
        return LegacySettlementCutoverValidation(
            False,
            ("legacy_cutover_manifest",),
            (),
        )
    current_entries = tuple(_snapshot_entry(row) for row in rows)
    current_by_id = {entry.trade_id: entry for entry in current_entries}
    stored_ids = tuple(str(row["trade_id"]) for row in exemptions)
    if not stored_ids:
        return LegacySettlementCutoverValidation(
            False,
            ("legacy_cutover_manifest",),
            (),
        )
    if set(current_by_id) - set(stored_ids):
        return LegacySettlementCutoverValidation(
            False,
            ("legacy_cutover_trade_set_mismatch",),
            (),
        )
    placeholders = ", ".join("?" for _ in stored_ids)
    stored_trade_rows = conn.execute(
        f"""
        SELECT trade.trade_id, trade.resolved, trade.identity_status,
               trade.settlement_observation_sha256,
               observation.observation_sha256 AS linked_observation_sha256
        FROM paper_trades AS trade
        LEFT JOIN paper_settlement_observations AS observation
          ON observation.observation_sha256 = trade.settlement_observation_sha256
        WHERE trade.trade_id IN ({placeholders})
        """,
        stored_ids,
    ).fetchall()
    stored_trade_by_id = {
        str(row["trade_id"]): row for row in stored_trade_rows
    }
    if set(stored_trade_by_id) != set(stored_ids):
        return LegacySettlementCutoverValidation(
            False,
            ("legacy_cutover_trade_set_mismatch",),
            (),
        )
    for trade_id in stored_ids:
        if trade_id in current_by_id:
            continue
        row = stored_trade_by_id[trade_id]
        if not (
            int(row["resolved"]) == 1
            and row["identity_status"] == "mapped"
            and row["settlement_observation_sha256"] is not None
            and row["linked_observation_sha256"] is not None
        ):
            return LegacySettlementCutoverValidation(
                False,
                ("legacy_cutover_trade_set_mismatch",),
                (),
            )
    expected_entries: list[LegacySettlementCutoverEntry] = []
    for row in exemptions:
        try:
            parsed_snapshot = json.loads(str(row["row_snapshot_json"]))
            canonical_snapshot = _canonical_json(parsed_snapshot)
        except (TypeError, ValueError, json.JSONDecodeError):
            return LegacySettlementCutoverValidation(
                False,
                ("legacy_cutover_snapshot_mismatch",),
                (),
            )
        stored_entry = LegacySettlementCutoverEntry(
            trade_id=str(row["trade_id"]),
            row_snapshot_sha256=str(row["row_snapshot_sha256"]),
            row_snapshot_json=canonical_snapshot,
        )
        current_entry = current_by_id.get(stored_entry.trade_id)
        if (
            row["reason_code"] != LEGACY_RESOLUTION_REASON_CODE
            or stored_entry.row_snapshot_json != str(row["row_snapshot_json"])
            or hashlib.sha256(stored_entry.row_snapshot_json.encode("utf-8")).hexdigest()
            != stored_entry.row_snapshot_sha256
            or (
                current_entry is not None
                and current_entry != stored_entry
            )
        ):
            return LegacySettlementCutoverValidation(
                False,
                ("legacy_cutover_snapshot_mismatch",),
                (),
            )
        expected_entries.append(stored_entry)
    expected_tuple = tuple(expected_entries)
    manifest = manifest_rows[0]
    if (
        int(manifest["legacy_trade_count"]) != len(expected_tuple)
        or str(manifest["manifest_sha256"]) != _manifest_sha256(expected_tuple)
    ):
        return LegacySettlementCutoverValidation(
            False,
            ("legacy_cutover_manifest",),
            (),
        )
    return LegacySettlementCutoverValidation(True, (), tuple(current_by_id))
