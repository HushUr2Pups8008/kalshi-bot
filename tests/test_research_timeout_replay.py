from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import sqlite3
from types import SimpleNamespace

import pytest

import analysis.research_gate as research_gate_module
from analysis.research_gate import (
    ResearchEvidence,
    ResearchStatus,
    _select_research_queries,
    build_research_queries,
    run_research_gate,
)
from analysis.research_timeout_replay import (
    ResearchTimeoutReplaySnapshot,
    load_timeout_replay_snapshot,
    replay_persisted_timeout,
)
from tasks.research_dossier import ResearchDossierStore


def _table_counts(db_path):
    with sqlite3.connect(db_path) as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "research_dossiers",
                "research_runs",
                "research_run_queries",
                "research_evidence",
                "research_tasks",
                "research_paper_admissions",
                "research_timeout_diagnostics",
            )
        }


@pytest.mark.asyncio
async def test_counter_adjudication_timeout_is_replayable_but_non_promoting(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    news = SimpleNamespace(headline="Event conditions strengthen", source="Reuters")
    market = SimpleNamespace(
        ticker="KXREPLAYCOUNTER-26JUL13",
        title="Will the event happen by July 13?",
        rules_primary="Reliable reporting determines the market.",
        rules_secondary="",
        settlement_sources=(),
    )
    initial_query_texts = {
        query.query
        for query in _select_research_queries(
            build_research_queries(news, market),
            max_queries=6,
            require_decision_grade=True,
        )
    }
    provider_calls = 0
    adjudication_calls = 0

    async def search_provider(query):
        nonlocal provider_calls
        provider_calls += 1
        if query.query not in initial_query_texts:
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="AP",
                    source_url="https://apnews.com/event-counter",
                    title="AP reports a risk to the event.",
                    snippet="AP reports the event could still fail.",
                    claim_type="disconfirming",
                    supports_direction="no",
                    supports_confidence=0.35,
                    retrieved_at=fresh,
                )
            ]
        return [
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.com/event-support",
                title="Reuters reports the event is likely.",
                snippet="Reuters reports the event is likely.",
                claim_type="supporting",
                supports_direction="yes",
                supports_confidence=0.9,
                retrieved_at=fresh,
            )
        ]

    async def adjudicator(**_kwargs):
        nonlocal adjudication_calls
        adjudication_calls += 1
        if adjudication_calls == 1:
            return {
                "direction": "yes",
                "estimated_probability_yes": 0.7,
                "confidence": 0.8,
                "reason": "Directional evidence supports the YES case.",
            }
        await asyncio.sleep(60)

    verdict = await asyncio.wait_for(
        run_research_gate(
            news,
            market,
            model_direction="neutral",
            model_confidence=0.5,
            model_reason="No keywords.",
            yes_ask=0.51,
            no_ask=0.51,
            live_mode=False,
            search_provider=search_provider,
            adjudicator=adjudicator,
            dossier_store=store,
            require_decision_grade=True,
            research_timeout_seconds=0.2,
        ),
        timeout=1.0,
    )

    assert verdict.status is ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "research_timeout"
    assert verdict.research_timeout_stage == "counter_adjudication"
    assert verdict.research_persisted is True
    assert verdict.research_run_id

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                """
                UPDATE research_timeout_diagnostics
                SET timeout_stage = 'provider_fanout'
                WHERE research_run_id = ?
                """,
                (verdict.research_run_id,),
            )

    counts_before = _table_counts(db_path)
    snapshot = load_timeout_replay_snapshot(db_path, verdict.research_run_id)
    first = replay_persisted_timeout(db_path, verdict.research_run_id)
    second = replay_persisted_timeout(db_path, verdict.research_run_id)

    assert snapshot is not None
    assert snapshot.research_run_id == verdict.research_run_id
    assert snapshot.timeout_stage == "counter_adjudication"
    assert snapshot.counter_evidence_added is True
    assert len(snapshot.queries) > 0
    assert len(snapshot.evidence) >= 2
    with pytest.raises(FrozenInstanceError):
        snapshot.timeout_stage = "provider_fanout"

    assert first == second
    assert first.replayable is True
    assert first.timeout_stage == "counter_adjudication"
    assert first.expected_status == ResearchStatus.CONTINUE_RESEARCHING.value
    assert first.skip_reason == "research_timeout"
    assert first.candidate_eligible is False
    assert first.cache_eligible is False
    assert first.admission_eligible is False
    assert first.query_count == len(snapshot.queries)
    assert first.evidence_count == len(snapshot.evidence)
    assert provider_calls > 0
    assert adjudication_calls == 2
    assert _table_counts(db_path) == counts_before


