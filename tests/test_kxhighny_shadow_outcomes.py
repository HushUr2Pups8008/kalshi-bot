from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import pytest

import tasks.kxhighny_shadow_capture as capture_module
from tasks.kxhighny_shadow_capture import WeatherShadowCaptureTask
from tasks.kxhighny_shadow_validation import (
    WeatherShadowValidationError,
    official_high_market,
    validate_outcome_batch,
)
from tasks.weather_shadow_store import WeatherShadowStore
from weather.shadow_models import (
    Fingerprints,
    NwsDailyLabel,
    OutcomeBatch,
    OutcomeCheck,
    OutcomeRow,
    OutcomeTarget,
    RetrievedEvent,
    RetrievedMarket,
    ShadowQuote,
)

UTC = timezone.utc
EVENT_TICKER = "KXHIGHNY-26JUL12"


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def market(
    suffix: str,
    *,
    lower: int | None,
    upper: int | None,
    result: str | None,
    status: str = "settled",
    fingerprints: Fingerprints | None = None,
    quote: int = 20,
    volume: str = "50",
) -> RetrievedMarket:
    ticker = f"{EVENT_TICKER}-{suffix}"
    return RetrievedMarket(
        market_ticker=ticker,
        event_ticker=EVENT_TICKER,
        status=status,
        close_time=dt("2026-07-12T23:00:00Z"),
        lower_bound_f=lower,
        upper_bound_f=upper,
        is_lower_tail=lower is None,
        is_upper_tail=upper is None,
        fingerprints=fingerprints or Fingerprints(f"contract-{suffix}", "rules-v1", "settlement-v1"),
        yes_bid_cents=quote,
        yes_ask_cents=quote + 1,
        no_bid_cents=99 - quote,
        no_ask_cents=100 - quote,
        yes_bid_size=Decimal("10"),
        yes_ask_size=Decimal("11"),
        no_bid_size=Decimal("11"),
        no_ask_size=Decimal("10"),
        last_price_cents=quote,
        volume=Decimal(volume),
        price_retrieved_at=dt("2026-07-13T00:00:00Z"),
        raw_payload_json=_canonical(
            {
                "ticker": ticker,
                "result": result,
                "status": status,
                "yes_bid": quote,
                "volume": volume,
            }
        ),
        result=result,  # type: ignore[arg-type]
    )


def event(*, markets: tuple[RetrievedMarket, ...] | None = None) -> RetrievedEvent:
    siblings = markets or (
        market("T70", lower=None, upper=69, result="no"),
        market("B70.5", lower=70, upper=71, result="yes"),
        market("T71", lower=72, upper=None, result="no"),
    )
    return RetrievedEvent(
        event_ticker=EVENT_TICKER,
        status="settled",
        close_time=dt("2026-07-12T23:00:00Z"),
        market_tickers=tuple(item.market_ticker for item in siblings),
        markets=siblings,
        retrieved_at=dt("2026-07-13T00:02:00Z"),
    )


def cli_label(
    *,
    high: str = "70",
    target_date: date = date(2026, 7, 12),
    issued_at: datetime = dt("2026-07-13T00:01:00Z"),
    retrieved_at: datetime = dt("2026-07-13T00:03:00Z"),
    product_id: str = "CLI-KOKX-20260712",
) -> NwsDailyLabel:
    source_url = f"https://api.weather.gov/products/{product_id}"
    payload = {
        "@id": source_url,
        "id": product_id,
        "issuanceTime": issued_at.isoformat().replace("+00:00", "Z"),
        "issuingOffice": "KOKX",
        "productCode": "CLI",
        "productText": (
            "THE CENTRAL PARK NY CLIMATE SUMMARY FOR JULY 12 2026\n"
            f"MAXIMUM {high}"
        ),
    }
    raw = _canonical(payload)
    return NwsDailyLabel(
        target_date=target_date,
        station_id="KNYC",
        official_high_f=Decimal(high),
        issued_at=issued_at,
        retrieved_at=retrieved_at,
        source_url=source_url,
        product_id=product_id,
        evidence_id=sha256(raw.encode()).hexdigest(),
        raw_payload_json=raw,
    )


