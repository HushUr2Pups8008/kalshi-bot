"""Resolution + calibration loop simulation — Task F.

Inserts one synthetic paper trade matching a canonical event into a
temp ``paper_trades.db``, then drives the resolution path
(:meth:`trading.paper_trader.PaperTrader.resolve_market`) for both
YES-wins and NO-wins outcomes. Verifies the per-row resolution effects
(``resolved=1``, ``resolved_ts``, ``pnl_dollars``), the bankroll
credit math, the source-credibility update, and the
``record_calibration_check`` callback wired in v0.29.47
(``PROFIT-CAL-001``).

This is the first end-to-end exercise of the resolution path against
the row shape produced by ``record_trade`` after EDGE-001/002/003.
The wiring closed in v0.29.47 has only been runtime-verified once
(three FISA paper trades on the MacBook 2026-04-30 / 05-01); this
harness pins the contract independent of waiting for live resolutions.

Origin: PROFIT-EDGE-004 — Task F in
``docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md``.

Read-only contract: writes only to a tempdir owned by the harness;
nothing under ``data/`` is touched.

Usage
-----
::

    .venv/bin/python scripts/simulations/resolution_calibration.py
    .venv/bin/python scripts/simulations/resolution_calibration.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis import SignalAnalysis  # noqa: E402
from config import PAPER_FLAT_CONTRACTS  # noqa: E402
from feeds import NewsItem  # noqa: E402
from scripts.simulations._common import (  # noqa: E402
    LLM_POSITIVE_EVENT_HEADLINES_2026_04_26,
    LLM_POSITIVE_EVENTS_2026_04_26,
    LLMPositiveEvent,
    synthetic_market,
)
from trading.paper_trader import PaperTrader  # noqa: E402


_DEFAULT_BANKROLL = 1000.0


# ── Recording calibration task stub ──────────────────────────────────────────


class _RecordingCalibrationTask:
    """Captures every record_calibration_check call without touching the
    real CalibrationMonitorState. Lets us pin the wiring contract
    independent of the drift-math semantics (which have their own
    targeted tests)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def record_calibration_check(
        self,
        *,
        market_ticker: str,
        lane: str,
        lane_estimate: float,
        final_resolution: float,
        error: float,
    ) -> None:
        self.calls.append(
            {
                "market_ticker": market_ticker,
                "lane": lane,
                "lane_estimate": lane_estimate,
                "final_resolution": final_resolution,
                "error": error,
            }
        )


# ── Reports ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolutionReport:
    outcome: Literal["yes", "no"]
    event_name: str
    ticker: str
    side: str
    bankroll_before_entry: float
    bankroll_after_entry: float
    bankroll_after_resolution: float
    expected_payout: float
    resolved_row: dict
    calibration_calls: tuple[dict, ...]
    source_credibility_after: Optional[dict]


@dataclass(frozen=True)
class ResolutionRunReport:
    yes_wins: ResolutionReport
    no_wins: ResolutionReport


# ── Helpers ───────────────────────────────────────────────────────────────────


def _signal_analysis_with_lanes(event: LLMPositiveEvent, headline: str) -> SignalAnalysis:
    """Build a SignalAnalysis carrying signal_meta with all three lane keys.

    record_trade writes lane_p columns from signal_meta — populating all
    three exercises every CALIBRATION_CHECK emission branch the
    resolution path can hit, so the harness reports one calibration call
    per lane on every resolved trade."""
    market = synthetic_market(event)
    estimated_prob = event.fast_p
    edge = estimated_prob - market.yes_price / 100.0
    price_cents = max(1, min(99, int(market.yes_price)))
    capped = PAPER_FLAT_CONTRACTS * price_cents / 100.0
    return SignalAnalysis(
        news_item=NewsItem(
            headline=headline,
            url="https://example.invalid/resolution",
            source="resolution-calibration",
            published=datetime(2026, 4, 26, tzinfo=timezone.utc),
        ),
        market=market,
        estimated_probability=estimated_prob,
        market_yes_price=market.yes_price,
        edge=edge,
        side=event.side,
        kelly_fraction=0.10,
        kelly_dollars=2.50,
        capped_dollars=capped,
        confidence=event.fast_conf,
        match_score=0.50,
        signal_type="news",
        llm_direction=event.side,
        llm_magnitude="small",
        llm_confidence=event.fast_conf,
        signal_meta={
            "fast_lane_p": estimated_prob,
            "fast_lane_confidence": event.fast_conf,
            "accumulation_p": 0.55,
            "accumulation_confidence": 0.60,
            "structural_p": 0.45,
            "structural_confidence": 0.55,
            "source_lane": "blend",
        },
    )


def _expected_payout(*, won: bool, contracts: int) -> float:
    return float(contracts) if won else 0.0


