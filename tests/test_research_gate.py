from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from analysis.research_gate import (
    ResearchEvidence,
    ResearchQuery,
    ResearchStatus,
    _contract_fingerprint,
    _direct_source_targets,
    urllib,
    _rss_search,
    build_research_queries,
    decide_research_verdict,
    run_research_gate,
)
from kalshi.series_metadata import SettlementSource
from tasks.research_dossier import ResearchDossierStore


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


def test_generic_market_query_pack_adds_contract_terms_context_fallback():
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
        query.source_class == "rules_source"
        and query.query_intent == "contract_terms"
        and "site:kalshi.com" in query.query
        for query in queries
    )
    assert any(
        query.source_class == "official_primary"
        and query.query_intent == "official_resolution_context"
        and "agreement is signed" in query.query
        for query in queries
    )


def test_direct_source_targets_use_domain_only_settlement_sources():
    market = SimpleNamespace(
        contract_terms_url="",
        settlement_sources=(SettlementSource(label="AP", domain="apnews.com"),),
    )

    assert _direct_source_targets(market) == [
        ("https://apnews.com", "resolution_source", "settlement_source")
    ]


def test_rss_search_does_not_treat_wrong_domain_as_resolution_source(monkeypatch):
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>Reuters report on OPEC data</title>
      <link>https://reuters.com/world/oil-output</link>
      <source url="https://reuters.com">Reuters</source>
      <description>Secondary reporting mentions OPEC production.</description>
      <pubDate>Sat, 27 Jun 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return rss

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _rss_search(
        ResearchQuery(
            query="site:opec.org Monthly Oil Market Report Iran",
            query_intent="resolution_source",
            source_class="resolution_source",
        )
    )

    assert len(evidence) == 1
    assert evidence[0].source_url == "https://reuters.com/world/oil-output"
    assert evidence[0].source_class == "reputable_secondary"


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


def test_single_resolution_source_requires_independent_corroboration():
    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Official source",
                source_url="https://official.example.com/result",
                title="Official result",
                snippet="Official data supports YES.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.95,
            )
        ],
        queries=[],
        model_direction="yes",
        model_confidence=0.9,
        model_reason="Official source supports the YES side.",
        estimated_probability_yes=0.72,
        yes_ask=0.5,
        no_ask=0.5,
        live_mode=False,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "insufficient_corroboration"
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


def test_research_verdict_log_fields_include_probability_and_freshness_span():
    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.example.com/current",
                title="Current report",
                snippet="Official data supports YES.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.9,
                published_at="2026-06-27T10:00:00Z",
                retrieved_at="2026-06-27T10:05:00Z",
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.example.com/current",
                title="Wire report",
                snippet="Wire report supports YES.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
                published_at="2026-06-27T11:00:00Z",
                retrieved_at="2026-06-27T11:03:00Z",
            ),
        ],
        queries=[],
        model_direction="yes",
        model_confidence=0.8,
        model_reason="Fresh settlement-backed evidence supports yes.",
        estimated_probability_yes=0.72,
        yes_ask=0.6,
        no_ask=0.4,
        live_mode=False,
    )

    fields = verdict.log_fields()

    assert fields["research_model_probability_yes"] == 0.72
    assert fields["research_min_published_at"] == "2026-06-27T10:00:00+00:00"
    assert fields["research_max_published_at"] == "2026-06-27T11:00:00+00:00"
    assert fields["research_min_retrieved_at"] == "2026-06-27T10:05:00+00:00"
    assert fields["research_max_retrieved_at"] == "2026-06-27T11:03:00+00:00"


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
async def test_cache_only_research_gate_promotes_from_vetted_cached_dossier(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="OPEC",
            source_url="https://opec.org/momr",
            title="MOMR table",
            snippet="Iran crude production secondary sources table.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran production rises",
            snippet="Analysts expect Iran crude production to exceed the threshold.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
    ]
    await store.record_research_run(
        market.ticker,
        "rr-vetted-cache",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached vetted research supports yes.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        evidence=evidence,
    )

    async def fail_search(_query):
        raise AssertionError("cache-only production path must not search")

    async def fail_direct_fetcher(*_args):
        raise AssertionError("cache-only production path must not fetch")

    async def fail_adjudicator(**_kwargs):
        raise AssertionError("cache-only production path must not call LLM")

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=True,
        search_provider=fail_search,
        direct_fetcher=fail_direct_fetcher,
        adjudicator=fail_adjudicator,
        dossier_store=store,
        cache_only=True,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.force_side == "yes"
    assert verdict.estimated_probability == pytest.approx(0.8)
    assert len(verdict.evidence) == 2
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == "rr-vetted-cache"
    assert verdict.research_run_id is None


