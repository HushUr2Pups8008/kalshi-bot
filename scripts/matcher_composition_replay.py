"""PROFIT-ALIGN — matcher-composition replay (min vs mean) against archived events.

Decisive empirical test for the PROFIT-ALIGN-001 / PROFIT-MATCH-DYNAMIC
"min vs mean" composition argument (operator session 2026-05-25). Replays
historical MATCH_DIAGNOSTIC events against TODAY's runtime weights file
(`data/matcher_token_weights.json`) under both composition operators and
cross-references downstream signal-analyzer behavior. Outputs the
actionable-signal rate in the "matches mean adds vs min rejects" cell, the
only cell where the two operators disagree (since mean ≥ min always for
weights ∈ [0, 1]).

Method:

  1. Read MATCH_DIAGNOSTIC events from `logs/trades/archive/**` + live.
  2. Filter to events emitted BEFORE 2026-05-24T22:13Z (matcher-feedback
     loop ship time). Those events have no historical downweight applied,
     so their recorded `match_score` is the clean pre-downweight base.
  3. For each match, compute `min_multiplier` and `mean_multiplier` from
     today's weights. Apply both fresh:
         score_min  = match_score * min_multiplier
         score_mean = match_score * mean_multiplier
  4. Compare both against `PAPER_MIN_MATCH_SCORE`. Three cells:
         pass-both         → no information (both operators agree pass)
         fail-both         → no information (both agree fail)
         mean-only-pass    → mean LETS THROUGH; min REJECTS
  5. For mean-only-pass matches, find the next SIGNAL_ANALYSIS_DETAIL on
     the same ticker within 30s. If LLM emitted `direction in {yes, no}`,
     count as "actionable". If neutral/missing, count as "noise".
  6. Compare actionable-rate in mean-only-pass cell vs pass-both baseline.

Output: stderr table + JSON. No env mutation, no behavior change.

Caveats (printed in output):

  - Counterfactual: applies TODAY's weights to YESTERDAY's matches. Real
    bot at emit time may have made different choices in unrelated layers.
  - LLM-rejection is a proxy for "actionable signal", not "winning trade".
    Calibration of LLM rejection still pending PROFIT-ALIGN-002 evidence.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Anchored to the PROFIT-MATCH-DYNAMIC commit 4/5 / v0.32.0 ship time.
# Events before this had no per-(token×prefix) downweight applied to score.
_FEEDBACK_SHIPPED_UTC: datetime = datetime(2026, 5, 24, 22, 13, tzinfo=timezone.utc)

# Matcher's paper-mode minimum, mirroring config.PAPER_MIN_MATCH_SCORE.
_PAPER_MIN_MATCH_SCORE: float = 0.06

# How far to look ahead for the LLM verdict on the same ticker.
_SAD_LOOKAHEAD_SECONDS: float = 30.0


@dataclass
class _Match:
    ts: datetime
    ticker: str
    market_prefix: str
    matched_tokens: list[str]
    match_score: float
    headline: str
    source: str


@dataclass
class _Sad:
    ts: datetime
    ticker: str
    llm_direction: str
    llm_magnitude: str


@dataclass
class _Cell:
    count: int = 0
    actionable: int = 0  # SAD with direction in {yes, no}
    no_sad: int = 0      # no follow-up SAD within window

    @property
    def actionable_rate(self) -> float:
        if self.count == 0:
            return 0.0
        return self.actionable / self.count

    @property
    def sad_observed_rate(self) -> float:
        if self.count == 0:
            return 0.0
        return (self.count - self.no_sad) / self.count


@dataclass
class _Bucket:
    name: str
    matches: int = 0
    cell: _Cell = field(default_factory=_Cell)


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_events(trade_log_root: Path) -> tuple[list[_Match], dict[str, list[_Sad]]]:
    """Return (matches, sads_by_ticker). Sads sorted ascending by ts."""
    paths: list[Path] = []
    live = trade_log_root / "live" / "trades.jsonl"
    if live.exists():
        paths.append(live)
    archive = trade_log_root / "archive"
    if archive.exists():
        paths.extend(sorted(archive.rglob("*.jsonl")))

    matches: list[_Match] = []
    sads_by_ticker: dict[str, list[_Sad]] = defaultdict(list)

    for p in paths:
        try:
            with p.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    # Cheap pre-filter to avoid JSON parse on lines we won't need.
                    if (
                        "MATCH_DIAGNOSTIC" not in line
                        and "SIGNAL_ANALYSIS_DETAIL" not in line
                    ):
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    t = r.get("type")
                    ts = _parse_ts(r.get("ts"))
                    if ts is None:
                        continue
                    if t == "MATCH_DIAGNOSTIC":
                        ticker = r.get("ticker") or r.get("market_ticker") or ""
                        prefix = ticker.split("-", 1)[0] if ticker else ""
                        tokens = r.get("matched_tokens") or []
                        try:
                            score = float(r.get("match_score", 0.0))
                        except (TypeError, ValueError):
                            continue
                        matches.append(_Match(
                            ts=ts,
                            ticker=ticker,
                            market_prefix=prefix,
                            matched_tokens=list(tokens),
                            match_score=score,
                            headline=str(r.get("headline", ""))[:120],
                            source=str(r.get("source", ""))[:60],
                        ))
                    elif t == "SIGNAL_ANALYSIS_DETAIL":
                        ticker = r.get("ticker") or ""
                        if not ticker:
                            continue
                        sads_by_ticker[ticker].append(_Sad(
                            ts=ts,
                            ticker=ticker,
                            llm_direction=str(r.get("llm_direction") or ""),
                            llm_magnitude=str(r.get("llm_magnitude") or ""),
                        ))
        except OSError:
            continue

    for ticker in sads_by_ticker:
        sads_by_ticker[ticker].sort(key=lambda s: s.ts)
    return matches, dict(sads_by_ticker)


def _load_weights(weights_path: Path) -> dict[str, dict[str, Any]]:
    if not weights_path.exists():
        return {}
    try:
        data = json.loads(weights_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _multipliers(
    overlap_tokens: list[str],
    market_prefix: str,
    weights: dict[str, dict[str, Any]],
) -> tuple[float, float]:
    """Return (min_multiplier, mean_multiplier) for the overlap.

    Missing or malformed weight entries count as 1.0 (no downweight).
    """
    if not overlap_tokens:
        return 1.0, 1.0
    values: list[float] = []
    for t in overlap_tokens:
        raw = weights.get(f"{market_prefix}:{t}", {}).get("weight", 1.0)
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            values.append(1.0)
    if not values:
        return 1.0, 1.0
    return min(values), sum(values) / len(values)


def _find_followup_sad(
    match: _Match,
    sads_by_ticker: dict[str, list[_Sad]],
    lookahead_seconds: float,
) -> _Sad | None:
    """Return the first SAD on the same ticker emitted within lookahead_seconds
    of the match. None if no SAD found in window."""
    candidates = sads_by_ticker.get(match.ticker) or []
    if not candidates:
        return None
    for sad in candidates:
        delta = (sad.ts - match.ts).total_seconds()
        if delta < 0:
            continue
        if delta > lookahead_seconds:
            return None
        return sad
    return None


def _classify_match(sad: _Sad | None) -> str:
    """Return 'actionable' / 'noise' / 'no_sad'.

    actionable: LLM emitted direction in {yes, no}
    noise:      LLM emitted neutral OR no direction (rejection)
    no_sad:     no SAD found in lookahead window (didn't reach LLM)
    """
    if sad is None:
        return "no_sad"
    if sad.llm_direction in ("yes", "no"):
        return "actionable"
    return "noise"


def run(
    *,
    trade_log_root: Path = Path("logs/trades"),
    weights_path: Path = Path("data/matcher_token_weights.json"),
    feedback_shipped_utc: datetime = _FEEDBACK_SHIPPED_UTC,
    min_score: float = _PAPER_MIN_MATCH_SCORE,
    lookahead_seconds: float = _SAD_LOOKAHEAD_SECONDS,
) -> dict[str, Any]:
    """Pure-function entry point used by tests + CLI."""
    matches, sads_by_ticker = _load_events(trade_log_root)
    weights = _load_weights(weights_path)

    # Restrict to pre-feedback events so match_score is the clean pre-downweight base.
    pre_feedback = [m for m in matches if m.ts < feedback_shipped_utc]

    pass_both = _Bucket("pass_both")
    fail_both = _Bucket("fail_both")
    mean_only_pass = _Bucket("mean_only_pass")

    divergent_samples: list[dict[str, Any]] = []  # first 20 mean-only-pass for operator inspection

    for m in pre_feedback:
        min_mult, mean_mult = _multipliers(m.matched_tokens, m.market_prefix, weights)
        score_min = m.match_score * min_mult
        score_mean = m.match_score * mean_mult
        passes_min = score_min >= min_score
        passes_mean = score_mean >= min_score

        sad = _find_followup_sad(m, sads_by_ticker, lookahead_seconds)
        classification = _classify_match(sad)

        if passes_min and passes_mean:
            target = pass_both
        elif not passes_min and not passes_mean:
            target = fail_both
        elif passes_mean and not passes_min:
            target = mean_only_pass
            if len(divergent_samples) < 20:
                divergent_samples.append({
                    "ts": m.ts.isoformat(),
                    "ticker": m.ticker,
                    "market_prefix": m.market_prefix,
                    "matched_tokens": m.matched_tokens,
                    "match_score": round(m.match_score, 4),
                    "min_multiplier": round(min_mult, 4),
                    "mean_multiplier": round(mean_mult, 4),
                    "score_min": round(score_min, 4),
                    "score_mean": round(score_mean, 4),
                    "headline": m.headline,
                    "source": m.source,
                    "downstream": classification,
                    "llm_direction": sad.llm_direction if sad else None,
                    "llm_magnitude": sad.llm_magnitude if sad else None,
                })
        else:
            # mathematically impossible since mean ≥ min for weights ≤ 1.0;
            # bucket as fail_both for safety.
            target = fail_both

        target.matches += 1
        target.cell.count += 1
        if classification == "actionable":
            target.cell.actionable += 1
        elif classification == "no_sad":
            target.cell.no_sad += 1

    return {
        "feedback_shipped_utc": feedback_shipped_utc.isoformat(),
        "paper_min_match_score": min_score,
        "sad_lookahead_seconds": lookahead_seconds,
        "total_pre_feedback_matches": len(pre_feedback),
        "buckets": {
            "pass_both": {
                "count": pass_both.cell.count,
                "actionable": pass_both.cell.actionable,
                "no_sad": pass_both.cell.no_sad,
                "actionable_rate": round(pass_both.cell.actionable_rate, 4),
                "sad_observed_rate": round(pass_both.cell.sad_observed_rate, 4),
            },
            "fail_both": {
                "count": fail_both.cell.count,
                "actionable": fail_both.cell.actionable,
                "no_sad": fail_both.cell.no_sad,
            },
            "mean_only_pass": {
                "count": mean_only_pass.cell.count,
                "actionable": mean_only_pass.cell.actionable,
                "no_sad": mean_only_pass.cell.no_sad,
                "actionable_rate": round(mean_only_pass.cell.actionable_rate, 4),
                "sad_observed_rate": round(mean_only_pass.cell.sad_observed_rate, 4),
            },
        },
        "divergent_samples": divergent_samples,
        "weights_path": str(weights_path),
        "weights_entry_count": len(weights),
    }


def _print_summary(s: dict[str, Any]) -> None:
    print(
        f"\nMatcher-composition replay (min vs mean) — "
        f"{s['total_pre_feedback_matches']} pre-feedback MATCH_DIAGNOSTIC events",
        file=sys.stderr,
    )
    print(
        f"  Threshold = {s['paper_min_match_score']}  "
        f"SAD lookahead = {s['sad_lookahead_seconds']}s  "
        f"Weights file = {s['weights_path']} ({s['weights_entry_count']} entries)",
        file=sys.stderr,
    )
    pb = s["buckets"]["pass_both"]
    fb = s["buckets"]["fail_both"]
    mp = s["buckets"]["mean_only_pass"]
    print(
        f"\n  pass_both       : {pb['count']:>6}  "
        f"actionable={pb['actionable']:>4} ({pb['actionable_rate']:.2%})  "
        f"sad_observed={pb['sad_observed_rate']:.2%}",
        file=sys.stderr,
    )
    print(
        f"  fail_both       : {fb['count']:>6}  (no further info — both reject)",
        file=sys.stderr,
    )
    print(
        f"  mean_only_pass  : {mp['count']:>6}  "
        f"actionable={mp['actionable']:>4} ({mp['actionable_rate']:.2%})  "
        f"sad_observed={mp['sad_observed_rate']:.2%}",
        file=sys.stderr,
    )
    print("\n  Decisive metric:", file=sys.stderr)
    print(
        f"    mean-only-pass actionable rate ({mp['actionable_rate']:.2%}) vs "
        f"pass-both actionable rate ({pb['actionable_rate']:.2%})",
        file=sys.stderr,
    )
    if mp["count"] == 0:
        print(
            "    → no divergent matches found. min and mean produce identical "
            "outcomes on this archive. Operator argument is moot for this window.",
            file=sys.stderr,
        )
    elif mp["actionable_rate"] >= pb["actionable_rate"]:
        print(
            "    → mean-added matches have ≥ actionable-signal rate vs baseline. "
            "Mean adds real signal, not just noise.",
            file=sys.stderr,
        )
    else:
        ratio = (
            mp["actionable_rate"] / pb["actionable_rate"]
            if pb["actionable_rate"] > 0 else 0.0
        )
        print(
            f"    → mean-added matches actionable at {ratio:.1%} of baseline rate. "
            "Mean is letting through more noise than signal.",
            file=sys.stderr,
        )
    if s["divergent_samples"]:
        print(f"\n  First {len(s['divergent_samples'])} mean-only-pass divergent samples:",
              file=sys.stderr)
        for d in s["divergent_samples"][:5]:
            print(
                f"    {d['ts']}  {d['ticker']:<35} "
                f"tokens={d['matched_tokens']}  "
                f"score_min={d['score_min']:.4f} score_mean={d['score_mean']:.4f}  "
                f"downstream={d['downstream']}",
                file=sys.stderr,
            )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Replay archived MATCH_DIAGNOSTIC events under min-vs-mean composition",
    )
    p.add_argument("--trade-log-root", type=Path, default=Path("logs/trades"))
    p.add_argument("--weights-path", type=Path, default=Path("data/matcher_token_weights.json"))
    p.add_argument("--output", type=Path, default=Path("data/matcher_composition_replay.json"))
    p.add_argument("--dry-run", action="store_true",
                   help="Print to stderr only, do not write JSON")
    args = p.parse_args(argv)

    summary = run(
        trade_log_root=args.trade_log_root,
        weights_path=args.weights_path,
    )
    _print_summary(summary)

    if args.dry_run:
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"\nWrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
