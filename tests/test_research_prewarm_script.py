from __future__ import annotations

import sqlite3
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scripts import research_prewarm
from kalshi.series_metadata import SettlementSource
from tests._helpers import write_jsonl
from analysis.research_gate import ResearchStatus
from tasks.research_dossier import ResearchDossierStore
from tasks.research_prewarm_task import ResearchPrewarmResult


class FakeClient:
    def __init__(self) -> None:
        self.market_calls: list[str] = []
        self.open_page_calls: list[int] = []
        self.series_market_calls: list[str] = []

    def get_market(self, ticker: str):
        self.market_calls.append(ticker)
        return SimpleNamespace(
            ticker=ticker,
            status="open",
            yes_ask_cents=42,
            no_ask_cents=59,
        )

    def get_all_open_markets(self, *, max_pages: int):
        self.open_page_calls.append(max_pages)
        return [
            SimpleNamespace(
                ticker="KX-A",
                status="open",
                yes_ask_cents=42,
                no_ask_cents=59,
            ),
            SimpleNamespace(
                ticker="KX-B",
                status="open",
                yes_ask_cents=43,
                no_ask_cents=58,
            ),
            SimpleNamespace(
                ticker="KX-C",
                status="open",
                yes_ask_cents=44,
                no_ask_cents=57,
            ),
        ]

    def get_markets(self, *, series_ticker: str, limit: int):
        self.series_market_calls.append(series_ticker)
        return [], None


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
async def test_run_once_processes_missing_explicit_ticker_as_closed_market():
    class MissingMarketClient(FakeClient):
        def get_market(self, ticker: str):
            self.market_calls.append(ticker)
            return None

    client = MissingMarketClient()
    task = FakeTask()
    args = Namespace(
        ticker=["KXMISSING-26JUL01"],
        max_markets=10,
        max_pages=99,
    )

    summary = await research_prewarm.run_once(args, client=client, task=task)

    assert client.market_calls == ["KXMISSING-26JUL01"]
    assert client.open_page_calls == []
    assert [market.ticker for market in task.markets] == ["KXMISSING-26JUL01"]
    assert task.markets[0].status == "closed"
    assert summary["markets"] == 1


@pytest.mark.asyncio
async def test_run_once_can_include_compact_per_market_results():
    client = FakeClient()
    task = FakeTask()
    args = Namespace(
        ticker=["KX-A", "KX-B"],
        max_markets=10,
        max_pages=99,
        include_results=True,
    )

    summary = await research_prewarm.run_once(args, client=client, task=task)

    assert summary["results"] == [
        {
            "market_ticker": "KX-A",
            "status": "trade_candidate",
            "attempted": True,
            "skip_reason": None,
            "query_count": 3,
            "evidence_count": 2,
            "research_run_id": None,
        },
        {
            "market_ticker": "KX-B",
            "status": "continue_researching",
            "attempted": True,
            "skip_reason": None,
            "query_count": 3,
            "evidence_count": 2,
            "research_run_id": None,
        },
    ]


@pytest.mark.asyncio
async def test_run_once_explicit_ticker_bypasses_configured_target_cooldown(monkeypatch):
    created: dict[str, object] = {}

    class CapturingTask(FakeTask):
        def __init__(self, **kwargs) -> None:
            super().__init__()
            created.update(kwargs)

    monkeypatch.setattr(research_prewarm, "ResearchPrewarmTask", CapturingTask)
    monkeypatch.setattr(
        research_prewarm.cfg,
        "research_prewarm_target_cooldown_seconds",
        1800.0,
    )
    client = FakeClient()
    args = Namespace(
        ticker=["KX-A"],
        max_markets=10,
        max_pages=99,
        max_queries=6,
        timeout_seconds=12.0,
        interval_seconds=None,
        no_trade_log=True,
        db_path=None,
    )

    await research_prewarm.run_once(args, client=client)

    assert created["target_cooldown_seconds"] == 0.0


@pytest.mark.asyncio
async def test_run_once_prioritizes_actionable_price_open_markets():
    class PriceClient(FakeClient):
        def get_all_open_markets(self, *, max_pages: int):
            self.open_page_calls.append(max_pages)
            return [
                SimpleNamespace(
                    ticker="KX-ZERO",
                    status="open",
                    yes_ask_cents=0,
                    no_ask_cents=100,
                ),
                SimpleNamespace(
                    ticker="KX-PRICED",
                    status="open",
                    yes_ask_cents=42,
                    no_ask_cents=59,
                ),
            ]

    client = PriceClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        max_markets=1,
        max_pages=2,
        target_from_log=None,
    )

    await research_prewarm.run_once(args, client=client, task=task)

    assert [market.ticker for market in task.markets] == ["KX-PRICED"]


