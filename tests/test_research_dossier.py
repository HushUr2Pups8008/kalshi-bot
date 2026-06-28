from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3

import pytest

from analysis.research_gate import (
    ResearchEvidence,
    ResearchQuery,
    ResearchStatus,
    _contract_fingerprint,
    run_research_gate,
    urllib,
)
from tasks.research_dossier import ResearchDossierStore


@pytest.fixture(autouse=True)
def _block_unmocked_research_http(monkeypatch):
    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("research dossier tests must inject search_provider; real HTTP is blocked")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)


@pytest.mark.asyncio
async def test_research_dossier_persists_and_returns_recent_evidence(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    evidence = ResearchEvidence(
        source_class="resolution_source",
        source_name="OPEC",
        source_url="https://opec.org/momr",
        title="MOMR table",
        snippet="Iran crude production secondary sources table.",
        claim_type="resolution",
        supports_direction="yes",
        supports_confidence=0.9,
        contract_fingerprint="contract-v1",
    )

    await store.add_evidence("KXIRANCRUDE-26JUL13-T3.8", "run-1", evidence)
    rows = await store.get_recent_evidence("KXIRANCRUDE-26JUL13-T3.8")

    assert len(rows) == 1
    assert rows[0].source_name == "OPEC"
    assert rows[0].source_class == "resolution_source"
    assert rows[0].source_url == "https://opec.org/momr"
    assert rows[0].contract_fingerprint == "contract-v1"


@pytest.mark.asyncio
async def test_research_dossier_records_run_queries_and_latest_verdict(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    evidence = ResearchEvidence(
        source_class="resolution_source",
        source_name="OPEC",
        source_url="https://opec.org/momr",
        title="MOMR table",
        snippet="Iran crude production secondary sources table.",
        claim_type="resolution",
        supports_direction="yes",
        supports_confidence=0.9,
        contract_fingerprint="contract-v1",
    )
    query = ResearchQuery(
        query="site:opec.org Iran crude production",
        query_intent="resolution_source",
        source_class="resolution_source",
    )

    await store.record_research_run(
        "KXIRANCRUDE-26JUL13-T3.8",
        "run-1",
        trigger_headline="Iran output update",
        trigger_source="Reuters",
        attempted=True,
        summary="Research supports yes.",
        verdict_status="trade_candidate",
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        queries=[query],
        evidence=[evidence],
    )

    with sqlite3.connect(db_path) as conn:
        dossier = conn.execute(
            """
            SELECT last_verdict_status, last_force_side, last_contract_fingerprint
            FROM research_dossiers
            """
        ).fetchone()
        run = conn.execute("SELECT summary FROM research_runs").fetchone()
        stored_query = conn.execute("SELECT query FROM research_run_queries").fetchone()
        stored_evidence = conn.execute(
            "SELECT source_name, contract_fingerprint FROM research_evidence"
        ).fetchone()

    assert dossier == ("trade_candidate", "yes", "contract-v1")
    assert run == ("Research supports yes.",)
    assert stored_query == ("site:opec.org Iran crude production",)
    assert stored_evidence == ("OPEC", "contract-v1")


@pytest.mark.asyncio
async def test_research_dossier_records_run_contract_fingerprint_without_evidence(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    await store.record_research_run(
        "KXIRANCRUDE-26JUL13-T3.8",
        "run-no-evidence",
        trigger_headline="Iran output update",
        trigger_source="Reuters",
        attempted=True,
        summary="No research hits.",
        verdict_status=ResearchStatus.CONTINUE_RESEARCHING.value,
        skip_reason="no_research_hits",
        contract_fingerprint="contract-v1",
    )

    with sqlite3.connect(db_path) as conn:
        dossier = conn.execute(
            "SELECT last_contract_fingerprint FROM research_dossiers"
        ).fetchone()

    assert dossier == ("contract-v1",)


@pytest.mark.asyncio
async def test_research_gate_reuses_dossier_before_fresh_search(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    news = SimpleNamespace(headline="Iran output update", source="Reuters", url="")
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    contract_fingerprint = _contract_fingerprint(market)
    fresh_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    await store.add_evidence(
        "KXIRANCRUDE-26JUL13-T3.8",
        "seed",
        ResearchEvidence(
            source_class="resolution_source",
            source_name="OPEC",
            source_url="https://opec.org/momr",
            title="MOMR table",
            snippet="Iran crude production secondary sources table.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=fresh_ts,
            contract_fingerprint=contract_fingerprint,
        ),
    )
    await store.add_evidence(
        "KXIRANCRUDE-26JUL13-T3.8",
        "seed",
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran output rises",
            snippet="Analysts expect production above the threshold.",
            claim_type="baseline",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=fresh_ts,
            contract_fingerprint=contract_fingerprint,
        ),
    )

    async def no_network(_query):
        raise AssertionError("fresh search should not run when dossier is sufficient")

    async def adjudicator(*, evidence, queries, news, market):
        assert {item.source_name for item in evidence} == {"OPEC", "Reuters"}
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.8,
            "confidence": 0.8,
            "reason": "Cached dossier has settlement and corroborating evidence.",
        }

    verdict = await run_research_gate(
        news,
        market,
        model_direction="neutral",
        model_confidence=0.7,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=no_network,
        adjudicator=adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.force_side == "yes"
    assert len(verdict.evidence) == 2


@pytest.mark.asyncio
async def test_research_gate_refreshes_stale_dossier_before_reuse(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat().replace("+00:00", "Z")
    await store.add_evidence(
        "KXIRANCRUDE-26JUL13-T3.8",
        "old-run-1",
        ResearchEvidence(
            source_class="resolution_source",
            source_name="OPEC",
            source_url="https://opec.org/old-momr",
            title="Old MOMR",
            snippet="Old production table.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=stale_ts,
        ),
    )
    await store.add_evidence(
        "KXIRANCRUDE-26JUL13-T3.8",
        "old-run-2",
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.example.com/old",
            title="Old wire report",
            snippet="Old corroborating report.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.7,
            retrieved_at=stale_ts,
        ),
    )

    async def fresh_search(_query):
        return [
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.org/fresh-momr",
                title="Fresh MOMR",
                snippet="Fresh settlement-source production signal.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.85,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.example.com/fresh",
                title="Fresh report",
                snippet="Fresh report confirms production signal.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.7,
            )
        ]

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.8,
            "confidence": 0.8,
            "reason": "Fresh evidence confirms yes.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude production fresh trigger", source="Example"),
        SimpleNamespace(
            ticker="KXIRANCRUDE-26JUL13-T3.8",
            title="Will Iran crude oil production be at least 3.8M bpd?",
            rules_primary="OPEC Monthly Oil Market Report resolves this market.",
            rules_secondary="Later revisions excluded.",
            settlement_sources=(),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keyword hit.",
        yes_ask=0.6,
        no_ask=0.4,
        live_mode=False,
        search_provider=fresh_search,
        adjudicator=adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert "https://reuters.example.com/fresh" in {item.source_url for item in verdict.evidence}


@pytest.mark.asyncio
async def test_research_gate_refreshes_wrong_contract_dossier_before_reuse(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    fresh_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for source_class, name, url in (
        ("resolution_source", "OPEC", "https://opec.org/old-contract"),
        ("reputable_secondary", "Reuters", "https://reuters.example.com/old-contract"),
    ):
        await store.add_evidence(
            "KXIRANCRUDE-26JUL13-T3.8",
            f"old-contract-{name}",
            ResearchEvidence(
                source_class=source_class,
                source_name=name,
                source_url=url,
                title="Old contract evidence",
                snippet="Fresh evidence for a different threshold/date contract.",
                claim_type="resolution" if source_class == "resolution_source" else "corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
                retrieved_at=fresh_ts,
                contract_fingerprint="old-contract-window",
            ),
        )

    async def fresh_search(_query):
        return [
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.org/current-contract",
                title="Current contract report",
                snippet="Current threshold/date contract evidence.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.85,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.example.com/current-contract",
                title="Current contract corroboration",
                snippet="Current threshold/date corroborating evidence.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.75,
            ),
        ]

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.8,
            "confidence": 0.8,
            "reason": "Current-contract evidence found.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran current production trigger", source="Example"),
        SimpleNamespace(
            ticker="KXIRANCRUDE-26JUL13-T3.8",
            title="Will Iran crude oil production be at least 3.8M bpd?",
            rules_primary="OPEC Monthly Oil Market Report for June 2026 resolves this market.",
            rules_secondary="Later revisions excluded.",
            settlement_sources=(),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keyword hit.",
        yes_ask=0.6,
        no_ask=0.4,
        live_mode=False,
        search_provider=fresh_search,
        adjudicator=adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert "https://opec.org/current-contract" in {item.source_url for item in verdict.evidence}


@pytest.mark.asyncio
async def test_research_gate_refreshes_old_published_article_even_if_retrieved_now(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC Monthly Oil Market Report for June 2026 resolves this market.",
        rules_secondary="Later revisions excluded.",
        settlement_sources=(),
    )
    contract_fingerprint = _contract_fingerprint(market)
    retrieved_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    old_published = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace(
        "+00:00",
        "Z",
    )
    for source_class, name, url in (
        ("resolution_source", "OPEC", "https://opec.org/old-published"),
        ("reputable_secondary", "Reuters", "https://reuters.example.com/old-published"),
    ):
        await store.add_evidence(
            "KXIRANCRUDE-26JUL13-T3.8",
            f"old-published-{name}",
            ResearchEvidence(
                source_class=source_class,
                source_name=name,
                source_url=url,
                title="Old published article",
                snippet="Old article fetched recently.",
                claim_type="resolution" if source_class == "resolution_source" else "corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
                published_at=old_published,
                retrieved_at=retrieved_now,
                contract_fingerprint=contract_fingerprint,
            ),
        )

    async def fresh_search(_query):
        return [
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.org/current-published",
                title="Current report",
                snippet="Current settlement-source evidence.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.85,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.example.com/current-published",
                title="Current corroboration",
                snippet="Current corroborating evidence.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.75,
            ),
        ]

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.8,
            "confidence": 0.8,
            "reason": "Current published evidence found.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran current production trigger", source="Example"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keyword hit.",
        yes_ask=0.6,
        no_ask=0.4,
        live_mode=False,
        search_provider=fresh_search,
        adjudicator=adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert "https://opec.org/current-published" in {item.source_url for item in verdict.evidence}
