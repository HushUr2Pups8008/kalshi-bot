from __future__ import annotations

import asyncio
import json
import threading
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
from tasks.research_dossier import (
    ResearchDossierStore,
    _decision_grade_persistence_quality,
    _stored_decision_grade_snapshot_is_valid_sync,
    _validated_research_status,
)


class _TrackingConnection:
    def __init__(self) -> None:
        self.entered = 0
        self.exit_args = None
        self.closed = 0
        self.executed = []

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exit_args = (exc_type, exc, traceback)
        return False

    def close(self) -> None:
        self.closed += 1

    def execute(self, statement, parameters=()):
        self.executed.append((statement, parameters))
        return SimpleNamespace(rowcount=1)


def test_decision_grade_persistence_requires_source_class_diversity():
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="Agency A",
            source_url="https://agency-a.gov/result",
            title="Result",
            snippet="Supports yes.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
        ),
        ResearchEvidence(
            source_class="resolution_source",
            source_name="Agency B",
            source_url="https://agency-b.gov/counter",
            title="Counter",
            snippet="Supports no.",
            claim_type="settlement",
            supports_direction="no",
            supports_confidence=0.9,
        ),
    ]

    quality = _decision_grade_persistence_quality(
        ticker="KXTEST-1",
        side="yes",
        queries=[SimpleNamespace(query="counter", query_intent="disconfirming")],
        evidence=evidence,
    )

    assert quality["has_reliable_source_path"] is False


def test_persistence_quality_rejects_irrelevant_speech_directional_evidence():
    quality = _decision_grade_persistence_quality(
        ticker="KXTRUMPMENTION-26JUL24-MAGA",
        side="yes",
        queries=[
            SimpleNamespace(
                query=(
                    "What will Trump say during the dinner? If Donald Trump says "
                    "MAGA / Make America Great Again as part of the dinner, then "
                    "the market resolves Yes."
                ),
                query_intent="official_resolution",
            ),
            SimpleNamespace(
                query="MAGA evidence against YES",
                query_intent="disconfirming",
            ),
        ],
        evidence=[
            ResearchEvidence(
                source_class="rules_source",
                source_name="Kalshi",
                source_url="https://kalshi.com/markets/KXTRUMPMENTION",
                title="Contract terms",
                snippet="The rules define the mention condition.",
                claim_type="rules",
                supports_direction="neutral",
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="USA Today",
                source_url="https://usatoday.com/america-birthday",
                title="Celebrations start in DC for America's birthday",
                snippet="Officials expect tight security at the White House event.",
                claim_type="supporting",
                supports_direction="yes",
                supports_confidence=0.9,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="New York Times",
                source_url="https://nytimes.com/greenland",
                title="Trump discusses Greenland",
                snippet="Trump says the public will find out what happens next.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.1,
            ),
        ],
    )

    assert quality["has_directional_evidence"] is False
    assert quality["has_counter_evidence"] is False


def test_persistence_quality_rejects_counter_query_boilerplate_match():
    quality = _decision_grade_persistence_quality(
        ticker="KXUSTRDAGREEMENT-26JUL01",
        side="yes",
        queries=[
            SimpleNamespace(
                query="Will the US sign a trade agreement before July 1?",
                query_intent="official_resolution",
            ),
            SimpleNamespace(
                query=(
                    "Will the US sign a trade agreement before July 1? evidence "
                    "against YES evidence against NO false not confirmed denied "
                    "opponent objection"
                ),
                query_intent="disconfirming",
            ),
        ],
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Commerce Department",
                source_url="https://commerce.gov/trade-agreement",
                title="US signs bilateral trade agreement",
                snippet="Officials signed the trade agreement before July 1.",
                claim_type="official_resolution",
                supports_direction="yes",
                supports_confidence=0.9,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Sports Wire",
                source_url="https://sports.example.com/objection",
                title="Opponent denied objection",
                snippet="The objection concerns an unrelated sports dispute.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.8,
            ),
        ],
    )

    assert quality["has_directional_evidence"] is True
    assert quality["has_counter_evidence"] is False


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


def test_admission_claim_uses_transaction_and_closes(monkeypatch, tmp_path):
    store = ResearchDossierStore(tmp_path / "research.db")
    connection = _TrackingConnection()
    monkeypatch.setattr(store, "_initialize_sync", lambda: None)
    monkeypatch.setattr(store, "_connect", lambda: connection)

    claimed = store._claim_research_paper_admission_sync(
        "KXTEST-26",
        "run-1",
        "fingerprint-1",
    )

    assert claimed is True
    assert connection.entered == 1
    assert connection.exit_args == (None, None, None)
    assert connection.closed == 1
    assert len(connection.executed) == 1


def test_admission_claim_rolls_back_and_closes_on_error(monkeypatch, tmp_path):
    store = ResearchDossierStore(tmp_path / "research.db")
    connection = _TrackingConnection()
    monkeypatch.setattr(store, "_initialize_sync", lambda: None)
    monkeypatch.setattr(store, "_connect", lambda: connection)

    def fail_execute(_statement, _parameters=()):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(connection, "execute", fail_execute)

    with pytest.raises(RuntimeError, match="insert failed"):
        store._claim_research_paper_admission_sync(
            "KXTEST-26",
            "run-1",
            "fingerprint-1",
        )

    assert connection.exit_args[0] is RuntimeError
    assert connection.closed == 1


