from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

import pytest

from tasks.kxhighny_shadow_validation import (
    CaptureTimingError,
    LadderValidationError,
    TickerValidationError,
    WeatherInputError,
    build_capture_batch,
    celsius_to_fahrenheit,
    derive_weather_features,
    normalize_complete_ladder,
    parse_event_target_date,
    select_due_horizon,
    validate_capture_timing,
    validate_one_hot_ladder,
)
from weather.shadow_models import (
    WEATHER_SHADOW_CAPTURE_MODEL_VERSION,
    WEATHER_SHADOW_FEE_SCHEDULE_RECORD,
    WEATHER_SHADOW_FEE_SCHEDULE_VERSION,
    Fingerprints,
    NwsCapturePayloads,
    NwsGridForecast,
    NwsHourlyForecast,
    NwsObservation,
    RetrievedEvent,
    RetrievedMarket,
)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def payloads(*, target: date = date(2026, 7, 12)) -> NwsCapturePayloads:
    issued = datetime.combine(target - timedelta(days=1), time(12), UTC)
    return NwsCapturePayloads(
        grid=(
            NwsGridForecast(
                valid_start=datetime.combine(target, datetime.min.time(), NY) - timedelta(hours=2),
                valid_end=datetime.combine(target, datetime.min.time(), NY) + timedelta(hours=8),
                issued_at=issued,
                temperature_c=Decimal("27.25"),
                source_id="grid-v1",
            ),
            NwsGridForecast(
                valid_start=datetime.combine(target, datetime.min.time(), NY) + timedelta(hours=8),
                valid_end=datetime.combine(target, datetime.min.time(), NY) + timedelta(hours=18),
                issued_at=issued,
                temperature_c=Decimal("30.25"),
                source_id="grid-v1",
            ),
        ),
        hourly=(
            NwsHourlyForecast(
                start_time=datetime.combine(target, datetime.min.time(), NY) + timedelta(hours=9),
                issued_at=issued,
                temperature_c=Decimal("28.25"),
                source_id="grid-v1",
            ),
            NwsHourlyForecast(
                start_time=datetime.combine(target, datetime.min.time(), NY) + timedelta(hours=15),
                issued_at=issued,
                temperature_c=Decimal("29.25"),
                source_id="grid-v1",
            ),
        ),
        observations=(
            NwsObservation(
                station_id="KNYC",
                measured_at=datetime.combine(target, datetime.min.time(), NY) + timedelta(hours=5),
                temperature_c=Decimal("20.25"),
                source_id="obs-1",
            ),
            NwsObservation(
                station_id="KNYC",
                measured_at=datetime.combine(target, datetime.min.time(), NY) + timedelta(hours=11),
                temperature_c=Decimal("25.25"),
                source_id="obs-2",
            ),
        ),
        retrieved_at=dt("2026-07-12T17:30:00Z"),
        grid_payload_json='{"source":"grid"}',
        hourly_payload_json='{"source":"hourly"}',
        observations_payload_json='{"source":"KNYC"}',
    )


def market(
    ticker: str,
    *,
    lower: int | None,
    upper: int | None,
    lower_tail: bool = False,
    upper_tail: bool = False,
    close: datetime = dt("2026-07-12T23:30:00Z"),
) -> RetrievedMarket:
    return RetrievedMarket(
        market_ticker=ticker,
        event_ticker="KXHIGHNY-26JUL12",
        status="open",
        close_time=close,
        lower_bound_f=lower,
        upper_bound_f=upper,
        is_lower_tail=lower_tail,
        is_upper_tail=upper_tail,
        fingerprints=Fingerprints("contract-v1", "rules-v1", "settlement-v1"),
        yes_bid_cents=30,
        yes_ask_cents=32,
        no_bid_cents=68,
        no_ask_cents=70,
        yes_bid_size=Decimal("10.250"),
        yes_ask_size=Decimal("11.500"),
        no_bid_size=Decimal("11.500"),
        no_ask_size=Decimal("10.250"),
        last_price_cents=31,
        volume=Decimal("101.125"),
        price_retrieved_at=dt("2026-07-12T17:30:05Z"),
        raw_payload_json=json.dumps({"ticker": ticker}, sort_keys=True),
    )


