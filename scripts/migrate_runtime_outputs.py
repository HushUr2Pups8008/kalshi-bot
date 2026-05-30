"""Dry-run/apply migration for generated runtime output artifacts.

The migration intentionally skips canonical raw telemetry and DB state:
``logs/trades/**``, ``logs/governance/**``, ``data/paper_trades.db``, and
``data/evidence_store.db`` are not moved here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from utils.output_paths import OUTPUT_ROOT, REPO_ROOT


@dataclass(frozen=True)
class Move:
    source: Path
    destination: Path
    classification: str


def _add_glob(moves: list[Move], root: Path, pattern: str, destination_dir: Path, classification: str) -> None:
    for source in sorted(root.glob(pattern)):
        if not source.is_file():
            continue
        moves.append(Move(source, destination_dir / source.name, classification))


def build_plan(*, repo: Path = REPO_ROOT, output_root: Path = OUTPUT_ROOT) -> list[Move]:
    moves: list[Move] = []

    logs = output_root
    reports = logs / "reports"
    app = logs / "app"
    data = repo / "data"

    _add_glob(moves, reports, "daily_review_*.txt", reports / "daily", "report")
    _add_glob(moves, app, "bothealth_*.md", reports / "health", "report")
    _add_glob(moves, reports, "report_*.txt", reports / "performance", "report")
    _add_glob(moves, reports, "analysis_*.txt", reports / "performance", "report")
    _add_glob(moves, logs / "eval", "*.md", reports / "evaluations", "report")

    derived = logs / "state" / "derived"
    for name in (
        "source_tier_state.json",
        "news_edge_series.json",
        "calibration_summary.json",
        "feed_health.json",
        "regime_prior_audit.json",
        "market_horizon_audit.json",
        "edge_activation_compare.json",
    ):
        for source in (reports / name, data / name):
            if source.is_file():
                moves.append(Move(source, derived / name, "derived"))

    legacy_snapshots = repo / "mac_archive" / "db_snapshots"
    if legacy_snapshots.is_dir():
        moves.append(Move(legacy_snapshots, logs / "backups" / "db_snapshots", "backup"))

    return [move for move in moves if move.source != move.destination]


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def execute_plan(moves: list[Move], *, apply: bool = False) -> list[dict[str, object]]:
    manifest: list[dict[str, object]] = []
    for move in moves:
        record = {
            **asdict(move),
            "source": str(move.source),
            "destination": str(move.destination),
            "source_sha256": _sha256(move.source),
            "applied": apply,
        }
        manifest.append(record)
        if not apply:
            continue
        move.destination.parent.mkdir(parents=True, exist_ok=True)
        if move.destination.exists():
            raise FileExistsError(f"destination already exists: {move.destination}")
        shutil.move(str(move.source), str(move.destination))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate generated runtime outputs")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--apply", action="store_true", help="move files; default is dry-run")
    parser.add_argument("--manifest", type=Path, help="write JSON manifest")
    args = parser.parse_args(argv)

    moves = build_plan(repo=args.repo, output_root=args.output_root)
    manifest = execute_plan(moves, apply=args.apply)
    envelope = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "apply": args.apply,
        "move_count": len(manifest),
        "moves": manifest,
    }
    rendered = json.dumps(envelope, indent=2, sort_keys=True)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
