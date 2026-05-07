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


def _price_to_cents(row: dict[str, Any]) -> float | None:
    cents = _as_float(row.get("yes_price") or row.get("yes_price_cents") or row.get("price"))
    if cents is not None:
        return cents
    dollars = _as_float(row.get("yes_price_dollars"))
    if dollars is not None:
        return dollars * 100.0
    return None


def normalize_trade_prices(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_rows = payload.get("trades", []) if isinstance(payload, dict) else payload
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        ts = row.get("created_time") or row.get("trade_time") or row.get("ts")
        price = _price_to_cents(row)
        if ts and price is not None:
            rows.append({"ts": str(ts), "yes_price": round(price, 6)})
    return rows


def merge_price_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ts: dict[str, dict[str, Any]] = {}
    priority = {"live_trades": 0, "historical_trades": 1}
    for row in rows:
        ts = str(row["ts"])
        candidate = {
            "ts": ts,
            "yes_price": float(row["yes_price"]),
            "source": str(row.get("source") or "unknown"),
        }
        existing = by_ts.get(ts)
        if existing is None or priority.get(candidate["source"], 9) < priority.get(existing.get("source"), 9):
            by_ts[ts] = candidate
    return [by_ts[ts] for ts in sorted(by_ts)]


def _fetch_endpoint_prices(client: Any, *, endpoint: str, ticker: str, source: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    try:
        payload = client._request("GET", endpoint, params={"ticker": ticker, "limit": 1000})  # noqa: SLF001
    except Exception as exc:
        return [], {"endpoint": endpoint, "source": source, "ticker": ticker, "error": str(exc)}
    rows = normalize_trade_prices(payload)
    for row in rows:
        row["source"] = source
    return rows, None


def price_rows_for_ticker(client: Any, ticker: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged_inputs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for endpoint, source in (
        ("/markets/trades", "live_trades"),
        ("/historical/trades", "historical_trades"),
    ):
        rows, error = _fetch_endpoint_prices(client, endpoint=endpoint, ticker=ticker, source=source)
        merged_inputs.extend(rows)
        if error is not None:
            errors.append(error)
    return merge_price_rows(merged_inputs), errors


def fetch_trade_prices(client: Any, ticker: str) -> list[dict[str, Any]]:
    rows, errors = price_rows_for_ticker(client, ticker)
    if not rows and errors:
        return [{"error": "; ".join(str(error["error"]) for error in errors), "ticker": ticker}]
    return rows


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
        rows, ticker_errors = price_rows_for_ticker(client, ticker)
        if ticker_errors:
            errors[ticker] = ticker_errors
        if rows:
            out[ticker] = rows
        else:
            errors.setdefault(ticker, []).append({"ticker": ticker, "error": "no price rows returned"})
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
