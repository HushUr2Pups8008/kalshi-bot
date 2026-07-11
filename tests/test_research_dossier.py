from __future__ import annotations

import asyncio
import json
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


class _TrackingConnection:
    def __init__(self) -> None:
        self.entered = 0
        self.exit_args = None
        self.closed = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exit_args = (exc_type, exc, traceback)
        return False

    def close(self) -> None:
        self.closed += 1


def test_connection_context_commits_and_closes(monkeypatch, tmp_path):
    store = ResearchDossierStore(tmp_path / "research.db")
    connection = _TrackingConnection()
    monkeypatch.setattr(store, "_connect", lambda: connection)

    with store._connection() as yielded:
        assert yielded is connection

    assert connection.entered == 1
    assert connection.exit_args == (None, None, None)
    assert connection.closed == 1


def test_connection_context_rolls_back_and_closes(monkeypatch, tmp_path):
    store = ResearchDossierStore(tmp_path / "research.db")
    connection = _TrackingConnection()
    monkeypatch.setattr(store, "_connect", lambda: connection)

    with pytest.raises(RuntimeError, match="boom"):
        with store._connection():
            raise RuntimeError("boom")

    assert connection.exit_args[0] is RuntimeError
    assert connection.closed == 1


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
async def test_research_dossier_initialize_is_safe_under_concurrency(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")

    await asyncio.gather(*(store.initialize() for _ in range(5)))

    snapshot = await store.get_dossier_snapshot("KX-MISSING")
    assert snapshot is None


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
async def test_research_dossier_persists_decision_grade_price_edge_and_task_state(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="Official",
            source_url="https://official.example.com/final",
            title="Official notice",
            snippet="Official notice supports yes.",
            claim_type="settlement",
            supports_direction="yes",
            supports_confidence=0.9,
            contract_fingerprint="contract-v1",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.example.com/report",
            title="Reuters report",
            snippet="Reuters independently supports yes.",
            claim_type="settlement",
            supports_direction="yes",
            supports_confidence=0.85,
            contract_fingerprint="contract-v1",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="AP",
            source_url="https://ap.example.com/counter",
            title="AP countercase",
            snippet="AP reports a ratification risk.",
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.7,
            contract_fingerprint="contract-v1",
        ),
    ]
    queries = [
        ResearchQuery(
            query="official ceasefire final",
            query_intent="official_resolution",
            source_class="resolution_source",
        ),
        ResearchQuery(
            query="ceasefire countercase",
            query_intent="disconfirming",
            source_class="reputable_secondary",
        ),
    ]

    await store.record_research_run(
        "KXCEASEFIRE-26JUL01",
        "run-decision-grade",
        trigger_headline="Agreement signed",
        trigger_source="Reuters",
        contract_question="Will a ceasefire agreement be signed by July 1?",
        attempted=True,
        summary="Official notice, Reuters support, and AP countercase produce YES edge.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.68,
        confidence=0.82,
        contract_fingerprint="contract-v1",
        market_price=0.55,
        estimated_edge=0.12,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        decision_grade_reasons=["edge_recomputed", "counter_evidence_present"],
        open_questions=["Would ratification be required?"],
        counterclaims=["AP says ratification could fail."],
        queries=queries,
        evidence=evidence,
    )

    snapshot = await store.get_dossier_snapshot("KXCEASEFIRE-26JUL01")
    task = await store.get_research_task_snapshot("KXCEASEFIRE-26JUL01")

    with sqlite3.connect(db_path) as conn:
        run = conn.execute(
            """
            SELECT market_price, estimated_edge, decision_grade_status,
                   decision_grade_reasons_json, open_questions_json,
                   counterclaims_json, contract_question
            FROM research_runs
            WHERE research_run_id = 'run-decision-grade'
            """
        ).fetchone()
        dossier_contract_question = conn.execute(
            """
            SELECT contract_question
            FROM research_dossiers
            WHERE market_ticker = 'KXCEASEFIRE-26JUL01'
            """
        ).fetchone()[0]

    assert snapshot is not None
    assert snapshot.last_market_price == pytest.approx(0.55)
    assert snapshot.last_estimated_edge == pytest.approx(0.12)
    assert snapshot.last_decision_grade_status == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert task is not None
    assert task.state == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert task.terminal_reason is None
    assert run[0] == pytest.approx(0.55)
    assert run[1] == pytest.approx(0.12)
    assert run[2] == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert json.loads(run[3]) == ["edge_recomputed", "counter_evidence_present"]
    assert json.loads(run[4]) == ["Would ratification be required?"]
    assert json.loads(run[5]) == ["AP says ratification could fail."]
    assert run[6] == "Will a ceasefire agreement be signed by July 1?"
    assert snapshot.contract_question == "Will a ceasefire agreement be signed by July 1?"
    assert dossier_contract_question == "Will a ceasefire agreement be signed by July 1?"


