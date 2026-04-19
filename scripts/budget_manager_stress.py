"""Synthetic S4.3 stress test for the LLM budget manager."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tasks.budget_manager import (
    GLOBAL_HOURLY_LIMIT,
    PER_MARKET_HOURLY_LIMIT,
    BudgetManager,
)


@dataclass(frozen=True)
class StressResult:
    total_requests: int
    admitted_count: int
    denied_count: int
    final_queue_depth: int
    circuit_breaker_open: bool
    circuit_breaker_threshold: int
    breaker_first_observed_at_request: int | None
    budget_pressure_event_count: int
    budget_pressure_events: list[dict[str, Any]]
    no_runaway_admission: bool
    per_market_limit_holds: bool
    per_market_admitted_in_probe: int
    global_limit_holds: bool
    deterministic_pass: bool


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class CaptureLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_budget_pressure(self, **payload) -> None:
        self.events.append(dict(payload))


def run_stress(
    *,
    per_market_limit: int = PER_MARKET_HOURLY_LIMIT,
    global_limit: int = GLOBAL_HOURLY_LIMIT,
    overflow_requests: int = 25,
) -> StressResult:
    """Drive the manager past 3x queue depth and summarize bounded behavior."""
    clock = FakeClock()
    logger = CaptureLogger()
    manager = BudgetManager(
        per_market_limit=per_market_limit,
        global_limit=global_limit,
        clock=clock,
        logger=logger,
    )
    results: list[bool] = []
    breaker_first_seen: int | None = None

    for idx in range(global_limit):
        results.append(manager.request_llm_call(f"KXSEED-{idx}", priority=1))
        if manager.circuit_breaker_open and breaker_first_seen is None:
            breaker_first_seen = len(results)

    stress_attempts = manager.circuit_breaker_threshold + overflow_requests
    for idx in range(stress_attempts):
        priority = idx % 7
        results.append(manager.request_llm_call(f"KXSTRESS-{idx}", priority=priority))
        if manager.circuit_breaker_open and breaker_first_seen is None:
            breaker_first_seen = len(results)

    admitted_count = sum(1 for result in results if result)
    denied_count = len(results) - admitted_count
    per_market_probe_admitted = _per_market_probe(
        per_market_limit=per_market_limit,
        global_limit=global_limit,
    )
    expected_total = global_limit + stress_attempts
    expected_breaker_request = global_limit + manager.circuit_breaker_threshold
    pressure_event = logger.events[0] if logger.events else {}

    return StressResult(
        total_requests=len(results),
        admitted_count=admitted_count,
        denied_count=denied_count,
        final_queue_depth=manager.queue_depth,
        circuit_breaker_open=manager.circuit_breaker_open,
        circuit_breaker_threshold=manager.circuit_breaker_threshold,
        breaker_first_observed_at_request=breaker_first_seen,
        budget_pressure_event_count=len(logger.events),
        budget_pressure_events=logger.events,
        no_runaway_admission=admitted_count == global_limit,
        per_market_limit_holds=per_market_probe_admitted == per_market_limit,
        per_market_admitted_in_probe=per_market_probe_admitted,
        global_limit_holds=admitted_count <= global_limit,
        deterministic_pass=(
            len(results) == expected_total
            and breaker_first_seen == expected_breaker_request
            and len(logger.events) == 1
            and pressure_event.get("queue_depth") == manager.circuit_breaker_threshold
            and manager.queue_depth == manager.circuit_breaker_threshold
            and admitted_count == global_limit
            and per_market_probe_admitted == per_market_limit
        ),
    )


def render_text(result: StressResult) -> str:
    lines = [
        "BUDGET MANAGER STRESS TEST",
        f"  Total requests:             {result.total_requests}",
        f"  Admitted:                   {result.admitted_count}",
        f"  Denied:                     {result.denied_count}",
        f"  Circuit breaker threshold:  {result.circuit_breaker_threshold}",
        f"  Final queue depth:          {result.final_queue_depth}",
        f"  Breaker open:               {result.circuit_breaker_open}",
        f"  Breaker first observed at:  {result.breaker_first_observed_at_request}",
        f"  BUDGET_PRESSURE events:     {result.budget_pressure_event_count}",
        f"  No runaway admission:       {result.no_runaway_admission}",
        f"  Per-market limit holds:     {result.per_market_limit_holds}",
        f"  Per-market probe admitted:  {result.per_market_admitted_in_probe}",
        f"  Global limit holds:         {result.global_limit_holds}",
        f"  Deterministic pass:         {result.deterministic_pass}",
    ]
    if result.budget_pressure_events:
        lines.append("  First BUDGET_PRESSURE:")
        for key, value in result.budget_pressure_events[0].items():
            lines.append(f"    {key}: {value}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overflow-requests", type=int, default=25)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = run_stress(overflow_requests=args.overflow_requests)
    if args.json:
        print(json.dumps(asdict(result), sort_keys=True, indent=2))
    else:
        print(render_text(result))
    return 0 if result.deterministic_pass else 1


def _per_market_probe(*, per_market_limit: int, global_limit: int) -> int:
    manager = BudgetManager(
        per_market_limit=per_market_limit,
        global_limit=max(global_limit, per_market_limit + 5),
        clock=FakeClock(),
        logger=CaptureLogger(),
    )
    results = [
        manager.request_llm_call("KXHOT-26DEC31", priority=1)
        for _ in range(per_market_limit + 5)
    ]
    return sum(1 for result in results if result)


if __name__ == "__main__":
    raise SystemExit(main())
