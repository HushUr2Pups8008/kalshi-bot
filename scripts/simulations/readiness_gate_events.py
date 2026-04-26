"""Readiness-gate end-to-end simulation against the canonical 5 LLM-positive events.

For each event in :data:`scripts.simulations._common.LLM_POSITIVE_EVENTS_2026_04_26`:

1. Compute regime weights from the *current* :func:`analysis.regime_classifier.compute_regime_weights`
   (so changes to ``_SERIES_PRIORS`` are reflected immediately).
2. Run :func:`analysis.decision_blender.blend` with the post-fix ``fast_p`` and
   without accumulation/structural lanes (the realistic shape on first
   engagement; markets without a dossier hit this path).
3. Apply the readiness gate (G1 / G3 / G4) using the live thresholds.
4. Report pre-fix (``fast_p=0.5``) vs post-fix (``fast_p=event.fast_p``).
5. Pre-flight check whether the sport-prefix blocklist would have rejected
   the ticker upstream.

Origin: PROFIT-EDGE-001 / EDGE-002 / EDGE-003 simulations on 2026-04-26.
This is the formalised version of ``/tmp/g1_simulation_v0.29.57.py``.

Usage
-----
::

    .venv/bin/python scripts/simulations/readiness_gate_events.py
    .venv/bin/python scripts/simulations/readiness_gate_events.py --json

Outputs are deterministic for a given code revision and event set.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.decision_blender import LaneInput, blend  # noqa: E402
from analysis.regime_classifier import compute_regime_weights  # noqa: E402
from config import MARKET_SERIES_BLOCKLIST_PREFIXES  # noqa: E402
from scripts.simulations._common import (  # noqa: E402
    LLM_POSITIVE_EVENTS_2026_04_26,
    LLMPositiveEvent,
    regime_confidence,
    synthetic_market,
)
from tasks.trade_readiness_gate import (  # noqa: E402
    G1_CONFIDENCE_THRESHOLD,
    G1_FAILSAFE_CONFIDENCE_THRESHOLD,
    G3_DISAGREEMENT_THRESHOLD,
    G3_FAILSAFE_DISAGREEMENT_THRESHOLD,
    G4_REGIME_CONFIDENCE_THRESHOLD,
)


@dataclass
class GateOutcome:
    passed: bool
    failure_reasons: list[str]
    scaled_confidence: float
    fail_safe_active: bool


def evaluate_gates(blended_conf: float, regime_conf: float, disagreement: float) -> GateOutcome:
    """Lightweight reproduction of ``tasks.trade_readiness_gate.evaluate_readiness``
    restricted to G1 / G3 / G4. Used so the simulation can run without
    constructing a full readiness candidate dict."""
    fail_safe = regime_conf < G4_REGIME_CONFIDENCE_THRESHOLD
    g1 = G1_FAILSAFE_CONFIDENCE_THRESHOLD if fail_safe else G1_CONFIDENCE_THRESHOLD
    g3 = G3_FAILSAFE_DISAGREEMENT_THRESHOLD if fail_safe else G3_DISAGREEMENT_THRESHOLD
    scaled = blended_conf * regime_conf
    fails: list[str] = []
    if scaled < g1:
        fails.append(f"G1 (scaled {scaled:.4f} < {g1})")
    if disagreement > g3:
        fails.append(f"G3 (disagreement {disagreement:.4f} > {g3})")
    if regime_conf < G4_REGIME_CONFIDENCE_THRESHOLD:
        fails.append(f"G4 (rc {regime_conf:.4f} < {G4_REGIME_CONFIDENCE_THRESHOLD})")
    return GateOutcome(
        passed=not fails,
        failure_reasons=fails,
        scaled_confidence=scaled,
        fail_safe_active=fail_safe,
    )


@dataclass
class EventReport:
    event_name: str
    ticker: str
    sport_blocked: bool
    regime_weights: dict[str, float]
    regime_confidence: float
    pre_fix_outcome: GateOutcome
    post_fix_outcome: GateOutcome


def is_sport_blocked(ticker: str) -> bool:
    upper = ticker.upper()
    return any(upper.startswith(p) for p in MARKET_SERIES_BLOCKLIST_PREFIXES)


def simulate_event(event: LLMPositiveEvent) -> EventReport:
    market = synthetic_market(event)
    rw = compute_regime_weights(market)
    rc = regime_confidence(list(rw.values()))

    fast_pre = LaneInput(lane_id="fast", p=0.5, confidence=event.fast_conf)
    fast_post = LaneInput(lane_id="fast", p=event.fast_p, confidence=event.fast_conf)

    pre = blend(
        fast=fast_pre, accumulation=None, structural=None,
        regime_weights=rw, regime_confidence=rc,
    )
    post = blend(
        fast=fast_post, accumulation=None, structural=None,
        regime_weights=rw, regime_confidence=rc,
    )

    return EventReport(
        event_name=event.name,
        ticker=event.ticker,
        sport_blocked=is_sport_blocked(event.ticker),
        regime_weights=dict(rw),
        regime_confidence=rc,
        pre_fix_outcome=evaluate_gates(pre.blended_confidence, rc, pre.disagreement_score),
        post_fix_outcome=evaluate_gates(post.blended_confidence, rc, post.disagreement_score),
    )


def run() -> list[EventReport]:
    return [simulate_event(ev) for ev in LLM_POSITIVE_EVENTS_2026_04_26]


def _print_text_report(reports: list[EventReport]) -> None:
    print("=" * 100)
    print(f"READINESS GATE SIMULATION  —  {len(reports)} canonical LLM-positive events")
    print("  G1={}  G1-failsafe={}  G3={}  G3-failsafe={}  G4={}".format(
        G1_CONFIDENCE_THRESHOLD,
        G1_FAILSAFE_CONFIDENCE_THRESHOLD,
        G3_DISAGREEMENT_THRESHOLD,
        G3_FAILSAFE_DISAGREEMENT_THRESHOLD,
        G4_REGIME_CONFIDENCE_THRESHOLD,
    ))
    print("=" * 100)
    n_pre = n_post = 0
    for r in reports:
        sport = "  ← sport-blocked upstream" if r.sport_blocked else ""
        print(f"\n{r.event_name}{sport}")
        print(f"  ticker={r.ticker}  rw={r.regime_weights}  rc={r.regime_confidence:.4f}")
        print(f"  PRE  fast_p=0.500  scaled={r.pre_fix_outcome.scaled_confidence:.4f}  "
              f"pass={r.pre_fix_outcome.passed}"
              + (f"  fails={r.pre_fix_outcome.failure_reasons}" if r.pre_fix_outcome.failure_reasons else ""))
        print(f"  POST fast_p={get_event_fast_p(r):.3f}  scaled={r.post_fix_outcome.scaled_confidence:.4f}  "
              f"pass={r.post_fix_outcome.passed}"
              + (f"  fails={r.post_fix_outcome.failure_reasons}" if r.post_fix_outcome.failure_reasons else ""))
        if r.pre_fix_outcome.passed:
            n_pre += 1
        if r.post_fix_outcome.passed:
            n_post += 1
    print()
    print("=" * 100)
    print(f"SUMMARY: {n_post}/{len(reports)} events clear readiness gate post-fix "
          f"(pre-fix: {n_pre}/{len(reports)})")
    print("=" * 100)


def get_event_fast_p(report: EventReport) -> float:
    for ev in LLM_POSITIVE_EVENTS_2026_04_26:
        if ev.ticker == report.ticker and ev.name == report.event_name:
            return ev.fast_p
    return float("nan")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n\n", 1)[0],
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit one JSONL record per event report (suitable for archiving)",
    )
    args = parser.parse_args(argv)

    reports = run()
    if args.json:
        for r in reports:
            payload = asdict(r)
            print(json.dumps(payload, separators=(",", ":")))
    else:
        _print_text_report(reports)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