@pytest.mark.asyncio
async def test_run_once_skips_due_task_missing_from_market_api(tmp_path):
    class MixedDueMarketClient(FakeClient):
        def get_market(self, ticker: str):
            self.market_calls.append(ticker)
            if ticker == "KXMISSING-26JUL01":
                return None
            return SimpleNamespace(
                ticker=ticker,
                status="open",
                yes_ask_cents=42,
                no_ask_cents=59,
            )

    db_path = tmp_path / "evidence_store.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, updated_ts
            ) VALUES (
                'KXMISSING-26JUL01',
                'needs_research',
                '2026-06-01T00:00:00Z'
            ), (
                'KXOPEN-26JUL01',
                'needs_research',
                '2026-06-01T00:01:00Z'
            )
            """
        )
    client = MixedDueMarketClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        db_path=db_path,
        max_markets=1,
        max_pages=2,
        target_from_log=None,
        sourceable_series_fallback=[],
    )

    await research_prewarm.run_once(args, client=client, task=task)

    assert client.market_calls == ["KXMISSING-26JUL01", "KXOPEN-26JUL01"]
    assert client.open_page_calls == []
    assert [market.ticker for market in task.markets] == ["KXOPEN-26JUL01"]


@pytest.mark.asyncio
async def test_run_once_processes_due_task_without_actionable_price(tmp_path):
    class MissingPriceClient(FakeClient):
        def get_market(self, ticker: str):
            self.market_calls.append(ticker)
            return SimpleNamespace(
                ticker=ticker,
                status="open",
                yes_ask_cents=None,
                no_ask_cents=None,
            )

    db_path = tmp_path / "evidence_store.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, updated_ts
            ) VALUES (
                'KXNOPRICE-26JUL01',
                'needs_price_edge',
                '2026-06-01T00:00:00Z'
            )
            """
        )
    client = MissingPriceClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        db_path=db_path,
        max_markets=1,
        max_pages=2,
        target_from_log=None,
        sourceable_series_fallback=[],
    )

    await research_prewarm.run_once(args, client=client, task=task)

    assert client.market_calls == ["KXNOPRICE-26JUL01"]
    assert client.open_page_calls == []
    assert [market.ticker for market in task.markets] == ["KXNOPRICE-26JUL01"]
    assert task.markets[0].yes_ask_cents is None


@pytest.mark.asyncio
async def test_run_once_prioritizes_researchable_priced_open_markets():
    class ResearchabilityClient(FakeClient):
        def get_all_open_markets(self, *, max_pages: int):
            self.open_page_calls.append(max_pages)
            return [
                SimpleNamespace(
                    ticker="KXMVECROSSCATEGORY-SYNTHETIC",
                    status="open",
                    title="yes Player A: 2+,yes Player B: 3+",
                    yes_ask_cents=42,
                    no_ask_cents=59,
                    settlement_sources=(),
                    rules_primary="",
                    rules_secondary="",
                ),
                SimpleNamespace(
                    ticker="KXCPI-26NOV-T0.2",
                    series_ticker="KXCPI",
                    status="open",
                    title="Will CPI rise by 0.2% in November?",
                    yes_ask_cents=45,
                    no_ask_cents=56,
                    settlement_sources=(
                        SettlementSource(label="BLS", domain="bls.gov"),
                    ),
                    rules_primary="Official BLS CPI release resolves this market.",
                    rules_secondary="",
                ),
            ]

    client = ResearchabilityClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        max_markets=1,
        max_pages=2,
        target_from_log=None,
    )

    await research_prewarm.run_once(args, client=client, task=task)

    assert [market.ticker for market in task.markets] == ["KXCPI-26NOV-T0.2"]


