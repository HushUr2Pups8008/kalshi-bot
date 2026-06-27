from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest

from scripts import research_prewarm
from tests._helpers import write_jsonl
from tasks.research_prewarm_task import ResearchPrewarmResult


class FakeClient:
    def __init__(self) -> None:
        self.market_calls: list[str] = []
        self.open_page_calls: list[int] = []

    def get_market(self, ticker: str):
        self.market_calls.append(ticker)
        return SimpleNamespace(ticker=ticker, status="open")

    def get_all_open_markets(self, *, max_pages: int):
        self.open_page_calls.append(max_pages)
        return [
            SimpleNamespace(ticker="KX-A", status="open"),
            SimpleNamespace(ticker="KX-B", status="open"),
            SimpleNamespace(ticker="KX-C", status="open"),
        ]


class FakeTask:
    def __init__(self) -> None:
        self.markets: list[object] = []

    async def run_once(self, markets):
        self.markets = list(markets)
        return [
            ResearchPrewarmResult(
                market_ticker=market.ticker,
                status="trade_candidate" if market.ticker.endswith("A") else "continue_researching",
                attempted=True,
                query_count=3,
                evidence_count=2,
            )
            for market in self.markets
        ]


@pytest.mark.asyncio
async def test_run_once_fetches_explicit_tickers_without_open_market_scan():
    client = FakeClient()
    task = FakeTask()
    args = Namespace(
        ticker=["KX-A", "KX-B"],
        max_markets=10,
        max_pages=99,
    )

    summary = await research_prewarm.run_once(args, client=client, task=task)

    assert client.market_calls == ["KX-A", "KX-B"]
    assert client.open_page_calls == []
    assert [market.ticker for market in task.markets] == ["KX-A", "KX-B"]
    assert summary == {
        "markets": 2,
        "attempted": 2,
        "statuses": {
            "continue_researching": 1,
            "trade_candidate": 1,
        },
        "evidence": 4,
        "queries": 6,
    }


@pytest.mark.asyncio
async def test_run_once_limits_open_market_scan_before_research():
    client = FakeClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        max_markets=2,
        max_pages=7,
    )

    summary = await research_prewarm.run_once(args, client=client, task=task)

    assert client.open_page_calls == [7]
    assert [market.ticker for market in task.markets] == ["KX-A", "KX-B"]
    assert summary["markets"] == 2


@pytest.mark.asyncio
async def test_run_once_targets_recent_no_keyword_trade_log_tickers(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    write_jsonl(
        trade_log,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T10:00:00Z",
                "ticker": "KX-OLD",
                "reason": "no_keywords",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T11:00:00Z",
                "ticker": "KX-SKIP",
                "reason": "stale_news",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T12:00:00Z",
                "ticker": "KX-NEW",
                "reason": "research_incomplete",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T13:00:00Z",
                "ticker": "KX-OLD",
                "reason": "no_keywords",
            },
        ],
    )
    client = FakeClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        target_from_log=trade_log,
        target_since="2026-06-27T09:00:00Z",
        target_reason=["no_keywords", "research_incomplete"],
        target_rejection_category=[],
        max_markets=2,
        max_pages=99,
    )

    summary = await research_prewarm.run_once(args, client=client, task=task)

    assert client.open_page_calls == []
    assert client.market_calls == ["KX-OLD", "KX-NEW"]
    assert [market.ticker for market in task.markets] == ["KX-OLD", "KX-NEW"]
    assert summary["markets"] == 2


@pytest.mark.asyncio
async def test_run_once_targets_retryable_research_skip_reasons(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    write_jsonl(
        trade_log,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T10:00:00Z",
                "ticker": "KX-CACHE",
                "reason": "researched_no_edge",
                "research_skip_reason": "cached_dossier_insufficient",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T11:00:00Z",
                "ticker": "KX-OPS",
                "reason": "research_operational_error",
                "research_skip_reason": "research_provider_error",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T12:00:00Z",
                "ticker": "KX-CAPITAL",
                "reason": "researched_no_edge",
                "research_skip_reason": "no_trade_capital_protection",
            },
        ],
    )
    client = FakeClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        target_from_log=trade_log,
        target_since="2026-06-27T09:00:00Z",
        target_reason=[
            "no_keywords",
            "research_incomplete",
            "research_operational_error",
        ],
        target_research_skip_reason=[
            "cached_dossier_insufficient",
            "cached_dossier_unvetted",
            "research_timeout",
            "research_provider_error",
            "research_adjudicator_error",
        ],
        target_rejection_category=[],
        max_markets=5,
        max_pages=99,
    )

    summary = await research_prewarm.run_once(args, client=client, task=task)

    assert client.open_page_calls == []
    assert client.market_calls == ["KX-OPS", "KX-CACHE"]
    assert [market.ticker for market in task.markets] == ["KX-OPS", "KX-CACHE"]
    assert summary["markets"] == 2