@pytest.mark.asyncio
async def test_research_dossier_demotes_decision_grade_without_reliable_source_path(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS",
            source_url="https://weather.gov/forecast",
            title="National Weather Service",
            snippet="NWS forecast supports below threshold.",
            claim_type="settlement",
            supports_direction="yes",
            supports_confidence=0.9,
            contract_fingerprint="contract-v1",
        ),
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS",
            source_url="https://weather.gov/counter",
            title="National Weather Service counter",
            snippet="NWS alternate scenario is neutral.",
            claim_type="disconfirming",
            supports_direction="neutral",
            supports_confidence=0.5,
            contract_fingerprint="contract-v1",
        ),
    ]
    queries = [
        ResearchQuery(
            query="NYC high temp support",
            query_intent="official_resolution",
            source_class="official_primary",
        ),
        ResearchQuery(
            query="NYC high temp disconfirming",
            query_intent="disconfirming",
            source_class="official_primary",
        ),
    ]

    await store.record_research_run(
        "KXHIGHNY-26JUL03-T98",
        "run-single-source",
        trigger_headline="NWS forecast",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Single-source weather evidence is not decision grade.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.7,
        confidence=0.8,
        contract_fingerprint="contract-v1",
        market_price=0.55,
        estimated_edge=0.15,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        queries=queries,
        evidence=evidence,
    )

    task = await store.get_research_task_snapshot("KXHIGHNY-26JUL03-T98")
    snapshot = await store.get_dossier_snapshot("KXHIGHNY-26JUL03-T98")

    assert task is not None
    assert task.state == ResearchStatus.NEEDS_RESEARCH.value
    assert task.last_skip_reason == "no_reliable_source_path"
    assert snapshot is not None
    assert snapshot.last_verdict_status == ResearchStatus.NEEDS_RESEARCH.value


@pytest.mark.asyncio
async def test_research_dossier_keeps_structured_official_metric_decision_grade(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS Climatological Report",
            source_url=(
                "https://forecast.weather.gov/product.php?"
                "site=OKX&product=CLI&issuedby=NYC"
            ),
            title="NWS Central Park daily maximum for July 2, 2026: 93F",
            snippet=(
                "NWS Central Park climate report lists TODAY MAXIMUM 93F "
                "for July 2, 2026, versus the below 99F market range."
            ),
            claim_type="official_resolution",
            supports_direction="yes",
            supports_confidence=0.95,
            metric_name="nws_daily_high_temp_f",
            metric_value=93.0,
            metric_unit="fahrenheit",
            extraction_confidence=0.95,
            contract_fingerprint="contract-v1",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Independent Weather Archive",
            source_url="https://weather.example.com/nyc-counter",
            title="NYC daily high countercheck",
            snippet="Independent countercheck found no higher official reading.",
            claim_type="disconfirming",
            supports_direction="neutral",
            supports_confidence=0.65,
            contract_fingerprint="contract-v1",
        ),
    ]
    queries = [
        ResearchQuery(
            query="site:forecast.weather.gov NYC climate daily high official",
            query_intent="official_resolution",
            source_class="official_primary",
        ),
        ResearchQuery(
            query="NYC high temp disconfirming alternate station official",
            query_intent="disconfirming",
            source_class="reputable_secondary",
        ),
    ]

    await store.record_research_run(
        "KXHIGHNY-26JUL02-T99",
        "run-structured-nws",
        trigger_headline="NWS climate report",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Official NWS observation supports YES with countercheck.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.94,
        confidence=0.9,
        contract_fingerprint="contract-v1",
        market_price=0.06,
        estimated_edge=0.88,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        queries=queries,
        evidence=evidence,
    )

    task = await store.get_research_task_snapshot("KXHIGHNY-26JUL02-T99")
    snapshot = await store.get_dossier_snapshot("KXHIGHNY-26JUL02-T99")

    assert task is not None
    assert task.state == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert task.last_skip_reason in {None, ""}
    assert snapshot is not None
    assert snapshot.last_verdict_status == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert snapshot.last_decision_grade_status == ResearchStatus.DECISION_GRADE_CANDIDATE.value


