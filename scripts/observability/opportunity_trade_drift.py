#!/usr/bin/env python3
"""Opportunity-vs-trade drift watcher.

Surfaces the "silent attrition" condition identified in audit finding F-06:
the bot executor logs ``OPPORTUNITY`` events to JSONL but zero new rows
appear in ``paper_trades`` SQL -- a state that is indistinguishable from
"no candidates fired the edge gate" when viewed from SQL-only operator
dashboards.

The watcher correlates the JSONL ``OPPORTUNITY`` stream (live log + day-
partitioned archive) against the ``paper_trades`` SQL table, both filtered
to the post-clean-start window, and emits a machine-readable verdict.

**Verdict ladder** (first match wins):

- ``WATCHER_ERROR`` (exit 2) -- DB / JSONL paths unreadable or sentinel
  table missing.
- ``INSUFFICIENT_DATA`` -- ``opportunity_count < min_opportunities``. Too few
  signals to diagnose drift; includes the "quiet news day" case where
  ``opportunity_count == 0``.
- ``INSUFFICIENT_WINDOW`` -- ``hours_elapsed < max_hours_without_trade``. Too
  early in the soak window to alarm.
- ``ALARM`` -- ``paper_trade_count == 0`` AND ``opportunity_count >=
  min_opportunities`` AND ``hours_elapsed >= max_hours_without_trade``.
  Silent attrition strongly suspected.
- ``DEGRADED`` -- ``drift_ratio < drift_ratio_threshold`` AND
  ``opportunity_count >= min_opportunities`` AND ``hours_elapsed >=
  max_hours_without_trade``. Some trades but lower-than-expected conversion.
- ``NORMAL`` -- otherwise.

Exit codes:

- ``0`` -- report produced (any verdict).
- ``2`` -- watcher itself failed (DB unreadable, table missing, etc.). Used
  so callers can distinguish setup failure from a drift verdict.

The script is strictly read-only -- opens the SQLite DB with
``PRAGMA query_only = ON``, streams JSONL line-by-line without loading files
into memory, and never writes or deletes any runtime files.
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
DEFAULT_TRADE_LOG_LIVE = REPO_ROOT / "logs" / "trades" / "live" / "trades.jsonl"
DEFAULT_TRADE_LOG_ARCHIVE_DIR = REPO_ROOT / "logs" / "trades" / "archive"
DEFAULT_CLEAN_START_TS = "2026-05-13T00:02:37Z"
DEFAULT_MIN_OPPORTUNITIES = 3
DEFAULT_MAX_HOURS_WITHOUT_TRADE = 6.0
DEFAULT_DRIFT_RATIO_THRESHOLD = 0.05


class WatcherError(Exception):
    """Raised when the watcher cannot produce a report at all."""


# ── Shared helpers (inlined per project convention) ───────────────────────────


def _parse_iso(ts: str, *, label: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime.

    Tolerates a trailing ``Z`` (treated as ``+00:00``) and naive strings
    (treated as already-UTC). Raises :class:`WatcherError` on failure so
    the caller can return a non-zero exit code.
    """
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise WatcherError(
            f"Could not parse {label} timestamp {ts!r}: {exc}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _open_db_read_only(db_path: Path) -> sqlite3.Connection:
    """Open the paper_trades DB in read-only mode.

    Uses the SQLite URI form so the OS-level open() call is read-only — a
    process holding a write lock on the live DB will not block this watcher
    and this watcher cannot accidentally take a write lock.
    """
    if not db_path.is_file():
        raise WatcherError(f"paper_trades DB not found at {db_path}")
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro&immutable=0",
            uri=True,
        )
    except sqlite3.Error as exc:
        raise WatcherError(f"could not open DB read-only: {exc}") from exc
    conn.execute("PRAGMA query_only = ON")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    )
    return cur.fetchone() is not None


# ── JSONL streaming ───────────────────────────────────────────────────────────


