"""Lazy append-only persistence for weather shadow calibration data."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Literal, TypeVar

from utils.output_paths import REPO_ROOT, WEATHER_SHADOW_DB
from weather.shadow_models import (
    CaptureBatch,
    Fingerprints,
    OutcomeBatch,
    OutcomeCheck,
    OutcomeRow,
    OutcomeTarget,
    ShadowQuote,
)


_BUSY_TIMEOUT_MS = 5_000
_T = TypeVar("_T")
_OUTCOME_IMMUTABLE_COLUMNS = (
    "outcome_id, outcome_batch_id, market_ticker, event_ticker, expected_sibling_count, "
    "result, kalshi_status, settlement_observed_at, source_payload_hash, "
    "contract_fingerprint, rules_source_fingerprint, settlement_source_fingerprint, "
    "official_high_f, official_evidence_id, official_source_url, official_product_id, "
    "official_issued_at, official_retrieved_at, label_available_at"
)
_CHECK_IMMUTABLE_COLUMNS = (
    "check_id, event_ticker, check_date_utc, checked_at, check_kind, "
    "observed_batch_hash, baseline_batch_hash, agrees_with_baseline, details_json"
)


@dataclass(frozen=True)
class CaptureKeyState:
    capture_key: str
    claimed: bool
    snapshot_id: str | None = None
    quotes_hash: str | None = None
    conflicted: bool = False


@dataclass(frozen=True)
class CaptureWriteResult:
    status: Literal["inserted", "identical", "conflict"]
    snapshot_id: str
    conflict_id: str | None = None


@dataclass(frozen=True)
class OutcomeWriteResult:
    status: Literal["inserted", "identical", "conflict"]
    outcome_batch_id: str
    conflict_id: str | None = None


@dataclass(frozen=True)
class CheckWriteResult:
    status: Literal["inserted", "identical", "conflict"]
    check_id: str
    conflict_id: str | None = None


class OutcomeCheckKindError(ValueError):
    """Raised when a caller attempts to append a store-owned seal check."""


class OutcomeCheckTimingError(ValueError):
    """Raised when a daily check is attributed to the wrong UTC date."""


@dataclass(frozen=True)
class SealResult:
    status: Literal["sealed", "already_sealed", "not_ready", "quarantined"]
    event_ticker: str
    check_id: str | None = None


@dataclass(frozen=True)
class LabelState:
    event_ticker: str
    labeled: bool
    sealed: bool
    quarantined: bool
    outcome_batch_ids: tuple[str, ...]
    daily_check_count: int


class WeatherShadowStore:
    """Dedicated SQLite store with no constructor-time I/O."""

    def __init__(
        self,
        db_path: Path = WEATHER_SHADOW_DB,
        schema_path: Path = REPO_ROOT / "docs/weather_shadow_schema.sql",
    ) -> None:
        self.db_path = db_path
        self.schema_path = schema_path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def capture_key_state(self, capture_key: str) -> CaptureKeyState:
        return await asyncio.to_thread(self._capture_key_state_sync, capture_key)

    async def append_capture(self, batch: CaptureBatch) -> CaptureWriteResult:
        _validate_capture_batch(batch)
        return await asyncio.to_thread(self._append_capture_sync, batch)

    async def list_outcome_targets(self, now: datetime) -> tuple[OutcomeTarget, ...]:
        return await asyncio.to_thread(self._list_outcome_targets_sync, now)

    async def capture_fingerprints(
        self, event_ticker: str
    ) -> dict[str, Fingerprints]:
        return await asyncio.to_thread(self._capture_fingerprints_sync, event_ticker)

    async def append_outcome_batch(self, batch: OutcomeBatch) -> OutcomeWriteResult:
        _validate_complete_outcome_batch(batch)
        return await asyncio.to_thread(self._append_outcome_batch_sync, batch)

    async def append_outcome_check(self, check: OutcomeCheck) -> CheckWriteResult:
        if check.check_kind != "daily":
            raise OutcomeCheckKindError("append_outcome_check accepts daily checks only")
        _validate_outcome_check_timing(check)
        return await asyncio.to_thread(self._append_outcome_check_sync, check)

    async def try_seal_event(self, event_ticker: str, now: datetime) -> SealResult:
        return await asyncio.to_thread(self._try_seal_event_sync, event_ticker, now)

    async def label_state(self, event_ticker: str) -> LabelState:
        return await asyncio.to_thread(self._label_state_sync, event_ticker)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_MS / 1_000, isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode=WAL")
        except BaseException:
            conn.close()
            raise
        return conn

    def _initialize_sync(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema = self.schema_path.read_text(encoding="utf-8")
        conn = self._connect()
        try:
            conn.executescript(schema)
        finally:
            conn.close()

    def _read(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        conn = self._connect()
        try:
            return operation(conn)
        finally:
            conn.close()

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = operation(conn)
            conn.commit()
            return result
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _capture_key_state_sync(self, capture_key: str) -> CaptureKeyState:
        def read(conn: sqlite3.Connection) -> CaptureKeyState:
            row = conn.execute(
                "SELECT snapshot_id, quotes_hash FROM research_weather_shadow_snapshots "
                "WHERE capture_key = ?",
                (capture_key,),
            ).fetchone()
            conflict = conn.execute(
                "SELECT 1 FROM research_weather_shadow_conflicts "
                "WHERE entity_type = 'snapshot' AND entity_key = ? LIMIT 1",
                (capture_key,),
            ).fetchone()
            return CaptureKeyState(
                capture_key=capture_key,
                claimed=row is not None,
                snapshot_id=None if row is None else str(row[0]),
                quotes_hash=None if row is None else str(row[1]),
                conflicted=conflict is not None,
            )

        return self._read(read)

    def _append_capture_sync(self, batch: CaptureBatch) -> CaptureWriteResult:
        def write(conn: sqlite3.Connection) -> CaptureWriteResult:
            existing = conn.execute(
                "SELECT snapshot_id, quotes_hash FROM research_weather_shadow_snapshots "
                "WHERE capture_key = ?",
                (batch.capture_key,),
            ).fetchone()
            if existing is not None:
                if existing == (batch.snapshot_id, batch.quotes_hash):
                    return CaptureWriteResult("identical", batch.snapshot_id)
                conflict_id = _insert_conflict(
                    conn,
                    entity_type="snapshot",
                    entity_key=batch.capture_key,
                    existing_hash=str(existing[0]),
                    incoming_hash=batch.snapshot_id,
                    details={
                        "capture_key": batch.capture_key,
                        "existing_snapshot_id": str(existing[0]),
                        "incoming_snapshot_id": batch.snapshot_id,
                    },
                )
                return CaptureWriteResult("conflict", batch.snapshot_id, conflict_id)

            _insert_snapshot(conn, batch)
            for item in batch.quotes:
                _insert_quote(conn, batch.snapshot_id, item)
            return CaptureWriteResult("inserted", batch.snapshot_id)

        return self._write(write)

    def _list_outcome_targets_sync(self, now: datetime) -> tuple[OutcomeTarget, ...]:
        def write(conn: sqlite3.Connection) -> tuple[OutcomeTarget, ...]:
            captures = conn.execute(
                "SELECT DISTINCT event_ticker, target_date "
                "FROM research_weather_shadow_snapshots ORDER BY target_date, event_ticker"
            ).fetchall()
            targets: list[OutcomeTarget] = []
            for event_ticker, target_date in captures:
                ticker = str(event_ticker)
                _record_missed_check_conflicts(conn, ticker, now)
                state = _label_state(conn, ticker)
                if state.sealed:
                    continue
                target = OutcomeTarget(ticker, date.fromisoformat(str(target_date)))
                if not state.labeled:
                    targets.append(target)
                    continue
                baseline = _outcome_baseline(conn, ticker)
                if baseline is None:
                    continue
                today = now.astimezone(timezone.utc).date()
                if today not in _expected_check_dates(baseline[1]):
                    continue
                checked = conn.execute(
                    "SELECT 1 FROM research_weather_shadow_outcome_checks "
                    "WHERE event_ticker = ? AND check_date_utc = ? "
                    "AND check_kind = 'daily' LIMIT 1",
                    (ticker, today.isoformat()),
                ).fetchone()
                if checked is None:
                    targets.append(target)
            return tuple(targets)

        return self._write(write)

    def _capture_fingerprints_sync(self, event_ticker: str) -> dict[str, Fingerprints]:
        def read(conn: sqlite3.Connection) -> dict[str, Fingerprints]:
            rows = conn.execute(
                "SELECT q.market_ticker, q.contract_fingerprint, q.rules_source_fingerprint, "
                "q.settlement_source_fingerprint FROM research_weather_shadow_quotes q "
                "JOIN research_weather_shadow_snapshots s ON s.snapshot_id = q.snapshot_id "
                "WHERE s.event_ticker = ? ORDER BY q.market_ticker",
                (event_ticker,),
            ).fetchall()
            result: dict[str, Fingerprints] = {}
            for ticker, contract, rules, settlement in rows:
                value = Fingerprints(str(contract), str(rules), str(settlement))
                previous = result.setdefault(str(ticker), value)
                if previous != value:
                    raise ValueError(f"capture fingerprints disagree for {ticker}")
            return result

        return self._read(read)

    def _append_outcome_batch_sync(self, batch: OutcomeBatch) -> OutcomeWriteResult:
        def write(conn: sqlite3.Connection) -> OutcomeWriteResult:
            incoming = sorted(_outcome_values(row) for row in batch.rows)
            existing_batch = sorted(
                tuple(row)
                for row in conn.execute(
                    f"SELECT {_OUTCOME_IMMUTABLE_COLUMNS} "
                    "FROM research_weather_shadow_outcomes WHERE outcome_batch_id = ?",
                    (batch.outcome_batch_id,),
                )
            )
            if existing_batch == incoming:
                return OutcomeWriteResult("identical", batch.outcome_batch_id)
            if existing_batch:
                conflict_id = _insert_conflict(
                    conn,
                    entity_type="outcome",
                    entity_key=batch.event_ticker,
                    existing_hash=_records_hash(existing_batch),
                    incoming_hash=_records_hash(incoming),
                    details={
                        "event_ticker": batch.event_ticker,
                        "existing_batch_id": batch.outcome_batch_id,
                        "incoming_batch_id": batch.outcome_batch_id,
                        "reason": "immutable_outcome_retry_mismatch",
                    },
                )
                return OutcomeWriteResult("conflict", batch.outcome_batch_id, conflict_id)

            collisions: list[tuple[object, ...]] = []
            for row in batch.rows:
                collisions.extend(
                    tuple(item)
                    for item in conn.execute(
                        f"SELECT {_OUTCOME_IMMUTABLE_COLUMNS} "
                        "FROM research_weather_shadow_outcomes "
                        "WHERE outcome_id = ? OR (market_ticker = ? AND source_payload_hash = ?)",
                        (row.outcome_id, row.market_ticker, row.source_payload_hash),
                    )
                )
            if collisions:
                conflict_id = _insert_conflict(
                    conn,
                    entity_type="outcome",
                    entity_key=batch.event_ticker,
                    existing_hash=_records_hash(sorted(set(collisions))),
                    incoming_hash=_records_hash(incoming),
                    details={
                        "event_ticker": batch.event_ticker,
                        "incoming_batch_id": batch.outcome_batch_id,
                        "reason": "outcome_identity_collision",
                    },
                )
                return OutcomeWriteResult("conflict", batch.outcome_batch_id, conflict_id)

            prior_rows = sorted(
                tuple(row)
                for row in conn.execute(
                    f"SELECT {_OUTCOME_IMMUTABLE_COLUMNS} "
                    "FROM research_weather_shadow_outcomes "
                    "WHERE event_ticker = ?",
                    (batch.event_ticker,),
                )
            )
            for row in batch.rows:
                _insert_outcome(conn, row)
            if not prior_rows:
                return OutcomeWriteResult("inserted", batch.outcome_batch_id)

            existing_hash = _records_hash(prior_rows)
            conflict_id = _insert_conflict(
                conn,
                entity_type="outcome",
                entity_key=batch.event_ticker,
                existing_hash=existing_hash,
                incoming_hash=_records_hash(incoming),
                details={
                    "event_ticker": batch.event_ticker,
                    "existing_batch_hash": existing_hash,
                    "incoming_batch_hash": batch.outcome_batch_id,
                },
            )
            return OutcomeWriteResult("conflict", batch.outcome_batch_id, conflict_id)

        return self._write(write)

    def _append_outcome_check_sync(self, check: OutcomeCheck) -> CheckWriteResult:
        def write(conn: sqlite3.Connection) -> CheckWriteResult:
            existing = conn.execute(
                f"SELECT {_CHECK_IMMUTABLE_COLUMNS} "
                "FROM research_weather_shadow_outcome_checks "
                "WHERE event_ticker = ? AND check_date_utc = ? AND check_kind = ?",
                (check.event_ticker, check.check_date_utc.isoformat(), check.check_kind),
            ).fetchone()
            expected = _check_values(check)
            if existing is not None:
                if tuple(existing) == expected:
                    return CheckWriteResult("identical", check.check_id)
                identity = _check_entity_key(
                    check.event_ticker, check.check_date_utc.isoformat(), check.check_kind
                )
                conflict_id = _insert_conflict(
                    conn,
                    entity_type="outcome",
                    entity_key=identity,
                    existing_hash=_records_hash([tuple(existing)]),
                    incoming_hash=_records_hash([expected]),
                    details={
                        "check_date_utc": check.check_date_utc.isoformat(),
                        "check_kind": check.check_kind,
                        "event_ticker": check.event_ticker,
                        "reason": "immutable_check_retry_mismatch",
                    },
                )
                return CheckWriteResult("conflict", check.check_id, conflict_id)
            conn.execute(
                "INSERT INTO research_weather_shadow_outcome_checks "
                "(check_id, event_ticker, check_date_utc, checked_at, check_kind, "
                "observed_batch_hash, baseline_batch_hash, agrees_with_baseline, details_json, created_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    check.check_id,
                    check.event_ticker,
                    check.check_date_utc.isoformat(),
                    _timestamp(check.checked_at),
                    check.check_kind,
                    check.observed_batch_hash,
                    check.baseline_batch_hash,
                    int(check.agrees_with_baseline),
                    check.details_json,
                    _now_timestamp(),
                ),
            )
            return CheckWriteResult("inserted", check.check_id)

        return self._write(write)

    def _try_seal_event_sync(self, event_ticker: str, now: datetime) -> SealResult:
        def write(conn: sqlite3.Connection) -> SealResult:
            _record_missed_check_conflicts(conn, event_ticker, now)
            state = _label_state(conn, event_ticker)
            if state.quarantined:
                return SealResult("quarantined", event_ticker)
            if state.sealed:
                return SealResult("already_sealed", event_ticker)
            if not state.labeled:
                return SealResult("not_ready", event_ticker)
            baseline = _outcome_baseline(conn, event_ticker)
            if baseline is None:
                return SealResult("not_ready", event_ticker)
            baseline_hash, first_label = baseline
            expected_dates = _expected_check_dates(first_label)
            if now.astimezone(timezone.utc).date() < expected_dates[-1]:
                return SealResult("not_ready", event_ticker)
            rows = conn.execute(
                "SELECT check_date_utc, observed_batch_hash, baseline_batch_hash, agrees_with_baseline "
                "FROM research_weather_shadow_outcome_checks "
                "WHERE event_ticker = ? AND check_kind = 'daily' "
                "ORDER BY check_date_utc",
                (event_ticker,),
            ).fetchall()
            by_date = {date.fromisoformat(str(row[0])): row for row in rows}
            if any(expected not in by_date for expected in expected_dates):
                return SealResult("not_ready", event_ticker)
            if any(
                not bool(by_date[expected][3])
                or str(by_date[expected][1]) != baseline_hash
                or str(by_date[expected][2]) != baseline_hash
                for expected in expected_dates
            ):
                return SealResult("quarantined", event_ticker)
            check_date = now.astimezone(timezone.utc).date().isoformat()
            check_id = _content_hash(
                {
                    "event_ticker": event_ticker,
                    "check_date_utc": check_date,
                    "kind": "seal",
                    "baseline": baseline_hash,
                }
            )
            conn.execute(
                "INSERT INTO research_weather_shadow_outcome_checks "
                "(check_id, event_ticker, check_date_utc, checked_at, check_kind, "
                "observed_batch_hash, baseline_batch_hash, agrees_with_baseline, details_json, created_ts) "
                "VALUES (?, ?, ?, ?, 'seal', ?, ?, 1, ?, ?)",
                (
                    check_id,
                    event_ticker,
                    check_date,
                    _timestamp(now),
                    baseline_hash,
                    baseline_hash,
                    _canonical_json({"daily_checks": 7, "event_ticker": event_ticker}),
                    _now_timestamp(),
                ),
            )
            return SealResult("sealed", event_ticker, check_id)

        return self._write(write)

    def _label_state_sync(self, event_ticker: str) -> LabelState:
        return self._read(lambda conn: _label_state(conn, event_ticker))


def _insert_snapshot(conn: sqlite3.Connection, batch: CaptureBatch) -> None:
    features = batch.features
    conn.execute(
        "INSERT INTO research_weather_shadow_snapshots "
        "(snapshot_id, capture_key, event_ticker, target_date, capture_started_at, "
        "capture_finished_at, as_of, close_time, seconds_to_close, horizon_bucket, "
        "forecast_issued_at, forecast_valid_start, forecast_valid_end, observation_measured_at, "
        "observation_coverage_start, observation_count, weather_retrieved_at, "
        "grid_forecast_high_f, hourly_forecast_high_f, running_observed_high_f, "
        "forecast_spread_f, target_weekday, source_payload_hash, source_payload_json, "
        "quotes_hash, fee_schedule_version, model_version, shadow_only, diagnostic_only, created_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?)",
        (
            batch.snapshot_id,
            batch.capture_key,
            batch.event_ticker,
            batch.target_date.isoformat(),
            _timestamp(batch.capture_started_at),
            _timestamp(batch.capture_finished_at),
            _timestamp(batch.as_of),
            _timestamp(batch.close_time),
            float(batch.seconds_to_close),
            batch.horizon_bucket,
            _timestamp(features.forecast_issued_at),
            _timestamp(features.forecast_valid_start),
            _timestamp(features.forecast_valid_end),
            _timestamp(features.observation_measured_at),
            _timestamp(features.observation_coverage_start),
            features.observation_count,
            _timestamp(features.weather_retrieved_at),
            float(features.grid_forecast_high_f),
            float(features.hourly_forecast_high_f),
            float(features.running_observed_high_f),
            float(features.forecast_spread_f),
            features.target_weekday,
            features.source_payload_hash,
            features.source_payload_json,
            batch.quotes_hash,
            batch.fee_schedule_version,
            batch.model_version,
            _now_timestamp(),
        ),
    )


def _insert_quote(conn: sqlite3.Connection, snapshot_id: str, quote: ShadowQuote) -> None:
    conn.execute(
        "INSERT INTO research_weather_shadow_quotes "
        "(snapshot_id, market_ticker, close_time, lower_bound_f, upper_bound_f, "
        "is_lower_tail, is_upper_tail, contract_fingerprint, rules_source_fingerprint, "
        "settlement_source_fingerprint, yes_bid_cents, yes_ask_cents, no_bid_cents, "
        "no_ask_cents, yes_bid_size_fp, yes_ask_size_fp, no_bid_size_fp, no_ask_size_fp, "
        "last_price_cents, volume_fp, price_retrieved_at, raw_payload_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot_id,
            quote.market_ticker,
            _timestamp(quote.close_time),
            quote.lower_bound_f,
            quote.upper_bound_f,
            int(quote.is_lower_tail),
            int(quote.is_upper_tail),
            quote.fingerprints.contract,
            quote.fingerprints.rules_source,
            quote.fingerprints.settlement_source,
            quote.yes_bid_cents,
            quote.yes_ask_cents,
            quote.no_bid_cents,
            quote.no_ask_cents,
            _decimal_text(quote.yes_bid_size),
            _decimal_text(quote.yes_ask_size),
            _decimal_text(quote.no_bid_size),
            _decimal_text(quote.no_ask_size),
            quote.last_price_cents,
            None if quote.volume is None else _decimal_text(quote.volume),
            _timestamp(quote.price_retrieved_at),
            quote.raw_payload_hash,
        ),
    )


def _insert_outcome(conn: sqlite3.Connection, row: OutcomeRow) -> None:
    conn.execute(
        "INSERT INTO research_weather_shadow_outcomes "
        "(outcome_id, outcome_batch_id, market_ticker, event_ticker, expected_sibling_count, "
        "result, kalshi_status, settlement_observed_at, source_payload_hash, contract_fingerprint, "
        "rules_source_fingerprint, settlement_source_fingerprint, official_high_f, official_evidence_id, "
        "official_source_url, official_product_id, official_issued_at, official_retrieved_at, "
        "label_available_at, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*_outcome_values(row), _now_timestamp()),
    )


def _outcome_values(row: OutcomeRow) -> tuple[object, ...]:
    return (
        row.outcome_id,
        row.outcome_batch_id,
        row.market_ticker,
        row.event_ticker,
        row.expected_sibling_count,
        row.result,
        row.kalshi_status,
        _timestamp(row.settlement_observed_at),
        row.source_payload_hash,
        row.fingerprints.contract,
        row.fingerprints.rules_source,
        row.fingerprints.settlement_source,
        float(row.official_high_f),
        row.official_evidence_id,
        row.official_source_url,
        row.official_product_id,
        _timestamp(row.official_issued_at),
        _timestamp(row.official_retrieved_at),
        _timestamp(row.label_available_at),
    )


def _check_values(check: OutcomeCheck) -> tuple[object, ...]:
    return (
        check.check_id,
        check.event_ticker,
        check.check_date_utc.isoformat(),
        _timestamp(check.checked_at),
        check.check_kind,
        check.observed_batch_hash,
        check.baseline_batch_hash,
        int(check.agrees_with_baseline),
        check.details_json,
    )


def _records_hash(records: list[tuple[object, ...]]) -> str:
    return _content_hash([list(record) for record in sorted(records)])


def _check_entity_key(event_ticker: str, check_date: str, check_kind: str) -> str:
    return f"outcome_check:{event_ticker}:{check_date}:{check_kind}"


def _insert_conflict(
    conn: sqlite3.Connection,
    *,
    entity_type: Literal["snapshot", "outcome"],
    entity_key: str,
    existing_hash: str,
    incoming_hash: str,
    details: dict[str, object],
) -> str:
    identity = {
        "entity_key": entity_key,
        "entity_type": entity_type,
        "existing_hash": existing_hash,
        "incoming_hash": incoming_hash,
    }
    conflict_id = _content_hash(identity)
    now = _now_timestamp()
    conn.execute(
        "INSERT OR IGNORE INTO research_weather_shadow_conflicts "
        "(conflict_id, entity_type, entity_key, existing_hash, incoming_hash, observed_at, details_json, created_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            conflict_id,
            entity_type,
            entity_key,
            existing_hash,
            incoming_hash,
            now,
            _canonical_json(details),
            now,
        ),
    )
    return conflict_id


def _validate_complete_ladder(quotes: tuple[ShadowQuote, ...]) -> None:
    if len(quotes) < 3 or len({quote.market_ticker for quote in quotes}) != len(quotes):
        raise ValueError("complete quote ladder required")
    lower_tails = [quote for quote in quotes if quote.is_lower_tail]
    upper_tails = [quote for quote in quotes if quote.is_upper_tail]
    if len(lower_tails) != 1 or len(upper_tails) != 1:
        raise ValueError("complete quote ladder required")
    if lower_tails[0].lower_bound_f is not None or lower_tails[0].upper_bound_f is None:
        raise ValueError("complete quote ladder required")
    if upper_tails[0].lower_bound_f is None or upper_tails[0].upper_bound_f is not None:
        raise ValueError("complete quote ladder required")
    ordered = sorted(
        quotes,
        key=lambda quote: (
            quote.lower_bound_f is not None,
            quote.lower_bound_f if quote.lower_bound_f is not None else -10_000,
        ),
    )
    if ordered[0] is not lower_tails[0] or ordered[-1] is not upper_tails[0]:
        raise ValueError("complete quote ladder required")
    for quote in ordered[1:-1]:
        if (
            quote.is_lower_tail
            or quote.is_upper_tail
            or quote.lower_bound_f is None
            or quote.upper_bound_f is None
            or quote.lower_bound_f > quote.upper_bound_f
        ):
            raise ValueError("complete quote ladder required")
    if any(
        previous.upper_bound_f is None
        or current.lower_bound_f is None
        or previous.upper_bound_f + 1 != current.lower_bound_f
        for previous, current in zip(ordered, ordered[1:])
    ):
        raise ValueError("complete quote ladder required")


def _validate_capture_batch(batch: CaptureBatch) -> None:
    _validate_complete_ladder(batch.quotes)
    expected_seconds = _elapsed_seconds(batch.close_time, batch.as_of)
    if batch.seconds_to_close != expected_seconds or expected_seconds < 0:
        raise ValueError("capture batch seconds_to_close is inconsistent")
    for quote in batch.quotes:
        if quote.close_time != batch.close_time:
            raise ValueError("capture batch quote close_time is inconsistent")
        if quote.yes_bid_cents > quote.yes_ask_cents or quote.no_bid_cents > quote.no_ask_cents:
            raise ValueError("capture batch contains a crossed quote book")
        if (
            quote.yes_bid_cents + quote.no_ask_cents != 100
            or quote.yes_ask_cents + quote.no_bid_cents != 100
        ):
            raise ValueError("capture batch quote complements are inconsistent")


def _elapsed_seconds(later: datetime, earlier: datetime) -> Decimal:
    if later.tzinfo is None or earlier.tzinfo is None:
        raise ValueError("capture batch timestamps must be timezone-aware")
    delta = later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)
    return Decimal(delta.days * 86_400 + delta.seconds) + (
        Decimal(delta.microseconds) / Decimal(1_000_000)
    )


def _validate_complete_outcome_batch(batch: OutcomeBatch) -> None:
    if not batch.rows:
        raise ValueError("complete outcome ladder required")
    expected = batch.rows[0].expected_sibling_count
    if expected != len(batch.rows) or len({row.market_ticker for row in batch.rows}) != expected:
        raise ValueError("complete outcome ladder required")
    if any(
        row.event_ticker != batch.event_ticker
        or row.outcome_batch_id != batch.outcome_batch_id
        or row.expected_sibling_count != expected
        or row.settlement_observed_at != batch.settlement_observed_at
        or row.label_available_at != batch.label_available_at
        for row in batch.rows
    ):
        raise ValueError("complete outcome ladder required")
    if sum(row.result == "yes" for row in batch.rows) != 1:
        raise ValueError("complete outcome ladder requires exactly one YES")


def _validate_outcome_check_timing(check: OutcomeCheck) -> None:
    if check.checked_at.tzinfo is None or check.checked_at.utcoffset() is None:
        raise OutcomeCheckTimingError("checked_at must be timezone-aware with a UTC date")
    checked_date = check.checked_at.astimezone(timezone.utc).date()
    if check.check_date_utc != checked_date:
        raise OutcomeCheckTimingError(
            "check_date_utc must equal the checked_at UTC date"
        )


def _coherent_outcome_batches(
    conn: sqlite3.Connection, event_ticker: str
) -> tuple[list[tuple[str, datetime]], bool, tuple[str, ...]]:
    batch_ids = tuple(
        sorted(
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT outcome_batch_id FROM research_weather_shadow_outcomes "
                "WHERE event_ticker = ?",
                (event_ticker,),
            )
        )
    )
    coherent: list[tuple[str, datetime]] = []
    incoherent = False
    for batch_id in batch_ids:
        batch_rows = conn.execute(
            "SELECT market_ticker, event_ticker, expected_sibling_count, result, label_available_at "
            "FROM research_weather_shadow_outcomes WHERE outcome_batch_id = ?",
            (batch_id,),
        ).fetchall()
        expected_values = {int(row[2]) for row in batch_rows}
        label_values = {str(row[4]) for row in batch_rows}
        valid = (
            len(expected_values) == 1
            and len(label_values) == 1
            and len(batch_rows) == next(iter(expected_values))
            and len({str(row[0]) for row in batch_rows}) == len(batch_rows)
            and all(str(row[1]) == event_ticker for row in batch_rows)
            and sum(str(row[3]) == "yes" for row in batch_rows) == 1
        )
        if valid:
            coherent.append((batch_id, _parse_timestamp(next(iter(label_values)))))
        else:
            incoherent = True
    return coherent, incoherent, batch_ids


def _outcome_baseline(
    conn: sqlite3.Connection, event_ticker: str
) -> tuple[str, datetime] | None:
    coherent, _, _ = _coherent_outcome_batches(conn, event_ticker)
    if not coherent:
        return None
    return min(coherent, key=lambda item: (item[1], item[0]))


def _expected_check_dates(first_label: datetime) -> tuple[date, ...]:
    first_date = first_label.astimezone(timezone.utc).date()
    return tuple(first_date + timedelta(days=offset) for offset in range(1, 8))


def _record_missed_check_conflicts(
    conn: sqlite3.Connection, event_ticker: str, now: datetime
) -> None:
    baseline = _outcome_baseline(conn, event_ticker)
    if baseline is None:
        return
    baseline_hash, first_label = baseline
    present = {
        date.fromisoformat(str(row[0]))
        for row in conn.execute(
            "SELECT check_date_utc FROM research_weather_shadow_outcome_checks "
            "WHERE event_ticker = ? AND check_kind = 'daily'",
            (event_ticker,),
        )
    }
    now_date = now.astimezone(timezone.utc).date()
    for expected in _expected_check_dates(first_label):
        if now_date <= expected or expected in present:
            continue
        identity = _check_entity_key(event_ticker, expected.isoformat(), "daily")
        _insert_conflict(
            conn,
            entity_type="outcome",
            entity_key=identity,
            existing_hash=baseline_hash,
            incoming_hash=_content_hash(
                {"check_date_utc": expected.isoformat(), "status": "missing"}
            ),
            details={
                "check_date_utc": expected.isoformat(),
                "check_kind": "daily",
                "event_ticker": event_ticker,
                "reason": "expected_daily_check_missing",
            },
        )


def _label_state(conn: sqlite3.Connection, event_ticker: str) -> LabelState:
    coherent, incoherent, batch_ids = _coherent_outcome_batches(conn, event_ticker)
    labeled = bool(coherent)
    baseline = _outcome_baseline(conn, event_ticker)
    baseline_hash = None if baseline is None else baseline[0]
    expected_dates = () if baseline is None else _expected_check_dates(baseline[1])
    versions: dict[str, set[tuple[str, str]]] = {}
    for ticker, result, source_hash in conn.execute(
        "SELECT market_ticker, result, source_payload_hash "
        "FROM research_weather_shadow_outcomes WHERE event_ticker = ?",
        (event_ticker,),
    ):
        versions.setdefault(str(ticker), set()).add((str(result), str(source_hash)))
    conflict = conn.execute(
        "SELECT 1 FROM research_weather_shadow_conflicts "
        "WHERE entity_type = 'outcome' AND (entity_key = ? OR entity_key LIKE ?) LIMIT 1",
        (event_ticker, f"outcome_check:{event_ticker}:%"),
    ).fetchone()
    checks = conn.execute(
        "SELECT check_date_utc, check_kind, observed_batch_hash, baseline_batch_hash, "
        "agrees_with_baseline FROM research_weather_shadow_outcome_checks "
        "WHERE event_ticker = ?",
        (event_ticker,),
    ).fetchall()
    daily = {date.fromisoformat(str(row[0])): row for row in checks if row[1] == "daily"}
    seal_rows = [row for row in checks if row[1] == "seal"]
    invalid_daily = any(
        not bool(row[4])
        or str(row[2]) != str(row[3])
        or baseline_hash is None
        or str(row[3]) != baseline_hash
        for row in daily.values()
    )
    correction_complete = bool(expected_dates) and all(
        expected in daily
        and bool(daily[expected][4])
        and str(daily[expected][2]) == baseline_hash
        and str(daily[expected][3]) == baseline_hash
        for expected in expected_dates
    )
    valid_seal = bool(seal_rows) and labeled and correction_complete and all(
        bool(row[4])
        and str(row[2]) == baseline_hash
        and str(row[3]) == baseline_hash
        for row in seal_rows
    )
    quarantined = (
        incoherent
        or conflict is not None
        or any(len(values) > 1 for values in versions.values())
        or invalid_daily
        or (bool(seal_rows) and not valid_seal)
    )
    return LabelState(
        event_ticker=event_ticker,
        labeled=labeled,
        sealed=valid_seal and not quarantined,
        quarantined=quarantined,
        outcome_batch_ids=batch_ids,
        daily_check_count=len(daily),
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _now_timestamp() -> str:
    return _timestamp(datetime.now(timezone.utc))


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite() or value < 0:
        raise ValueError("fixed-point values must be finite and nonnegative")
    return format(value, "f")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _content_hash(value: object) -> str:
    return sha256(_canonical_json(value).encode()).hexdigest()