@pytest.mark.asyncio
async def test_research_dossier_replaces_invalid_decision_grade_when_snapshot_update_disabled(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    await store.record_research_run(
        "KXHIGHNY-26JUL03-T98",
        "run-old-invalid",
        trigger_headline="NWS forecast",
        trigger_source="manual_backfill",
        attempted=True,
        summary="Invalid candidate persisted before strict source validation.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.7,
        confidence=0.8,
        contract_fingerprint="contract-v1",
        market_price=0.55,
        estimated_edge=0.14,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        queries=[
            ResearchQuery(
                query="NYC high temp support",
                query_intent="supporting",
                source_class="official_primary",
            ),
            ResearchQuery(
                query="NYC high temp disconfirming",
                query_intent="disconfirming",
                source_class="official_primary",
            ),
        ],
        evidence=[
            ResearchEvidence(
                source_class="official_primary",
                source_name="NWS",
                source_url="https://weather.gov/forecast",
                title="National Weather Service",
                snippet="Single source supports YES.",
                claim_type="supporting",
                supports_direction="yes",
                supports_confidence=0.9,
                contract_fingerprint="contract-v1",
            ),
            ResearchEvidence(
                source_class="official_primary",
                source_name="NWS",
                source_url="https://weather.gov/forecast",
                title="National Weather Service counter",
                snippet="Same source supplies the countercase.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.7,
                contract_fingerprint="contract-v1",
            ),
        ],
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_dossiers
            SET last_verdict_status = 'decision_grade_candidate',
                last_decision_grade_status = 'decision_grade_candidate',
                last_skip_reason = NULL
            WHERE market_ticker = 'KXHIGHNY-26JUL03-T98'
            """
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET state = 'decision_grade_candidate',
                cooldown_until_ts = NULL,
                backoff_seconds = 0,
                terminal_reason = NULL
            WHERE market_ticker = 'KXHIGHNY-26JUL03-T98'
            """
        )

    await store.record_research_run(
        "KXHIGHNY-26JUL03-T98",
        "run-followup-needs-research",
        trigger_headline="",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Follow-up research no longer clears source validation.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        skip_reason="insufficient_corroboration",
        decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
        queries=[
            ResearchQuery(
                query="NYC high temp official resolution",
                query_intent="official_resolution",
                source_class="official_primary",
            ),
        ],
        evidence=[
            ResearchEvidence(
                source_class="official_primary",
                source_name="NWS",
                source_url="https://weather.gov/forecast",
                title="National Weather Service",
                snippet="Follow-up still has only one source path.",
                claim_type="official_resolution",
                supports_direction="yes",
                supports_confidence=0.8,
                contract_fingerprint="contract-v1",
            ),
        ],
        update_dossier_snapshot=False,
        update_dossier_run_id=True,
    )

    task = await store.get_research_task_snapshot("KXHIGHNY-26JUL03-T98")
    snapshot = await store.get_dossier_snapshot("KXHIGHNY-26JUL03-T98")

    assert task is not None
    assert task.state == ResearchStatus.NEEDS_RESEARCH.value
    assert task.last_skip_reason == "insufficient_corroboration"
    assert snapshot is not None
    assert snapshot.last_research_run_id == "run-followup-needs-research"
    assert snapshot.last_verdict_status == ResearchStatus.NEEDS_RESEARCH.value
    assert snapshot.last_decision_grade_status == ResearchStatus.NEEDS_RESEARCH.value
    assert snapshot.last_skip_reason == "insufficient_corroboration"


@pytest.mark.asyncio
async def test_research_task_keeps_repeated_ambiguous_direction_queued(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    for attempt in range(1, 4):
        await store.record_research_run(
            "KXAMBIGUOUS-26JUL01",
            f"run-ambiguous-{attempt}",
            trigger_headline="Mixed signal",
            trigger_source="Reuters",
            attempted=True,
            summary="Evidence remains balanced between yes and no.",
            verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
            skip_reason="ambiguous_direction",
            decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
            open_questions=["Which side has current official support?"],
        )
        task = await store.get_research_task_snapshot("KXAMBIGUOUS-26JUL01")
        assert task is not None
        assert task.attempt_count == attempt
        assert task.same_reason_count == attempt
        assert task.last_skip_reason == "ambiguous_direction"
        assert task.state == ResearchStatus.NEEDS_COUNTER_EVIDENCE.value
        assert task.terminal_reason is None
        assert task.backoff_seconds > 0
        assert task.cooldown_until_ts is not None


@pytest.mark.asyncio
async def test_research_task_keeps_neutral_only_evidence_queued(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    for attempt in range(1, 3):
        await store.record_research_run(
            "KXNEUTRALONLY-26JUL01",
            f"run-neutral-only-{attempt}",
            trigger_headline="Background only",
            trigger_source="Reuters",
            attempted=True,
            summary="Searches returned source-present background without a directional settlement fact.",
            verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
            skip_reason="neutral_only_evidence",
            decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
            open_questions=["Need current directional settlement fact."],
        )
        task = await store.get_research_task_snapshot("KXNEUTRALONLY-26JUL01")
        assert task is not None

    assert task.state == ResearchStatus.NEEDS_COUNTER_EVIDENCE.value
    assert task.terminal_reason is None
    assert task.backoff_seconds > 0
    assert task.cooldown_until_ts is not None


@pytest.mark.asyncio
async def test_research_task_keeps_research_timeouts_queued_after_retries(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    for attempt in range(1, 4):
        await store.record_research_run(
            "KXTIMEOUT-26JUL01",
            f"run-timeout-{attempt}",
            trigger_headline="Timeout",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Research timed out before adjudication.",
            verdict_status=ResearchStatus.CONTINUE_RESEARCHING.value,
            skip_reason="research_timeout",
            decision_grade_status=ResearchStatus.CONTINUE_RESEARCHING.value,
        )
        task = await store.get_research_task_snapshot("KXTIMEOUT-26JUL01")
        assert task is not None
        assert task.same_reason_count == attempt
        if attempt < 3:
            assert task.state == ResearchStatus.CONTINUE_RESEARCHING.value
            assert task.terminal_reason is None

    assert task.state == ResearchStatus.CONTINUE_RESEARCHING.value
    assert task.terminal_reason is None
    assert task.backoff_seconds > 0
    assert task.cooldown_until_ts is not None


@pytest.mark.asyncio
async def test_research_task_keeps_provider_outages_queued_after_retries(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    for attempt in range(1, 3):
        await store.record_research_run(
            "KXPROVIDER-26JUL01",
            f"run-provider-{attempt}",
            trigger_headline="Provider outage",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Research provider failed before evidence retrieval.",
            verdict_status=ResearchStatus.RESEARCH_PROVIDER_ERROR.value,
            skip_reason="research_provider_error",
            decision_grade_status=ResearchStatus.RESEARCH_PROVIDER_ERROR.value,
        )
        task = await store.get_research_task_snapshot("KXPROVIDER-26JUL01")
        assert task is not None
        assert task.same_reason_count == attempt
        if attempt < 3:
            assert task.state == ResearchStatus.RESEARCH_PROVIDER_ERROR.value
            assert task.terminal_reason is None

    assert task.state == ResearchStatus.RESEARCH_PROVIDER_ERROR.value
    assert task.terminal_reason is None
    assert task.backoff_seconds > 0
    assert task.cooldown_until_ts is not None


@pytest.mark.asyncio
async def test_research_task_caps_official_data_pending_backoff(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    for attempt in range(1, 8):
        await store.record_research_run(
            "KXOFFICIALPENDING-26JUL01",
            f"run-official-pending-{attempt}",
            trigger_headline="Official data pending",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Official settlement data has not been released yet.",
            verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
            skip_reason="official_data_pending",
            decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
        )
        task = await store.get_research_task_snapshot("KXOFFICIALPENDING-26JUL01")
        assert task is not None
        assert task.state == ResearchStatus.NEEDS_RESEARCH.value
        assert task.terminal_reason is None
        assert task.same_reason_count == attempt
        assert task.cooldown_until_ts is not None

    assert task.backoff_seconds <= 1800.0


@pytest.mark.asyncio
async def test_research_task_terminalizes_insufficient_corroboration_after_retries(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    for attempt in range(1, 4):
        await store.record_research_run(
            "KXCORROBORATION-26JUL01",
            f"run-corroboration-{attempt}",
            trigger_headline="Official page found",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Only one official source was available.",
            verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
            skip_reason="insufficient_corroboration",
            decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
        )
        task = await store.get_research_task_snapshot("KXCORROBORATION-26JUL01")
        assert task is not None
        assert task.same_reason_count == attempt

    assert task.state == ResearchStatus.UNTRADEABLE.value
    assert task.terminal_reason == "no_reliable_source_path"
    assert task.backoff_seconds == 0
    assert task.cooldown_until_ts is None


@pytest.mark.asyncio
async def test_research_dossier_lists_due_researchable_tasks(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    for ticker, state, reason in (
        ("KXDUE-26JUL01", ResearchStatus.NEEDS_COUNTER_EVIDENCE.value, "ambiguous_direction"),
        ("KXCOOLING-26JUL01", ResearchStatus.NEEDS_COUNTER_EVIDENCE.value, "ambiguous_direction"),
        ("KXDECISION-26JUL01", ResearchStatus.DECISION_GRADE_CANDIDATE.value, None),
        (
            "KXNEEDSRESEARCH-26JUL01",
            ResearchStatus.NEEDS_RESEARCH.value,
            "missing_resolution_source",
        ),
        (
            "KXSOURCEPATH-26JUL01",
            ResearchStatus.UNTRADEABLE.value,
            "no_reliable_source_path",
        ),
        ("KXTERMINAL-26JUL01", ResearchStatus.UNTRADEABLE.value, "no_edge"),
    ):
        evidence = []
        queries = []
        if state == ResearchStatus.DECISION_GRADE_CANDIDATE.value:
            evidence = [
                ResearchEvidence(
                    source_class="resolution_source",
                    source_name="Official",
                    source_url="https://official.example.com/final",
                    title="Official result",
                    snippet="Official result supports yes.",
                    claim_type="settlement",
                    supports_direction="yes",
                    supports_confidence=0.9,
                ),
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="Reuters",
                    source_url="https://reuters.example.com/report",
                    title="Reuters report",
                    snippet="Reuters supports yes independently.",
                    claim_type="settlement",
                    supports_direction="yes",
                    supports_confidence=0.85,
                ),
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="AP",
                    source_url="https://ap.example.com/counter",
                    title="AP counter",
                    snippet="AP reports a countercase.",
                    claim_type="disconfirming",
                    supports_direction="no",
                    supports_confidence=0.7,
                ),
            ]
            queries = [
                ResearchQuery(
                    query="official result",
                    query_intent="official_resolution",
                    source_class="resolution_source",
                ),
                ResearchQuery(
                    query="countercase",
                    query_intent="disconfirming",
                    source_class="reputable_secondary",
                ),
            ]
        await store.record_research_run(
            ticker,
            f"run-{ticker}",
            trigger_headline="Research update",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Research state.",
            verdict_status=state,
            skip_reason=reason,
            decision_grade_status=state,
            force_side="yes" if state == ResearchStatus.DECISION_GRADE_CANDIDATE.value else None,
            queries=queries,
            evidence=evidence,
        )

    now = datetime(2026, 6, 30, 12, tzinfo=timezone.utc)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T10:00:00.000Z',
                cooldown_until_ts = '2026-06-30T10:05:00.000Z'
            WHERE market_ticker = 'KXDUE-26JUL01'
            """
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T11:00:00.000Z',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXDECISION-26JUL01'
            """
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T11:55:00.000Z',
                cooldown_until_ts = '2026-06-30T12:30:00.000Z'
            WHERE market_ticker = 'KXCOOLING-26JUL01'
            """
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T09:30:00.000Z',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXSOURCEPATH-26JUL01'
            """
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T09:45:00.000Z',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXNEEDSRESEARCH-26JUL01'
            """
        )

    assert store.get_due_research_task_tickers(
        now=now,
        target_cooldown_seconds=1800.0,
    ) == [
        "KXDUE-26JUL01",
        "KXNEEDSRESEARCH-26JUL01",
        "KXDECISION-26JUL01",
        "KXSOURCEPATH-26JUL01",
    ]


@pytest.mark.asyncio
async def test_research_dossier_lists_repaired_zero_backoff_tasks_immediately(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    await store.record_research_run(
        "KXREPAIRED-26JUL01",
        "run-repaired",
        trigger_headline="Research update",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Needs counter evidence.",
        verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        skip_reason="missing_counter_evidence",
        decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        queries=[
            ResearchQuery(
                query="countercase",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            )
        ],
        evidence=[],
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T11:59:00.000Z',
                cooldown_until_ts = NULL,
                backoff_seconds = 0,
                last_skip_reason = 'missing_counter_evidence'
            WHERE market_ticker = 'KXREPAIRED-26JUL01'
            """
        )

    assert store.get_due_research_task_tickers(
        limit=5,
        now=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        target_cooldown_seconds=1800.0,
    ) == ["KXREPAIRED-26JUL01"]


@pytest.mark.asyncio
async def test_due_research_tasks_prioritize_actionable_source_work_before_pending_official_data(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    for ticker, skip_reason in (
        ("KXGENERIC-26JUL01", "no_research_hits"),
        ("KXOFFICIAL-26JUL01", "official_data_pending"),
        ("KXSOURCE-26JUL01", "missing_resolution_source"),
    ):
        await store.record_research_run(
            ticker,
            f"run-{ticker}",
            trigger_headline="Research update",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Research needs another pass.",
            verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
            skip_reason=skip_reason,
            decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
        )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T08:00:00.000Z',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXGENERIC-26JUL01'
            """
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T11:00:00.000Z',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXOFFICIAL-26JUL01'
            """
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T10:00:00.000Z',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXSOURCE-26JUL01'
            """
        )

    assert store.get_due_research_task_tickers(
        now=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        target_cooldown_seconds=0.0,
    ) == [
        "KXSOURCE-26JUL01",
        "KXOFFICIAL-26JUL01",
        "KXGENERIC-26JUL01",
    ]


@pytest.mark.asyncio
async def test_due_research_tasks_include_timeout_exhausted_terminal_tasks(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    await store.record_research_run(
        "KXTIMEOUT-26JUL01",
        "run-timeout-terminal",
        trigger_headline="Research timed out",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Timeout is not a trade decision.",
        verdict_status=ResearchStatus.UNTRADEABLE.value,
        skip_reason="research_timeout_exhausted",
        decision_grade_status=ResearchStatus.UNTRADEABLE.value,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET terminal_reason = 'research_timeout_exhausted',
                last_skip_reason = 'research_timeout',
                updated_ts = '2026-06-30T09:00:00.000Z',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXTIMEOUT-26JUL01'
            """
        )

    assert store.get_due_research_task_tickers(
        now=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        target_cooldown_seconds=0.0,
    ) == ["KXTIMEOUT-26JUL01"]


@pytest.mark.asyncio
async def test_due_research_tasks_cap_existing_official_pending_cooldown(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    await store.record_research_run(
        "KXOFFICIAL-26JUL01",
        "run-official-pending",
        trigger_headline="Official data pending",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Official source has not published yet.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        skip_reason="official_data_pending",
        decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T11:00:00.000Z',
                cooldown_until_ts = '2026-06-30T17:00:00.000Z',
                backoff_seconds = 21600
            WHERE market_ticker = 'KXOFFICIAL-26JUL01'
            """
        )

    assert store.get_due_research_task_tickers(
        now=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        target_cooldown_seconds=0.0,
    ) == ["KXOFFICIAL-26JUL01"]


@pytest.mark.asyncio
async def test_official_data_pending_records_as_needs_research_not_counter_evidence(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    await store.record_research_run(
        "KXOFFICIALCOUNTER-26JUL01",
        "run-official-counter-pending",
        trigger_headline="Official data pending",
        trigger_source="research_prewarm",
        attempted=True,
        summary="The official result is not available yet.",
        verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        skip_reason="official_data_pending",
        decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
    )

    snapshot = await store.get_research_task_snapshot("KXOFFICIALCOUNTER-26JUL01")
    dossier = await store.get_dossier_snapshot("KXOFFICIALCOUNTER-26JUL01")

    assert snapshot is not None
    assert snapshot.state == ResearchStatus.NEEDS_RESEARCH.value
    assert snapshot.last_skip_reason == "official_data_pending"
    assert dossier is not None
    assert dossier.last_verdict_status == ResearchStatus.NEEDS_RESEARCH.value
    assert dossier.last_decision_grade_status == ResearchStatus.NEEDS_RESEARCH.value
    assert dossier.last_skip_reason == "official_data_pending"


@pytest.mark.asyncio
async def test_research_dossier_can_record_attempt_without_overwriting_snapshot(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    await store.record_research_run(
        "KXIRANCRUDE-26JUL13-T3.8",
        "run-vetted",
        trigger_headline="Iran output update",
        trigger_source="Reuters",
        attempted=True,
        summary="Research supports yes.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        contract_fingerprint="contract-v1",
    )

    await store.record_research_run(
        "KXIRANCRUDE-26JUL13-T3.8",
        "run-timeout",
        trigger_headline="Iran output update",
        trigger_source="Reuters",
        attempted=True,
        summary="Research timed out.",
        verdict_status=ResearchStatus.CONTINUE_RESEARCHING.value,
        skip_reason="research_timeout",
        contract_fingerprint="contract-v1",
        update_dossier_snapshot=False,
    )

    with sqlite3.connect(db_path) as conn:
        dossier = conn.execute(
            """
            SELECT last_research_run_id, last_verdict_status, last_force_side
            FROM research_dossiers
            """
        ).fetchone()
        timeout_run = conn.execute(
            """
            SELECT verdict_status, skip_reason
            FROM research_runs
            WHERE research_run_id = 'run-timeout'
            """
        ).fetchone()

    assert dossier == ("run-vetted", ResearchStatus.TRADE_CANDIDATE.value, "yes")
    assert timeout_run == (ResearchStatus.CONTINUE_RESEARCHING.value, "research_timeout")


@pytest.mark.asyncio
async def test_research_dossier_can_update_snapshot_without_replacing_proof_run(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    await store.record_research_run(
        "KXIRANCRUDE-26JUL13-T3.8",
        "run-vetted",
        trigger_headline="Iran output update",
        trigger_source="Reuters",
        attempted=True,
        summary="Research supports yes.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        contract_fingerprint="contract-v1",
    )

    await store.record_research_run(
        "KXIRANCRUDE-26JUL13-T3.8",
        "run-no-edge",
        trigger_headline="Iran output update",
        trigger_source="Reuters",
        attempted=True,
        summary="Research completed with no edge.",
        verdict_status=ResearchStatus.RESEARCHED_SKIP_NO_EDGE.value,
        skip_reason="negative_net_edge_after_costs",
        contract_fingerprint="contract-v1",
        update_dossier_snapshot=True,
        update_dossier_run_id=False,
    )

    with sqlite3.connect(db_path) as conn:
        dossier = conn.execute(
            """
            SELECT last_research_run_id, last_verdict_status, last_skip_reason
            FROM research_dossiers
            """
        ).fetchone()
        no_edge_run = conn.execute(
            """
            SELECT verdict_status, skip_reason
            FROM research_runs
            WHERE research_run_id = 'run-no-edge'
            """
        ).fetchone()

    assert dossier == (
        "run-vetted",
        ResearchStatus.RESEARCHED_SKIP_NO_EDGE.value,
        "negative_net_edge_after_costs",
    )
    assert no_edge_run == (
        ResearchStatus.RESEARCHED_SKIP_NO_EDGE.value,
        "negative_net_edge_after_costs",
    )


@pytest.mark.asyncio
async def test_research_dossier_can_associate_same_evidence_with_multiple_runs(tmp_path):
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

    for run_id in ("run-1", "run-2"):
        await store.record_research_run(
            "KXIRANCRUDE-26JUL13-T3.8",
            run_id,
            trigger_headline="Iran output update",
            trigger_source="Reuters",
            attempted=True,
            summary="Research supports yes.",
            verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
            force_side="yes",
            estimated_probability=0.8,
            confidence=0.8,
            contract_fingerprint="contract-v1",
            evidence=[evidence],
        )

    with sqlite3.connect(db_path) as conn:
        counts = conn.execute(
            """
            SELECT research_run_id, COUNT(*)
            FROM research_evidence
            GROUP BY research_run_id
            ORDER BY research_run_id
            """
        ).fetchall()

    assert counts == [("run-1", 1), ("run-2", 1)]


@pytest.mark.asyncio
async def test_research_dossier_preserves_structured_counter_evidence_same_url(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    source_url = (
        "https://forecast.weather.gov/product.php"
        "?site=OKX&product=CLI&issuedby=NYC#high-2026-07-01"
    )
    base = dict(
        source_class="official_primary",
        source_name="NWS Climatological Report",
        source_url=source_url,
        title="NWS Central Park daily maximum for July 1, 2026: 87F",
        snippet="NWS Central Park climate report lists TODAY MAXIMUM 87F.",
        supports_direction="no",
        supports_confidence=0.95,
        metric_name="nws_daily_high_temp_f",
        metric_value=87.0,
        metric_unit="fahrenheit",
        extraction_confidence=0.95,
        contract_fingerprint="contract-weather",
    )

    await store.record_research_run(
        "KXHIGHNY-26JUL01-B94.5",
        "run-weather",
        trigger_headline="",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Weather research.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="no",
        estimated_probability=0.05,
        confidence=0.95,
        contract_fingerprint="contract-weather",
        evidence=[
            ResearchEvidence(claim_type="supporting", **base),
            ResearchEvidence(claim_type="disconfirming", **base),
        ],
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT claim_type, metric_name
            FROM research_evidence
            ORDER BY claim_type
            """
        ).fetchall()

    assert rows == [
        ("disconfirming", "nws_daily_high_temp_f"),
        ("supporting", "nws_daily_high_temp_f"),
    ]


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
