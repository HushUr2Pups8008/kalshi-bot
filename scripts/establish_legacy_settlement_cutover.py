"""Plan, verify, and explicitly establish an immutable legacy settlement cutover.

This CLI never enables reconciliation, restarts the bot, changes a trading mode,
or restores a database. Its mutating mode requires an operator-created matching
backup and the same runtime lock used by the long-running bot.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trading.legacy_settlement_cutover import (  # noqa: E402
    LegacySettlementCutoverPlan,
    apply_legacy_settlement_cutover,
    plan_legacy_settlement_cutover,
    validate_legacy_settlement_cutover,
)
from trading.settlement_store import SettlementStore  # noqa: E402


@contextmanager
def open_readonly(db_path: Path) -> Iterator[sqlite3.Connection]:
    resolved = db_path.expanduser().resolve()
    conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        yield conn
    finally:
        conn.close()


def _require_absolute_path(parser: argparse.ArgumentParser, value: Path, label: str) -> Path:
    expanded = value.expanduser()
    if not expanded.is_absolute():
        parser.error(f"{label} must be an absolute path")
    return expanded.resolve()


@contextmanager
def _runtime_lock(db_path: Path) -> Iterator[None]:
    """Acquire main.py's DB-parent lock without importing main and its runtime."""
    lock_path = db_path.parent / "bot_runtime.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(
            f"runtime lock is held at {lock_path}; stop the bot before --apply"
        ) from exc
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "cwd": os.getcwd(),
                    "started_utc": datetime.now(timezone.utc).isoformat(),
                    "operation": "legacy_settlement_cutover_apply",
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
        yield
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _write_plan(path: Path, plan: LegacySettlementCutoverPlan) -> None:
    if not path.parent.exists():
        raise RuntimeError(f"plan directory does not exist: {path.parent}")
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(plan.to_json() + "\n", encoding="utf-8")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _load_plan(path: Path) -> LegacySettlementCutoverPlan:
    try:
        return LegacySettlementCutoverPlan.from_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"cannot read reviewed plan artifact: {path}") from exc


def _plan_for(db_path: Path) -> LegacySettlementCutoverPlan:
    with open_readonly(db_path) as conn:
        return plan_legacy_settlement_cutover(conn, db_path)


def _financial_state_checkpoint(conn: sqlite3.Connection) -> dict[str, str | None]:
    checkpoint: dict[str, str | None] = {
        "notional_bankroll": None,
        "go_live_confirmed": None,
    }
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='bot_state'"
    ).fetchone()
    if table_exists is None:
        return checkpoint
    rows = conn.execute(
        "SELECT key, value FROM bot_state WHERE key IN (?, ?)",
        tuple(checkpoint),
    ).fetchall()
    for row in rows:
        checkpoint[str(row["key"])] = str(row["value"])
    return checkpoint