def captured(source: RetrievedEvent) -> dict[str, Fingerprints]:
    return {item.market_ticker: item.fingerprints for item in source.markets}


def quote(item: RetrievedMarket) -> ShadowQuote:
    return ShadowQuote(
        market_ticker=item.market_ticker,
        close_time=item.close_time,
        lower_bound_f=item.lower_bound_f,
        upper_bound_f=item.upper_bound_f,
        is_lower_tail=item.is_lower_tail,
        is_upper_tail=item.is_upper_tail,
        fingerprints=item.fingerprints,
        yes_bid_cents=item.yes_bid_cents,
        yes_ask_cents=item.yes_ask_cents,
        no_bid_cents=item.no_bid_cents,
        no_ask_cents=item.no_ask_cents,
        yes_bid_size=item.yes_bid_size,
        yes_ask_size=item.yes_ask_size,
        no_bid_size=item.no_bid_size,
        no_ask_size=item.no_ask_size,
        last_price_cents=item.last_price_cents,
        volume=item.volume,
        price_retrieved_at=item.price_retrieved_at,
        raw_payload_hash="quote-payload",
    )


def test_outcome_dtos_are_frozen() -> None:
    batch = validate_outcome_batch(event(), cli_label(), captured(event()))

    with pytest.raises(FrozenInstanceError):
        batch.rows[0].result = "no"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        batch.label_available_at = dt("2026-07-14T00:00:00Z")  # type: ignore[misc]
    check = OutcomeCheck(
        "check", EVENT_TICKER, date(2026, 7, 14), dt("2026-07-14T00:00:00Z"),
        "daily", batch.outcome_batch_id, batch.outcome_batch_id, True, "{}",
    )
    with pytest.raises(FrozenInstanceError):
        check.agrees_with_baseline = False  # type: ignore[misc]


def test_valid_outcome_batch_is_complete_one_hot_and_temporally_auditable() -> None:
    source = event()
    batch = validate_outcome_batch(source, cli_label(), captured(source))

    assert isinstance(batch, OutcomeBatch)
    assert len(batch.rows) == 3
    assert sum(row.result == "yes" for row in batch.rows) == 1
    assert {row.kalshi_status for row in batch.rows} == {"settled"}
    assert batch.settlement_observed_at == source.retrieved_at
    assert batch.label_available_at == dt("2026-07-13T00:03:00Z")
    assert all(isinstance(row, OutcomeRow) for row in batch.rows)
    assert all(row.label_available_at == batch.label_available_at for row in batch.rows)


@pytest.mark.parametrize("invalid", ["missing", "status", "enumeration", "fingerprint", "zero_yes", "wrong_yes"])
def test_outcome_validation_fails_closed_for_incomplete_or_changed_settlement(invalid: str) -> None:
    source = event()
    fingerprints = captured(source)
    siblings = list(source.markets)
    if invalid == "missing":
        siblings[0] = replace(siblings[0], result=None)
    elif invalid == "status":
        siblings[0] = replace(siblings[0], status="closed")
    elif invalid == "enumeration":
        source = replace(source, market_tickers=source.market_tickers[:-1])
    elif invalid == "fingerprint":
        fingerprints[siblings[0].market_ticker] = Fingerprints("changed", "rules-v1", "settlement-v1")
    elif invalid == "zero_yes":
        siblings = [replace(item, result="no") for item in siblings]
    else:
        siblings = [replace(item, result="yes" if index == 0 else "no") for index, item in enumerate(siblings)]
    if invalid not in {"enumeration"}:
        source = event(markets=tuple(siblings))

    with pytest.raises(WeatherShadowValidationError):
        validate_outcome_batch(source, cli_label(), fingerprints)


