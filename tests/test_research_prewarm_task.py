from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import sqlite3

import pytest

from analysis.research_gate import ResearchEvidence, ResearchStatus, ResearchVerdict
from tasks.research_dossier import ResearchDossierStore
from tasks.research_prewarm_task import (
    ResearchPrewarmError,
    ResearchPrewarmTask,
    _prewarm_news,
)


def _market(ticker: str = "KXRESEARCH-26DEC31", *, status: str = "open"):
    return SimpleNamespace(
        ticker=ticker,
        title="Will the researched event resolve yes?",
        rules_primary="The market resolves from the official report.",
        rules_secondary="Later revisions are ignored.",
        settlement_sources=(),
        contract_terms_url="",
        status=status,
        yes_ask_cents=60,
        no_ask_cents=40,
    )


def test_prewarm_news_does_not_fabricate_trigger_headline():
    news = _prewarm_news(_market())

    assert news.headline == ""
    assert news.source == "research_prewarm"


@pytest.mark.asyncio
async def test_prewarm_process_market_persists_research_run_and_evidence(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def search_provider(_query):
        return [
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Official Report",
                source_url="https://official.example.com/report",
                title="Official report",
                snippet="The official report supports yes.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.9,
                retrieved_at=retrieved_at,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Wire",
                source_url="https://wire.example.com/context",
                title="Wire context",
                snippet="Independent context also supports yes.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
                retrieved_at=retrieved_at,
            ),
        ]

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.8,
            "confidence": 0.8,
            "reason": "Prewarmed evidence clears edge.",
        }

    task = ResearchPrewarmTask(
        store=store,
        search_provider=search_provider,
        adjudicator=adjudicator,
    )

    result = await task.process_market(_market())

    assert result.status == ResearchStatus.TRADE_CANDIDATE.value
    assert result.attempted is True
    assert result.evidence_count == 2
    with sqlite3.connect(db_path) as conn:
        dossier = conn.execute(
            "SELECT last_verdict_status, last_force_side FROM research_dossiers"
        ).fetchone()
        evidence_count = conn.execute("SELECT COUNT(*) FROM research_evidence").fetchone()
    assert dossier == (ResearchStatus.TRADE_CANDIDATE.value, "yes")
    assert evidence_count == (2,)


@pytest.mark.asyncio
async def test_prewarm_run_once_market_failure_does_not_abort_cycle(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(_news, market, **_kwargs):
        if market.ticker == "KXRESEARCH-BAD":
            raise RuntimeError("research failed")
        return SimpleNamespace(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="no_research_hits",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    results = await task.run_once([
        _market("KXRESEARCH-BAD"),
        _market("KXRESEARCH-GOOD"),
    ])

    assert [result.market_ticker for result in results] == ["KXRESEARCH-GOOD"]
    assert results[0].status == ResearchStatus.CONTINUE_RESEARCHING.value


@pytest.mark.asyncio
async def test_prewarm_run_once_limits_market_concurrency(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    started: list[str] = []
    release = asyncio.Event()
    active = 0
    max_active = 0

    async def research_gate(_news, market, **_kwargs):
        nonlocal active, max_active
        started.append(market.ticker)
        active += 1
        max_active = max(max_active, active)
        await release.wait()
        active -= 1
        return SimpleNamespace(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="no_research_hits",
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        max_concurrency=2,
    )
    run_task = asyncio.create_task(
        task.run_once([_market(f"KXRESEARCH-{i}") for i in range(5)])
    )
    while len(started) < 2:
        await asyncio.sleep(0)

    assert len(started) == 2
    assert set(started) <= {f"KXRESEARCH-{i}" for i in range(5)}
    assert max_active == 2
    release.set()
    results = await run_task

    assert len(results) == 5
    assert max_active == 2


@pytest.mark.asyncio
async def test_prewarm_run_once_emits_structured_result_events(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    emitted = []

    async def research_gate(_news, market, **_kwargs):
        if market.ticker == "KXRESEARCH-BAD":
            raise RuntimeError("research failed")
        return SimpleNamespace(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=[object(), object()],
            evidence=[object()],
            skip_reason="no_research_hits",
            research_run_id="rr-test-good",
            log_fields=lambda: {
                "research_contract_fingerprint": "contract-test-good",
            },
            research_persisted=True,
            research_persistence_error=None,
            research_direct_fetch_failures=("resolution_source:https://bad.example:boom",),
        )

    async def result_sink(result):
        emitted.append(result)

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        result_sink=result_sink,
    )

    results = await task.run_once(
        [
            _market("KXRESEARCH-BAD"),
            _market("KXRESEARCH-GOOD"),
        ]
    )

    assert [result.market_ticker for result in results] == ["KXRESEARCH-GOOD"]
    assert [(result.market_ticker, result.status) for result in emitted] == [
        ("KXRESEARCH-BAD", "error"),
        ("KXRESEARCH-GOOD", ResearchStatus.CONTINUE_RESEARCHING.value),
    ]
    assert emitted[0].error == "failed research prewarm for KXRESEARCH-BAD"
    assert emitted[1].query_count == 2
    assert emitted[1].evidence_count == 1
    assert emitted[1].research_run_id == "rr-test-good"
    assert emitted[1].research_contract_fingerprint == "contract-test-good"
    assert emitted[1].research_persisted is True
    assert len(emitted[1].research_direct_fetch_failures) == 1


@pytest.mark.asyncio
async def test_prewarm_skips_closed_markets_without_research_call(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(*_args, **_kwargs):
        raise AssertionError("closed markets must not be researched")

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market(status="closed"))

    assert result.status == "skipped_closed"
    assert result.attempted is False


@pytest.mark.asyncio
async def test_prewarm_skips_unresearchable_markets_without_research_call(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(*_args, **_kwargs):
        raise AssertionError("markets without a source path must not be researched")

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(
        SimpleNamespace(
            ticker="KXMVESPORTSMULTIGAMEEXTENDED-S2026",
            title="yes Pittsburgh,yes Texas,yes Tampa Bay,yes Detroit",
            rules_primary="",
            rules_secondary="",
            settlement_sources=(),
            contract_terms_url="",
            status="active",
            yes_ask_cents=60,
            no_ask_cents=40,
        )
    )

    assert result.status == "skipped_unresearchable"
    assert result.attempted is False
    assert result.skip_reason == "missing_source_path"


@pytest.mark.asyncio
async def test_prewarm_processes_active_kalshi_response_markets(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls = []

    async def research_gate(news, market, **kwargs):
        calls.append((news, market, kwargs))
        return ResearchVerdict(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            evidence=[
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="Wire",
                    source_url="https://wire.example.com/context",
                    title="Wire context",
                    snippet="More context is needed.",
                    claim_type="corroboration",
                )
            ],
            skip_reason="insufficient_corroboration",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market(status="active"))

    assert result.status == ResearchStatus.CONTINUE_RESEARCHING.value
    assert result.attempted is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_prewarm_process_market_wraps_failures(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    with pytest.raises(ResearchPrewarmError, match="KXRESEARCH-26DEC31"):
        await task.process_market(_market())