@pytest.mark.asyncio
async def test_research_dossier_adds_and_persists_market_eligibility_columns(tmp_path):
    db_path = tmp_path / "research.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE research_dossiers (
                market_ticker TEXT PRIMARY KEY,
                last_researched_ts TEXT NOT NULL,
                last_verdict_status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE research_runs (
                research_run_id TEXT PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                trigger_headline TEXT NOT NULL,
                trigger_source TEXT NOT NULL,
                attempted INTEGER NOT NULL,
                summary TEXT NOT NULL,
                verdict_status TEXT NOT NULL
            )
            """
        )

    store = ResearchDossierStore(db_path)
    await store.initialize()

    with sqlite3.connect(db_path) as conn:
        dossier_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(research_dossiers)")
        }
        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}

    assert {"market_status", "market_close_time"} <= dossier_columns
    assert {"market_status", "market_close_time"} <= run_columns

    fresh_db_path = tmp_path / "fresh-research.db"
    fresh_store = ResearchDossierStore(fresh_db_path)
    await fresh_store.initialize()
    await fresh_store.record_research_run(
        "KXTEST-26",
        "run-eligibility",
        trigger_headline="Current market observation",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Still open.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        market_status="active",
        market_close_time="2026-07-12T18:00:00Z",
    )

    snapshot = await fresh_store.get_dossier_snapshot("KXTEST-26")
    with sqlite3.connect(fresh_db_path) as conn:
        run_metadata = conn.execute(
            """
            SELECT market_status, market_close_time
            FROM research_runs
            WHERE research_run_id = 'run-eligibility'
            """
        ).fetchone()
        dossier_metadata = conn.execute(
            """
            SELECT market_status, market_close_time
            FROM research_dossiers
            WHERE market_ticker = 'KXTEST-26'
            """
        ).fetchone()

    assert snapshot is not None
    assert snapshot.market_status == "active"
    assert snapshot.market_close_time == "2026-07-12T18:00:00Z"
    assert run_metadata == ("active", "2026-07-12T18:00:00Z")
    assert dossier_metadata == run_metadata

    await fresh_store.record_research_run(
        "KXTEST-26",
        "run-object-eligibility",
        trigger_headline="Enum-like market observation",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Raw API metadata.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        market_status=SimpleNamespace(value="OPEN"),
        market_close_time=datetime(2026, 7, 12, 20, tzinfo=timezone.utc),
        update_dossier_snapshot=False,
        update_dossier_run_id=False,
    )
    await fresh_store.record_research_run(
        "KXINVALID-26",
        "run-invalid-close",
        trigger_headline="Invalid close observation",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Invalid raw API metadata.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        market_status="ACTIVE",
        market_close_time=object(),
    )
    await fresh_store.record_research_run(
        "KXMISSING-26",
        "run-missing-close",
        trigger_headline="Missing close observation",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Missing raw API metadata.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        market_status="OPEN",
        market_close_time=None,
    )

    with sqlite3.connect(fresh_db_path) as conn:
        object_metadata = conn.execute(
            """
            SELECT market_status, market_close_time
            FROM research_runs
            WHERE research_run_id = 'run-object-eligibility'
            """
        ).fetchone()
        invalid_metadata = conn.execute(
            """
            SELECT market_status, market_close_time
            FROM research_runs
            WHERE research_run_id = 'run-invalid-close'
            """
        ).fetchone()
        missing_metadata = conn.execute(
            """
            SELECT market_status, market_close_time
            FROM research_runs
            WHERE research_run_id = 'run-missing-close'
            """
        ).fetchone()

    assert object_metadata == ("open", "2026-07-12T20:00:00Z")
    assert invalid_metadata == ("active", None)
    assert missing_metadata == ("open", None)


@pytest.mark.asyncio
async def test_research_runs_contract_fingerprint_migrates_without_backfill(tmp_path):
    db_path = tmp_path / "legacy-research.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE research_dossiers (
                market_ticker TEXT PRIMARY KEY,
                last_contract_fingerprint TEXT,
                last_researched_ts TEXT NOT NULL,
                last_verdict_status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE research_runs (
                research_run_id TEXT PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                trigger_headline TEXT NOT NULL,
                trigger_source TEXT NOT NULL,
                attempted INTEGER NOT NULL,
                summary TEXT NOT NULL,
                verdict_status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_dossiers (
                market_ticker, last_contract_fingerprint, last_researched_ts,
                last_verdict_status
            ) VALUES ('KXLEGACY-26', 'dossier-fingerprint', '2026-07-12T18:00:00Z', 'needs_research')
            """
        )
        conn.execute(
            """
            INSERT INTO research_runs (
                research_run_id, market_ticker, trigger_headline, trigger_source,
                attempted, summary, verdict_status
            ) VALUES ('legacy-run', 'KXLEGACY-26', 'Legacy', 'manual', 1, 'Old row.', 'needs_research')
            """
        )

    store = ResearchDossierStore(db_path)
    await store.initialize()

    with sqlite3.connect(db_path) as conn:
        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}
        legacy_fingerprint = conn.execute(
            "SELECT contract_fingerprint FROM research_runs WHERE research_run_id = 'legacy-run'"
        ).fetchone()[0]

    assert "contract_fingerprint" in run_columns
    assert legacy_fingerprint is None


