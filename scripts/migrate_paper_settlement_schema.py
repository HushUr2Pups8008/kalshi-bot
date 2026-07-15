#!/usr/bin/env python3
"""Plan and explicitly apply the durable paper-settlement schema."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading.settlement_store import (  # noqa: E402
    SETTLEMENT_DDL_SHA256,
    SETTLEMENT_MIGRATION_STATEMENTS,
    SETTLEMENT_PAPER_TRADE_COLUMNS,
    SETTLEMENT_SCHEMA_VERSION,
    SETTLEMENT_TARGET_STATEMENTS,
    enable_and_verify_foreign_keys,
    settlement_schema_contract_matches,
)


@dataclass(frozen=True)
class SettlementSchemaPlan:
    resolved_db_uri: str
    sqlite_schema_sha256: str
    open_rows_sha256: str
    schema_version: int
    ddl_sha256: str
    action: str
    fingerprint: str

    def to_json(self) -> str:
        return json.dumps(
            asdict(self),
            allow_nan=False,
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
    values = [list(row) for row in rows]
    return _sha256_json(values)


def open_rows_sha256(conn: sqlite3.Connection) -> str:
    columns = [row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")]
    if not columns:
        raise RuntimeError("paper_trades table is required")
    rows = conn.execute("SELECT * FROM paper_trades WHERE resolved=0").fetchall()
    fingerprints = sorted(
        ([row[column] for column in columns] for row in rows),
        key=lambda values: str(values[columns.index("trade_id")]),
    )
    return _sha256_json({"columns": columns, "rows": fingerprints})


def plan_settlement_schema(
    conn: sqlite3.Connection,
    db_path: Path,
) -> SettlementSchemaPlan:
    resolved_path = db_path.expanduser().resolve()
    database_row = next(
        (row for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
        None,
    )
    if (
        database_row is None
        or not database_row[2]
        or Path(database_row[2]).resolve() != resolved_path
    ):
        raise RuntimeError("database path mismatch between connection and plan")
    resolved_db_uri = resolved_path.as_uri()
    schema_hash = sqlite_schema_sha256(conn)
    rows_hash = open_rows_sha256(conn)
    action = _migration_action(conn)
    payload = {
        "resolved_db_uri": resolved_db_uri,
        "sqlite_schema_sha256": schema_hash,
        "open_rows_sha256": rows_hash,
        "schema_version": SETTLEMENT_SCHEMA_VERSION,
        "ddl_sha256": SETTLEMENT_DDL_SHA256,
    }
    fingerprint = _sha256_json(payload)
    return SettlementSchemaPlan(
        **payload,
        action=action,
        fingerprint=fingerprint,
    )


def apply_settlement_schema(
    db_path: Path,
    plan: SettlementSchemaPlan,
    *,
    reviewed_plan_fingerprint: str | None,
    fault_hook: Callable[[str], None] | None = None,
) -> None:
    if reviewed_plan_fingerprint != plan.fingerprint:
        raise ValueError("--apply requires the reviewed plan fingerprint")
    resolved_path = db_path.expanduser().resolve()
    if resolved_path.as_uri() != plan.resolved_db_uri:
        raise RuntimeError("resolved database path drift")
    hook = fault_hook or (lambda _stage: None)
    conn = sqlite3.connect(resolved_path, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    enable_and_verify_foreign_keys(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if sqlite_schema_sha256(conn) != plan.sqlite_schema_sha256:
            raise RuntimeError("sqlite_schema drift")
        if open_rows_sha256(conn) != plan.open_rows_sha256:
            raise RuntimeError("open-row drift")
        if plan.action == "apply":
            if _migration_action(conn) != "apply":
                raise RuntimeError("settlement schema state drift")
            for name, statement in SETTLEMENT_MIGRATION_STATEMENTS:
                conn.execute(statement)
                hook(f"after_ddl:{name}")
            conn.execute(
                """
                INSERT INTO paper_settlement_schema_meta (
                    schema_version, ddl_sha256, migration_plan_sha256, applied_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    SETTLEMENT_SCHEMA_VERSION,
                    SETTLEMENT_DDL_SHA256,
                    plan.fingerprint,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            hook("after_meta")
            if _migration_action(conn) != "noop":
                raise RuntimeError("settlement schema verification failed")
        elif plan.action == "noop":
            if _migration_action(conn) != "noop":
                raise RuntimeError("settlement schema state drift")
        else:
            raise RuntimeError(f"unsupported migration action: {plan.action}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migration_action(conn: sqlite3.Connection) -> str:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}
    objects = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type IN ('table','index','trigger')"
        )
    }
    target_columns = {name for name, _definition in SETTLEMENT_PAPER_TRADE_COLUMNS}
    target_objects = {name for name, _statement in SETTLEMENT_TARGET_STATEMENTS}
    meta_exists = "paper_settlement_schema_meta" in objects
    artifacts_present = bool(columns & target_columns or objects & target_objects)
    if not artifacts_present:
        return "apply"
    if not meta_exists:
        raise RuntimeError("partial settlement schema without schema meta")
    meta = conn.execute(
        """
        SELECT schema_version, ddl_sha256 FROM paper_settlement_schema_meta
        """
    ).fetchall()
    if (
        len(meta) != 1
        or meta[0]["schema_version"] != SETTLEMENT_SCHEMA_VERSION
        or meta[0]["ddl_sha256"] != SETTLEMENT_DDL_SHA256
        or not settlement_schema_contract_matches(conn)
    ):
        raise RuntimeError("settlement schema does not match target contract")
    return "noop"


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
        plan = plan_settlement_schema(conn, db_path)
    print(plan.to_json())
    if args.apply:
        apply_settlement_schema(
            db_path,
            plan,
            reviewed_plan_fingerprint=args.reviewed_plan_fingerprint,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
