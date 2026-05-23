"""OOS corpus seed extractor.

Extracts paper-trade rows from ``data/paper_trades.db`` for a configurable
``ts`` window and writes them as JSONL annotated with cohort metadata. The
output is intended as the **seed** for an out-of-sample (OOS) replay corpus
that the eventual I-1 corpus builder (`docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md`)
will consume and normalize into the full replay schema.

This script is intentionally narrow:

- Pure read. ``PRAGMA query_only = ON``. Never writes the DB.
- Adds cohort metadata fields (``cohort_tag``, ``corpus_window_start_utc``,
  ``corpus_window_end_utc``, ``oos_seed_version``) to each row.
- Does NOT normalize to the full ``replay_dataset.jsonl`` schema (decision-side
  columns from `evidence_store.db` are not joined here). That is I-1's job.
- Idempotent: identical inputs yield identical output.

Tier classification (per framework v2 §2): **T0** — observability/mechanical,
read-only, no path to a paper or live trade decision. Unit-test gate sufficient.

Default cohort start ``2026-05-23T21:22:16Z`` is the bot restart timestamp at
which the bot first loaded the v0.30.2 release commit (`7dbfb47`) — see
`docs/profit_path_debt_log.md` §2.2 Wave-1 Replay Closure.

Usage::

    python scripts/edge_replay/oos_corpus_seed.py \\
        --start-ts 2026-05-23T21:22:16Z \\
        --output logs/edge_replay/oos_v030_2/replay_dataset_seed.jsonl

Exit codes:

- ``0`` — output written (regardless of row count).
- ``2`` — DB unreadable, required table missing, or argument parse failure.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("data/paper_trades.db")
DEFAULT_OUTPUT = Path("logs/edge_replay/oos_v030_2/replay_dataset_seed.jsonl")
DEFAULT_START_TS = "2026-05-23T21:22:16Z"
DEFAULT_COHORT_TAG = "POST_V030_2_OOS_SEED"
OOS_SEED_VERSION = 1


def _parse_iso8601(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting trailing ``Z`` for UTC."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _normalize_iso8601_z(value: str) -> str:
    """Canonicalize to a ``YYYY-MM-DDTHH:MM:SSZ`` string."""
    dt = _parse_iso8601(value).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row into a plain dict so json.dumps can serialize it."""
    return {key: row[key] for key in row.keys()}


def _annotate(
    row: dict[str, Any],
    cohort_tag: str,
    window_start: str,
    window_end: str | None,
) -> dict[str, Any]:
    """Add cohort metadata fields to a row."""
    annotated = dict(row)
    annotated["cohort_tag"] = cohort_tag
    annotated["corpus_window_start_utc"] = window_start
    annotated["corpus_window_end_utc"] = window_end
    annotated["oos_seed_version"] = OOS_SEED_VERSION
    return annotated


def extract_seed(
    db_path: Path,
    start_ts: str,
    end_ts: str | None,
    cohort_tag: str,
    output_path: Path,
) -> dict[str, Any]:
    """Extract paper_trades rows matching the window into JSONL at output_path.

    Returns a summary dict ``{"row_count", "window_start_utc", "window_end_utc",
    "output_path", "cohort_tag"}``. The output file is created with its parent
    directory; existing content at ``output_path`` is overwritten so the
    operation is idempotent.
    """
    window_start = _normalize_iso8601_z(start_ts)
    window_end = _normalize_iso8601_z(end_ts) if end_ts is not None else None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.execute("PRAGMA table_info(paper_trades)")
        cols = {row[1] for row in cursor.fetchall()}
        if "ts" not in cols:
            raise RuntimeError(
                "paper_trades table missing required 'ts' column "
                "(seed extractor expects existing schema)"
            )

        if window_end is None:
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE ts >= ? ORDER BY ts ASC",
                (window_start,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE ts >= ? AND ts < ? ORDER BY ts ASC",
                (window_start, window_end),
            ).fetchall()
    finally:
        conn.close()

    with output_path.open("w", encoding="utf-8") as out:
        for row in rows:
            annotated = _annotate(
                _row_to_dict(row), cohort_tag, window_start, window_end
            )
            out.write(json.dumps(annotated, sort_keys=True))
            out.write("\n")

    return {
        "row_count": len(rows),
        "window_start_utc": window_start,
        "window_end_utc": window_end,
        "output_path": str(output_path),
        "cohort_tag": cohort_tag,
    }


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract paper_trades rows post-v0.30.2 cohort start into a JSONL "
            "seed for the eventual OOS replay corpus."
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to paper_trades SQLite DB (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--start-ts",
        type=str,
        default=DEFAULT_START_TS,
        help=(
            "Window start in ISO-8601 (default: v0.30.2 cohort start "
            f"{DEFAULT_START_TS})"
        ),
    )
    parser.add_argument(
        "--end-ts",
        type=str,
        default=None,
        help="Window end in ISO-8601 (exclusive). Omit for open-ended.",
    )
    parser.add_argument(
        "--cohort-tag",
        type=str,
        default=DEFAULT_COHORT_TAG,
        help=f"Cohort tag stamped on each row (default: {DEFAULT_COHORT_TAG})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSONL path (default: {DEFAULT_OUTPUT})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"[oos_corpus_seed] DB not found: {args.db}", file=sys.stderr)
        return 2

    try:
        summary = extract_seed(
            db_path=args.db,
            start_ts=args.start_ts,
            end_ts=args.end_ts,
            cohort_tag=args.cohort_tag,
            output_path=args.output,
        )
    except (sqlite3.DatabaseError, RuntimeError, ValueError) as exc:
        print(f"[oos_corpus_seed] extraction failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
