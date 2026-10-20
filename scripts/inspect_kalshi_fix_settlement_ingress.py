#!/usr/bin/env python3
"""Read-only inspection for captured Kalshi FIX UMS ingress records.

This script never starts a FIX session, accepts receipt material, initializes a
store, or derives financial outcomes. It reports only the local capture ledger's
non-authoritative state.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.output_paths import DB_STATE_DIR


DEFAULT_KALSHI_FIX_SETTLEMENT_INGRESS_DB_PATH = (
    DB_STATE_DIR / "kalshi_fix_settlement_ingress.db"
)
_STATIC_INVARIANT: dict[str, object] = {
    "transport_authentication": "upstream_attested_not_proven_by_ledger",
    "pagination_coverage": "unknown",
    "canonical_settlement_binding": "absent",
    "fee_net_pnl": "unscorable",
    "paper_trader_updated": False,
    "orders_changed": False,
    "promotion_eligible": False,
}
_STATUS_VALUES = frozenset(
    {
        "captured_non_authoritative",
        "disabled_non_authoritative",
        "absent_non_authoritative",
        "invalid_non_authoritative",
    }
)


def _status(
    state: str,
    *,
    raw_capture: str,
    session_provenance: str,
) -> dict[str, object]:
    return {
        "status": state,
        "transport_authentication": _STATIC_INVARIANT["transport_authentication"],
        "raw_capture": raw_capture,
        "session_provenance": session_provenance,
        "pagination_coverage": _STATIC_INVARIANT["pagination_coverage"],
        "canonical_settlement_binding": _STATIC_INVARIANT[
            "canonical_settlement_binding"
        ],
        "fee_net_pnl": _STATIC_INVARIANT["fee_net_pnl"],
        "paper_trader_updated": _STATIC_INVARIANT["paper_trader_updated"],
        "orders_changed": _STATIC_INVARIANT["orders_changed"],
        "promotion_eligible": _STATIC_INVARIANT["promotion_eligible"],
    }


def _validated_snapshot(snapshot: object) -> None:
    if not is_dataclass(snapshot) or isinstance(snapshot, type):
        raise ValueError("FIX settlement ingress snapshot must be a dataclass")

    state = getattr(snapshot, "status", None)
    raw_capture = getattr(snapshot, "raw_capture", None)
    session_provenance = getattr(snapshot, "session_provenance", None)
    if state not in _STATUS_VALUES:
        raise ValueError("FIX settlement ingress snapshot has an invalid status")
    if raw_capture not in {"present", "absent"}:
        raise ValueError("FIX settlement ingress snapshot has an invalid raw-capture state")
    if session_provenance not in {"present", "absent"}:
        raise ValueError(
            "FIX settlement ingress snapshot has an invalid session-provenance state"
        )
    if state == "captured_non_authoritative" and (
        raw_capture != "present" or session_provenance != "present"
    ):
        raise ValueError("captured FIX ingress status requires raw capture and provenance")
    if state != "captured_non_authoritative" and (
        raw_capture != "absent" or session_provenance != "absent"
    ):
        raise ValueError("non-captured FIX ingress status cannot assert receipt material")
    for key, expected in _STATIC_INVARIANT.items():
        if getattr(snapshot, key, None) != expected:
            raise ValueError(f"FIX settlement ingress snapshot violates {key}")


def read_status(db_path: Path) -> dict[str, object]:
    """Read an existing ledger only; missing or invalid paths create nothing."""
    if not db_path.is_file():
        return _status(
            "absent_non_authoritative",
            raw_capture="absent",
            session_provenance="absent",
        )

    try:
        from trading.kalshi_fix_settlement_ingress import (
            read_kalshi_fix_settlement_ingress_snapshot,
        )

        snapshot = read_kalshi_fix_settlement_ingress_snapshot(db_path)
        _validated_snapshot(snapshot)
        status = asdict(snapshot)
        latest_received_at = status.get("latest_received_at")
        if isinstance(latest_received_at, datetime):
            status["latest_received_at"] = latest_received_at.astimezone(UTC).isoformat()
        return status
    except Exception as exc:
        result = _status(
            "invalid_non_authoritative",
            raw_capture="absent",
            session_provenance="absent",
        )
        result["error"] = type(exc).__name__
        return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only status for the default-off Kalshi FIX UMS ingress ledger."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_KALSHI_FIX_SETTLEMENT_INGRESS_DB_PATH,
    )
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--status", action="store_true")
    command.add_argument("--verify-schema", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    status = read_status(args.db_path)
    print(json.dumps(status, sort_keys=True))
    if args.verify_schema:
        if not args.db_path.is_file():
            return 1
        return 0 if status.get("schema_valid") is True else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
