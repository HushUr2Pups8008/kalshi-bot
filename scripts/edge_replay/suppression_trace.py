#!/usr/bin/env python3
"""Cycle-15B C5: trace suppression and magnitude-shift decisions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.signal_analyzer import _keyword_score, keyword_estimate
from scripts.edge_replay.cycle15b_common import (
    BASE_PROBABILITY,
    FIXTURES_PATH,
    OUTPUT_DIR,
    fixture_market,
    fixture_news,
    load_fixtures,
    write_json,
)


def classify_suppression(
    reasoning: str,
    *,
    pre_suppression_magnitude: float,
    post_suppression_magnitude: float,
) -> dict[str, Any]:
    if reasoning.startswith("Geo-entity mismatch:"):
        return {
            "suppression_triggered": True,
            "suppression_reason": "geo_entity_mismatch",
            "pre_suppression_magnitude": pre_suppression_magnitude,
            "post_suppression_magnitude": post_suppression_magnitude,
        }
    if abs(pre_suppression_magnitude) >= 0.01 and abs(post_suppression_magnitude) < 0.01:
        reason = "zero_shift_after_keyword_estimate"
    else:
        reason = None
    return {
        "suppression_triggered": False,
        "suppression_reason": reason,
        "pre_suppression_magnitude": pre_suppression_magnitude,
        "post_suppression_magnitude": post_suppression_magnitude,
    }


def trace_fixture_suppression(fixture: dict[str, Any]) -> dict[str, Any]:
    news = fixture_news(fixture)
    market = fixture_market(fixture)
    combined_text = f"{news.headline} {news.body}"
    pre_shift, direction, matched = _keyword_score(combined_text, series_ticker=market.series_ticker or "")
    prob, side, keywords, reasoning = keyword_estimate(news, market, base_probability=BASE_PROBABILITY)
    post_shift = float(prob) - BASE_PROBABILITY
    classified = classify_suppression(
        reasoning,
        pre_suppression_magnitude=float(pre_shift),
        post_suppression_magnitude=post_shift,
    )
    return {
        "fixture_id": fixture.get("fixture_id"),
        "expected_direction": fixture.get("expected_direction"),
        "raw_keyword_direction": direction,
        "raw_matched_keywords": matched,
        "keyword_estimate_side": side,
        "keyword_estimate_keywords": keywords,
        "keyword_estimate_reasoning": reasoning,
        **classified,
    }


def trace_suppression(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [trace_fixture_suppression(fixture) for fixture in fixtures]
    return {
        "audit": "cycle15b_suppression_trace",
        "fixture_count": len(rows),
        "suppression_count": sum(1 for row in rows if row["suppression_triggered"]),
        "fixtures": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "suppression_trace.json")
    args = parser.parse_args()
    payload = trace_suppression(load_fixtures(args.fixtures))
    write_json(args.output, payload)
    print(json.dumps({"output": str(args.output), "suppression_count": payload["suppression_count"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
