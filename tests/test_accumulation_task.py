import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from analysis.evidence_types import Dossier, Evidence, EvidenceScore
from tasks.accumulation_task import (
    AccumulationProcessingError,
    AccumulationTask,
)
from tasks.evidence_store import EvidenceStore


TS0 = "2026-04-19T00:00:00+00:00"
TS1 = "2026-04-19T00:01:00+00:00"
TS2 = "2026-04-19T00:02:00+00:00"


def _store(tmp_path: Path) -> EvidenceStore:
    return EvidenceStore(tmp_path / "evidence_store.db")


def _evidence(
    evidence_id: str = "ev-1",
    *,
    market_ticker: str = "KXACCUM-26DEC31",
    source_class: str = "news",
    implied_probability: float = 0.70,
    content_hash: str | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        market_ticker=market_ticker,
        source="Reuters",
        source_class=source_class,
        headline=f"Evidence headline {evidence_id}",
        ingested_ts=TS1,
        implied_probability=implied_probability,
        content_hash=content_hash or f"hash-{evidence_id}",
        url=f"https://example.test/{evidence_id}",
        published_ts=TS0,
    )


class SpyLogger:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.evidence_ingestions: list[dict] = []
        self.dossier_updates: list[dict] = []

    def log_evidence_ingestion(self, **payload) -> None:
        self.events.append("log_evidence_ingestion")
        self.evidence_ingestions.append(payload)

    def log_dossier_update(self, **payload) -> None:
        self.events.append("log_dossier_update")
        self.dossier_updates.append(payload)


class SpyScorer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[Evidence, list[Evidence]]] = []

    def __call__(
        self,
        evidence: Evidence,
        recent_market_evidence: list[Evidence],
    ) -> EvidenceScore:
        self.calls.append((evidence, recent_market_evidence))
        if self.fail:
            raise RuntimeError("scorer failed")
        return EvidenceScore(
            evidence_id=evidence.evidence_id,
            source_class=evidence.source_class,
            quality_score=0.70,
            original_weight=0.70,
            is_duplicate=False,
            correlation_discount_applied=False,
            is_independent=True,
            same_class_count=0,
            implied_probability=evidence.implied_probability,
        )


class SpyBuilder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[Dossier, EvidenceScore, str]] = []

    def __call__(
        self,
        current_dossier: Dossier,
        new_evidence_score: EvidenceScore,
        update_type: str,
    ) -> Dossier:
        self.calls.append((current_dossier, new_evidence_score, update_type))
        if self.fail:
            raise RuntimeError("builder failed")
        return Dossier(
            market_ticker=current_dossier.market_ticker,
            dossier_version=current_dossier.dossier_version + 1,
            current_estimate=new_evidence_score.implied_probability,
            confidence=min(0.95, current_dossier.confidence + 0.20),
            prior_estimate=new_evidence_score.implied_probability,
            drift_suspect=False,
            in_recovery=False,
            created_ts=current_dossier.created_ts,
            updated_ts=TS2,
        )


def _classifier(_: Dossier, __: EvidenceScore) -> str:
    return "state"


def _task(
    tmp_path: Path,
    *,
    scorer: SpyScorer | None = None,
    builder: SpyBuilder | None = None,
    logger: SpyLogger | None = None,
) -> tuple[AccumulationTask, SpyScorer, SpyBuilder, SpyLogger]:
    scorer = scorer or SpyScorer()
    builder = builder or SpyBuilder()
    logger = logger or SpyLogger()
    return (
        AccumulationTask(
            store=_store(tmp_path),
            scorer=scorer,
            builder=builder,
            update_classifier=_classifier,
            logger=logger,
        ),
        scorer,
        builder,
        logger,
    )


