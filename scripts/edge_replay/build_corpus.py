"""I-1 general corpus builder with regime/family OOS standard.

Third Phase 1 deliverable of the paper-mode rapid-learning framework v3
(see `docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md`
§3 I-1 + §4 §16.7). Addresses Codex blocker C: calendar-only diversity is
not OOS. A corpus is treated as IN_PERIOD (downstream gates refuse to gate
T1 deploys with only IN_PERIOD inputs) unless the operator declares the
regime AND filters across at least two distinct market families.

This module REPLACES the lineage of:

- ``build_cycle17d_corpus.py``
- ``build_cycle17d_broader_corpus.py``
- ``build_replay_dataset.py``
- ``cycle15b_common.py``

… as the canonical path going forward. Those scripts remain on-disk and
untouched because they are referenced by archived cycle reports and must
keep producing identical historical replay artifacts. This module is the
sanctioned target for all NEW corpus work from the rapid-learning framework
forward.

Tier classification (per the I-5 classifier shipped in PR #21):
``scripts/edge_replay/*.py`` is **T0** — observability/mechanical, read-only
with no path to a paper or live trade decision. Unit-test gate sufficient.

Public API::

    result = build_corpus(
        start_utc=datetime(...),
        end_utc=datetime(...),
        market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=Path(...) | None,
        paper_trades_db=Path(...),
    )

Each emitted JSONL row carries the source columns from ``paper_trades``
PLUS the corpus-stamp fields described in :data:`CORPUS_STAMP_FIELDS`.

Read-only: ``PRAGMA query_only = ON`` on every connection. Never writes
the source DB. The script does NOT modify the legacy scripts above and
does NOT consume or modify ``oos_corpus_seed.py`` — that remains a
separate minimal while-we-wait extractor.

Exit codes (CLI):

- ``0`` — corpus written (regardless of row count or IN_PERIOD flag).
- ``2`` — DB unreadable, required table missing, unregistered regime,
  or argument parse failure.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CORPUS_BUILDER_VERSION = 1

DEFAULT_DB = Path("data/paper_trades.db")
DEFAULT_REGIMES_DOC = Path("docs/governance/corpus-regimes.md")
DEFAULT_OUTPUT_DIR = Path("logs/edge_replay")

CORPUS_STAMP_FIELDS = (
    "corpus_window_start_utc",
    "corpus_window_end_utc",
    "cohort_tag",
    "regime_label",
    "market_families",
    "corpus_builder_version",
    "built_at_utc",
)


class UnregisteredRegimeError(ValueError):
    """Raised when the caller passes a ``regime_label`` not declared in the
    pre-registered regimes file (`docs/governance/corpus-regimes.md`)."""


@dataclass(frozen=True)
class BuildResult:
    output_path: Path
    row_count: int
    corpus_window_start_utc: str
    corpus_window_end_utc: str
    cohort_tag: str
    regime_label: str
    in_period_validation_only: bool
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Time normalization
# ---------------------------------------------------------------------------


def _to_iso_z(dt: datetime) -> str:
    """Canonicalize a datetime to ``YYYY-MM-DDTHH:MM:SSZ``."""
    if dt.tzinfo is None:
        # Treat naive as UTC; preferable to silent local-time drift.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso8601(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Regimes doc parser
# ---------------------------------------------------------------------------


def load_registered_regimes(doc_path: Path) -> set[str]:
    """Parse ``corpus-regimes.md`` and return the set of declared labels.

    The expected format is a markdown table with ``Label`` as the first
    column, where each row's label is wrapped in backticks. The parser
    is tolerant of leading/trailing whitespace, blank lines, headers, and
    non-table prose. Header rows (containing ``Label``) and separator
    rows (``|---|---|``) are skipped.
    """
    if not doc_path.exists():
        raise FileNotFoundError(
            f"corpus regimes doc not found: {doc_path} "
            "(declare regimes there before building a corpus)"
        )

    labels: set[str] = set()
    for raw_line in doc_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("|"):
            continue
        # Strip the leading/trailing pipes, then split.
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        first = cells[0]
        # Skip the header row and separator row.
        if first.lower() == "label" or set(first) <= set("-: "):
            continue
        if first.startswith("`") and first.endswith("`") and len(first) > 2:
            label = first.strip("`").strip()
            if label:
                labels.add(label)
    return labels


# ---------------------------------------------------------------------------
# Family matching
# ---------------------------------------------------------------------------


def _row_families(row: dict[str, Any], requested: Iterable[str]) -> list[str]:
    """Return the subset of ``requested`` families this row matches.

    A row matches a family when either its ``series_ticker`` equals the
    family OR its ``ticker`` starts with the family token (case-insensitive).
    This mirrors the ``build_cycle17d_broader_corpus.py`` precedent.
    """
    ticker = str(row.get("ticker") or "").upper()
    series = str(row.get("series_ticker") or "").upper()
    matched = []
    for fam in requested:
        f_upper = fam.upper()
        if series == f_upper or ticker.startswith(f_upper):
            matched.append(fam)
    return matched


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _stamp_row(
    row: dict[str, Any],
    *,
    window_start: str,
    window_end: str,
    cohort_tag: str,
    regime_label: str,
    market_families: list[str],
    built_at_utc: str,
) -> dict[str, Any]:
    stamped = dict(row)
    stamped["corpus_window_start_utc"] = window_start
    stamped["corpus_window_end_utc"] = window_end
    stamped["cohort_tag"] = cohort_tag
    stamped["regime_label"] = regime_label
    stamped["market_families"] = list(market_families)
    stamped["corpus_builder_version"] = CORPUS_BUILDER_VERSION
    stamped["built_at_utc"] = built_at_utc
    return stamped


def _default_output_path(
    *,
    regime_label: str,
    cohort_tag: str,
    window_start: str,
    window_end: str,
) -> Path:
    # Colons are illegal on Windows filesystems; substitute with '-'.
    safe_start = window_start.replace(":", "-")
    safe_end = window_end.replace(":", "-")
    name = (
        f"corpus_{regime_label}_{cohort_tag}_"
        f"{safe_start}_{safe_end}.jsonl"
    )
    return DEFAULT_OUTPUT_DIR / name


def build_corpus(
    *,
    start_utc: datetime,
    end_utc: datetime,
    market_families: list[str],
    cohort_tag: str,
    regime_label: str,
    output_path: Path | None = None,
    paper_trades_db: Path | None = None,
    evidence_store_db: Path | None = None,  # noqa: ARG001 — reserved for I-2/I-4
    regimes_doc_path: Path | None = None,
    built_at_utc: str | None = None,
) -> BuildResult:
    """Build a replay corpus from ``paper_trades.db`` for one window+regime.

    See module docstring for the OOS standard, blocker-C rationale, and
    the JSONL row contract. Raises :class:`UnregisteredRegimeError` if
    ``regime_label`` is not declared in the regimes doc.
    """
    if not market_families:
        raise ValueError("market_families must be a non-empty list")

    db_path = paper_trades_db or DEFAULT_DB
    doc_path = regimes_doc_path or DEFAULT_REGIMES_DOC

    registered = load_registered_regimes(doc_path)
    if regime_label not in registered:
        raise UnregisteredRegimeError(
            f"regime_label {regime_label!r} is not registered in "
            f"{doc_path}. Declare it there before building a corpus."
        )

    if not db_path.exists():
        raise FileNotFoundError(f"paper_trades DB not found: {db_path}")

    if start_utc >= end_utc:
        raise ValueError(
            "start_utc must be strictly before end_utc; got "
            f"start={start_utc!r}, end={end_utc!r}. Inverted bounds silently "
            "match zero rows and yield a misleading empty corpus."
        )

    window_start = _to_iso_z(start_utc)
    window_end = _to_iso_z(end_utc)
    stamp_built_at = built_at_utc or _now_iso_z()

    resolved_output = output_path or _default_output_path(
        regime_label=regime_label,
        cohort_tag=cohort_tag,
        window_start=window_start,
        window_end=window_end,
    )
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    # Read paper_trades read-only.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.execute("PRAGMA table_info(paper_trades)")
        cols = {row[1] for row in cursor.fetchall()}
        if "ts" not in cols:
            raise RuntimeError(
                "paper_trades table missing required 'ts' column "
                "(corpus builder expects the canonical schema)"
            )

        rows = conn.execute(
            "SELECT * FROM paper_trades "
            "WHERE ts >= ? AND ts < ? ORDER BY ts ASC, trade_id ASC",
            (window_start, window_end),
        ).fetchall()
    finally:
        conn.close()

    # Filter to rows that match at least one of the requested families.
    matched_rows: list[tuple[dict[str, Any], list[str]]] = []
    distinct_families_present: set[str] = set()
    for raw in rows:
        row_dict = _row_to_dict(raw)
        matches = _row_families(row_dict, market_families)
        if matches:
            matched_rows.append((row_dict, matches))
            distinct_families_present.update(matches)

    in_period = len(distinct_families_present) < 2
    notes: list[str] = []
    if in_period:
        notes.append(
            "in_period_validation_only=True: fewer than 2 distinct market "
            "families filtered through; downstream I-4 must refuse to gate "
            "T1 deploys with only IN_PERIOD corpora."
        )

    # Write JSONL atomically by writing then renaming.
    tmp_path = resolved_output.with_name(resolved_output.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as out:
        for row_dict, _matches in matched_rows:
            stamped = _stamp_row(
                row_dict,
                window_start=window_start,
                window_end=window_end,
                cohort_tag=cohort_tag,
                regime_label=regime_label,
                market_families=list(market_families),
                built_at_utc=stamp_built_at,
            )
            out.write(json.dumps(stamped, sort_keys=True))
            out.write("\n")
    tmp_path.replace(resolved_output)

    return BuildResult(
        output_path=resolved_output,
        row_count=len(matched_rows),
        corpus_window_start_utc=window_start,
        corpus_window_end_utc=window_end,
        cohort_tag=cohort_tag,
        regime_label=regime_label,
        in_period_validation_only=in_period,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build an OOS replay corpus from paper_trades.db with regime "
            "and market-family filtering (framework v3 I-1)."
        ),
    )
    parser.add_argument("--start-ts", type=str, required=True, help="ISO-8601 inclusive start (e.g. 2026-05-23T21:22:16Z)")
    parser.add_argument("--end-ts", type=str, required=True, help="ISO-8601 exclusive end")
    parser.add_argument(
        "--market-families",
        type=str,
        required=True,
        help="Comma-separated list of market family prefixes (e.g. KXTRUMPIRAN,KXMOCTRUMP25)",
    )
    parser.add_argument("--cohort-tag", type=str, required=True)
    parser.add_argument(
        "--regime-label",
        type=str,
        required=True,
        help="Must be declared in docs/governance/corpus-regimes.md",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to paper_trades SQLite DB (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSONL path (default: logs/edge_replay/corpus_<regime>_<cohort>_<start>_<end>.jsonl)",
    )
    parser.add_argument(
        "--regimes-doc",
        type=Path,
        default=DEFAULT_REGIMES_DOC,
        help=f"Path to corpus-regimes.md (default: {DEFAULT_REGIMES_DOC})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    try:
        start = _parse_iso8601(args.start_ts)
        end = _parse_iso8601(args.end_ts)
    except ValueError as exc:
        print(f"[build_corpus] invalid timestamp: {exc}", file=sys.stderr)
        return 2

    families = [tok.strip() for tok in args.market_families.split(",") if tok.strip()]
    if not families:
        print("[build_corpus] --market-families produced no tokens", file=sys.stderr)
        return 2

    try:
        result = build_corpus(
            start_utc=start,
            end_utc=end,
            market_families=families,
            cohort_tag=args.cohort_tag,
            regime_label=args.regime_label,
            output_path=args.output,
            paper_trades_db=args.db,
            regimes_doc_path=args.regimes_doc,
        )
    except FileNotFoundError as exc:
        print(f"[build_corpus] {exc}", file=sys.stderr)
        return 2
    except UnregisteredRegimeError as exc:
        print(f"[build_corpus] {exc}", file=sys.stderr)
        return 2
    except (sqlite3.DatabaseError, RuntimeError, ValueError) as exc:
        print(f"[build_corpus] build failed: {exc}", file=sys.stderr)
        return 2

    summary = {
        "output_path": str(result.output_path),
        "row_count": result.row_count,
        "corpus_window_start_utc": result.corpus_window_start_utc,
        "corpus_window_end_utc": result.corpus_window_end_utc,
        "cohort_tag": result.cohort_tag,
        "regime_label": result.regime_label,
        "in_period_validation_only": result.in_period_validation_only,
        "notes": result.notes,
    }
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
