from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from analysis.research_gate import (
    ResearchEvidence,
    ResearchStatus,
    urllib,
    build_research_queries,
    decide_research_verdict,
    run_research_gate,
)


@pytest.fixture(autouse=True)
def _block_unmocked_research_http(monkeypatch):
    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("research-gate tests must inject search_provider; real HTTP is blocked")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)


def test_crude_oil_query_pack_targets_resolution_and_contradictions():
    news = SimpleNamespace(
        headline=(
            "Iran exports 2,300% more oil in just 6 days, still has "
            "3.6 crore barrels on water"
        ),
        source="Inshorts",
        url="https://example.com/iran-oil",
    )
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran's average daily crude oil production be at least 3.8M bpd?",
        rules_primary=(
            "If Iran's average daily crude oil production for June 2026 is "
            "at least 3.8M bpd, as reported in the OPEC Monthly Oil Market Report, "
            "then the market resolves to Yes."
        ),
        rules_secondary=(
            "Underlying is Iran crude oil production based on OPEC secondary "
            "sources. Later revisions will not be considered."
        ),
        settlement_sources=(),
    )

    queries = build_research_queries(news, market)
    rendered = "\n".join(query.query for query in queries)
    intents = {query.query_intent for query in queries}

    assert "site:opec.org" in rendered
    assert "Iran" in rendered
    assert "crude oil production" in rendered
    assert "exports" in rendered
    assert "inventory" in rendered or "barrels on water" in rendered
    assert {"resolution_source", "reputable_secondary", "contradiction_check"} <= intents


def test_crude_oil_query_pack_uses_contract_reporting_window():
    news = SimpleNamespace(
        headline="Iran output update",
        source="Example",
        url="https://example.com/iran-oil",
    )
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26AUG13-T3.8",
        title="Will Iran's average daily crude oil production be at least 3.8M bpd?",
        rules_primary=(
            "If Iran's average daily crude oil production for July 2026 is "
            "at least 3.8M bpd, as reported in the OPEC Monthly Oil Market Report, "
            "then the market resolves to Yes."
        ),
        rules_secondary="Underlying is Iran crude oil production based on OPEC secondary sources.",
        settlement_sources=(),
    )

    queries = build_research_queries(news, market)
    rendered = "\n".join(query.query for query in queries)

    assert "July 2026" in rendered
    assert "June 2026" not in rendered


def test_generic_market_query_pack_adds_contract_terms_resolution_fallback():
    news = SimpleNamespace(
        headline="Officials say ceasefire agreement could be signed this week",
        source="Reuters",
        url="https://reuters.example.com/ceasefire",
    )
    market = SimpleNamespace(
        ticker="KXCEASEFIRE-26JUL01",
        title="Will a ceasefire agreement be signed by July 1?",
        rules_primary="The market resolves Yes if an agreement is signed by the deadline.",
        rules_secondary="The determination will be based on official public announcements.",
        settlement_sources=(),
        contract_terms_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
    )

    queries = build_research_queries(news, market)

    assert any(
        query.source_class == "resolution_source"
        and query.query_intent == "resolution_source"
        and "site:kalshi.com" in query.query
        for query in queries
    )


def test_inconsistent_research_reason_continues_researching():
    evidence = [
        ResearchEvidence(
            source_class="other",
            source_name="Inshorts",
            source_url="https://example.com/iran-oil",
            title="Iran exports surge",
            snippet="Exports rose sharply, but this may be barrels on water.",
            claim_type="leading_indicator",
            supports_direction="neutral",
            supports_confidence=0.4,
        )
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        model_direction="no",
        model_confidence=0.75,
        model_reason=(
            "The headline indicates a significant increase in Iran's oil exports, "
            "which could potentially lead to higher average daily crude oil production."
        ),
        yes_ask=0.02,
        no_ask=0.98,
        live_mode=True,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "direction_reason_conflict"
    assert verdict.force_side is None


def test_live_tail_risk_blocks_chosen_no_side():
    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Resolution Source",
                source_url="https://example.com/resolution",
                title="Resolution source table",
                snippet="Official data supports NO.",
                claim_type="resolution",
                supports_direction="no",
                supports_confidence=0.95,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.example.com/no",
                title="Wire confirmation",
                snippet="Wire report confirms the official data is below the threshold.",
                claim_type="corroboration",
                supports_direction="no",
                supports_confidence=0.8,
            ),
        ],
        queries=[],
        model_direction="no",
        model_confidence=0.95,
        model_reason="Official source is below the threshold.",
        estimated_probability_yes=0.05,
        yes_ask=0.98,
        no_ask=0.02,
        live_mode=True,
    )

    assert verdict.status == ResearchStatus.HARD_CAPITAL_BLOCK
    assert verdict.skip_reason == "no_trade_capital_protection"


