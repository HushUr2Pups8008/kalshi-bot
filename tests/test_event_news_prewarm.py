from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from analysis.research_gate import ResearchEvidence, ResearchStatus, ResearchVerdict, run_research_gate
from kalshi.normalizer import SettlementSource
from tasks.research_dossier import ResearchDossierStore
from tasks.research_prewarm_task import ResearchPrewarmTask
from utils.event_news_research import EVENT_NEWS_COHORT_ID


@pytest.mark.asyncio
async def test_injected_direct_fetcher_receives_official_pdf_flag():
    seen: dict[str, object] = {}
    fresh = "2026-08-22T16:00:00Z"

    async def spy(url, source_class, claim_type, *, allow_official_pdf_and_homepage=False):
        seen["allow"] = allow_official_pdf_and_homepage
        seen["url"] = url
        return ResearchEvidence(
            source_class=source_class,
            source_name=url,
            source_url=url,
            title="Official terms",
            snippet="Official settlement language.",
            claim_type=claim_type,
            retrieved_at=fresh,
        )

    async def search_provider(_query):
        return []

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.8,
            "confidence": 0.7,
            "reason": "Official source supports YES.",
        }

    await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        SimpleNamespace(
            ticker="KXOFFICIAL-26AUG22",
            title="Will the nominee be confirmed?",
            rules_primary="Resolves to the official PDF.",
            rules_secondary="",
            settlement_sources=(
                SettlementSource(label="Senate", url="https://www.senate.gov/"),
            ),
            contract_terms_url="https://assets.kalshi.com/contract_terms/NOMINEE.pdf",
        ),
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.80,
        no_ask=0.22,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=spy,
        adjudicator=adjudicator,
        require_decision_grade=True,
        allow_official_pdf_and_homepage=True,
        prefer_official_sources=True,
    )

    assert seen.get("allow") is True
    assert str(seen.get("url") or "").endswith(".pdf") or "senate.gov" in str(seen.get("url") or "")


@pytest.mark.asyncio
async def test_politics_prewarm_skips_longshot_and_passes_official_kwargs(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "utils.event_news_research.cfg",
        SimpleNamespace(
            paper_cohort_id=EVENT_NEWS_COHORT_ID,
            llm_allowed_price_bands=[(0.55, 0.99)],
            llm_excluded_price_bands=[(0.00, 0.35)],
        ),
        raising=False,
    )
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    gate = AsyncMock(
        return_value=ResearchVerdict(
            status=ResearchStatus.NEEDS_RESEARCH,
            attempted=True,
            skip_reason="insufficient_corroboration",
        )
    )
    task = ResearchPrewarmTask(store=store, research_gate=gate)

    longshot = SimpleNamespace(
        ticker="KXLONGSHOT-26AUG22",
        title="Will a longshot event happen?",
        rules_primary="Official source.",
        rules_secondary="",
        settlement_sources=(),
        contract_terms_url="https://assets.kalshi.com/contract_terms/LONG.pdf",
        status="open",
        close_time="2099-12-31T23:59:59Z",
        yes_ask_cents=12,
        no_ask_cents=90,
        yes_ask=12,
        no_ask=90,
        yes_prob=0.12,
        yes_price=12,
        yes_ask_size=10.0,
        no_ask_size=10.0,
        open_interest_fp=50.0,
        volume_24h_fp=20.0,
    )
    skipped = await task.process_market(longshot)
    assert skipped.skip_reason == "ask_outside_favorite_band"
    gate.assert_not_awaited()

    favorite = SimpleNamespace(
        ticker="KXFAVORITE-26AUG22",
        title="Will the favorite event happen?",
        rules_primary="Official source.",
        rules_secondary="",
        settlement_sources=(),
        contract_terms_url="https://assets.kalshi.com/contract_terms/FAV.pdf",
        status="open",
        close_time="2099-12-31T23:59:59Z",
        yes_ask_cents=72,
        no_ask_cents=30,
        yes_ask=72,
        no_ask=30,
        yes_prob=0.70,
        yes_price=70,
        yes_ask_size=10.0,
        no_ask_size=10.0,
        open_interest_fp=50.0,
        volume_24h_fp=20.0,
    )
    await task.process_market(favorite)
    assert gate.await_args.kwargs["allow_official_pdf_and_homepage"] is True
    assert gate.await_args.kwargs["prefer_official_sources"] is True
    assert gate.await_args.kwargs["require_decision_grade"] is True
