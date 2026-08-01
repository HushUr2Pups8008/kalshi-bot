#!/usr/bin/env python3
"""Read-only profit evidence report bound to the live runtime paper cohort.

This command never writes a database, contacts a venue, or changes admission
policy. It refuses to inspect a paper database unless botcheck attests that the
receipt belongs to the current launchd-managed main.py process and its cohort
manifest still matches.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import stat
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import botcheck
from scripts.profit_evidence_report import (
    ProfitEvidenceReport,
    ReplayEvidenceSummary,
    build_profit_evidence_report,
    render_json as render_profit_json,
    render_text as render_profit_text,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class RuntimeProfitEvidenceError(RuntimeError):
    """Raised when a runtime-bound evidence view cannot be safely built."""


@dataclass(frozen=True)
class RuntimeCohort:
    status: Literal["attested"]
    detail: str
    receipt_path: Path
    pid: int
    cohort_id: str
    cohort_kind: str
    database_path: Path


@dataclass(frozen=True)
class ReplayEvidenceInventory:
    current_oos_replay_available: bool
    top_level_corpus_candidate_count: int
    historical_negative_evidence_present: bool
    provenance_counts: dict[str, int]


@dataclass(frozen=True)
class RuntimeProfitEvidenceReport:
    runtime_cohort: RuntimeCohort
    profit_evidence: ProfitEvidenceReport
    replay_inventory: ReplayEvidenceInventory


def collect_live_runtime_binding(
    home: Path,
    receipt_path: Path,
    *,
    label: str,
    main_path: Path,
) -> dict[str, object]:
    """Reuse botcheck's live process and manifest validation without mutation."""

    now_epoch = time.time()
    now = datetime.fromtimestamp(now_epoch, tz=timezone.utc)
    launchd_output = botcheck.launchd_print(label)
    return botcheck.summarize_runtime_paper_cohort_attestation(
        home / "data",
        receipt_path,
        rows=botcheck.process_table(),
        launchd_pid=botcheck.launchd_pid(launchd_output),
        main_path=main_path,
        now=now,
        now_epoch=now_epoch,
    )


def build_runtime_profit_evidence_report(
    runtime_binding: Mapping[str, object],
    *,
    data_dir: Path,
    edge_replay_root: Path,
    receipt_path: Path | None = None,
) -> RuntimeProfitEvidenceReport:
    """Build a report only from an already botcheck-attested runtime binding."""

    runtime_cohort = _runtime_cohort_from_binding(
        runtime_binding,
        data_dir=Path(data_dir),
        receipt_path=receipt_path,
    )
    profit_evidence = build_profit_evidence_report(
        runtime_cohort.database_path,
        edge_replay_root,
    )
    replay_inventory = _replay_inventory(edge_replay_root, profit_evidence.replay)
    return RuntimeProfitEvidenceReport(
        runtime_cohort=runtime_cohort,
        profit_evidence=profit_evidence,
        replay_inventory=replay_inventory,
    )


def render_json(report: RuntimeProfitEvidenceReport) -> str:
    return json.dumps(
        {
            "runtime_cohort": {
                "status": report.runtime_cohort.status,
                "detail": report.runtime_cohort.detail,
                "receipt_path": str(report.runtime_cohort.receipt_path),
                "pid": report.runtime_cohort.pid,
                "cohort_id": report.runtime_cohort.cohort_id,
                "cohort_kind": report.runtime_cohort.cohort_kind,
                "database_path": str(report.runtime_cohort.database_path),
            },
            "profit_evidence": json.loads(render_profit_json(report.profit_evidence)),
            "replay_inventory": {
                "current_oos_replay_available": (
                    report.replay_inventory.current_oos_replay_available
                ),
                "top_level_corpus_candidate_count": (
                    report.replay_inventory.top_level_corpus_candidate_count
                ),
                "historical_negative_evidence_present": (
                    report.replay_inventory.historical_negative_evidence_present
                ),
                "provenance_counts": report.replay_inventory.provenance_counts,
            },
        },
        indent=2,
        sort_keys=True,
    )


