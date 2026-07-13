"""Frozen records shared by the weather shadow capture pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Literal, Mapping

HorizonBucket = Literal["T-24h", "T-12h", "T-6h", "T-1h"]
OutcomeResult = Literal["yes", "no"]
OutcomeCheckKind = Literal["daily", "seal"]

WEATHER_SHADOW_CAPTURE_MODEL_VERSION = "kxhighny-fixed-horizon-v1"

_FEE_SOURCE_RECORD = {
    "hash_kind": "sha256-canonical-official-reference-metadata",
    "official_reference_updated": "2026-04-19",
    "official_reference_url": "https://help.kalshi.com/en/articles/13823805-fees",
    "source_url": "https://kalshi.com/docs/kalshi-fee-schedule.pdf",
    "verified_on": "2026-07-12",
}
_FEE_SOURCE_HASH = sha256(
    json.dumps(_FEE_SOURCE_RECORD, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
WEATHER_SHADOW_FEE_SCHEDULE_RECORD: Mapping[str, str] = MappingProxyType(
    {**_FEE_SOURCE_RECORD, "source_hash": _FEE_SOURCE_HASH}
)
WEATHER_SHADOW_FEE_SCHEDULE_VERSION = sha256(
    json.dumps(dict(WEATHER_SHADOW_FEE_SCHEDULE_RECORD), sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


@dataclass(frozen=True)
class Fingerprints:
    contract: str
    rules_source: str
    settlement_source: str


@dataclass(frozen=True)
class NwsGridForecast:
    valid_start: datetime
    valid_end: datetime
    issued_at: datetime
    temperature_c: Decimal
    source_id: str | None
    qc_passed: bool = True


@dataclass(frozen=True)
class NwsHourlyForecast:
    start_time: datetime
    issued_at: datetime
    temperature_c: Decimal
    source_id: str | None
    qc_passed: bool = True


@dataclass(frozen=True)
class NwsObservation:
    station_id: str
    measured_at: datetime
    temperature_c: Decimal
    source_id: str | None
    qc_passed: bool = True


@dataclass(frozen=True)
class NwsCapturePayloads:
    grid: tuple[NwsGridForecast, ...]
    hourly: tuple[NwsHourlyForecast, ...]
    observations: tuple[NwsObservation, ...]
    retrieved_at: datetime
    grid_payload_json: str
    hourly_payload_json: str
    observations_payload_json: str


@dataclass(frozen=True)
class WeatherFeatures:
    forecast_issued_at: datetime
    forecast_valid_start: datetime
    forecast_valid_end: datetime
    observation_measured_at: datetime
    observation_coverage_start: datetime
    observation_count: int
    weather_retrieved_at: datetime
    grid_forecast_high_f: Decimal
    hourly_forecast_high_f: Decimal
    running_observed_high_f: Decimal
    forecast_spread_f: Decimal
    target_weekday: int
    source_payload_json: str
    source_payload_hash: str


@dataclass(frozen=True)
class RetrievedMarket:
    market_ticker: str
    event_ticker: str
    status: str
    close_time: datetime
    lower_bound_f: int | None
    upper_bound_f: int | None
    is_lower_tail: bool
    is_upper_tail: bool
    fingerprints: Fingerprints
    yes_bid_cents: int
    yes_ask_cents: int
    no_bid_cents: int
    no_ask_cents: int
    yes_bid_size: Decimal
    yes_ask_size: Decimal
    no_bid_size: Decimal
    no_ask_size: Decimal
    last_price_cents: int | None
    volume: Decimal | None
    price_retrieved_at: datetime
    raw_payload_json: str
    result: OutcomeResult | None = None


@dataclass(frozen=True)
class RetrievedEvent:
    event_ticker: str
    status: str
    close_time: datetime
    market_tickers: tuple[str, ...]
    markets: tuple[RetrievedMarket, ...]
    retrieved_at: datetime


@dataclass(frozen=True)
class ShadowQuote:
    market_ticker: str
    close_time: datetime
    lower_bound_f: int | None
    upper_bound_f: int | None
    is_lower_tail: bool
    is_upper_tail: bool
    fingerprints: Fingerprints
    yes_bid_cents: int
    yes_ask_cents: int
    no_bid_cents: int
    no_ask_cents: int
    yes_bid_size: Decimal
    yes_ask_size: Decimal
    no_bid_size: Decimal
    no_ask_size: Decimal
    last_price_cents: int | None
    volume: Decimal | None
    price_retrieved_at: datetime
    raw_payload_hash: str


@dataclass(frozen=True)
class CaptureBatch:
    snapshot_id: str
    capture_key: str
    event_ticker: str
    target_date: date
    capture_started_at: datetime
    capture_finished_at: datetime
    as_of: datetime
    close_time: datetime
    seconds_to_close: Decimal
    horizon_bucket: HorizonBucket
    features: WeatherFeatures
    quotes_hash: str
    fee_schedule_version: str
    model_version: str
    quotes: tuple[ShadowQuote, ...]


@dataclass(frozen=True)
class NwsDailyLabel:
    target_date: date
    station_id: str
    official_high_f: Decimal
    issued_at: datetime
    retrieved_at: datetime
    source_url: str
    product_id: str
    evidence_id: str
    raw_payload_json: str


@dataclass(frozen=True)
class OutcomeRow:
    outcome_id: str
    outcome_batch_id: str
    market_ticker: str
    event_ticker: str
    expected_sibling_count: int
    result: OutcomeResult
    kalshi_status: str
    settlement_observed_at: datetime
    source_payload_hash: str
    fingerprints: Fingerprints
    official_high_f: Decimal
    official_evidence_id: str
    official_source_url: str
    official_product_id: str
    official_issued_at: datetime
    official_retrieved_at: datetime
    label_available_at: datetime


@dataclass(frozen=True)
class OutcomeBatch:
    outcome_batch_id: str
    event_ticker: str
    target_date: date
    settlement_observed_at: datetime
    label_available_at: datetime
    rows: tuple[OutcomeRow, ...]


@dataclass(frozen=True)
class OutcomeCheck:
    check_id: str
    event_ticker: str
    check_date_utc: date
    checked_at: datetime
    check_kind: OutcomeCheckKind
    observed_batch_hash: str
    baseline_batch_hash: str
    agrees_with_baseline: bool
    details_json: str


@dataclass(frozen=True)
class OutcomeTarget:
    event_ticker: str
    target_date: date


@dataclass(frozen=True)
class CaptureAttemptResult:
    event_ticker: str
    horizon_bucket: HorizonBucket
    captured: bool
    reason: str
    snapshot_id: str | None = None


@dataclass(frozen=True)
class CaptureCycleResult:
    attempted: int
    captured: int
    skipped: int
