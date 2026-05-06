#!/usr/bin/env python3
"""Fetch or normalize resolved Kalshi markets for edge replay."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


RESOLVED_STATUSES = {"settled", "finalized", "closed"}
RESOLVED_RESULTS = {"yes", "no"}


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_market(raw: dict[str, Any]) -> dict[str, Any] | None:
    status = str(raw.get("status") or "").lower()
    result = str(raw.get("result") or raw.get("outcome") or "").lower()
    ticker = str(raw.get("ticker") or "").strip()
    if not ticker or status not in RESOLVED_STATUSES or result not in RESOLVED_RESULTS:
        return None
    return {
        "ticker": ticker,
        "title": raw.get("title") or raw.get("subtitle") or ticker,
        "series_ticker": raw.get("series_ticker") or ticker.split("-")[0],
        "status": status,
        "resolved_yes": result == "yes",
        "result": result,
        "yes_price": _as_float(raw.get("yes_price") or raw.get("last_price")),
        "close_time": raw.get("close_time") or raw.get("close_ts") or raw.get("expiration_time"),
    }


def normalize_markets(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = payload.get("markets", []) if isinstance(payload, dict) else payload
    normalized = [normalize_market(row) for row in rows]
    return [row for row in normalized if row is not None]


def load_markets_from_json(path: Path) -> list[dict[str, Any]]:
    return normalize_markets(json.loads(path.read_text(encoding="utf-8")))


def load_markets_from_paper_trades_db(path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(paper_trades)")}
        required = {"ticker", "resolved", "resolved_yes"}
        if not required.issubset(columns):
            return []
        order_by = "ts ASC" if "ts" in columns else "ticker ASC"
        rows = conn.execute(f"SELECT * FROM paper_trades WHERE resolved = 1 ORDER BY {order_by}").fetchall()
    finally:
        conn.close()

    markets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        trade = dict(row)
        ticker = str(trade.get("ticker") or "")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        resolved_yes = bool(trade.get("resolved_yes"))
        markets.append(
            {
                "ticker": ticker,
                "title": trade.get("market_title") or ticker,
                "series_ticker": trade.get("series_ticker") or ticker.split("-")[0],
                "status": "settled",
                "resolved_yes": resolved_yes,
                "result": "yes" if resolved_yes else "no",
                "yes_price": _as_float(trade.get("market_yes_price") or trade.get("price_cents")),
                "close_time": trade.get("resolved_ts"),
            }
        )
    return markets


def fetch_markets_from_api(*, limit: int = 200, status: str = "settled") -> list[dict[str, Any]]:
    from kalshi.rest_client import KalshiRestClient

    client = KalshiRestClient()
    cursor: str | None = None
    rows: list[dict[str, Any]] = []
    while len(rows) < limit:
        page, cursor = client.get_markets(status=status, limit=min(200, limit - len(rows)), cursor=cursor)
        for market in page:
            rows.append(
                {
                    "ticker": market.ticker,
                    "title": market.title,
                    "series_ticker": market.series_ticker,
                    "status": market.status,
                    "result": getattr(market, "result", None),
                    "yes_price": market.yes_price,
                    "close_time": getattr(market, "close_time", None),
                }
            )
        if not cursor:
            break
    return normalize_markets(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-json", type=Path, help="Normalize a saved Kalshi markets JSON payload.")
    parser.add_argument("--from-paper-trades-db", type=Path, help="Seed resolved markets from local resolved paper trades.")
    parser.add_argument("--output", type=Path, help="Write normalized resolved markets JSON.")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--status", default="settled")
    parser.add_argument("--json", action="store_true", help="Print normalized JSON to stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.from_json:
        markets = load_markets_from_json(args.from_json)
    elif args.from_paper_trades_db:
        markets = load_markets_from_paper_trades_db(args.from_paper_trades_db)
    else:
        markets = fetch_markets_from_api(limit=args.limit, status=args.status)
    text = json.dumps(markets, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    if args.json or not args.output:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
