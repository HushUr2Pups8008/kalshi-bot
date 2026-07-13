from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

import tasks.weather_shadow_store as weather_store_module
from tasks.weather_shadow_store import WeatherShadowStore
from weather.shadow_models import CaptureBatch, Fingerprints, ShadowQuote, WeatherFeatures
from weather.shadow_models import OutcomeBatch, OutcomeCheck, OutcomeRow


UTC = timezone.utc
TABLES = {
    "research_weather_shadow_snapshots",
    "research_weather_shadow_quotes",
    "research_weather_shadow_outcomes",
    "research_weather_shadow_conflicts",
    "research_weather_shadow_outcome_checks",
}


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def quote(
    ticker: str,
    *,
    lower: int | None,
    upper: int | None,
    lower_tail: bool = False,
    upper_tail: bool = False,
) -> ShadowQuote:
    return ShadowQuote(
        market_ticker=ticker,
        close_time=dt("2026-07-13T16:00:00Z"),
        lower_bound_f=lower,
        upper_bound_f=upper,
        is_lower_tail=lower_tail,
        is_upper_tail=upper_tail,
        fingerprints=Fingerprints("contract-v1", "rules-v1", "settlement-v1"),
        yes_bid_cents=20,
        yes_ask_cents=22,
        no_bid_cents=78,
        no_ask_cents=80,
        yes_bid_size=Decimal("12.5"),
        yes_ask_size=Decimal("10"),
        no_bid_size=Decimal("8.25"),
        no_ask_size=Decimal("9"),
        last_price_cents=21,
        volume=Decimal("123.5"),
        price_retrieved_at=dt("2026-07-12T16:00:02Z"),
        raw_payload_hash=f"raw-{ticker}",
    )


def batch(*, snapshot_id: str = "snapshot-a", capture_key: str = "capture-a") -> CaptureBatch:
    features = WeatherFeatures(
        forecast_issued_at=dt("2026-07-12T12:00:00Z"),
        forecast_valid_start=dt("2026-07-13T04:00:00Z"),
        forecast_valid_end=dt("2026-07-14T04:00:00Z"),
        observation_measured_at=dt("2026-07-12T15:50:00Z"),
        observation_coverage_start=dt("2026-07-12T04:00:00Z"),
        observation_count=12,
        weather_retrieved_at=dt("2026-07-12T16:00:01Z"),
        grid_forecast_high_f=Decimal("84.5"),
        hourly_forecast_high_f=Decimal("83.7"),
        running_observed_high_f=Decimal("77.2"),
        forecast_spread_f=Decimal("0.8"),
        target_weekday=0,
        source_payload_json='{"observations":"redacted-normalized"}',
        source_payload_hash="weather-hash",
    )
    return CaptureBatch(
        snapshot_id=snapshot_id,
        capture_key=capture_key,
        event_ticker="KXHIGHNY-26JUL13",
        target_date=date(2026, 7, 13),
        capture_started_at=dt("2026-07-12T16:00:00Z"),
        capture_finished_at=dt("2026-07-12T16:00:03Z"),
        as_of=dt("2026-07-12T16:00:03Z"),
        close_time=dt("2026-07-13T16:00:00Z"),
        event_retrieved_at=dt("2026-07-12T16:00:02Z"),
        seconds_to_close=Decimal("86397"),
        horizon_bucket="T-24h",
        features=features,
        quotes_hash=f"quotes-{snapshot_id}",
        fee_schedule_version="fee-v1",
        model_version="model-v1",
        quotes=(
            quote("LOW", lower=None, upper=69, lower_tail=True),
            quote("MID", lower=70, upper=70),
            quote("HIGH", lower=71, upper=None, upper_tail=True),
        ),
    )


def rows(db_path: Path, sql: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql).fetchall()


