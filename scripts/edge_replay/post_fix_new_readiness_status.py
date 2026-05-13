#!/usr/bin/env python3
"""POST_FIX_NEW corpus-readiness watcher.

Reports whether the POST_FIX_NEW replay cohort has accumulated enough
clean post-P0 / post-hotfix evidence to satisfy a downstream readiness
gate (e.g. the Cycle-17D / PROFIT-EDGE-012 resume condition documented
at ``docs/governance/2026-05-10-cycle-17d-halt-on-historical-corpus-degeneracy.md``).

Two distinct timestamps gate the cohort:

- **``bot_state.p0_price_fix_deployed_ts``** — the sentinel planted at
  first PaperTrader runtime init on v0.30.0 (LD-7 / CR-F). Anchors the
  formal post-P0 boundary.
- **clean-start timestamp** — the time the bot was confirmed to be
  running correctly post-hotfix. Defaults to ``2026-05-13T00:02:37Z``
  (the bootstrap after the P-7 status-filter regression was hotfixed in
  MR ``!14``). Operators can override via ``--clean-start-ts``.

The interval ``[sentinel, clean_start)`` is **failed-start carve-out**:
trades or decisions captured in that window may have been recorded under
the v0.30.0 400-error storm or during the bootout interval, so they are
counted separately and **not** credited toward readiness.

Exit codes:

- ``0`` — report produced (regardless of ``READY`` / ``NOT_READY``).
- ``2`` — DB unreadable, required table missing, or timestamp parse
  failure. Used so a caller can distinguish "everything fine, just not
  ready yet" from "the watcher itself failed".

The script is strictly read-only — opens the SQLite DB with
``PRAGMA query_only = ON``, never writes, never deletes runtime files.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "paper_trades.db"
DEFAULT_CLEAN_START_TS = "2026-05-13T00:02:37Z"
DEFAULT_MIN_TRADES = 10
DEFAULT_MIN_TICKERS = 3


class ReadinessError(Exception):
    """Raised when the watcher cannot produce a report at all."""


def _parse_iso(ts: str, *, label: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime.

    Tolerates a trailing ``Z`` (treated as ``+00:00``) and naive strings
    (treated as already-UTC). Raises :class:`ReadinessError` on failure
    so the caller can return a non-zero exit code.
    """
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ReadinessError(
            f"Could not parse {label} timestamp {ts!r}: {exc}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _open_db_read_only(db_path: Path) -> sqlite3.Connection:
    """Open the paper_trades DB in read-only mode.

    Uses the SQLite URI form so the OS-level open() call is
    read-only — a process holding a write lock on the live DB will not
    block this watcher and this watcher cannot accidentally take a
    write lock.
    """
    if not db_path.is_file():
        raise ReadinessError(f"paper_trades DB not found at {db_path}")
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro&immutable=0",
            uri=True,
        )
    except sqlite3.Error as exc:
        raise ReadinessError(f"could not open DB read-only: {exc}") from exc
    conn.execute("PRAGMA query_only = ON")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    )
    return cur.fetchone() is not None


def _read_sentinel(conn: sqlite3.Connection) -> str | None:
    if not _table_exists(conn, "bot_state"):
        raise ReadinessError("bot_state table missing from DB")
    row = conn.execute(
        "SELECT value FROM bot_state WHERE key = ?",
        ("p0_price_fix_deployed_ts",),
    ).fetchone()
    return row[0] if row is not None else None


