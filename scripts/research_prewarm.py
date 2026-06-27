#!/usr/bin/env python3
"""Prewarm durable research dossiers for open Kalshi markets.

This runner is intentionally out-of-band. It may fetch market/research data and
write `research_*` dossier rows, but it never blends signals, sizes positions,
or submits orders.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kalshi.rest_client import KalshiRestClient
from tasks.research_dossier import ResearchDossierStore
from tasks.research_prewarm_task import ResearchPrewarmResult, ResearchPrewarmTask


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ticker",
        action="append",
        default=[],
        help="Market ticker to prewarm. Repeatable. Defaults to open-market scan.",
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=50,
        help="Maximum markets to prewarm per cycle.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="Maximum Kalshi open-market pages to fetch when --ticker is omitted.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Research dossier SQLite path. Defaults to data/evidence_store.db.",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=6,
        help="Maximum research queries per market.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=12.0,
        help="End-to-end research timeout per market.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=None,
        help="Run periodically at this interval. Omit for one run.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable summary JSON.",
    )
    return parser


def _load_markets(args: argparse.Namespace, client: Any) -> list[Any]:
    if args.max_markets <= 0:
        raise ValueError("--max-markets must be positive")
    if args.max_pages <= 0:
        raise ValueError("--max-pages must be positive")

    markets: list[Any] = []
    tickers = [str(ticker).strip() for ticker in args.ticker if str(ticker).strip()]
    if tickers:
        for ticker in tickers:
            market = client.get_market(ticker)
            if market is not None:
                markets.append(market)
            if len(markets) >= args.max_markets:
                break
        return markets

    open_markets = client.get_all_open_markets(max_pages=args.max_pages)
    for market in open_markets:
        if market is None:
            continue
        markets.append(market)
        if len(markets) >= args.max_markets:
            break
    return markets


def summarize_results(results: Iterable[ResearchPrewarmResult]) -> dict[str, Any]:
    result_list = list(results)
    statuses = Counter(result.status for result in result_list)
    return {
        "markets": len(result_list),
        "attempted": sum(1 for result in result_list if result.attempted),
        "statuses": dict(sorted(statuses.items())),
        "evidence": sum(result.evidence_count for result in result_list),
        "queries": sum(result.query_count for result in result_list),
    }


async def run_once(
    args: argparse.Namespace,
    *,
    client: Any | None = None,
    task: ResearchPrewarmTask | None = None,
) -> dict[str, Any]:
    client = client or KalshiRestClient()
    db_path = getattr(args, "db_path", None)
    store = ResearchDossierStore(db_path) if db_path else None
    task = task or ResearchPrewarmTask(
        store=store,
        max_queries=getattr(args, "max_queries", 6),
        research_timeout_seconds=getattr(args, "timeout_seconds", 12.0),
    )
    markets = _load_markets(args, client)
    results = await task.run_once(markets)
    return summarize_results(results)


async def run_periodic(args: argparse.Namespace) -> None:
    if args.interval_seconds is None or args.interval_seconds <= 0:
        raise ValueError("--interval-seconds must be positive")
    while True:
        summary = await run_once(args)
        print(_render_summary(summary, json_output=args.json), flush=True)
        await asyncio.sleep(args.interval_seconds)


def _render_summary(summary: dict[str, Any], *, json_output: bool) -> str:
    if json_output:
        return json.dumps(summary, sort_keys=True)
    statuses = ", ".join(
        f"{status}={count}" for status, count in summary.get("statuses", {}).items()
    )
    return (
        "research_prewarm "
        f"markets={summary.get('markets', 0)} "
        f"attempted={summary.get('attempted', 0)} "
        f"evidence={summary.get('evidence', 0)} "
        f"queries={summary.get('queries', 0)} "
        f"statuses={statuses or '{}'}"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    if args.interval_seconds is not None:
        asyncio.run(run_periodic(args))
        return 0
    summary = asyncio.run(run_once(args))
    print(_render_summary(summary, json_output=args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