def outcome_batch(
    *,
    version: str = "v1",
    event_ticker: str = "KXHIGHNY-26JUL13",
    results: tuple[str, ...] = ("yes", "no", "no"),
    label_available_at: datetime = dt("2026-07-14T00:01:00Z"),
) -> OutcomeBatch:
    tickers = ("LOW", "MID", "HIGH")
    items = tuple(
        OutcomeRow(
            outcome_id=f"outcome-{version}-{ticker}",
            outcome_batch_id=f"outcome-batch-{version}",
            market_ticker=ticker,
            event_ticker=event_ticker,
            expected_sibling_count=len(tickers),
            result=result,
            kalshi_status="settled",
            settlement_observed_at=dt("2026-07-13T20:00:00Z"),
            source_payload_hash=f"source-{version}-{ticker}",
            fingerprints=Fingerprints("contract-v1", "rules-v1", "settlement-v1"),
            official_high_f=Decimal("70"),
            official_evidence_id=f"evidence-{version}",
            official_source_url="https://weather.gov/example",
            official_product_id=f"CLI-{version}",
            official_issued_at=dt("2026-07-14T00:00:00Z"),
            official_retrieved_at=label_available_at,
            label_available_at=label_available_at,
        )
        for ticker, result in zip(tickers, results)
    )
    return OutcomeBatch(
        outcome_batch_id=f"outcome-batch-{version}",
        event_ticker=event_ticker,
        target_date=date(2026, 7, 13),
        settlement_observed_at=dt("2026-07-13T20:00:00Z"),
        label_available_at=label_available_at,
        rows=items,
    )


def outcome_check(
    check_date: date,
    *,
    suffix: str = "v1",
    kind: str = "daily",
    observed: str = "outcome-batch-v1",
    baseline: str = "outcome-batch-v1",
    agrees: bool = True,
    details: str = '{"source":"public"}',
) -> OutcomeCheck:
    return OutcomeCheck(
        check_id=f"check-{check_date.isoformat()}-{suffix}",
        event_ticker="KXHIGHNY-26JUL13",
        check_date_utc=check_date,
        checked_at=datetime.combine(check_date, datetime.min.time(), UTC) + timedelta(hours=12),
        check_kind=kind,
        observed_batch_hash=observed,
        baseline_batch_hash=baseline,
        agrees_with_baseline=agrees,
        details_json=details,
    )


def test_constructor_performs_zero_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "missing" / "weather.db"

    def unexpected_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("constructor connected to SQLite")

    monkeypatch.setattr(sqlite3, "connect", unexpected_connect)
    store = WeatherShadowStore(db_path=db_path)

    assert store.db_path == db_path
    assert not db_path.parent.exists()


@pytest.mark.asyncio
async def test_initialize_is_idempotent_and_applies_exact_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "weather.db"
    store = WeatherShadowStore(db_path=db_path)

    await store.initialize()
    await store.initialize()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'research_weather_shadow_%'"
            )
        }
        triggers = conn.execute(
            "SELECT tbl_name, name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
        quote_fks = conn.execute(
            "PRAGMA foreign_key_list(research_weather_shadow_quotes)"
        ).fetchall()
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            )
        }

    assert tables == TABLES
    assert len(triggers) == 10
    assert {table for table, _ in triggers} == TABLES
    assert len(quote_fks) == 1
    assert quote_fks[0][2] == "research_weather_shadow_snapshots"
    assert quote_fks[0][6].upper() == "NO ACTION"
    assert {
        "idx_weather_shadow_snapshots_event",
        "idx_weather_shadow_quotes_market",
        "idx_weather_shadow_outcomes_event",
        "idx_weather_shadow_outcomes_market",
        "idx_weather_shadow_checks_event_date",
        "idx_weather_shadow_checks_check_date",
    } <= indexes