def collect_readiness(
    *,
    db_path: Path,
    clean_start_ts: str,
    min_trades: int,
    min_tickers: int,
) -> dict[str, Any]:
    """Build the readiness report as a plain dict.

    The dict shape is stable and forms the JSON-output contract; tests
    rely on the exact key names.
    """
    clean_start_dt = _parse_iso(clean_start_ts, label="clean-start")

    conn = _open_db_read_only(db_path)
    try:
        if not _table_exists(conn, "paper_trades"):
            raise ReadinessError("paper_trades table missing from DB")

        sentinel = _read_sentinel(conn)
        sentinel_dt = (
            _parse_iso(sentinel, label="sentinel") if sentinel is not None else None
        )

        clean_start_iso = clean_start_dt.isoformat()
        post_count_row = conn.execute(
            "SELECT count(*) FROM paper_trades WHERE ts >= ?",
            (clean_start_iso,),
        ).fetchone()
        post_count = int(post_count_row[0]) if post_count_row else 0

        post_tickers_row = conn.execute(
            "SELECT count(DISTINCT ticker) FROM paper_trades WHERE ts >= ?",
            (clean_start_iso,),
        ).fetchone()
        post_distinct_tickers = int(post_tickers_row[0]) if post_tickers_row else 0

        if sentinel_dt is not None and sentinel_dt < clean_start_dt:
            carve_count_row = conn.execute(
                "SELECT count(*) FROM paper_trades WHERE ts >= ? AND ts < ?",
                (sentinel_dt.isoformat(), clean_start_iso),
            ).fetchone()
            carve_count = int(carve_count_row[0]) if carve_count_row else 0
        else:
            carve_count = 0
    finally:
        conn.close()

    # Decide readiness verdict and reason. NOT_READY trumps READY when
    # any precondition fails; reason explains the first failed predicate
    # so operators see a single, actionable line.
    if sentinel is None:
        verdict = "NOT_READY"
        reason = (
            "sentinel bot_state.p0_price_fix_deployed_ts is missing — bot has "
            "not yet planted the v0.30.x cohort boundary"
        )
    elif post_count < min_trades:
        verdict = "NOT_READY"
        reason = (
            f"post-clean-start paper_trades count {post_count} < "
            f"min_trades {min_trades}"
        )
    elif post_distinct_tickers < min_tickers:
        verdict = "NOT_READY"
        reason = (
            f"post-clean-start distinct tickers {post_distinct_tickers} < "
            f"min_tickers {min_tickers}"
        )
    else:
        verdict = "READY"
        reason = None

    return {
        "db_path": str(db_path),
        "sentinel_ts": sentinel,
        "clean_start_ts": clean_start_iso,
        "carve_out_start": sentinel,
        "carve_out_end": clean_start_iso,
        "carve_out_row_count": carve_count,
        "post_clean_start_row_count": post_count,
        "post_clean_start_distinct_tickers": post_distinct_tickers,
        "min_trades_required": min_trades,
        "min_tickers_required": min_tickers,
        "readiness": verdict,
        "reason": reason,
    }


def format_human_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=== POST_FIX_NEW readiness ===")
    lines.append(f"DB                  : {report['db_path']}")
    lines.append(f"sentinel            : {report['sentinel_ts'] or 'MISSING'}")
    lines.append(f"clean_start         : {report['clean_start_ts']}")
    lines.append(
        "carve_out_window    : "
        f"[{report['carve_out_start'] or 'n/a'}, {report['carve_out_end']})"
    )
    lines.append(
        f"carve_out_rows      : {report['carve_out_row_count']} "
        "(failed-start; excluded from readiness)"
    )
    lines.append(
        f"post_clean_start    : {report['post_clean_start_row_count']} rows "
        f"/ {report['post_clean_start_distinct_tickers']} distinct tickers"
    )
    lines.append(
        f"thresholds          : min_trades={report['min_trades_required']} "
        f"min_tickers={report['min_tickers_required']}"
    )
    lines.append(f"readiness           : {report['readiness']}")
    if report["reason"]:
        lines.append(f"reason              : {report['reason']}")
    return "\n".join(lines)


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="path to paper_trades.db (read-only).",
    )
    parser.add_argument(
        "--clean-start-ts",
        default=DEFAULT_CLEAN_START_TS,
        help=(
            "ISO-8601 timestamp of the first known-clean post-hotfix runtime "
            f"(default {DEFAULT_CLEAN_START_TS})."
        ),
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=DEFAULT_MIN_TRADES,
        help="minimum post-clean-start paper_trades count to declare READY.",
    )
    parser.add_argument(
        "--min-tickers",
        type=int,
        default=DEFAULT_MIN_TICKERS,
        help="minimum post-clean-start distinct tickers to declare READY.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the report as JSON instead of a human-readable summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    try:
        report = collect_readiness(
            db_path=Path(args.db),
            clean_start_ts=args.clean_start_ts,
            min_trades=int(args.min_trades),
            min_tickers=int(args.min_tickers),
        )
    except ReadinessError as exc:
        # Non-zero exit only for watcher-itself failures (per spec) so
        # callers can distinguish setup failure from NOT_READY verdict.
        print(f"post_fix_new_readiness: ERROR {exc}", file=sys.stderr)
        return 2

    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(format_human_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