def _iter_jsonl_files(
    live_path: Path,
    archive_dir: Path,
) -> list[Path]:
    """Return all JSONL paths to stream, in stable order.

    Includes the live log (if it exists) plus any ``*.jsonl`` files found
    recursively under the archive directory (if it exists).  Missing paths
    are silently omitted -- callers get zero records from them rather than
    an error.

    Paths are deduplicated by resolved canonical path so that if the archive
    dir is a parent of (or equal to the directory containing) the live log,
    the live log is not counted twice.  Order is preserved: live log first,
    then archive partitions in sorted() order.
    """
    candidates: list[Path] = []
    if live_path.is_file():
        candidates.append(live_path)
    if archive_dir.is_dir():
        candidates.extend(sorted(archive_dir.rglob("*.jsonl")))

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _count_opportunities(
    live_path: Path,
    archive_dir: Path,
    clean_start_dt: datetime,
) -> tuple[int, int]:
    """Stream all JSONL files and count post-clean-start OPPORTUNITY records.

    Returns ``(opportunity_count, parse_errors)``.  Lines are processed one
    at a time — no full-file load into memory.  Malformed JSON, missing ``ts``
    fields, and unparseable timestamps each increment ``parse_errors`` and are
    otherwise skipped.  Blank/whitespace-only lines are skipped silently.
    """
    opportunity_count = 0
    parse_errors = 0

    for path in _iter_jsonl_files(live_path, archive_dir):
        try:
            fh_ctx = path.open("r", encoding="utf-8")
        except OSError as exc:
            print(
                f"opportunity_trade_drift: WARN skipping unreadable file {path}: {exc}",
                file=sys.stderr,
            )
            parse_errors += 1
            continue
        with fh_ctx as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue  # blank — skip silently
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    parse_errors += 1
                    continue

                if not isinstance(record, dict):
                    parse_errors += 1
                    continue

                if record.get("type") != "OPPORTUNITY":
                    continue  # not an opportunity — ignore, no error

                raw_ts = record.get("ts")
                if raw_ts is None:
                    parse_errors += 1
                    continue

                try:
                    rec_dt = _parse_iso(str(raw_ts), label="record ts")
                except WatcherError:
                    parse_errors += 1
                    continue

                if rec_dt < clean_start_dt:
                    continue  # pre-clean-start — exclude

                opportunity_count += 1

    return opportunity_count, parse_errors


# ── Core report builder ───────────────────────────────────────────────────────