def _read_row(db_path: Path, trade_id: str) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM paper_trades WHERE trade_id = ?", (trade_id,),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _read_credibility(db_path: Path, source: str) -> Optional[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM source_credibility WHERE source = ?", (source,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── Per-outcome simulation ────────────────────────────────────────────────────


async def _run_one(
    *,
    event: LLMPositiveEvent,
    outcome: Literal["yes", "no"],
    db_path: Path,
) -> ResolutionReport:
    headline = LLM_POSITIVE_EVENT_HEADLINES_2026_04_26.get(event.name, event.title)
    analysis = _signal_analysis_with_lanes(event, headline)

    calibration = _RecordingCalibrationTask()
    paper = PaperTrader(
        db_path=db_path,
        startup_context="test",
        calibration_task=calibration,
    )
    paper._set_state("notional_bankroll", str(round(_DEFAULT_BANKROLL, 4)))

    bankroll_before = paper.get_notional_bankroll()
    trade_id = paper.record_trade(analysis)
    bankroll_after_entry = paper.get_notional_bankroll()

    resolved_yes = outcome == "yes"
    await paper.resolve_market(event.ticker, resolved_yes=resolved_yes)
    bankroll_after_resolution = paper.get_notional_bankroll()

    row = _read_row(db_path, trade_id)
    won = (event.side == "yes" and resolved_yes) or (event.side == "no" and not resolved_yes)
    expected_payout = _expected_payout(won=won, contracts=int(row.get("contracts", 0)))
    cred = _read_credibility(db_path, analysis.news_item.source)

    return ResolutionReport(
        outcome=outcome,
        event_name=event.name,
        ticker=event.ticker,
        side=event.side,
        bankroll_before_entry=bankroll_before,
        bankroll_after_entry=bankroll_after_entry,
        bankroll_after_resolution=bankroll_after_resolution,
        expected_payout=expected_payout,
        resolved_row=row,
        calibration_calls=tuple(calibration.calls),
        source_credibility_after=cred,
    )


def run(*, event: Optional[LLMPositiveEvent] = None, db_root: Optional[Path] = None) -> ResolutionRunReport:
    """Run YES-wins + NO-wins resolutions on the same canonical event.

    Each outcome runs on a fresh DB so the row-write contract isolates
    cleanly from the resolution path."""
    target_event = event or LLM_POSITIVE_EVENTS_2026_04_26[2]  # KXTRUMPIRAN dispatching, side="yes"

    async def _all(root: Path) -> ResolutionRunReport:
        yes_db = root / "paper_trades-yes.db"
        no_db = root / "paper_trades-no.db"
        return ResolutionRunReport(
            yes_wins=await _run_one(event=target_event, outcome="yes", db_path=yes_db),
            no_wins=await _run_one(event=target_event, outcome="no", db_path=no_db),
        )

    if db_root is not None:
        db_root.mkdir(parents=True, exist_ok=True)
        return asyncio.run(_all(db_root))

    with tempfile.TemporaryDirectory(prefix="resolution-calibration-") as tmp:
        return asyncio.run(_all(Path(tmp)))


# ── CLI rendering ─────────────────────────────────────────────────────────────


def _print_outcome(label: str, report: ResolutionReport) -> None:
    print(f"\n• {label}: {report.event_name}  side={report.side}")
    print(f"  ticker          : {report.ticker}")
    print(
        f"  bankroll        : ${report.bankroll_before_entry:.2f} → "
        f"${report.bankroll_after_entry:.2f} (entry) → "
        f"${report.bankroll_after_resolution:.2f} (resolved); "
        f"expected payout=${report.expected_payout:.2f}"
    )
    row = report.resolved_row
    print(
        f"  row             : resolved={row.get('resolved')} "
        f"resolved_yes={row.get('resolved_yes')} "
        f"pnl_dollars={row.get('pnl_dollars')} "
        f"resolved_ts={row.get('resolved_ts')!r}"
    )
    print(f"  calibration     : {len(report.calibration_calls)} call(s)")
    for c in report.calibration_calls:
        print(
            f"    - lane={c['lane']:<13} estimate={c['lane_estimate']:.3f}  "
            f"final={c['final_resolution']:.1f}  error={c['error']:.3f}"
        )
    cred = report.source_credibility_after or {}
    print(
        f"  credibility     : wins={cred.get('wins')} losses={cred.get('losses')} "
        f"total={cred.get('total')} accuracy={cred.get('accuracy')}"
    )


def _print_report(report: ResolutionRunReport) -> None:
    print("=" * 90)
    print("Resolution + calibration loop simulation")
    print("=" * 90)
    _print_outcome("YES wins", report.yes_wins)
    _print_outcome("NO wins", report.no_wins)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the resolution + calibration loop sim against a temp DB.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON to stdout")
    parser.add_argument(
        "--db-root",
        type=Path,
        default=None,
        help="optional persistent DB directory (default: tempdir, deleted on exit)",
    )
    args = parser.parse_args(argv)

    report = run(db_root=args.db_root)
    if args.json:
        json.dump(asdict(report), sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
