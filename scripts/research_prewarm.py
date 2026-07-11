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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import cfg
from analysis.research_gate import market_has_research_source_path
from kalshi.rest_client import KalshiRestClient
from tasks.research_dossier import ResearchDossierStore
from tasks.research_prewarm_task import (
    ResearchPrewarmResult,
    ResearchPrewarmTask,
    market_has_actionable_price,
)
from utils.research_prewarm_targets import (
    DEFAULT_TARGET_REASONS,
    DEFAULT_TARGET_RESEARCH_SKIP_REASONS,
    RESEARCH_PREWARM_EVENT_TYPES,
    record_targets_kalshi_research_prewarm,
)
from utils.trade_log_reader import iter_trade_records


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
        "--target-from-log",
        type=Path,
        default=None,
        help=(
            "Read recent information-gap rows from a trade-log file/root and "
            "prewarm those tickers before falling back to open-market scan."
        ),
    )
    parser.add_argument(
        "--target-since",
        default=None,
        help="UTC ISO lower bound for --target-from-log records.",
    )
    parser.add_argument(
        "--target-reason",
        action="append",
        default=list(DEFAULT_TARGET_REASONS),
        help=(
            "ANALYSIS_REJECTED reason to target from logs. Repeatable. "
            "Defaults to retryable information-deficiency reasons."
        ),
    )
    parser.add_argument(
        "--target-research-skip-reason",
        action="append",
        default=list(DEFAULT_TARGET_RESEARCH_SKIP_REASONS),
        help=(
            "ANALYSIS_REJECTED research_skip_reason to target from logs. "
            "Repeatable. Defaults to retryable cache/research failures."
        ),
    )
    parser.add_argument(
        "--target-rejection-category",
        action="append",
        default=[],
        help="Optional rejection_category filter for --target-from-log. Repeatable.",
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
    parser.add_argument(
        "--include-results",
        action="store_true",
        help="Include compact per-market results in JSON output.",
    )
    parser.add_argument(
        "--no-trade-log",
        action="store_true",
        help="Do not emit RESEARCH_PREWARM_RESULT rows; useful for temp-DB probes.",
    )
    parser.add_argument(
        "--sourceable-series-fallback",
        action="append",
        default=None,
        help=(
            "Series ticker to scan before broad open-market fallback. "
            "Repeatable. Defaults to RESEARCH_PREWARM_SOURCEABLE_SERIES_FALLBACK."
        ),
    )
    return parser


def _load_markets(
    args: argparse.Namespace,
    client: Any,
    *,
    due_store: ResearchDossierStore | None = None,
) -> list[Any]:
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
                markets.append(_enrich_market_source_path(market, client))
            else:
                markets.append(_closed_market_placeholder(ticker))
            if len(markets) >= args.max_markets:
                break
        return markets

    if due_store is not None:
        due_limit = max(args.max_markets, args.max_markets * 5)
        for ticker in due_store.get_due_research_task_tickers(
            limit=due_limit,
            target_cooldown_seconds=_target_cooldown_seconds(),
        ):
            market = client.get_market(ticker)
            if market is None:
                continue
            else:
                market = _enrich_market_source_path(market, client)
            status = str(getattr(market, "status", "open") or "open").lower()
            if status not in {"open", "active"}:
                continue
            markets.append(market)
            if len(markets) >= args.max_markets:
                break
        if len(markets) >= args.max_markets:
            return markets

    target_from_log = getattr(args, "target_from_log", None)
    if target_from_log:
        for ticker in _target_tickers_from_trade_log(
            target_from_log,
            since=_parse_optional_ts(getattr(args, "target_since", None)),
            reasons=getattr(args, "target_reason", None),
            research_skip_reasons=getattr(args, "target_research_skip_reason", None),
            rejection_categories=getattr(args, "target_rejection_category", None),
        ):
            market = client.get_market(ticker)
            if market is not None:
                market = _enrich_market_source_path(market, client)
                if market_has_actionable_price(market):
                    markets.append(market)
            if len(markets) >= args.max_markets:
                break
        if markets:
            return markets

    fallback_markets = _sourceable_series_fallback(
        args,
        client,
        selected_tickers={str(getattr(market, "ticker", "") or "") for market in markets},
    )
    if fallback_markets:
        return fallback_markets[: args.max_markets]

    series_cache: dict[str, Any] = {}
    open_markets = sorted(
        (
            _enrich_market_source_path(market, client, series_cache=series_cache)
            for market in client.get_all_open_markets(max_pages=args.max_pages)
            if market is not None
        ),
        key=_open_market_priority,
    )
    for market in open_markets:
        markets.append(market)
        if len(markets) >= args.max_markets:
            break
    return markets


def _closed_market_placeholder(ticker: str) -> SimpleNamespace:
    return SimpleNamespace(
        ticker=ticker,
        title=ticker,
        status="closed",
    )


def _target_cooldown_seconds() -> float:
    try:
        return float(getattr(cfg, "research_prewarm_target_cooldown_seconds", 1800.0))
    except (TypeError, ValueError):
        return 1800.0


def _should_discard_trade_log(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "no_trade_log", False))