@pytest.mark.asyncio
async def test_run_once_targets_empty_keyword_neutral_review_rows(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    write_jsonl(
        trade_log,
        [
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": "2026-06-27T10:00:00Z",
                "ticker": "KX-MISS",
                "verdict": "false_positive_neutral",
                "keyword_count": 0,
            },
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": "2026-06-27T11:00:00Z",
                "ticker": "KX-HASKEYWORDS",
                "verdict": "false_positive_neutral",
                "keyword_count": 2,
            },
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": "2026-06-27T12:00:00Z",
                "ticker": "KX-RELEVANT",
                "verdict": "relevant",
                "keyword_count": 0,
            },
        ],
    )
    client = FakeClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        target_from_log=trade_log,
        target_since="2026-06-27T09:00:00Z",
        target_reason=["no_keywords", "research_incomplete"],
        target_research_skip_reason=[],
        target_rejection_category=[],
        max_markets=5,
        max_pages=99,
    )

    summary = await research_prewarm.run_once(args, client=client, task=task)

    assert client.open_page_calls == []
    assert client.market_calls == ["KX-MISS"]
    assert [market.ticker for market in task.markets] == ["KX-MISS"]
    assert summary["markets"] == 1


@pytest.mark.asyncio
async def test_run_once_targets_empty_keyword_review_rows_with_category_filter(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    write_jsonl(
        trade_log,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T09:00:00Z",
                "ticker": "KX-REJECT",
                "reason": "no_keywords",
                "rejection_category": "other_category",
            },
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": "2026-06-27T10:00:00Z",
                "ticker": "KX-MISS",
                "verdict": "false_positive_neutral",
                "keyword_count": 0,
            },
        ],
    )
    client = FakeClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        target_from_log=trade_log,
        target_since="2026-06-27T08:00:00Z",
        target_reason=["no_keywords"],
        target_research_skip_reason=[],
        target_rejection_category=["no_signal_empty_keywords"],
        max_markets=5,
        max_pages=99,
    )

    summary = await research_prewarm.run_once(args, client=client, task=task)

    assert client.open_page_calls == []
    assert client.market_calls == ["KX-MISS"]
    assert [market.ticker for market in task.markets] == ["KX-MISS"]
    assert summary["markets"] == 1


@pytest.mark.asyncio
async def test_run_once_targets_useful_pre_llm_blocked_empty_keyword_rows(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    write_jsonl(
        trade_log,
        [
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": "2026-06-27T10:00:00Z",
                "ticker": "KX-USEFUL",
                "keywords": [],
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
                "pre_llm_would_block_and_useful": True,
            },
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": "2026-06-27T11:00:00Z",
                "ticker": "KX-NOTUSEFUL",
                "keywords": [],
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
                "pre_llm_would_block_and_useful": False,
            },
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": "2026-06-27T12:00:00Z",
                "ticker": "KX-HASKEYWORDS",
                "keywords": ["midterms"],
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
                "pre_llm_would_block_and_useful": True,
            },
        ],
    )
    client = FakeClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        target_from_log=trade_log,
        target_since="2026-06-27T09:00:00Z",
        target_reason=["no_keywords", "research_incomplete"],
        target_research_skip_reason=[],
        target_rejection_category=[],
        max_markets=5,
        max_pages=99,
    )

    summary = await research_prewarm.run_once(args, client=client, task=task)

    assert client.open_page_calls == []
    assert client.market_calls == ["KX-USEFUL"]
    assert [market.ticker for market in task.markets] == ["KX-USEFUL"]
    assert summary["markets"] == 1


def test_build_argparser_defaults_to_single_run():
    args = research_prewarm.build_argparser().parse_args([])

    assert args.interval_seconds is None
    assert args.max_markets == 50
    assert args.max_pages == 10
    assert args.max_queries == 6
    assert args.target_reason == [
        "no_keywords",
        "research_incomplete",
        "research_operational_error",
    ]
    assert args.target_research_skip_reason == [
        "cached_dossier_insufficient",
        "cached_dossier_unvetted",
        "research_timeout",
        "research_provider_error",
        "research_adjudicator_error",
    ]
