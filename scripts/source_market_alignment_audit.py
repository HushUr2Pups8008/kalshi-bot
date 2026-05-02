"""
ROADMAP P3.3 — Source-market alignment audit.

Question under test
-------------------
Which (source, series_ticker) pairings produce matches that the LLM
actually uses to update its estimate, and which pairings produce
matches that the LLM silently passes through as ``est == market_price``?
A pairing that reliably generates pass-through outputs is structurally
low-signal for the current LLM prompt — there is no path from "this
source matched this series" to "trader would update belief," because
the LLM consistently declines to update.

Pairing granularity
-------------------
``(source, series_ticker)``. Series is the prefix of the Kalshi ticker
up to the first dash, e.g. ``KXTRUMPIRAN-26MAY01 → KXTRUMPIRAN``. This
matches the series grouping used by ``paper_trader.resolve_market``'s
``keyword_outcomes`` path and keeps the audit at a useful granularity:
many individual markets share a series and should be treated together
for alignment purposes.

Signal-value metric
-------------------
Since zero paper trades have resolved in the relevant windows (blocked
behind P0-GATE), ``win_rate`` and ``pnl_per_match`` are unavailable. We
use ``anchor_rate`` — the fraction of joined rows where
``|final_probability - market_price| < 1e-3`` — as the best proxy
available today:

- ``anchor_rate ≈ 1.0`` ⇒ LLM never updates on this pairing's matches.
  Structurally low signal. A candidate for future filtering.
- ``anchor_rate < 0.8`` ⇒ LLM produces non-neutral outputs on this
  pairing. Candidate keeper; one of the few pairings contributing
  usable signal today.

Classification bands (all configurable via CLI):

- ``insufficient``:     n < ``--min-n`` (default 5). Too few joined
  rows to draw a conclusion; kept out of all other buckets.
- ``flagged_low_signal``: n ≥ min_n AND anchor_rate ≥ ``--low-signal-anchor``
  (default 0.95). Reliably produces pass-through outputs.
- ``keeper``:           n ≥ min_n AND anchor_rate <
  ``--keeper-anchor`` (default 0.80). Produces non-neutral outputs
  meaningfully often.
- ``middling``:         everything else (n ≥ min_n and 0.80 ≤
  anchor_rate < 0.95). Not clearly flagged or clearly keeping.

Output
------
- Top N flagged pairings (worst anchor rate, tie-break by n descending)
- Top N keepers (lowest anchor rate with n ≥ min_n)
- Per-source summary: n, anchor rate, #flagged_pairs
- Per-series summary: n, anchor rate, #flagged_pairs
- Overall band counts

PASSIVE ONLY — read-only analysis. No runtime behavior is modified.

Usage
-----
    python scripts/source_market_alignment_audit.py \
        --since 2026-04-22 --until 2026-04-24 --exclude-test
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.diagnostics_script_helpers import (
    add_exclude_test_arg,
    add_path_arg,
    add_since_arg,
    add_until_arg,
    parse_date_end,
    parse_date_start,
)

# Reuse the MATCH_DIAGNOSTIC + SIGNAL_ANALYSIS_DETAIL collection /
# join logic from the P3.1/P3.2 script. Same join key, same data
# shape; duplicating would drift.
from scripts.flag_outcome_correlation import (
    EST_EQ_MKT_TOL,
    MatchKey,
    _wilson_ci,
    collect,
)

DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"

# Classification thresholds -- CLI-overridable.
DEFAULT_MIN_N = 5
DEFAULT_LOW_SIGNAL_ANCHOR = 0.95
DEFAULT_KEEPER_ANCHOR = 0.80


@dataclass
class PairStats:
    source: str
    series_ticker: str
    n: int = 0
    anchor: int = 0

    @property
    def anchor_rate(self) -> float:
        return self.anchor / self.n if self.n else 0.0

    def classify(self, min_n: int, low_signal_anchor: float, keeper_anchor: float) -> str:
        if self.n < min_n:
            return "insufficient"
        if self.anchor_rate >= low_signal_anchor:
            return "flagged_low_signal"
        if self.anchor_rate < keeper_anchor:
            return "keeper"
        return "middling"


def _series_ticker(ticker: str) -> str:
    """Prefix of the ticker up to the first dash. Mirrors the convention
    used by `paper_trader.resolve_market`."""
    return ticker.split("-", 1)[0] if ticker else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P3.3 — audit source/series-ticker alignment by LLM anchor rate",
    )
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text="Path to trade-log file or root (default: logs/trades/)",
    )
    add_since_arg(parser, help_text="Inclusive start date in YYYY-MM-DD")
    add_until_arg(parser, help_text="Inclusive end date in YYYY-MM-DD")
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    parser.add_argument(
        "--min-n", type=int, default=DEFAULT_MIN_N,
        help=f"Minimum joined rows per pair to classify it (default: {DEFAULT_MIN_N})",
    )
    parser.add_argument(
        "--low-signal-anchor", type=float, default=DEFAULT_LOW_SIGNAL_ANCHOR,
        help=(
            "Anchor-rate threshold at or above which a pair is flagged as "
            f"low-signal (default: {DEFAULT_LOW_SIGNAL_ANCHOR})"
        ),
    )
    parser.add_argument(
        "--keeper-anchor", type=float, default=DEFAULT_KEEPER_ANCHOR,
        help=(
            "Anchor-rate threshold strictly below which a pair is a keeper "
            f"(default: {DEFAULT_KEEPER_ANCHOR})"
        ),
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Maximum rows to show in the flagged / keeper / per-group tables (default: 20)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print per-event join diagnostics on stderr",
    )
    return parser.parse_args()


def aggregate(
    match_index: dict[MatchKey, Any],
    analysis_rows: list[tuple[MatchKey, Any]],
) -> tuple[dict[tuple[str, str], PairStats], int]:
    """Build per-(source, series_ticker) stats. Returns (pairs, unpaired_analysis_rows)."""
    pairs: dict[tuple[str, str], PairStats] = {}
    unpaired = 0
    for key, row in analysis_rows:
        info = match_index.get(key)
        if info is None:
            unpaired += 1
            continue
        series = _series_ticker(row.ticker)
        pair_key = (row.source, series)
        stats = pairs.get(pair_key)
        if stats is None:
            stats = PairStats(source=row.source, series_ticker=series)
            pairs[pair_key] = stats
        stats.n += 1
        if abs(row.final_probability - row.market_price) < EST_EQ_MKT_TOL:
            stats.anchor += 1
    return pairs, unpaired


def _fmt_pair_row(p: PairStats) -> str:
    if p.n == 0:
        return f"  {p.source[:38]:<38} {p.series_ticker:<22} n={p.n:>4}  anchor=—         ci=—"
    lo, hi = _wilson_ci(p.anchor, p.n)
    return (
        f"  {p.source[:38]:<38} {p.series_ticker:<22} n={p.n:>4}  "
        f"anchor={p.anchor_rate * 100:>6.2f}%  ci=[{lo * 100:>5.2f}%, {hi * 100:>5.2f}%]"
    )


def _fmt_group_row(label: str, n: int, anchor: int, flagged_count: int) -> str:
    if n == 0:
        return f"  {label:<40} n={n:>5}  anchor=—         flagged_pairs={flagged_count}"
    rate = anchor / n
    lo, hi = _wilson_ci(anchor, n)
    return (
        f"  {label:<40} n={n:>5}  anchor={rate * 100:>6.2f}%  "
        f"ci=[{lo * 100:>5.2f}%, {hi * 100:>5.2f}%]  flagged_pairs={flagged_count}"
    )


def render(
    pairs: dict[tuple[str, str], PairStats],
    unpaired: int,
    *,
    min_n: int,
    low_signal_anchor: float,
    keeper_anchor: float,
    top: int,
) -> None:
    total_pairs = len(pairs)
    total_n = sum(p.n for p in pairs.values())
    total_anchor = sum(p.anchor for p in pairs.values())

    print("=" * 80)
    print("P3.3 — Source / Series-Ticker Alignment Audit")
    print("=" * 80)
    print(
        f"Pairs: {total_pairs}   Joined rows: {total_n}   "
        f"Unpaired analysis rows: {unpaired}"
    )
    if total_n == 0:
        print(
            "\nNo joined rows — either the window is empty or the join key "
            "does not match any records. Widen --since/--until or inspect "
            "MATCH_DIAGNOSTIC / SIGNAL_ANALYSIS_DETAIL schemas."
        )
        return
    overall_rate = total_anchor / total_n
    lo, hi = _wilson_ci(total_anchor, total_n)
    print(
        f"Overall anchor rate: {overall_rate * 100:.2f}% "
        f"({total_anchor}/{total_n}) "
        f"Wilson 95% CI [{lo * 100:.2f}%, {hi * 100:.2f}%]"
    )

    classified = [(p, p.classify(min_n, low_signal_anchor, keeper_anchor)) for p in pairs.values()]
    flagged = [p for p, c in classified if c == "flagged_low_signal"]
    keepers = [p for p, c in classified if c == "keeper"]
    middling = [p for p, c in classified if c == "middling"]
    insufficient = [p for p, c in classified if c == "insufficient"]

    print(
        f"\nClassification bands "
        f"(min_n={min_n}, low_signal_anchor≥{low_signal_anchor:.2f}, keeper_anchor<{keeper_anchor:.2f}):"
    )
    print(f"  flagged_low_signal : {len(flagged):>4}")
    print(f"  keeper             : {len(keepers):>4}")
    print(f"  middling           : {len(middling):>4}")
    print(f"  insufficient       : {len(insufficient):>4}  (n < {min_n})")

    # Worst-first: highest anchor rate, ties broken by largest n.
    flagged_sorted = sorted(flagged, key=lambda p: (-p.anchor_rate, -p.n))
    print(f"\nTop {min(top, len(flagged_sorted))} flagged_low_signal pairs (worst anchor rate first):")
    if flagged_sorted:
        for p in flagged_sorted[:top]:
            print(_fmt_pair_row(p))
    else:
        print("  (none — no pairs with n ≥ min_n exceeded the low-signal anchor threshold)")

    # Best-first: lowest anchor rate, ties broken by largest n.
    keepers_sorted = sorted(keepers, key=lambda p: (p.anchor_rate, -p.n))
    print(f"\nTop {min(top, len(keepers_sorted))} keeper pairs (lowest anchor rate first):")
    if keepers_sorted:
        for p in keepers_sorted[:top]:
            print(_fmt_pair_row(p))
    else:
        print("  (none — expected while overall anchor rate remains ≥ keeper_anchor threshold)")

    # Per-source aggregate.
    per_source_n: dict[str, int] = defaultdict(int)
    per_source_anchor: dict[str, int] = defaultdict(int)
    per_source_flagged: dict[str, int] = defaultdict(int)
    for p, c in classified:
        per_source_n[p.source] += p.n
        per_source_anchor[p.source] += p.anchor
        if c == "flagged_low_signal":
            per_source_flagged[p.source] += 1
    per_source = sorted(
        per_source_n.items(),
        key=lambda kv: -kv[1],
    )
    print(f"\nTop {min(top, len(per_source))} sources by joined-row volume:")
    for src, n in per_source[:top]:
        print(_fmt_group_row(src[:40], n, per_source_anchor[src], per_source_flagged[src]))

    # Per-series aggregate.
    per_series_n: dict[str, int] = defaultdict(int)
    per_series_anchor: dict[str, int] = defaultdict(int)
    per_series_flagged: dict[str, int] = defaultdict(int)
    for p, c in classified:
        per_series_n[p.series_ticker] += p.n
        per_series_anchor[p.series_ticker] += p.anchor
        if c == "flagged_low_signal":
            per_series_flagged[p.series_ticker] += 1
    per_series = sorted(per_series_n.items(), key=lambda kv: -kv[1])
    print(f"\nTop {min(top, len(per_series))} series tickers by joined-row volume:")
    for series, n in per_series[:top]:
        print(_fmt_group_row(series, n, per_series_anchor[series], per_series_flagged[series]))


def main() -> int:
    args = parse_args()
    log_path = Path(args.path)
    since = parse_date_start(getattr(args, "since", None))
    until = parse_date_end(getattr(args, "until", None))

    match_index, analysis_rows, read_stats = collect(
        log_path,
        since,
        until,
        args.exclude_test,
        verbose=args.verbose,
    )
    pairs, unpaired = aggregate(match_index, analysis_rows)
    render(
        pairs,
        unpaired,
        min_n=args.min_n,
        low_signal_anchor=args.low_signal_anchor,
        keeper_anchor=args.keeper_anchor,
        top=args.top,
    )

    if args.verbose:
        print(
            f"\n[verbose] read_stats: lines_total={read_stats.lines_total}, "
            f"lines_malformed={read_stats.lines_malformed}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
