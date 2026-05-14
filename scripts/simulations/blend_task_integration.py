"""Full BlendTask integration simulation — Task B of pipeline buildout.

Drives the real :class:`tasks.blend_task.BlendTask` against each of the
five canonical LLM-positive events, with a production-shaped dossier +
structural prior + recent evidence seeded for KXTRUMPIRAN-26MAY01.

The existing :mod:`scripts.simulations.readiness_gate_events` harness
calls :func:`analysis.decision_blender.blend` directly with no
accumulation / structural lanes. The real production path runs every
lane through ``BlendTask.process_fast_lane_result``:

1. Reads dossier + structural prior + recent evidence from the store.
2. Computes regime weights + regime confidence.
3. Runs ``decision_blender.blend(...)`` with all available lanes.
4. Evaluates the readiness gate.
5. Emits a ``BLEND_DECISION`` log record (intercepted by a spy here).
6. Enqueues a ``TradeCandidate`` if readiness passed.

When a market has a dossier with recent evidence the *accumulation lane*
contributes a non-0.5 ``acc_p``. If accumulation disagrees with fast,
``disagreement_score`` may exceed G3 = 0.20 and silently block a trade
that ``readiness_gate_events`` (no-dossier path) would have predicted to
pass. This harness surfaces that divergence per event.

Origin: PROFIT-EDGE-004 — Task B in
``docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md``.

Read-only: uses an in-memory ``FakeStore`` shaped after the BlendTask
contract, an in-memory queue, and a spy logger. No DB writes, no
trade-log writes.

Usage
-----
::

    .venv/bin/python scripts/simulations/blend_task_integration.py
    .venv/bin/python scripts/simulations/blend_task_integration.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis import SignalAnalysis  # noqa: E402
from analysis.regime_classifier import compute_regime_weights  # noqa: E402
from feeds import NewsItem  # noqa: E402
from scripts.simulations._common import (  # noqa: E402
    LLM_POSITIVE_EVENT_HEADLINES_2026_04_26,
    LLM_POSITIVE_EVENTS_2026_04_26,
    LLMPositiveEvent,
    synthetic_market,
)
from scripts.simulations.readiness_gate_events import simulate_event  # noqa: E402
from tasks.blend_task import BlendTask, TradeCandidate  # noqa: E402
from tasks.evidence_store import (  # noqa: E402
    DossierState,
    EvidenceRecord,
    StructuralPriorRecord,
)


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeStore:
    """Shaped after :class:`tasks.blend_task.EvidenceStoreLike`. Returns the
    pre-seeded dossier / prior / evidence for any ticker the harness asks
    about. Keys default to None (no slow-lane context) when no seed."""

    def __init__(
        self,
        *,
        dossier: DossierState | None = None,
        structural_prior: StructuralPriorRecord | None = None,
        evidence: list[EvidenceRecord] | None = None,
    ) -> None:
        self.dossier = dossier
        self.structural_prior = structural_prior
        self.evidence = list(evidence or [])

    async def get_dossier(self, market_ticker: str) -> DossierState | None:
        return self.dossier

    async def get_structural_prior(
        self,
        market_ticker: str,
    ) -> StructuralPriorRecord | None:
        return self.structural_prior

    async def get_recent_evidence(
        self,
        market_ticker: str,
        *,
        limit: int = 100,
    ) -> list[EvidenceRecord]:
        return list(self.evidence)


class _SpyLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.skipped_records: list[dict] = []

    def log_blend_decision(self, **kwargs) -> None:
        self.records.append(kwargs)

    def log_skipped(self, **kwargs) -> None:
        self.skipped_records.append(kwargs)


# ── Seed helpers ──────────────────────────────────────────────────────────────


def _seed_kxtrumpiran_state(market_ticker: str) -> _FakeStore:
    """Build a production-shaped store for KXTRUMPIRAN-26MAY01.

    Mirrors the v0.29.54 paper-soak observation cited in
    ``docs/profit_path_debt_log.md::PROFIT-EDGE-002`` — KXTRUMPIRAN had
    10 evidence records and an LLM-synthesised structural prior at the
    time of the diagnostic events. The exact numbers are not load-bearing;
    the *presence* of an accumulation lane is what makes the integration
    path diverge from the no-dossier readiness simulation.
    """
    # Anchor evidence within hours of the simulation's "now" (2026-04-26)
    # so recency-driven gates (G6) see fresh material — this matches the
    # production timing for the dispatching / talks-stall headlines that
    # produced the canonical signals.
    base = datetime(2026, 4, 25, 12, tzinfo=UTC)
    # Alternate news + official sources to clear G2's source-class-diversity
    # check. Production evidence streams typically mix wire reporting with
    # statements from the relevant ministry / readout, so this matches the
    # shape we expect to see for an LLM-positive Iran market.
    source_specs = [
        ("Reuters", "news"),
        ("State Department", "official"),
        ("AP", "news"),
        ("White House", "official"),
        ("Times of Israel", "news"),
        ("Iranian Foreign Ministry", "official"),
        ("Al Jazeera", "news"),
        ("Pentagon", "official"),
        ("BBC", "news"),
        ("UN spokesperson", "official"),
    ]
    evidence = [
        EvidenceRecord(
            evidence_id=f"ev-iran-{i:02d}",
            market_ticker=market_ticker,
            source=src,
            source_class=src_class,
            headline=f"Iran development {i:02d}",
            ingested_ts=(
                base.replace(hour=(base.hour + i) % 24)
            ).isoformat(),
            content_hash=f"hash-iran-{i:02d}",
            update_type="state",
            dossier_version_before=i,
            dossier_version_after=i + 1,
            original_weight=0.7,
        )
        for i, (src, src_class) in enumerate(source_specs)
    ]
    dossier = DossierState(
        market_ticker=market_ticker,
        dossier_version=10,
        current_estimate=0.42,  # accumulation tilts NO (talks not happening)
        confidence=0.65,
        prior_estimate=0.35,
        drift_suspect=False,
        in_recovery=False,
        created_ts=base.isoformat(),
        updated_ts=(base.replace(hour=12)).isoformat(),
    )
    structural = StructuralPriorRecord(
        market_ticker=market_ticker,
        prior_estimate=0.30,
        confidence=0.55,
        computed_ts=base.replace(hour=6).isoformat(),
        recompute_trigger="dossier_update",
        input_source_count=10,
        llm_called=True,
    )
    return _FakeStore(dossier=dossier, structural_prior=structural, evidence=evidence)


def _empty_store() -> _FakeStore:
    return _FakeStore()


def _build_signal_analysis(event: LLMPositiveEvent, headline: str) -> SignalAnalysis:
    market = synthetic_market(event)
    # Production attaches regime_weights at MarketCache fetch time
    # (analysis.market_matcher._attach_regime_weights). The synthetic_market
    # helper omits them, so we have to populate them here for the integration
    # path to mirror what BlendTask.process_fast_lane_result actually reads
    # off the market.
    market.regime_weights = dict(compute_regime_weights(market))
    estimated_prob = event.fast_p
    edge = estimated_prob - market.yes_price / 100.0
    return SignalAnalysis(
        news_item=NewsItem(
            headline=headline,
            url="https://example.invalid/blend-int",
            source="blend-task-integration",
            published=datetime(2026, 4, 26, tzinfo=UTC),
        ),
        market=market,
        estimated_probability=estimated_prob,
        executed_price_cents=int(round(market.yes_price)),  # F-16: canonical post-P0; __post_init__ mirrors to market_yes_price
        edge=edge,
        side=event.side,
        kelly_fraction=0.0,
        kelly_dollars=0.0,
        capped_dollars=0.0,
        keywords_matched=[],
        reasoning="canonical-event fast lane",
        confidence=event.fast_conf,
        match_score=0.2,
    )


# ── Reports ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BlendIntegrationReport:
    event_name: str
    ticker: str
    has_dossier: bool
    regime_weights: dict[str, float]
    regime_confidence: float
    blended_p: float
    blended_confidence: float
    disagreement_score: float
    blend_mode: str
    accumulation_p: Optional[float]
    structural_p: Optional[float]
    readiness_passed: bool
    trade_blocked_reason: Optional[str]
    enqueued: bool
    # Cross-check vs the no-dossier readiness simulation
    no_dossier_predicted_pass: bool
    diverges_from_no_dossier_prediction: bool


# ── Per-event simulation ──────────────────────────────────────────────────────


async def _run_event(event: LLMPositiveEvent) -> BlendIntegrationReport:
    headline = LLM_POSITIVE_EVENT_HEADLINES_2026_04_26.get(event.name, event.title)

    # Seed the slow lane only for the KXTRUMPIRAN ticker (events 3 + 5 share it).
    if event.ticker == "KXTRUMPIRAN-26MAY01":
        store = _seed_kxtrumpiran_state(event.ticker)
    else:
        store = _empty_store()

    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = _SpyLogger()

    task = BlendTask(
        trading_queue=queue,
        store=store,
        logger=logger,
        is_paper_mode=True,
        now=lambda: datetime(2026, 4, 26, tzinfo=UTC),
    )

    analysis = _build_signal_analysis(event, headline)
    result = await task.process_fast_lane_result(analysis)

    no_dossier_pred = simulate_event(event)

    return BlendIntegrationReport(
        event_name=event.name,
        ticker=event.ticker,
        has_dossier=store.dossier is not None,
        regime_weights=dict(logger.records[0]["regime_weights"]),
        regime_confidence=float(logger.records[0]["regime_confidence"]),
        blended_p=float(result.blend_result.blended_p),
        blended_confidence=float(result.blend_result.blended_confidence),
        disagreement_score=float(result.blend_result.disagreement_score),
        blend_mode=str(result.blend_result.blend_mode),
        accumulation_p=(
            None
            if result.blend_result.accumulation_p is None
            else float(result.blend_result.accumulation_p)
        ),
        structural_p=(
            None
            if result.blend_result.structural_p is None
            else float(result.blend_result.structural_p)
        ),
        readiness_passed=result.readiness_decision.passed,
        trade_blocked_reason=result.trade_blocked_reason,
        enqueued=result.enqueued,
        no_dossier_predicted_pass=no_dossier_pred.post_fix_outcome.passed,
        diverges_from_no_dossier_prediction=(
            no_dossier_pred.post_fix_outcome.passed != bool(result.enqueued)
        ),
    )


def run() -> list[BlendIntegrationReport]:
    """Run the integration sim against every canonical event."""

    async def _all() -> list[BlendIntegrationReport]:
        return [await _run_event(ev) for ev in LLM_POSITIVE_EVENTS_2026_04_26]

    return asyncio.run(_all())


# ── CLI rendering ─────────────────────────────────────────────────────────────


def _print_per_event(reports: list[BlendIntegrationReport]) -> None:
    print("=" * 90)
    print("BlendTask integration simulation — five canonical LLM-positive events")
    print("=" * 90)
    for r in reports:
        flag = " ← divergence vs no-dossier prediction" if r.diverges_from_no_dossier_prediction else ""
        print(f"\n• {r.event_name}{flag}")
        print(f"  ticker             : {r.ticker}  (dossier seeded: {r.has_dossier})")
        print(f"  regime_weights     : {r.regime_weights}")
        print(f"  regime_confidence  : {r.regime_confidence:.4f}")
        print(f"  blend_mode         : {r.blend_mode}")
        print(f"  blended_p / conf   : {r.blended_p:.4f} / {r.blended_confidence:.4f}")
        print(f"  accumulation_p     : {r.accumulation_p}")
        print(f"  structural_p       : {r.structural_p}")
        print(f"  disagreement_score : {r.disagreement_score:.4f}")
        print(f"  readiness_passed   : {r.readiness_passed}")
        print(f"  trade_blocked      : {r.trade_blocked_reason}")
        print(f"  enqueued           : {r.enqueued}")
        print(f"  no-dossier predict : pass={r.no_dossier_predicted_pass}")


def _print_summary(reports: list[BlendIntegrationReport]) -> None:
    enq = sum(1 for r in reports if r.enqueued)
    div = sum(1 for r in reports if r.diverges_from_no_dossier_prediction)
    print("\n" + "-" * 90)
    print(
        f"Summary: {enq}/{len(reports)} events enqueued; "
        f"{div} diverge from the no-dossier readiness prediction"
    )
    print("-" * 90)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the full BlendTask integration sim for the canonical events.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSONL to stdout")
    args = parser.parse_args(argv)

    reports = run()

    if args.json:
        for r in reports:
            print(json.dumps(asdict(r), separators=(",", ":")))
    else:
        _print_per_event(reports)
        _print_summary(reports)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
