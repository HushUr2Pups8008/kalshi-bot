#!/usr/bin/env python3
"""Cycle-14 synthetic high-information lane diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.dossier_builder import classify_update, update_dossier
from analysis.evidence_types import Dossier, EvidenceScore
from analysis.signal_analyzer import estimate_probability
from feeds import NewsItem
from kalshi import KalshiMarket


@dataclass(frozen=True)
class SyntheticFixture:
    name: str
    market_title: str
    headline: str
    body: str
    resolved_yes: bool
    implied_probability: float


FIXTURES = [
    SyntheticFixture(
        name="clear_yes",
        market_title="Will FISA Section 702 reauthorization become law before May 1, 2026?",
        headline="FISA Section 702 reauthorization signed into law on April 30, 2026",
        body="The president signed the reauthorization before the market deadline.",
        resolved_yes=True,
        implied_probability=0.90,
    ),
    SyntheticFixture(
        name="clear_no",
        market_title="Will FISA Section 702 reauthorization become law before May 1, 2026?",
        headline="Congress adjourns past deadline without passing FISA Section 702 reauthorization",
        body="The deadline passed without enactment.",
        resolved_yes=False,
        implied_probability=0.10,
    ),
]


def _initial_dossier(name: str) -> Dossier:
    now = datetime(2026, 5, 6, tzinfo=UTC).isoformat()
    return Dossier(
        market_ticker=f"KXSYNTH-{name}",
        dossier_version=0,
        confidence=0.20,
        drift_suspect=False,
        in_recovery=False,
        created_ts=now,
        updated_ts=now,
        current_estimate=0.50,
        prior_estimate=0.50,
        freeze_started_ts=None,
        recovery_started_ts=None,
        recovery_until_ts=None,
        last_cross_class_state_update_ts=None,
    )


def _score(name: str, implied_probability: float) -> EvidenceScore:
    return EvidenceScore(
        evidence_id=f"synthetic-{name}",
        source_class="official",
        quality_score=0.95,
        original_weight=0.95,
        is_duplicate=False,
        correlation_discount_applied=False,
        is_independent=True,
        same_class_count=0,
        implied_probability=implied_probability,
    )


def _passes(before: float, after: float, resolved_yes: bool) -> bool:
    return abs(after - before) > 0.05 and ((after > before) == resolved_yes)


def run_lane_a() -> dict[str, Any]:
    rows = []
    for fixture in FIXTURES:
        dossier = _initial_dossier(fixture.name)
        score = _score(fixture.name, fixture.implied_probability)
        updated = update_dossier(dossier, score, classify_update(dossier, score))
        rows.append(
            {
                "fixture": fixture.name,
                "before": dossier.current_estimate,
                "after": updated.current_estimate,
                "delta": updated.current_estimate - dossier.current_estimate,
                "correct_direction": (updated.current_estimate > dossier.current_estimate) == fixture.resolved_yes,
                "pass": _passes(dossier.current_estimate, updated.current_estimate, fixture.resolved_yes),
            }
        )
    return {"rows": rows, "pass": all(row["pass"] for row in rows)}


async def _extract_probability(fixture: SyntheticFixture) -> float:
    news = NewsItem(headline=fixture.headline, body=fixture.body, source="synthetic", url="synthetic://cycle14")
    market = KalshiMarket(
        ticker=f"KXSYNTH-{fixture.name}",
        title=fixture.market_title,
        yes_bid=50,
        yes_ask=50,
        yes_price=50,
        volume=1,
        open_interest=1,
        close_time="2026-05-01T00:00:00Z",
        status="open",
        series_ticker="KXSYNTH",
        # P-5 CR-C: post-P0 fields required for guarded legacy reads.
        yes_bid_cents=50,
        yes_ask_cents=50,
        no_bid_cents=50,
        no_ask_cents=50,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
    )
    prob, *_ = await estimate_probability(
        news,
        market,
        match_meta={
            "score": 1.0,
            "pre_llm_quality_pass": True,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 1.0,
            "pre_llm_gate_reason": "synthetic_cycle14",
        },
        is_startup_probe=True,
    )
    return float(prob)


def run_lane_b(extractor: Callable[[SyntheticFixture], float] | None = None) -> dict[str, Any]:
    rows = []
    for fixture in FIXTURES:
        extracted_prob = extractor(fixture) if extractor is not None else asyncio.run(_extract_probability(fixture))
        dossier = _initial_dossier(fixture.name)
        score = _score(fixture.name, extracted_prob)
        updated = update_dossier(dossier, score, classify_update(dossier, score))
        rows.append(
            {
                "fixture": fixture.name,
                "extracted_probability": extracted_prob,
                "before": dossier.current_estimate,
                "after": updated.current_estimate,
                "delta": updated.current_estimate - dossier.current_estimate,
                "correct_direction": (updated.current_estimate > dossier.current_estimate) == fixture.resolved_yes,
                "pass": _passes(dossier.current_estimate, updated.current_estimate, fixture.resolved_yes),
            }
        )
    return {"rows": rows, "pass": all(row["pass"] for row in rows)}


def run_synthetic_lanes(extractor: Callable[[SyntheticFixture], float] | None = None) -> dict[str, Any]:
    lane_a = run_lane_a()
    lane_b = run_lane_b(extractor=extractor)
    if lane_a["pass"] and lane_b["pass"]:
        verdict = "synthetic_lanes_pass"
    elif lane_a["pass"]:
        verdict = "extraction_broken"
    else:
        verdict = "dossier_update_broken"
    return {"lane_a": lane_a, "lane_b": lane_b, "verdict": verdict}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_synthetic_lanes()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "verdict": result["verdict"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