@pytest.mark.asyncio
async def test_research_runs_persist_and_preserve_contract_fingerprint(tmp_path):
    db_path = tmp_path / "research.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    await store.record_research_run(
        "KXFRESH-26",
        "fresh-run",
        trigger_headline="Fresh contract",
        trigger_source="manual",
        attempted=True,
        summary="Fresh fingerprint.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        contract_fingerprint="fresh-fingerprint",
    )
    await store.record_research_run(
        "KXLATER-26",
        "later-run",
        trigger_headline="First observation",
        trigger_source="manual",
        attempted=True,
        summary="No identity yet.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
    )
    await store.record_research_run(
        "KXLATER-26",
        "later-run",
        trigger_headline="Evidence arrived",
        trigger_source="manual",
        attempted=True,
        summary="Identity now known.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        evidence=[
            ResearchEvidence(
                source_class="rules_source",
                source_name="Kalshi",
                source_url="https://kalshi.com/markets/KXLATER",
                title="Contract terms",
                snippet="The terms identify this contract.",
                claim_type="rules",
                supports_direction="neutral",
                contract_fingerprint="evidence-fingerprint",
            )
        ],
    )
    await store.record_research_run(
        "KXLATER-26",
        "later-run",
        trigger_headline="Whitespace-only identity",
        trigger_source="manual",
        attempted=True,
        summary="Whitespace must not erase prior identity.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        contract_fingerprint="  \t  ",
    )

    with sqlite3.connect(db_path) as conn:
        fingerprints = dict(
            conn.execute(
                """
                SELECT research_run_id, contract_fingerprint
                FROM research_runs
                WHERE research_run_id IN ('fresh-run', 'later-run')
                """
            ).fetchall()
        )

    assert fingerprints == {
        "fresh-run": "fresh-fingerprint",
        "later-run": "evidence-fingerprint",
    }


@pytest.mark.asyncio
async def test_research_runs_normalize_contract_fingerprints_before_persistence(tmp_path):
    db_path = tmp_path / "research.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    def evidence(fingerprint: str) -> ResearchEvidence:
        return ResearchEvidence(
            source_class="rules_source",
            source_name="Kalshi",
            source_url="https://kalshi.com/markets/KXTEST",
            title="Contract terms",
            snippet="The terms identify this contract.",
            claim_type="rules",
            supports_direction="neutral",
            contract_fingerprint=fingerprint,
        )

    async def record(run_id: str, **kwargs) -> None:
        await store.record_research_run(
            "KXTEST-26",
            run_id,
            trigger_headline="Contract identity",
            trigger_source="manual",
            attempted=True,
            summary="Research identity update.",
            verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
            **kwargs,
        )

    await record("mixed-evidence", evidence=[evidence("fp-1"), evidence(" fp-1 ")])
    await record("later-evidence")
    await record("later-evidence", evidence=[evidence(" fp-2 ")])
    await record("explicit-padded", contract_fingerprint=" fp-3 ")
    await record("distinct-evidence", evidence=[evidence("fp-4"), evidence(" fp-5 ")])

    with sqlite3.connect(db_path) as conn:
        fingerprints = dict(
            conn.execute(
                """
                SELECT research_run_id, contract_fingerprint
                FROM research_runs
                ORDER BY research_run_id
                """
            ).fetchall()
        )

    assert fingerprints == {
        "distinct-evidence": None,
        "explicit-padded": "fp-3",
        "later-evidence": "fp-2",
        "mixed-evidence": "fp-1",
    }


@pytest.mark.asyncio
async def test_market_eligibility_metadata_updates_without_demoting_snapshot(tmp_path):
    db_path = tmp_path / "research.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    await store.record_research_run(
        "KXTEST-26",
        "run-proof",
        trigger_headline="Decision-grade proof",
        trigger_source="manual",
        attempted=True,
        summary="Retained proof.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        market_status="active",
        market_close_time="2026-07-12T18:00:00Z",
    )
    await store.record_research_run(
        "KXTEST-26",
        "run-observation",
        trigger_headline="Current market observation",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Market closed.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        market_status="closed",
        market_close_time="2026-07-11T18:00:00Z",
        update_dossier_snapshot=False,
        update_dossier_run_id=False,
    )

    snapshot = await store.get_dossier_snapshot("KXTEST-26")

    assert snapshot is not None
    assert snapshot.last_research_run_id == "run-proof"
    assert snapshot.last_verdict_status == ResearchStatus.TRADE_CANDIDATE.value
    assert snapshot.market_status == "closed"
    assert snapshot.market_close_time == "2026-07-11T18:00:00Z"