@pytest.mark.asyncio
async def test_researched_no_edge_invalidates_stale_cached_candidate(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="OPEC",
            source_url="https://opec.org/momr",
            title="MOMR table",
            snippet="Iran crude production secondary sources table.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran production rises",
            snippet="Analysts expect Iran crude production to exceed the threshold.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
    ]
    await store.record_research_run(
        market.ticker,
        "rr-vetted-cache",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached vetted research supports yes.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        evidence=evidence,
    )

    async def fail_search(_query):
        raise AssertionError("sufficient cached evidence should avoid fresh search")

    async def no_edge_adjudicator(*, evidence, queries, news, market):
        return {
            "direction": "yes",
            "confidence": 0.8,
            "estimated_probability_yes": 0.53,
            "reason": "Evidence still supports YES but edge no longer clears costs.",
        }

    no_edge = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=fail_search,
        adjudicator=no_edge_adjudicator,
        dossier_store=store,
    )

    assert no_edge.status == ResearchStatus.RESEARCHED_SKIP_NO_EDGE
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == no_edge.research_run_id
    assert snapshot.last_verdict_status == ResearchStatus.RESEARCHED_SKIP_NO_EDGE.value

    cache_only = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=True,
        search_provider=fail_search,
        adjudicator=no_edge_adjudicator,
        dossier_store=store,
        cache_only=True,
    )

    assert cache_only.status == ResearchStatus.CONTINUE_RESEARCHING
    assert cache_only.skip_reason == "cached_dossier_unvetted"


@pytest.mark.asyncio
async def test_cached_evidence_candidate_records_matching_full_proof_run(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="OPEC",
            source_url="https://opec.org/momr",
            title="MOMR table",
            snippet="Iran crude production secondary sources table.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran production rises",
            snippet="Analysts expect Iran crude production to exceed the threshold.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
    ]
    await store.record_research_run(
        market.ticker,
        "rr-vetted-cache",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached vetted research supports yes.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        evidence=evidence,
    )

    async def fail_search(_query):
        raise AssertionError("sufficient cached evidence should avoid fresh search")

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=fail_search,
        adjudicator=_fake_adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == verdict.research_run_id
    with sqlite3.connect(db_path) as conn:
        proof_rows = conn.execute(
            """
            SELECT source_class
            FROM research_evidence
            WHERE research_run_id = ?
            ORDER BY source_class
            """,
            (snapshot.last_research_run_id,),
        ).fetchall()

    assert proof_rows == [("reputable_secondary",), ("resolution_source",)]


