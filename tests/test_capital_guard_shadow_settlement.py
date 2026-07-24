from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from kalshi import KalshiMarket
from polymarket.settlement_reconciler import SettlementNotFound
from tasks.capital_guard_shadow_settlement import (
    CapitalGuardShadowSettlementCollector,
)
from tests.test_capital_guard_shadow import (
    NOW,
    _append_candidate,
    _json,
    candidate,
)
from trading.capital_guard_shadow import (
    MAX_SETTLEMENT_CANDIDATES_PER_MARKET,
    MAX_SETTLEMENT_MARKETS_PER_RUN,
    CapitalGuardShadowIdentityError,
    CapitalGuardShadowStore,
    SettlementMarketKey,
    canonical_json,
)
from trading.authoritative_settlement_source import AuthoritativeSettlementSource
from trading.fees import (
    POLYMARKET_US_2026_07_01,
    FeeRole,
    fee_coefficient_for,
    fee_type_for_schedule,
    serialize_fee_schedule,
)
from trading.settlement import (
    MarketOutcome,
    SettlementDriftError,
    SettlementObservation,
    UnsupportedVoidError,
    VoidRefundContract,
    build_settlement_observation,
)
from trading.venue import MarketRef, Venue


UTC = timezone.utc


def _authoritative(
    market_ref: MarketRef,
    *,
    outcome: MarketOutcome = MarketOutcome.YES,
    observed_at: datetime = NOW + timedelta(days=1),
    effective_at: datetime | None = None,
    payload: object | None = None,
    previous_observation: SettlementObservation | None = None,
    supersedes_observation_sha256: str | None = None,
):
    return build_settlement_observation(
        market_ref=market_ref,
        outcome=outcome,
        authoritative_outcome=outcome.value,
        authoritative_payload=(
            payload
            if payload is not None
            else {
                "market_id": market_ref.venue_market_id,
                "result": outcome.value,
            }
        ),
        observed_at=observed_at,
        effective_at=effective_at or observed_at,
        rules_version=("kalshi-settlement-v1" if market_ref.venue is Venue.KALSHI else "polymarket-us-settlement-v1"),
        source_id=("kalshi-market-api" if market_ref.venue is Venue.KALSHI else "polymarket-us-public-api"),
        previous_observation=previous_observation,
        supersedes_observation_sha256=supersedes_observation_sha256,
    )


class SequenceSource:
    def __init__(self, values: dict[MarketRef, list[object]]) -> None:
        self.values = values
        self.calls: list[MarketRef] = []
        self.prior_observations: list[object | None] = []

    async def get_settlement_exact(
        self,
        market_ref: MarketRef,
        *,
        prior_observation: object | None,
    ):
        self.calls.append(market_ref)
        self.prior_observations.append(prior_observation)
        value = self.values[market_ref].pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


