#!/usr/bin/env python3
"""Cycle-15B C4: dump keyword direction map and fixture vocabulary coverage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import GEOPOLITICAL_SIGNALS
from scripts.edge_replay.cycle15b_common import FIXTURES_PATH, OUTPUT_DIR, load_fixtures, write_json


EXPECTED_PHRASES = {
    "F1_FISA_REAUTHORIZED_YES": ["reauthorization signed", "signed into law"],
    "F2_FISA_LAPSED_NO": ["expires", "fails to act", "will not become law"],
    "F3_PARDONS_ISSUED_YES": ["issues pardons", "signed pardons", "pardons for"],
    "F4_PARDONS_NOT_ISSUED_NO": ["no january 6 pardons", "no pardons", "will be issued"],
    "F5_TRUMP_IRAN_DEAL_YES": ["sign nuclear deal", "nuclear agreement"],
    "F6_VANCE_PAKISTAN_VISIT_YES": ["arrives in islamabad", "official pakistan visit", "landed in islamabad"],
    "F7_VANCE_PAKISTAN_CANCELED_NO": ["cancels pakistan trip", "canceled his planned pakistan trip"],
    "F10_REPETITION_DAMPING": ["reauthorization signed", "signed into law"],
}


def _combined_text(fixture: dict[str, Any]) -> str:
    return f"{fixture.get('headline') or ''} {fixture.get('body') or ''}".lower()


def _flatten_signal_defs(signal_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group_index, signal in enumerate(signal_defs):
        for keyword in signal.get("keywords", []):
            rows.append(
                {
                    "group_index": group_index,
                    "keyword": keyword,
                    "direction": signal.get("direction"),
                    "strength": signal.get("strength"),
                }
            )
    return rows


def audit_fixture_keyword_coverage(
    fixture: dict[str, Any],
    signal_defs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    signal_defs = signal_defs or GEOPOLITICAL_SIGNALS
    text = _combined_text(fixture)
    matched: list[dict[str, Any]] = []
    for row in _flatten_signal_defs(signal_defs):
        if str(row["keyword"]).lower() in text:
            matched.append(row)

    fixture_id = str(fixture.get("fixture_id") or "")
    expected_phrases = EXPECTED_PHRASES.get(fixture_id, [])
    covered = [phrase for phrase in expected_phrases if phrase.lower() in text]
    keyword_text = {str(row["keyword"]).lower() for row in matched}
    missing = [phrase for phrase in covered if phrase.lower() not in keyword_text]
    expected_direction = str(fixture.get("expected_direction") or "NEUTRAL").upper()
    directional = expected_direction in {"YES", "NO"}
    expected_keyword_direction = expected_direction.lower() if directional else None
    has_expected_direction_match = any(row.get("direction") == expected_keyword_direction for row in matched)
    coverage_ok = (not directional) or (bool(covered) and has_expected_direction_match and not missing)
    return {
        "fixture_id": fixture.get("fixture_id"),
        "expected_direction": fixture.get("expected_direction"),
        "matched_keywords": [row["keyword"] for row in matched],
        "matched_directions": sorted({str(row.get("direction")) for row in matched}),
        "expected_phrases": expected_phrases,
        "covered_expected_phrases": covered,
        "missing_expected_phrases": missing,
        "coverage_ok": coverage_ok,
    }


def audit_keyword_direction_map(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [audit_fixture_keyword_coverage(fixture) for fixture in fixtures]
    return {
        "audit": "cycle15b_keyword_direction_map",
        "keyword_map": _flatten_signal_defs(GEOPOLITICAL_SIGNALS),
        "fixture_count": len(rows),
        "coverage_gap_count": sum(1 for row in rows if not row["coverage_ok"]),
        "fixtures": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "keyword_audit.json")
    args = parser.parse_args()
    payload = audit_keyword_direction_map(load_fixtures(args.fixtures))
    write_json(args.output, payload)
    print(json.dumps({"output": str(args.output), "coverage_gap_count": payload["coverage_gap_count"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
