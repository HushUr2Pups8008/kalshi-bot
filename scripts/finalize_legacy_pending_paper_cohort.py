#!/usr/bin/env python3
"""Review-gated planning and filesystem-only apply for legacy-pending finalization."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.legacy_pending_finalization import (
    apply_legacy_pending_finalization,
    finalization_plan_sha256,
    plan_legacy_pending_finalization,
    serialize_legacy_pending_finalization_certificate,
    serialize_legacy_pending_finalization_plan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or apply legacy-pending finalization."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan")
    _add_plan_arguments(plan_parser)
    plan_parser.add_argument(
        "--plan-output-path",
        help="Optional path to write the sealed finalization plan artifact.",
    )

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--db-path", required=True)
    apply_parser.add_argument("--pending-root", required=True)
    apply_parser.add_argument("--sealed-plan-path", required=True)
    apply_parser.add_argument("--expected-finalization-plan-sha", required=False)
    apply_parser.add_argument("--operator-confirmation", required=True)
    apply_parser.add_argument("--write", action="store_true")
    return parser


def _add_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--pending-root", required=True)
    parser.add_argument("--finalization-id", required=True)
    parser.add_argument("--expected-root-sha256", required=True)
    parser.add_argument(
        "--expected-pending-manifest-sha256",
        action="append",
        dest="expected_pending_manifest_sha256s",
        required=True,
    )
    parser.add_argument("--expected-legacy-snapshot-sha256", required=True)
    parser.add_argument("--expected-baseline-open-rows-sha256", required=True)
    parser.add_argument(
        "--expected-baseline-trade-id",
        action="append",
        dest="expected_baseline_trade_ids",
        required=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            plan = plan_legacy_pending_finalization(
                db_path=args.db_path,
                pending_root=args.pending_root,
                finalization_id=args.finalization_id,
                expected_root_sha256=args.expected_root_sha256,
                expected_pending_manifest_sha256s=args.expected_pending_manifest_sha256s,
                expected_legacy_snapshot_sha256=args.expected_legacy_snapshot_sha256,
                expected_baseline_open_rows_sha256=args.expected_baseline_open_rows_sha256,
                expected_baseline_trade_ids=args.expected_baseline_trade_ids,
            )
            serialized = serialize_legacy_pending_finalization_plan(plan)
            print(serialized)
            if args.plan_output_path:
                Path(args.plan_output_path).write_text(serialized, encoding="utf-8")
            print(
                f"finalization_plan_sha256={finalization_plan_sha256(plan)}",
                file=sys.stderr,
            )
            return 0

        expected_sha = args.expected_finalization_plan_sha
        if not expected_sha:
            raise ValueError("legacy pending finalization apply requires --expected-finalization-plan-sha")
        result = apply_legacy_pending_finalization(
            db_path=args.db_path,
            pending_root=args.pending_root,
            sealed_plan_path=args.sealed_plan_path,
            expected_finalization_plan_sha256=expected_sha,
            operator_confirmation=args.operator_confirmation,
            write=args.write,
        )
        print(
            serialize_legacy_pending_finalization_certificate(result.certificate)
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