def event() -> RetrievedEvent:
    markets = (
        market("KXHIGHNY-26JUL12-T69", lower=None, upper=69, lower_tail=True),
        market("KXHIGHNY-26JUL12-B70", lower=70, upper=70),
        market("KXHIGHNY-26JUL12-B71", lower=71, upper=71),
        market("KXHIGHNY-26JUL12-T72", lower=72, upper=None, upper_tail=True),
    )
    return RetrievedEvent(
        event_ticker="KXHIGHNY-26JUL12",
        status="open",
        close_time=dt("2026-07-12T23:30:00Z"),
        market_tickers=tuple(item.market_ticker for item in markets),
        markets=markets,
        retrieved_at=dt("2026-07-12T17:30:05Z"),
    )


@pytest.mark.parametrize(
    ("ticker", "expected"),
    [
        ("KXHIGHNY-26MAR08", date(2026, 3, 8)),
        ("KXHIGHNY-26NOV01", date(2026, 11, 1)),
        ("KXHIGHNY-27JAN31", date(2027, 1, 31)),
    ],
)
def test_parse_event_target_date_real_tickers_and_dst_dates(ticker: str, expected: date) -> None:
    assert parse_event_target_date(ticker) == expected


@pytest.mark.parametrize(
    "ticker",
    ["KXHIGHNY-26FEB30", "KXHIGHNY-26XYZ01", "kxhighny-26JUL12", "KXHIGHNY-2026JUL12", "KXHIGHNY-26JUL12-B80"],
)
def test_parse_event_target_date_fails_closed(ticker: str) -> None:
    with pytest.raises(TickerValidationError):
        parse_event_target_date(ticker)


def test_select_due_horizon_uses_closed_fifteen_minute_windows_once() -> None:
    close = dt("2026-07-13T16:00:00Z")
    assert select_due_horizon(close - timedelta(hours=24), close, frozenset()) == "T-24h"
    assert select_due_horizon(close - timedelta(hours=24) + timedelta(minutes=15), close, frozenset()) == "T-24h"
    assert select_due_horizon(close - timedelta(hours=24, minutes=1), close, frozenset()) is None
    assert select_due_horizon(close - timedelta(hours=23, minutes=44, seconds=59), close, frozenset()) is None
    assert select_due_horizon(close - timedelta(hours=12), close, {"T-24h"}) == "T-12h"
    assert select_due_horizon(close - timedelta(hours=12), close, {"T-12h"}) is None
    assert select_due_horizon(close, close, frozenset()) is None
    assert select_due_horizon(close + timedelta(seconds=1), close, frozenset()) is None


def test_select_due_horizon_uses_elapsed_time_across_new_york_dst() -> None:
    now = datetime(2026, 3, 8, 1, 30, tzinfo=NY)
    close = datetime(2026, 3, 8, 3, 30, tzinfo=NY)
    assert select_due_horizon(now, close, frozenset()) == "T-1h"


def test_celsius_conversion_is_decimal_half_up() -> None:
    assert celsius_to_fahrenheit(Decimal("0")) == Decimal("32.0")
    assert celsius_to_fahrenheit(Decimal("27.25")) == Decimal("81.1")
    assert celsius_to_fahrenheit(Decimal("-17.75")) == Decimal("0.1")


def test_weather_features_are_dst_aware_deterministic_and_canonical() -> None:
    features = derive_weather_features(payloads(), date(2026, 7, 12), dt("2026-07-12T17:30:00Z"))
    assert features.grid_forecast_high_f == Decimal("86.5")
    assert features.hourly_forecast_high_f == Decimal("84.7")
    assert features.running_observed_high_f == Decimal("77.5")
    assert features.forecast_spread_f == Decimal("1.8")
    assert features.observation_count == 2
    assert features.target_weekday == 6
    assert json.dumps(json.loads(features.source_payload_json), sort_keys=True, separators=(",", ":")) == features.source_payload_json
    assert features.source_payload_hash == sha256(features.source_payload_json.encode()).hexdigest()


