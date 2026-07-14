"""Behavior contract for durable settlement outbox consumers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
from trading.settlement import MarketOutcome, VoidRefundContract
from trading.settlement_store import SettlementStore
from trading.venue import MarketRef, Venue
from utils.logger import TradeLogger


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


def _task(
    seed: SeededEvent,
    *,
    calibration_task: object | None = None,
    trade_logger: object | None = None,
    clock: FixedClock | None = None,
):
    tokens = (f"worker-token-{index}" for index in itertools.count(1))
    return _settlement_outbox_task_class()(
        db_path=seed.db_path,
        calibration_task=calibration_task or CalibrationTask(),
        trade_logger=trade_logger or MagicMock(),
        clock=clock or FixedClock(),
        token_factory=lambda: next(tokens),
        lease_seconds=60,
    )


def _seed_directional_event(
    monkeypatch,
    tmp_path: Path,
    *,
    side: str = "yes",
    outcome: MarketOutcome = MarketOutcome.YES,
    db_path: Path | None = None,
    trade_id: str = "worker000001",
    venue_market_id: str = "8594",
    ticker: str = "stored-worker-alias",
    lane_estimates: tuple[float, float, float] = (0.70, 0.65, 0.60),
    event_time: datetime | None = None,
) -> SeededEvent:
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(_cfg_module.cfg, "max_ticker_exposure_pct", 0.25)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)

    import trading.paper_trader as paper_trader_module

    if event_time is not None:
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return event_time

        monkeypatch.setattr(paper_trader_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(paper_trader_module, "trade_log", MagicMock())
    db_path = db_path or tmp_path / "paper.db"
    trader = paper_trader_module.PaperTrader(
        db_path=db_path,
        startup_context="test",
    )
    trader._set_state("notional_bankroll", "500.0")
    market_ref = MarketRef(
        Venue.POLYMARKET_US,
        venue_market_id,
        ticker,
    )
    analysis = _make_mock_analysis(
        ticker=market_ref.alias,
        series_ticker="PMOUTBOX",
        side=side,
        yes_price=40.0 if side == "yes" else 60.0,
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
        "fast_lane_p": lane_estimates[0],
        "fast_lane_confidence": 0.90,
        "accumulation_p": lane_estimates[1],
        "accumulation_confidence": 0.80,
        "structural_p": lane_estimates[2],
        "structural_confidence": 0.70,
    }
    trade_id = _record_analysis(trader, analysis, trade_id=trade_id)
    observation = _observation(
        MarketRef(
            market_ref.venue,
            market_ref.venue_market_id,
            "drifted-worker-alias",
        ),
        outcome,
        void_refund=(
            VoidRefundContract(
                refund_cents_per_contract=Decimal("50"),
                refunds_entry_fee=False,
            )
            if outcome is MarketOutcome.VOID
            else None
        ),
    )
    assert _resolve(trader, observation) is True
    row = trader._conn.execute(
        """
        SELECT outbox_id, payload_json FROM paper_settlement_outbox
        WHERE trade_id=?
        """,
        (trade_id,),
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
    seed: SeededEvent,
    consumers: tuple[str, ...],
) -> None:
    settled_at = datetime.fromisoformat(str(seed.payload["settled_at"]))
    with SettlementStore(seed.db_path) as store:
        outbox_row = store.connection.execute(
            "SELECT created_at FROM paper_settlement_outbox WHERE outbox_id=?",
            (seed.outbox_id,),
        ).fetchone()
        assert outbox_row is not None
        created_at = datetime.fromisoformat(str(outbox_row["created_at"]))
        claim_at = max(settled_at, created_at)
        processed_at = claim_at + timedelta(seconds=1)
        for index, consumer_name in enumerate(consumers, start=1):
            claim_token = f"fixture-token-{index}-{consumer_name}"
            assert store.acquire_claim(
                consumer_name,
                seed.outbox_id,
                claim_token=claim_token,
                now=claim_at,
                lease_seconds=60,
            )
            assert store.record_receipt(
                consumer_name,
                seed.outbox_id,
                claim_token=claim_token,
                processed_at=processed_at,
                result_sha256=_result_sha256(seed.outbox_id, consumer_name),
            )
            receipt = store.connection.execute(
                """
                SELECT processed_at FROM paper_settlement_consumer_receipts
                WHERE outbox_id=? AND consumer_name=?
                """,
                (seed.outbox_id, consumer_name),
            ).fetchone()
            assert receipt is not None
            persisted_at = datetime.fromisoformat(str(receipt["processed_at"]))
            assert persisted_at >= settled_at
            assert persisted_at >= created_at


def _rows(db_path: Path, sql: str, parameters: tuple[object, ...] = ()) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        return [tuple(row) for row in conn.execute(sql, parameters).fetchall()]
    finally:
        conn.close()


def _execute(
    db_path: Path,
    sql: str,
    parameters: tuple[object, ...] = (),
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(sql, parameters)
        conn.commit()
    finally:
        conn.close()


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


class FailAfterAppendTradeLogger(TradeLogger):
    def __init__(self, path: Path, *, fail_after: int) -> None:
        super().__init__(path)
        self._append_count = 0
        self._fail_after = fail_after

    def _write(self, record: dict[str, object]) -> None:
        super()._write(record)
        self._append_count += 1
        if self._append_count == self._fail_after:
            raise RuntimeError("injected logger failure")


class RecordingCalibrationTask(CalibrationTask):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, object]] = []

    async def record_calibration_check(
        self,
        *,
        market_ticker: str,
        lane: str,
        lane_estimate: float,
        final_resolution: float,
        error: float,
        outbox_id: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "market_ticker": market_ticker,
                "lane": lane,
                "lane_estimate": lane_estimate,
                "final_resolution": final_resolution,
                "error": error,
                "outbox_id": outbox_id,
            }
        )
        await super().record_calibration_check(
            market_ticker=market_ticker,
            lane=lane,
            lane_estimate=lane_estimate,
            final_resolution=final_resolution,
            error=error,
            outbox_id=outbox_id,
        )


def _clock_from_payload(seed: SeededEvent) -> FixedClock:
    return FixedClock(datetime.fromisoformat(str(seed.payload["settled_at"])))


def _paper_log_receipts(seed: SeededEvent) -> list[tuple]:
    return _rows(
        seed.db_path,
        """
        SELECT consumer_name FROM paper_settlement_consumer_receipts
        WHERE outbox_id=? AND consumer_name='paper_trade_log'
        """,
        (seed.outbox_id,),
    )


def _expected_directional_log_records(seed: SeededEvent) -> list[dict[str, object]]:
    common = {
        "outbox_id": seed.outbox_id,
        "ts": seed.payload["settled_at"],
    }
    return [
        {
            "type": "PAPER_RESOLUTION",
            "trade_id": seed.trade_id,
            "ticker": seed.payload["ticker"],
            "resolved_yes": True,
            "terminal_state": "lost",
            "pnl_dollars": -10.0,
            "bankroll_delta_dollars": 0.0,
            "venue": "polymarket_us",
            **common,
        },
        {
            "type": "CALIBRATION_OBSERVATION",
            "trade_id": seed.trade_id,
            "ticker": seed.payload["ticker"],
            "market_prefix": "PMOUTBOX",
            "side": "no",
            "estimated_probability": 0.33,
            "realized_outcome": 0,
            "entry_price_cents": 40.0,
            "pnl_dollars": -10.0,
            "cost_dollars": 10.0,
            "llm_magnitude": "moderate",
            "llm_confidence": 0.81,
            "signal_source": "wire:test-source",
            "ts_entry": seed.payload["entry_ts"],
            "ts_resolved": seed.payload["settled_at"],
            **common,
        },
        *[
            {
                "type": "CALIBRATION_CHECK",
                "market_ticker": seed.payload["ticker"],
                "lane": lane,
                "lane_estimate": estimate,
                "final_resolution": 1.0,
                "error": error,
                "venue": "polymarket_us",
                **common,
            }
            for lane, estimate, error in (
                ("fast", 0.7, 0.3),
                ("accumulation", 0.65, 0.35),
                ("structural", 0.6, 0.4),
            )
        ],
    ]


def _expected_calibration_calls(seed: SeededEvent) -> list[dict[str, object]]:
    final_resolution = 1.0 if seed.payload["resolved_yes"] else 0.0
    return [
        {
            "market_ticker": seed.payload["ticker"],
            "lane": lane,
            "lane_estimate": estimate,
            "final_resolution": final_resolution,
            "error": abs(estimate - final_resolution),
            "outbox_id": seed.outbox_id,
        }
        for lane, estimate in (
            ("fast", float(seed.payload["lane_estimates"]["fast"])),
            (
                "accumulation",
                float(seed.payload["lane_estimates"]["accumulation"]),
            ),
            ("structural", float(seed.payload["lane_estimates"]["structural"])),
        )
    ]


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
        _record_receipts(seed, DIRECTIONAL_CONSUMERS)
        return seed

    _record_receipts(seed, DIRECTIONAL_CONSUMERS)
    payload = dict(seed.payload)
    original_observation_sha256 = str(payload["observation_sha256"])
    outer_observation_sha256 = hashlib.sha256(
        f"{original_observation_sha256}:{invalid_case}".encode()
    ).hexdigest()
    outer_event_version = 1
    outer_event_kind = "paper_trade_settled"
    outer_trade_id = seed.trade_id
    payload["observation_sha256"] = outer_observation_sha256

    if invalid_case == "unknown_version":
        outer_event_version = 2
        payload["event_version"] = 2
    elif invalid_case == "unknown_kind":
        outer_event_kind = "unknown_settlement_event"
        payload["event_kind"] = outer_event_kind
    elif invalid_case == "missing_field":
        payload.pop("signal_source")
    elif invalid_case == "wrong_type":
        payload["keyword_outcomes"] = "not-a-list"
    elif invalid_case == "invalid_enum":
        payload["side"] = "hold"
    elif invalid_case == "outcome_yes_resolved_no":
        payload["resolved_yes"] = False
        payload["won"] = False
        payload["terminal_state"] = "lost"
        payload["keyword_outcomes"] = [
            {"keyword": "missile strike", "direction": "yes", "correct": False},
            {"keyword": "ceasefire", "direction": "no", "correct": True},
        ]
    elif invalid_case == "outcome_no_resolved_yes":
        payload["outcome"] = "no"
    elif invalid_case == "keyword_correct_contradiction":
        payload["keyword_outcomes"][0]["correct"] = False
    elif invalid_case == "keyword_correct_none":
        payload["keyword_outcomes"][0]["correct"] = None
    elif invalid_case == "outer_event_version_mismatch":
        outer_event_version = 2
    elif invalid_case == "outer_event_kind_mismatch":
        outer_event_kind = "unknown_settlement_event"
    elif invalid_case == "outer_observation_mismatch":
        payload["observation_sha256"] = original_observation_sha256
    elif invalid_case == "outer_trade_mismatch":
        payload["trade_id"] = "payload-trade-mismatch"
    elif invalid_case not in {"malformed_json", "outer_outbox_mismatch"}:
        raise AssertionError(f"unsupported invalid case: {invalid_case}")

    outer_outbox_id = _outbox_id(
        event_version=outer_event_version,
        event_kind=outer_event_kind,
        observation_sha256=outer_observation_sha256,
        trade_id=outer_trade_id,
    )
    payload["outbox_id"] = outer_outbox_id
    if invalid_case == "outer_outbox_mismatch":
        outer_outbox_id = hashlib.sha256(
            f"{seed.outbox_id}:outer-mismatch".encode()
        ).hexdigest()
    payload_json = (
        "{not-json"
        if invalid_case == "malformed_json"
        else _canonical_json(payload)
    )

    conn = sqlite3.connect(seed.db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        observation_columns = [
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(paper_settlement_observations)"
            )
        ]
        observation_projection = [
            "?" if column == "observation_sha256" else column
            for column in observation_columns
        ]
        conn.execute(
            f"""
            INSERT INTO paper_settlement_observations ({', '.join(observation_columns)})
            SELECT {', '.join(observation_projection)}
            FROM paper_settlement_observations
            WHERE observation_sha256=?
            """,
            (outer_observation_sha256, original_observation_sha256),
        )
        if outer_event_version != 1 or outer_event_kind != "paper_trade_settled":
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
                outer_event_version,
                outer_event_kind,
                outer_observation_sha256,
                outer_trade_id,
                payload_json,
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
        invalid_seed,
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
        seed,
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
async def test_run_once_uses_losing_payload_after_trade_row_context_changes(
    monkeypatch,
    tmp_path,
):
    seed = _seed_directional_event(
        monkeypatch,
        tmp_path,
        side="no",
        outcome=MarketOutcome.YES,
    )
    assert seed.payload["won"] is False
    _record_receipts(
        seed,
        ("paper_trade_log", "calibration_state"),
    )
    _execute(
        seed.db_path,
        """
        UPDATE paper_trades
        SET signal_source='mutated-source', series_ticker='MUTATED',
            keywords_matched='["mutated-keyword"]', side='yes', resolved_yes=0
        WHERE trade_id=?
        """,
        (seed.trade_id,),
    )

    await _task(seed).run_once(limit=100)

    assert _rows(
        seed.db_path,
        "SELECT source, wins, losses, total FROM source_credibility",
    ) == [("wire:test-source", 0, 1, 1)]
    assert _rows(
        seed.db_path,
        """
        SELECT ticker, series_ticker, keyword, direction, market_side,
               resolved_yes, correct
        FROM keyword_outcomes ORDER BY id
        """,
    ) == [
        (
            seed.payload["ticker"],
            "PMOUTBOX",
            "missile strike",
            "yes",
            "no",
            1,
            1,
        ),
        (
            seed.payload["ticker"],
            "PMOUTBOX",
            "ceasefire",
            "no",
            "no",
            1,
            0,
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("consumer_name", "effect_table"),
    (
        ("source_credibility", "source_credibility"),
        ("keyword_outcomes", "keyword_outcomes"),
    ),
)
async def test_receipt_insert_failure_rolls_back_database_consumer_effect(
    monkeypatch,
    tmp_path,
    consumer_name,
    effect_table,
):
    seed = _seed_directional_event(monkeypatch, tmp_path)
    _record_receipts(
        seed,
        tuple(consumer for consumer in DIRECTIONAL_CONSUMERS if consumer != consumer_name),
    )
    _execute(
        seed.db_path,
        f"""
        CREATE TRIGGER inject_{consumer_name}_receipt_failure
        BEFORE INSERT ON paper_settlement_consumer_receipts
        WHEN NEW.consumer_name='{consumer_name}'
        BEGIN
            SELECT RAISE(ABORT, 'injected receipt failure');
        END
        """,
    )

    await _task(seed).run_once(limit=100)

    assert _rows(seed.db_path, f"SELECT * FROM {effect_table}") == []
    assert _rows(
        seed.db_path,
        """
        SELECT consumer_name FROM paper_settlement_consumer_receipts
        WHERE outbox_id=? AND consumer_name=?
        """,
        (seed.outbox_id, consumer_name),
    ) == []
    assert _pending_consumers(seed) == (consumer_name,)
    with SettlementStore(seed.db_path) as store:
        assert store.claim_state(consumer_name, seed.outbox_id, now=WORKER_NOW) == "active"


@pytest.mark.asyncio
async def test_second_keyword_insert_failure_rolls_back_batch_receipt_and_claim(
    monkeypatch,
    tmp_path,
):
    seed = _seed_directional_event(monkeypatch, tmp_path)
    _record_receipts(
        seed,
        tuple(
            consumer
            for consumer in DIRECTIONAL_CONSUMERS
            if consumer != "keyword_outcomes"
        ),
    )
    _execute(
        seed.db_path,
        """
        CREATE TRIGGER inject_second_keyword_failure
        BEFORE INSERT ON keyword_outcomes
        WHEN (SELECT COUNT(*) FROM keyword_outcomes) = 1
        BEGIN
            SELECT RAISE(ABORT, 'injected second keyword failure');
        END
        """,
    )

    await _task(seed).run_once(limit=100)

    assert _rows(seed.db_path, "SELECT * FROM keyword_outcomes") == []
    assert _rows(
        seed.db_path,
        """
        SELECT consumer_name FROM paper_settlement_consumer_receipts
        WHERE outbox_id=? AND consumer_name='keyword_outcomes'
        """,
        (seed.outbox_id,),
    ) == []
    assert _pending_consumers(seed) == ("keyword_outcomes",)
    with SettlementStore(seed.db_path) as store:
        assert (
            store.claim_state("keyword_outcomes", seed.outbox_id, now=WORKER_NOW)
            == "active"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_case",
    (
        "malformed_json",
        "missing_field",
        "wrong_type",
        "invalid_enum",
        "outer_event_version_mismatch",
        "outer_event_kind_mismatch",
        "outer_observation_mismatch",
        "outer_trade_mismatch",
        "outer_outbox_mismatch",
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
    before_effect_receipts = _rows(
        invalid_seed.db_path,
        """
        SELECT consumer_name
        FROM paper_settlement_consumer_receipts
        WHERE outbox_id=? AND consumer_name IN (
            'source_credibility', 'keyword_outcomes', 'unknown_consumer'
        )
        ORDER BY consumer_name
        """,
        (invalid_seed.outbox_id,),
    )
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
    assert invalid_receipts == before_effect_receipts
    assert ("unknown_consumer",) not in invalid_receipts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_case",
    (
        "outcome_yes_resolved_no",
        "outcome_no_resolved_yes",
        "keyword_correct_contradiction",
        "keyword_correct_none",
    ),
)
async def test_semantic_payload_contradictions_are_rejected_before_claim(
    monkeypatch,
    tmp_path,
    invalid_case,
):
    seed = _seed_directional_event(monkeypatch, tmp_path)
    invalid_seed = _insert_invalid_event(seed, invalid_case)

    await _task(invalid_seed).run_once(limit=100)

    assert _rows(invalid_seed.db_path, "SELECT * FROM source_credibility") == []
    assert _rows(invalid_seed.db_path, "SELECT * FROM keyword_outcomes") == []
    with SettlementStore(invalid_seed.db_path) as store:
        for consumer_name in ("source_credibility", "keyword_outcomes"):
            assert (
                store.claim_state(
                    consumer_name,
                    invalid_seed.outbox_id,
                    now=WORKER_NOW,
                )
                is None
            )


@pytest.mark.asyncio
async def test_limit_counts_supported_database_consumers_after_filtering(
    monkeypatch,
    tmp_path,
):
    seed = _seed_directional_event(monkeypatch, tmp_path)

    processed = await _task(seed).run_once(limit=1)

    assert processed == 1
    assert len(_rows(seed.db_path, "SELECT * FROM keyword_outcomes")) == 2
    assert _rows(seed.db_path, "SELECT * FROM source_credibility") == []
    assert _pending_consumers(seed) == (
        "calibration_state",
        "paper_trade_log",
        "source_credibility",
    )


@pytest.mark.asyncio
async def test_calibration_state_applies_payload_once_and_rebuilds_after_restart(
    monkeypatch,
    tmp_path,
):
    seed = _seed_directional_event(monkeypatch, tmp_path)
    _record_receipts(
        seed,
        ("paper_trade_log", "source_credibility", "keyword_outcomes"),
    )
    _execute(
        seed.db_path,
        """
        UPDATE paper_trades
        SET ticker='MUTATED-CALIBRATION', fast_lane_p=0.01,
            accumulation_p=0.02, structural_p=0.03, resolved_yes=0
        WHERE trade_id=?
        """,
        (seed.trade_id,),
    )
    calibration = RecordingCalibrationTask()

    await _task(
        seed,
        calibration_task=calibration,
        clock=_clock_from_payload(seed),
    ).run_once(limit=100)

    expected_calls = _expected_calibration_calls(seed)
    expected_summary = {
        "fast": {
            "lane": "fast",
            "sample_count": 1,
            "brier_score": 0.09,
            "scaling_factor": 1.0,
            "drift_detected": False,
        },
        "accumulation": {
            "lane": "accumulation",
            "sample_count": 1,
            "brier_score": 0.1225,
            "scaling_factor": 1.0,
            "drift_detected": False,
        },
        "structural": {
            "lane": "structural",
            "sample_count": 1,
            "brier_score": 0.16,
            "scaling_factor": 1.0,
            "drift_detected": False,
        },
    }
    assert calibration.calls == expected_calls
    assert calibration.get_calibration_summary() == expected_summary
    assert _paper_log_receipts(seed) == [("paper_trade_log",)]
    assert _rows(
        seed.db_path,
        """
        SELECT consumer_name FROM paper_settlement_consumer_receipts
        WHERE outbox_id=? AND consumer_name='calibration_state'
        """,
        (seed.outbox_id,),
    ) == [("calibration_state",)]
    assert _pending_consumers(seed) == ()

    rebuilt = RecordingCalibrationTask()
    await _task(
        seed,
        calibration_task=rebuilt,
        clock=_clock_from_payload(seed),
    ).run_once(limit=100)

    assert rebuilt.calls == expected_calls
    assert rebuilt.get_calibration_summary() == expected_summary
    assert _pending_consumers(seed) == ()


@pytest.mark.asyncio
async def test_calibration_receipt_failure_retry_does_not_double_samples(
    monkeypatch,
    tmp_path,
):
    seed = _seed_directional_event(monkeypatch, tmp_path)
    _record_receipts(
        seed,
        ("paper_trade_log", "source_credibility", "keyword_outcomes"),
    )
    _execute(
        seed.db_path,
        """
        CREATE TRIGGER inject_calibration_receipt_failure
        BEFORE INSERT ON paper_settlement_consumer_receipts
        WHEN NEW.consumer_name='calibration_state'
        BEGIN
            SELECT RAISE(ABORT, 'injected calibration receipt failure');
        END
        """,
    )
    calibration = RecordingCalibrationTask()
    clock = _clock_from_payload(seed)
    task = _task(seed, calibration_task=calibration, clock=clock)

    await task.run_once(limit=100)

    first_summary = calibration.get_calibration_summary()
    assert {
        lane: summary["sample_count"] for lane, summary in first_summary.items()
    } == {"fast": 1, "accumulation": 1, "structural": 1}
    assert _pending_consumers(seed) == ("calibration_state",)
    with SettlementStore(seed.db_path) as store:
        assert (
            store.claim_state("calibration_state", seed.outbox_id, now=clock.value)
            == "active"
        )

    _execute(seed.db_path, "DROP TRIGGER inject_calibration_receipt_failure")
    clock.value += timedelta(seconds=61)
    await task.run_once(limit=100)

    assert calibration.get_calibration_summary() == first_summary
    assert _rows(
        seed.db_path,
        """
        SELECT consumer_name FROM paper_settlement_consumer_receipts
        WHERE outbox_id=? AND consumer_name='calibration_state'
        """,
        (seed.outbox_id,),
    ) == [("calibration_state",)]
    assert _pending_consumers(seed) == ()


@pytest.mark.asyncio
async def test_calibration_reverse_delivery_matches_canonical_receipt_rebuild(
    monkeypatch,
    tmp_path,
):
    db_path = tmp_path / "ordered-calibration.db"
    shared_event_time = WORKER_NOW + timedelta(minutes=10)
    first = _seed_directional_event(
        monkeypatch,
        tmp_path,
        db_path=db_path,
        trade_id="worker000002",
        venue_market_id="8594",
        ticker="ordered-market-a",
        event_time=shared_event_time,
    )
    second = _seed_directional_event(
        monkeypatch,
        tmp_path,
        db_path=db_path,
        trade_id="worker000001",
        venue_market_id="8595",
        ticker="ordered-market-b",
        outcome=MarketOutcome.NO,
        lane_estimates=(0.20, 0.30, 0.40),
        event_time=shared_event_time,
    )
    assert second.payload["settled_at"] == first.payload["settled_at"]
    for seed in (first, second):
        _record_receipts(
            seed,
            ("paper_trade_log", "source_credibility", "keyword_outcomes"),
        )
    clock = FixedClock(shared_event_time)
    with SettlementStore(db_path) as store:
        assert store.acquire_claim(
            "calibration_state",
            second.outbox_id,
            claim_token="hold-canonical-first",
            now=clock.value,
            lease_seconds=60,
        )
    reverse_delivery = RecordingCalibrationTask()
    task = _task(
        first,
        calibration_task=reverse_delivery,
        clock=clock,
    )

    await task.run_once(limit=100)

    assert reverse_delivery.calls == _expected_calibration_calls(first)
    clock.value += timedelta(seconds=61)
    await task.run_once(limit=100)
    reverse_summary = reverse_delivery.get_calibration_summary()
    assert {
        lane: summary["sample_count"] for lane, summary in reverse_summary.items()
    } == {"fast": 2, "accumulation": 2, "structural": 2}
    assert _pending_consumers(first) == ()
    assert _pending_consumers(second) == ()

    rebuilt = RecordingCalibrationTask()
    await _task(
        second,
        calibration_task=rebuilt,
        clock=clock,
    ).run_once(limit=100)

    canonical_events = sorted(
        (first, second),
        key=lambda seed: (str(seed.payload["settled_at"]), seed.trade_id),
    )
    assert [seed.trade_id for seed in canonical_events] == [
        "worker000001",
        "worker000002",
    ]
    assert rebuilt.calls == [
        call
        for seed in canonical_events
        for call in _expected_calibration_calls(seed)
    ]
    assert [call["outbox_id"] for call in rebuilt.calls] == [
        *([second.outbox_id] * 3),
        *([first.outbox_id] * 3),
    ]
    assert [call["lane"] for call in rebuilt.calls] == [
        "fast",
        "accumulation",
        "structural",
        "fast",
        "accumulation",
        "structural",
    ]
    assert rebuilt.get_calibration_summary() == reverse_summary


@pytest.mark.asyncio
async def test_paper_trade_log_emits_directional_resolution_and_calibration_lineage(
    monkeypatch,
    tmp_path,
):
    seed = _seed_directional_event(
        monkeypatch,
        tmp_path,
        side="no",
        outcome=MarketOutcome.YES,
    )
    _record_receipts(
        seed,
        ("source_credibility", "calibration_state", "keyword_outcomes"),
    )
    log_path = tmp_path / "worker-trades.jsonl"

    await _task(
        seed,
        trade_logger=TradeLogger(log_path),
        clock=_clock_from_payload(seed),
    ).run_once(limit=100)

    records = _read_jsonl(log_path)
    assert records == _expected_directional_log_records(seed)
    assert _paper_log_receipts(seed) == [("paper_trade_log",)]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after", range(1, 6))
async def test_logger_failure_retries_at_least_once_with_stable_outbox_lineage(
    monkeypatch,
    tmp_path,
    fail_after,
):
    seed = _seed_directional_event(
        monkeypatch,
        tmp_path,
        side="no",
        outcome=MarketOutcome.YES,
    )
    _record_receipts(
        seed,
        ("source_credibility", "calibration_state", "keyword_outcomes"),
    )
    log_path = tmp_path / "retry-trades.jsonl"
    logger = FailAfterAppendTradeLogger(log_path, fail_after=fail_after)
    clock = _clock_from_payload(seed)
    task = _task(seed, trade_logger=logger, clock=clock)
    expected = _expected_directional_log_records(seed)

    await task.run_once(limit=100)

    first_records = _read_jsonl(log_path)
    assert first_records == expected[:fail_after]
    assert _paper_log_receipts(seed) == []
    with SettlementStore(seed.db_path) as store:
        assert (
            store.claim_state("paper_trade_log", seed.outbox_id, now=clock.value)
            == "active"
        )

    clock.value += timedelta(seconds=59)
    await task.run_once(limit=100)

    assert _read_jsonl(log_path) == first_records
    assert _paper_log_receipts(seed) == []

    clock.value += timedelta(seconds=2)
    await task.run_once(limit=100)

    retried_records = _read_jsonl(log_path)
    assert retried_records == [*expected[:fail_after], *expected]
    assert retried_records[:fail_after] == retried_records[
        fail_after : fail_after * 2
    ]
    assert all(row["outbox_id"] == seed.outbox_id for row in retried_records)
    assert all(row["ts"] == seed.payload["settled_at"] for row in retried_records)
    assert _paper_log_receipts(seed) == [("paper_trade_log",)]


@pytest.mark.asyncio
async def test_paper_trade_log_emits_void_without_directional_calibration(
    monkeypatch,
    tmp_path,
):
    seed = _seed_directional_event(
        monkeypatch,
        tmp_path,
        outcome=MarketOutcome.VOID,
    )
    log_path = tmp_path / "void-trades.jsonl"

    await _task(
        seed,
        trade_logger=TradeLogger(log_path),
        clock=_clock_from_payload(seed),
    ).run_once(limit=100)

    records = _read_jsonl(log_path)
    assert records == [
        {
            "type": "PAPER_RESOLUTION",
            "trade_id": seed.trade_id,
            "ticker": seed.payload["ticker"],
            "resolved_yes": None,
            "terminal_state": "void",
            "pnl_dollars": 2.5,
            "bankroll_delta_dollars": 12.5,
            "venue": "polymarket_us",
            "outbox_id": seed.outbox_id,
            "ts": seed.payload["settled_at"],
        }
    ]
    assert _rows(
        seed.db_path,
        """
        SELECT consumer_name FROM paper_settlement_consumer_receipts
        WHERE outbox_id=? AND consumer_name='paper_trade_log'
        """,
        (seed.outbox_id,),
    ) == [("paper_trade_log",)]