def render_text(report: RuntimeProfitEvidenceReport) -> str:
    cohort = report.runtime_cohort
    inventory = report.replay_inventory
    lines = [
        "RUNTIME COHORT",
        f"  status: {cohort.status}",
        f"  detail: {cohort.detail}",
        f"  cohort: {cohort.cohort_kind}/{cohort.cohort_id}",
        f"  pid: {cohort.pid}",
        f"  database: {cohort.database_path}",
        "  view: current read-only runtime database; not a quiescent snapshot",
        "",
        render_profit_text(report.profit_evidence),
        "",
        "REPLAY INVENTORY",
        "  current OOS replay available: "
        f"{str(inventory.current_oos_replay_available).lower()}",
        "  top-level corpus candidates: "
        f"{inventory.top_level_corpus_candidate_count}",
        "  historical negative evidence present: "
        f"{str(inventory.historical_negative_evidence_present).lower()}",
        "  provenance counts: "
        + ", ".join(
            f"{provenance}={count}"
            for provenance, count in sorted(inventory.provenance_counts.items())
        ),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--runtime-paper-cohort-attestation",
        type=Path,
        default=Path("logs/state/runtime_paper_cohort_attestation.json"),
    )
    parser.add_argument(
        "--edge-replay-root",
        type=Path,
        default=Path("logs/edge_replay"),
    )
    parser.add_argument(
        "--label",
        default="com.jake.kalshi-bot",
    )
    parser.add_argument("--main", type=Path, default=Path("main.py"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    try:
        home = Path(args.home).resolve(strict=False)
        receipt_path = _path_within_home(
            home,
            args.runtime_paper_cohort_attestation,
            label="runtime cohort attestation",
        )
        replay_root = _path_within_home(
            home,
            args.edge_replay_root,
            label="edge replay root",
        )
        main_path = _path_within_home(home, args.main, label="main path")
        runtime_binding = collect_live_runtime_binding(
            home,
            receipt_path,
            label=args.label,
            main_path=main_path,
        )
        report = build_runtime_profit_evidence_report(
            runtime_binding,
            data_dir=home / "data",
            edge_replay_root=replay_root,
            receipt_path=receipt_path,
        )
    except (OSError, RuntimeProfitEvidenceError, ValueError, sqlite3.Error) as exc:
        _print_unverified(str(exc), as_json=args.as_json)
        return 2

    print(render_json(report) if args.as_json else render_text(report))
    return 0


def _runtime_cohort_from_binding(
    runtime_binding: Mapping[str, object],
    *,
    data_dir: Path,
    receipt_path: Path | None,
) -> RuntimeCohort:
    status = runtime_binding.get("status")
    detail = runtime_binding.get("detail")
    if status != "attested":
        suffix = f": {detail}" if isinstance(detail, str) and detail else ""
        raise RuntimeProfitEvidenceError(f"runtime cohort binding is unverified{suffix}")

    database_path = runtime_binding.get("database_path")
    if not isinstance(database_path, Path):
        raise RuntimeProfitEvidenceError("attested runtime database path is invalid")
    database_path = _regular_database_within_data(database_path, data_dir)

    pid = runtime_binding.get("pid")
    cohort_id = runtime_binding.get("cohort_id")
    cohort_kind = runtime_binding.get("cohort_kind")
    if type(pid) is not int or not isinstance(cohort_id, str) or not isinstance(
        cohort_kind, str
    ):
        raise RuntimeProfitEvidenceError("attested runtime binding fields are invalid")
    return RuntimeCohort(
        status="attested",
        detail=detail if isinstance(detail, str) else "runtime binding attested",
        receipt_path=(receipt_path or data_dir.parent / "logs/state/runtime_paper_cohort_attestation.json"),
        pid=pid,
        cohort_id=cohort_id,
        cohort_kind=cohort_kind,
        database_path=database_path,
    )


def _regular_database_within_data(database_path: Path, data_dir: Path) -> Path:
    root = data_dir.resolve(strict=False)
    target = Path(database_path)
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise RuntimeProfitEvidenceError(
            "attested runtime database path escapes the data directory"
        ) from exc
    try:
        mode = target.lstat().st_mode
    except OSError as exc:
        raise RuntimeProfitEvidenceError(
            "attested runtime database is unavailable"
        ) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise RuntimeProfitEvidenceError(
            "attested runtime database is not a regular file"
        )
    return target


def _replay_inventory(
    edge_replay_root: Path,
    replay: list[ReplayEvidenceSummary],
) -> ReplayEvidenceInventory:
    root = Path(edge_replay_root)
    candidates = (
        [path for path in root.glob("corpus_*.jsonl") if path.is_file()]
        if root.is_dir()
        else []
    )
    historical_negative = any(
        item.status == "scored"
        and item.provenance
        in {"historical_cycle", "production_proxy", "ci_historical", "ci_explicit_subset"}
        and (
            (item.realized_pnl is not None and item.realized_pnl < 0)
            or (item.per_trade_ev is not None and item.per_trade_ev < 0)
        )
        for item in replay
    )
    return ReplayEvidenceInventory(
        current_oos_replay_available=any(
            item.provenance == "head_scored_attested" and item.status == "scored"
            for item in replay
        ),
        top_level_corpus_candidate_count=len(candidates),
        historical_negative_evidence_present=historical_negative,
        provenance_counts=dict(Counter(item.provenance for item in replay)),
    )


def _path_within_home(home: Path, value: Path, *, label: str) -> Path:
    candidate = Path(value)
    target = candidate.resolve(strict=False) if candidate.is_absolute() else (home / candidate).resolve(strict=False)
    try:
        target.relative_to(home.resolve(strict=False))
    except ValueError as exc:
        raise RuntimeProfitEvidenceError(f"{label} must stay within --home") from exc
    return target


def _print_unverified(detail: str, *, as_json: bool) -> None:
    payload = {"runtime_cohort": {"status": "unverified", "detail": detail}}
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print("RUNTIME COHORT")
    print("  status: unverified")
    print(f"  detail: {detail}")


if __name__ == "__main__":
    raise SystemExit(main())
