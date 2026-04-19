import json
from datetime import datetime
from pathlib import Path

import pytest

from analysis.dossier_builder import update_dossier
from analysis.evidence_types import Dossier, EvidenceScore
from scripts.replay_dossier import (
    ReplayDefect,
    render_text,
    replay_market,
    result_to_dict,
)
from tasks.evidence_store import DossierState, EvidenceRecord, EvidenceStore


MARKET = "KXREPLAY-26DEC31"
TS0 = "2026-04-19T00:00:00+00:00"
TS1 = "2026-04-19T00:01:00+00:00"
TS2 = "2026-04-19T00:02:00+00:00"


def _store(tmp_path: Path) -> EvidenceStore:
    return EvidenceStore(tmp_path / "evidence_store.db")


def _record(
    evidence_id: str,
    *,
    ingested_ts: str,
    implied_probability: float,
    source_class: str = "news",
    update_type: str = "state",
    version_before: int = 0,
    version_after: int = 1,
    raw_payload_json: str | None = "use-default",
    correlation_discount_applied: bool = False,
    quality_score: float = 0.70,
    original_weight: float = 0.70,
) -> EvidenceRecord:
    payload = (
        json.dumps({"implied_probability": implied_probability})
        if raw_payload_json == "use-default"
        else raw_payload_json
    )
    return EvidenceRecord(
        evidence_id=evidence_id,
        market_ticker=MARKET,
        source="Reuters",
        source_class=source_class,
        headline=f"Replay headline {evidence_id}",
        ingested_ts=ingested_ts,
        content_hash=f"hash-{evidence_id}",
        update_type=update_type,
        dossier_version_before=version_before,
        dossier_version_after=version_after,
        raw_payload_json=payload,
        correlation_discount_applied=correlation_discount_applied,
        quality_score=quality_score,
        original_weight=original_weight,
    )


def _initial_state(created_ts: str = TS1) -> DossierState:
    return DossierState(
        market_ticker=MARKET,
        dossier_version=0,
        current_estimate=None,
        confidence=0.0,
        prior_estimate=None,
        created_ts=created_ts,
        updated_ts=created_ts,
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


def _score(record: EvidenceRecord) -> EvidenceScore:
    payload = json.loads(record.raw_payload_json or "{}")
    return EvidenceScore(
        evidence_id=record.evidence_id,
        source_class=record.source_class,
        quality_score=float(record.quality_score or 0.0),
        original_weight=float(record.original_weight or 0.0),
        is_duplicate=record.is_duplicate,
        correlation_discount_applied=record.correlation_discount_applied,
        is_independent=not record.correlation_discount_applied,
        same_class_count=0,
        implied_probability=float(payload["implied_probability"]),
    )


def _expected_dossier(records: list[EvidenceRecord]) -> Dossier:
    dossier = _dossier_from_state(_initial_state(records[0].ingested_ts))
    for record in sorted(records, key=lambda item: (item.ingested_ts, item.evidence_id)):
        dossier = update_dossier(
            dossier,
            _score(record),
            record.update_type,
            now=datetime.fromisoformat(record.ingested_ts),
        )
    return dossier


@pytest.mark.asyncio
async def test_replay_reconstructs_synthetic_event_chain_and_matches_stored(tmp_path: Path):
    store = _store(tmp_path)
    first = _record("ev-1", ingested_ts=TS1, implied_probability=0.70)
    second = _record(
        "ev-2",
        ingested_ts=TS2,
        implied_probability=0.80,
        source_class="official",
        version_before=1,
        version_after=2,
    )
    final = _expected_dossier([first, second])

    await store.update_dossier(_initial_state())
    await store.add_evidence(first)
    await store.add_evidence(second)
    await store.update_dossier(_state_from_dossier(final))

    result = replay_market(MARKET, db_path=store.db_path)

    assert result.comparison.status == "match"
    assert result.event_count == 2
    assert [step.evidence_id for step in result.steps] == ["ev-1", "ev-2"]
    assert result.final_dossier is not None
    assert result.final_dossier.current_estimate == pytest.approx(final.current_estimate)
    assert "DOSSIER REPLAY" in render_text(result)


@pytest.mark.asyncio
async def test_replay_is_deterministic_across_repeated_runs(tmp_path: Path):
    store = _store(tmp_path)
    record = _record("ev-1", ingested_ts=TS1, implied_probability=0.64)
    final = _expected_dossier([record])

    await store.update_dossier(_initial_state())
    await store.add_evidence(record)
    await store.update_dossier(_state_from_dossier(final))

    first = replay_market(MARKET, db_path=store.db_path)
    second = replay_market(MARKET, db_path=store.db_path)

    assert result_to_dict(first) == result_to_dict(second)


@pytest.mark.asyncio
async def test_replay_orders_by_timestamp_then_immutable_event_id(tmp_path: Path):
    store = _store(tmp_path)
    later_inserted_first = _record(
        "ev-b",
        ingested_ts=TS1,
        implied_probability=0.80,
        version_before=1,
        version_after=2,
    )
    tie_breaks_first = _record("ev-a", ingested_ts=TS1, implied_probability=0.60)
    final = _expected_dossier([later_inserted_first, tie_breaks_first])

    await store.update_dossier(_initial_state())
    await store.add_evidence(later_inserted_first)
    await store.add_evidence(tie_breaks_first)
    await store.update_dossier(_state_from_dossier(final))

    result = replay_market(MARKET, db_path=store.db_path)

    assert [step.evidence_id for step in result.steps] == ["ev-a", "ev-b"]
    assert result.comparison.status == "match"


@pytest.mark.asyncio
async def test_replay_surfaces_divergence_from_stored_dossier(tmp_path: Path):
    store = _store(tmp_path)
    record = _record("ev-1", ingested_ts=TS1, implied_probability=0.70)
    final = _expected_dossier([record])

    await store.update_dossier(_initial_state())
    await store.add_evidence(record)
    await store.update_dossier(
        DossierState(
            **{
                **_state_from_dossier(final).__dict__,
                "current_estimate": 0.42,
            }
        )
    )

    result = replay_market(MARKET, db_path=store.db_path)

    assert result.comparison.status == "diverged"
    assert "current_estimate" in result.comparison.differences


@pytest.mark.asyncio
async def test_replay_fails_loudly_when_implied_probability_is_not_persisted(tmp_path: Path):
    store = _store(tmp_path)
    record = _record(
        "ev-1",
        ingested_ts=TS1,
        implied_probability=0.70,
        raw_payload_json=None,
    )

    await store.update_dossier(_initial_state())
    await store.add_evidence(record)

    with pytest.raises(ReplayDefect, match="raw_payload_json.implied_probability"):
        replay_market(MARKET, db_path=store.db_path)
