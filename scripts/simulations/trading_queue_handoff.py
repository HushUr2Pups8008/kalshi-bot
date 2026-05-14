"""Trading-queue → executor handoff simulation — Task D.

Replicates the wiring of :func:`main._trading_queue_consumer_task` to
verify the queue → executor pipe behaves correctly under a normal drain
*and* under back-pressure. The real consumer reads
``await self._trading_queue.get()`` and calls
``await self._executor.execute(candidate)`` in a loop, with a
``task_done()`` in a ``finally`` block.

This harness pins:

*   FIFO contract — consumer drains in enqueue order.
*   No silent drop on back-pressure — when the producer enqueues past
    ``maxsize``, ``put()`` awaits the consumer rather than dropping the
    candidate or raising.
*   Executor is invoked once per candidate, with the candidate as the
    argument — exact match to the production call shape.

Origin: PROFIT-EDGE-004 — Task D in
``docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md``.

Read-only — no DB writes, no trade-log writes, no network. Uses an
in-memory queue, a no-op recording executor stub, and synthetic
``TradeCandidate`` objects shaped from the canonical events.

Usage
-----
::

    .venv/bin/python scripts/simulations/trading_queue_handoff.py
    .venv/bin/python scripts/simulations/trading_queue_handoff.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time as _time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis import SignalAnalysis  # noqa: E402
from feeds import NewsItem  # noqa: E402
from scripts.simulations._common import (  # noqa: E402
    LLM_POSITIVE_EVENT_HEADLINES_2026_04_26,
    LLM_POSITIVE_EVENTS_2026_04_26,
    LLMPositiveEvent,
    synthetic_market,
)
from tasks.blend_task import TradeCandidate  # noqa: E402
from tasks.trade_readiness_gate import ReadinessDecision  # noqa: E402


# ── TradeCandidate construction ───────────────────────────────────────────────


def _readiness_decision_passed() -> ReadinessDecision:
    """Minimal-shape readiness decision marking a candidate as approved."""
    return ReadinessDecision(
        passed=True,
        failure_reasons=(),
        trade_blocked_reason=None,
        readiness_gate_min_edge_override=None,
        scaled_confidence=0.10,
        regime_confidence=0.50,
        fail_safe_active=False,
        applied_conditions=("G1", "G3", "G4"),
    )


def _signal_analysis(event: LLMPositiveEvent, headline: str) -> SignalAnalysis:
    market = synthetic_market(event)
    estimated_prob = event.fast_p
    edge = estimated_prob - market.yes_price / 100.0
    return SignalAnalysis(
        news_item=NewsItem(
            headline=headline,
            url="https://example.invalid/handoff",
            source="trading-queue-handoff",
            published=datetime(2026, 4, 26, tzinfo=timezone.utc),
        ),
        market=market,
        estimated_probability=estimated_prob,
        executed_price_cents=int(round(market.yes_price)),  # F-16: canonical post-P0; __post_init__ mirrors to market_yes_price
        edge=edge,
        side=event.side,
        kelly_fraction=0.0,
        kelly_dollars=0.0,
        capped_dollars=0.0,
        confidence=event.fast_conf,
        match_score=0.50,
        signal_type="news",
        llm_direction=event.side,
        llm_magnitude="small",
        llm_confidence=event.fast_conf,
        signal_meta={},
    )


def _candidate_for_event(event: LLMPositiveEvent) -> TradeCandidate:
    headline = LLM_POSITIVE_EVENT_HEADLINES_2026_04_26.get(event.name, event.title)
    analysis = _signal_analysis(event, headline)
    return TradeCandidate(
        fast_lane_analysis=analysis,
        market=analysis.market,
        blended_probability=analysis.estimated_probability,
        market_yes_price=analysis.market.yes_price,
        side=event.side,
        signal_meta={
            "source_lane": "fast",
            "blended_confidence": event.fast_conf,
            "disagreement_score": 0.0,
        },
        readiness_decision=_readiness_decision_passed(),
    )


# ── Recording executor stub ──────────────────────────────────────────────────


class _RecordingExecutor:
    """No-op stub matching the shape ``await executor.execute(candidate)``.

    Captures the order of received candidates + the wallclock at which
    each was processed so the harness can assert FIFO + no drops."""

    def __init__(self, *, latency_seconds: float = 0.0) -> None:
        self._latency = latency_seconds
        self.received: list[TradeCandidate] = []
        self.received_at: list[float] = []

    async def execute(self, candidate: TradeCandidate) -> str:
        if self._latency > 0:
            await asyncio.sleep(self._latency)
        self.received.append(candidate)
        self.received_at.append(_time.monotonic())
        return f"sim-{len(self.received):03d}"


# ── Consumer (mirrors main._trading_queue_consumer_task) ──────────────────────


async def _consumer(
    queue: asyncio.Queue[TradeCandidate],
    executor: _RecordingExecutor,
    *,
    stop_after: int,
) -> None:
    drained = 0
    while drained < stop_after:
        candidate = await queue.get()
        try:
            await executor.execute(candidate)
        finally:
            queue.task_done()
            drained += 1


# ── Reports ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HandoffReport:
    candidate_index: int
    ticker: str
    enqueue_ts: float
    dequeue_ts: float
    waited_seconds: float


@dataclass(frozen=True)
class BackpressureReport:
    queue_maxsize: int
    enqueued: int
    drained: int
    producer_ever_blocked: bool
    no_drops: bool
    total_seconds: float


@dataclass(frozen=True)
class HandoffRunReport:
    fifo_handoffs: tuple[HandoffReport, ...]
    fifo_in_order: bool
    backpressure: BackpressureReport


# ── Scenarios ─────────────────────────────────────────────────────────────────


async def _drain_in_order_scenario() -> tuple[tuple[HandoffReport, ...], bool]:
    """Enqueue one candidate per canonical event, drain, assert FIFO order."""
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    executor = _RecordingExecutor()
    candidates = [_candidate_for_event(ev) for ev in LLM_POSITIVE_EVENTS_2026_04_26]

    enqueue_ts: list[float] = []
    for c in candidates:
        enqueue_ts.append(_time.monotonic())
        await queue.put(c)

    consumer_task = asyncio.create_task(
        _consumer(queue, executor, stop_after=len(candidates))
    )
    await queue.join()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    handoffs = tuple(
        HandoffReport(
            candidate_index=i,
            ticker=c.market.ticker,
            enqueue_ts=enqueue_ts[i],
            dequeue_ts=executor.received_at[i],
            waited_seconds=executor.received_at[i] - enqueue_ts[i],
        )
        for i, c in enumerate(candidates)
    )
    received_tickers = [c.market.ticker for c in executor.received]
    expected_tickers = [c.market.ticker for c in candidates]
    fifo_in_order = received_tickers == expected_tickers
    return handoffs, fifo_in_order


async def _backpressure_scenario(*, maxsize: int = 2, n: int = 8) -> BackpressureReport:
    """Enqueue n > maxsize candidates against a slow consumer; assert no drops."""
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue(maxsize=maxsize)
    # Slow consumer so producer puts genuinely block.
    executor = _RecordingExecutor(latency_seconds=0.005)
    base_event = LLM_POSITIVE_EVENTS_2026_04_26[0]

    started = _time.monotonic()
    consumer_task = asyncio.create_task(_consumer(queue, executor, stop_after=n))

    producer_ever_blocked = False
    for _ in range(n):
        # If the queue is full at put() time the producer will await — that is
        # the backpressure contract. Detect it by checking qsize before the put.
        if queue.qsize() >= maxsize:
            producer_ever_blocked = True
        await queue.put(_candidate_for_event(base_event))

    await queue.join()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    return BackpressureReport(
        queue_maxsize=maxsize,
        enqueued=n,
        drained=len(executor.received),
        producer_ever_blocked=producer_ever_blocked,
        no_drops=len(executor.received) == n,
        total_seconds=_time.monotonic() - started,
    )


# ── Top-level runner ──────────────────────────────────────────────────────────


def run() -> HandoffRunReport:
    async def _all() -> HandoffRunReport:
        handoffs, fifo_ok = await _drain_in_order_scenario()
        backpressure = await _backpressure_scenario()
        return HandoffRunReport(
            fifo_handoffs=handoffs,
            fifo_in_order=fifo_ok,
            backpressure=backpressure,
        )

    return asyncio.run(_all())


# ── CLI rendering ─────────────────────────────────────────────────────────────


def _print_report(report: HandoffRunReport) -> None:
    print("=" * 90)
    print("Trading-queue → executor handoff simulation")
    print("=" * 90)
    print("\nFIFO drain (one candidate per canonical event)")
    print("-" * 90)
    print(f"  in_order = {report.fifo_in_order}")
    for h in report.fifo_handoffs:
        print(
            f"  [{h.candidate_index}] {h.ticker:<32}  "
            f"waited={h.waited_seconds * 1000:.3f} ms"
        )
    bp = report.backpressure
    print("\nBack-pressure scenario")
    print("-" * 90)
    print(
        f"  queue_maxsize={bp.queue_maxsize}  enqueued={bp.enqueued}  "
        f"drained={bp.drained}  no_drops={bp.no_drops}"
    )
    print(
        f"  producer_ever_blocked={bp.producer_ever_blocked}  "
        f"total={bp.total_seconds * 1000:.2f} ms"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the trading-queue → executor handoff sim.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON to stdout")
    args = parser.parse_args(argv)

    report = run()
    if args.json:
        json.dump(asdict(report), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