def test_timeout_replay_rejects_legacy_or_integrity_broken_rows_without_writing(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    legacy = replay_persisted_timeout(db_path, "rr-missing")

    assert legacy.replayable is False
    assert legacy.reason == "timeout_diagnostic_unavailable"
    assert legacy.candidate_eligible is False
    assert legacy.cache_eligible is False
    assert legacy.admission_eligible is False

    snapshot = ResearchTimeoutReplaySnapshot(
        schema_version=1,
        research_run_id="rr-bad-digest",
        market_ticker="KXREPLAY-26JUL13",
        contract_fingerprint="contract-fingerprint",
        timeout_stage="counter_adjudication",
        configured_timeout_seconds=12.0,
        remaining_budget_seconds=0.0,
        observed_market_price=0.51,
        yes_ask=0.51,
        no_ask=0.49,
        require_decision_grade=True,
        live_mode=False,
        counter_evidence_added=True,
        model_direction="yes",
        model_confidence=0.8,
        estimated_probability_yes=0.7,
        model_reason="first adjudication was directional",
        counterclaims=("Counter evidence exists.",),
        open_questions=(),
        queries=(("contract terms", "resolution_source", "rules_source"),),
        evidence=(
            (
                "reputable_secondary",
                "AP",
                "https://apnews.com/counter",
                "Counter report",
                "Counter evidence",
                "disconfirming",
                "no",
                0.35,
                None,
                "2026-07-28T00:00:00Z",
                None,
                None,
                None,
                None,
                None,
                "contract-fingerprint",
                None,
                None,
            ),
        ),
    )
    payload_json = snapshot.canonical_json()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE research_runs (
                research_run_id TEXT PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                skip_reason TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE research_timeout_diagnostics (
                research_run_id TEXT PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                timeout_stage TEXT NOT NULL,
                input_sha256 TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO research_runs VALUES (?, ?, ?)",
            (snapshot.research_run_id, snapshot.market_ticker, "research_timeout"),
        )
        conn.execute(
            "INSERT INTO research_timeout_diagnostics VALUES (?, ?, ?, ?, ?)",
            (
                snapshot.research_run_id,
                snapshot.market_ticker,
                snapshot.timeout_stage,
                "0" * 64,
                payload_json,
            ),
        )

    before = _table_counts_for_partial_db(db_path)
    broken = replay_persisted_timeout(db_path, snapshot.research_run_id)

    assert broken.replayable is False
    assert broken.reason == "timeout_diagnostic_digest_mismatch"
    assert broken.candidate_eligible is False
    assert broken.cache_eligible is False
    assert broken.admission_eligible is False
    assert _table_counts_for_partial_db(db_path) == before


@pytest.mark.asyncio
async def test_timeout_diagnostic_validation_rolls_back_the_entire_run_write(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    snapshot = ResearchTimeoutReplaySnapshot(
        schema_version=1,
        research_run_id="rr-non-timeout",
        market_ticker="KXREPLAY-26JUL13",
        contract_fingerprint="contract-fingerprint",
        timeout_stage="counter_adjudication",
        configured_timeout_seconds=12.0,
        remaining_budget_seconds=0.0,
        observed_market_price=0.51,
        yes_ask=0.51,
        no_ask=0.49,
        require_decision_grade=True,
        live_mode=False,
        counter_evidence_added=True,
        model_direction="yes",
        model_confidence=0.8,
        estimated_probability_yes=0.7,
        model_reason="first adjudication was directional",
        counterclaims=(),
        open_questions=(),
        queries=(),
        evidence=(),
    )

    with pytest.raises(ValueError, match="requires research_timeout"):
        await store.record_research_run(
            snapshot.market_ticker,
            snapshot.research_run_id,
            trigger_headline="Timeout replay validation",
            trigger_source="test",
            attempted=True,
            summary="Not a timeout.",
            verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
            skip_reason="missing_resolution_source",
            contract_fingerprint=snapshot.contract_fingerprint,
            timeout_diagnostic=snapshot,
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM research_timeout_diagnostics").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_timeout_diagnostic_capture_failure_keeps_the_gate_fail_closed(
    monkeypatch,
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    async def hung_search(_query):
        await asyncio.sleep(60)
        return []

    async def unused_adjudicator(**_kwargs):
        raise AssertionError("provider timeout must finish before adjudication")

    def fail_capture(**_kwargs):
        raise RuntimeError("diagnostic serialization unavailable")

    monkeypatch.setattr(
        research_gate_module,
        "capture_timeout_replay_snapshot",
        fail_capture,
    )
    verdict = await asyncio.wait_for(
        run_research_gate(
            SimpleNamespace(headline="Research signal", source="Reuters"),
            SimpleNamespace(
                ticker="KXREPLAYCAPTURE-26JUL13",
                title="Will the event happen by July 13?",
                rules_primary="Reliable reporting determines the market.",
                rules_secondary="",
                settlement_sources=(),
            ),
            model_direction="neutral",
            model_confidence=0.5,
            model_reason="No keywords.",
            yes_ask=0.51,
            no_ask=0.51,
            live_mode=False,
            search_provider=hung_search,
            adjudicator=unused_adjudicator,
            dossier_store=store,
            research_timeout_seconds=0.05,
        ),
        timeout=1.0,
    )

    assert verdict.status is ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "research_timeout"
    assert verdict.research_timeout_stage == "provider_fanout"
    assert verdict.research_persisted is True
    assert verdict.research_run_id
    replay = replay_persisted_timeout(db_path, verdict.research_run_id)
    assert replay.replayable is False
    assert replay.reason == "timeout_diagnostic_unavailable"
    assert replay.candidate_eligible is False
    assert replay.cache_eligible is False
    assert replay.admission_eligible is False


@pytest.mark.asyncio
async def test_timeout_diagnostic_retry_is_idempotent_but_rejects_mismatches(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    snapshot = ResearchTimeoutReplaySnapshot(
        schema_version=1,
        research_run_id="rr-idempotent-timeout",
        market_ticker="KXREPLAY-26JUL13",
        contract_fingerprint="contract-fingerprint",
        timeout_stage="counter_adjudication",
        configured_timeout_seconds=12.0,
        remaining_budget_seconds=0.0,
        observed_market_price=0.51,
        yes_ask=0.51,
        no_ask=0.49,
        require_decision_grade=True,
        live_mode=False,
        counter_evidence_added=True,
        model_direction="yes",
        model_confidence=0.8,
        estimated_probability_yes=0.7,
        model_reason="first adjudication was directional",
        counterclaims=(),
        open_questions=(),
        queries=(),
        evidence=(),
    )
    record_kwargs = {
        "trigger_headline": "Timeout retry validation",
        "trigger_source": "test",
        "attempted": True,
        "summary": "Timed out during counter adjudication.",
        "verdict_status": ResearchStatus.CONTINUE_RESEARCHING.value,
        "skip_reason": "research_timeout",
        "contract_fingerprint": snapshot.contract_fingerprint,
        "timeout_diagnostic": snapshot,
    }

    await store.record_research_run(
        snapshot.market_ticker,
        snapshot.research_run_id,
        **record_kwargs,
    )
    await store.record_research_run(
        snapshot.market_ticker,
        snapshot.research_run_id,
        **record_kwargs,
    )

    with sqlite3.connect(db_path) as conn:
        original = conn.execute(
            """
            SELECT timeout_stage, input_sha256, snapshot_json
            FROM research_timeout_diagnostics
            WHERE research_run_id = ?
            """,
            (snapshot.research_run_id,),
        ).fetchone()
        assert original is not None
        assert (
            conn.execute("SELECT COUNT(*) FROM research_timeout_diagnostics").fetchone()[0]
            == 1
        )

    mismatched = replace(snapshot, timeout_stage="provider_fanout")
    with pytest.raises(sqlite3.IntegrityError, match="mismatch"):
        await store.record_research_run(
            snapshot.market_ticker,
            snapshot.research_run_id,
            **{**record_kwargs, "timeout_diagnostic": mismatched},
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT timeout_stage, input_sha256, snapshot_json
            FROM research_timeout_diagnostics
            WHERE research_run_id = ?
            """,
            (snapshot.research_run_id,),
        ).fetchone() == original


def _table_counts_for_partial_db(db_path):
    with sqlite3.connect(db_path) as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("research_runs", "research_timeout_diagnostics")
        }