def _sourceable_series_fallback(
    args: argparse.Namespace,
    client: Any,
    *,
    selected_tickers: set[str] | None = None,
) -> list[Any]:
    if not hasattr(client, "get_markets"):
        return []
    selected_tickers = selected_tickers or set()
    out: list[Any] = []
    series_values = getattr(args, "sourceable_series_fallback", None)
    if series_values is None:
        series_values = getattr(cfg, "research_prewarm_sourceable_series_fallback", ())
    for raw_series in series_values or ():
        if len(out) >= args.max_markets:
            break
        series_ticker = str(raw_series or "").strip()
        if not series_ticker or _is_multi_venue_synthetic_ticker(series_ticker):
            continue
        try:
            page, _cursor = client.get_markets(
                series_ticker=series_ticker,
                limit=args.max_markets,
            )
        except Exception:
            continue
        for market in page or ():
            if len(out) >= args.max_markets:
                break
            ticker = str(getattr(market, "ticker", "") or "").strip()
            if not ticker or ticker in selected_tickers:
                continue
            market = _enrich_market_source_path(market, client)
            status = str(getattr(market, "status", "open") or "open").lower()
            if status not in {"open", "active"}:
                continue
            if not market_has_actionable_price(market):
                continue
            if not market_has_research_source_path(market):
                continue
            out.append(market)
            selected_tickers.add(ticker)
    return out


def _enrich_market_source_path(
    market: Any,
    client: Any,
    *,
    series_cache: dict[str, Any] | None = None,
) -> Any:
    if tuple(getattr(market, "settlement_sources", ()) or ()):
        return market
    series_ticker = str(getattr(market, "series_ticker", "") or "").strip()
    if not series_ticker:
        ticker = str(getattr(market, "ticker", "") or "").strip()
        series_ticker = ticker.split("-", 1)[0] if ticker else ""
    if not series_ticker or not hasattr(client, "get_series"):
        return market
    try:
        if series_cache is not None and series_ticker in series_cache:
            metadata = series_cache[series_ticker]
        else:
            metadata = client.get_series(series_ticker)
            if series_cache is not None:
                series_cache[series_ticker] = metadata
    except Exception:
        return market
    if metadata is None:
        return market
    for attr in (
        "rules_primary",
        "rules_secondary",
        "contract_terms_url",
        "settlement_sources",
    ):
        current = getattr(market, attr, None)
        if current:
            continue
        value = getattr(metadata, attr, None)
        if value:
            setattr(market, attr, value)
    return market


def _open_market_priority(market: Any) -> tuple[int, int, int, str]:
    return (
        0 if market_has_actionable_price(market) else 1,
        0 if market_has_research_source_path(market) else 1,
        1 if _is_multi_venue_synthetic_market(market) else 0,
        str(getattr(market, "ticker", "") or ""),
    )


def _is_multi_venue_synthetic_market(market: Any) -> bool:
    ticker = str(getattr(market, "ticker", "") or "")
    return _is_multi_venue_synthetic_ticker(ticker)