def test_weather_features_use_new_york_civil_day_across_dst() -> None:
    target = date(2026, 3, 8)
    source = payloads(target=target)
    source = replace(source, retrieved_at=dt("2026-03-08T18:00:00Z"))
    features = derive_weather_features(source, target, dt("2026-03-08T18:00:00Z"))
    assert features.target_weekday == 6
    assert features.observation_count == 2


@pytest.mark.parametrize("failure", ["missing", "ambiguous", "revision", "future_issue", "future_measure", "qc", "station"])
def test_weather_inputs_fail_closed(failure: str) -> None:
    source = payloads()
    if failure == "missing":
        source = replace(source, hourly=())
    elif failure == "ambiguous":
        source = replace(source, hourly=source.hourly + (replace(source.hourly[0], temperature_c=Decimal("31")),))
    elif failure == "revision":
        source = replace(
            source,
            grid=source.grid + (replace(source.grid[0], issued_at=dt("2026-07-11T13:00:00Z"), source_id=None),),
        )
    elif failure == "future_issue":
        source = replace(source, grid=(replace(source.grid[0], issued_at=dt("2026-07-13T00:00:00Z")),) + source.grid[1:])
    elif failure == "future_measure":
        source = replace(source, observations=source.observations + (replace(source.observations[0], measured_at=dt("2026-07-12T18:00:00Z")),))
    elif failure == "qc":
        source = replace(source, observations=(replace(source.observations[0], qc_passed=False),) + source.observations[1:])
    else:
        source = replace(source, observations=tuple(replace(item, station_id="KLGA") for item in source.observations))
    with pytest.raises(WeatherInputError):
        derive_weather_features(source, date(2026, 7, 12), dt("2026-07-12T17:30:00Z"))


def test_normalize_complete_ladder_preserves_fixed_point_values_and_partition() -> None:
    quotes = normalize_complete_ladder(event(), date(2026, 7, 12))
    assert [quote.market_ticker for quote in quotes] == [
        "KXHIGHNY-26JUL12-T69",
        "KXHIGHNY-26JUL12-B70",
        "KXHIGHNY-26JUL12-B71",
        "KXHIGHNY-26JUL12-T72",
    ]
    assert quotes[0].yes_bid_size == Decimal("10.250")
    validate_one_hot_ladder(quotes)


def test_ladder_allows_contract_specific_fingerprints_with_common_sources() -> None:
    source = event()
    markets = tuple(
        replace(
            item,
            fingerprints=replace(
                item.fingerprints,
                contract=f"contract-{index}",
                rules_source=f"rules-{index}",
            ),
        )
        for index, item in enumerate(source.markets)
    )
    quotes = normalize_complete_ladder(replace(source, markets=markets), date(2026, 7, 12))
    assert len({quote.fingerprints.contract for quote in quotes}) == len(quotes)
    assert len({quote.fingerprints.rules_source for quote in quotes}) == len(quotes)


