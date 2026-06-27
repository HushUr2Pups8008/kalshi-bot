from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest

from scripts import research_prewarm
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


def test_build_argparser_defaults_to_single_run():
    args = research_prewarm.build_argparser().parse_args([])

    assert args.interval_seconds is None
    assert args.max_markets == 50
    assert args.max_pages == 10
    assert args.max_queries == 6
