#!/usr/bin/env python3
"""Cycle-15B per-step extraction trace harness.

Read-only diagnostic: records extraction-stage signal magnitudes without changing
the production estimate path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.signal_analyzer import estimate_probability, keyword_estimate
from feeds import NewsItem
from kalshi import KalshiMarket


BASE_PROBABILITY = 0.50
ZERO_COLLAPSE_INPUT_FLOOR = 0.05
ZERO_COLLAPSE_OUTPUT_CEILING = 0.01


def _signed_expected_magnitude(fixture: dict[str, Any]) -> float:
    direction = str(fixture.get("expected_direction") or "NEUTRAL").upper()
    if direction == "NEUTRAL":
        return 0.0
    magnitude = float(fixture.get("expected_magnitude_min") or 0.05)
    if magnitude <= ZERO_COLLAPSE_INPUT_FLOOR:
        magnitude = ZERO_COLLAPSE_INPUT_FLOOR + 0.001
    return magnitude if direction == "YES" else -magnitude


def _news(fixture: dict[str, Any]) -> NewsItem:
    return NewsItem(
        headline=str(fixture.get("headline") or ""),
        body=str(fixture.get("body") or ""),
        source=str(fixture.get("source") or "synthetic"),
        url=str(fixture.get("url") or "synthetic://cycle15b"),
    )


def _market(fixture: dict[str, Any]) -> KalshiMarket:
    return KalshiMarket(
        ticker=str(fixture.get("market_ticker") or "KXSYNTH"),
        title=str(fixture.get("market_title") or fixture.get("headline") or "Synthetic market"),
        yes_bid=50,
        yes_ask=50,
        yes_price=50,
        volume=1,
        open_interest=1,
        close_time="2026-05-01T00:00:00Z",
        status="open",
        series_ticker=str(fixture.get("market_ticker") or "KXSYNTH").split("-", 1)[0],
        # P-5 CR-C: post-P0 fields required for the guarded legacy reads.
        yes_bid_cents=50,
        yes_ask_cents=50,
        no_bid_cents=50,
        no_ask_cents=50,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
    )


def _step(name: str, input_magnitude: float, output_magnitude: float, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_name": name,
        "input_signal_magnitude": input_magnitude,
        "output_signal_magnitude": output_magnitude,
        "intermediate_state": state,
    }


async def _llm_final_probability(news: NewsItem, market: KalshiMarket) -> tuple[float, dict[str, Any]]:
    prob, confidence, keywords, reasoning, llm_direction, llm_magnitude, llm_confidence = await estimate_probability(
        news,
        market,
        match_meta={
            "score": 1.0,
            "pre_llm_quality_pass": True,
            "pre_llm_semantic_overlap_count": 1,
            "pre_llm_semantic_overlap_ratio": 1.0,
            "pre_llm_gate_reason": "cycle15b_trace",
        },
        is_startup_probe=True,
    )
    return float(prob), {
        "confidence": confidence,
        "keywords": keywords,
        "reasoning": reasoning,
        "llm_direction": llm_direction,
        "llm_magnitude": llm_magnitude,
        "llm_confidence": llm_confidence,
    }


def trace_fixture(fixture: dict[str, Any], *, run_llm: bool = True) -> dict[str, Any]:
    news = _news(fixture)
    market = _market(fixture)
    expected = _signed_expected_magnitude(fixture)
    steps: list[dict[str, Any]] = []
    steps.append(
        _step(
            "fixture_expected_signal",
            expected,
            expected,
            {
                "expected_direction": fixture.get("expected_direction"),
                "expected_magnitude_min": fixture.get("expected_magnitude_min"),
                "expected_magnitude_max": fixture.get("expected_magnitude_max"),
            },
        )
    )

    kw_prob, kw_side, keywords, kw_reasoning = keyword_estimate(news, market, base_probability=BASE_PROBABILITY)
    keyword_signal = float(kw_prob) - BASE_PROBABILITY
    steps.append(
        _step(
            "keyword_path",
            expected,
            keyword_signal,
            {
                "estimated_probability": kw_prob,
                "side": kw_side,
                "keywords": keywords,
                "reasoning": kw_reasoning,
            },
        )
    )

    if run_llm:
        final_prob, llm_state = asyncio.run(_llm_final_probability(news, market))
        llm_signal = final_prob - BASE_PROBABILITY
    else:
        final_prob = kw_prob
        llm_signal = 0.0
        llm_state = {"skipped": True}
    steps.append(_step("llm_path", expected, llm_signal, llm_state))

    final_signal = final_prob - BASE_PROBABILITY
    steps.append(
        _step(
            "final_estimate",
            max(keyword_signal, llm_signal, key=abs),
            final_signal,
            {"estimated_probability": final_prob, "delta": final_signal},
        )
    )
    return {
        "fixture_id": fixture.get("fixture_id"),
        "market_ticker": fixture.get("market_ticker"),
        "expected_direction": fixture.get("expected_direction"),
        "steps": steps,
    }


def identify_zero_collapse_step(traces: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for trace in traces:
        for step in trace.get("steps", []):
            if (
                abs(float(step["input_signal_magnitude"])) > ZERO_COLLAPSE_INPUT_FLOOR
                and abs(float(step["output_signal_magnitude"])) < ZERO_COLLAPSE_OUTPUT_CEILING
            ):
                name = str(step["step_name"])
                counts[name] = counts.get(name, 0) + 1
                examples.setdefault(name, []).append(str(trace.get("fixture_id")))
                break
    if not counts:
        return {
            "finding_type": "no_single_step",
            "zero_collapse_step": None,
            "counts": counts,
            "criterion": {
                "input_abs_gt": ZERO_COLLAPSE_INPUT_FLOOR,
                "output_abs_lt": ZERO_COLLAPSE_OUTPUT_CEILING,
            },
        }
    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    step_name, count = ordered[0]
    return {
        "finding_type": "single_step" if len(ordered) == 1 or ordered[0][1] > ordered[1][1] else "multi_step",
        "zero_collapse_step": step_name,
        "count": count,
        "counts": counts,
        "examples": examples.get(step_name, []),
        "criterion": {
            "input_abs_gt": ZERO_COLLAPSE_INPUT_FLOOR,
            "output_abs_lt": ZERO_COLLAPSE_OUTPUT_CEILING,
        },
    }


def load_fixtures(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("tests/fixtures/cycle14_synthetic_evidence.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zero-collapse-output", type=Path, required=True)
    parser.add_argument("--no-llm", action="store_true", help="Skip live LLM call for schema-only tests.")
    args = parser.parse_args()
    traces = [trace_fixture(fixture, run_llm=not args.no_llm) for fixture in load_fixtures(args.fixtures)]
    zero = identify_zero_collapse_step(traces)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"traces": traces}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.zero_collapse_output.parent.mkdir(parents=True, exist_ok=True)
    args.zero_collapse_output.write_text(json.dumps(zero, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "zero_collapse_step": zero["zero_collapse_step"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
