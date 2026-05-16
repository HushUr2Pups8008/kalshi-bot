#!/usr/bin/env python3
"""POST_FIX_NEW corpus-readiness watcher.

Reports whether the POST_FIX_NEW replay cohort has accumulated enough
clean post-P0 / post-hotfix evidence to satisfy the downstream
Cycle-17D / PROFIT-EDGE-012 resume gate:

- at least 200 production-proxy-complete rows,
- at least one 4-axis bin with at least 10 admitted rows,
- at least 95% production-proxy completeness.

The 4-axis key is reported as
``signal_source × market_family × signal_type × news_class``. Current
runtime ``paper_trades`` rows do not persist every replay axis, so the
watcher uses replay-compatible fallbacks: ``market_family`` falls back
to ``series_ticker`` / ``ticker`` and missing ``news_class`` is grouped
as ``unknown``.

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
DEFAULT_MIN_TRADES = 200
DEFAULT_MIN_TICKERS = 0
DEFAULT_MIN_BIN_ADMISSIONS = 10
DEFAULT_MIN_COMPLETENESS_RATIO = 0.95
DEFAULT_READINESS_CONFIDENCE = 0.85
DEFAULT_READINESS_YES_THRESHOLD = 0.60
DEFAULT_READINESS_NO_THRESHOLD = 0.40


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


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _truthy(value: Any) -> bool:
    if _is_blank(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _row_value(row: sqlite3.Row, columns: set[str], *names: str) -> Any:
    for name in names:
        if name in columns:
            return row[name]
    return None


def _as_float(value: Any) -> float | None:
    if _is_blank(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_production_proxy_fields(row: sqlite3.Row, columns: set[str]) -> bool:
    """Return True when a paper-trade row can feed scorer production-proxy replay."""
    price = _row_value(row, columns, "entry_price_cents", "market_yes_price")
    model_prob = _row_value(row, columns, "model_prob", "estimated_prob")
    confidence = _row_value(row, columns, "confidence", "llm_confidence")
    return all(
        not _is_blank(value)
        for value in (
            price,
            _row_value(row, columns, "edge"),
            model_prob,
            confidence,
            _row_value(row, columns, "resolved_yes"),
        )
    )


def _is_readiness_admitted(row: sqlite3.Row, columns: set[str]) -> bool:
    """Mirror the replay readiness admission predicate for runtime DB rows."""
    if "readiness_admitted" in columns:
        return _truthy(row["readiness_admitted"])

    confidence = _as_float(_row_value(row, columns, "confidence", "llm_confidence"))
    model_prob = _as_float(_row_value(row, columns, "model_prob", "estimated_prob"))
    if (
        confidence is None
        or model_prob is None
        or confidence < DEFAULT_READINESS_CONFIDENCE
    ):
        return False
    return (
        model_prob >= DEFAULT_READINESS_YES_THRESHOLD
        or model_prob <= DEFAULT_READINESS_NO_THRESHOLD
    )


def _axis_key(row: sqlite3.Row, columns: set[str]) -> tuple[str, str, str, str] | None:
    signal_source = str(_row_value(row, columns, "signal_source") or "unknown").strip()
    market_family = str(
        _row_value(row, columns, "market_family", "series_ticker", "ticker")
        or "unknown"
    ).strip()
    signal_type = str(_row_value(row, columns, "signal_type") or "unknown").strip()
    news_class = str(_row_value(row, columns, "news_class") or "unknown").strip()
    values = (signal_source, market_family, signal_type, news_class)
    if any(not value for value in values):
        return None
    return values


def collect_readiness(
    *,
    db_path: Path,
    clean_start_ts: str,
    min_trades: int,
    min_tickers: int,
    min_bin_admissions: int = DEFAULT_MIN_BIN_ADMISSIONS,
    min_completeness_ratio: float = DEFAULT_MIN_COMPLETENESS_RATIO,
) -> dict[str, Any]:
    """Build the readiness report as a plain dict.

    The dict shape is stable and forms the JSON-output contract; tests
    rely on the exact key names.
    """
    clean_start_dt = _parse_iso(clean_start_ts, label="clean-start")

    conn = _open_db_read_only(db_path)
    conn.row_factory = sqlite3.Row
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

        post_rows = conn.execute(
            "SELECT * FROM paper_trades WHERE ts >= ?",
            (clean_start_iso,),
        ).fetchall()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(paper_trades)")}
        complete_rows = [
            row for row in post_rows if _has_production_proxy_fields(row, columns)
        ]
        complete_count = len(complete_rows)
        completeness_ratio = (
            complete_count / post_count if post_count else 0.0
        )
        bin_counts: dict[tuple[str, str, str, str], int] = {}
        for row in complete_rows:
            if not _is_readiness_admitted(row, columns):
                continue
            axis_key = _axis_key(row, columns)
            if axis_key is None:
                continue
            bin_counts[axis_key] = bin_counts.get(axis_key, 0) + 1
        top_bins = sorted(
            (
                {
                    "signal_source": key[0],
                    "market_family": key[1],
                    "signal_type": key[2],
                    "news_class": key[3],
                    "admissions": count,
                }
                for key, count in bin_counts.items()
            ),
            key=lambda item: (-int(item["admissions"]), str(item["signal_source"])),
        )
        max_bin_admissions = int(top_bins[0]["admissions"]) if top_bins else 0
        qualifying_bin_count = sum(
            1 for item in top_bins if int(item["admissions"]) >= min_bin_admissions
        )

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
    elif complete_count < min_trades:
        verdict = "NOT_READY"
        reason = (
            f"post-clean-start production-proxy-complete rows {complete_count} < "
            f"min_trades {min_trades}"
        )
    elif completeness_ratio < min_completeness_ratio:
        verdict = "NOT_READY"
        reason = (
            f"production-proxy completeness {completeness_ratio:.3f} < "
            f"min_completeness_ratio {min_completeness_ratio:.3f}"
        )
    elif qualifying_bin_count < 1:
        verdict = "NOT_READY"
        reason = (
            f"no 4-axis bin has >= {min_bin_admissions} admitted rows "
            f"(max {max_bin_admissions})"
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
        "post_clean_start_production_proxy_complete_rows": complete_count,
        "production_proxy_completeness_ratio": round(completeness_ratio, 6),
        "min_trades_required": min_trades,
        "min_tickers_required": min_tickers,
        "min_production_proxy_complete_required": min_trades,
        "min_completeness_ratio_required": min_completeness_ratio,
        "min_4axis_bin_admissions_required": min_bin_admissions,
        "qualifying_4axis_bin_count": qualifying_bin_count,
        "max_4axis_bin_admissions": max_bin_admissions,
        "top_4axis_bins": top_bins[:10],
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
        "production_proxy    : "
        f"{report['post_clean_start_production_proxy_complete_rows']} complete rows "
        f"/ completeness {report['production_proxy_completeness_ratio']:.3f}"
    )
    lines.append(
        "4axis_bins          : "
        f"{report['qualifying_4axis_bin_count']} qualifying "
        f"/ max_admissions={report['max_4axis_bin_admissions']}"
    )
    lines.append(
        f"thresholds          : min_trades={report['min_trades_required']} "
        f"min_tickers={report['min_tickers_required']} "
        f"min_bin_admissions={report['min_4axis_bin_admissions_required']} "
        f"min_completeness={report['min_completeness_ratio_required']:.3f}"
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
        help=(
            "minimum post-clean-start production-proxy-complete rows to declare "
            "READY."
        ),
    )
    parser.add_argument(
        "--min-tickers",
        type=int,
        default=DEFAULT_MIN_TICKERS,
        help="minimum post-clean-start distinct tickers to declare READY.",
    )
    parser.add_argument(
        "--min-bin-admissions",
        type=int,
        default=DEFAULT_MIN_BIN_ADMISSIONS,
        help="minimum admitted rows in at least one 4-axis bin to declare READY.",
    )
    parser.add_argument(
        "--min-completeness-ratio",
        type=float,
        default=DEFAULT_MIN_COMPLETENESS_RATIO,
        help="minimum production-proxy completeness ratio to declare READY.",
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
            min_bin_admissions=int(args.min_bin_admissions),
            min_completeness_ratio=float(args.min_completeness_ratio),
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