def test_direction_confidence_without_probability_continues_researching():
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="Resolution Source",
            source_url="https://example.com/resolution",
            title="Resolution source table",
            snippet="Official data supports YES.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.example.com/yes",
            title="Wire confirmation",
            snippet="Wire report supports YES.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.8,
        ),
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=[],
        model_direction="yes",
        model_confidence=0.8,
        model_reason="Evidence points yes with high confidence.",
        yes_ask=0.6,
        no_ask=0.4,
        live_mode=False,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "missing_estimated_probability"


async def _fake_search(query):
    if query.query_intent == "resolution_source":
        return [
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.org/momr",
                title="MOMR table",
                snippet="Iran crude production secondary sources table.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.9,
            )
        ]
    return [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran production rises",
            snippet="Analysts expect Iran crude production to exceed the threshold.",
            claim_type="baseline",
            supports_direction="yes",
            supports_confidence=0.8,
        )
    ]


async def _fake_adjudicator(*, evidence, queries, news, market):
    return {
        "direction": "yes",
        "confidence": 0.8,
        "estimated_probability_yes": 0.8,
        "reason": "Resolution-source and wire evidence support higher production.",
    }


@pytest.mark.asyncio
async def test_run_research_gate_can_promote_neutral_to_trade_candidate():
    news = SimpleNamespace(
        headline="Iran crude output rises sharply",
        source="Reuters",
        url="https://reuters.com/iran-production",
    )
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )

    verdict = await run_research_gate(
        news,
        market,
        model_direction="neutral",
        model_confidence=0.85,
        model_reason="No relevant keywords found.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=_fake_search,
        adjudicator=_fake_adjudicator,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.force_side == "yes"
    assert verdict.estimated_probability == 0.8


@pytest.mark.asyncio
async def test_run_research_gate_times_out_hung_search_provider():
    async def hung_search(_query):
        await asyncio.sleep(60)
        return []

    verdict = await asyncio.wait_for(
        run_research_gate(
            SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
            SimpleNamespace(
                ticker="KXIRANCRUDE-26JUL13-T3.8",
                title="Will Iran crude oil production be at least 3.8M bpd?",
                rules_primary="OPEC MOMR secondary sources decide the market.",
                rules_secondary="Later revisions ignored.",
                settlement_sources=(),
            ),
            model_direction="neutral",
            model_confidence=0.5,
            model_reason="No keywords.",
            yes_ask=0.51,
            no_ask=0.51,
            live_mode=False,
            search_provider=hung_search,
            adjudicator=_fake_adjudicator,
            research_timeout_seconds=0.05,
        ),
        timeout=1.0,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "research_timeout"


@pytest.mark.asyncio
async def test_generic_market_contract_terms_fallback_can_promote_candidate():
    async def search_provider(query):
        if query.source_class == "resolution_source":
            return [
                ResearchEvidence(
                    source_class="resolution_source",
                    source_name="Kalshi Contract",
                    source_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
                    title="Contract terms",
                    snippet="Official public announcements decide the ceasefire agreement market.",
                    claim_type="resolution",
                    supports_direction="yes",
                    supports_confidence=0.85,
                )
            ]
        return [
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.example.com/ceasefire",
                title="Ceasefire agreement signed",
                snippet="Officials confirm the agreement was signed before the deadline.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
            )
        ]

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.78,
            "confidence": 0.8,
            "reason": "Contract terms and wire evidence support YES.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(
            headline="Officials confirm ceasefire agreement was signed",
            source="Reuters",
            url="https://reuters.example.com/ceasefire",
        ),
        SimpleNamespace(
            ticker="KXCEASEFIRE-26JUL01",
            title="Will a ceasefire agreement be signed by July 1?",
            rules_primary="The market resolves Yes if an agreement is signed by the deadline.",
            rules_secondary="The determination will be based on official public announcements.",
            settlement_sources=(),
            contract_terms_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.6,
        no_ask=0.4,
        live_mode=False,
        search_provider=search_provider,
        adjudicator=adjudicator,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.force_side == "yes"