@pytest.mark.parametrize("invalid", ["date", "station", "source", "product", "evidence", "identity", "issued_after_retrieval", "fractional_high"])
def test_outcome_validation_rejects_invalid_cli_target_source_or_timing(invalid: str) -> None:
    source = event()
    label = cli_label()
    if invalid == "date":
        label = replace(label, target_date=date(2026, 7, 13))
    elif invalid == "station":
        label = replace(label, station_id="KLGA")
    elif invalid == "source":
        label = replace(label, source_url="https://example.com/products/CLI")
    elif invalid == "product":
        label = replace(label, product_id="../unsafe")
    elif invalid == "evidence":
        label = replace(label, evidence_id="wrong")
    elif invalid == "identity":
        payload = json.loads(label.raw_payload_json)
        payload["issuingOffice"] = "OTHER"
        raw = _canonical(payload)
        label = replace(label, raw_payload_json=raw, evidence_id=sha256(raw.encode()).hexdigest())
    elif invalid == "issued_after_retrieval":
        label = replace(label, issued_at=label.retrieved_at + timedelta(seconds=1))
    else:
        label = replace(label, official_high_f=Decimal("70.5"))

    with pytest.raises(WeatherShadowValidationError):
        validate_outcome_batch(source, label, captured(source))


def test_official_high_market_matches_exactly_one_integer_bucket() -> None:
    source = event()
    quotes = tuple(quote(item) for item in source.markets)

    assert official_high_market(Decimal("69"), quotes).endswith("-T70")
    assert official_high_market(Decimal("70"), quotes).endswith("-B70.5")
    assert official_high_market(Decimal("72"), quotes).endswith("-T71")
    with pytest.raises(WeatherShadowValidationError):
        official_high_market(Decimal("70.5"), quotes)


def test_stable_outcome_hashes_exclude_quote_and_volume_drift() -> None:
    first = event()
    drifted = event(
        markets=tuple(
            replace(
                item,
                yes_bid_cents=1,
                yes_ask_cents=2,
                no_bid_cents=98,
                no_ask_cents=99,
                volume=Decimal("9999"),
                raw_payload_json=_canonical({"quote": "drift", "ticker": item.market_ticker}),
            )
            for item in first.markets
        )
    )

    a = validate_outcome_batch(first, cli_label(), captured(first))
    b = validate_outcome_batch(drifted, cli_label(), captured(first))

    assert a.outcome_batch_id == b.outcome_batch_id
    assert [row.outcome_id for row in a.rows] == [row.outcome_id for row in b.rows]
    assert [row.source_payload_hash for row in a.rows] == [row.source_payload_hash for row in b.rows]


def test_stable_outcome_hashes_include_settlement_and_official_evidence() -> None:
    source = event()
    baseline = validate_outcome_batch(source, cli_label(), captured(source))
    changed_market = replace(source.markets[0], status="finalized")
    changed_source = event(markets=(changed_market, *source.markets[1:]))
    changed_label = cli_label(product_id="CLI-KOKX-20260712-REV2")

    settlement = validate_outcome_batch(changed_source, cli_label(), captured(changed_source))
    official = validate_outcome_batch(source, changed_label, captured(source))

    assert len({baseline.outcome_batch_id, settlement.outcome_batch_id, official.outcome_batch_id}) == 3


@pytest.mark.asyncio
async def test_any_sibling_correction_versions_every_row_and_appends_complete_batch(
    tmp_path: Path,
) -> None:
    source = event()
    changed_market = replace(
        source.markets[0],
        status="finalized",
        raw_payload_json=_canonical(
            {
                "ticker": source.markets[0].market_ticker,
                "result": source.markets[0].result,
                "status": "finalized",
            }
        ),
    )
    corrected = event(markets=(changed_market, *source.markets[1:]))
    baseline = validate_outcome_batch(source, cli_label(), captured(source))
    correction = validate_outcome_batch(corrected, cli_label(), captured(corrected))

    assert baseline.outcome_batch_id != correction.outcome_batch_id
    assert {row.outcome_id for row in baseline.rows}.isdisjoint(
        row.outcome_id for row in correction.rows
    )
    assert {row.source_payload_hash for row in baseline.rows}.isdisjoint(
        row.source_payload_hash for row in correction.rows
    )

    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    assert (await store.append_outcome_batch(baseline)).status == "inserted"
    assert (await store.append_outcome_batch(correction)).status == "conflict"
    assert (await store.append_outcome_batch(correction)).status == "identical"

    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT outcome_batch_id, COUNT(*) "
            "FROM research_weather_shadow_outcomes GROUP BY outcome_batch_id"
        ).fetchall()
    assert sorted(count for _, count in rows) == [3, 3]
    state = await store.label_state(EVENT_TICKER)
    assert len(state.outcome_batch_ids) == 2
    assert state.quarantined is True


