#!/usr/bin/env python3
"""Cycle-15B C3: audit LLM prompt convention against Lane B fixtures."""

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

from analysis.signal_analyzer import _extract_json, _ollama_estimate_detailed
from scripts.edge_replay.cycle15b_common import (
    FIXTURES_PATH,
    OUTPUT_DIR,
    expected_is_directional,
    fixture_market,
    fixture_news,
    load_fixtures,
    write_json,
)


def flag_llm_output(fixture: dict[str, Any], parsed: dict[str, Any] | None) -> dict[str, Any]:
    reasons: list[str] = []
    direction = str((parsed or {}).get("direction") or "").lower()
    magnitude = str((parsed or {}).get("magnitude") or "").lower()
    if expected_is_directional(fixture) and (direction == "neutral" or magnitude == "none"):
        reasons.append("directional_fixture_collapsed")
    expected = str(fixture.get("expected_direction") or "").lower()
    if expected in {"yes", "no"} and direction in {"yes", "no"} and direction != expected:
        reasons.append("wrong_direction")
    return {"flagged": bool(reasons), "reasons": reasons}


async def audit_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    news = fixture_news(fixture)
    market = fixture_market(fixture)
    result, meta = await _ollama_estimate_detailed(news, market)
    raw_response = meta.get("raw_response")
    parsed = None
    if isinstance(raw_response, str) and raw_response.strip():
        try:
            parsed = _extract_json(raw_response.strip())
        except ValueError:
            parsed = None
    direction = None
    magnitude = None
    if isinstance(result, tuple) and len(result) >= 5:
        direction = result[3]
        magnitude = result[4]
    elif parsed:
        direction = parsed.get("direction")
        magnitude = parsed.get("magnitude")
    flag = flag_llm_output(fixture, parsed or {"direction": direction, "magnitude": magnitude})
    return {
        "fixture_id": fixture.get("fixture_id"),
        "expected_direction": fixture.get("expected_direction"),
        "status": meta.get("status"),
        "provider": meta.get("provider"),
        "prompt": meta.get("prompt"),
        "raw_response": raw_response,
        "parsed": parsed,
        "direction": direction,
        "magnitude": magnitude,
        "flagged": flag["flagged"],
        "flag_reasons": flag["reasons"],
    }


async def audit_llm_prompt_outputs(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [await audit_fixture(fixture) for fixture in fixtures]
    return {
        "audit": "cycle15b_llm_prompt_convention",
        "fixture_count": len(rows),
        "flagged_count": sum(1 for row in rows if row["flagged"]),
        "statuses": sorted({str(row["status"]) for row in rows}),
        "fixtures": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "llm_prompt_audit.json")
    args = parser.parse_args()
    payload = asyncio.run(audit_llm_prompt_outputs(load_fixtures(args.fixtures)))
    write_json(args.output, payload)
    print(json.dumps({"output": str(args.output), "flagged_count": payload["flagged_count"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
