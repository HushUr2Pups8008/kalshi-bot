"""Atomically provision a bound active-paper manifest and SQLite identity DB."""

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
    initialize_active_paper_cohort_manifest,
    resolve_runtime_paper_cohort,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision an immutable active-paper cohort"
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
        help="repeat --legacy-starting-bankroll to attest the immutable cutover baseline",
    )
    parser.add_argument(
        "--max-days-to-close",
        type=float,
        default=14.0,
        help=f"paper admission horizon, in (0, {MAX_MARKET_DAYS_TO_EXPIRY}]",
    )
    parser.add_argument(
        "--confirm-cohort",
        required=True,
        help="repeat the cohort ID to confirm immutable provisioning",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.confirm_legacy_starting_bankroll != args.legacy_starting_bankroll:
        raise SystemExit(
            "--confirm-legacy-starting-bankroll must exactly match --legacy-starting-bankroll"
        )
    cohort = resolve_runtime_paper_cohort(
        args.cohort_id,
        legacy_starting_bankroll=args.legacy_starting_bankroll,
        active_starting_bankroll=args.starting_bankroll,
        db_root=DATA_DIR,
    )
    if cohort.cohort_id == LEGACY_PAPER_COHORT_ID:
        raise SystemExit("Refusing to provision the legacy cohort")
    if args.confirm_cohort.strip().lower() != cohort.cohort_id:
        raise SystemExit("--confirm-cohort must exactly match the normalized cohort ID")

    manifest_path = initialize_active_paper_cohort_manifest(
        cohort,
        max_days_to_close=args.max_days_to_close,
        legacy_db_path=DATA_DIR / "paper_trades.db",
        legacy_starting_bankroll=args.legacy_starting_bankroll,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": "initialized",
                "cohort_id": cohort.cohort_id,
                "manifest_path": str(manifest_path),
                "database_path": str(cohort.db_path),
                "database_created": True,
                "starting_bankroll": cohort.starting_bankroll,
                "legacy_starting_bankroll": args.legacy_starting_bankroll,
                "legacy_baseline_attestation": manifest["legacy_baseline_attestation"],
                "legacy_baseline_verification": manifest["legacy_baseline_verification"],
                "max_days_to_close": args.max_days_to_close,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