@pytest.mark.asyncio
async def test_research_paper_admission_claim_is_sequentially_at_most_once(tmp_path):
    store = ResearchDossierStore(tmp_path / "research.db")
    await store.initialize()

    first = await store.claim_research_paper_admission(
        "KXTEST-26",
        "run-1",
        "fingerprint-1",
    )
    second = await store.claim_research_paper_admission(
        "KXTEST-26",
        "run-1",
        "fingerprint-1",
    )

    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_research_paper_admission_reclaim_is_atomic_for_unfilled_politics(
    tmp_path,
    monkeypatch,
):
    import config as config_module
    from utils.event_news_research import EVENT_NEWS_COHORT_ID

    monkeypatch.setattr(config_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    monkeypatch.setattr("utils.event_news_research.cfg", config_module.cfg)
    store = ResearchDossierStore(tmp_path / "research.db")
    await store.initialize()
    assert await store.claim_research_paper_admission(
        "KXTEST-26", "run-1", "fingerprint-1"
    )
    await store.complete_research_paper_admission(
        "KXTEST-26",
        "run-1",
        "fingerprint-1",
        state="completed",
        enqueued=True,
        outcome_reason=None,
    )
    assert await store.reclaim_research_paper_admission(
        "KXTEST-26",
        "run-1",
        "fingerprint-1",
        allow_unfilled_enqueue=True,
    )
    assert not await store.reclaim_research_paper_admission(
        "KXTEST-26",
        "run-1",
        "fingerprint-1",
        allow_unfilled_enqueue=True,
    )


@pytest.mark.asyncio
async def test_research_paper_admission_claim_is_atomic_across_stores(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "research.db"
    migration_barrier = threading.Barrier(2)
    real_connect = sqlite3.connect

    class _ConcurrentMigrationConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            cursor = super().execute(statement, parameters)
            if statement.strip() != "PRAGMA table_info(research_dossiers)":
                return cursor

            rows = cursor.fetchall()
            try:
                migration_barrier.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            return SimpleNamespace(fetchall=lambda: rows)

    def connect_with_migration_barrier(*args, **kwargs):
        kwargs["factory"] = _ConcurrentMigrationConnection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(
        "tasks.research_dossier.sqlite3.connect",
        connect_with_migration_barrier,
    )
    stores = [ResearchDossierStore(db_path), ResearchDossierStore(db_path)]
    await asyncio.gather(*(store.initialize() for store in stores))

    results = await asyncio.gather(
        *(
            store.claim_research_paper_admission(
                "KXTEST-26",
                "run-1",
                "fingerprint-1",
            )
            for store in stores
        )
    )

    assert sorted(results) == [False, True]
    with real_connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM research_paper_admissions").fetchone()[0]
    assert count == 1


@pytest.mark.asyncio
async def test_research_paper_admission_claim_survives_store_reopen(tmp_path):
    db_path = tmp_path / "research.db"
    first_store = ResearchDossierStore(db_path)
    await first_store.initialize()
    assert await first_store.claim_research_paper_admission(
        "KXTEST-26",
        "run-1",
        "fingerprint-1",
    )

    reopened_store = ResearchDossierStore(db_path)
    await reopened_store.initialize()

    assert not await reopened_store.claim_research_paper_admission(
        "KXTEST-26",
        "run-1",
        "fingerprint-1",
    )


@pytest.mark.asyncio
async def test_research_paper_admission_composite_key_keeps_distinct_claims(tmp_path):
    db_path = tmp_path / "research.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    keys = (
        ("KXTEST-26", "run-1", "fingerprint-1"),
        ("KXTEST-26", "run-2", "fingerprint-1"),
        ("KXTEST-26", "run-1", "fingerprint-2"),
        ("KXOTHER-26", "run-1", "fingerprint-1"),
    )

    assert all([await store.claim_research_paper_admission(*key) for key in keys])
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM research_paper_admissions").fetchone()[0]
    assert count == len(keys)


@pytest.mark.asyncio
async def test_research_paper_admission_records_completed_and_failed_outcomes(tmp_path):
    db_path = tmp_path / "research.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    await store.claim_research_paper_admission("KXTEST-26", "run-1", "fingerprint-1")
    await store.claim_research_paper_admission("KXTEST-26", "run-2", "fingerprint-2")

    await store.complete_research_paper_admission(
        "KXTEST-26",
        "run-1",
        "fingerprint-1",
        state="completed",
        enqueued=True,
        outcome_reason="paper route accepted",
    )
    await store.complete_research_paper_admission(
        "KXTEST-26",
        "run-2",
        "fingerprint-2",
        state="failed",
        enqueued=None,
        outcome_reason="bridge crashed",
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT research_run_id, state, enqueued, outcome_reason,
                   claimed_ts, completed_ts, updated_ts
            FROM research_paper_admissions
            ORDER BY research_run_id
            """
        ).fetchall()

    assert rows[0][:4] == ("run-1", "completed", 1, "paper route accepted")
    assert rows[1][:4] == ("run-2", "failed", None, "bridge crashed")
    assert all(row[4] for row in rows)
    assert all(row[5] for row in rows)
    assert all(row[6] for row in rows)
    assert not await store.claim_research_paper_admission(
        "KXTEST-26",
        "run-1",
        "fingerprint-1",
    )
    assert not await store.claim_research_paper_admission(
        "KXTEST-26",
        "run-2",
        "fingerprint-2",
    )


@pytest.mark.asyncio
async def test_research_paper_admission_rejects_invalid_completion_state(tmp_path):
    db_path = tmp_path / "research.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    await store.claim_research_paper_admission("KXTEST-26", "run-1", "fingerprint-1")

    with pytest.raises(ValueError, match="completed or failed"):
        await store.complete_research_paper_admission(
            "KXTEST-26",
            "run-1",
            "fingerprint-1",
            state="claimed",  # type: ignore[arg-type]
            enqueued=False,
            outcome_reason="invalid transition",
        )

    with sqlite3.connect(db_path) as conn:
        state = conn.execute("SELECT state FROM research_paper_admissions").fetchone()[0]
    assert state == "claimed"


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
        aggregator_url="https://news.google.com/rss/articles/opec-example",
    )

    await store.add_evidence("KXIRANCRUDE-26JUL13-T3.8", "run-1", evidence)
    rows = await store.get_recent_evidence("KXIRANCRUDE-26JUL13-T3.8")

    assert len(rows) == 1
    assert rows[0].source_name == "OPEC"
    assert rows[0].source_class == "resolution_source"
    assert rows[0].source_url == "https://opec.org/momr"
    assert rows[0].contract_fingerprint == "contract-v1"
    assert rows[0].aggregator_url == (
        "https://news.google.com/rss/articles/opec-example"
    )


@pytest.mark.asyncio
async def test_research_dossier_initialize_is_safe_under_concurrency(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")

    await asyncio.gather(*(store.initialize() for _ in range(5)))

    snapshot = await store.get_dossier_snapshot("KX-MISSING")
    assert snapshot is None


def test_research_dossier_initializes_schema_once_per_store(monkeypatch, tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    initialize_calls = 0

    def initialize_schema():
        nonlocal initialize_calls
        initialize_calls += 1

    monkeypatch.setattr(store, "_initialize_sync_locked", initialize_schema)

    store._initialize_sync()
    store._initialize_sync()

    assert initialize_calls == 1


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
            source_url="https://agency.gov/final",
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
            source_url="https://reuters.com/report",
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
            source_url="https://apnews.com/counter",
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
        proof_run_before = conn.execute(
            """
            SELECT trigger_headline, trigger_source, contract_question, attempted,
                   summary, verdict_status, skip_reason, force_side,
                   estimated_probability, confidence, market_price, estimated_edge,
                   decision_grade_status, decision_grade_reasons_json,
                   open_questions_json, counterclaims_json
            FROM research_runs
            WHERE research_run_id = 'run-decision-grade'
            """
        ).fetchone()
        proof_queries_before = conn.execute(
            """
            SELECT ordinal, query, query_intent, source_class
            FROM research_run_queries
            WHERE research_run_id = 'run-decision-grade'
            ORDER BY ordinal
            """
        ).fetchall()
        proof_evidence_before = conn.execute(
            """
            SELECT evidence_id, source_class, source_name, source_url, claim_type,
                   supports_direction, supports_confidence, contract_fingerprint,
                   raw_payload_json
            FROM research_evidence
            WHERE research_run_id = 'run-decision-grade'
            ORDER BY evidence_id
            """
        ).fetchall()

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

    await store.record_research_run(
        "KXCEASEFIRE-26JUL01",
        "run-decision-grade",
        trigger_headline="Current market metadata",
        trigger_source="market_status_refresh",
        attempted=False,
        summary="Metadata-only refresh.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        market_status="closed",
        market_close_time="2026-07-11T18:00:00Z",
        update_dossier_snapshot=False,
        update_dossier_run_id=False,
    )

    retained_snapshot = await store.get_dossier_snapshot("KXCEASEFIRE-26JUL01")
    with sqlite3.connect(db_path) as conn:
        proof_run_after = conn.execute(
            """
            SELECT trigger_headline, trigger_source, contract_question, attempted,
                   summary, verdict_status, skip_reason, force_side,
                   estimated_probability, confidence, market_price, estimated_edge,
                   decision_grade_status, decision_grade_reasons_json,
                   open_questions_json, counterclaims_json
            FROM research_runs
            WHERE research_run_id = 'run-decision-grade'
            """
        ).fetchone()
        proof_queries_after = conn.execute(
            """
            SELECT ordinal, query, query_intent, source_class
            FROM research_run_queries
            WHERE research_run_id = 'run-decision-grade'
            ORDER BY ordinal
            """
        ).fetchall()
        proof_evidence_after = conn.execute(
            """
            SELECT evidence_id, source_class, source_name, source_url, claim_type,
                   supports_direction, supports_confidence, contract_fingerprint,
                   raw_payload_json
            FROM research_evidence
            WHERE research_run_id = 'run-decision-grade'
            ORDER BY evidence_id
            """
        ).fetchall()

    assert proof_run_after == proof_run_before
    assert proof_queries_after == proof_queries_before
    assert proof_evidence_after == proof_evidence_before
    assert retained_snapshot is not None
    assert retained_snapshot.last_research_run_id == "run-decision-grade"
    assert (
        retained_snapshot.last_decision_grade_status
        == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    )
    assert retained_snapshot.last_contract_fingerprint == "contract-v1"
    assert retained_snapshot.last_force_side == "yes"
    assert retained_snapshot.last_estimated_probability == pytest.approx(0.68)
    assert retained_snapshot.last_confidence == pytest.approx(0.82)
    assert retained_snapshot.last_market_price == pytest.approx(0.55)
    assert retained_snapshot.last_estimated_edge == pytest.approx(0.12)
    assert retained_snapshot.market_status == "closed"
    assert retained_snapshot.market_close_time == "2026-07-11T18:00:00Z"


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
            published_at="2026-07-02",
            retrieved_at="2026-07-03T14:00:00+00:00",
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
async def test_research_task_uses_pending_event_horizon_for_backoff(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    event_at = datetime.now(timezone.utc) + timedelta(days=14)

    await store.record_research_run(
        "KXOFFICIALPENDING-26JUL14",
        "run-official-pending-horizon",
        trigger_headline="Official data pending",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Official settlement data has not been released yet.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        skip_reason="official_data_pending",
        decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
        evidence=[
            ResearchEvidence(
                source_class="official_primary",
                source_name="Official agency",
                source_url="https://agency.gov/release",
                title="Release pending",
                snippet="The target-period release is pending.",
                claim_type="official_data_pending",
                published_at=event_at.isoformat(),
                available_at=event_at.isoformat(),
                metric_name="target_period_pending",
            )
        ],
    )

    task = await store.get_research_task_snapshot("KXOFFICIALPENDING-26JUL14")
    persisted = await store.get_research_run_evidence(
        "KXOFFICIALPENDING-26JUL14",
        "run-official-pending-horizon",
    )

    assert task is not None
    assert task.backoff_seconds == 86400.0
    assert persisted[0].available_at == event_at.isoformat()


@pytest.mark.asyncio
async def test_research_task_preserves_open_question_across_empty_same_reason_retry(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    ticker = "KXGAP-26JUL12"
    question = "Which official source reports the contract-window result?"

    await store.record_research_run(
        ticker,
        "run-gap-1",
        trigger_headline="",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Missing settlement-aligned evidence.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        skip_reason="missing_resolution_source",
        decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
        open_questions=[question],
    )
    await store.record_research_run(
        ticker,
        "run-gap-2",
        trigger_headline="",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Still missing settlement-aligned evidence.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        skip_reason="missing_resolution_source",
        decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
        open_questions=[],
    )

    task = await store.get_research_task_snapshot(ticker)

    assert task is not None
    assert task.open_questions == (question,)


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
    assert hasattr(store, "get_due_research_tasks")
    assert [
        (task.market_ticker, task.last_skip_reason)
        for task in store.get_due_research_tasks(
            now=now,
            target_cooldown_seconds=1800.0,
        )
    ] == [
        ("KXDUE-26JUL01", "ambiguous_direction"),
        ("KXNEEDSRESEARCH-26JUL01", "missing_resolution_source"),
        ("KXDECISION-26JUL01", "no_reliable_source_path"),
        ("KXSOURCEPATH-26JUL01", "no_reliable_source_path"),
    ]


@pytest.mark.asyncio
async def test_research_dossier_lists_all_due_tasks_for_reason(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    tickers = [f"KXOFFICIAL-{index:02d}" for index in range(6)]
    for ticker in tickers:
        await store.record_research_run(
            ticker,
            f"run-{ticker}",
            trigger_headline="Official data pending",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Official source has not published yet.",
            verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
            skip_reason="official_data_pending",
            decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
        )
    await store.record_research_run(
        "KXOTHER-REASON",
        "run-other-reason",
        trigger_headline="More research needed",
        trigger_source="research_prewarm",
        attempted=True,
        summary="No usable research hits.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        skip_reason="no_research_hits",
        decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET cooldown_until_ts = NULL,
                backoff_seconds = 0,
                updated_ts = '2026-06-30T10:00:00.000Z'
            """
        )

    assert hasattr(store, "get_due_research_tasks_for_reason")
    due = store.get_due_research_tasks_for_reason(
        "official_data_pending",
        now=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        target_cooldown_seconds=1800.0,
    )

    assert [task.market_ticker for task in due] == tickers
    assert {task.last_skip_reason for task in due} == {"official_data_pending"}


@pytest.mark.asyncio
async def test_due_research_tasks_for_reason_matches_last_skip_or_terminal_reason(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    for ticker in (
        "KXTERMINAL-OFFICIAL-26JUL01",
        "KXLAST-OFFICIAL-26JUL01",
    ):
        await store.record_research_run(
            ticker,
            f"run-{ticker}",
            trigger_headline="Research update",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Another research pass is needed.",
            verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
            skip_reason="no_research_hits",
            decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
        )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET last_skip_reason = 'no_research_hits',
                terminal_reason = 'official_data_pending',
                cooldown_until_ts = NULL,
                backoff_seconds = 0,
                updated_ts = '2026-06-30T10:00:00.000Z'
            WHERE market_ticker = 'KXTERMINAL-OFFICIAL-26JUL01'
            """
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET last_skip_reason = 'official_data_pending',
                terminal_reason = 'no_research_hits',
                cooldown_until_ts = NULL,
                backoff_seconds = 0,
                updated_ts = '2026-06-30T10:00:00.000Z'
            WHERE market_ticker = 'KXLAST-OFFICIAL-26JUL01'
            """
        )

    due = store.get_due_research_tasks_for_reason(
        "official_data_pending",
        now=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        target_cooldown_seconds=1800.0,
    )

    assert {task.market_ticker for task in due} == {
        "KXTERMINAL-OFFICIAL-26JUL01",
        "KXLAST-OFFICIAL-26JUL01",
    }


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
async def test_due_research_tasks_prioritize_active_retry_before_pending_and_terminal(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    for ticker, status, skip_reason in (
        (
            "KXACTIVE-RETRY",
            ResearchStatus.CONTINUE_RESEARCHING.value,
            "research_timeout",
        ),
        (
            "KXPENDING-OFFICIAL",
            ResearchStatus.NEEDS_RESEARCH.value,
            "official_data_pending",
        ),
        (
            "KXTERMINAL-SOURCE",
            ResearchStatus.UNTRADEABLE.value,
            "no_reliable_source_path",
        ),
    ):
        await store.record_research_run(
            ticker,
            f"run-{ticker}",
            trigger_headline="Research update",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Research needs another pass.",
            verdict_status=status,
            skip_reason=skip_reason,
            decision_grade_status=status,
        )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T08:00:00.000Z',
                cooldown_until_ts = NULL
            """
        )

    assert store.get_due_research_task_tickers(
        limit=3,
        now=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        target_cooldown_seconds=0.0,
    ) == [
        "KXACTIVE-RETRY",
        "KXPENDING-OFFICIAL",
        "KXTERMINAL-SOURCE",
    ]


@pytest.mark.asyncio
async def test_due_research_tasks_prioritize_recent_active_retries(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    for ticker, skip_reason in (
        ("KXACTIVE-OLD", "missing_counter_evidence"),
        ("KXACTIVE-RECENT", "research_timeout"),
    ):
        await store.record_research_run(
            ticker,
            f"run-{ticker}",
            trigger_headline="Research timed out",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Research needs another pass.",
            verdict_status=ResearchStatus.CONTINUE_RESEARCHING.value,
            skip_reason=skip_reason,
            decision_grade_status=ResearchStatus.CONTINUE_RESEARCHING.value,
        )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-01T08:00:00.000Z',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXACTIVE-OLD'
            """
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T08:00:00.000Z',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXACTIVE-RECENT'
            """
        )

    assert store.get_due_research_task_tickers(
        limit=2,
        now=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        target_cooldown_seconds=0.0,
    ) == ["KXACTIVE-RECENT", "KXACTIVE-OLD"]


@pytest.mark.asyncio
async def test_due_research_tasks_keep_actionable_source_work_before_active_retries(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    for ticker, status, skip_reason in (
        (
            "KXACTIONABLE-SOURCE",
            ResearchStatus.NEEDS_RESEARCH.value,
            "missing_resolution_source",
        ),
        (
            "KXACTIVE-RETRY",
            ResearchStatus.CONTINUE_RESEARCHING.value,
            "research_timeout",
        ),
    ):
        await store.record_research_run(
            ticker,
            f"run-{ticker}",
            trigger_headline="Research update",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Research needs another pass.",
            verdict_status=status,
            skip_reason=skip_reason,
            decision_grade_status=status,
        )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T08:00:00.000Z',
                cooldown_until_ts = NULL
            """
        )

    assert store.get_due_research_task_tickers(
        limit=2,
        now=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        target_cooldown_seconds=0.0,
    ) == ["KXACTIONABLE-SOURCE", "KXACTIVE-RETRY"]


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
async def test_due_research_tasks_honor_existing_official_pending_cooldown(tmp_path):
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

    now = datetime(2026, 6, 30, 12, tzinfo=timezone.utc)
    assert store.get_due_research_task_tickers(
        now=now,
        target_cooldown_seconds=0.0,
    ) == []
    assert store.get_due_research_tasks_for_reason(
        "official_data_pending",
        now=now,
        target_cooldown_seconds=0.0,
    ) == []


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
async def test_research_dossier_persists_pending_edge_origin(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()

    await store.record_research_run(
        "KXEDGE-26AUG01",
        "run-pending-edge",
        trigger_headline="Current predictive evidence",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Official event window remains open.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        skip_reason="official_data_pending",
        research_pending_origin="negative_net_edge_after_costs",
        contract_fingerprint="contract-pending-edge",
    )

    snapshot = await store.get_dossier_snapshot("KXEDGE-26AUG01")
    with sqlite3.connect(db_path) as conn:
        run_origin = conn.execute(
            """
            SELECT research_pending_origin
            FROM research_runs
            WHERE research_run_id = 'run-pending-edge'
            """
        ).fetchone()
        dossier_origin = conn.execute(
            """
            SELECT last_research_pending_origin
            FROM research_dossiers
            WHERE market_ticker = 'KXEDGE-26AUG01'
            """
        ).fetchone()

    assert run_origin == ("negative_net_edge_after_costs",)
    assert dossier_origin == ("negative_net_edge_after_costs",)
    assert snapshot is not None
    assert snapshot.last_research_pending_origin == "negative_net_edge_after_costs"


@pytest.mark.asyncio
async def test_research_dossier_migrates_pending_edge_origin_columns(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    current_store = ResearchDossierStore(db_path)
    await current_store.initialize()

    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE research_dossiers DROP COLUMN last_research_pending_origin")
        conn.execute("ALTER TABLE research_runs DROP COLUMN research_pending_origin")

    upgraded_store = ResearchDossierStore(db_path)
    await upgraded_store.initialize()

    with sqlite3.connect(db_path) as conn:
        dossier_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(research_dossiers)")
        }
        run_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(research_runs)")
        }

    assert "last_research_pending_origin" in dossier_columns
    assert "research_pending_origin" in run_columns


@pytest.mark.asyncio
async def test_research_dossier_rejects_invalid_pending_edge_origin(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    with pytest.raises(ValueError, match="research_pending_origin"):
        await store.record_research_run(
            "KXEDGE-26AUG01",
            "run-invalid-pending-edge",
            trigger_headline="Current predictive evidence",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Official event window remains open.",
            verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
            skip_reason="official_data_pending",
            research_pending_origin="untrusted_origin",
            contract_fingerprint="contract-pending-edge",
        )


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
@pytest.mark.asyncio
async def test_persisted_future_nws_evidence_invalidates_decision_grade_snapshot(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    ticker = "KXHIGHNY-26JUL11-B81.5"
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS",
            source_url="https://weather.gov/climate",
            title="Central Park high temperature July 11",
            snippet="The July 11 maximum supports YES.",
            claim_type="official_resolution",
            supports_direction="yes",
            supports_confidence=0.95,
            published_at="2026-07-11",
            retrieved_at="2026-07-10T14:11:00+00:00",
            metric_name="nws_daily_high_temp_f",
            metric_value=82.0,
            extraction_confidence=0.95,
            contract_fingerprint="contract-v1",
        ),
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS",
            source_url="https://weather.gov/climate",
            title="Central Park high temperature countercheck July 11",
            snippet="The same official maximum was checked against the contract.",
            claim_type="disconfirming",
            supports_direction="yes",
            supports_confidence=0.95,
            published_at="2026-07-11",
            retrieved_at="2026-07-10T14:11:00+00:00",
            metric_name="nws_daily_high_temp_f",
            metric_value=82.0,
            extraction_confidence=0.95,
            contract_fingerprint="contract-v1",
        ),
    ]
    await store.record_research_run(
        ticker,
        "run-future-nws",
        trigger_headline="Central Park high temperature July 11",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Future-dated NWS evidence must not remain decision grade.",
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
                query="Central Park high temperature July 11 official result",
                query_intent="official_resolution",
                source_class="official_primary",
            ),
            ResearchQuery(
                query="Central Park high temperature July 11 disconfirming evidence",
                query_intent="disconfirming",
                source_class="official_primary",
            ),
        ],
        evidence=evidence,
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM research_dossiers WHERE market_ticker = ?",
            (ticker,),
        ).fetchone()
        assert row is not None
        assert (
            _stored_decision_grade_snapshot_is_valid_sync(
                conn,
                market_ticker=ticker,
                row=row,
            )
            is False
        )


