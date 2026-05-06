#!/usr/bin/env python3
"""Probe Kalshi market trade history and emit replay price rows."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_trade_prices(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_rows = payload.get("trades", []) if isinstance(payload, dict) else payload
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        ts = row.get("created_time") or row.get("trade_time") or row.get("ts")
        price = _as_float(row.get("yes_price") or row.get("yes_price_cents") or row.get("price"))
        if ts and price is not None:
            rows.append({"ts": str(ts), "yes_price": price})
    return rows


def fetch_trade_prices(client: Any, ticker: str) -> list[dict[str, Any]]:
    try:
        payload = client._request("GET", f"/markets/{ticker}/trades")  # noqa: SLF001
    except Exception as exc:
        return [{"error": str(exc), "ticker": ticker}]
    return normalize_trade_prices(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", type=Path, required=True, help="Resolved markets JSON.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--sleep-seconds", type=float, default=0.50)
    return parser.parse_args()


def main() -> int:
    from kalshi.rest_client import KalshiRestClient

    args = parse_args()
    markets = json.loads(args.markets.read_text(encoding="utf-8"))[: args.limit]
    client = KalshiRestClient()
    out: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, list[dict[str, Any]]] = {}
    for market in markets:
        ticker = str(market["ticker"])
        rows = fetch_trade_prices(client, ticker)
        if rows and "error" in rows[0]:
            errors[ticker] = rows
        else:
            out[ticker] = rows
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    error_path = args.output.with_suffix(".errors.json")
    error_path.write_text(json.dumps(errors, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"tickers": len(markets), "with_prices": len(out), "errors": len(errors), "output": str(args.output)}, sort_keys=True))
    return 0 if out else 1


if __name__ == "__main__":
    raise SystemExit(main())
