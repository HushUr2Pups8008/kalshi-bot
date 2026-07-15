#!/usr/bin/env python3
"""Plan or explicitly apply the additive paper-accounting schema."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Callable, Iterator


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading.paper_accounting import (  # noqa: E402
    PAPER_ACCOUNTING_DDL_SHA256,
    PAPER_ACCOUNTING_SCHEMA_VERSION,
    PAPER_ACCOUNTING_VERSION,
    PaperAccountingSchemaError,
    accounting_schema_state,
    initialize_fresh_paper_accounting_schema,
    paper_accounting_schema_contract_matches,
)
from trading.settlement_store import enable_and_verify_foreign_keys  # noqa: E402


@dataclass(frozen=True)
class PaperAccountingSchemaPlan:
    resolved_db_uri: str
    sqlite_schema_sha256: str
    paper_trades_rows_sha256: str
    paper_accounting_state_sha256: str
    schema_version: int
    accounting_version: int
    ddl_sha256: str
    action: str
    fingerprint: str

    def to_json(self) -> str:
        return json.dumps(
            asdict(self),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


@contextmanager
def open_readonly(path: Path) -> Iterator[sqlite3.Connection]:
    resolved_path = path.expanduser().resolve()
    conn = sqlite3.connect(f"{resolved_path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    enable_and_verify_foreign_keys(conn)
    conn.execute("PRAGMA query_only=ON")
    try:
        yield conn
    finally:
        conn.close()


def sqlite_schema_sha256(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_schema
        ORDER BY type, name, tbl_name
        """
    ).fetchall()
    return _sha256_json([[_canonical_sqlite_value(value) for value in row] for row in rows])


def paper_trades_rows_sha256(conn: sqlite3.Connection) -> str:
    columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(paper_trades)")]
    if not columns or "trade_id" not in columns:
        raise PaperAccountingSchemaError("paper_trades table with trade_id is required")
    rows = conn.execute("SELECT * FROM paper_trades ORDER BY trade_id").fetchall()
    return _sha256_json(
        {
            "columns": columns,
            "rows": [[_canonical_sqlite_value(row[index]) for index in range(len(columns))] for row in rows],
        }
    )


def paper_accounting_state_sha256(conn: sqlite3.Connection) -> str:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name IN "
            "('paper_accounting_schema_meta','paper_trade_accounting')"
        )
    }
    if not tables:
        return _sha256_json({"state": "absent"})
    state: dict[str, object] = {}
    for table, order_by in (
        ("paper_accounting_schema_meta", "schema_version"),
        ("paper_trade_accounting", "accounting_id"),
    ):
        if table not in tables:
            state[table] = {"state": "absent"}
            continue
        columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall()
        state[table] = {
            "columns": columns,
            "rows": [[_canonical_sqlite_value(row[index]) for index in range(len(columns))] for row in rows],
        }
    return _sha256_json(state)


def plan_paper_accounting_schema(
    conn: sqlite3.Connection,
    db_path: Path,
) -> PaperAccountingSchemaPlan:
    resolved_path = db_path.expanduser().resolve()
    database_row = next(
        (row for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
        None,
    )
    if database_row is None or not database_row[2] or Path(database_row[2]).resolve() != resolved_path:
        raise RuntimeError("database path mismatch between connection and plan")
    action = accounting_schema_state(conn)
    payload: dict[str, object] = {
        "resolved_db_uri": resolved_path.as_uri(),
        "sqlite_schema_sha256": sqlite_schema_sha256(conn),
        "paper_trades_rows_sha256": paper_trades_rows_sha256(conn),
        "paper_accounting_state_sha256": paper_accounting_state_sha256(conn),
        "schema_version": PAPER_ACCOUNTING_SCHEMA_VERSION,
        "accounting_version": PAPER_ACCOUNTING_VERSION,
        "ddl_sha256": PAPER_ACCOUNTING_DDL_SHA256,
        "action": action,
    }
    return PaperAccountingSchemaPlan(
        **payload,
        fingerprint=_sha256_json(payload),
    )


def apply_paper_accounting_schema(
    db_path: Path,
    plan: PaperAccountingSchemaPlan,
    *,
    reviewed_plan_fingerprint: str | None,
    fault_hook: Callable[[str], None] | None = None,
) -> None:
    expected_fingerprint = _plan_fingerprint(plan)
    if reviewed_plan_fingerprint != plan.fingerprint or plan.fingerprint != expected_fingerprint:
        raise ValueError("--apply requires the exact reviewed plan fingerprint")
    resolved_path = db_path.expanduser().resolve()
    if resolved_path.as_uri() != plan.resolved_db_uri:
        raise RuntimeError("resolved database path drift")

    conn = sqlite3.connect(resolved_path, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    enable_and_verify_foreign_keys(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if sqlite_schema_sha256(conn) != plan.sqlite_schema_sha256:
            raise RuntimeError("sqlite_schema drift")
        if paper_trades_rows_sha256(conn) != plan.paper_trades_rows_sha256:
            raise RuntimeError("paper_trades row drift")
        if paper_accounting_state_sha256(conn) != plan.paper_accounting_state_sha256:
            raise RuntimeError("paper-accounting state drift")
        current_action = accounting_schema_state(conn)
        if current_action != plan.action:
            raise RuntimeError("paper-accounting schema state drift")
        if plan.action == "apply":
            initialize_fresh_paper_accounting_schema(
                conn,
                migration_plan_sha256=plan.fingerprint,
                applied_at=datetime.now(timezone.utc).isoformat(),
                fault_hook=fault_hook,
            )
            if accounting_schema_state(conn) != "noop":
                raise RuntimeError("paper-accounting schema verification failed")
        elif plan.action == "noop":
            if not paper_accounting_schema_contract_matches(conn):
                raise RuntimeError("paper-accounting no-op contract drift")
        else:
            raise RuntimeError(f"unsupported migration action: {plan.action}")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("foreign-key violation after paper-accounting migration")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _plan_fingerprint(plan: PaperAccountingSchemaPlan) -> str:
    payload = asdict(plan)
    payload.pop("fingerprint")
    return _sha256_json(payload)


def _canonical_sqlite_value(value: object) -> dict[str, object]:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bytes):
        return {"type": "blob", "value": value.hex()}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite SQLite REAL cannot be fingerprinted")
        return {"type": "real", "value": value.hex()}
    raise TypeError(f"unsupported SQLite value type: {type(value).__name__}")


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "paper_trades.db")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reviewed-plan-fingerprint")
    args = parser.parse_args()
    if args.apply and not args.reviewed_plan_fingerprint:
        parser.error("--apply requires --reviewed-plan-fingerprint")
    return args


def main() -> int:
    args = _parse_args()
    db_path = args.db.expanduser().resolve()
    with open_readonly(db_path) as conn:
        plan = plan_paper_accounting_schema(conn, db_path)
    print(plan.to_json())
    if args.apply:
        apply_paper_accounting_schema(
            db_path,
            plan,
            reviewed_plan_fingerprint=args.reviewed_plan_fingerprint,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