@pytest.mark.asyncio
async def test_mixed_cached_and_fresh_candidate_records_full_proof_run(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    await store.record_research_run(
        market.ticker,
        "rr-resolution-only",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached resolution source only.",
        verdict_status=ResearchStatus.CONTINUE_RESEARCHING.value,
        skip_reason="insufficient_corroboration",
        contract_fingerprint=_contract_fingerprint(market),
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.org/momr",
                title="MOMR table",
                snippet="Iran crude production secondary sources table.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.9,
                retrieved_at=retrieved_at,
                contract_fingerprint=_contract_fingerprint(market),
            )
        ],
    )

    async def one_fresh_secondary(_query):
        return [
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.com/iran-production",
                title="Iran production rises",
                snippet="Analysts expect Iran crude production to exceed the threshold.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
                retrieved_at=retrieved_at,
                contract_fingerprint=_contract_fingerprint(market),
            )
        ]

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=one_fresh_secondary,
        adjudicator=_fake_adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == verdict.research_run_id
    with sqlite3.connect(db_path) as conn:
        proof_rows = conn.execute(
            """
            SELECT source_class
            FROM research_evidence
            WHERE research_run_id = ?
            ORDER BY source_class
            """,
            (snapshot.last_research_run_id,),
        ).fetchall()

    assert proof_rows == [("reputable_secondary",), ("resolution_source",)]