@pytest.mark.asyncio
async def test_every_store_connection_enables_fk_wal_and_busy_timeout(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()

    conn = store._connect()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert 0 < busy_timeout < 60_000
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_constraints_foreign_keys_and_append_only_triggers_fire(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    conn = store._connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute(
                "INSERT INTO research_weather_shadow_quotes "
                "(snapshot_id, market_ticker, close_time, is_lower_tail, is_upper_tail, "
                "contract_fingerprint, rules_source_fingerprint, settlement_source_fingerprint, "
                "yes_bid_cents, yes_ask_cents, no_bid_cents, no_ask_cents, yes_bid_size_fp, "
                "yes_ask_size_fp, no_bid_size_fp, no_ask_size_fp, price_retrieved_at, raw_payload_hash) "
                "VALUES ('missing', 'M', '2026-01-01T00:00:00Z', 0, 0, 'c', 'r', 's', "
                "1, 2, 98, 99, '1', '1', '1', '1', '2026-01-01T00:00:00Z', 'h')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO research_weather_shadow_outcomes "
                "(outcome_id, outcome_batch_id, market_ticker, event_ticker, expected_sibling_count, "
                "result, kalshi_status, settlement_observed_at, source_payload_hash, contract_fingerprint, "
                "rules_source_fingerprint, settlement_source_fingerprint, official_high_f, official_evidence_id, "
                "official_source_url, official_product_id, official_issued_at, official_retrieved_at, "
                "label_available_at, created_ts) VALUES "
                "('o', 'b', 'm', 'e', 1, 'maybe', 'open', 't', 'h', 'c', 'r', 's', 1, 'e', 'u', 'p', 't', 't', 't', 't')"
            )
    finally:
        conn.close()

    await store.append_capture(batch())
    conn = store._connect()
    try:
        populated = {
            "research_weather_shadow_snapshots": "snapshot_id",
            "research_weather_shadow_quotes": "market_ticker",
        }
        for table, column in populated.items():
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(f"UPDATE {table} SET {column} = {column}")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(f"DELETE FROM {table}")
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_snapshot_and_complete_ladder_commit_atomically(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()

    result = await store.append_capture(batch())

    assert result.status == "inserted"
    assert rows(store.db_path, "SELECT snapshot_id FROM research_weather_shadow_snapshots") == [
        ("snapshot-a",)
    ]
    assert len(rows(store.db_path, "SELECT * FROM research_weather_shadow_quotes")) == 3
    state = await store.capture_key_state("capture-a")
    assert state.claimed is True
    assert state.snapshot_id == "snapshot-a"


@pytest.mark.asyncio
async def test_mid_ladder_failure_rolls_back_snapshot_and_quotes(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    invalid_quote = replace(
        batch().quotes[1],
        yes_bid_cents=-1,
        yes_ask_cents=0,
        no_bid_cents=100,
        no_ask_cents=101,
    )
    invalid = replace(
        batch(),
        quotes=(batch().quotes[0], invalid_quote, batch().quotes[2]),
    )

    with pytest.raises(sqlite3.IntegrityError):
        await store.append_capture(invalid)

    assert rows(store.db_path, "SELECT * FROM research_weather_shadow_snapshots") == []
    assert rows(store.db_path, "SELECT * FROM research_weather_shadow_quotes") == []


@pytest.mark.asyncio
async def test_identical_retry_is_idempotent(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()

    first = await store.append_capture(batch())
    retry = await store.append_capture(batch())

    assert (first.status, retry.status) == ("inserted", "identical")
    assert len(rows(store.db_path, "SELECT * FROM research_weather_shadow_snapshots")) == 1
    assert len(rows(store.db_path, "SELECT * FROM research_weather_shadow_quotes")) == 3
    assert rows(store.db_path, "SELECT * FROM research_weather_shadow_conflicts") == []


@pytest.mark.asyncio
async def test_nonidentical_retry_is_append_only_and_sanitized(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    incoming = replace(
        batch(snapshot_id="snapshot-b"),
        features=replace(
            batch().features,
            source_payload_json='{"api_key":"SECRET","raw_response":"PRIVATE"}',
            source_payload_hash="different-weather-hash",
        ),
    )

    await store.append_capture(batch())
    first = await store.append_capture(incoming)
    second = await store.append_capture(incoming)

    assert first.status == second.status == "conflict"
    assert rows(store.db_path, "SELECT snapshot_id FROM research_weather_shadow_snapshots") == [
        ("snapshot-a",)
    ]
    conflict_rows = rows(
        store.db_path,
        "SELECT conflict_id, details_json FROM research_weather_shadow_conflicts",
    )
    assert len(conflict_rows) == 1
    detail = conflict_rows[0][1]
    assert detail == json.dumps(json.loads(detail), sort_keys=True, separators=(",", ":"))
    assert "SECRET" not in detail
    assert "PRIVATE" not in detail
    assert "api_key" not in detail


@pytest.mark.asyncio
async def test_two_concurrent_claimants_preserve_first_complete_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "weather.db"
    first_store = WeatherShadowStore(db_path=db_path)
    second_store = WeatherShadowStore(db_path=db_path)
    await first_store.initialize()
    contender = batch(snapshot_id="snapshot-b")

    results = await asyncio.gather(
        first_store.append_capture(batch()),
        second_store.append_capture(contender),
    )

    assert sorted(result.status for result in results) == ["conflict", "inserted"]
    winner = rows(db_path, "SELECT snapshot_id FROM research_weather_shadow_snapshots")
    assert winner in [[("snapshot-a",)], [("snapshot-b",)]]
    assert len(rows(db_path, "SELECT * FROM research_weather_shadow_quotes")) == 3
    assert len(rows(db_path, "SELECT * FROM research_weather_shadow_conflicts")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quotes",
    [
        (),
        (
            batch().quotes[0],
            replace(batch().quotes[2], lower_bound_f=70),
        ),
    ],
)
async def test_empty_or_partial_ladder_cannot_commit(
    tmp_path: Path, quotes: tuple[ShadowQuote, ...]
) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()

    with pytest.raises(ValueError, match="complete quote ladder"):
        await store.append_capture(replace(batch(), quotes=quotes))

    assert rows(store.db_path, "SELECT * FROM research_weather_shadow_snapshots") == []
    assert rows(store.db_path, "SELECT * FROM research_weather_shadow_quotes") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    ["crossed", "complement", "quote_close", "seconds_to_close"],
)
async def test_invalid_capture_inputs_cannot_claim_key(tmp_path: Path, invalid: str) -> None:
    source = batch()
    if invalid == "crossed":
        source = replace(
            source,
            quotes=(replace(source.quotes[0], yes_bid_cents=23), *source.quotes[1:]),
        )
    elif invalid == "complement":
        source = replace(
            source,
            quotes=(replace(source.quotes[0], no_ask_cents=79), *source.quotes[1:]),
        )
    elif invalid == "quote_close":
        source = replace(
            source,
            quotes=(
                replace(source.quotes[0], close_time=source.close_time + timedelta(seconds=1)),
                *source.quotes[1:],
            ),
        )
    else:
        source = replace(source, seconds_to_close=Decimal("1"))
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()

    with pytest.raises(ValueError, match="capture batch"):
        await store.append_capture(source)

    assert rows(store.db_path, "SELECT * FROM research_weather_shadow_snapshots") == []
    assert rows(store.db_path, "SELECT * FROM research_weather_shadow_quotes") == []


@pytest.mark.asyncio
async def test_capture_fingerprints_return_stable_values_and_reject_drift(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    await store.append_capture(batch())

    assert await store.capture_fingerprints("KXHIGHNY-26JUL13") == {
        ticker: Fingerprints("contract-v1", "rules-v1", "settlement-v1")
        for ticker in ("HIGH", "LOW", "MID")
    }
    drifted = replace(
        batch(snapshot_id="snapshot-b", capture_key="capture-b"),
        quotes=tuple(
            replace(
                item,
                fingerprints=Fingerprints("contract-v2", "rules-v1", "settlement-v1"),
            )
            if item.market_ticker == "MID"
            else item
            for item in batch().quotes
        ),
    )
    await store.append_capture(drifted)

    with pytest.raises(ValueError, match="fingerprints disagree"):
        await store.capture_fingerprints("KXHIGHNY-26JUL13")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    ["empty", "count", "duplicate", "event", "batch", "zero_yes", "two_yes"],
)
async def test_incoherent_outcome_batch_fails_before_insert(tmp_path: Path, invalid: str) -> None:
    source = outcome_batch()
    if invalid == "empty":
        source = replace(source, rows=())
    elif invalid == "count":
        source = replace(source, rows=source.rows[:2])
    elif invalid == "duplicate":
        source = replace(source, rows=(*source.rows[:2], replace(source.rows[2], market_ticker="MID")))
    elif invalid == "event":
        source = replace(source, rows=(*source.rows[:2], replace(source.rows[2], event_ticker="OTHER")))
    elif invalid == "batch":
        source = replace(
            source,
            rows=(*source.rows[:2], replace(source.rows[2], outcome_batch_id="other-batch")),
        )
    elif invalid == "zero_yes":
        source = outcome_batch(results=("no", "no", "no"))
    else:
        source = outcome_batch(results=("yes", "yes", "no"))
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()

    with pytest.raises(ValueError, match="complete outcome ladder"):
        await store.append_outcome_batch(source)

    assert rows(store.db_path, "SELECT * FROM research_weather_shadow_outcomes") == []


@pytest.mark.asyncio
async def test_outcome_batch_is_atomic_idempotent_and_reopen_safe(tmp_path: Path) -> None:
    db_path = tmp_path / "weather.db"
    first_store = WeatherShadowStore(db_path=db_path)
    second_store = WeatherShadowStore(db_path=db_path)
    await first_store.initialize()

    first, retry = await asyncio.gather(
        first_store.append_outcome_batch(outcome_batch()),
        second_store.append_outcome_batch(outcome_batch()),
    )

    assert sorted((first.status, retry.status)) == ["identical", "inserted"]
    assert len(rows(db_path, "SELECT * FROM research_weather_shadow_outcomes")) == 3
    reopened = WeatherShadowStore(db_path=db_path)
    assert (await reopened.append_outcome_batch(outcome_batch())).status == "identical"
    state = await reopened.label_state("KXHIGHNY-26JUL13")
    assert state.labeled is True
    assert state.sealed is False
    assert state.quarantined is False


@pytest.mark.asyncio
async def test_outcome_retry_compares_full_immutable_content(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    await store.append_outcome_batch(outcome_batch())
    changed = replace(
        outcome_batch(),
        rows=(
            replace(outcome_batch().rows[0], official_evidence_id="credential-SECRET-response-RAW"),
            *outcome_batch().rows[1:],
        ),
    )

    first = await store.append_outcome_batch(changed)
    second = await store.append_outcome_batch(changed)

    assert first.status == second.status == "conflict"
    audit = rows(
        store.db_path,
        "SELECT conflict_id, entity_type, entity_key, details_json "
        "FROM research_weather_shadow_conflicts",
    )
    assert len(audit) == 1
    assert audit[0][1:3] == ("outcome", "KXHIGHNY-26JUL13")
    assert "SECRET" not in str(audit[0][3])
    assert "RAW" not in str(audit[0][3])
    assert len(rows(store.db_path, "SELECT * FROM research_weather_shadow_outcomes")) == 3
    assert (await store.label_state("KXHIGHNY-26JUL13")).quarantined is True


@pytest.mark.asyncio
async def test_distinct_outcome_version_is_append_only_conflict(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    await store.append_outcome_batch(outcome_batch())

    result = await store.append_outcome_batch(outcome_batch(version="v2"))

    assert result.status == "conflict"
    assert len(rows(store.db_path, "SELECT * FROM research_weather_shadow_outcomes")) == 6
    state = await store.label_state("KXHIGHNY-26JUL13")
    assert state.labeled is True
    assert state.quarantined is True


@pytest.mark.asyncio
async def test_outcome_mid_batch_failure_rolls_back_every_row(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    source = outcome_batch()
    source = replace(
        source,
        rows=(source.rows[0], replace(source.rows[1], kalshi_status="open"), source.rows[2]),
    )

    with pytest.raises(sqlite3.IntegrityError):
        await store.append_outcome_batch(source)

    assert rows(store.db_path, "SELECT * FROM research_weather_shadow_outcomes") == []


@pytest.mark.asyncio
async def test_append_outcome_check_rejects_caller_supplied_seal(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()

    with pytest.raises(ValueError, match="daily") as error:
        await store.append_outcome_check(outcome_check(date(2026, 7, 15), kind="seal"))

    assert type(error.value).__name__ == "OutcomeCheckKindError"
    assert rows(store.db_path, "SELECT * FROM research_weather_shadow_outcome_checks") == []


@pytest.mark.asyncio
async def test_daily_check_retry_compares_full_content_and_audits_conflict(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    await store.append_outcome_batch(outcome_batch())
    original = outcome_check(date(2026, 7, 15))

    first = await store.append_outcome_check(original)
    retry = await store.append_outcome_check(original)
    changed = replace(
        original,
        check_id="check-conflicting-credential-SECRET",
        details_json='{"raw_response":"PRIVATE"}',
    )
    conflict = await store.append_outcome_check(changed)
    repeated = await store.append_outcome_check(changed)

    assert (first.status, retry.status, conflict.status, repeated.status) == (
        "inserted",
        "identical",
        "conflict",
        "conflict",
    )
    audit = rows(
        store.db_path,
        "SELECT entity_type, entity_key, details_json FROM research_weather_shadow_conflicts",
    )
    assert len(audit) == 1
    assert audit[0][:2] == (
        "outcome",
        "outcome_check:KXHIGHNY-26JUL13:2026-07-15:daily",
    )
    assert "SECRET" not in str(audit[0][2])
    assert "PRIVATE" not in str(audit[0][2])
    assert len(rows(store.db_path, "SELECT * FROM research_weather_shadow_outcome_checks")) == 1
    assert (await store.label_state("KXHIGHNY-26JUL13")).quarantined is True


@pytest.mark.asyncio
async def test_list_outcome_targets_persists_missed_day_quarantine(tmp_path: Path) -> None:
    db_path = tmp_path / "weather.db"
    store = WeatherShadowStore(db_path=db_path)
    await store.initialize()
    await store.append_capture(batch())
    assert await store.list_outcome_targets(dt("2026-07-13T20:00:00Z")) == (
        outcome_batch_target(),
    )
    await store.append_outcome_batch(outcome_batch())
    await store.append_outcome_check(outcome_check(date(2026, 7, 15)))

    before_deadline = await store.list_outcome_targets(dt("2026-07-16T23:59:59Z"))
    assert before_deadline == (outcome_batch_target(),)
    assert (await store.label_state("KXHIGHNY-26JUL13")).quarantined is False
    after_deadline = await store.list_outcome_targets(dt("2026-07-17T00:00:01Z"))

    assert after_deadline == (outcome_batch_target(),)
    reopened = WeatherShadowStore(db_path=db_path)
    state = await reopened.label_state("KXHIGHNY-26JUL13")
    assert state.labeled is True
    assert state.sealed is False
    assert state.quarantined is True
    assert await reopened.list_outcome_targets(dt("2026-08-01T00:00:00Z")) == (
        outcome_batch_target(),
    )
    conflicts = rows(
        db_path,
        "SELECT entity_key FROM research_weather_shadow_conflicts ORDER BY entity_key",
    )
    assert ("outcome_check:KXHIGHNY-26JUL13:2026-07-16:daily",) in conflicts


def outcome_batch_target() -> object:
    from weather.shadow_models import OutcomeTarget

    return OutcomeTarget("KXHIGHNY-26JUL13", date(2026, 7, 13))


@pytest.mark.asyncio
async def test_seal_requires_exact_seven_expected_agreeing_days_and_survives_reopen(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "weather.db"
    store = WeatherShadowStore(db_path=db_path)
    await store.initialize()
    await store.append_capture(batch())
    await store.append_outcome_batch(outcome_batch())
    for offset in range(1, 8):
        await store.append_outcome_check(outcome_check(date(2026, 7, 14) + timedelta(days=offset)))

    sealed = await store.try_seal_event("KXHIGHNY-26JUL13", dt("2026-07-21T20:00:00Z"))

    assert sealed.status == "sealed"
    state = await store.label_state("KXHIGHNY-26JUL13")
    assert (state.labeled, state.sealed, state.quarantined) == (True, True, False)
    assert await store.list_outcome_targets(dt("2026-07-22T00:00:00Z")) == ()
    reopened = WeatherShadowStore(db_path=db_path)
    assert (await reopened.try_seal_event("KXHIGHNY-26JUL13", dt("2026-07-22T00:00:00Z"))).status == "already_sealed"


@pytest.mark.asyncio
async def test_seal_rejects_wrong_dates_or_hashes(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    await store.append_outcome_batch(outcome_batch())
    for offset in (1, 2, 3, 4, 5, 6, 8):
        await store.append_outcome_check(outcome_check(date(2026, 7, 14) + timedelta(days=offset)))

    assert (
        await store.try_seal_event("KXHIGHNY-26JUL13", dt("2026-07-22T20:00:00Z"))
    ).status == "quarantined"


@pytest.mark.asyncio
async def test_legacy_incoherent_or_orphan_seal_state_is_quarantined(tmp_path: Path) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    conn = store._connect()
    try:
        conn.execute(
            "INSERT INTO research_weather_shadow_outcome_checks "
            "(check_id,event_ticker,check_date_utc,checked_at,check_kind,observed_batch_hash,"
            "baseline_batch_hash,agrees_with_baseline,details_json,created_ts) "
            "VALUES ('legacy-seal','KXHIGHNY-26JUL13','2026-07-21','2026-07-21T00:00:00Z',"
            "'seal','x','x',1,'{}','2026-07-21T00:00:00Z')"
        )
    finally:
        conn.close()

    state = await store.label_state("KXHIGHNY-26JUL13")
    assert state.labeled is False
    assert state.sealed is False
    assert state.quarantined is True


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", ["partial", "zero_yes"])
async def test_legacy_incoherent_outcome_batch_is_quarantined(
    tmp_path: Path, legacy: str
) -> None:
    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    source = outcome_batch(results=("no", "no", "no"))
    legacy_rows = source.rows[:2] if legacy == "partial" else source.rows
    conn = store._connect()
    try:
        for item in legacy_rows:
            weather_store_module._insert_outcome(conn, item)
    finally:
        conn.close()

    state = await store.label_state("KXHIGHNY-26JUL13")
    assert state.labeled is False
    assert state.sealed is False
    assert state.quarantined is True


def test_public_surface_has_no_generic_query_api() -> None:
    public = {name for name in dir(WeatherShadowStore) if not name.startswith("_")}
    assert public == {
        "append_capture",
        "append_outcome_batch",
        "append_outcome_check",
        "capture_fingerprints",
        "capture_key_state",
        "initialize",
        "label_state",
        "list_outcome_targets",
        "try_seal_event",
    }
