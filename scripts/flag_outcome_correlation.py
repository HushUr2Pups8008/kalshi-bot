"""
ROADMAP P3.1 + P3.2 — Outcome correlation diagnostics.

Scope
-----
Two correlations of ``est == market_price`` (LLM anchoring) against
per-match metadata:

1. **P3.1 — flag correlation.** Does the presence of match-quality
   heuristic flags (``single_named_entity_only``, ``minimal_overlap``,
   ``low_token_overlap``, ``near_threshold_score``) predict a higher
   anchor rate?
2. **P3.2 — specificity-bucket correlation.** Does
   ``market_specificity_score`` (a [0, 1] feature emitted on
   ``MATCH_DIAGNOSTIC`` as of v0.29.49) predict a lower anchor rate in
   high-specificity markets?

Method
------
1. Scan the trade-log root for ``MATCH_DIAGNOSTIC`` events and index them
   by ``(ticker, headline, source)``. Each event contributes a set of
   ``heuristic_flags``, the ``low_match_quality`` boolean, and the
   ``market_specificity_score`` (when present — historical events
   predating v0.29.49 will be missing it, in which case the specificity
   correlation is simply skipped).
2. Scan for ``SIGNAL_ANALYSIS_DETAIL`` events (``method='llm'``,
   ``llm_result_used=True``, excluding startup probes and synthetic
   probes). Each event contributes ``final_probability`` and
   ``market_price``.
3. Inner-join by ``(ticker, headline, source)``. Keep only the paired
   rows -- unpaired rows lack one side of the correlation.
4. For each flag (plus ``any_flag`` and the baseline ``no_flag``),
   compute the fraction of joined rows where
   ``abs(final_probability - market_price) < 1e-3`` (== market anchoring).
5. For each specificity-score bucket (fixed thresholds: [0, 0.25),
   [0.25, 0.50), [0.50, 0.75), [0.75, 1.00], plus a high/low split at
   0.50), compute the same anchor fraction.
6. Print per-group counts, rates, Wilson 95% CI, and a verdict for each
   section.

PASSIVE ONLY -- read-only analysis. No runtime behavior is modified.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
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
    is_test_record_source_only as is_test_record,
    parse_date_end,
    parse_date_start,
)
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records

DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"

# Flags tracked by P3.1. near_threshold_score is included for completeness
# even though it's not in the original P3.1 row (adds value at zero cost).
TRACKED_FLAGS: tuple[str, ...] = (
    "single_named_entity_only",
    "minimal_overlap",
    "low_token_overlap",
    "near_threshold_score",
)

# Tolerance for "est == market_price". LLM outputs are rounded to 4 decimals
# in the log schema; 1e-3 catches exact-anchor cases without false negatives.
EST_EQ_MKT_TOL = 1e-3


@dataclass(frozen=True)
class MatchKey:
    ticker: str
    headline: str
    source: str


@dataclass
class MatchInfo:
    flags: set[str] = field(default_factory=set)
    low_match_quality: bool = False
    # P3.2 — populated when the MATCH_DIAGNOSTIC event carries the field
    # (introduced v0.29.49). None for pre-v0.29.49 historical events.
    market_specificity_score: float | None = None


# Fixed bucket thresholds for the P3.2 specificity correlation.
# Half-open intervals [lo, hi) except the top bucket is closed on both ends
# so score == 1.0 lands somewhere.
_SPECIFICITY_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("[0.00, 0.25)", 0.00, 0.25),
    ("[0.25, 0.50)", 0.25, 0.50),
    ("[0.50, 0.75)", 0.50, 0.75),
    ("[0.75, 1.00]", 0.75, 1.0001),  # +eps so score==1.0 falls in top bucket
)
_SPECIFICITY_HIGH_LOW_SPLIT: float = 0.50


@dataclass
class AnalysisRow:
    ticker: str
    source: str
    final_probability: float
    market_price: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P3.1 — correlate match-quality heuristic flags with est==market outcomes"
    )
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text=(
            "Path to trade-log file or root "
            "(default: logs/trades/ for full archive; supply a specific file to scope)"
        ),
    )
    add_since_arg(parser, help_text="Inclusive start date in YYYY-MM-DD")
    add_until_arg(parser, help_text="Inclusive end date in YYYY-MM-DD")
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-event join diagnostics (unpaired rows, duplicate keys)",
    )
    return parser.parse_args()


def _is_analysis_row(record: dict[str, Any]) -> bool:
    if (record.get("type") or record.get("event")) != "SIGNAL_ANALYSIS_DETAIL":
        return False
    if record.get("method") != "llm":
        return False
    if record.get("is_startup_probe") or record.get("is_synthetic_probe"):
        return False
    if record.get("llm_result_used") is False:
        # Exclude events where the LLM didn't actually contribute -- the est
        # in those rows is the keyword-gate fallback, not the hypothesis under
        # test.
        return False
    if record.get("final_probability") is None or record.get("market_price") is None:
        return False
    return True


def _is_match_diagnostic(record: dict[str, Any]) -> bool:
    if (record.get("type") or record.get("event")) != "MATCH_DIAGNOSTIC":
        return False
    # Probe/synthetic records are conventionally ticker-prefixed KXTEST or
    # sourced r/test; the exclude_test path already handles those.
    return True


def _wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for a binomial proportion. Returns (lo, hi)."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1.0 + (z * z) / n
    center = (p + (z * z) / (2 * n)) / denom
    halfwidth = (z * math.sqrt(p * (1 - p) / n + (z * z) / (4 * n * n))) / denom
    return (max(0.0, center - halfwidth), min(1.0, center + halfwidth))


def collect(
    log_path: Path,
    since,
    until,
    exclude_test: bool,
    *,
    verbose: bool,
) -> tuple[dict[MatchKey, MatchInfo], list[tuple[MatchKey, AnalysisRow]], TradeLogReadStats]:
    match_index: dict[MatchKey, MatchInfo] = {}
    analysis_rows: list[tuple[MatchKey, AnalysisRow]] = []
    read_stats = TradeLogReadStats()

    for record in iter_trade_records(log_path, since=since, until=until, stats=read_stats):
        if exclude_test and is_test_record(record):
            continue

        if _is_match_diagnostic(record):
            ticker = str(record.get("ticker") or "")
            headline = str(record.get("headline") or "")
            source = str(record.get("source") or "")
            if not ticker or not headline:
                continue
            key = MatchKey(ticker=ticker, headline=headline, source=source)
            flags_raw = record.get("heuristic_flags") or []
            flags = {str(f) for f in flags_raw if isinstance(f, str)}
            info = match_index.get(key)
            if info is None:
                info = MatchInfo()
                match_index[key] = info
            info.flags.update(flags)
            if bool(record.get("low_match_quality")):
                info.low_match_quality = True
            # P3.2: capture market_specificity_score when emitted. Multiple
            # MATCH_DIAGNOSTIC events can share a key (same headline matched
            # to the same market twice); the score is deterministic per
            # market, so last-write-wins is safe.
            spec = record.get("market_specificity_score")
            if isinstance(spec, (int, float)) and not isinstance(spec, bool):
                info.market_specificity_score = float(spec)
            continue

        if _is_analysis_row(record):
            ticker = str(record.get("ticker") or "")
            headline = str(record.get("headline") or "")
            source = str(record.get("source") or "")
            if not ticker or not headline:
                continue
            key = MatchKey(ticker=ticker, headline=headline, source=source)
            analysis_rows.append(
                (
                    key,
                    AnalysisRow(
                        ticker=ticker,
                        source=source,
                        final_probability=float(record["final_probability"]),
                        market_price=float(record["market_price"]),
                    ),
                )
            )

    if verbose:
        print(
            f"[verbose] indexed {len(match_index)} unique MATCH_DIAGNOSTIC keys, "
            f"{len(analysis_rows)} SIGNAL_ANALYSIS_DETAIL rows",
            file=sys.stderr,
        )

    return match_index, analysis_rows, read_stats


def correlate(
    match_index: dict[MatchKey, MatchInfo],
    analysis_rows: list[tuple[MatchKey, AnalysisRow]],
) -> dict[str, Any]:
    # Inner-join.
    joined: list[tuple[MatchInfo, AnalysisRow]] = []
    unpaired = 0
    for key, row in analysis_rows:
        info = match_index.get(key)
        if info is None:
            unpaired += 1
            continue
        joined.append((info, row))

    # Per-flag buckets + any_flag + no_flag baseline.
    per_flag_n: dict[str, int] = {flag: 0 for flag in TRACKED_FLAGS}
    per_flag_anchor: dict[str, int] = {flag: 0 for flag in TRACKED_FLAGS}
    any_flag_n = 0
    any_flag_anchor = 0
    no_flag_n = 0
    no_flag_anchor = 0
    total_n = len(joined)
    total_anchor = 0

    for info, row in joined:
        anchored = abs(row.final_probability - row.market_price) < EST_EQ_MKT_TOL
        total_anchor += int(anchored)

        flagged_any = False
        for flag in TRACKED_FLAGS:
            if flag in info.flags:
                per_flag_n[flag] += 1
                per_flag_anchor[flag] += int(anchored)
                flagged_any = True
        if flagged_any:
            any_flag_n += 1
            any_flag_anchor += int(anchored)
        else:
            no_flag_n += 1
            no_flag_anchor += int(anchored)

    return {
        "total_joined": total_n,
        "total_anchor": total_anchor,
        "unpaired_analysis_rows": unpaired,
        "per_flag": {
            flag: {"n": per_flag_n[flag], "anchor": per_flag_anchor[flag]}
            for flag in TRACKED_FLAGS
        },
        "any_flag": {"n": any_flag_n, "anchor": any_flag_anchor},
        "no_flag": {"n": no_flag_n, "anchor": no_flag_anchor},
    }


def _fmt_row(label: str, n: int, anchor: int) -> str:
    if n == 0:
        return f"  {label:<30} n={n:>6}  anchor=—           ci=—"
    rate = anchor / n
    lo, hi = _wilson_ci(anchor, n)
    return (
        f"  {label:<30} n={n:>6}  anchor={rate * 100:>6.2f}%  "
        f"ci=[{lo * 100:>5.2f}%, {hi * 100:>5.2f}%]"
    )


def render(result: dict[str, Any]) -> None:
    total_n = result["total_joined"]
    total_anchor = result["total_anchor"]
    unpaired = result["unpaired_analysis_rows"]

    print("=" * 72)
    print("P3.1 — Flag-Outcome Correlation")
    print("=" * 72)
    print(
        f"Joined rows: {total_n} "
        f"(unpaired SIGNAL_ANALYSIS_DETAIL rows lacking a matching MATCH_DIAGNOSTIC: {unpaired})"
    )

    if total_n == 0:
        print(
            "\nNo joined rows — either the window has no MATCH_DIAGNOSTIC / "
            "SIGNAL_ANALYSIS_DETAIL pair or they fail the join key. "
            "Widen --since/--until or check schema."
        )
        return

    overall_rate = total_anchor / total_n
    lo, hi = _wilson_ci(total_anchor, total_n)
    print(
        f"Overall anchor rate: {overall_rate * 100:.2f}% "
        f"({total_anchor}/{total_n}) "
        f"Wilson 95% CI [{lo * 100:.2f}%, {hi * 100:.2f}%]"
    )

    print("\nPer-flag anchor rate  (|final_probability - market_price| < 1e-3):")
    for flag in TRACKED_FLAGS:
        stats = result["per_flag"][flag]
        print(_fmt_row(flag, stats["n"], stats["anchor"]))

    print("\nAggregate:")
    print(_fmt_row("any_flag (≥1 of above)", result["any_flag"]["n"], result["any_flag"]["anchor"]))
    print(_fmt_row("no_flag (baseline)", result["no_flag"]["n"], result["no_flag"]["anchor"]))

    # Verdict — the hypothesis is "flags predict est==market." Operationally:
    # does any_flag's anchor rate exceed no_flag's, with non-overlapping CIs?
    af = result["any_flag"]
    nf = result["no_flag"]
    if af["n"] == 0 or nf["n"] == 0:
        print(
            "\nVerdict: INSUFFICIENT DATA — need both flagged and unflagged joined rows "
            "to evaluate differential anchoring."
        )
        return
    af_rate = af["anchor"] / af["n"]
    nf_rate = nf["anchor"] / nf["n"]
    af_lo, af_hi = _wilson_ci(af["anchor"], af["n"])
    nf_lo, nf_hi = _wilson_ci(nf["anchor"], nf["n"])
    diff = af_rate - nf_rate
    ci_overlap = not (af_lo > nf_hi or nf_lo > af_hi)
    print("\nVerdict:")
    print(f"  any_flag rate - no_flag rate = {diff * 100:+.2f} pp")
    if ci_overlap:
        print(
            "  Wilson 95% CIs OVERLAP → insufficient evidence to claim flags predict "
            "est==market (hypothesis NOT supported by this window)."
        )
    elif diff > 0:
        print(
            "  any_flag CI strictly ABOVE no_flag CI → flags DO predict higher est==market rate "
            "(hypothesis supported)."
        )
    else:
        print(
            "  any_flag CI strictly BELOW no_flag CI → flagged events are LESS anchored than "
            "unflagged (hypothesis refuted in this direction)."
        )

    if overall_rate > 0.9 and not (ci_overlap and abs(diff) < 0.05):
        pass  # Already covered above.
    elif overall_rate > 0.9:
        print(
            "\n  Note: overall anchor rate is very high across BOTH flagged and unflagged "
            "rows. If anchoring is this universal, match-quality is not the distinguishing "
            "variable — fix path is upstream (LLM prompting / market specificity / source "
            "alignment), per ROADMAP P3.2 and P3.3."
        )


def correlate_specificity(
    match_index: dict[MatchKey, MatchInfo],
    analysis_rows: list[tuple[MatchKey, AnalysisRow]],
) -> dict[str, Any]:
    """P3.2 — bucket joined rows by ``market_specificity_score``.

    Rows missing a score (pre-v0.29.49 MATCH_DIAGNOSTIC events) are
    excluded from every bucket. The ``scored_n`` total reports how many
    joined rows contributed to any specificity bucket.
    """
    buckets_n: dict[str, int] = {label: 0 for label, _, _ in _SPECIFICITY_BUCKETS}
    buckets_anchor: dict[str, int] = {label: 0 for label, _, _ in _SPECIFICITY_BUCKETS}
    high_n = high_anchor = 0
    low_n = low_anchor = 0
    scored_n = 0
    missing_score_n = 0

    for key, row in analysis_rows:
        info = match_index.get(key)
        if info is None:
            continue
        score = info.market_specificity_score
        if score is None:
            missing_score_n += 1
            continue
        scored_n += 1
        anchored = abs(row.final_probability - row.market_price) < EST_EQ_MKT_TOL
        for label, lo, hi in _SPECIFICITY_BUCKETS:
            if lo <= score < hi:
                buckets_n[label] += 1
                buckets_anchor[label] += int(anchored)
                break
        if score >= _SPECIFICITY_HIGH_LOW_SPLIT:
            high_n += 1
            high_anchor += int(anchored)
        else:
            low_n += 1
            low_anchor += int(anchored)

    return {
        "scored_n": scored_n,
        "missing_score_n": missing_score_n,
        "buckets": {
            label: {"n": buckets_n[label], "anchor": buckets_anchor[label]}
            for label, _, _ in _SPECIFICITY_BUCKETS
        },
        "high": {"n": high_n, "anchor": high_anchor},
        "low": {"n": low_n, "anchor": low_anchor},
    }


def render_specificity(result: dict[str, Any]) -> None:
    scored_n = result["scored_n"]
    missing_n = result["missing_score_n"]

    print()
    print("=" * 72)
    print("P3.2 — Specificity-Bucket Anchor Correlation")
    print("=" * 72)
    print(
        f"Joined rows with market_specificity_score: {scored_n} "
        f"(joined rows missing the score — pre-v0.29.49 events: {missing_n})"
    )

    if scored_n == 0:
        print(
            "\nNo joined rows carried market_specificity_score. Expected while "
            "MATCH_DIAGNOSTIC events from before v0.29.49 dominate the window. "
            "Re-run after fresh matches accumulate (bot restart on v0.29.49+)."
        )
        return

    print("\nPer-bucket anchor rate  (|final_probability - market_price| < 1e-3):")
    for label, _, _ in _SPECIFICITY_BUCKETS:
        stats = result["buckets"][label]
        print(_fmt_row(label, stats["n"], stats["anchor"]))

    print("\nAggregate (split at 0.50):")
    print(_fmt_row(f"high (>= {_SPECIFICITY_HIGH_LOW_SPLIT:.2f})", result["high"]["n"], result["high"]["anchor"]))
    print(_fmt_row(f"low  (<  {_SPECIFICITY_HIGH_LOW_SPLIT:.2f})", result["low"]["n"], result["low"]["anchor"]))

    # Verdict — the hypothesis is "high-specificity markets anchor LESS."
    hi_bucket = result["high"]
    lo_bucket = result["low"]
    if hi_bucket["n"] == 0 or lo_bucket["n"] == 0:
        print(
            "\nVerdict: INSUFFICIENT DATA — need rows on both sides of the 0.50 "
            "split to evaluate differential anchoring by specificity."
        )
        return
    hi_rate = hi_bucket["anchor"] / hi_bucket["n"]
    lo_rate = lo_bucket["anchor"] / lo_bucket["n"]
    hi_lo_lo, hi_lo_hi = _wilson_ci(hi_bucket["anchor"], hi_bucket["n"])
    lo_lo_lo, lo_lo_hi = _wilson_ci(lo_bucket["anchor"], lo_bucket["n"])
    diff = hi_rate - lo_rate
    ci_overlap = not (hi_lo_lo > lo_lo_hi or lo_lo_lo > hi_lo_hi)

    print("\nVerdict:")
    print(f"  high rate - low rate = {diff * 100:+.2f} pp")
    if ci_overlap:
        print(
            "  Wilson 95% CIs OVERLAP → insufficient evidence that specificity "
            "score predicts lower anchoring in this window."
        )
    elif diff < 0:
        print(
            "  high CI strictly BELOW low CI → high-specificity markets DO anchor "
            "less than low-specificity (hypothesis supported; score earning its keep)."
        )
    else:
        print(
            "  high CI strictly ABOVE low CI → high-specificity markets anchor MORE "
            "than low (hypothesis refuted in this direction; retune weights or "
            "reconsider the feature set)."
        )


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
    result = correlate(match_index, analysis_rows)
    render(result)

    specificity_result = correlate_specificity(match_index, analysis_rows)
    render_specificity(specificity_result)

    if args.verbose:
        print(
            f"\n[verbose] read_stats: lines_total={read_stats.lines_total}, "
            f"lines_malformed={read_stats.lines_malformed}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
