"""I-10 corpus contamination-window marker with retention.

Sixth and final Phase 1 deliverable of the paper-mode rapid-learning
framework v3 (see
``docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md``
§3 I-10 + §4 §16.7). Closes Codex blocker E: silently excluding rows from
the bot's T1/T2 observation windows creates the same sampling bias the
gate-machinery was meant to avoid.

Design summary
==============

During a T1/T2 observation window for some change ``change_id``, the bot
keeps paper-trading. Each row written under that window is *labeled*
with the contamination tag

    cohort_extension = f"contamination_window:{change_id}:{window_id}"

stamped on ``paper_trades.cohort_extension``. The label is the only
behavioral change — the bot does not alter execution decisions. The
contamination is observational: the candidate code was under measurement
when those trades were taken, so they are not a clean OOS sample for
*other* changes.

Two consumers, two contracts
----------------------------

* The I-1 corpus builder (``scripts.edge_replay.build_corpus``) excludes
  contamination rows by default (``include_contamination=False``) so
  pre-deploy gates for *other* changes are not polluted.
* The I-11 T1 retrospective uses ``export_contamination_corpus`` to dump
  the contamination rows for the specific ``change_id`` as a separate
  OOS corpus — the "this is what the bot did under candidate code"
  gold-standard measurement.

Both consumers see the same rows; only the framing changes. That is the
apples-to-apples retention Codex blocker E demands: nothing is silently
discarded.

Tier classification (per the I-5 classifier shipped in PR #21):
``scripts/edge_replay/*.py`` is **T0** — observability/mechanical,
read-only with no path to a paper or live trade decision. The I-10
write-side hook in ``trading/paper_trader.py`` is the *only* surface in
this deliverable that can affect a live DB row; that hook is fail-soft.

Public API
==========

* :data:`CONTAMINATION_SENTINEL_PATH` — sentinel file location.
* :data:`CONTAMINATION_CORPORA_DIR` — exported-corpus output directory.
* :data:`CONTAMINATION_PREFIX` — ``"contamination_window"`` (used by
  read-side LIKE filter and tag construction).
* :class:`ContaminationWindow` — immutable view of the active window.
* :class:`ContaminationWindowAlreadyOpenError` — raised by
  :func:`open_window` if the sentinel exists. Operator must close first.
* :func:`get_active_window` — read sentinel; ``None`` if absent or
  malformed (fail-soft for write-path callers).
* :func:`open_window` — atomic create-sentinel.
* :func:`close_active_window` — atomic delete-sentinel.
* :func:`cohort_extension_tag` — render the tag for the INSERT.
* :func:`export_contamination_corpus` — write JSONL of rows matching a
  ``change_id`` to ``logs/edge_replay/contamination_corpora/{id}.jsonl``.

CLI: ``open`` / ``close`` / ``status`` / ``export`` subcommands.

Exit codes (CLI):

* ``0`` — operation succeeded.
* ``2`` — operator/IO error (sentinel exists for ``open``, missing DB
  for ``export``, malformed sentinel for ``status``, etc.).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONTAMINATION_SENTINEL_PATH = Path(
    "logs/edge_replay/active_contamination_window.json"
)
CONTAMINATION_CORPORA_DIR = Path("logs/edge_replay/contamination_corpora")
CONTAMINATION_PREFIX = "contamination_window"

_DEFAULT_PAPER_TRADES_DB = Path("data/paper_trades.db")


# ---------------------------------------------------------------------------
# Types and errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContaminationWindow:
    """Immutable snapshot of an active contamination window."""

    change_id: str
    window_id: str
    opened_at_utc: str
    closes_at_utc: str


class ContaminationWindowAlreadyOpenError(RuntimeError):
    """Raised by :func:`open_window` when the sentinel file already exists.

    Two concurrent contamination windows would produce ambiguous
    ``cohort_extension`` tags. The operator must explicitly
    :func:`close_active_window` before opening a new one.
    """


# ---------------------------------------------------------------------------
# Time helpers (UTC-only)
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compact_window_id(dt: datetime) -> str:
    """Render ``dt`` as ``YYYYMMDDTHHMMSSZ`` — compact ISO-8601 form.

    Compact form is used because the tag is stored inside
    ``cohort_extension`` and shows up in JSONL filenames; the canonical
    form ``YYYY-MM-DDTHH:MM:SSZ`` contains colons that are illegal on
    Windows filesystems (mirrors the same constraint enforced by
    :func:`scripts.edge_replay.build_corpus._default_output_path`).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# Sentinel I/O