def _verify_receipt(db_path: Path) -> dict[str, object]:
    with SettlementStore(db_path, read_only=True) as store:
        readiness = store.readiness(pre_cutover=True)
        conservation = store.conservation(now=datetime.now(timezone.utc))
        canonical_delivery_complete_ids = store.canonical_delivery_complete_trade_ids(
            now=datetime.now(timezone.utc)
        )
    with open_readonly(db_path) as conn:
        legacy_cutover = validate_legacy_settlement_cutover(conn)
        financial_state_checkpoint = _financial_state_checkpoint(conn)
        resolved_count = int(
            conn.execute("SELECT COUNT(*) FROM paper_trades WHERE resolved=1").fetchone()[0]
        )
    delivery_incomplete_count = resolved_count - len(canonical_delivery_complete_ids)
    operational_ok = (
        readiness.ok
        and conservation.ok
        and legacy_cutover.ok
    )
    return {
        "operation": "verify",
        "database_uri": db_path.as_uri(),
        "operational_ok": operational_ok,
        "readiness": {
            "ok": readiness.ok,
            "failures": readiness.failures,
            "metrics": readiness.metrics,
        },
        "conservation": {
            "ok": conservation.ok,
            "failures": conservation.failures,
            "metrics": conservation.metrics,
        },
        "legacy_cutover": {
            "ok": legacy_cutover.ok,
            "failures": legacy_cutover.failures,
            "exempt_trade_ids": legacy_cutover.exempt_trade_ids,
        },
        "financial_state_checkpoint": financial_state_checkpoint,
        "canonical_delivery_complete_resolved_count": len(
            canonical_delivery_complete_ids
        ),
        "delivery_incomplete_resolved_count": delivery_incomplete_count,
        "profit_readiness": {
            "eligible": False,
            "reason": (
                "This verifies local settlement-state integrity and delivery only. "
                "No independently verified execution-profit ledger exists."
            ),
        },
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Absolute paper-trade DB path")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply a reviewed plan artifact")
    mode.add_argument("--verify", action="store_true", help="Read-only post-apply verification")
    parser.add_argument(
        "--write-plan",
        type=Path,
        help="Write the default read-only plan to this absolute JSON artifact path",
    )
    parser.add_argument(
        "--plan-file",
        type=Path,
        help="Reviewed plan JSON artifact; required with --apply",
    )
    parser.add_argument(
        "--backup-db",
        type=Path,
        help=(
            "Matching pre-apply paper_trades.db snapshot from "
            "scripts/db_snapshot_backup.sh; required with --apply"
        ),
    )
    parser.add_argument(
        "--reviewed-plan-fingerprint",
        help="Required with --apply; must equal the reviewed plan artifact",
    )
    parser.add_argument(
        "--expected-trade-id",
        action="append",
        default=[],
        help="Required with --apply; repeat for every reviewed legacy trade id",
    )
    args = parser.parse_args(argv)
    args.db = _require_absolute_path(parser, args.db, "--db")
    if args.write_plan is not None:
        args.write_plan = _require_absolute_path(parser, args.write_plan, "--write-plan")
        if args.write_plan == args.db or (
            args.write_plan.exists()
            and args.db.exists()
            and os.path.samefile(args.write_plan, args.db)
        ):
            parser.error("--write-plan must not replace --db")
    if args.plan_file is not None:
        args.plan_file = _require_absolute_path(parser, args.plan_file, "--plan-file")
    if args.backup_db is not None:
        args.backup_db = _require_absolute_path(parser, args.backup_db, "--backup-db")
    if args.apply:
        if args.plan_file is None:
            parser.error("--apply requires --plan-file")
        if args.backup_db is None:
            parser.error("--apply requires --backup-db")
        if not args.reviewed_plan_fingerprint:
            parser.error("--apply requires the reviewed plan fingerprint")
        if not args.expected_trade_id:
            parser.error("--apply requires the exact reviewed trade ids")
        if args.write_plan is not None:
            parser.error("--write-plan cannot be combined with --apply")
    elif args.plan_file is not None or args.backup_db is not None:
        parser.error("--plan-file and --backup-db are only valid with --apply")
    if args.verify and args.write_plan is not None:
        parser.error("--write-plan cannot be combined with --verify")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.verify:
            receipt = _verify_receipt(args.db)
            print(json.dumps(receipt, indent=2, sort_keys=True))
            return 0 if receipt["operational_ok"] else 1

        if not args.apply:
            plan = _plan_for(args.db)
            if args.write_plan is not None:
                _write_plan(args.write_plan, plan)
                print(
                    json.dumps(
                        {
                            "operation": "plan",
                            "database_uri": args.db.as_uri(),
                            "plan_file": args.write_plan.as_uri(),
                            "fingerprint": plan.fingerprint,
                            "state_fingerprint": plan.state_fingerprint,
                            "eligible_legacy_trade_count": plan.legacy_trade_count,
                            "unattested_resolved_count": plan.unattested_resolved_count,
                            "non_mapped_unattested_count": plan.non_mapped_unattested_count,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                print(plan.to_json())
            return 0

        plan = _load_plan(args.plan_file)
        if args.backup_db == args.db or (
            args.backup_db.exists() and os.path.samefile(args.backup_db, args.db)
        ):
            raise RuntimeError("--backup-db must be a distinct pre-apply snapshot")
        with _runtime_lock(args.db):
            backup_plan = _plan_for(args.backup_db)
            if backup_plan.state_fingerprint != plan.state_fingerprint:
                raise RuntimeError("backup state does not match the reviewed plan")
            apply_legacy_settlement_cutover(
                args.db,
                plan,
                reviewed_plan_fingerprint=args.reviewed_plan_fingerprint,
                reviewed_trade_ids=tuple(args.expected_trade_id),
            )
            receipt = _verify_receipt(args.db)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt["operational_ok"] else 1
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