@pytest.mark.parametrize("failure", ["enumeration", "gap", "tails", "status", "close", "settlement_fingerprint", "cents", "crossed", "complement", "size"])
def test_ladder_fails_closed(failure: str) -> None:
    source = event()
    markets = list(source.markets)
    if failure == "enumeration":
        source = replace(source, market_tickers=source.market_tickers + ("KXHIGHNY-26JUL12-B99",))
    elif failure == "gap":
        markets[2] = replace(markets[2], lower_bound_f=72, upper_bound_f=72)
    elif failure == "tails":
        markets[0] = replace(markets[0], is_lower_tail=False)
    elif failure == "status":
        markets[1] = replace(markets[1], status="closed")
    elif failure == "close":
        markets[1] = replace(markets[1], close_time=markets[1].close_time + timedelta(seconds=1))
    elif failure == "settlement_fingerprint":
        markets[1] = replace(
            markets[1],
            fingerprints=replace(
                markets[1].fingerprints,
                settlement_source="settlement-v2",
            ),
        )
    elif failure == "cents":
        markets[1] = replace(markets[1], yes_ask_cents=101)
    elif failure == "crossed":
        markets[1] = replace(markets[1], yes_bid_cents=40, yes_ask_cents=32, no_ask_cents=60)
    elif failure == "complement":
        markets[1] = replace(markets[1], no_ask_cents=69)
    else:
        markets[1] = replace(markets[1], yes_bid_size=Decimal("NaN"))
    source = replace(source, markets=tuple(markets))
    with pytest.raises(LadderValidationError):
        normalize_complete_ladder(source, date(2026, 7, 12))


def build_batch(**changes: object):
    arguments = {
        "event": event(),
        "target_date": date(2026, 7, 12),
        "horizon_bucket": "T-6h",
        "features": derive_weather_features(payloads(), date(2026, 7, 12), dt("2026-07-12T17:30:06Z")),
        "capture_started_at": dt("2026-07-12T17:29:58Z"),
        "capture_finished_at": dt("2026-07-12T17:30:06Z"),
        "as_of": dt("2026-07-12T17:30:06Z"),
    }
    arguments.update(changes)
    return build_capture_batch(**arguments)


def test_capture_batch_has_canonical_hashes_and_version_sensitive_identities() -> None:
    batch = build_batch()
    assert batch.model_version == WEATHER_SHADOW_CAPTURE_MODEL_VERSION
    assert batch.fee_schedule_version == WEATHER_SHADOW_FEE_SCHEDULE_VERSION
    assert len(batch.snapshot_id) == len(batch.capture_key) == len(batch.quotes_hash) == 64
    model_changed = build_batch(model_version="kxhighny-fixed-horizon-v2")
    fee_changed = build_batch(fee_schedule_version="fee-v2")
    assert model_changed.capture_key != batch.capture_key
    assert model_changed.snapshot_id != batch.snapshot_id
    assert fee_changed.capture_key == batch.capture_key
    assert fee_changed.snapshot_id != batch.snapshot_id
    assert fee_changed.quotes_hash != batch.quotes_hash


def test_event_retrieval_time_is_part_of_quote_and_snapshot_identity() -> None:
    original = build_batch()
    changed_event = replace(event(), retrieved_at=event().retrieved_at + timedelta(seconds=1))
    changed = build_batch(event=changed_event)
    assert changed.quotes_hash != original.quotes_hash
    assert changed.snapshot_id != original.snapshot_id


def test_build_capture_rejects_a_bucket_outside_its_due_window() -> None:
    with pytest.raises(CaptureTimingError):
        build_batch(horizon_bucket="T-12h")


def test_event_retrieval_cannot_predate_a_quote() -> None:
    source = replace(event(), retrieved_at=event().retrieved_at - timedelta(seconds=1))
    with pytest.raises(LadderValidationError):
        normalize_complete_ladder(source, date(2026, 7, 12))