def collect_report(
    *,
    db_path: Path,
    trade_log_live: Path,
    trade_log_archive_dir: Path,
    clean_start_ts: str,
    min_opportunities: int,
    max_hours_without_trade: float,
    drift_ratio_threshold: float,
    now_ts: str | None = None,
) -> dict[str, Any]:
    """Build the drift report as a plain dict.

    The dict shape is stable and forms the JSON-output contract; tests rely
    on the exact key names.

    Raises :class:`WatcherError` on any condition that prevents report
    generation (DB missing, required table absent, unparseable timestamps).
    """
    clean_start_dt = _parse_iso(clean_start_ts, label="clean-start")

    if now_ts is not None:
        now_dt = _parse_iso(now_ts, label="now")
    else:
        now_dt = datetime.now(timezone.utc)

    hours_elapsed = (now_dt - clean_start_dt).total_seconds() / 3600.0

    # ── DB queries (read-only) ────────────────────────────────────────────────
    conn = _open_db_read_only(db_path)
    try:
        if not _table_exists(conn, "bot_state"):
            raise WatcherError("bot_state table missing from DB")
        if not _table_exists(conn, "paper_trades"):
            raise WatcherError("paper_trades table missing from DB")

        clean_start_iso = clean_start_dt.isoformat()

        # Storage invariant: paper_trades.ts is always written via
        # datetime.now(timezone.utc).isoformat() in trading/paper_trader.py,
        # which produces the "+00:00" offset form (e.g. "2026-05-13T00:02:37.265238+00:00").
        # The WHERE comparisons below rely on lexicographic string ordering, which
        # is correct only when all stored ts values use the same offset notation.
        # If a future change ever writes "Z"-suffixed timestamps, the ordering
        # still works because "Z" (ASCII 90) sorts after "+" (ASCII 43), so Z-rows
        # have a lexicographically larger suffix and are correctly included when
        # their datetime is >= clean_start.  However, mixing formats is fragile;
        # if this ever regresses, normalise with:
        #   WHERE REPLACE(ts, 'Z', '+00:00') >= ?
        paper_trade_count_row = conn.execute(
            "SELECT count(*) FROM paper_trades WHERE ts >= ?",
            (clean_start_iso,),
        ).fetchone()
        paper_trade_count = int(paper_trade_count_row[0]) if paper_trade_count_row else 0

        distinct_tickers_row = conn.execute(
            "SELECT count(DISTINCT ticker) FROM paper_trades WHERE ts >= ?",
            (clean_start_iso,),
        ).fetchone()
        paper_trade_distinct_tickers = (
            int(distinct_tickers_row[0]) if distinct_tickers_row else 0
        )
    finally:
        conn.close()

    # ── JSONL streaming ───────────────────────────────────────────────────────
    opportunity_count, parse_errors = _count_opportunities(
        trade_log_live, trade_log_archive_dir, clean_start_dt
    )

    # ── Derived metrics ───────────────────────────────────────────────────────
    drift_ratio: float | None
    if opportunity_count > 0:
        drift_ratio = round(paper_trade_count / opportunity_count, 4)
    else:
        drift_ratio = None

    # ── Verdict ladder (first match wins) ────────────────────────────────────
    verdict: str
    reason: str | None

    if opportunity_count < min_opportunities:
        verdict = "INSUFFICIENT_DATA"
        reason = (
            f"opportunity_count {opportunity_count} < min_opportunities "
            f"{min_opportunities}; cannot assess drift"
        )
    elif hours_elapsed < max_hours_without_trade:
        verdict = "INSUFFICIENT_WINDOW"
        reason = (
            f"hours_elapsed {round(hours_elapsed, 4)} < "
            f"max_hours_without_trade {max_hours_without_trade}; too early to alarm"
        )
    elif paper_trade_count == 0:
        verdict = "ALARM"
        reason = (
            f"zero paper_trades after {round(hours_elapsed, 4)}h with "
            f"{opportunity_count} OPPORTUNITY events logged -- silent attrition suspected"
        )
    elif drift_ratio < drift_ratio_threshold:  # drift_ratio is non-None here: opportunity_count >= min_opportunities > 0
        verdict = "DEGRADED"
        reason = (
            f"drift_ratio {drift_ratio} < threshold {drift_ratio_threshold} "
            f"({paper_trade_count} trades / {opportunity_count} opportunities)"
        )
    else:
        verdict = "NORMAL"
        reason = None

    return {
        "db_path": str(db_path),
        "trade_log_live": str(trade_log_live),
        "trade_log_archive_dir": str(trade_log_archive_dir),
        "clean_start_ts": clean_start_dt.isoformat(),
        "now_ts": now_dt.isoformat(),
        "hours_elapsed": round(hours_elapsed, 4),
        "opportunity_count": opportunity_count,
        "paper_trade_count": paper_trade_count,
        "paper_trade_distinct_tickers": paper_trade_distinct_tickers,
        "drift_ratio": drift_ratio,
        "parse_errors": parse_errors,
        "min_opportunities": min_opportunities,
        "max_hours_without_trade": max_hours_without_trade,
        "drift_ratio_threshold": drift_ratio_threshold,
        "verdict": verdict,
        "reason": reason,
    }


# ── Human-readable formatter ──────────────────────────────────────────────────