def _is_multi_venue_synthetic_ticker(ticker: str) -> bool:
    ticker = str(ticker or "").upper()
    return ticker.startswith(("KXMVECROSSCATEGORY", "KXMVESPORTSMULTIGAMEEXTENDED"))


def _parse_optional_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _target_tickers_from_trade_log(
    path: Path,
    *,
    since: datetime | None,
    reasons: list[str] | None,
    rejection_categories: list[str] | None,
    research_skip_reasons: list[str] | None = None,
) -> list[str]:
    reason_set = {
        str(reason).strip()
        for reason in (reasons or [])
        if str(reason).strip()
    }
    category_set = {
        str(category).strip()
        for category in (rejection_categories or [])
        if str(category).strip()
    }
    research_skip_reason_set = {
        str(reason).strip()
        for reason in (research_skip_reasons or [])
        if str(reason).strip()
    }
    last_index_by_ticker: dict[str, int] = {}
    for index, record in enumerate(
        iter_trade_records(
            Path(path),
            since=since,
            event_types=RESEARCH_PREWARM_EVENT_TYPES,
        )
    ):
        if not _record_targets_research_prewarm(
            record,
            reason_set=reason_set,
            research_skip_reason_set=research_skip_reason_set,
        ):
            continue
        category = str(record.get("rejection_category") or "").strip()
        event_type = str(record.get("type") or "").strip()
        if event_type == "ANALYSIS_REJECTED" and category_set and category not in category_set:
            continue
        ticker = str(record.get("ticker") or record.get("market_ticker") or "").strip()
        if _is_kalshi_ticker(ticker):
            last_index_by_ticker[ticker] = index
    return [
        ticker
        for ticker, _index in sorted(
            last_index_by_ticker.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]


def _is_kalshi_ticker(ticker: str) -> bool:
    text = str(ticker or "").strip()
    return text.startswith("KX")


def _record_targets_research_prewarm(
    record: dict[str, Any],
    *,
    reason_set: set[str],
    research_skip_reason_set: set[str],
) -> bool:
    return record_targets_kalshi_research_prewarm(
        record,
        reason_set=reason_set,
        research_skip_reason_set=research_skip_reason_set,
    )


def summarize_results(
    results: Iterable[ResearchPrewarmResult],
    *,
    include_results: bool = False,
) -> dict[str, Any]:
    result_list = list(results)
    statuses = Counter(result.status for result in result_list)
    summary: dict[str, Any] = {
        "markets": len(result_list),
        "attempted": sum(1 for result in result_list if result.attempted),
        "statuses": dict(sorted(statuses.items())),
        "evidence": sum(result.evidence_count for result in result_list),
        "queries": sum(result.query_count for result in result_list),
    }
    if include_results:
        summary["results"] = [
            {
                "market_ticker": result.market_ticker,
                "status": result.status,
                "attempted": result.attempted,
                "skip_reason": result.skip_reason,
                "query_count": result.query_count,
                "evidence_count": result.evidence_count,
                "research_run_id": result.research_run_id,
            }
            for result in result_list
        ]
    return summary


async def run_once(
    args: argparse.Namespace,
    *,
    client: Any | None = None,
    task: ResearchPrewarmTask | None = None,
) -> dict[str, Any]:
    client = client or KalshiRestClient()
    db_path = getattr(args, "db_path", None)
    store = ResearchDossierStore(db_path) if db_path else getattr(task, "store", None)
    if store is None and task is None:
        store = ResearchDossierStore()
    explicit_tickers = any(
        str(ticker).strip() for ticker in getattr(args, "ticker", ()) or ()
    )
    task = task or ResearchPrewarmTask(
        store=store,
        max_queries=getattr(args, "max_queries", 6),
        research_timeout_seconds=getattr(args, "timeout_seconds", 12.0),
        target_cooldown_seconds=0.0
        if explicit_tickers
        else _target_cooldown_seconds(),
        result_sink=_discard_result if _should_discard_trade_log(args) else None,
    )
    markets = _load_markets(args, client, due_store=store)
    results = await task.run_once(markets)
    return summarize_results(
        results,
        include_results=bool(getattr(args, "include_results", False)),
    )


async def _discard_result(_result: ResearchPrewarmResult) -> None:
    return None


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