@pytest.mark.asyncio
async def test_run_once_uses_sourceable_series_fallback_before_mv_open_scan():
    class SeriesFallbackClient(FakeClient):
        def get_all_open_markets(self, *, max_pages: int):
            self.open_page_calls.append(max_pages)
            return [
                SimpleNamespace(
                    ticker="KXMVESPORTSMULTIGAMEEXTENDED-SYNTHETIC",
                    status="open",
                    yes_ask_cents=44,
                    no_ask_cents=58,
                    settlement_sources=(
                        SettlementSource(label="League homepage", domain="nfl.com"),
                    ),
                )
            ]

        def get_markets(self, *, series_ticker: str, limit: int):
            self.series_market_calls.append(series_ticker)
            if series_ticker == "KXGDP":
                return [
                    SimpleNamespace(
                        ticker="KXGDP-26JUL30-T3.0",
                        series_ticker="KXGDP",
                        status="open",
                        yes_ask_cents=26,
                        no_ask_cents=79,
                        settlement_sources=(
                            SettlementSource(label="BEA", domain="bea.gov"),
                        ),
                        rules_primary="Official BEA GDP release resolves this market.",
                        rules_secondary="",
                    )
                ], None
            return [], None

    client = SeriesFallbackClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        max_markets=1,
        max_pages=2,
        target_from_log=None,
        sourceable_series_fallback=["KXGDP"],
    )

    await research_prewarm.run_once(args, client=client, task=task)

    assert client.series_market_calls == ["KXGDP"]
    assert client.open_page_calls == []
    assert [market.ticker for market in task.markets] == ["KXGDP-26JUL30-T3.0"]


@pytest.mark.asyncio
async def test_run_once_prioritizes_due_research_task_from_db(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    await store.record_research_run(
        "KX-DUE",
        "run-due",
        trigger_headline="Needs more research",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Research needs counter evidence.",
        verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        skip_reason="ambiguous_direction",
        decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T10:00:00.000Z',
                cooldown_until_ts = '2026-06-30T10:05:00.000Z'
            WHERE market_ticker = 'KX-DUE'
            """
        )

    client = FakeClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        max_markets=1,
        max_pages=2,
        target_from_log=None,
        db_path=db_path,
    )

    await research_prewarm.run_once(args, client=client, task=task)

    assert client.market_calls == ["KX-DUE"]
    assert client.open_page_calls == []
    assert [market.ticker for market in task.markets] == ["KX-DUE"]


@pytest.mark.asyncio
async def test_run_once_uses_default_store_for_due_tasks_when_db_path_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class DefaultStore:
        def __init__(self) -> None:
            captured["store_created"] = True

        def get_due_research_task_tickers(self, **_kwargs):
            return ["KX-DEFAULT-DUE"]

    class CapturingTask(FakeTask):
        def __init__(self, **kwargs) -> None:
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr(research_prewarm, "ResearchDossierStore", DefaultStore)
    monkeypatch.setattr(research_prewarm, "ResearchPrewarmTask", CapturingTask)
    client = FakeClient()
    args = research_prewarm.build_argparser().parse_args(["--max-markets", "1"])

    await research_prewarm.run_once(args, client=client)

    assert captured["store_created"] is True
    assert captured["store"].__class__ is DefaultStore
    assert client.market_calls == ["KX-DEFAULT-DUE"]
    assert client.open_page_calls == []


@pytest.mark.asyncio
async def test_run_once_does_not_select_due_task_inside_configured_cooldown(
    monkeypatch,
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    await store.record_research_run(
        "KX-COOLDOWN",
        "run-cooldown",
        trigger_headline="Needs more research",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Research needs counter evidence.",
        verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        skip_reason="ambiguous_direction",
        decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
    )
    recent = datetime.now(timezone.utc) - timedelta(seconds=60)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = ?,
                cooldown_until_ts = '2000-01-01T00:00:00.000Z'
            WHERE market_ticker = 'KX-COOLDOWN'
            """,
            (recent.isoformat(timespec="milliseconds").replace("+00:00", "Z"),),
        )

    monkeypatch.setattr(
        research_prewarm.cfg,
        "research_prewarm_target_cooldown_seconds",
        1800.0,
    )
    client = FakeClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        max_markets=1,
        max_pages=2,
        target_from_log=None,
        db_path=db_path,
    )

    await research_prewarm.run_once(args, client=client, task=task)

    assert "KX-COOLDOWN" not in client.market_calls
    assert client.open_page_calls == [2]
    assert [market.ticker for market in task.markets] == ["KX-A"]