# ---------------------------------------------------------------------------


def _resolve_sentinel(path: Path | None) -> Path:
    return path if path is not None else CONTAMINATION_SENTINEL_PATH


def get_active_window(
    sentinel_path: Path | None = None,
) -> ContaminationWindow | None:
    """Return the active :class:`ContaminationWindow`, or ``None``.

    Returns ``None`` when:

    * the sentinel file is absent, or
    * the file exists but the payload is unreadable / malformed
      (fail-soft).

    Write-path callers (``trading.paper_trader``) rely on this never
    raising so the trade write is not blocked by a corrupt sentinel.
    """
    path = _resolve_sentinel(sentinel_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return ContaminationWindow(
            change_id=str(payload["change_id"]),
            window_id=str(payload["window_id"]),
            opened_at_utc=str(payload["opened_at_utc"]),
            closes_at_utc=str(payload["closes_at_utc"]),
        )
    except KeyError:
        return None


def open_window(
    change_id: str,
    duration_seconds: int,
    *,
    sentinel_path: Path | None = None,
    now_utc: datetime | None = None,
) -> ContaminationWindow:
    """Atomically open a new contamination window.

    Writes the sentinel via ``Path.replace`` from a sibling ``.tmp`` file
    so the visible state never observes a partial write. Raises
    :class:`ContaminationWindowAlreadyOpenError` if the sentinel already
    exists — operator must :func:`close_active_window` first.
    """
    if not change_id or not change_id.strip():
        raise ValueError("change_id must be a non-empty string")
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")

    path = _resolve_sentinel(sentinel_path)
    if path.exists():
        raise ContaminationWindowAlreadyOpenError(
            f"contamination sentinel already exists at {path}; "
            "close the active window before opening a new one"
        )

    opened = now_utc or _now_utc()
    closes = opened + timedelta(seconds=duration_seconds)
    window = ContaminationWindow(
        change_id=change_id,
        window_id=_compact_window_id(opened),
        opened_at_utc=_iso_z(opened),
        closes_at_utc=_iso_z(closes),
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(asdict(window), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return window


def close_active_window(
    *,
    sentinel_path: Path | None = None,
) -> ContaminationWindow | None:
    """Delete the sentinel and return the previously-active window.

    Returns ``None`` if there was no active window (no-op). Returns the
    parsed window if a sentinel existed and was removed. If the sentinel
    file is malformed it is still removed and ``None`` is returned —
    leaving a corrupt sentinel in place would block all future
    :func:`open_window` calls.
    """
    path = _resolve_sentinel(sentinel_path)
    if not path.exists():
        return None
    window = get_active_window(sentinel_path=path)
    try:
        path.unlink()
    except FileNotFoundError:
        # Raced with another close; treat as already-closed.
        return window
    return window


def cohort_extension_tag(window: ContaminationWindow) -> str:
    """Render the canonical ``cohort_extension`` tag for a window.

    Stored verbatim in ``paper_trades.cohort_extension``. Read by the
    I-1 corpus builder LIKE filter and the I-11 retrospective exporter.
    """
    return f"{CONTAMINATION_PREFIX}:{window.change_id}:{window.window_id}"


# ---------------------------------------------------------------------------
# Corpus export
# ---------------------------------------------------------------------------


def _default_export_path(change_id: str) -> Path:
    # Preserve the change_id verbatim; operators choose the id and own
    # any filesystem-safety implications.
    return CONTAMINATION_CORPORA_DIR / f"{change_id}.jsonl"


def _row_to_jsonable(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def export_contamination_corpus(
    change_id: str,
    *,
    paper_trades_db: Path | None = None,
    output_path: Path | None = None,
) -> Path:
    """Export contamination-tagged rows for ``change_id`` to JSONL.

    Reads ``paper_trades.db`` read-only and writes
    ``logs/edge_replay/contamination_corpora/{change_id}.jsonl`` (or the
    caller-supplied path). Each emitted JSONL line is the source row
    dict from ``paper_trades`` plus the parsed ``window_id`` for
    convenience.

    Atomic write via ``Path.replace``. Returns the resolved output path
    even when zero rows match (empty JSONL is a valid corpus — it
    documents that no trades occurred under the window).
    """
    if not change_id or not change_id.strip():
        raise ValueError("change_id must be a non-empty string")

    db_path = paper_trades_db or _DEFAULT_PAPER_TRADES_DB
    if not db_path.exists():
        raise FileNotFoundError(f"paper_trades DB not found: {db_path}")

    resolved_output = output_path or _default_export_path(change_id)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows: list[dict] = []
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        existing_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")
        }
        if "cohort_extension" not in existing_cols:
            # Migration has not yet run; no rows can carry the tag.
            # Treat as a clean empty export rather than an error so the
            # exporter is safe to invoke pre-migration.
            tag_prefix = ""
            cursor = []
        else:
            tag_prefix = f"{CONTAMINATION_PREFIX}:{change_id}:"
            cursor = conn.execute(
                "SELECT * FROM paper_trades "
                "WHERE cohort_extension LIKE ? "
                "ORDER BY ts ASC, trade_id ASC",
                (tag_prefix + "%",),
            ).fetchall()
        for raw in cursor:
            row = _row_to_jsonable(raw)
            tag = row.get("cohort_extension") or ""
            # Defensive: only emit if the tag matches the requested
            # change_id exactly (LIKE prefix scan is fine, but other
            # operators could theoretically inject a colon).
            if not tag.startswith(tag_prefix):
                continue
            row["contamination_window_id"] = tag[len(tag_prefix) :]
            rows.append(row)
    finally:
        conn.close()

    tmp_path = resolved_output.with_name(resolved_output.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, sort_keys=True))
            out.write("\n")
    tmp_path.replace(resolved_output)
    return resolved_output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_open(args: argparse.Namespace) -> int:
    try:
        window = open_window(
            args.change_id,
            args.duration_seconds,
            sentinel_path=args.sentinel,
        )
    except ContaminationWindowAlreadyOpenError as exc:
        print(f"[contamination] {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[contamination] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(window), sort_keys=True, indent=2))
    return 0


def _cmd_close(args: argparse.Namespace) -> int:
    window = close_active_window(sentinel_path=args.sentinel)
    if window is None:
        print(json.dumps({"closed": None}, sort_keys=True, indent=2))
        return 0
    payload = {"closed": asdict(window)}
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    window = get_active_window(sentinel_path=args.sentinel)
    if window is None:
        print(json.dumps({"active": None}, sort_keys=True, indent=2))
        return 0
    payload = {
        "active": asdict(window),
        "cohort_extension_tag": cohort_extension_tag(window),
    }
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    try:
        path = export_contamination_corpus(
            args.change_id,
            paper_trades_db=args.db,
            output_path=args.output,
        )
    except FileNotFoundError as exc:
        print(f"[contamination] {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[contamination] {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"change_id": args.change_id, "output_path": str(path)},
            sort_keys=True,
            indent=2,
        )
    )
    return 0


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Manage the active contamination window sentinel and export "
            "contamination corpora for the I-11 T1 retrospective "
            "(framework v3 I-10)."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_open = sub.add_parser("open", help="Open a new contamination window")
    p_open.add_argument("--change-id", required=True)
    p_open.add_argument(
        "--duration-seconds",
        type=int,
        required=True,
        help="Window duration; sets closes_at_utc but is informational only",
    )
    p_open.add_argument(
        "--sentinel",
        type=Path,
        default=None,
        help=f"Sentinel path (default: {CONTAMINATION_SENTINEL_PATH})",
    )
    p_open.set_defaults(func=_cmd_open)

    p_close = sub.add_parser("close", help="Close the active contamination window")
    p_close.add_argument(
        "--sentinel",
        type=Path,
        default=None,
        help=f"Sentinel path (default: {CONTAMINATION_SENTINEL_PATH})",
    )
    p_close.set_defaults(func=_cmd_close)

    p_status = sub.add_parser("status", help="Report active window, if any")
    p_status.add_argument(
        "--sentinel",
        type=Path,
        default=None,
        help=f"Sentinel path (default: {CONTAMINATION_SENTINEL_PATH})",
    )
    p_status.set_defaults(func=_cmd_status)

    p_export = sub.add_parser(
        "export", help="Export the contamination corpus for a change_id"
    )
    p_export.add_argument("--change-id", required=True)
    p_export.add_argument(
        "--db",
        type=Path,
        default=None,
        help=f"Path to paper_trades.db (default: {_DEFAULT_PAPER_TRADES_DB})",
    )
    p_export.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output JSONL path "
            f"(default: {CONTAMINATION_CORPORA_DIR}/<change_id>.jsonl)"
        ),
    )
    p_export.set_defaults(func=_cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
