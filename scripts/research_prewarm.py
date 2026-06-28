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
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kalshi.rest_client import KalshiRestClient
from tasks.research_dossier import ResearchDossierStore
from tasks.research_prewarm_task import ResearchPrewarmResult, ResearchPrewarmTask
from utils.trade_log_reader import iter_trade_records


DEFAULT_TARGET_REASONS = [
    "no_keywords",
    "research_incomplete",
    "research_operational_error",
]
DEFAULT_TARGET_RESEARCH_SKIP_REASONS = [
    "ambiguous_direction",
    "cached_dossier_insufficient",
    "cached_dossier_unvetted",
    "direction_reason_conflict",
    "insufficient_corroboration",
    "missing_estimated_probability",
    "missing_resolution_source",
    "new_market",
    "no_research_hits",
    "probability_direction_conflict",
    "research_timeout",
    "research_provider_error",
    "research_adjudicator_error",
]
MAX_REPAIR_KEYWORD_COUNT = 1


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
                markets.append(market)
            if len(markets) >= args.max_markets:
                break
        if markets:
            return markets

    open_markets = client.get_all_open_markets(max_pages=args.max_pages)
    for market in open_markets:
        if market is None:
            continue
        markets.append(market)
        if len(markets) >= args.max_markets:
            break
    return markets


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
            event_types={
                "ANALYSIS_REJECTED",
                "MATCH_LLM_REVIEW",
                "SIGNAL_ANALYSIS_DETAIL",
            },
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
        if ticker:
            last_index_by_ticker[ticker] = index
    return [
        ticker
        for ticker, _index in sorted(
            last_index_by_ticker.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]


def _record_targets_research_prewarm(
    record: dict[str, Any],
    *,
    reason_set: set[str],
    research_skip_reason_set: set[str],
) -> bool:
    event_type = str(record.get("type") or "").strip()
    if event_type == "ANALYSIS_REJECTED":
        reason = str(record.get("reason") or "").strip()
        research_skip_reason = str(record.get("research_skip_reason") or "").strip()
        matches_reason = not reason_set or reason in reason_set
        matches_research_skip = (
            bool(research_skip_reason_set)
            and research_skip_reason in research_skip_reason_set
        )
        return matches_reason or matches_research_skip
    if event_type == "MATCH_LLM_REVIEW":
        return (
            str(record.get("verdict") or "").strip() == "false_positive_neutral"
            and _keyword_count_targets_research_prewarm(record)
        )
    if event_type == "SIGNAL_ANALYSIS_DETAIL":
        return (
            _keyword_count_targets_research_prewarm(record)
            and str(record.get("pre_llm_gate_reason") or "").strip()
            == "insufficient_semantic_overlap"
        )
    return False


def _keyword_count(record: dict[str, Any]) -> int | None:
    raw_count = record.get("keyword_count")
    if raw_count is not None:
        try:
            return int(raw_count)
        except (TypeError, ValueError):
            return None
    keywords = record.get("keywords")
    if isinstance(keywords, list):
        return len(keywords)
    return None


def _keyword_count_targets_research_prewarm(record: dict[str, Any]) -> bool:
    keyword_count = _keyword_count(record)
    return keyword_count is not None and keyword_count <= MAX_REPAIR_KEYWORD_COUNT


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
