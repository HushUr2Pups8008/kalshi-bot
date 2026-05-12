#!/usr/bin/env python3
"""Shared helpers for Cycle-15B extraction diagnostics."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from feeds import NewsItem
from kalshi import KalshiMarket


FIXTURES_PATH = Path("tests/fixtures/cycle14_synthetic_evidence.json")
OUTPUT_DIR = Path("logs/edge_replay/cycle15b")
BASE_PROBABILITY = 0.50


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_fixtures(path: Path = FIXTURES_PATH) -> list[dict[str, Any]]:
    return load_json(path)


def fixture_news(fixture: dict[str, Any]) -> NewsItem:
    return NewsItem(
        headline=str(fixture.get("headline") or ""),
        body=str(fixture.get("body") or ""),
        source=str(fixture.get("source") or "synthetic"),
        url=str(fixture.get("url") or "synthetic://cycle15b"),
    )


def fixture_market(fixture: dict[str, Any]) -> KalshiMarket:
    ticker = str(fixture.get("market_ticker") or "KXSYNTH")
    return KalshiMarket(
        ticker=ticker,
        title=str(fixture.get("market_title") or fixture.get("headline") or "Synthetic market"),
        yes_bid=50,
        yes_ask=50,
        yes_price=50,
        volume=1,
        open_interest=1,
        close_time="2026-05-01T00:00:00Z",
        status="open",
        series_ticker=ticker.split("-", 1)[0],
        # P-5 CR-C: post-P0 fields required for guarded legacy reads.
        yes_bid_cents=50,
        yes_ask_cents=50,
        no_bid_cents=50,
        no_ask_cents=50,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
    )


def expected_is_directional(fixture: dict[str, Any]) -> bool:
    return str(fixture.get("expected_direction") or "NEUTRAL").upper() in {"YES", "NO"}