def _official_p_evidence(*, metric: str, probability: float, direction: str) -> ResearchEvidence:
    retrieved = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return ResearchEvidence(
        source_class="official_primary",
        source_name="Roll Call Factbase Truth Social records",
        source_url="https://rollcall.com/wp-json/factbase/v1/twitter",
        title="official p",
        snippet=f"Implied YES probability {probability:.3f}.",
        claim_type="official_resolution",
        supports_direction=direction,
        supports_confidence=0.85,
        retrieved_at=retrieved,
        metric_name=metric,
        metric_value=probability,
        metric_unit="probability",
        extraction_confidence=0.96,
    )


def test_persistence_keeps_decision_grade_when_official_p_is_labeled_the_other_side(monkeypatch):
    """B209: p=0.39 labeled NO, trade YES. Persist must not rewrite neutral_only."""
    monkeypatch.setattr(
        "utils.event_news_research.is_event_news_paper_cohort",
        lambda _config=None: True,
    )
    evidence = [_official_p_evidence(
        metric="truth_social_range_probability",
        probability=0.39,
        direction="no",
    )]
    quality = _decision_grade_persistence_quality(
        ticker="KXTRUTHSOCIAL-26AUG29-B209",
        side="yes",
        queries=[
            SimpleNamespace(query="q", query_intent="official_resolution"),
            SimpleNamespace(query="c", query_intent="contradiction_check"),
        ],
        evidence=evidence,
    )
    assert quality["has_directional_evidence"] is True
    assert quality["has_counter_evidence"] is True
    status, grade, skip = _validated_research_status(
        market_ticker="KXTRUTHSOCIAL-26AUG29-B209",
        verdict_status="decision_grade_candidate",
        decision_grade_status="decision_grade_candidate",
        skip_reason=None,
        force_side="yes",
        queries=[],
        evidence=evidence,
    )
    assert status == "decision_grade_candidate"
    assert skip is None


