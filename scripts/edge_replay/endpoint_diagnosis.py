#!/usr/bin/env python3
"""Cycle-16D D1 endpoint diagnosis for Kalshi trade-price reconstruction."""

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


DEFAULT_MARKETS = Path("logs/edge_replay/cycle13_live/resolved_markets_full.json")
DEFAULT_OUTPUT = Path("logs/edge_replay/cycle16d/endpoint_diagnosis.json")

DOCS_REFERENCE = {
    "markets_api": {
        "url": "https://docs.kalshi.com/python-sdk/api/MarketsApi",
        "observed": [
            "get_trades documents GET /markets/trades",
            "get_trades accepts ticker, min_ts, max_ts query parameters",
            "get_market_candlesticks documents GET /series/{series}/markets/{market_ticker}/candlesticks",
        ],
    },
    "historical_data": {
        "url": "https://docs.kalshi.com/getting_started/historical_data",
        "observed": [
            "live GET /markets/trades is cutoff by trades_created_ts",
            "older trades route to GET /historical/trades",
            "older market candlesticks route to GET /historical/markets/{ticker}/candlesticks",
        ],
    },
}


def _load_resolved_tickers(path: Path) -> list[str]:
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [str(row["ticker"]) for row in rows if row.get("ticker")]


def select_probe_tickers(
    resolved_tickers: list[str],
    open_tickers: list[str],
    *,
    limit: int = 6,
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    for ticker in resolved_tickers[: max(3, limit // 2)]:
        selected.append({"ticker": ticker, "source": "resolved"})
    for ticker in open_tickers:
        if len(selected) >= limit:
            break
        if ticker not in {row["ticker"] for row in selected}:
            selected.append({"ticker": ticker, "source": "open"})
    for ticker in resolved_tickers:
        if len(selected) >= limit:
            break
        if ticker not in {row["ticker"] for row in selected}:
            selected.append({"ticker": ticker, "source": "resolved"})
    return selected[:limit]


def _safe_headers(headers: Any) -> dict[str, str]:
    allow = {
        "content-type",
        "date",
        "server",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "cf-cache-status",
        "cache-control",
    }
    return {key: value for key, value in dict(headers).items() if key.lower() in allow}


def _sanitize_body(text: str, limit: int = 500) -> str:
    snippet = (text or "")[:limit]
    lowered = snippet.lower()
    sensitive = ("kalshi-access-key", "kalshi-access-signature", "authorization", "private_key", "api_key")
    if any(item in lowered for item in sensitive):
        return "(redacted: response body may contain credentials)"
    return snippet


def _usable_trade_shape(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("trades"), list):
        return True
    if isinstance(payload.get("cursor"), str) and "trades" in payload:
        return True
    return False


def _request_probe(client: Any, *, variant: str, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    path = "/trade-api/v2" + endpoint
    url = client._base + endpoint  # noqa: SLF001
    headers = client._headers("GET", path, "")  # noqa: SLF001
    response = client._session.request("GET", url, headers=headers, params=params, timeout=10)  # noqa: SLF001
    body_text = response.text or ""
    payload = None
    if body_text.strip():
        try:
            payload = response.json()
        except ValueError:
            payload = None
    return {
        "variant": variant,
        "endpoint": endpoint,
        "params": params or {},
        "url_path": response.request.path_url,
        "status_code": response.status_code,
        "headers": _safe_headers(response.headers),
        "body_snippet": _sanitize_body(body_text),
        "usable_shape": _usable_trade_shape(payload),
        "row_count": len(payload.get("trades", [])) if isinstance(payload, dict) and isinstance(payload.get("trades"), list) else None,
    }


def _get_open_tickers(client: Any, count: int = 3) -> list[str]:
    try:
        payload = client._request("GET", "/markets", params={"status": "open", "limit": count})  # noqa: SLF001
    except Exception:
        return []
    return [str(row["ticker"]) for row in payload.get("markets", []) if row.get("ticker")][:count]


def classify_endpoint_state(probes: list[dict[str, Any]]) -> dict[str, Any]:
    if any(row.get("variant") == "documented_live_trades" and row.get("status_code") == 200 and row.get("usable_shape") for row in probes):
        return {
            "classification": "solvable_auth_or_param",
            "rationale": "legacy per-ticker path is not the documented shape; documented_live_trades returned usable /markets/trades?ticker=... shape.",
        }
    if any(row.get("variant") == "documented_historical_trades" and row.get("status_code") == 200 and row.get("usable_shape") for row in probes):
        return {
            "classification": "solvable_alternate_endpoint",
            "rationale": "historical trades endpoint returned usable shape; route older trade fills through /historical/trades.",
        }
    if any(row.get("status_code") in {401, 403} for row in probes):
        return {
            "classification": "solvable_auth_or_param",
            "rationale": "one or more probes returned auth/permission status; endpoint state cannot be called dead until auth/tier is resolved.",
        }
    tickers = {str(row.get("ticker")) for row in probes if row.get("ticker")}
    trade_probes = [row for row in probes if row.get("variant") in {"legacy_per_ticker_trades", "documented_live_trades", "documented_historical_trades"}]
    if len(tickers) >= 5 and trade_probes and all(row.get("status_code") in {404, 410} for row in trade_probes):
        return {
            "classification": "permanently_dead",
            "rationale": "all trade endpoint variants returned 404/410 across five or more ticker probes.",
        }
    return {
        "classification": "ambiguous_operator_pick",
        "rationale": "probe statuses did not match a locked classification cleanly.",
    }


def run_endpoint_diagnosis(markets_path: Path, output: Path, *, limit: int = 6, sleep_seconds: float = 0.2) -> dict[str, Any]:
    from kalshi.rest_client import KalshiRestClient

    client = KalshiRestClient()
    resolved = _load_resolved_tickers(markets_path)
    open_tickers = _get_open_tickers(client, count=max(2, limit // 2))
    tickers = select_probe_tickers(resolved, open_tickers, limit=limit)
    probes: list[dict[str, Any]] = []
    for item in tickers:
        ticker = item["ticker"]
        variants = [
            ("legacy_per_ticker_trades", f"/markets/{ticker}/trades", None),
            ("documented_live_trades", "/markets/trades", {"ticker": ticker, "limit": 1000}),
            ("documented_historical_trades", "/historical/trades", {"ticker": ticker, "limit": 1000}),
        ]
        for variant, endpoint, params in variants:
            try:
                probe = _request_probe(client, variant=variant, endpoint=endpoint, params=params)
            except Exception as exc:  # network/auth exceptions still belong in D1 evidence
                probe = {
                    "variant": variant,
                    "endpoint": endpoint,
                    "params": params or {},
                    "status_code": None,
                    "headers": {},
                    "body_snippet": "",
                    "usable_shape": False,
                    "error": str(exc),
                }
            probe.update({"ticker": ticker, "ticker_source": item["source"]})
            probes.append(probe)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    classification = classify_endpoint_state(probes)
    payload = {
        "audit": "cycle16d_endpoint_diagnosis",
        "probe_count": len(probes),
        "ticker_count": len(tickers),
        "tickers": tickers,
        "docs_reference": DOCS_REFERENCE,
        "classification": classification["classification"],
        "classification_rationale": classification["rationale"],
        "probes": probes,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", type=Path, default=DEFAULT_MARKETS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    args = parser.parse_args()
    payload = run_endpoint_diagnosis(args.markets, args.output, limit=args.limit, sleep_seconds=args.sleep_seconds)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "classification": payload["classification"],
                "ticker_count": payload["ticker_count"],
                "probe_count": payload["probe_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