@pytest.mark.parametrize(
    "failure",
    [
        "as_of_before_finish",
        "weather_before_start",
        "weather_after_quote",
        "event_before_start",
        "event_after_as_of",
        "quote_before_start",
        "quote_after_as_of",
        "future_feature",
        "at_close",
        "sweep",
        "target",
    ],
)
def test_capture_timing_fails_closed(failure: str) -> None:
    batch = build_batch()
    if failure == "as_of_before_finish":
        batch = replace(batch, as_of=batch.capture_finished_at - timedelta(seconds=1))
    elif failure == "weather_before_start":
        batch = replace(
            batch,
            features=replace(batch.features, weather_retrieved_at=batch.capture_started_at - timedelta(microseconds=1)),
        )
    elif failure == "weather_after_quote":
        batch = replace(batch, features=replace(batch.features, weather_retrieved_at=dt("2026-07-12T17:31:00Z")))
    elif failure == "event_before_start":
        batch = replace(batch, event_retrieved_at=batch.capture_started_at - timedelta(microseconds=1))
    elif failure == "event_after_as_of":
        batch = replace(batch, event_retrieved_at=batch.as_of + timedelta(microseconds=1))
    elif failure == "quote_before_start":
        quote = replace(batch.quotes[0], price_retrieved_at=batch.capture_started_at - timedelta(microseconds=1))
        batch = replace(batch, quotes=(quote, *batch.quotes[1:]))
    elif failure == "quote_after_as_of":
        quote = replace(batch.quotes[0], price_retrieved_at=batch.as_of + timedelta(microseconds=1))
        batch = replace(batch, quotes=(quote, *batch.quotes[1:]))
    elif failure == "future_feature":
        batch = replace(batch, features=replace(batch.features, forecast_issued_at=dt("2026-07-12T17:31:00Z")))
    elif failure == "at_close":
        batch = replace(batch, as_of=batch.close_time)
    elif failure == "sweep":
        batch = replace(batch, capture_finished_at=batch.capture_started_at + timedelta(seconds=11))
    else:
        batch = replace(batch, target_date=date(2026, 7, 13))
    with pytest.raises(CaptureTimingError):
        validate_capture_timing(batch, timedelta(seconds=10))


def test_capture_retrieval_window_accepts_inclusive_boundaries() -> None:
    batch = build_batch()
    quotes = tuple(replace(quote, price_retrieved_at=batch.as_of) for quote in batch.quotes)
    boundary_batch = replace(
        batch,
        features=replace(batch.features, weather_retrieved_at=batch.capture_started_at),
        event_retrieved_at=batch.as_of,
        quotes=quotes,
    )
    validate_capture_timing(boundary_batch, timedelta(seconds=10))


def test_capture_identity_is_invariant_to_api_enumeration_order() -> None:
    original_event = event()
    reversed_event = replace(
        original_event,
        market_tickers=tuple(reversed(original_event.market_tickers)),
        markets=tuple(reversed(original_event.markets)),
    )
    original = build_batch(event=original_event)
    reversed_batch = build_batch(event=reversed_event)
    assert reversed_batch.quotes == original.quotes
    assert reversed_batch.quotes_hash == original.quotes_hash
    assert reversed_batch.snapshot_id == original.snapshot_id


def test_conflicting_weather_interval_is_ambiguous_across_source_ids() -> None:
    source = payloads()
    conflict = replace(source.grid[0], temperature_c=Decimal("31"), source_id="grid-v2")
    with pytest.raises(WeatherInputError):
        derive_weather_features(replace(source, grid=source.grid + (conflict,)), date(2026, 7, 12), dt("2026-07-12T17:30:00Z"))


def test_exact_weather_duplicates_are_order_deterministic() -> None:
    source = payloads()
    duplicate = replace(source.grid[0], source_id="grid-v2")
    first = derive_weather_features(
        replace(source, grid=source.grid + (duplicate,)),
        date(2026, 7, 12),
        dt("2026-07-12T17:30:00Z"),
    )
    second = derive_weather_features(
        replace(source, grid=(duplicate, *source.grid)),
        date(2026, 7, 12),
        dt("2026-07-12T17:30:00Z"),
    )
    assert first == second


