"""Provision a permanently paper-only cohort while legacy positions settle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from config import DATA_DIR, MAX_MARKET_DAYS_TO_EXPIRY  # noqa: E402
from trading.paper_cohorts import (  # noqa: E402
    LEGACY_PAPER_COHORT_ID,
    LegacyOpenExposureFingerprint,
    initialize_legacy_pending_paper_cohort_manifest,
    legacy_open_exposure_fingerprint,
    resolve_runtime_paper_cohort,
)


_PAPER_ONLY_CONFIRMATION = "PAPER_ONLY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision a permanently paper-only legacy-pending cohort"
    )
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--starting-bankroll", required=True, type=float)
    parser.add_argument(
        "--legacy-starting-bankroll",
        required=True,
        type=float,
        help="operator-attested historical legacy paper baseline; never read from BANKROLL",
    )
    parser.add_argument(
        "--confirm-legacy-starting-bankroll",
        required=True,
        type=float,
        help="repeat --legacy-starting-bankroll to attest the immutable baseline",
    )
    parser.add_argument(
        "--max-days-to-close",
        type=float,
        default=14.0,
        help=f"paper admission horizon, in (0, {MAX_MARKET_DAYS_TO_EXPIRY}]",
    )
    parser.add_argument(
        "--expected-legacy-open-trade-count",
        required=True,
        type=int,
        help="exact unresolved legacy trade count from the reviewed fingerprint",
    )
    parser.add_argument(
        "--expected-legacy-open-rows-sha256",
        required=True,
        help="exact SHA-256 of the reviewed unresolved legacy rows",
    )
    parser.add_argument(
        "--confirm-cohort",
        required=True,
        help="repeat the cohort ID to confirm immutable provisioning",
    )
    parser.add_argument(
        "--confirm-paper-only",
        required=True,
        help=f"type {_PAPER_ONLY_CONFIRMATION} to acknowledge this cohort can never trade live",
    )
    return parser.parse_args()


def _require_confirmations(args: argparse.Namespace) -> None:
    if args.confirm_paper_only != _PAPER_ONLY_CONFIRMATION:
        raise SystemExit(
            f"--confirm-paper-only must exactly equal {_PAPER_ONLY_CONFIRMATION}"
        )
    if args.confirm_legacy_starting_bankroll != args.legacy_starting_bankroll:
        raise SystemExit(
            "--confirm-legacy-starting-bankroll must exactly match --legacy-starting-bankroll"
        )
    if args.confirm_cohort.strip().lower() != args.cohort_id.strip().lower():
        raise SystemExit("--confirm-cohort must exactly match the normalized cohort ID")


def _require_expected_open_exposure(
    args: argparse.Namespace,
) -> LegacyOpenExposureFingerprint:
    exposure = legacy_open_exposure_fingerprint(DATA_DIR / "paper_trades.db")
    if args.expected_legacy_open_trade_count != exposure.unresolved_trade_count:
        raise SystemExit(
            "--expected-legacy-open-trade-count does not match the current legacy open exposure"
        )
    if args.expected_legacy_open_rows_sha256 != exposure.rows_sha256:
        raise SystemExit(
            "--expected-legacy-open-rows-sha256 does not match the current legacy open exposure"
        )
    return exposure


def main() -> int:
    args = parse_args()
    _require_confirmations(args)
    legacy_open_exposure = _require_expected_open_exposure(args)
    cohort = resolve_runtime_paper_cohort(
        args.cohort_id,
        legacy_starting_bankroll=args.legacy_starting_bankroll,
        active_starting_bankroll=args.starting_bankroll,
        db_root=DATA_DIR,
        cohort_kind="legacy_pending",
    )
    if cohort.cohort_id == LEGACY_PAPER_COHORT_ID:
        raise SystemExit("Refusing to provision the legacy cohort")

    manifest_path = initialize_legacy_pending_paper_cohort_manifest(
        cohort,
        max_days_to_close=args.max_days_to_close,
        legacy_db_path=DATA_DIR / "paper_trades.db",
        legacy_starting_bankroll=args.legacy_starting_bankroll,
        expected_legacy_open_trade_count=args.expected_legacy_open_trade_count,
        expected_legacy_open_rows_sha256=args.expected_legacy_open_rows_sha256,
    )
    print(
        json.dumps(
            {
                "status": "initialized",
                "cohort_kind": "legacy_pending",
                "cohort_id": cohort.cohort_id,
                "manifest_path": str(manifest_path),
                "database_path": str(cohort.db_path),
                "database_created": True,
                "starting_bankroll": cohort.starting_bankroll,
                "legacy_starting_bankroll": args.legacy_starting_bankroll,
                "legacy_open_trade_count": legacy_open_exposure.unresolved_trade_count,
                "legacy_open_rows_sha256": legacy_open_exposure.rows_sha256,
                "max_days_to_close": args.max_days_to_close,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