@pytest.mark.asyncio
async def test_corrected_one_hot_yes_move_preserves_two_complete_versions(
    tmp_path: Path,
) -> None:
    baseline_event = event()
    corrected_event = event(
        markets=(
            market("T70", lower=None, upper=69, result="no"),
            market("B70.5", lower=70, upper=71, result="no"),
            market("T71", lower=72, upper=None, result="yes"),
        )
    )
    baseline = validate_outcome_batch(
        baseline_event, cli_label(), captured(baseline_event)
    )
    correction = validate_outcome_batch(
        corrected_event,
        cli_label(high="72"),
        captured(corrected_event),
    )

    store = WeatherShadowStore(db_path=tmp_path / "weather.db")
    await store.initialize()
    await store.append_outcome_batch(baseline)
    result = await store.append_outcome_batch(correction)

    assert result.status == "conflict"
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM research_weather_shadow_outcomes"
        ).fetchone() == (6,)


class LabelStore:
    def __init__(self, *, labeled: bool = False) -> None:
        self.targets = (OutcomeTarget(EVENT_TICKER, date(2026, 7, 12)),)
        self.labeled = labeled
        self.batches: list[OutcomeBatch] = []
        self.checks: list[OutcomeCheck] = []
        self.seals: list[tuple[str, datetime]] = []

    async def initialize(self) -> None:
        return None

    async def list_outcome_targets(self, now: datetime) -> tuple[OutcomeTarget, ...]:
        return self.targets

    async def capture_fingerprints(self, event_ticker: str) -> dict[str, Fingerprints]:
        assert event_ticker == EVENT_TICKER
        return captured(event())

    async def label_state(self, event_ticker: str) -> SimpleNamespace:
        return SimpleNamespace(
            labeled=self.labeled,
            sealed=False,
            quarantined=False,
            outcome_batch_ids=(() if not self.labeled else (self.baseline_id,)),
        )

    async def append_outcome_batch(self, batch: OutcomeBatch) -> SimpleNamespace:
        self.batches.append(batch)
        self.labeled = True
        self.baseline_id = batch.outcome_batch_id
        return SimpleNamespace(status="inserted")

    async def append_outcome_check(self, check: OutcomeCheck) -> SimpleNamespace:
        self.checks.append(check)
        return SimpleNamespace(status="inserted")

    async def try_seal_event(self, event_ticker: str, now: datetime) -> SimpleNamespace:
        self.seals.append((event_ticker, now))
        return SimpleNamespace(status="not_ready")


class LabelMarkets:
    def __init__(self, source: RetrievedEvent) -> None:
        self.source = source
        self.get_calls: list[str] = []

    async def get_event(self, *, event_ticker: str) -> RetrievedEvent:
        self.get_calls.append(event_ticker)
        return self.source


class LabelWeather:
    def __init__(self, label: NwsDailyLabel | None) -> None:
        self.label = label
        self.calls: list[tuple[date, str]] = []

    async def fetch_daily_label(self, *, target_date: date, station_id: str) -> NwsDailyLabel | None:
        self.calls.append((target_date, station_id))
        return self.label