def format_human_report(report: dict[str, Any]) -> str:
    """Format the report dict as a left-aligned human-readable summary."""
    drift_ratio_str = (
        str(report["drift_ratio"]) if report["drift_ratio"] is not None else "n/a"
    )
    lines: list[str] = []
    lines.append("=== opportunity-vs-trade drift ===")
    lines.append(f"DB                       : {report['db_path']}")
    lines.append(f"trade_log_live           : {report['trade_log_live']}")
    lines.append(f"trade_log_archive_dir    : {report['trade_log_archive_dir']}")
    lines.append(f"clean_start_ts           : {report['clean_start_ts']}")
    lines.append(f"now_ts                   : {report['now_ts']}")
    lines.append(f"hours_elapsed            : {report['hours_elapsed']}")
    lines.append(f"opportunity_count        : {report['opportunity_count']}")
    lines.append(
        f"paper_trade_count        : {report['paper_trade_count']} "
        f"({report['paper_trade_distinct_tickers']} distinct tickers)"
    )
    lines.append(f"drift_ratio              : {drift_ratio_str}")
    lines.append(f"parse_errors             : {report['parse_errors']}")
    lines.append(
        f"thresholds               : min_opp={report['min_opportunities']} "
        f"max_hours={report['max_hours_without_trade']} "
        f"ratio_threshold={report['drift_ratio_threshold']}"
    )
    lines.append(f"verdict                  : {report['verdict']}")
    if report["reason"]:
        lines.append(f"reason                   : {report['reason']}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="path to paper_trades.db (read-only).",
    )
    parser.add_argument(
        "--trade-log-live",
        type=Path,
        default=DEFAULT_TRADE_LOG_LIVE,
        help="path to the live trades.jsonl file.",
    )
    parser.add_argument(
        "--trade-log-archive-dir",
        type=Path,
        default=DEFAULT_TRADE_LOG_ARCHIVE_DIR,
        help="path to the day-partitioned archive directory.",
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
        "--min-opportunities",
        type=int,
        default=DEFAULT_MIN_OPPORTUNITIES,
        help=(
            "minimum OPPORTUNITY count before a drift verdict is meaningful "
            f"(default {DEFAULT_MIN_OPPORTUNITIES})."
        ),
    )
    parser.add_argument(
        "--max-hours-without-trade",
        type=float,
        default=DEFAULT_MAX_HOURS_WITHOUT_TRADE,
        help=(
            "maximum permissible hours from clean-start with zero trades when "
            f"opportunity >= min-opportunities (default {DEFAULT_MAX_HOURS_WITHOUT_TRADE})."
        ),
    )
    parser.add_argument(
        "--drift-ratio-threshold",
        type=float,
        default=DEFAULT_DRIFT_RATIO_THRESHOLD,
        help=(
            "minimum acceptable paper_trade_count / opportunity_count ratio "
            f"(default {DEFAULT_DRIFT_RATIO_THRESHOLD})."
        ),
    )
    parser.add_argument(
        "--now",
        dest="now_ts",
        default=None,
        help="override 'now' for deterministic testing (ISO-8601).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the report as JSON instead of a human-readable summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns 0 on successful report, 2 on watcher failure."""
    parser = _build_argparser()
    args = parser.parse_args(argv)
    try:
        report = collect_report(
            db_path=Path(args.db),
            trade_log_live=Path(args.trade_log_live),
            trade_log_archive_dir=Path(args.trade_log_archive_dir),
            clean_start_ts=args.clean_start_ts,
            min_opportunities=int(args.min_opportunities),
            max_hours_without_trade=float(args.max_hours_without_trade),
            drift_ratio_threshold=float(args.drift_ratio_threshold),
            now_ts=args.now_ts,
        )
    except WatcherError as exc:
        print(f"opportunity_trade_drift: ERROR {exc}", file=sys.stderr)
        return 2

    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(format_human_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