def test_persistence_clears_leftover_skip_when_official_p_is_decision_grade(monkeypatch):
    """Trade YES/NO must not stay parked on a leftover skip_reason."""
    monkeypatch.setattr(
        "utils.event_news_research.is_event_news_paper_cohort",
        lambda _config=None: True,
    )
    evidence = [_official_p_evidence(
        metric="truth_social_range_probability",
        probability=0.39,
        direction="no",
    )]
    status, _grade, skip = _validated_research_status(
        market_ticker="KXTRUTHSOCIAL-26AUG29-B209",
        verdict_status="decision_grade_candidate",
        decision_grade_status="decision_grade_candidate",
        skip_reason="neutral_only_evidence",
        force_side="yes",
        queries=[],
        evidence=evidence,
    )
    assert status == "decision_grade_candidate"
    assert skip is None


def test_persistence_keeps_decision_grade_for_white_house_remaining_time_p(monkeypatch):
    monkeypatch.setattr(
        "utils.event_news_research.is_event_news_paper_cohort",
        lambda _config=None: True,
    )
    evidence = [_official_p_evidence(
        metric="white_house_action_range_probability",
        probability=0.77,
        direction="yes",
    )]
    evidence[0] = replace_wh_source(evidence[0])
    status, _grade, skip = _validated_research_status(
        market_ticker="KXTRUMPACT-26AUG23-T6",
        verdict_status="decision_grade_candidate",
        decision_grade_status="decision_grade_candidate",
        skip_reason=None,
        force_side="yes",
        queries=[],
        evidence=evidence,
    )
    assert status == "decision_grade_candidate"
    assert skip is None


def replace_wh_source(item: ResearchEvidence) -> ResearchEvidence:
    from dataclasses import replace
    return replace(
        item,
        source_name="White House Presidential Actions",
        source_url="https://www.whitehouse.gov/presidential-actions/",
    )