class BlockingThreadStore(LabelStore):
    def __init__(self, *, labeled: bool = False) -> None:
        super().__init__(labeled=labeled)
        self.entered = {
            name: threading.Event() for name in ("outcome", "check", "seal")
        }
        self.release = {
            name: threading.Event() for name in ("outcome", "check", "seal")
        }
        self.mutations: list[str] = []

    def _commit(self, name: str, value: object) -> None:
        self.entered[name].set()
        assert self.release[name].wait(timeout=2)
        if name == "outcome":
            assert isinstance(value, OutcomeBatch)
            self.batches.append(value)
        elif name == "check":
            assert isinstance(value, OutcomeCheck)
            self.checks.append(value)
        else:
            assert isinstance(value, tuple)
            self.seals.append(value)
        self.mutations.append(name)

    async def append_outcome_batch(self, batch: OutcomeBatch) -> SimpleNamespace:
        await asyncio.to_thread(self._commit, "outcome", batch)
        return SimpleNamespace(status="inserted")

    async def append_outcome_check(self, check: OutcomeCheck) -> SimpleNamespace:
        await asyncio.to_thread(self._commit, "check", check)
        return SimpleNamespace(status="inserted")

    async def try_seal_event(self, event_ticker: str, now: datetime) -> SimpleNamespace:
        await asyncio.to_thread(self._commit, "seal", (event_ticker, now))
        return SimpleNamespace(status="not_ready")


async def wait_for_thread(event: threading.Event) -> bool:
    return await asyncio.wait_for(asyncio.to_thread(event.wait, 0.5), timeout=1)


class CaptureBomb:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"capture client used during labeling: {name}")


def label_task(store: LabelStore, markets: LabelMarkets, weather: LabelWeather) -> WeatherShadowCaptureTask:
    return WeatherShadowCaptureTask(
        store=store,  # type: ignore[arg-type]
        capture_markets=CaptureBomb(),  # type: ignore[arg-type]
        capture_weather=CaptureBomb(),  # type: ignore[arg-type]
        label_markets=markets,  # type: ignore[arg-type]
        label_weather=weather,  # type: ignore[arg-type]
        now=lambda: dt("2026-07-14T12:00:00Z"),
    )


@pytest.mark.asyncio
async def test_run_label_once_uses_only_dedicated_label_clients_and_captured_targets() -> None:
    store = LabelStore()
    markets = LabelMarkets(event())
    weather = LabelWeather(cli_label())

    await label_task(store, markets, weather).run_label_once()

    assert markets.get_calls == [EVENT_TICKER]
    assert weather.calls == [(date(2026, 7, 12), "KNYC")]
    assert len(store.batches) == 1
    assert store.checks == []


@pytest.mark.asyncio
async def test_daily_label_revalidates_public_sources_before_writing_check() -> None:
    store = LabelStore()
    markets = LabelMarkets(event())
    weather = LabelWeather(cli_label())
    task = label_task(store, markets, weather)
    await task.run_label_once()
    store.targets = (OutcomeTarget(EVENT_TICKER, date(2026, 7, 12)),)

    await task.run_label_once()

    assert markets.get_calls == [EVENT_TICKER, EVENT_TICKER]
    assert len(weather.calls) == 2
    assert len(store.batches) == 1
    assert len(store.checks) == 1
    assert store.checks[0].agrees_with_baseline is True
    assert store.seals == [(EVENT_TICKER, dt("2026-07-14T12:00:00Z"))]


@pytest.mark.asyncio
async def test_changed_daily_version_is_appended_and_quarantined_before_check() -> None:
    store = LabelStore()
    markets = LabelMarkets(event())
    weather = LabelWeather(cli_label())
    task = label_task(store, markets, weather)
    await task.run_label_once()
    weather.label = cli_label(product_id="CLI-KOKX-20260712-REV2")

    await task.run_label_once()

    assert len(store.batches) == 2
    assert store.checks[0].agrees_with_baseline is False
    assert store.checks[0].observed_batch_hash != store.checks[0].baseline_batch_hash


@pytest.mark.asyncio
async def test_missing_cli_label_writes_nothing() -> None:
    store = LabelStore()

    await label_task(store, LabelMarkets(event()), LabelWeather(None)).run_label_once()

    assert store.batches == store.checks == store.seals == []