@pytest.mark.parametrize("bad_value", [72.0, True])
def test_weather_temperature_requires_exact_decimal(bad_value: object) -> None:
    source = payloads()
    malformed = replace(source.grid[0], temperature_c=bad_value)
    with pytest.raises(WeatherInputError):
        derive_weather_features(replace(source, grid=(malformed, *source.grid[1:])), date(2026, 7, 12), dt("2026-07-12T17:30:00Z"))


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("lower_bound_f", True),
        ("upper_bound_f", 70.0),
        ("is_lower_tail", 1),
        ("yes_bid_cents", True),
        ("yes_bid_size", 10),
    ],
)
def test_ladder_runtime_types_fail_closed(field: str, bad_value: object) -> None:
    source = event()
    index = 0 if field == "is_lower_tail" else 1
    markets = list(source.markets)
    markets[index] = replace(markets[index], **{field: bad_value})
    with pytest.raises(LadderValidationError):
        normalize_complete_ladder(replace(source, markets=tuple(markets)), date(2026, 7, 12))


@pytest.mark.parametrize(
    ("quote_index", "field", "bad_value"),
    [
        (1, "lower_bound_f", "70"),
        (1, "lower_bound_f", None),
        (1, "lower_bound_f", True),
        (0, "upper_bound_f", "69"),
        (0, "is_lower_tail", 1),
        (1, "yes_bid_cents", "30"),
        (1, "yes_bid_size", "10"),
    ],
)
def test_standalone_ladder_validator_raises_typed_errors_for_malformed_dtos(
    quote_index: int,
    field: str,
    bad_value: object,
) -> None:
    quotes = list(normalize_complete_ladder(event(), date(2026, 7, 12)))
    quotes[quote_index] = replace(quotes[quote_index], **{field: bad_value})
    with pytest.raises(LadderValidationError):
        validate_one_hot_ladder(quotes)


@pytest.mark.parametrize("field", ["grid_forecast_high_f", "hourly_forecast_high_f", "running_observed_high_f", "forecast_spread_f"])
def test_capture_weather_feature_values_require_decimal(field: str) -> None:
    batch = build_batch()
    batch = replace(batch, features=replace(batch.features, **{field: 80.0}))
    with pytest.raises(CaptureTimingError):
        validate_capture_timing(batch, timedelta(seconds=10))


@pytest.mark.parametrize(
    "field",
    [
        "forecast_issued_at",
        "forecast_valid_start",
        "forecast_valid_end",
        "observation_measured_at",
        "observation_coverage_start",
        "weather_retrieved_at",
    ],
)
def test_standalone_capture_validator_rejects_naive_feature_timestamps(field: str) -> None:
    batch = build_batch()
    naive = getattr(batch.features, field).replace(tzinfo=None)
    malformed = replace(batch, features=replace(batch.features, **{field: naive}))
    with pytest.raises(CaptureTimingError):
        validate_capture_timing(malformed, timedelta(seconds=10))


@pytest.mark.parametrize("location", ["batch", "feature", "quote"])
def test_standalone_capture_validator_rejects_non_datetime_timestamps(location: str) -> None:
    batch = build_batch()
    if location == "batch":
        malformed = replace(batch, event_retrieved_at="not-a-datetime")
    elif location == "feature":
        malformed = replace(
            batch,
            features=replace(batch.features, forecast_issued_at="not-a-datetime"),
        )
    else:
        quote = replace(batch.quotes[0], price_retrieved_at="not-a-datetime")
        malformed = replace(batch, quotes=(quote, *batch.quotes[1:]))
    with pytest.raises(CaptureTimingError):
        validate_capture_timing(malformed, timedelta(seconds=10))


def test_fee_provenance_is_static_canonical_and_official() -> None:
    assert WEATHER_SHADOW_FEE_SCHEDULE_RECORD["source_url"] == "https://kalshi.com/docs/kalshi-fee-schedule.pdf"
    assert WEATHER_SHADOW_FEE_SCHEDULE_RECORD["official_reference_url"] == "https://help.kalshi.com/en/articles/13823805-fees"
    canonical = json.dumps(dict(WEATHER_SHADOW_FEE_SCHEDULE_RECORD), sort_keys=True, separators=(",", ":"))
    assert WEATHER_SHADOW_FEE_SCHEDULE_VERSION == sha256(canonical.encode()).hexdigest()
