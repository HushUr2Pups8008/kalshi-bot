#!/usr/bin/env python3
"""Build the Cycle-17D broader-fetch replay corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import MARKET_SERIES_BLOCKLIST_PREFIXES
from scripts.edge_replay.build_replay_dataset import build_replay_dataset, expand_trade_logs
from scripts.edge_replay.fetch_historical_prices import price_rows_for_ticker
from scripts.edge_replay.fetch_resolved_markets import (
    fetch_live_kalshi_markets,
    fetch_live_kalshi_markets_by_ticker,
    load_evidence_store_tickers,
    normalize_markets,
)


DEFAULT_OUT_DIR = REPO_ROOT / "logs/edge_replay/cycle17d-broader"
DEFAULT_OUTPUT = DEFAULT_OUT_DIR / "replay_dataset_broader.jsonl"
DEFAULT_MANIFEST = DEFAULT_OUT_DIR / "build_manifest.json"
DEFAULT_MARKETS = DEFAULT_OUT_DIR / "resolved_markets.json"
DEFAULT_PRICES = DEFAULT_OUT_DIR / "historical_prices.json"
DEFAULT_PRICE_ERRORS = DEFAULT_OUT_DIR / "historical_price_errors.json"

DEFAULT_START = "2026-01-01T00:00:00Z"
DEFAULT_END = "2026-05-10T00:00:00Z"
DEFAULT_COHORT = "POST_FIX_REBUILT"
DEFAULT_STATUSES = ("settled",)
# F-11 P1-C: canonical output key is entry_price_cents (post-P1-A).
# The legacy market_yes_price key is accepted on read via _entry_price_cents()
# in downstream consumers; new output rows use entry_price_cents.
REQUIRED_OUTPUT_FIELDS = (
    "ticker",
    "decision_ts",
    "signal_source",
    "market_family",
    "signal_type",
    "news_class",
    "cohort",
    "headline",
    "entry_price_cents",
    "confidence",
    "edge",
    "model_prob",
    "resolved_yes",
)


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _unix_ts(value: str) -> int:
    parsed = _parse_dt(value)
    if parsed is None:
        raise ValueError(f"invalid timestamp: {value}")
    return int(parsed.timestamp())


def _in_window(market: dict[str, Any], *, start: datetime, end: datetime) -> bool:
    close_time = _parse_dt(market.get("close_time"))
    return close_time is not None and start <= close_time <= end


def _is_sports_blocked(market: dict[str, Any]) -> bool:
    candidates = {
        str(market.get("ticker") or "").upper(),
        str(market.get("series_ticker") or "").upper(),
    }
    return any(candidate.startswith(prefix) for candidate in candidates for prefix in MARKET_SERIES_BLOCKLIST_PREFIXES)


def filter_markets(
    markets: list[dict[str, Any]],
    *,
    evidence_tickers: set[str],
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)
    if start_dt is None or end_dt is None:
        raise ValueError("start and end must be ISO timestamps")

    filtered: list[dict[str, Any]] = []
    counts = Counter({"pre_filter": len(markets)})
    for market in markets:
        ticker = str(market.get("ticker") or "")
        if ticker not in evidence_tickers:
            counts["excluded_not_in_evidence_store"] += 1
            continue
        if not _in_window(market, start=start_dt, end=end_dt):
            counts["excluded_outside_close_time_window"] += 1
            continue
        if _is_sports_blocked(market):
            counts["excluded_sports_prefix_blocklist"] += 1
            continue
        filtered.append(market)
    counts["post_market_filters"] = len(filtered)
    return filtered, dict(sorted(counts.items()))


def fetch_historical_price_coverage(
    *,
    client: Any,
    markets: list[dict[str, Any]],
    sleep_seconds: float,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    prices: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, list[dict[str, Any]]] = {}
    for market in markets:
        ticker = str(market["ticker"])
        rows, ticker_errors = price_rows_for_ticker(client, ticker)
        if ticker_errors:
            errors[ticker] = ticker_errors
        if rows:
            prices[ticker] = rows
        else:
            errors.setdefault(ticker, []).append({"ticker": ticker, "error": "no price rows returned"})
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return prices, errors


def merge_markets(primary: list[dict[str, Any]], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ticker = {str(market["ticker"]): market for market in primary}
    for market in fallback:
        by_ticker.setdefault(str(market["ticker"]), market)
    return [by_ticker[ticker] for ticker in sorted(by_ticker)]


def _filter_price_coverage(
    markets: list[dict[str, Any]],
    prices: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    with_prices = [market for market in markets if prices.get(str(market.get("ticker") or ""))]
    return with_prices, {
        "pre_price_coverage": len(markets),
        "excluded_without_historical_price_coverage": len(markets) - len(with_prices),
        "post_price_coverage": len(with_prices),
    }


def _market_lookup(markets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(market["ticker"]): market for market in markets}


def _market_family_for(row: dict[str, Any], markets: dict[str, dict[str, Any]]) -> str:
    market = markets.get(str(row.get("ticker") or ""), {})
    return str(
        row.get("market_family")
        or market.get("series_ticker")
        or row.get("series_ticker")
        or row.get("ticker")
        or "unknown"
    )


def _prepare_rows(rows: list[dict[str, Any]], markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup = _market_lookup(markets)
    out: list[dict[str, Any]] = []
    for row in rows:
        prepared = dict(row)
        prepared["cohort"] = DEFAULT_COHORT
        prepared["market_family"] = _market_family_for(prepared, lookup)
        for field in REQUIRED_OUTPUT_FIELDS:
            prepared.setdefault(field, None)
        out.append(prepared)
    return sorted(out, key=lambda row: (row.get("decision_ts") or "", row.get("ticker") or ""))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    output_bytes = b"".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for row in rows
    )
    path.write_bytes(output_bytes)
    return output_bytes


def _git_rev(args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_head() -> str | None:
    return _git_rev(["rev-parse", "HEAD"])


def _blocklist_commit() -> str | None:
    return _git_rev(["log", "-1", "--format=%H", "--", "config.py"])


def _field_completeness(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    total = len(rows)
    out: dict[str, dict[str, float | int]] = {}
    for field in REQUIRED_OUTPUT_FIELDS:
        populated = sum(1 for row in rows if row.get(field) is not None and row.get(field) != "")
        out[field] = {
            "populated": populated,
            "missing": total - populated,
            "fraction": round((populated / total), 4) if total else 0.0,
        }
    return out


def build_cycle17d_broader_corpus(
    *,
    markets: list[dict[str, Any]],
    historical_prices: dict[str, list[dict[str, Any]]],
    evidence_tickers: set[str],
    paper_trades_db: Path | None,
    evidence_store_db: Path | None,
    trade_logs: list[Path],
    output: Path = DEFAULT_OUTPUT,
    manifest_path: Path = DEFAULT_MANIFEST,
    markets_output: Path = DEFAULT_MARKETS,
    prices_output: Path = DEFAULT_PRICES,
    price_errors_output: Path = DEFAULT_PRICE_ERRORS,
    price_errors: dict[str, list[dict[str, Any]]] | None = None,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    statuses: tuple[str, ...] = DEFAULT_STATUSES,
    fetch_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market_filtered, market_filter_counts = filter_markets(
        markets,
        evidence_tickers=evidence_tickers,
        start=start,
        end=end,
    )
    price_filtered, price_filter_counts = _filter_price_coverage(market_filtered, historical_prices)
    allowed_prices = {str(market["ticker"]): historical_prices[str(market["ticker"])] for market in price_filtered}

    _write_json(markets_output, price_filtered)
    _write_json(prices_output, allowed_prices)
    _write_json(price_errors_output, price_errors or {})

    raw_rows = build_replay_dataset(
        markets_path=markets_output,
        paper_trades_db=paper_trades_db,
        trade_logs=trade_logs,
        evidence_store_db=evidence_store_db,
        historical_prices_path=prices_output,
    )
    output_rows = _prepare_rows(raw_rows, price_filtered)
    output_bytes = _write_jsonl(output, output_rows)

    manifest = {
        "schema_version": 1,
        "corpus_id": "cycle17d_broader_api_fetch",
        "cohort": DEFAULT_COHORT,
        "time_window": {"start": start, "end": end, "filter": "close_time IN [START, END]"},
        "statuses": list(statuses),
        "input_paths": {
            "paper_trades_db": _display_path(paper_trades_db) if paper_trades_db else None,
            "evidence_store_db": _display_path(evidence_store_db) if evidence_store_db else None,
            "trade_logs": [_display_path(path) for path in trade_logs],
        },
        "output_paths": {
            "dataset": _display_path(output),
            "manifest": _display_path(manifest_path),
            "resolved_markets": _display_path(markets_output),
            "historical_prices": _display_path(prices_output),
            "historical_price_errors": _display_path(price_errors_output),
        },
        "blocklist": {
            "source": "config.MARKET_SERIES_BLOCKLIST_PREFIXES",
            "current_head": _git_head(),
            "source_commit": _blocklist_commit(),
            "prefix_count": len(MARKET_SERIES_BLOCKLIST_PREFIXES),
        },
        "filter_counts": {
            **market_filter_counts,
            **price_filter_counts,
            "post_dataset_build_rows": len(output_rows),
        },
        "raw_market_status_counts": dict(sorted(Counter(str(market.get("status") or "unknown") for market in markets).items())),
        "row_count": len(output_rows),
        "market_count": len(price_filtered),
        "cohort_counts": dict(sorted(Counter(row["cohort"] for row in output_rows).items())),
        "required_field_completeness": _field_completeness(output_rows),
        "scorer_schema_compatibility": {
            "status": "ready-for-audit",
            "audit_script": "scripts/edge_replay/cycle17d_schema_audit.py",
        },
        "fetch_metadata": fetch_metadata or {},
        "sha256": hashlib.sha256(output_bytes).hexdigest(),
    }
    _write_json(manifest_path, manifest)
    return manifest


def load_markets(path: Path) -> list[dict[str, Any]]:
    return normalize_markets(json.loads(path.read_text(encoding="utf-8")))


def load_historical_prices(path: Path) -> dict[str, list[dict[str, Any]]]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets-json", type=Path, help="Use saved Kalshi markets JSON instead of live API fetch.")
    parser.add_argument("--historical-prices-json", type=Path, help="Use saved historical prices JSON instead of live API fetch.")
    parser.add_argument("--evidence-store-db", type=Path, default=Path("data/evidence_store.db"))
    parser.add_argument("--paper-trades-db", type=Path, default=Path("data/paper_trades.db"))
    parser.add_argument("--trade-log", action="append", default=["logs/trades/**/*.jsonl"], help="JSONL path/glob; repeatable.")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--status", action="append", default=list(DEFAULT_STATUSES))
    parser.add_argument("--fallback-get-market", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--page-limit", type=int, default=200)
    parser.add_argument("--max-pages-per-status", type=int, default=100)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--markets-output", type=Path, default=DEFAULT_MARKETS)
    parser.add_argument("--historical-prices-output", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--historical-price-errors-output", type=Path, default=DEFAULT_PRICE_ERRORS)
    parser.add_argument("--json", action="store_true", help="Print the full build manifest to stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    evidence_tickers = load_evidence_store_tickers(args.evidence_store_db)
    statuses = tuple(args.status)
    fetch_metadata = {
        "mode": "saved-json" if args.markets_json else "live-kalshi",
        "page_limit": args.page_limit,
        "max_pages_per_status": args.max_pages_per_status,
    }

    if args.markets_json:
        markets = load_markets(args.markets_json)
    else:
        from kalshi.rest_client import KalshiRestClient

        markets = fetch_live_kalshi_markets(
            client=KalshiRestClient(),
            statuses=list(statuses),
            evidence_tickers=None,
            page_limit=args.page_limit,
            max_pages_per_status=args.max_pages_per_status,
            sleep_seconds=args.sleep_seconds,
            min_close_ts=_unix_ts(args.start),
            max_close_ts=_unix_ts(args.end),
        )
        if args.fallback_get_market:
            market_filtered, _ = filter_markets(
                markets,
                evidence_tickers=evidence_tickers,
                start=args.start,
                end=args.end,
            )
            missing_tickers = evidence_tickers - {str(market.get("ticker") or "") for market in market_filtered}
            if missing_tickers:
                fallback_markets = fetch_live_kalshi_markets_by_ticker(
                    client=KalshiRestClient(),
                    tickers=missing_tickers,
                    sleep_seconds=args.sleep_seconds,
                )
                markets = merge_markets(markets, fallback_markets)

    if args.historical_prices_json:
        historical_prices = load_historical_prices(args.historical_prices_json)
        price_errors: dict[str, list[dict[str, Any]]] = {}
    else:
        from kalshi.rest_client import KalshiRestClient

        market_filtered, _ = filter_markets(
            markets,
            evidence_tickers=evidence_tickers,
            start=args.start,
            end=args.end,
        )
        historical_prices, price_errors = fetch_historical_price_coverage(
            client=KalshiRestClient(),
            markets=market_filtered,
            sleep_seconds=args.sleep_seconds,
        )

    manifest = build_cycle17d_broader_corpus(
        markets=markets,
        historical_prices=historical_prices,
        evidence_tickers=evidence_tickers,
        paper_trades_db=args.paper_trades_db,
        evidence_store_db=args.evidence_store_db,
        trade_logs=expand_trade_logs(args.trade_log),
        output=args.output,
        manifest_path=args.manifest,
        markets_output=args.markets_output,
        prices_output=args.historical_prices_output,
        price_errors_output=args.historical_price_errors_output,
        price_errors=price_errors,
        start=args.start,
        end=args.end,
        statuses=statuses,
        fetch_metadata=fetch_metadata,
    )

    if args.json:
        print(json.dumps(manifest, sort_keys=True))
    else:
        print(
            json.dumps(
                {
                    "row_count": manifest["row_count"],
                    "market_count": manifest["market_count"],
                    "output_path": manifest["output_paths"]["dataset"],
                    "sha256": manifest["sha256"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
