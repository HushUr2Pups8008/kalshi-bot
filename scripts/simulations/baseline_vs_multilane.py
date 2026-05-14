"""Fast-lane baseline vs multi-lane validation harness.

Compares the same canonical intake window across two paper/offline paths:

* baseline: fast lane only, using the no-dossier readiness simulation
* multi_lane: BlendTask.process_fast_lane_result with seeded slow-lane context

Both paths then run the executor validation gates in sequential paper mode.
No production DB, trade-log, Kalshi, or live-order path is touched.

Origin: PROFIT-VALID-001.

Usage
-----
::

    .venv/bin/python scripts/simulations/baseline_vs_multilane.py
    .venv/bin/python scripts/simulations/baseline_vs_multilane.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
import time as _time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.simulations._common import (  # noqa: E402
    LLM_POSITIVE_EVENTS_2026_04_26,
    LLMPositiveEvent,
)
from scripts.simulations import blend_task_integration  # noqa: E402
from scripts.simulations.executor_validate import (  # noqa: E402
    _make_paper_executor,
    _signal_analysis_from_event,
)
from scripts.simulations.readiness_gate_events import (  # noqa: E402
    is_sport_blocked,
    simulate_event,
)
from tasks.blend_task import BlendTask, TradeCandidate  # noqa: E402


@dataclass(frozen=True)
class EventComparison:
    event_name: str
    ticker: str
    baseline_readiness_passed: bool
    baseline_candidate: bool
    baseline_executor_skip_reason: Optional[str]
    baseline_paper_trade: bool
    multi_lane_readiness_passed: bool
    multi_lane_candidate: bool
    multi_lane_executor_skip_reason: Optional[str]
    multi_lane_paper_trade: bool
    multi_lane_blend_mode: str
    multi_lane_blended_p: float
    multi_lane_disagreement_score: float
    multi_lane_block_reason: Optional[str]


@dataclass(frozen=True)
class LaneSummary:
    lane: str
    evaluated_candidates: int
    readiness_pass_count: int
    readiness_block_count: int
    executor_evaluated_count: int
    paper_trade_count: int
    acceptance_rate: float
    trade_frequency_per_candidate: float
    blend_pass_count: int
    blend_block_count: int


@dataclass(frozen=True)
class ComparisonReport:
    fixture: str
    offline_paper_only: bool
    baseline: LaneSummary
    multi_lane: LaneSummary
    frequency_ratio_multi_lane_to_baseline: Optional[float]
    within_2x_trade_frequency_constraint: bool
    events: list[EventComparison]


def _executor_passes_sequential(candidates: list[Any]) -> list[str | None]:
    """Run executor validation gates without recording trades."""
    executor = _make_paper_executor()
    open_positions: dict[str, list] = {}
    executor._paper.portfolio.open_positions = lambda ticker: open_positions.get(ticker, [])

    out: list[str | None] = []
    for candidate in candidates:
        analysis = asyncio.run(executor._analysis_from_candidate(candidate))
        skip = executor._validate(analysis)
        out.append(skip)
        if skip is None:
            executor._last_traded[analysis.market.ticker] = _time.monotonic()
            open_positions.setdefault(analysis.market.ticker, []).append(SimpleNamespace(
                side=analysis.side,
                estimated_prob=analysis.estimated_probability,
                # F-11 P1-A: SignalAnalysis.market_yes_price was deleted; mirror
                # the executed-side price under the canonical name so the
                # SimpleNamespace open-position fixture matches Position's new
                # entry_price_cents field.
                entry_price_cents=analysis.executed_price_cents,
            ))
    return out


def _baseline_candidates(events: list[LLMPositiveEvent]) -> list[Any]:
    candidates: list[Any] = []
    for event in events:
        readiness = simulate_event(event)
        if is_sport_blocked(event.ticker):
            continue
        if not readiness.post_fix_outcome.passed:
            continue
        analysis = _signal_analysis_from_event(event)
        analysis.news_item.headline = event.name
        candidates.append(analysis)
    return candidates


async def _multi_lane_results(events: list[LLMPositiveEvent]) -> list[Any]:
    out: list[Any] = []
    for event in events:
        headline = event.name
        if event.ticker == "KXTRUMPIRAN-26MAY01":
            store = blend_task_integration._seed_kxtrumpiran_state(event.ticker)
        else:
            store = blend_task_integration._empty_store()

        queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
        logger = blend_task_integration._SpyLogger()
        task = BlendTask(
            trading_queue=queue,
            store=store,
            logger=logger,
            is_paper_mode=True,
            now=lambda: datetime(2026, 4, 26, tzinfo=UTC),
        )
        analysis = blend_task_integration._build_signal_analysis(event, headline)
        result = await task.process_fast_lane_result(analysis)
        out.append((event, result))
    return out


def _multi_lane_candidates(results: list[Any]) -> list[Any]:
    candidates: list[Any] = []
    for _, result in results:
        if result.candidate is not None:
            candidates.append(copy.copy(result.candidate))
    return candidates


def _summary(
    *,
    lane: str,
    evaluated: int,
    readiness_pass: int,
    executor_evaluated: int,
    paper_trades: int,
    blend_pass: int,
    blend_block: int,
) -> LaneSummary:
    return LaneSummary(
        lane=lane,
        evaluated_candidates=evaluated,
        readiness_pass_count=readiness_pass,
        readiness_block_count=evaluated - readiness_pass,
        executor_evaluated_count=executor_evaluated,
        paper_trade_count=paper_trades,
        acceptance_rate=(paper_trades / executor_evaluated if executor_evaluated else 0.0),
        trade_frequency_per_candidate=(paper_trades / evaluated if evaluated else 0.0),
        blend_pass_count=blend_pass,
        blend_block_count=blend_block,
    )


def run() -> ComparisonReport:
    events = list(LLM_POSITIVE_EVENTS_2026_04_26)

    baseline_readiness = {event.name: simulate_event(event) for event in events}
    baseline_candidates = _baseline_candidates(events)
    baseline_skip_list = _executor_passes_sequential(baseline_candidates)
    baseline_skips = {
        candidate.news_item.headline: skip
        for candidate, skip in zip(baseline_candidates, baseline_skip_list)
    }

    multi_results = asyncio.run(_multi_lane_results(events))
    multi_candidates = _multi_lane_candidates(multi_results)
    multi_skip_list = _executor_passes_sequential(multi_candidates)
    multi_skips: dict[str, str | None] = {}
    for candidate, skip in zip(multi_candidates, multi_skip_list):
        multi_skips[candidate.fast_lane_analysis.news_item.headline] = skip

    multi_by_name = {event.name: result for event, result in multi_results}
    events_out: list[EventComparison] = []
    for event in events:
        base_ready = (
            baseline_readiness[event.name].post_fix_outcome.passed
            and not is_sport_blocked(event.ticker)
        )
        base_skip = baseline_skips.get(event.name)
        result = multi_by_name[event.name]
        multi_key = event.name
        multi_skip = multi_skips.get(multi_key)
        events_out.append(EventComparison(
            event_name=event.name,
            ticker=event.ticker,
            baseline_readiness_passed=base_ready,
            baseline_candidate=base_ready,
            baseline_executor_skip_reason=base_skip,
            baseline_paper_trade=base_ready and base_skip is None,
            multi_lane_readiness_passed=result.readiness_decision.passed,
            multi_lane_candidate=result.candidate is not None,
            multi_lane_executor_skip_reason=multi_skip,
            multi_lane_paper_trade=result.candidate is not None and multi_skip is None,
            multi_lane_blend_mode=str(result.blend_result.blend_mode),
            multi_lane_blended_p=float(result.blend_result.blended_p),
            multi_lane_disagreement_score=float(result.blend_result.disagreement_score),
            multi_lane_block_reason=result.trade_blocked_reason,
        ))

    baseline_trades = sum(1 for event in events_out if event.baseline_paper_trade)
    multi_trades = sum(1 for event in events_out if event.multi_lane_paper_trade)
    ratio = None if baseline_trades == 0 else multi_trades / baseline_trades

    baseline_summary = _summary(
        lane="baseline_fast_lane",
        evaluated=len(events),
        readiness_pass=sum(1 for event in events_out if event.baseline_readiness_passed),
        executor_evaluated=len(baseline_candidates),
        paper_trades=baseline_trades,
        blend_pass=0,
        blend_block=0,
    )
    multi_summary = _summary(
        lane="multi_lane_s4_5",
        evaluated=len(events),
        readiness_pass=sum(1 for event in events_out if event.multi_lane_readiness_passed),
        executor_evaluated=len(multi_candidates),
        paper_trades=multi_trades,
        blend_pass=sum(1 for event in events_out if event.multi_lane_candidate),
        blend_block=sum(1 for event in events_out if not event.multi_lane_candidate),
    )

    return ComparisonReport(
        fixture="LLM_POSITIVE_EVENTS_2026_04_26",
        offline_paper_only=True,
        baseline=baseline_summary,
        multi_lane=multi_summary,
        frequency_ratio_multi_lane_to_baseline=ratio,
        within_2x_trade_frequency_constraint=(ratio is None or ratio <= 2.0),
        events=events_out,
    )


def _print_summary(report: ComparisonReport) -> None:
    print("=" * 96)
    print("Baseline vs multi-lane validation - PROFIT-VALID-001")
    print("=" * 96)
    print(f"fixture           : {report.fixture}")
    print(f"offline/paper only: {report.offline_paper_only}")
    print()
    for lane in (report.baseline, report.multi_lane):
        print(f"{lane.lane}")
        print(f"  evaluated candidates       : {lane.evaluated_candidates}")
        print(f"  readiness pass/block       : {lane.readiness_pass_count}/{lane.readiness_block_count}")
        print(f"  executor evaluated         : {lane.executor_evaluated_count}")
        print(f"  paper trades               : {lane.paper_trade_count}")
        print(f"  acceptance rate            : {lane.acceptance_rate:.3f}")
        print(f"  trade frequency/candidate  : {lane.trade_frequency_per_candidate:.3f}")
        print(f"  blend pass/block           : {lane.blend_pass_count}/{lane.blend_block_count}")
    print()
    ratio = report.frequency_ratio_multi_lane_to_baseline
    ratio_text = "n/a" if ratio is None else f"{ratio:.3f}x"
    print(f"multi-lane / baseline trade-frequency ratio: {ratio_text}")
    print(f"within 2x constraint                        : {report.within_2x_trade_frequency_constraint}")
    print()
    print("Per-event")
    for event in report.events:
        print(
            f"  {event.event_name}: baseline_trade={event.baseline_paper_trade} "
            f"multi_trade={event.multi_lane_paper_trade} "
            f"blend_block={event.multi_lane_block_reason}"
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare fast-lane baseline with multi-lane S4.5 validation metrics.",
    )
    parser.add_argument("--json", action="store_true", help="emit one JSON document")
    args = parser.parse_args(argv)

    report = run()
    if args.json:
        print(json.dumps(asdict(report), separators=(",", ":")))
    else:
        _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