@pytest.mark.asyncio
async def test_run_once_enriches_explicit_ticker_with_series_metadata():
    class SeriesClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.series_calls: list[str] = []

        def get_market(self, ticker: str):
            self.market_calls.append(ticker)
            return SimpleNamespace(
                ticker=ticker,
                series_ticker="KXCPI",
                status="open",
                settlement_sources=(),
                contract_terms_url="",
                rules_primary="",
                rules_secondary="",
            )

        def get_series(self, series_ticker: str):
            self.series_calls.append(series_ticker)
            return SimpleNamespace(
                settlement_sources=(SettlementSource(label="BLS", domain="bls.gov"),),
                contract_terms_url="https://kalshi.com/markets/KXCPI",
                rules_primary="Official BLS CPI release resolves this market.",
                rules_secondary="Later revisions ignored.",
            )

    client = SeriesClient()
    task = FakeTask()
    args = Namespace(
        ticker=["KXCPI-26NOV-T0.1"],
        max_markets=10,
        max_pages=99,
    )

    await research_prewarm.run_once(args, client=client, task=task)

    assert client.series_calls == ["KXCPI"]
    assert task.markets[0].settlement_sources == (
        SettlementSource(label="BLS", domain="bls.gov"),
    )
    assert task.markets[0].contract_terms_url == "https://kalshi.com/markets/KXCPI"


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
                "ticker": "paccc-usse-midterms-2026-11-03-rep",
                "reason": "research_incomplete",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T14:00:00Z",
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
async def test_run_once_targets_nonterminal_prewarm_result_from_log(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    write_jsonl(
        trade_log,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-27T14:00:00Z",
                "ticker": "KX-CONTINUE",
                "venue": "kalshi",
                "research_status": "needs_counter_evidence",
            }
        ],
    )
    client = FakeClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        target_from_log=trade_log,
        target_since="2026-06-27T09:00:00Z",
        target_reason=[],
        target_research_skip_reason=[],
        target_rejection_category=[],
        max_markets=1,
        max_pages=99,
    )

    await research_prewarm.run_once(args, client=client, task=task)

    assert client.market_calls == ["KX-CONTINUE"]
    assert client.open_page_calls == []
    assert [market.ticker for market in task.markets] == ["KX-CONTINUE"]


@pytest.mark.asyncio
async def test_run_once_skips_unpriced_log_targets_and_falls_back_to_open_markets(
    tmp_path,
):
    trade_log = tmp_path / "trades.jsonl"
    write_jsonl(
        trade_log,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T12:00:00Z",
                "ticker": "KX-UNPRICED",
                "reason": "research_incomplete",
            },
        ],
    )

    class PriceFallbackClient(FakeClient):
        def get_market(self, ticker: str):
            self.market_calls.append(ticker)
            return SimpleNamespace(ticker=ticker, status="open")

        def get_all_open_markets(self, *, max_pages: int):
            self.open_page_calls.append(max_pages)
            return [
                SimpleNamespace(
                    ticker="KX-PRICED",
                    status="open",
                    yes_ask_cents=42,
                    no_ask_cents=59,
                )
            ]

    client = PriceFallbackClient()
    task = FakeTask()
    args = Namespace(
        ticker=[],
        target_from_log=trade_log,
        target_since="2026-06-27T09:00:00Z",
        target_reason=["research_incomplete"],
        target_research_skip_reason=[],
        target_rejection_category=[],
        max_markets=1,
        max_pages=3,
    )

    await research_prewarm.run_once(args, client=client, task=task)

    assert client.market_calls == ["KX-UNPRICED"]
    assert client.open_page_calls == [3]
    assert [market.ticker for market in task.markets] == ["KX-PRICED"]


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
                "ts": "2026-06-27T11:30:00Z",
                "ticker": "KX-AMBIG",
                "reason": "researched_no_edge",
                "research_skip_reason": "ambiguous_direction",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T12:00:00Z",
                "ticker": "KX-SOURCE",
                "reason": "researched_no_edge",
                "research_skip_reason": "missing_resolution_source",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T12:30:00Z",
                "ticker": "KX-OFFICIAL",
                "reason": "researched_no_edge",
                "research_skip_reason": "official_data_pending",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-27T13:00:00Z",
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
            "ambiguous_direction",
            "cached_dossier_insufficient",
            "cached_dossier_unvetted",
            "missing_resolution_source",
            "official_data_pending",
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
    assert client.market_calls == [
        "KX-OFFICIAL",
        "KX-SOURCE",
        "KX-AMBIG",
        "KX-OPS",
        "KX-CACHE",
    ]
    assert [market.ticker for market in task.markets] == [
        "KX-OFFICIAL",
        "KX-SOURCE",
        "KX-AMBIG",
        "KX-OPS",
        "KX-CACHE",
    ]
    assert summary["markets"] == 5


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
                "ticker": "KX-THIN",
                "verdict": "false_positive_neutral",
                "keyword_count": 1,
            },
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": "2026-06-27T12:00:00Z",
                "ticker": "KX-HASKEYWORDS",
                "verdict": "false_positive_neutral",
                "keyword_count": 2,
            },
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": "2026-06-27T13:00:00Z",
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
    assert client.market_calls == ["KX-THIN", "KX-MISS"]
    assert [market.ticker for market in task.markets] == ["KX-THIN", "KX-MISS"]
    assert summary["markets"] == 2


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
                "ticker": "KX-THIN",
                "keywords": ["thin"],
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
                "pre_llm_would_block_and_useful": False,
            },
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": "2026-06-27T13:00:00Z",
                "ticker": "KX-HASKEYWORDS",
                "keywords": ["midterms", "senate"],
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
    assert client.market_calls == [
        "KX-HASKEYWORDS",
        "KX-THIN",
        "KX-NOTUSEFUL",
        "KX-USEFUL",
    ]
    assert [market.ticker for market in task.markets] == [
        "KX-HASKEYWORDS",
        "KX-THIN",
        "KX-NOTUSEFUL",
        "KX-USEFUL",
    ]
    assert summary["markets"] == 4