@pytest.mark.asyncio
async def test_collector_passes_rehydrated_prior_source_observation(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    first = _authoritative(market_ref)
    await CapitalGuardShadowSettlementCollector(store=store, source=SequenceSource({market_ref: [first]})).run_once()
    source = SequenceSource({market_ref: [first]})

    result = await CapitalGuardShadowSettlementCollector(store=store, source=source).run_once()

    assert result.identical_observations == 1
    assert source.prior_observations[0] is not None
    assert source.prior_observations[0].observation_sha256 == first.observation_sha256


@pytest.mark.asyncio
async def test_source_correction_links_prior_source_hash_but_store_links_prior_record(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    _append_candidate(store, candidate())
    market_key = store.settlement_market_backlog(limit=1)[0]
    market_ref = store.candidate_settlement_backlog(market_key, limit=1).market_ref
    prior = _authoritative(market_ref)
    await CapitalGuardShadowSettlementCollector(store=store, source=SequenceSource({market_ref: [prior]})).run_once()
    prior_head = store.current_authoritative_head(market_ref)
    assert prior_head is not None
    correction = build_settlement_observation(
        market_ref=market_ref,
        outcome=MarketOutcome.NO,
        authoritative_outcome="no",
        authoritative_payload={"market_id": market_ref.venue_market_id, "result": "no"},
        observed_at=prior.observed_at + timedelta(seconds=1),
        effective_at=prior.effective_at + timedelta(seconds=1),
        rules_version=prior.rules_version,
        source_id=prior.source_id,
        previous_observation=prior,
        supersedes_observation_sha256=prior.observation_sha256,
    )
    source = SequenceSource({market_ref: [correction]})

    result = await CapitalGuardShadowSettlementCollector(store=store, source=source).run_once()

    assert result.inserted_observations == 1
    assert source.prior_observations[0] is not None
    assert source.prior_observations[0].observation_sha256 == prior.observation_sha256
    with sqlite3.connect(store.db_path) as conn:
        supersedes = conn.execute(
            "SELECT supersedes_observation_sha256 "
            "FROM capital_guard_shadow_observations "
            "WHERE authoritative_observation_sha256 = ?",
            (correction.observation_sha256,),
        ).fetchone()[0]
    assert supersedes == prior_head.observation_sha256
    assert supersedes != prior.observation_sha256


@pytest.mark.asyncio
async def test_authoritative_source_correction_keeps_source_and_store_lineage_distinct(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)

    def market(result: str) -> KalshiMarket:
        return KalshiMarket(
            ticker=record.venue_market_id,
            title="authoritative correction test",
            yes_bid=0.0,
            yes_ask=0.0,
            yes_price=0.0,
            volume=0,
            open_interest=0,
            close_time="2026-07-23T00:00:00+00:00",
            status="settled",
            result=result,
            expiration_time="2026-07-23T00:00:00+00:00",
            raw_payload_hash="a" * 64,
        )

    class KalshiClient:
        def __init__(self) -> None:
            self.values = [market("yes"), market("no")]

        async def get_market_exact_bounded(self, ticker: str, *, timeout_seconds: float) -> KalshiMarket:
            assert ticker == record.venue_market_id
            assert timeout_seconds > 0
            return self.values.pop(0)

    class RecordingSource:
        def __init__(self, adapter: AuthoritativeSettlementSource) -> None:
            self.adapter = adapter
            self.observations: list[SettlementObservation] = []

        async def get_settlement_exact(
            self,
            requested_ref: MarketRef,
            *,
            prior_observation: SettlementObservation | None,
        ) -> SettlementObservation | None:
            observation = await self.adapter.get_settlement_exact(requested_ref, prior_observation=prior_observation)
            if observation is not None:
                self.observations.append(observation)
            return observation

    times = iter(
        (
            NOW + timedelta(days=1),
            NOW + timedelta(days=1, minutes=1),
            NOW + timedelta(days=1, minutes=2),
        )
    )
    source = RecordingSource(
        AuthoritativeSettlementSource(
            kalshi_client=KalshiClient(),
            polymarket_client=object(),
            clock=lambda: next(times),
        )
    )
    collector = CapitalGuardShadowSettlementCollector(store=store, source=source)

    first_result = await collector.run_once()
    second_result = await collector.run_once()

    assert first_result.inserted_observations == second_result.inserted_observations == 1
    assert len(source.observations) == 2
    assert source.observations[1].supersedes_observation_sha256 == source.observations[0].observation_sha256
    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT observation_sha256, authoritative_observation_sha256, "
            "supersedes_observation_sha256 "
            "FROM capital_guard_shadow_observations ORDER BY observed_at"
        ).fetchall()
    assert rows[1][2] == rows[0][0]
    assert rows[1][2] != source.observations[0].observation_sha256
    assert rows[1][1] == source.observations[1].observation_sha256


@pytest.mark.asyncio
async def test_source_correction_without_prior_source_hash_quarantines(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    prior = _authoritative(market_ref)
    await CapitalGuardShadowSettlementCollector(store=store, source=SequenceSource({market_ref: [prior]})).run_once()
    unlinked = _authoritative(
        market_ref,
        outcome=MarketOutcome.NO,
        observed_at=prior.observed_at + timedelta(seconds=1),
        effective_at=prior.effective_at + timedelta(seconds=1),
    )

    result = await CapitalGuardShadowSettlementCollector(
        store=store, source=SequenceSource({market_ref: [unlinked]})
    ).run_once()

    assert result.quarantined == 1
    assert _counts(store)["capital_guard_shadow_observations"] == 1


@pytest.mark.parametrize(
    "corrupted_field",
    (
        "authoritative_observation_sha256",
        "semantic_sha256",
        "observation_sha256",
    ),
)
@pytest.mark.asyncio
async def test_corrupt_authoritative_head_quarantines_before_source_io(
    corrupted_field: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    first = _authoritative(market_ref)
    await CapitalGuardShadowSettlementCollector(store=store, source=SequenceSource({market_ref: [first]})).run_once()
    original = store._current_authoritative_head_transaction

    def corrupt_head(*args: object, **kwargs: object):
        head = original(*args, **kwargs)
        return None if head is None else replace(head, **{corrupted_field: "0" * 64})

    monkeypatch.setattr(store, "_current_authoritative_head_transaction", corrupt_head)
    source = SequenceSource({market_ref: [first]})

    result = await CapitalGuardShadowSettlementCollector(store=store, source=source).run_once()

    assert result.quarantined == 1
    assert source.calls == []
    backlog = store.candidate_settlement_backlog(SettlementMarketKey(Venue.KALSHI, record.venue_market_id))
    direct = store.record_settlement_attempt(
        backlog,
        attempted_at=NOW + timedelta(days=2),
        status="terminal",
        observation=first,
    )
    assert direct.attempt_status == "quarantined"
    assert _counts(store)["capital_guard_shadow_observations"] == 1
    with sqlite3.connect(store.db_path) as conn:
        reason = conn.execute("SELECT reason_taxonomy FROM capital_guard_shadow_settlement_quarantines").fetchone()[0]
    assert reason == "source_drift"


@pytest.mark.parametrize("error_type", (ValueError, SettlementDriftError))
@pytest.mark.asyncio
async def test_invalid_authoritative_head_lookup_quarantines_before_source_io(
    error_type: type[Exception], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    first = _authoritative(market_ref)
    await CapitalGuardShadowSettlementCollector(store=store, source=SequenceSource({market_ref: [first]})).run_once()

    def invalid_head(*args: object, **kwargs: object):
        raise error_type("invalid persisted authoritative head")

    monkeypatch.setattr(store, "_current_authoritative_head_transaction", invalid_head)
    source = SequenceSource({market_ref: [first]})

    result = await CapitalGuardShadowSettlementCollector(store=store, source=source).run_once()

    assert result.quarantined == 1
    assert source.calls == []


@pytest.mark.asyncio
async def test_void_authoritative_head_without_refund_quarantines_before_source_io(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    first = _authoritative(market_ref)
    await CapitalGuardShadowSettlementCollector(store=store, source=SequenceSource({market_ref: [first]})).run_once()
    original = store._current_authoritative_head_transaction

    def invalid_void_head(*args: object, **kwargs: object):
        head = original(*args, **kwargs)
        return None if head is None else replace(head, outcome="void")

    monkeypatch.setattr(store, "_current_authoritative_head_transaction", invalid_void_head)
    source = SequenceSource({market_ref: [first]})

    result = await CapitalGuardShadowSettlementCollector(store=store, source=source).run_once()

    assert result.quarantined == 1
    assert source.calls == []


def _initialized_store(tmp_path: Path) -> CapitalGuardShadowStore:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    return store


def _polymarket_candidate():
    record = candidate()
    venue_market_id = "123456789"
    alias = "will-denver-hit-100f"
    schedule_json = serialize_fee_schedule(POLYMARKET_US_2026_07_01)
    coefficient = fee_coefficient_for(POLYMARKET_US_2026_07_01, FeeRole.TAKER)
    provenance_json = canonical_json(
        {
            "account_precision_dollars": None,
            "accumulator_dollars": "0",
            "coefficient": str(coefficient),
            "effective_at": POLYMARKET_US_2026_07_01.effective_from.isoformat(),
            "fee_multiplier": "1",
            "fee_role": "taker",
            "fee_schedule": json.loads(schedule_json),
            "fee_type": fee_type_for_schedule(POLYMARKET_US_2026_07_01),
            "schema_version": 1,
            "source_payload_sha256": "d" * 64,
            "venue": "polymarket_us",
        }
    )
    identity_json = canonical_json(
        {
            "alias": alias,
            "contract_fingerprint": "pm-contract-v1",
            "decision_key": record.decision_key,
            "lifecycle_id": record.lifecycle_id,
            "rules_fingerprint": "pm-rules-v1",
            "schema_version": 1,
            "settlement_fingerprint": "pm-settlement-v1",
            "venue": "polymarket_us",
            "venue_market_id": venue_market_id,
        }
    )
    return replace(
        record,
        venue=Venue.POLYMARKET_US,
        venue_market_id=venue_market_id,
        identity_json=identity_json,
        book_source="polymarket-us-orderbook-v1",
        fee_schedule_json=schedule_json,
        fee_provenance_json=provenance_json,
        fee_provenance_sha256=hashlib.sha256(provenance_json.encode("utf-8")).hexdigest(),
        fee_formula_type=fee_type_for_schedule(POLYMARKET_US_2026_07_01),
        fee_coefficient=coefficient,
        fee_account_precision=None,
    )


def _counts(store: CapitalGuardShadowStore) -> dict[str, int]:
    tables = (
        "capital_guard_shadow_settlement_attempts",
        "capital_guard_shadow_settlement_quarantines",
        "capital_guard_shadow_observations",
        "capital_guard_shadow_candidate_observations",
        "capital_guard_shadow_settlements",
        "capital_guard_shadow_evaluations",
    )
    with sqlite3.connect(store.db_path) as conn:
        return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}


def test_settlement_backlog_is_bounded_grouped_and_exact(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    first = candidate()
    second = candidate(
        decision_key="decision-2",
        lifecycle_id="lifecycle-2",
        side="no",
    )
    other = candidate(
        decision_key="decision-3",
        lifecycle_id="lifecycle-3",
        venue_market_id="KXZZZ-26JUL15-T50",
    )
    for record in (first, second, other):
        _append_candidate(store, record)

    keys = store.settlement_market_backlog(limit=1)
    backlog = store.candidate_settlement_backlog(keys[0], limit=10)

    assert keys == (SettlementMarketKey(Venue.KALSHI, first.venue_market_id),)
    assert backlog.market_ref == MarketRef(Venue.KALSHI, first.venue_market_id, first.venue_market_id)
    assert len(backlog.candidate_ids) == 2
    assert backlog.missing_link_candidate_ids == backlog.candidate_ids
    assert len(backlog.candidate_set_sha256) == 64
    assert backlog.contract_fingerprint == "contract-v1"
    assert backlog.rules_fingerprint == "rules-v1"
    assert backlog.settlement_fingerprint == "settlement-v1"
    assert backlog.current_head_sha256 is None

    with pytest.raises(ValueError, match="positive|limit"):
        store.settlement_market_backlog(limit=0)
    with pytest.raises(ValueError, match="bounded"):
        store.candidate_settlement_backlog(keys[0], limit=1)


def test_candidate_backlog_rejects_ambiguous_same_market_identity(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    first = candidate()
    second = candidate(
        decision_key="decision-2",
        lifecycle_id="lifecycle-2",
        side="no",
    )
    second_identity = {
        **__import__("json").loads(second.identity_json),
        "settlement_fingerprint": "settlement-v2",
    }
    second = replace(second, identity_json=_json(second_identity))
    _append_candidate(store, first)
    _append_candidate(store, second)

    with pytest.raises(CapitalGuardShadowIdentityError, match="ambiguous"):
        store.candidate_settlement_backlog(SettlementMarketKey(Venue.KALSHI, first.venue_market_id), limit=10)


@pytest.mark.asyncio
async def test_terminal_poll_appends_one_market_observation_links_all_and_is_semantic_noop(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    first = candidate()
    second = candidate(
        decision_key="decision-2",
        lifecycle_id="lifecycle-2",
        side="no",
    )
    first_id = _append_candidate(store, first).candidate_id
    second_id = _append_candidate(store, second).candidate_id
    market_ref = MarketRef(Venue.KALSHI, first.venue_market_id, first.venue_market_id)
    payload = {"market_id": first.venue_market_id, "result": "yes"}
    source = SequenceSource(
        {
            market_ref: [
                _authoritative(market_ref, payload=payload),
                _authoritative(
                    market_ref,
                    observed_at=NOW + timedelta(days=1, minutes=30),
                    effective_at=NOW + timedelta(days=1, minutes=30),
                    payload=payload,
                ),
            ]
        }
    )
    collector = CapitalGuardShadowSettlementCollector(
        store=store,
        source=source,
        clock=lambda: NOW + timedelta(days=1, hours=1),
    )

    first_run = await collector.run_once(limit=10)
    third = candidate(
        decision_key="decision-3",
        lifecycle_id="lifecycle-3",
    )
    third_id = _append_candidate(store, third).candidate_id
    second_run = await collector.run_once(limit=10)

    assert first_run.terminal == second_run.terminal == 1
    assert first_run.inserted_observations == 1
    assert second_run.identical_observations == 1
    counts = _counts(store)
    assert counts["capital_guard_shadow_settlement_attempts"] == 2
    assert counts["capital_guard_shadow_observations"] == 1
    assert counts["capital_guard_shadow_candidate_observations"] == 3
    assert counts["capital_guard_shadow_settlements"] == 0
    assert counts["capital_guard_shadow_evaluations"] == 0
    head = store.current_authoritative_head(market_ref)
    assert head is not None
    assert head.outcome == "yes"
    assert {row.candidate_id for row in store.current_head_settlements(market_ref)} == set()
    with sqlite3.connect(store.db_path) as conn:
        linked = {
            str(row[0]) for row in conn.execute("SELECT candidate_id FROM capital_guard_shadow_candidate_observations")
        }
    assert linked == {first_id, second_id, third_id}


@pytest.mark.asyncio
async def test_changed_authoritative_evidence_appends_one_successor(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    first = _authoritative(market_ref)
    correction = _authoritative(
        market_ref,
        outcome=MarketOutcome.NO,
        observed_at=NOW + timedelta(days=1, hours=1),
        effective_at=NOW + timedelta(days=1, hours=1),
        previous_observation=first,
        supersedes_observation_sha256=first.observation_sha256,
    )
    source = SequenceSource(
        {
            market_ref: [
                first,
                correction,
            ]
        }
    )
    collector = CapitalGuardShadowSettlementCollector(store=store, source=source)

    await collector.run_once(limit=10)
    # All original candidates are linked, but the market remains poll-eligible so
    # authoritative corrections cannot be hidden by a completed link backlog.
    result = await collector.run_once(limit=10)

    assert result.inserted_observations == 1
    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT observation_sha256, outcome, supersedes_observation_sha256 "
            "FROM capital_guard_shadow_observations ORDER BY observed_at"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][1:] == ("yes", None)
    assert rows[1][1:] == ("no", rows[0][0])
    assert store.current_authoritative_head(market_ref).observation_sha256 == rows[1][0]


@pytest.mark.asyncio
async def test_nonterminal_not_found_transport_internal_and_quarantine_are_isolated(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    records = [
        candidate(
            decision_key=f"decision-{index}",
            lifecycle_id=f"lifecycle-{index}",
            venue_market_id=f"KXTEST-{index}",
        )
        for index in range(1, 7)
    ]
    for record in records:
        _append_candidate(store, record)
    refs = [MarketRef(Venue.KALSHI, r.venue_market_id, r.venue_market_id) for r in records]
    source = SequenceSource(
        {
            refs[0]: [None],
            refs[1]: [SettlementNotFound("not found secret=hidden")],
            refs[2]: [TimeoutError("token=must-not-persist")],
            refs[3]: [SettlementDriftError("payload secret=must-not-persist")],
            refs[4]: [RuntimeError("internal secret=must-not-persist")],
            refs[5]: [_authoritative(refs[5])],
        }
    )
    collector = CapitalGuardShadowSettlementCollector(store=store, source=source)

    result = await collector.run_once(limit=10)

    assert (
        result.nonterminal,
        result.not_found,
        result.transient_errors,
        result.internal_errors,
        result.quarantined,
        result.terminal,
    ) == (1, 1, 1, 1, 1, 1)
    with sqlite3.connect(store.db_path) as conn:
        statuses = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                "SELECT status, COUNT(*) FROM capital_guard_shadow_settlement_attempts GROUP BY status"
            )
        }
        serialized = "\n".join(
            str(value)
            for row in conn.execute("SELECT * FROM capital_guard_shadow_settlement_attempts")
            for value in row
        )
    assert statuses == {
        "not_found": 1,
        "internal_error": 1,
        "nonterminal": 1,
        "quarantined": 1,
        "terminal": 1,
        "transient_error": 1,
    }
    assert "secret" not in serialized and "token" not in serialized


@pytest.mark.asyncio
async def test_void_is_quarantined_without_observation_or_financial_rows(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    source = SequenceSource({market_ref: [UnsupportedVoidError("refund economics unknown")]})
    collector = CapitalGuardShadowSettlementCollector(store=store, source=source)

    result = await collector.run_once(limit=10)

    assert result.quarantined == 1
    counts = _counts(store)
    assert counts["capital_guard_shadow_settlement_quarantines"] == 1
    assert counts["capital_guard_shadow_observations"] == 0
    assert counts["capital_guard_shadow_settlements"] == 0
    assert counts["capital_guard_shadow_evaluations"] == 0
    with sqlite3.connect(store.db_path) as conn:
        reason = conn.execute("SELECT reason_taxonomy FROM capital_guard_shadow_settlement_quarantines").fetchone()[0]
    assert reason == "missing_void_refund_contract"


@pytest.mark.asyncio
async def test_post_fetch_candidate_set_race_quarantines_without_observation(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)

    class RacingSource:
        async def get_settlement_exact(self, requested_ref: MarketRef, *, prior_observation: object | None):
            added = candidate(
                decision_key="racing-decision",
                lifecycle_id="racing-lifecycle",
                side="no",
            )
            _append_candidate(store, added)
            return _authoritative(requested_ref)

    collector = CapitalGuardShadowSettlementCollector(store=store, source=RacingSource())

    result = await collector.run_once(limit=10)

    assert result.quarantined == 1
    counts = _counts(store)
    assert counts["capital_guard_shadow_observations"] == 0
    with sqlite3.connect(store.db_path) as conn:
        reason = conn.execute("SELECT reason_taxonomy FROM capital_guard_shadow_settlement_quarantines").fetchone()[0]
    assert reason == "concurrent_state_change"


@pytest.mark.asyncio
async def test_strict_async_source_cancellation_never_appends_after_shutdown(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingAsyncSource:
        async def get_settlement_exact(self, requested_ref: MarketRef, *, prior_observation: object | None):
            assert requested_ref == market_ref
            started.set()
            await release.wait()
            return _authoritative(requested_ref)

    collector = CapitalGuardShadowSettlementCollector(store=store, source=BlockingAsyncSource())
    task = asyncio.create_task(collector.run_once(limit=10))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await asyncio.sleep(0.05)

    assert _counts(store)["capital_guard_shadow_settlement_attempts"] == 0
    assert _counts(store)["capital_guard_shadow_observations"] == 0


@pytest.mark.asyncio
async def test_blocking_store_call_does_not_starve_unrelated_coroutine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _initialized_store(tmp_path)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original = store.settlement_market_backlog

    def blocking_backlog(*, limit: int):
        started.set()
        if not release.wait(timeout=1):
            raise AssertionError("test did not release blocking store call")
        finished.set()
        return original(limit=limit)

    monkeypatch.setattr(store, "settlement_market_backlog", blocking_backlog)
    collector = CapitalGuardShadowSettlementCollector(store=store, source=SequenceSource({}))
    run = asyncio.create_task(collector.run_once(limit=10))
    fallback_release = threading.Timer(1, release.set)
    fallback_release.start()
    peer_ran = asyncio.Event()

    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1), timeout=2)

        async def peer() -> None:
            peer_ran.set()

        await asyncio.wait_for(asyncio.create_task(peer()), timeout=1)
        assert peer_ran.is_set()
        assert not finished.is_set()

        release.set()
        result = await asyncio.wait_for(run, timeout=2)
    finally:
        release.set()
        fallback_release.cancel()
        if not run.done():
            await run

    assert result.checked == 0


@pytest.mark.asyncio
async def test_cancellation_waits_for_inflight_store_write_before_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original = store.record_settlement_attempt

    def blocking_record_settlement_attempt(*args: object, **kwargs: object):
        started.set()
        if not release.wait(timeout=1):
            raise AssertionError("test did not release blocking store call")
        result = original(*args, **kwargs)
        finished.set()
        return result

    monkeypatch.setattr(store, "record_settlement_attempt", blocking_record_settlement_attempt)
    collector = CapitalGuardShadowSettlementCollector(
        store=store,
        source=SequenceSource({market_ref: [_authoritative(market_ref)]}),
    )
    run = asyncio.create_task(collector.run_once(limit=10))
    fallback_release = threading.Timer(1, release.set)
    fallback_release.start()

    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1), timeout=2)
        run.cancel()
        await asyncio.sleep(0)
        assert not run.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, timeout=2)
    finally:
        release.set()
        fallback_release.cancel()
        if not run.done():
            await run

    assert finished.is_set()
    counts = _counts(store)
    assert counts["capital_guard_shadow_settlement_attempts"] == 1
    assert counts["capital_guard_shadow_observations"] == 1


def test_collector_rejects_lossy_sync_router_contract(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)

    class LossySyncRouter:
        def get_settlement(self, market_ref: MarketRef):
            return None

    with pytest.raises(TypeError, match="strict async|exact"):
        CapitalGuardShadowSettlementCollector(store=store, source=LossySyncRouter())


@pytest.mark.asyncio
async def test_ambiguous_identity_quarantines_honestly_without_network(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    first = candidate()
    second = candidate(
        decision_key="decision-2",
        lifecycle_id="lifecycle-2",
        side="no",
    )
    second = replace(
        second,
        identity_json=canonical_json(
            {
                **json.loads(second.identity_json),
                "settlement_fingerprint": "settlement-v2",
            }
        ),
    )
    _append_candidate(store, first)
    _append_candidate(store, second)

    class TrapSource:
        calls = 0

        async def get_settlement_exact(self, market_ref: MarketRef, *, prior_observation: object | None):
            self.calls += 1
            raise AssertionError(market_ref)

    source = TrapSource()
    result = await CapitalGuardShadowSettlementCollector(store=store, source=source).run_once(limit=10)

    assert result.quarantined == 1 and source.calls == 0
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT alias, contract_fingerprint, candidate_set_complete, "
            "candidate_set_sha256, identity_set_sha256, identity_sample_sha256, "
            "candidate_count, error_taxonomy "
            "FROM capital_guard_shadow_settlement_attempts"
        ).fetchone()
    assert row[0:2] == (None, None)
    assert row[2] == 1
    assert len(row[3]) == len(row[4]) == 64 and row[5] is None
    assert row[6:] == (2, "identity_ambiguous")


@pytest.mark.asyncio
async def test_ambiguous_identity_with_invalid_head_quarantines_without_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _initialized_store(tmp_path)
    first = candidate()
    second = candidate(
        decision_key="decision-2",
        lifecycle_id="lifecycle-2",
        side="no",
    )
    second = replace(
        second,
        identity_json=canonical_json(
            {
                **json.loads(second.identity_json),
                "settlement_fingerprint": "settlement-v2",
            }
        ),
    )
    _append_candidate(store, first)
    _append_candidate(store, second)

    def invalid_head(*args: object, **kwargs: object):
        raise ValueError("invalid persisted authoritative head")

    monkeypatch.setattr(store, "_current_authoritative_head_transaction", invalid_head)

    class TrapSource:
        calls = 0

        async def get_settlement_exact(self, market_ref: MarketRef, *, prior_observation: object | None):
            self.calls += 1
            raise AssertionError(market_ref)

    source = TrapSource()
    result = await CapitalGuardShadowSettlementCollector(store=store, source=source).run_once(limit=10)

    assert result.quarantined == 1 and source.calls == 0
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT head_before_sha256, head_after_sha256, error_taxonomy FROM capital_guard_shadow_settlement_attempts"
        ).fetchone()
    assert row == (None, None, "identity_ambiguous")


@pytest.mark.asyncio
async def test_over_cap_group_records_exact_count_and_sample_not_fake_full_hashes(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    _append_candidate(store, candidate())
    _append_candidate(
        store,
        candidate(
            decision_key="decision-2",
            lifecycle_id="lifecycle-2",
            side="no",
        ),
    )

    class TrapSource:
        calls = 0

        async def get_settlement_exact(self, market_ref: MarketRef, *, prior_observation: object | None):
            self.calls += 1
            raise AssertionError(market_ref)

    source = TrapSource()
    result = await CapitalGuardShadowSettlementCollector(
        store=store,
        source=source,
        max_candidates_per_market=1,
    ).run_once(limit=10)

    assert result.quarantined == 1 and source.calls == 0
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT candidate_set_complete, candidate_set_sha256, "
            "identity_set_sha256, identity_sample_sha256, candidate_count, "
            "error_taxonomy FROM capital_guard_shadow_settlement_attempts"
        ).fetchone()
    assert row[0:3] == (0, None, None)
    assert len(row[3]) == 64
    assert row[4:] == (2, "candidate_group_over_cap")

    key = SettlementMarketKey(Venue.KALSHI, candidate().venue_market_id)
    with pytest.raises(ValueError, match="hard bounded"):
        store.candidate_settlement_backlog(key, limit=MAX_SETTLEMENT_CANDIDATES_PER_MARKET + 1)
    with pytest.raises(ValueError, match="hard bounded"):
        store.settlement_market_backlog(limit=MAX_SETTLEMENT_MARKETS_PER_RUN + 1)


def test_polymarket_numeric_id_and_slug_alias_remain_separate(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    record = _polymarket_candidate()
    _append_candidate(store, record)

    backlog = store.candidate_settlement_backlog(SettlementMarketKey(Venue.POLYMARKET_US, record.venue_market_id))

    assert backlog.market_ref == MarketRef(
        Venue.POLYMARKET_US,
        "123456789",
        "will-denver-hit-100f",
    )
    assert backlog.contract_fingerprint == "pm-contract-v1"


def test_attempt_exact_retry_and_changed_payload_collision_commit_evidence_only(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    backlog = store.candidate_settlement_backlog(SettlementMarketKey(Venue.KALSHI, record.venue_market_id))
    attempted_at = NOW + timedelta(days=2)

    first = store.record_settlement_attempt(
        backlog,
        attempted_at=attempted_at,
        status="nonterminal",
        error_taxonomy="authoritative_nonterminal",
        error_sha256="a" * 64,
    )
    retry = store.record_settlement_attempt(
        backlog,
        attempted_at=attempted_at,
        status="nonterminal",
        error_taxonomy="authoritative_nonterminal",
        error_sha256="a" * 64,
    )
    collision = store.record_settlement_attempt(
        backlog,
        attempted_at=attempted_at,
        status="internal_error",
        error_taxonomy="internal_source_error",
        error_sha256="b" * 64,
    )

    assert (first.status, retry.status, collision.status) == (
        "inserted",
        "identical",
        "conflict",
    )
    assert first.attempt_id == retry.attempt_id == collision.attempt_id
    counts = _counts(store)
    assert counts["capital_guard_shadow_settlement_attempts"] == 1
    assert counts["capital_guard_shadow_observations"] == 0
    assert counts["capital_guard_shadow_candidate_observations"] == 0
    with sqlite3.connect(store.db_path) as conn:
        conflict_count = conn.execute(
            "SELECT COUNT(*) FROM capital_guard_shadow_conflicts WHERE entity_type='settlement_attempt'"
        ).fetchone()[0]
    assert conflict_count == 1


def test_terminal_attempt_collision_preflight_prevents_head_and_link_mutation(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    backlog = store.candidate_settlement_backlog(SettlementMarketKey(Venue.KALSHI, record.venue_market_id))
    attempted_at = NOW + timedelta(days=2)
    store.record_settlement_attempt(
        backlog,
        attempted_at=attempted_at,
        status="nonterminal",
        error_taxonomy="authoritative_nonterminal",
        error_sha256="a" * 64,
    )

    collision = store.record_settlement_attempt(
        backlog,
        attempted_at=attempted_at,
        status="terminal",
        observation=_authoritative(market_ref),
    )

    assert collision.status == "conflict"
    assert store.current_authoritative_head(market_ref) is None
    counts = _counts(store)
    assert counts["capital_guard_shadow_observations"] == 0
    assert counts["capital_guard_shadow_candidate_observations"] == 0
    assert counts["capital_guard_shadow_settlement_attempts"] == 1


@pytest.mark.asyncio
async def test_semantic_retry_preserves_original_source_times_and_links_late_candidate(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    first_observed = NOW + timedelta(days=1)
    first_effective = first_observed - timedelta(minutes=10)
    payload = {"market_id": record.venue_market_id, "result": "yes"}
    source = SequenceSource(
        {
            market_ref: [
                _authoritative(
                    market_ref,
                    observed_at=first_observed,
                    effective_at=first_effective,
                    payload=payload,
                ),
                _authoritative(
                    market_ref,
                    observed_at=first_observed + timedelta(hours=1),
                    effective_at=first_effective + timedelta(hours=1),
                    payload=payload,
                ),
            ]
        }
    )
    times = iter([NOW + timedelta(days=2), NOW + timedelta(days=2, minutes=30)])
    collector = CapitalGuardShadowSettlementCollector(store=store, source=source, clock=lambda: next(times))
    await collector.run_once(limit=10)
    late = candidate(
        decision_key="late-decision",
        lifecycle_id="late-lifecycle",
        side="no",
    )
    late_id = _append_candidate(store, late).candidate_id
    await collector.run_once(limit=10)

    head = store.current_authoritative_head(market_ref)
    assert head.observed_at == first_observed
    assert head.effective_at == first_effective
    with sqlite3.connect(store.db_path) as conn:
        linked_at = conn.execute(
            "SELECT linked_at FROM capital_guard_shadow_candidate_observations "
            "WHERE candidate_id = ? AND observation_sha256 = ?",
            (late_id, head.observation_sha256),
        ).fetchone()[0]
    assert linked_at == "2026-07-17T05:00:00.000000Z"


@pytest.mark.asyncio
async def test_equal_or_backward_time_correction_is_quarantined(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    first = _authoritative(market_ref)
    backward = _authoritative(
        market_ref,
        outcome=MarketOutcome.NO,
        observed_at=first.observed_at,
        effective_at=first.effective_at,
        previous_observation=first,
        supersedes_observation_sha256=first.observation_sha256,
    )
    source = SequenceSource({market_ref: [first, backward]})
    times = iter([NOW + timedelta(days=2), NOW + timedelta(days=2, minutes=1)])
    collector = CapitalGuardShadowSettlementCollector(store=store, source=source, clock=lambda: next(times))

    await collector.run_once(limit=10)
    result = await collector.run_once(limit=10)

    assert result.quarantined == 1
    assert _counts(store)["capital_guard_shadow_observations"] == 1
    with sqlite3.connect(store.db_path) as conn:
        reason = conn.execute("SELECT reason_taxonomy FROM capital_guard_shadow_settlement_quarantines").fetchone()[0]
    assert reason == "backward_time"


@pytest.mark.asyncio
async def test_valid_void_refund_contract_is_bound_then_deferred_without_finance(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    observed_at = NOW + timedelta(days=1)
    void = build_settlement_observation(
        market_ref=market_ref,
        outcome=MarketOutcome.VOID,
        authoritative_outcome="void",
        authoritative_payload={"market_id": record.venue_market_id, "result": "void"},
        observed_at=observed_at,
        effective_at=observed_at,
        rules_version="kalshi-settlement-v1",
        source_id="kalshi-market-api",
        void_refund=VoidRefundContract(Decimal("50"), False),
    )
    source = SequenceSource({market_ref: [void]})

    result = await CapitalGuardShadowSettlementCollector(store=store, source=source).run_once(limit=10)

    assert result.quarantined == 1
    counts = _counts(store)
    assert counts["capital_guard_shadow_observations"] == 0
    assert counts["capital_guard_shadow_settlements"] == 0
    assert counts["capital_guard_shadow_evaluations"] == 0
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT outcome, void_refund_json, void_refund_sha256, error_taxonomy "
            "FROM capital_guard_shadow_settlement_attempts"
        ).fetchone()
    assert row[0] == "void"
    assert len(row[1]) > 0 and len(row[2]) == 64
    assert row[3] == "void_financial_economics_deferred"


@pytest.mark.asyncio
async def test_void_source_correction_without_prior_hash_quarantines_drift(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    first = _authoritative(market_ref)
    await CapitalGuardShadowSettlementCollector(store=store, source=SequenceSource({market_ref: [first]})).run_once()
    correction_time = first.observed_at + timedelta(hours=1)
    unlinked_void = build_settlement_observation(
        market_ref=market_ref,
        outcome=MarketOutcome.VOID,
        authoritative_outcome="void",
        authoritative_payload={"market_id": record.venue_market_id, "result": "void"},
        observed_at=correction_time,
        effective_at=correction_time,
        rules_version="kalshi-settlement-v1",
        source_id="kalshi-market-api",
        void_refund=VoidRefundContract(Decimal("50"), False),
    )

    result = await CapitalGuardShadowSettlementCollector(
        store=store, source=SequenceSource({market_ref: [unlinked_void]})
    ).run_once()

    assert result.quarantined == 1
    with sqlite3.connect(store.db_path) as conn:
        reason = conn.execute("SELECT reason_taxonomy FROM capital_guard_shadow_settlement_quarantines").fetchone()[0]
    assert reason == "source_drift"


@pytest.mark.parametrize("fatal", [SystemExit("stop"), KeyboardInterrupt()])
@pytest.mark.asyncio
async def test_fatal_source_control_flow_never_appends(
    tmp_path: Path,
    fatal: BaseException,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)

    class FatalSource:
        async def get_settlement_exact(self, market_ref: MarketRef, *, prior_observation: object | None):
            raise fatal

    collector = CapitalGuardShadowSettlementCollector(store=store, source=FatalSource())
    with pytest.raises(type(fatal)):
        await collector.run_once(limit=10)

    assert _counts(store)["capital_guard_shadow_settlement_attempts"] == 0


@pytest.mark.asyncio
async def test_forged_source_supersedes_and_alias_mismatch_quarantine(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    records = [
        candidate(),
        candidate(
            decision_key="decision-2",
            lifecycle_id="lifecycle-2",
            venue_market_id="KXOTHER",
        ),
    ]
    for record in records:
        _append_candidate(store, record)
    refs = [MarketRef(Venue.KALSHI, r.venue_market_id, r.venue_market_id) for r in records]
    forged = replace(_authoritative(refs[0]), supersedes_observation_sha256="f" * 64)
    wrong_alias = _authoritative(MarketRef(Venue.KALSHI, refs[1].venue_market_id, "WRONG-ALIAS"))
    source = SequenceSource({refs[0]: [forged], refs[1]: [wrong_alias]})

    result = await CapitalGuardShadowSettlementCollector(store=store, source=source).run_once(limit=10)

    assert result.quarantined == 2
    assert _counts(store)["capital_guard_shadow_observations"] == 0


def test_direct_append_rejects_cross_contract_identity_and_backward_effective_time(
    tmp_path: Path,
) -> None:
    import trading.capital_guard_shadow as shadow_module
    from tests.test_capital_guard_shadow import observation

    store = _initialized_store(tmp_path)
    result = _append_candidate(store, candidate())
    base = observation()
    authoritative = build_settlement_observation(
        market_ref=MarketRef(base.venue, base.venue_market_id, base.alias),
        outcome=MarketOutcome.YES,
        authoritative_outcome=json.loads(base.authoritative_outcome_json),
        authoritative_payload=json.loads(base.source_payload_json),
        observed_at=base.observed_at,
        effective_at=base.effective_at,
        rules_version=base.rules_version,
        source_id=base.source_id,
    )
    mismatched = replace(
        base,
        settlement_fingerprint="settlement-v2",
        semantic_sha256=shadow_module._source_settlement_semantic_sha256(
            authoritative,
            contract_fingerprint=base.contract_fingerprint,
            rules_fingerprint=base.rules_fingerprint,
            settlement_fingerprint="settlement-v2",
        ),
    )
    with pytest.raises(ValueError, match="contract identity"):
        store.append_observation(mismatched, (result.candidate_id,))
    assert _counts(store)["capital_guard_shadow_observations"] == 0

    first = store.append_observation(base, (result.candidate_id,))
    correction_base = observation(
        outcome="no",
        observed_at=base.observed_at + timedelta(minutes=5),
        supersedes=first.observation_sha256,
    )
    backward_effective = base.effective_at - timedelta(seconds=1)
    backward_authoritative = build_settlement_observation(
        market_ref=MarketRef(
            correction_base.venue,
            correction_base.venue_market_id,
            correction_base.alias,
        ),
        outcome=MarketOutcome.NO,
        authoritative_outcome=json.loads(correction_base.authoritative_outcome_json),
        authoritative_payload=json.loads(correction_base.source_payload_json),
        observed_at=correction_base.observed_at,
        effective_at=backward_effective,
        rules_version=correction_base.rules_version,
        source_id=correction_base.source_id,
    )
    backward = replace(
        correction_base,
        effective_at=backward_effective,
        authoritative_observation_sha256=(backward_authoritative.observation_sha256),
    )
    with pytest.raises(ValueError, match="effective_at"):
        store.append_observation(backward, (result.candidate_id,))


def test_schema_rejects_manufactured_terminal_attempt_and_v1_is_not_repaired(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    with sqlite3.connect(store.db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO capital_guard_shadow_settlement_attempts (
                attempt_id, attempt_version, payload_sha256, venue, venue_market_id,
                alias, contract_fingerprint, rules_fingerprint,
                settlement_fingerprint, identity_set_sha256,
                identity_sample_sha256, candidate_set_sha256,
                candidate_set_complete, candidate_count, attempted_at, status,
                outcome, source_id, rules_version, authoritative_outcome_json,
                authoritative_payload_sha256, authoritative_observation_sha256,
                semantic_sha256, void_refund_json, void_refund_sha256,
                head_before_sha256, head_after_sha256, error_taxonomy, error_sha256
            ) VALUES (?,1,?,?,?,?,?,?,?,?,NULL,?,1,1,?,'terminal','yes',?,?,?,
                      ?,?, ?,NULL,NULL,NULL,NULL,NULL,NULL)
            """,
            (
                "a" * 64,
                "b" * 64,
                "kalshi",
                "KXTEST",
                "KXTEST",
                "contract",
                "rules",
                "settlement",
                "c" * 64,
                "d" * 64,
                "2026-07-17T12:00:00.000000Z",
                "source",
                "rules-v1",
                '"yes"',
                "e" * 64,
                "f" * 64,
                "1" * 64,
            ),
        )

    legacy = tmp_path / "legacy-v1.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute(
            "CREATE TABLE capital_guard_shadow_schema_meta ("
            "schema_version INTEGER PRIMARY KEY, ddl_sha256 TEXT, applied_at TEXT)"
        )
        conn.execute(
            "INSERT INTO capital_guard_shadow_schema_meta VALUES (1, ?, ?)",
            ("0" * 64, "2026-07-15T12:30:00.000000Z"),
        )
    with sqlite3.connect(legacy) as conn:
        before = (
            conn.execute("SELECT type, name, sql FROM sqlite_schema ORDER BY type, name").fetchall(),
            conn.execute("SELECT * FROM capital_guard_shadow_schema_meta").fetchall(),
        )
    with pytest.raises(RuntimeError, match="schema drift"):
        CapitalGuardShadowStore(legacy).initialize(applied_at=NOW)
    with sqlite3.connect(legacy) as conn:
        after = (
            conn.execute("SELECT type, name, sql FROM sqlite_schema ORDER BY type, name").fetchall(),
            conn.execute("SELECT * FROM capital_guard_shadow_schema_meta").fetchall(),
        )
    assert after == before


def test_attempt_and_quarantine_are_append_only_and_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    backlog = store.candidate_settlement_backlog(SettlementMarketKey(Venue.KALSHI, record.venue_market_id))
    inserted = store.record_settlement_attempt(
        backlog,
        attempted_at=NOW + timedelta(days=2),
        status="quarantined",
        error_taxonomy="source_drift",
        error_sha256="a" * 64,
        quarantine_reason="source_drift",
    )
    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE capital_guard_shadow_settlement_attempts SET status='nonterminal'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM capital_guard_shadow_settlement_quarantines")

    real = store._persist_settlement_attempt_transaction

    def fail_after_persist(*args: object, **kwargs: object):
        real(*args, **kwargs)
        raise RuntimeError("injected after persistence")

    monkeypatch.setattr(store, "_persist_settlement_attempt_transaction", fail_after_persist)
    with pytest.raises(RuntimeError, match="injected"):
        store.record_settlement_attempt(
            backlog,
            attempted_at=NOW + timedelta(days=2, minutes=1),
            status="not_found",
            error_taxonomy="authoritative_not_found",
            error_sha256="b" * 64,
        )
    counts = _counts(store)
    assert counts["capital_guard_shadow_settlement_attempts"] == 1
    assert counts["capital_guard_shadow_settlement_quarantines"] == 1
    assert inserted.status == "inserted"


@pytest.mark.asyncio
async def test_current_head_change_during_fetch_quarantines_without_reparenting(
    tmp_path: Path,
) -> None:
    from tests.test_capital_guard_shadow import observation

    store = _initialized_store(tmp_path)
    record = candidate()
    candidate_id = _append_candidate(store, record).candidate_id
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)

    class HeadRacingSource:
        async def get_settlement_exact(self, requested_ref: MarketRef, *, prior_observation: object | None):
            store.append_observation(observation(), (candidate_id,))
            return _authoritative(
                requested_ref,
                outcome=MarketOutcome.NO,
                observed_at=NOW + timedelta(days=1, hours=1),
                effective_at=NOW + timedelta(days=1, hours=1),
            )

    result = await CapitalGuardShadowSettlementCollector(store=store, source=HeadRacingSource()).run_once(limit=10)

    assert result.quarantined == 1
    assert _counts(store)["capital_guard_shadow_observations"] == 1
    head = store.current_authoritative_head(market_ref)
    assert head.outcome == "yes"
    with sqlite3.connect(store.db_path) as conn:
        reason = conn.execute("SELECT reason_taxonomy FROM capital_guard_shadow_settlement_quarantines").fetchone()[0]
    assert reason == "concurrent_state_change"


@pytest.mark.asyncio
async def test_concurrent_different_corrections_produce_one_successor_no_fork(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    root_observation = _authoritative(market_ref)
    initial_source = SequenceSource({market_ref: [root_observation]})
    await CapitalGuardShadowSettlementCollector(store=store, source=initial_source).run_once(limit=10)
    snapshot = store.candidate_settlement_backlog(SettlementMarketKey(Venue.KALSHI, record.venue_market_id))
    assert snapshot.prior_authoritative_observation == root_observation
    correction_time = NOW + timedelta(days=1, hours=1)
    first = _authoritative(
        market_ref,
        outcome=MarketOutcome.NO,
        observed_at=correction_time,
        effective_at=correction_time,
        previous_observation=snapshot.prior_authoritative_observation,
        supersedes_observation_sha256=root_observation.observation_sha256,
    )
    second = _authoritative(
        market_ref,
        outcome=MarketOutcome.YES,
        observed_at=correction_time + timedelta(seconds=1),
        effective_at=correction_time + timedelta(seconds=1),
        payload={
            "market_id": record.venue_market_id,
            "result": "yes",
            "revision": 2,
        },
        previous_observation=snapshot.prior_authoritative_observation,
        supersedes_observation_sha256=root_observation.observation_sha256,
    )

    results = await asyncio.gather(
        asyncio.to_thread(
            store.record_settlement_attempt,
            snapshot,
            attempted_at=NOW + timedelta(days=2, minutes=1),
            status="terminal",
            observation=first,
        ),
        asyncio.to_thread(
            store.record_settlement_attempt,
            snapshot,
            attempted_at=NOW + timedelta(days=2, minutes=2),
            status="terminal",
            observation=second,
        ),
    )

    assert sorted(result.attempt_status for result in results) == [
        "quarantined",
        "terminal",
    ]
    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT observation_sha256, supersedes_observation_sha256 FROM capital_guard_shadow_observations"
        ).fetchall()
        correction_conflicts = conn.execute(
            "SELECT COUNT(*) FROM capital_guard_shadow_conflicts WHERE entity_type='observation_correction'"
        ).fetchone()[0]
    assert len(rows) == 2
    root = next(row[0] for row in rows if row[1] is None)
    assert [row[1] for row in rows if row[1] is not None] == [root]
    assert correction_conflicts == 0


def test_lock_timeout_rolls_back_without_attempt_or_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading.capital_guard_shadow as shadow_module

    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    backlog = store.candidate_settlement_backlog(SettlementMarketKey(Venue.KALSHI, record.venue_market_id))
    monkeypatch.setattr(shadow_module, "_BUSY_TIMEOUT_MS", 10)
    blocker = sqlite3.connect(store.db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.record_settlement_attempt(
                backlog,
                attempted_at=NOW + timedelta(days=2),
                status="not_found",
                error_taxonomy="authoritative_not_found",
                error_sha256="a" * 64,
            )
    finally:
        blocker.rollback()
        blocker.close()
    assert _counts(store)["capital_guard_shadow_settlement_attempts"] == 0
    assert _counts(store)["capital_guard_shadow_observations"] == 0


@pytest.mark.asyncio
async def test_restart_semantic_idempotency_keeps_one_head(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path)
    record = candidate()
    _append_candidate(store, record)
    market_ref = MarketRef(Venue.KALSHI, record.venue_market_id, record.venue_market_id)
    payload = {"market_id": record.venue_market_id, "result": "yes"}
    await CapitalGuardShadowSettlementCollector(
        store=store,
        source=SequenceSource({market_ref: [_authoritative(market_ref, payload=payload)]}),
        clock=lambda: NOW + timedelta(days=2),
    ).run_once(limit=10)
    restarted = CapitalGuardShadowStore(store.db_path, existing_only=True)
    restarted.initialize()
    await CapitalGuardShadowSettlementCollector(
        store=restarted,
        source=SequenceSource(
            {
                market_ref: [
                    _authoritative(
                        market_ref,
                        payload=payload,
                        observed_at=NOW + timedelta(days=2),
                        effective_at=NOW + timedelta(days=2),
                    )
                ]
            }
        ),
        clock=lambda: NOW + timedelta(days=3),
    ).run_once(limit=10)

    assert _counts(store)["capital_guard_shadow_observations"] == 1
    assert _counts(store)["capital_guard_shadow_settlement_attempts"] == 2