@pytest.mark.asyncio
async def test_cache_only_research_gate_fails_closed_without_sufficient_cache(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def fail_search(_query):
        raise AssertionError("cache-only production path must not search")

    async def fail_direct_fetcher(*_args):
        raise AssertionError("cache-only production path must not fetch")

    async def fail_adjudicator(**_kwargs):
        raise AssertionError("cache-only production path must not call LLM")

    verdict = await run_research_gate(
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
        live_mode=True,
        search_provider=fail_search,
        direct_fetcher=fail_direct_fetcher,
        adjudicator=fail_adjudicator,
        dossier_store=store,
        cache_only=True,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.attempted is False
    assert verdict.skip_reason == "cached_dossier_insufficient"
    snapshot = await store.get_dossier_snapshot("KXIRANCRUDE-26JUL13-T3.8")
    assert snapshot is None


@pytest.mark.asyncio
async def test_run_research_gate_persists_contract_fingerprint_without_evidence(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    expected_fingerprint = _contract_fingerprint(market)

    async def no_hits(_query):
        return []

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=no_hits,
        dossier_store=store,
    )

    snapshot = await store.get_dossier_snapshot(market.ticker)
    fields = verdict.log_fields()

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "no_research_hits"
    assert snapshot is not None
    assert snapshot.last_contract_fingerprint == expected_fingerprint
    assert fields["research_contract_fingerprint"] == expected_fingerprint


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


@pytest.mark.asyncio
async def test_run_research_gate_exposes_persisted_run_id(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )

    verdict = await run_research_gate(
        SimpleNamespace(
            headline="Iran crude output rises sharply",
            source="Reuters",
            url="https://reuters.com/iran-production",
        ),
        market,
        model_direction="neutral",
        model_confidence=0.85,
        model_reason="No relevant keywords found.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=_fake_search,
        adjudicator=_fake_adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.research_run_id
    assert verdict.research_persisted is True
    fields = verdict.log_fields()
    assert fields["research_run_id"] == verdict.research_run_id
    assert fields["research_persisted"] is True
    assert fields["research_contract_fingerprint"] == _contract_fingerprint(market)


@pytest.mark.asyncio
async def test_run_research_gate_uses_unique_run_id_per_attempt(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
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

    first = await run_research_gate(
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
        dossier_store=store,
    )
    second = await run_research_gate(
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
        dossier_store=store,
    )

    assert first.research_run_id
    assert second.research_run_id
    assert first.research_run_id != second.research_run_id


@pytest.mark.asyncio
async def test_run_research_gate_surfaces_persistence_failure():
    class FailingStore:
        async def get_recent_evidence(self, _ticker):
            return []

        async def record_research_run(self, *_args, **_kwargs):
            raise RuntimeError("sqlite locked")

    verdict = await run_research_gate(
        SimpleNamespace(
            headline="Iran crude output rises sharply",
            source="Reuters",
            url="https://reuters.com/iran-production",
        ),
        SimpleNamespace(
            ticker="KXIRANCRUDE-26JUL13-T3.8",
            title="Will Iran crude oil production be at least 3.8M bpd?",
            rules_primary="OPEC MOMR secondary sources decide the market.",
            rules_secondary="Later revisions ignored.",
            settlement_sources=(),
        ),
        model_direction="neutral",
        model_confidence=0.85,
        model_reason="No relevant keywords found.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=_fake_search,
        adjudicator=_fake_adjudicator,
        dossier_store=FailingStore(),
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.research_run_id
    assert verdict.research_persisted is False
    assert verdict.research_persistence_error == "sqlite locked"
    assert verdict.log_fields()["research_persisted"] is False


@pytest.mark.asyncio
async def test_run_research_gate_surfaces_direct_fetch_failure():
    async def direct_fetcher(_url, _source_class, _claim_type):
        raise RuntimeError("official source unavailable")

    async def search_provider(query):
        assert query.source_class != "resolution_source"
        return [
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.example.com/ceasefire",
                title="Ceasefire agreement signed",
                snippet="Officials confirm the agreement was signed before the deadline.",
                claim_type="corroboration",
            )
        ]

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
        direct_fetcher=direct_fetcher,
        adjudicator=_fake_adjudicator,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "missing_resolution_source"
    assert len(verdict.research_direct_fetch_failures) == 1
    assert verdict.log_fields()["research_direct_fetch_failure_count"] == 1


@pytest.mark.asyncio
async def test_contract_terms_direct_fetch_alone_does_not_satisfy_resolution_evidence():
    async def search_provider(query):
        assert query.source_class != "resolution_source"
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

    async def direct_fetcher(url, source_class, claim_type):
        assert url == "https://kalshi.com/markets/KXCEASEFIRE-26JUL01"
        return ResearchEvidence(
            source_class=source_class,
            source_name="Kalshi Contract",
            source_url=url,
            title="Contract terms",
            snippet="Official public announcements decide the ceasefire agreement market.",
            claim_type=claim_type,
            supports_direction="neutral",
            supports_confidence=0.0,
        )

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
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "missing_resolution_source"
    assert "https://kalshi.com/markets/KXCEASEFIRE-26JUL01" in {
        item.source_url for item in verdict.evidence
    }


@pytest.mark.asyncio
async def test_run_research_gate_reports_provider_exception():
    async def failing_search(_query):
        raise RuntimeError("rss unavailable")

    verdict = await run_research_gate(
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
        search_provider=failing_search,
        adjudicator=_fake_adjudicator,
    )

    assert verdict.status == ResearchStatus.RESEARCH_PROVIDER_ERROR
    assert verdict.skip_reason == "research_provider_error"


@pytest.mark.asyncio
async def test_noncritical_provider_error_does_not_block_sufficient_evidence():
    async def search_provider(query):
        if query.query_intent == "corroboration":
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="Reuters",
                    source_url="https://reuters.example.com/opec-corroboration",
                    title="Reuters confirms OPEC table",
                    snippet="OPEC table supports the reported production figure.",
                    claim_type="corroboration",
                    supports_direction="yes",
                    supports_confidence=0.8,
                )
            ]
        raise RuntimeError("one non-critical query failed")

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="OPEC",
            source_url=url,
            title="OPEC monthly report",
            snippet="Official table reports Iran crude production above threshold.",
            claim_type=claim_type,
            supports_direction="yes",
            supports_confidence=0.9,
        )

    verdict = await run_research_gate(
        SimpleNamespace(
            headline="Reuters confirms OPEC table supports Iran crude production",
            source="Reuters",
        ),
        SimpleNamespace(
            ticker="KXIRANCRUDE-26JUL13-T3.8",
            title="Will Iran crude oil production be at least 3.8M bpd?",
            rules_primary="OPEC MOMR secondary sources decide the market.",
            rules_secondary="Later revisions ignored.",
            settlement_sources=(
                SimpleNamespace(label="OPEC", url="https://opec.org/momr"),
            ),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=_fake_adjudicator,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.force_side == "yes"


@pytest.mark.asyncio
async def test_run_research_gate_reports_adjudicator_exception():
    async def failing_adjudicator(**_kwargs):
        raise RuntimeError("ollama unavailable")

    verdict = await run_research_gate(
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
        search_provider=_fake_search,
        adjudicator=failing_adjudicator,
    )

    assert verdict.status == ResearchStatus.RESEARCH_ADJUDICATOR_ERROR
    assert verdict.skip_reason == "research_adjudicator_error"


@pytest.mark.asyncio
async def test_run_research_gate_times_out_hung_search_provider(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )

    async def hung_search(_query):
        await asyncio.sleep(60)
        return []

    verdict = await asyncio.wait_for(
        run_research_gate(
            SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
            market,
            model_direction="neutral",
            model_confidence=0.5,
            model_reason="No keywords.",
            yes_ask=0.51,
            no_ask=0.51,
            live_mode=False,
            search_provider=hung_search,
            adjudicator=_fake_adjudicator,
            dossier_store=store,
            research_timeout_seconds=0.05,
        ),
        timeout=1.0,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "research_timeout"
    assert verdict.research_run_id
    assert verdict.research_persisted is True
    fields = verdict.log_fields()
    assert fields["research_contract_fingerprint"] == _contract_fingerprint(market)
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_contract_fingerprint == _contract_fingerprint(market)


@pytest.mark.asyncio
async def test_run_research_gate_timeout_preserves_vetted_dossier_snapshot(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    await store.record_research_run(
        market.ticker,
        "rr-vetted-timeout",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached vetted research supports yes.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        contract_fingerprint=_contract_fingerprint(market),
    )

    async def hung_search(_query):
        await asyncio.sleep(60)
        return []

    verdict = await asyncio.wait_for(
        run_research_gate(
            SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
            market,
            model_direction="neutral",
            model_confidence=0.5,
            model_reason="No keywords.",
            yes_ask=0.51,
            no_ask=0.51,
            live_mode=False,
            search_provider=hung_search,
            adjudicator=_fake_adjudicator,
            dossier_store=store,
            research_timeout_seconds=0.05,
        ),
        timeout=1.0,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "research_timeout"
    assert verdict.research_run_id
    assert verdict.research_run_id != "rr-vetted-timeout"
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == "rr-vetted-timeout"
    assert snapshot.last_verdict_status == ResearchStatus.TRADE_CANDIDATE.value
    assert snapshot.last_force_side == "yes"


@pytest.mark.asyncio
async def test_run_research_gate_enforces_end_to_end_timeout_budget():
    async def slow_direct_fetcher(url, source_class, claim_type):
        await asyncio.sleep(0.04)
        return ResearchEvidence(
            source_class=source_class,
            source_name=url,
            source_url=url,
            title="Slow source",
            snippet="Slow source",
            claim_type=claim_type,
        )

    started = asyncio.get_running_loop().time()
    verdict = await run_research_gate(
        SimpleNamespace(headline="Slow research target", source="Reuters"),
        SimpleNamespace(
            ticker="KXSLOW-26JUL13",
            title="Will slow source resolve?",
            rules_primary="Two official sources decide this market.",
            rules_secondary="Later revisions ignored.",
            settlement_sources=(
                SimpleNamespace(label="Source A", url="https://source-a.example.com"),
                SimpleNamespace(label="Source B", url="https://source-b.example.com"),
            ),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=_fake_search,
        direct_fetcher=slow_direct_fetcher,
        adjudicator=_fake_adjudicator,
        research_timeout_seconds=0.05,
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "research_timeout"
    assert elapsed < 0.10


@pytest.mark.asyncio
async def test_generic_market_contract_terms_fallback_remains_non_promotable():
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

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "missing_resolution_source"