@pytest.mark.asyncio
async def test_run_once_excludes_startup_probe_log_targets(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    write_jsonl(
        trade_log,
        [
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": "2026-06-27T10:00:00Z",
                "ticker": "KXSTARTUP-PROBE",
                "keywords": ["peace deal"],
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
                "is_startup_probe": True,
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
        max_markets=1,
        max_pages=2,
    )

    await research_prewarm.run_once(args, client=client, task=task)

    assert client.market_calls == []
    assert client.open_page_calls == [2]
    assert [market.ticker for market in task.markets] == ["KX-A"]


def test_build_argparser_defaults_to_single_run():
    args = research_prewarm.build_argparser().parse_args([])

    assert args.interval_seconds is None
    assert args.no_trade_log is False
    assert args.max_markets == 50
    assert args.max_pages == 10
    assert args.max_queries == 6
    assert args.target_reason == [
        "no_keywords",
        "research_incomplete",
        "research_operational_error",
    ]
    assert args.target_research_skip_reason == [
        "ambiguous_direction",
        "cached_dossier_insufficient",
        "cached_dossier_unvetted",
        "direction_reason_conflict",
        "insufficient_corroboration",
        "missing_estimated_probability",
        "missing_resolution_source",
        "new_market",
        "no_research_hits",
        "official_data_pending",
        "probability_direction_conflict",
        "research_timeout",
        "research_provider_error",
        "research_adjudicator_error",
    ]


@pytest.mark.asyncio
async def test_run_once_no_trade_log_passes_discard_sink(monkeypatch):
    captured = {}

    class CapturingTask(FakeTask):
        def __init__(self, **kwargs) -> None:
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr(research_prewarm, "ResearchPrewarmTask", CapturingTask)
    monkeypatch.setattr(
        research_prewarm.cfg,
        "research_prewarm_target_cooldown_seconds",
        1800.0,
    )
    args = Namespace(
        ticker=[],
        max_markets=1,
        max_pages=1,
        db_path=None,
        max_queries=6,
        timeout_seconds=12.0,
        no_trade_log=True,
    )

    await research_prewarm.run_once(args, client=FakeClient())

    assert captured["result_sink"] is not None
    assert captured["target_cooldown_seconds"] == 1800.0


@pytest.mark.asyncio
async def test_run_once_single_run_emits_trade_log_by_default(monkeypatch):
    captured = {}

    class CapturingTask(FakeTask):
        def __init__(self, **kwargs) -> None:
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr(research_prewarm, "ResearchPrewarmTask", CapturingTask)
    args = Namespace(
        ticker=["KX-A"],
        max_markets=1,
        max_pages=1,
        db_path=None,
        max_queries=6,
        timeout_seconds=12.0,
        interval_seconds=None,
        no_trade_log=False,
    )

    await research_prewarm.run_once(args, client=FakeClient())

    assert captured["result_sink"] is None


@pytest.mark.asyncio
async def test_run_once_periodic_mode_emits_trade_log_by_default(monkeypatch):
    captured = {}

    class CapturingTask(FakeTask):
        def __init__(self, **kwargs) -> None:
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr(research_prewarm, "ResearchPrewarmTask", CapturingTask)
    args = Namespace(
        ticker=["KX-A"],
        max_markets=1,
        max_pages=1,
        db_path=None,
        max_queries=6,
        timeout_seconds=12.0,
        interval_seconds=300.0,
        no_trade_log=False,
    )

    await research_prewarm.run_once(args, client=FakeClient())

    assert captured["result_sink"] is None
