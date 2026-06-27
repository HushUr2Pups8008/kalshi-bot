from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import sqlite3

import pytest

from analysis.research_gate import ResearchEvidence, ResearchStatus
from tasks.research_dossier import ResearchDossierStore
from tasks.research_prewarm_task import (
    ResearchPrewarmError,
    ResearchPrewarmTask,
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
async def test_prewarm_process_market_wraps_failures(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    with pytest.raises(ResearchPrewarmError, match="KXRESEARCH-26DEC31"):
        await task.process_market(_market())
