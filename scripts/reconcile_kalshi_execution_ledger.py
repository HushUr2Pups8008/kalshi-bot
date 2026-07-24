#!/usr/bin/env python3
"""Reconcile explicit Kalshi order IDs into the local execution ledger."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

# Allow this one-shot operator script to run by file path from any directory.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tasks.kalshi_execution_ledger_collector import (
    KalshiExecutionLedgerCollector,
    KalshiExecutionReceiptClient,
)
from trading.kalshi_execution_ledger import (
    KALSHI_EXECUTION_LEDGER_DB,
    KalshiExecutionLedger,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_client_factory() -> KalshiExecutionReceiptClient:
    # Import only after the explicit network/write guard. Kalshi config validates
    # credentials at import time, which must not affect the default-off command.
    from kalshi.rest_client import KalshiRestClient

    return KalshiRestClient()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--order-id",
        action="append",
        help="explicit official Kalshi order ID to reconcile; may be repeated",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="allow authenticated GET requests for the explicit order IDs",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="create or update the separate local execution ledger",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=KALSHI_EXECUTION_LEDGER_DB,
        help="ledger SQLite path (default: data/live_execution_ledger.db)",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: Callable[[], KalshiExecutionReceiptClient] | None = None,
    ledger_factory: Callable[[Path], KalshiExecutionLedger] = KalshiExecutionLedger,
    now: Callable[[], str] = _timestamp,
) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_network or not args.write:
        print(
            "refusing network or writes without both --allow-network and --write",
            file=sys.stderr,
        )
        return 2
    if not args.order_id:
        print("at least one --order-id is required", file=sys.stderr)
        return 2

    order_ids = tuple(dict.fromkeys(args.order_id))
    if any(not isinstance(order_id, str) or not order_id.strip() for order_id in order_ids):
        print("--order-id values must be non-empty", file=sys.stderr)
        return 2

    try:
        client = (client_factory or _default_client_factory)()
        ledger = ledger_factory(args.db_path)
        ledger.initialize(applied_at=now())
    except (Exception, SystemExit) as exc:
        print(f"reconciliation setup failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    collector = KalshiExecutionLedgerCollector(
        client=client,
        ledger=ledger,
        now=now,
    )
    for order_id in order_ids:
        try:
            result = collector.collect_order(order_id)
        except Exception as exc:
            print(
                f"reconciliation failed for explicit order {order_id!r}: {type(exc).__name__}",
                file=sys.stderr,
            )
            return 1
        print(
            json.dumps(
                {
                    "complete_coverage": result.complete_coverage,
                    "coverage_state": result.coverage_state,
                    "fill_statuses": list(result.fill_statuses),
                    "integrity_ok": result.integrity_ok,
                    "order_id": result.order_id,
                    "pages": result.pages,
                    "source_kind": result.source_kind,
                },
                sort_keys=True,
            )
        )
        if not result.integrity_ok:
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main().
    raise SystemExit(main())
