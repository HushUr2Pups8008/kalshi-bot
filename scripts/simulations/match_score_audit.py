"""Match-score gate audit — Task A of the pipeline simulation buildout.

Walks each of the five canonical LLM-positive events from
:data:`scripts.simulations._common.LLM_POSITIVE_EVENTS_2026_04_26` through
the real :meth:`analysis.market_matcher.MarketMatcher.find_candidates`
implementation, using the actual production headline +
production market title captured from the MacBook trade-log archive.

For each event it reports:

*   the top-3 matched candidates and their match scores,
*   whether the anchor ticker survived the default
    ``PAPER_MIN_MATCH_SCORE`` threshold and appeared in the top-3,
*   a sweep over alternate thresholds (0.03, 0.04, 0.05, 0.06, 0.08, 0.10)
    showing where the anchor would fall off.

The matcher emits ``MATCH_DIAGNOSTIC`` records to the trade log via
``write_trade_log_async`` in the normal flow.  The audit patches that
helper to a no-op so a CLI run never pollutes ``logs/trades/live`` —
this is what keeps the simulation read-only.

Origin: PROFIT-EDGE-004 follow-up — Task A in
``docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md``.

Usage
-----
::

    .venv/bin/python scripts/simulations/match_score_audit.py
    .venv/bin/python scripts/simulations/match_score_audit.py --json

"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis import market_matcher as mm  # noqa: E402
from config import PAPER_MIN_MATCH_SCORE  # noqa: E402
from feeds import NewsItem  # noqa: E402
from kalshi import KalshiMarket  # noqa: E402
from scripts.simulations._common import (  # noqa: E402
    LLM_POSITIVE_EVENT_HEADLINES_2026_04_26,
    LLM_POSITIVE_EVENT_MARKET_TITLES_2026_04_26,
    LLM_POSITIVE_EVENTS_2026_04_26,
    LLMPositiveEvent,
)


DEFAULT_THRESHOLD_SWEEP: tuple[float, ...] = (0.03, 0.04, 0.05, 0.06, 0.08, 0.10)


@dataclass(frozen=True)
class MatchAuditReport:
    """Per-event audit record returned by :func:`run`."""

    event_name: str
    headline: str
    target_ticker: str
    target_in_top_3: bool
    target_score: Optional[float]
    top_3_matches: tuple[tuple[str, float], ...]
    threshold_sweep: dict[float, bool]
    paper_min_match_score: float


# ── Synthetic decoy markets ───────────────────────────────────────────────────
#
# A handful of unrelated markets to pad the candidate pool so the audit is
# not just "anchor vs. anchor." All carry production-shaped titles so the
# matcher's real geo / named-entity gates fire normally.

def _decoy_markets() -> list[KalshiMarket]:
    return [
        # Domestic-policy decoy with overlap potential on ICE headlines.
        _market(
            ticker="KXIMMIG-26-OVERALL",
            title="Will Congress pass an immigration bill before Dec 31, 2026?",
            series_ticker="KXIMMIG",
            close_time="2026-12-31T23:59:59Z",
        ),
        # Russia / Ukraine decoy — geopolitical but unrelated to canonical events.
        _market(
            ticker="KXRUSUKRCEASE-26",
            title="Will Russia and Ukraine sign a ceasefire before Sep 1, 2026?",
            series_ticker="KXRUSUKRCEASE",
            close_time="2026-09-01T23:59:59Z",
        ),
        # China-Taiwan decoy.
        _market(
            ticker="KXCHTW-26",
            title="Will China invade Taiwan before Oct 1, 2026?",
            series_ticker="KXCHTW",
            close_time="2026-10-01T23:59:59Z",
        ),
    ]


def _market(*, ticker: str, title: str, series_ticker: str, close_time: str) -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        title=title,
        yes_bid=49,
        yes_ask=51,
        yes_price=50,
        volume=1000,
        open_interest=500,
        close_time=close_time,
        status="active",
        series_ticker=series_ticker,
    )


def _candidate_market_for_event(event: LLMPositiveEvent) -> KalshiMarket:
    """Build a KalshiMarket carrying the *production* title for the event."""
    real_title = LLM_POSITIVE_EVENT_MARKET_TITLES_2026_04_26.get(event.ticker)
    if real_title is None:
        # Fall back to the event's generic title if no production title was captured.
        real_title = event.title
    return _market(
        ticker=event.ticker,
        title=real_title,
        series_ticker=event.series,
        close_time=event.close_time,
    )


class _StaticMarketCache:
    """Minimal stand-in for :class:`market_matcher.MarketCache` that returns a
    pre-built market list. Lets us drive ``find_candidates`` without a Kalshi
    REST client or HTTP traffic."""

    def __init__(self, markets: Iterable[KalshiMarket]) -> None:
        self._markets = list(markets)

    async def get_markets(self) -> list[KalshiMarket]:
        return list(self._markets)


def _candidate_pool() -> list[KalshiMarket]:
    """Build the deterministic candidate market pool used for every event."""
    seen: set[str] = set()
    pool: list[KalshiMarket] = []
    for ev in LLM_POSITIVE_EVENTS_2026_04_26:
        m = _candidate_market_for_event(ev)
        if m.ticker not in seen:
            pool.append(m)
            seen.add(m.ticker)
    for m in _decoy_markets():
        if m.ticker not in seen:
            pool.append(m)
            seen.add(m.ticker)
    return pool


# ── Per-event audit ───────────────────────────────────────────────────────────


async def _audit_event(
    event: LLMPositiveEvent,
    headline: str,
    *,
    pool: list[KalshiMarket],
    thresholds: tuple[float, ...],
) -> MatchAuditReport:
    news = NewsItem(
        headline=headline,
        url="https://example.invalid/sim",
        source="match-score-audit",
        published=datetime(2026, 4, 26, tzinfo=timezone.utc),
    )

    matcher = mm.MarketMatcher.__new__(mm.MarketMatcher)
    matcher._cache = _StaticMarketCache(pool)

    async def _silent_writer(*_args, **_kwargs):
        return None

    with patch.object(mm, "write_trade_log_async", _silent_writer):
        results = await matcher.find_candidates(news, max_results=10)

    top_3 = tuple((m.ticker, score) for m, score, _ in results[:3])
    target_score: Optional[float] = next(
        (s for m, s, _ in results if m.ticker == event.ticker),
        None,
    )
    target_in_top_3 = any(t == event.ticker for t, _ in top_3)

    sweep = {
        round(th, 4): (target_score is not None and target_score >= th)
        for th in thresholds
    }

    return MatchAuditReport(
        event_name=event.name,
        headline=headline,
        target_ticker=event.ticker,
        target_in_top_3=target_in_top_3,
        target_score=target_score,
        top_3_matches=top_3,
        threshold_sweep=sweep,
        paper_min_match_score=PAPER_MIN_MATCH_SCORE,
    )


def run(
    *,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLD_SWEEP,
) -> list[MatchAuditReport]:
    """Run the audit for every canonical event and return one report per event."""
    pool = _candidate_pool()

    async def _run_all() -> list[MatchAuditReport]:
        out: list[MatchAuditReport] = []
        for ev in LLM_POSITIVE_EVENTS_2026_04_26:
            headline = LLM_POSITIVE_EVENT_HEADLINES_2026_04_26[ev.name]
            out.append(
                await _audit_event(
                    ev,
                    headline,
                    pool=pool,
                    thresholds=thresholds,
                )
            )
        return out

    return asyncio.run(_run_all())


# ── CLI rendering ─────────────────────────────────────────────────────────────


def _print_per_event(reports: list[MatchAuditReport]) -> None:
    print("=" * 78)
    print("Match-score gate audit — five canonical LLM-positive events (2026-04-26)")
    print(f"PAPER_MIN_MATCH_SCORE = {PAPER_MIN_MATCH_SCORE}")
    print("=" * 78)
    for r in reports:
        print(f"\n• {r.event_name}")
        print(f"  headline      : {r.headline}")
        print(f"  target ticker : {r.target_ticker}")
        score_str = f"{r.target_score:.4f}" if r.target_score is not None else "—"
        print(f"  in top-3      : {r.target_in_top_3}   (target score={score_str})")
        if r.top_3_matches:
            print("  top 3 matches :")
            for tk, sc in r.top_3_matches:
                marker = "*" if tk == r.target_ticker else " "
                print(f"    {marker} {tk:<32}  score={sc:.4f}")
        else:
            print("  top 3 matches : (none — matcher produced zero candidates)")


def _print_threshold_sweep(reports: list[MatchAuditReport]) -> None:
    if not reports:
        return
    thresholds = sorted(reports[0].threshold_sweep.keys())
    print("\n" + "-" * 78)
    print("Threshold sweep — does the anchor ticker survive each candidate cutoff?")
    print("-" * 78)
    header = "  event" + " " * 44 + "".join(f"{t:>8.2f}" for t in thresholds)
    print(header)
    for r in reports:
        row = "  " + r.event_name[:48].ljust(48)
        for t in thresholds:
            row += "    OK  " if r.threshold_sweep[t] else "    --  "
        print(row)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit match-score gate for the five canonical LLM-positive events.",
    )
    parser.add_argument("--json", action="store_true", help="emit reports as JSON to stdout")
    args = parser.parse_args(argv)

    reports = run()

    if args.json:
        payload = [
            {
                **asdict(r),
                # tuple → list, dict[float] → dict[str] for JSON friendliness
                "top_3_matches": [list(p) for p in r.top_3_matches],
                "threshold_sweep": {f"{k:.2f}": v for k, v in r.threshold_sweep.items()},
            }
            for r in reports
        ]
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_per_event(reports)
        _print_threshold_sweep(reports)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