@pytest.mark.asyncio
async def test_process_evidence_wires_scorer_builder_store_and_telemetry(tmp_path: Path):
    task, scorer, builder, logger = _task(tmp_path)

    result = await task.process_evidence(_evidence())

    assert result.status == "processed"
    assert result.dossier_version_before == 0
    assert result.dossier_version_after == 1
    assert result.update_type == "state"
    assert scorer.calls[0][0].evidence_id == "ev-1"
    assert scorer.calls[0][1] == []
    assert builder.calls[0][2] == "state"
    assert logger.events == ["log_evidence_ingestion", "log_dossier_update"]
    assert logger.evidence_ingestions == [
        {
            "market_ticker": "KXACCUM-26DEC31",
            "evidence_id": "ev-1",
            "source_class": "news",
            "is_duplicate": False,
            "correlation_discount_applied": False,
            "update_type": "state",
            "dossier_version_before": 0,
            "dossier_version_after": 1,
        }
    ]
    assert logger.dossier_updates[0]["market_ticker"] == "KXACCUM-26DEC31"
    assert logger.dossier_updates[0]["dossier_version"] == 1
    assert logger.dossier_updates[0]["evidence_ids_contributing"] == ["ev-1"]
    assert logger.dossier_updates[0]["llm_called"] is False

    state = await task.store.get_dossier("KXACCUM-26DEC31")
    assert state is not None
    assert state.dossier_version == 1
    assert state.current_estimate == pytest.approx(0.70)

    with sqlite3.connect(task.store.db_path) as conn:
        evidence_count = conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        update_count = conn.execute("SELECT COUNT(*) FROM dossier_updates").fetchone()[0]
        payload_json = conn.execute(
            "SELECT raw_payload_json FROM evidence WHERE evidence_id = ?",
            ("ev-1",),
        ).fetchone()[0]

    assert evidence_count == 1
    assert update_count == 1
    payload = json.loads(payload_json)
    assert payload["implied_probability"] == pytest.approx(0.70)
    assert payload["evidence_id"] == "ev-1"
    assert payload["content_hash"] == "hash-ev-1"


@pytest.mark.asyncio
async def test_existing_recent_evidence_is_passed_to_scorer(tmp_path: Path):
    task, scorer, _, logger = _task(tmp_path)

    await task.process_evidence(_evidence("ev-1"))
    await task.process_evidence(_evidence("ev-2", source_class="official"))

    second_recent = scorer.calls[1][1]
    assert [evidence.evidence_id for evidence in second_recent] == ["ev-1"]
    assert logger.dossier_updates[-1]["evidence_ids_contributing"] == ["ev-1", "ev-2"]


@pytest.mark.asyncio
async def test_duplicate_evidence_returns_duplicate_without_second_telemetry(tmp_path: Path):
    task, _, _, logger = _task(tmp_path)

    first = await task.process_evidence(_evidence("ev-1"))
    second = await task.process_evidence(_evidence("ev-1"))

    assert first.status == "processed"
    assert second.status == "duplicate_evidence"
    assert len(logger.evidence_ingestions) == 1
    assert len(logger.dossier_updates) == 1

    state = await task.store.get_dossier("KXACCUM-26DEC31")
    assert state is not None
    assert state.dossier_version == 1


@pytest.mark.asyncio
async def test_scorer_failure_is_wrapped_without_inserting_evidence(tmp_path: Path):
    task, _, _, _ = _task(tmp_path, scorer=SpyScorer(fail=True))

    with pytest.raises(AccumulationProcessingError, match="failed to process evidence ev-1"):
        await task.process_evidence(_evidence())

    assert await task.store.get_dossier("KXACCUM-26DEC31") is None
    assert await task.store.get_recent_evidence("KXACCUM-26DEC31") == []


@pytest.mark.asyncio
async def test_builder_failure_is_wrapped_without_inserting_evidence(tmp_path: Path):
    task, _, _, _ = _task(tmp_path, builder=SpyBuilder(fail=True))

    with pytest.raises(AccumulationProcessingError, match="failed to process evidence ev-1"):
        await task.process_evidence(_evidence())

    assert await task.store.get_dossier("KXACCUM-26DEC31") is None
    assert await task.store.get_recent_evidence("KXACCUM-26DEC31") == []


@pytest.mark.asyncio
async def test_same_market_concurrent_updates_serialize_full_pipeline(tmp_path: Path):
    task, _, _, logger = _task(tmp_path)

    results = await asyncio.gather(
        task.process_evidence(_evidence("ev-1")),
        task.process_evidence(_evidence("ev-2")),
    )

    assert sorted(result.dossier_version_after for result in results) == [1, 2]
    state = await task.store.get_dossier("KXACCUM-26DEC31")
    assert state is not None
    assert state.dossier_version == 2
    assert len(logger.dossier_updates) == 2


@pytest.mark.asyncio
async def test_different_market_lock_keys_are_independent(tmp_path: Path):
    task, _, _, _ = _task(tmp_path)

    first = await task._lock_for_market("KXACCUM-A")
    second = await task._lock_for_market("KXACCUM-B")
    again = await task._lock_for_market("KXACCUM-A")

    assert first is again
    assert first is not second


@pytest.mark.asyncio
async def test_run_processes_queue_until_sentinel(tmp_path: Path):
    task, _, _, logger = _task(tmp_path)
    queue: asyncio.Queue[Evidence | None] = asyncio.Queue()
    await queue.put(_evidence("ev-1"))
    await queue.put(_evidence("ev-2", market_ticker="KXACCUM-OTHER"))
    await queue.put(None)

    await task.run(queue)

    assert len(logger.evidence_ingestions) == 2
    assert queue.empty()