@pytest.mark.asyncio
async def test_label_budget_is_independent_exact_and_cancels_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert capture_module.LABEL_BUILD_BUDGET_SECONDS == 20
    cancelled = asyncio.Event()

    class BlockingLabelMarkets(LabelMarkets):
        async def get_event(self, *, event_ticker: str) -> RetrievedEvent:
            self.get_calls.append(event_ticker)
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    store = LabelStore()
    monkeypatch.setattr(capture_module, "LABEL_BUILD_BUDGET_SECONDS", 0.01)

    await label_task(
        store,
        BlockingLabelMarkets(event()),
        LabelWeather(cli_label()),
    ).run_label_once()

    assert cancelled.is_set()
    assert store.batches == store.checks == store.seals == []


@pytest.mark.asyncio
async def test_label_budget_expires_before_persistence_and_cannot_leave_late_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = BlockingThreadStore()
    monkeypatch.setattr(capture_module, "LABEL_BUILD_BUDGET_SECONDS", 0.01)
    run = asyncio.create_task(
        label_task(store, LabelMarkets(event()), LabelWeather(cli_label())).run_label_once()
    )
    assert await wait_for_thread(store.entered["outcome"])

    await asyncio.sleep(0.03)
    pending_while_commit_blocked = not run.done()
    store.release["outcome"].set()
    await run

    assert pending_while_commit_blocked is True
    assert store.mutations == ["outcome"]
    snapshot = list(store.mutations)
    await asyncio.sleep(0.02)
    assert store.mutations == snapshot


@pytest.mark.asyncio
async def test_external_label_cancellation_drains_full_changed_version_plan() -> None:
    baseline_event = event()
    baseline = validate_outcome_batch(
        baseline_event, cli_label(), captured(baseline_event)
    )
    corrected_event = event(
        markets=(
            market("T70", lower=None, upper=69, result="no"),
            market("B70.5", lower=70, upper=71, result="no"),
            market("T71", lower=72, upper=None, result="yes"),
        )
    )
    store = BlockingThreadStore(labeled=True)
    store.baseline_id = baseline.outcome_batch_id
    run = asyncio.create_task(
        label_task(
            store,
            LabelMarkets(corrected_event),
            LabelWeather(cli_label(high="72")),
        ).run_label_once()
    )
    assert await wait_for_thread(store.entered["outcome"])

    run.cancel()
    await asyncio.sleep(0)
    pending_after_cancel = not run.done()
    store.release["outcome"].set()
    check_entered = await wait_for_thread(store.entered["check"])
    if check_entered:
        store.release["check"].set()
    seal_entered = await wait_for_thread(store.entered["seal"])
    if seal_entered:
        store.release["seal"].set()
    result = await asyncio.gather(run, return_exceptions=True)

    assert pending_after_cancel is True
    assert check_entered is seal_entered is True
    assert isinstance(result[0], asyncio.CancelledError)
    assert store.mutations == ["outcome", "check", "seal"]
    snapshot = list(store.mutations)
    await asyncio.sleep(0.02)
    assert store.mutations == snapshot


@pytest.mark.asyncio
async def test_run_schedules_capture_and_label_lanes_without_starvation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    capture_started = asyncio.Event()
    label_started = asyncio.Event()
    task = label_task(LabelStore(), LabelMarkets(event()), LabelWeather(cli_label()))

    async def capture_once() -> object:
        capture_started.set()
        await label_started.wait()
        return object()

    async def label_once() -> None:
        label_started.set()
        await capture_started.wait()

    async def stop_after_cycle(seconds: float) -> None:
        stop.set()

    monkeypatch.setattr(task, "run_capture_once", capture_once)
    monkeypatch.setattr(task, "run_label_once", label_once)
    task._sleep = stop_after_cycle

    await asyncio.wait_for(task.run(stop), timeout=1)

    assert capture_started.is_set()
    assert label_started.is_set()
