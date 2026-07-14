"""Behavior contract for durable settlement outbox consumers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock

import pytest

from tasks.calibration_task import CalibrationTask
from tests.test_paper_canonical_settlement import (
    _observation,
    _record_analysis,
    _resolve,
)
from tests.test_paper_trader import _cfg_module, _make_mock_analysis
from trading.settlement import MarketOutcome
from trading.settlement_store import SettlementStore
from trading.venue import MarketRef, Venue


WORKER_NOW = datetime(2026, 7, 14, 22, 0, tzinfo=timezone.utc)
DIRECTIONAL_CONSUMERS = (
    "paper_trade_log",
    "source_credibility",
    "calibration_state",
    "keyword_outcomes",
)


@dataclass(frozen=True)
class SeededEvent:
    db_path: Path
    outbox_id: str
    trade_id: str
    payload: dict[str, object]


@dataclass
class FixedClock:
    value: datetime = WORKER_NOW

    def __call__(self) -> datetime:
        return self.value


def _settlement_outbox_task_class():
    from tasks.settlement_outbox_task import SettlementOutboxTask

    return SettlementOutboxTask


def _task(seed: SeededEvent):
    tokens = (f"worker-token-{index}" for index in itertools.count(1))
    return _settlement_outbox_task_class()(
        db_path=seed.db_path,
        calibration_task=CalibrationTask(),
        trade_logger=MagicMock(),
        clock=FixedClock(),
        token_factory=lambda: next(tokens),
        lease_seconds=60,
    )


def _seed_directional_event(monkeypatch, tmp_path: Path) -> SeededEvent:
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(_cfg_module.cfg, "max_ticker_exposure_pct", 0.25)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)

    import trading.paper_trader as paper_trader_module

    monkeypatch.setattr(paper_trader_module, "trade_log", MagicMock())
    db_path = tmp_path / "paper.db"
    trader = paper_trader_module.PaperTrader(
        db_path=db_path,
        startup_context="test",
    )
    trader._set_state("notional_bankroll", "500.0")
    market_ref = MarketRef(
        Venue.POLYMARKET_US,
        "8594",
        "stored-worker-alias",
    )
    analysis = _make_mock_analysis(
        ticker=market_ref.alias,
        series_ticker="PMOUTBOX",
        side="yes",
        yes_price=40.0,
        estimated_prob=0.67,
        source="wire:test-source",
        keywords=["missile strike", "ceasefire"],
        llm_direction="yes",
        llm_magnitude="moderate",
        llm_confidence=0.81,
    )
    analysis.venue = market_ref.venue.value
    analysis.market.venue = market_ref.venue.value
    analysis.market.market_id = market_ref.alias
    analysis.market.venue_market_id = market_ref.venue_market_id
    analysis.signal_meta = {
        "fast_lane_p": 0.70,
        "fast_lane_confidence": 0.90,
        "accumulation_p": 0.65,
        "accumulation_confidence": 0.80,
        "structural_p": 0.60,
        "structural_confidence": 0.70,
    }
    trade_id = _record_analysis(trader, analysis, trade_id="worker000001")
    observation = _observation(
        MarketRef(
            market_ref.venue,
            market_ref.venue_market_id,
            "drifted-worker-alias",
        ),
        MarketOutcome.YES,
    )
    assert _resolve(trader, observation) is True
    row = trader._conn.execute(
        "SELECT outbox_id, payload_json FROM paper_settlement_outbox"
    ).fetchone()
    seed = SeededEvent(
        db_path=db_path,
        outbox_id=str(row["outbox_id"]),
        trade_id=trade_id,
        payload=json.loads(row["payload_json"]),
    )
    trader.credibility._conn.close()
    trader._conn.close()
    return seed


def _result_sha256(outbox_id: str, consumer_name: str) -> str:
    return hashlib.sha256(f"{outbox_id}:{consumer_name}".encode()).hexdigest()


def _record_receipts(
    db_path: Path,
    outbox_id: str,
    consumers: tuple[str, ...],
) -> None:
    with SettlementStore(db_path) as store:
        for index, consumer_name in enumerate(consumers, start=1):
            claim_token = f"fixture-token-{index}-{consumer_name}"
            assert store.acquire_claim(
                consumer_name,
                outbox_id,
                claim_token=claim_token,
                now=WORKER_NOW,
                lease_seconds=60,
            )
            assert store.record_receipt(
                consumer_name,
                outbox_id,
                claim_token=claim_token,
                processed_at=WORKER_NOW + timedelta(seconds=1),
                result_sha256=_result_sha256(outbox_id, consumer_name),
            )


def _rows(db_path: Path, sql: str, parameters: tuple[object, ...] = ()) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        return [tuple(row) for row in conn.execute(sql, parameters).fetchall()]
    finally:
        conn.close()


def _pending_consumers(seed: SeededEvent) -> tuple[str, ...]:
    with SettlementStore(seed.db_path) as store:
        return tuple(
            row.consumer_name
            for row in store.pending_requirements()
            if row.outbox_id == seed.outbox_id
        )


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _outbox_id(
    *,
    event_version: int,
    event_kind: str,
    observation_sha256: str,
    trade_id: str,
) -> str:
    encoded = _canonical_json(
        {
            "event_kind": event_kind,
            "event_version": event_version,
            "observation_sha256": observation_sha256,
            "trade_id": trade_id,
        }
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _insert_invalid_event(seed: SeededEvent, invalid_case: str) -> SeededEvent:
    if invalid_case == "unknown_consumer":
        conn = sqlite3.connect(seed.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                INSERT INTO paper_settlement_outbox_requirements (
                    outbox_id, consumer_name
                ) VALUES (?, 'unknown_consumer')
                """,
                (seed.outbox_id,),
            )
            conn.commit()
        finally:
            conn.close()
        _record_receipts(seed.db_path, seed.outbox_id, DIRECTIONAL_CONSUMERS)
        return seed

    _record_receipts(seed.db_path, seed.outbox_id, DIRECTIONAL_CONSUMERS)
    payload = dict(seed.payload)
    event_version = 2 if invalid_case == "unknown_version" else 1
    event_kind = (
        "unknown_settlement_event"
        if invalid_case == "unknown_kind"
        else "paper_trade_settled"
    )
    outer_outbox_id = _outbox_id(
        event_version=event_version,
        event_kind=event_kind,
        observation_sha256=str(payload["observation_sha256"]),
        trade_id=seed.trade_id,
    )
    payload["event_version"] = event_version
    payload["event_kind"] = event_kind
    payload["outbox_id"] = outer_outbox_id
    if invalid_case == "outer_payload_mismatch":
        outer_outbox_id = hashlib.sha256(
            f"{seed.outbox_id}:outer-mismatch".encode()
        ).hexdigest()
        payload["outbox_id"] = seed.outbox_id

    conn = sqlite3.connect(seed.db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        if event_version != 1:
            conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute(
            """
            INSERT INTO paper_settlement_outbox (
                outbox_id, event_version, event_kind, observation_sha256,
                trade_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outer_outbox_id,
                event_version,
                event_kind,
                payload["observation_sha256"],
                seed.trade_id,
                _canonical_json(payload),
                payload["settled_at"],
            ),
        )
        conn.executemany(
            """
            INSERT INTO paper_settlement_outbox_requirements (
                outbox_id, consumer_name
            ) VALUES (?, ?)
            """,
            [(outer_outbox_id, consumer) for consumer in DIRECTIONAL_CONSUMERS],
        )
        conn.commit()
    finally:
        conn.close()
    invalid_seed = SeededEvent(
        db_path=seed.db_path,
        outbox_id=outer_outbox_id,
        trade_id=seed.trade_id,
        payload=payload,
    )
    _record_receipts(
        invalid_seed.db_path,
        invalid_seed.outbox_id,
        ("paper_trade_log", "calibration_state"),
    )
    return invalid_seed


@pytest.mark.asyncio
async def test_run_once_projects_source_and_keywords_atomically_once(
    monkeypatch,
    tmp_path,
):
    seed = _seed_directional_event(monkeypatch, tmp_path)
    _record_receipts(
        seed.db_path,
        seed.outbox_id,
        ("paper_trade_log", "calibration_state"),
    )
    assert _pending_consumers(seed) == ("keyword_outcomes", "source_credibility")
    task = _task(seed)

    await task.run_once(limit=100)

    source_rows = _rows(
        seed.db_path,
        """
        SELECT source, wins, losses, total
        FROM source_credibility
        """,
    )
    keyword_rows = _rows(
        seed.db_path,
        """
        SELECT trade_id, ticker, series_ticker, keyword, direction,
               market_side, resolved_yes, correct, ts
        FROM keyword_outcomes
        ORDER BY id
        """,
    )
    effect_receipts = _rows(
        seed.db_path,
        """
        SELECT consumer_name
        FROM paper_settlement_consumer_receipts
        WHERE outbox_id=? AND consumer_name IN (
            'source_credibility', 'keyword_outcomes'
        )
        ORDER BY consumer_name
        """,
        (seed.outbox_id,),
    )
    expected_keywords = [
        (
            seed.trade_id,
            seed.payload["ticker"],
            "PMOUTBOX",
            "missile strike",
            "yes",
            "yes",
            1,
            1,
            seed.payload["settled_at"],
        ),
        (
            seed.trade_id,
            seed.payload["ticker"],
            "PMOUTBOX",
            "ceasefire",
            "no",
            "yes",
            1,
            0,
            seed.payload["settled_at"],
        ),
    ]
    assert source_rows == [("wire:test-source", 1, 0, 1)]
    assert keyword_rows == expected_keywords
    assert effect_receipts == [("keyword_outcomes",), ("source_credibility",)]
    assert _pending_consumers(seed) == ()

    await task.run_once(limit=100)

    assert _rows(
        seed.db_path,
        "SELECT source, wins, losses, total FROM source_credibility",
    ) == source_rows
    assert _rows(
        seed.db_path,
        """
        SELECT trade_id, ticker, series_ticker, keyword, direction,
               market_side, resolved_yes, correct, ts
        FROM keyword_outcomes ORDER BY id
        """,
    ) == keyword_rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_case",
    (
        "outer_payload_mismatch",
        "unknown_version",
        "unknown_kind",
        "unknown_consumer",
    ),
)
async def test_run_once_rejects_invalid_contract_without_effects_or_receipts(
    monkeypatch,
    tmp_path,
    invalid_case,
):
    seed = _seed_directional_event(monkeypatch, tmp_path)
    invalid_seed = _insert_invalid_event(seed, invalid_case)
    before_pending = _pending_consumers(invalid_seed)
    task = _task(invalid_seed)

    await task.run_once(limit=100)

    assert _rows(invalid_seed.db_path, "SELECT * FROM source_credibility") == []
    assert _rows(invalid_seed.db_path, "SELECT * FROM keyword_outcomes") == []
    assert _pending_consumers(invalid_seed) == before_pending
    invalid_receipts = _rows(
        invalid_seed.db_path,
        """
        SELECT consumer_name
        FROM paper_settlement_consumer_receipts
        WHERE outbox_id=? AND consumer_name IN (
            'source_credibility', 'keyword_outcomes', 'unknown_consumer'
        )
        """,
        (invalid_seed.outbox_id,),
    )
    assert invalid_receipts == []
