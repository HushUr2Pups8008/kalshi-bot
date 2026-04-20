"""
Async orchestration for the accumulation lane (S2.5).

The task wires feed-derived evidence through pure analysis components and the
evidence store. It does not score evidence itself, build dossier policy, blend
signals, or interact with trading.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from analysis.evidence_scorer import score_evidence
from analysis.evidence_types import Dossier, Evidence, EvidenceScore
from tasks.evidence_store import (
    DossierState,
    DossierUpdateRecord,
    DuplicateEvidenceError,
    EvidenceRecord,
    EvidenceStore,
    default_store,
)
from utils.logger import trade_log, write_trade_log_async


class AccumulationTaskError(Exception):
    """Base class for accumulation task orchestration failures."""


class AccumulationDependencyError(AccumulationTaskError):
    """Raised when a required upstream S2 dependency is unavailable."""


class AccumulationProcessingError(AccumulationTaskError):
    """Raised when processing one evidence item fails."""


class EvidenceScorer(Protocol):
    def __call__(
        self,
        evidence: Evidence,
        recent_market_evidence: list[Evidence],
    ) -> EvidenceScore:
        ...


class DossierBuilder(Protocol):
    def __call__(
        self,
        current_dossier: Dossier,
        new_evidence_score: EvidenceScore,
        update_type: str,
    ) -> Dossier:
        ...


class UpdateClassifier(Protocol):
    def __call__(
        self,
        current_dossier: Dossier,
        new_evidence_score: EvidenceScore,
    ) -> str:
        ...


@dataclass(frozen=True)
class AccumulationResult:
    evidence_id: str
    market_ticker: str
    status: str
    dossier_version_before: int | None = None
    dossier_version_after: int | None = None
    update_type: str | None = None
    error: str | None = None


class AccumulationTask:
    """Orchestrates scorer -> builder -> evidence store for evidence items."""

    def __init__(
        self,
        *,
        store: EvidenceStore | None = None,
        scorer: EvidenceScorer = score_evidence,
        builder: DossierBuilder | None = None,
        update_classifier: UpdateClassifier | None = None,
        logger=trade_log,
        recent_evidence_limit: int = 100,
    ) -> None:
        self.store = store or default_store()
        self.scorer = scorer
        self.builder = builder or _load_default_builder()
        self.update_classifier = update_classifier or _load_default_update_classifier()
        self.logger = logger
        self.recent_evidence_limit = recent_evidence_limit
        self._market_locks: dict[str, asyncio.Lock] = {}
        self._market_locks_guard = asyncio.Lock()

    async def process_evidence(self, evidence: Evidence) -> AccumulationResult:
        """Process one evidence item through scorer, builder, store, and telemetry."""
        lock = await self._lock_for_market(evidence.market_ticker)
        async with lock:
            return await self._process_evidence_locked(evidence)

    async def _process_evidence_locked(self, evidence: Evidence) -> AccumulationResult:
        try:
            current_state = await self.store.get_dossier(evidence.market_ticker)
            if current_state is None:
                current_dossier = _initial_dossier(evidence)
            else:
                current_dossier = _dossier_from_state(current_state)

            recent_records = await self.store.get_recent_evidence(
                evidence.market_ticker,
                limit=self.recent_evidence_limit,
            )
            recent_evidence = [_evidence_from_record(record) for record in recent_records]

            score = self.scorer(evidence, recent_evidence)
            update_type = self.update_classifier(current_dossier, score)
            next_dossier = self.builder(current_dossier, score, update_type)

            evidence_record = _evidence_record_from_score(
                evidence=evidence,
                score=score,
                update_type=update_type,
                dossier_version_before=current_dossier.dossier_version,
                dossier_version_after=next_dossier.dossier_version,
            )
            update_record = _update_record_from_dossiers(
                current=current_dossier,
                updated=next_dossier,
                trigger_evidence_id=evidence.evidence_id,
                update_type=update_type,
            )
            contributing_ids = _contributing_evidence_ids(
                recent_records,
                evidence.evidence_id,
            )

            if current_state is None:
                await self.store.update_dossier(_state_from_dossier(current_dossier))
            await self.store.add_evidence(evidence_record)
            await self.store.update_dossier(
                _state_from_dossier(next_dossier),
                update=update_record,
                evidence_ids_contributing=contributing_ids,
            )

            await write_trade_log_async(
                self.logger.log_evidence_ingestion,
                market_ticker=evidence.market_ticker,
                evidence_id=evidence.evidence_id,
                source_class=score.source_class,
                is_duplicate=score.is_duplicate,
                correlation_discount_applied=score.correlation_discount_applied,
                update_type=update_type,
                dossier_version_before=current_dossier.dossier_version,
                dossier_version_after=next_dossier.dossier_version,
            )
            await write_trade_log_async(
                self.logger.log_dossier_update,
                market_ticker=next_dossier.market_ticker,
                dossier_version=next_dossier.dossier_version,
                prior_estimate=current_dossier.current_estimate or 0.0,
                new_estimate=next_dossier.current_estimate or 0.0,
                update_delta=_estimate_delta(current_dossier, next_dossier),
                confidence_before=current_dossier.confidence,
                confidence_after=next_dossier.confidence,
                evidence_ids_contributing=contributing_ids,
                llm_called=False,
                drift_suspect=next_dossier.drift_suspect,
                in_recovery=next_dossier.in_recovery,
            )
            return AccumulationResult(
                evidence_id=evidence.evidence_id,
                market_ticker=evidence.market_ticker,
                status="processed",
                dossier_version_before=current_dossier.dossier_version,
                dossier_version_after=next_dossier.dossier_version,
                update_type=update_type,
            )
        except DuplicateEvidenceError as exc:
            return AccumulationResult(
                evidence_id=evidence.evidence_id,
                market_ticker=evidence.market_ticker,
                status="duplicate_evidence",
                error=str(exc),
            )
        except Exception as exc:
            raise AccumulationProcessingError(
                f"failed to process evidence {evidence.evidence_id}"
            ) from exc

    async def _lock_for_market(self, market_ticker: str) -> asyncio.Lock:
        async with self._market_locks_guard:
            lock = self._market_locks.get(market_ticker)
            if lock is None:
                lock = asyncio.Lock()
                self._market_locks[market_ticker] = lock
            return lock

    async def run(
        self,
        queue: asyncio.Queue[Evidence | None],
        *,
        stop_on_error: bool = False,
    ) -> None:
        """Run until a ``None`` sentinel is received from the queue."""
        while True:
            evidence = await queue.get()
            try:
                if evidence is None:
                    return
                await self.process_evidence(evidence)
            except Exception:
                if stop_on_error:
                    raise
            finally:
                queue.task_done()


def _load_default_builder() -> DossierBuilder:
    try:
        from analysis.dossier_builder import update_dossier as builder_update_dossier
    except ImportError as exc:
        raise AccumulationDependencyError(
            "analysis.dossier_builder.update_dossier is required for AccumulationTask; "
            "pass builder=... while S2.4 is unavailable"
        ) from exc
    return builder_update_dossier


def _load_default_update_classifier() -> UpdateClassifier:
    try:
        from analysis.dossier_builder import classify_update
    except ImportError as exc:
        raise AccumulationDependencyError(
            "analysis.dossier_builder.classify_update is required for AccumulationTask; "
            "pass update_classifier=... while S2.4 is unavailable"
        ) from exc
    return classify_update


def _initial_dossier(evidence: Evidence) -> Dossier:
    return Dossier(
        market_ticker=evidence.market_ticker,
        dossier_version=0,
        confidence=0.0,
        drift_suspect=False,
        in_recovery=False,
        created_ts=evidence.ingested_ts,
        updated_ts=evidence.ingested_ts,
    )


def _dossier_from_state(state: DossierState) -> Dossier:
    return Dossier(
        market_ticker=state.market_ticker,
        dossier_version=state.dossier_version,
        confidence=state.confidence,
        drift_suspect=state.drift_suspect,
        in_recovery=state.in_recovery,
        created_ts=state.created_ts,
        updated_ts=state.updated_ts,
        current_estimate=state.current_estimate,
        prior_estimate=state.prior_estimate,
        freeze_started_ts=state.freeze_started_ts,
        recovery_started_ts=state.recovery_started_ts,
        recovery_until_ts=state.recovery_until_ts,
        last_cross_class_state_update_ts=state.last_cross_class_state_update_ts,
    )


def _state_from_dossier(dossier: Dossier) -> DossierState:
    return DossierState(
        market_ticker=dossier.market_ticker,
        dossier_version=dossier.dossier_version,
        current_estimate=dossier.current_estimate,
        confidence=dossier.confidence,
        prior_estimate=dossier.prior_estimate,
        drift_suspect=dossier.drift_suspect,
        in_recovery=dossier.in_recovery,
        freeze_started_ts=dossier.freeze_started_ts,
        recovery_started_ts=dossier.recovery_started_ts,
        recovery_until_ts=dossier.recovery_until_ts,
        last_cross_class_state_update_ts=dossier.last_cross_class_state_update_ts,
        created_ts=dossier.created_ts,
        updated_ts=dossier.updated_ts,
    )


def _evidence_from_record(record: EvidenceRecord) -> Evidence:
    return Evidence(
        evidence_id=record.evidence_id,
        market_ticker=record.market_ticker,
        source=record.source,
        source_class=record.source_class,
        headline=record.headline,
        ingested_ts=record.ingested_ts,
        implied_probability=0.0,
        content_hash=record.content_hash,
        url=record.url,
        published_ts=record.published_ts,
    )


def _evidence_record_from_score(
    *,
    evidence: Evidence,
    score: EvidenceScore,
    update_type: str,
    dossier_version_before: int,
    dossier_version_after: int,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence.evidence_id,
        market_ticker=evidence.market_ticker,
        source=evidence.source,
        source_class=evidence.source_class,
        headline=evidence.headline,
        ingested_ts=evidence.ingested_ts,
        content_hash=evidence.content_hash,
        update_type=update_type,
        dossier_version_before=dossier_version_before,
        dossier_version_after=dossier_version_after,
        url=evidence.url,
        published_ts=evidence.published_ts,
        raw_payload_json=json.dumps(
            {
                "implied_probability": score.implied_probability,
                "evidence_id": evidence.evidence_id,
                "content_hash": evidence.content_hash,
            },
            sort_keys=True,
        ),
        is_duplicate=score.is_duplicate,
        correlation_discount_applied=score.correlation_discount_applied,
        quality_score=score.quality_score,
        original_weight=score.original_weight,
    )


def _update_record_from_dossiers(
    *,
    current: Dossier,
    updated: Dossier,
    trigger_evidence_id: str,
    update_type: str,
) -> DossierUpdateRecord:
    return DossierUpdateRecord(
        market_ticker=updated.market_ticker,
        dossier_version=updated.dossier_version,
        created_ts=updated.updated_ts,
        trigger_evidence_id=trigger_evidence_id,
        prior_estimate=current.current_estimate,
        new_estimate=updated.current_estimate,
        update_delta=_estimate_delta(current, updated),
        confidence_before=current.confidence,
        confidence_after=updated.confidence,
        update_type=update_type,
        llm_called=False,
        drift_suspect=updated.drift_suspect,
        in_recovery=updated.in_recovery,
    )


def _estimate_delta(current: Dossier, updated: Dossier) -> float:
    if current.current_estimate is None or updated.current_estimate is None:
        return 0.0
    return updated.current_estimate - current.current_estimate


def _contributing_evidence_ids(
    recent_records: Sequence[EvidenceRecord],
    current_evidence_id: str,
) -> list[str]:
    existing = [record.evidence_id for record in reversed(recent_records)]
    return [*existing, current_evidence_id]
