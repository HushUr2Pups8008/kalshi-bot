"""Pure, fail-closed validation for deterministic KXHIGHNY shadow captures."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, fields, is_dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
import math
import re
from typing import AbstractSet, Any
from zoneinfo import ZoneInfo

from weather.shadow_models import (
    WEATHER_SHADOW_CAPTURE_MODEL_VERSION,
    WEATHER_SHADOW_FEE_SCHEDULE_VERSION,
    CaptureBatch,
    Fingerprints,
    HorizonBucket,
    NwsCapturePayloads,
    NwsDailyLabel,
    NwsGridForecast,
    NwsHourlyForecast,
    OutcomeBatch,
    OutcomeRow,
    RetrievedEvent,
    RetrievedMarket,
    ShadowQuote,
    WeatherFeatures,
)

_NEW_YORK = ZoneInfo("America/New_York")
_TICKER_PATTERN = re.compile(r"^KXHIGHNY-(?P<year>\d{2})(?P<month>[A-Z]{3})(?P<day>\d{2})$")
_MONTHS = {name: number for number, name in enumerate(("", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")) if name}
_HORIZONS: tuple[tuple[HorizonBucket, int], ...] = (
    ("T-24h", 24 * 60 * 60),
    ("T-12h", 12 * 60 * 60),
    ("T-6h", 6 * 60 * 60),
    ("T-1h", 60 * 60),
)
_ONE_TENTH = Decimal("0.1")


class WeatherShadowValidationError(ValueError):
    """Base error for an ineligible shadow capture."""


class TickerValidationError(WeatherShadowValidationError):
    """Raised when an event ticker has no unambiguous New York target date."""


class WeatherInputError(WeatherShadowValidationError):
    """Raised when weather evidence is missing, ambiguous, late, or failed QC."""


class LadderValidationError(WeatherShadowValidationError):
    """Raised when the retrieved event is not a complete one-hot ladder."""


class CaptureTimingError(WeatherShadowValidationError):
    """Raised when inputs do not share a leakage-free capture time."""


class OutcomeValidationError(WeatherShadowValidationError):
    """Raised when public settlement or official label evidence is incoherent."""


def _require_aware(value: object, error_type: type[WeatherShadowValidationError], name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise error_type(f"{name} must be timezone-aware")


def _utc_text(value: datetime) -> str:
    _require_aware(value, WeatherShadowValidationError, "datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _elapsed_seconds(later: datetime, earlier: datetime) -> Decimal:
    return Decimal(str((_utc(later) - _utc(earlier)).total_seconds()))


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise WeatherShadowValidationError("non-finite decimal is not canonical")
        return str(value)
    if isinstance(value, datetime):
        return _utc_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WeatherShadowValidationError("non-finite float is not canonical")
        return value
    raise WeatherShadowValidationError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return stable JSON with decimal strings and UTC timestamps."""
    return json.dumps(_canonical_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _canonical_raw_json(
    value: str,
    name: str,
    error_type: type[WeatherShadowValidationError] = WeatherInputError,
) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise error_type(f"{name} is not valid JSON") from exc


def parse_event_target_date(event_ticker: str) -> date:
    match = _TICKER_PATTERN.fullmatch(event_ticker)
    if match is None:
        raise TickerValidationError("event ticker must match KXHIGHNY-YYMONDD")
    month = _MONTHS.get(match.group("month"))
    if month is None:
        raise TickerValidationError("event ticker contains an invalid month")
    try:
        return date(2000 + int(match.group("year")), month, int(match.group("day")))
    except ValueError as exc:
        raise TickerValidationError("event ticker contains an invalid civil date") from exc


def select_due_horizon(
    now: datetime,
    close: datetime,
    claimed: AbstractSet[HorizonBucket],
) -> HorizonBucket | None:
    _require_aware(now, CaptureTimingError, "now")
    _require_aware(close, CaptureTimingError, "close")
    if _utc(now) >= _utc(close):
        return None
    seconds_to_close = _elapsed_seconds(close, now)
    for bucket, horizon_seconds in _HORIZONS:
        if bucket in claimed:
            continue
        horizon = Decimal(horizon_seconds)
        if horizon - Decimal(900) <= seconds_to_close <= horizon:
            return bucket
    return None


def celsius_to_fahrenheit(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise WeatherInputError("temperature must be a finite Decimal")
    return (value * Decimal(9) / Decimal(5) + Decimal(32)).quantize(_ONE_TENTH, rounding=ROUND_HALF_UP)


def _target_bounds(target: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target, time.min, _NEW_YORK)
    return start, datetime.combine(target + timedelta(days=1), time.min, _NEW_YORK)


def _validate_forecasts(
    grid: tuple[NwsGridForecast, ...],
    hourly: tuple[NwsHourlyForecast, ...],
    as_of: datetime,
) -> datetime:
    combined: tuple[NwsGridForecast | NwsHourlyForecast, ...] = (*grid, *hourly)
    if any(not item.qc_passed for item in combined):
        raise WeatherInputError("forecast input failed QC")
    if any(_utc(item.issued_at) > _utc(as_of) for item in combined):
        raise WeatherInputError("forecast was issued after as_of")
    issued = {item.issued_at for item in combined}
    if len(issued) != 1:
        if any(item.source_id is None for item in combined):
            raise WeatherInputError("revised forecast has no stable identity")
        raise WeatherInputError("forecast revisions are ambiguous")
    if any(not item.source_id for item in combined):
        raise WeatherInputError("forecast identity is missing")
    return next(iter(issued))


def _reject_conflicting_duplicates(items: Sequence[Any], key_fields: tuple[str, ...]) -> None:
    seen: dict[tuple[Any, ...], Decimal] = {}
    for item in items:
        key = tuple(
            _utc(value) if isinstance(value := getattr(item, field), datetime) else value
            for field in key_fields
        )
        previous = seen.setdefault(key, item.temperature_c)
        if previous != item.temperature_c:
            raise WeatherInputError("weather values are ambiguous for one source interval")


def derive_weather_features(
    payloads: NwsCapturePayloads,
    target: date,
    as_of: datetime,
) -> WeatherFeatures:
    _require_aware(as_of, WeatherInputError, "as_of")
    _require_aware(payloads.retrieved_at, WeatherInputError, "retrieved_at")
    if _utc(payloads.retrieved_at) > _utc(as_of):
        raise WeatherInputError("weather was retrieved after as_of")
    day_start, day_end = _target_bounds(target)
    for item in (*payloads.grid, *payloads.hourly, *payloads.observations):
        for value in asdict(item).values():
            if isinstance(value, datetime):
                _require_aware(value, WeatherInputError, "weather timestamp")
        if type(item.temperature_c) is not Decimal or not item.temperature_c.is_finite():
            raise WeatherInputError("weather temperature must be a finite Decimal")
        if type(item.qc_passed) is not bool:
            raise WeatherInputError("weather QC flag must be bool")

    day_start_utc, day_end_utc = _utc(day_start), _utc(day_end)
    grid = tuple(
        item
        for item in payloads.grid
        if _utc(item.valid_start) < day_end_utc and _utc(item.valid_end) > day_start_utc
    )
    hourly = tuple(item for item in payloads.hourly if day_start_utc <= _utc(item.start_time) < day_end_utc)
    if not grid or not hourly:
        raise WeatherInputError("target-day forecast inputs are incomplete")
    if any(_utc(item.valid_end) <= _utc(item.valid_start) for item in grid):
        raise WeatherInputError("grid valid interval is invalid")
    _reject_conflicting_duplicates(grid, ("valid_start", "valid_end", "issued_at"))
    _reject_conflicting_duplicates(hourly, ("start_time", "issued_at"))
    forecast_issued_at = _validate_forecasts(grid, hourly, as_of)

    if any(item.station_id != "KNYC" for item in payloads.observations):
        raise WeatherInputError("observations must come only from KNYC")
    target_observations = tuple(
        item for item in payloads.observations if day_start_utc <= _utc(item.measured_at) < day_end_utc
    )
    if not target_observations:
        raise WeatherInputError("target-day KNYC observations are missing")
    if any(_utc(item.measured_at) > _utc(as_of) for item in target_observations):
        raise WeatherInputError("observation was measured after as_of")
    if any(not item.qc_passed for item in target_observations):
        raise WeatherInputError("observation failed QC")
    if any(not item.source_id for item in target_observations):
        raise WeatherInputError("observation identity is missing")
    _reject_conflicting_duplicates(target_observations, ("station_id", "measured_at"))

    grid_high = max(celsius_to_fahrenheit(item.temperature_c) for item in grid)
    hourly_high = max(celsius_to_fahrenheit(item.temperature_c) for item in hourly)
    observed_high = max(celsius_to_fahrenheit(item.temperature_c) for item in target_observations)
    canonical_grid = tuple(
        sorted(
            payloads.grid,
            key=lambda item: (
                _utc_text(item.valid_start),
                _utc_text(item.valid_end),
                _utc_text(item.issued_at),
                str(item.temperature_c),
                item.source_id or "",
                item.qc_passed,
            ),
        )
    )
    canonical_hourly = tuple(
        sorted(
            payloads.hourly,
            key=lambda item: (
                _utc_text(item.start_time),
                _utc_text(item.issued_at),
                str(item.temperature_c),
                item.source_id or "",
                item.qc_passed,
            ),
        )
    )
    canonical_observations = tuple(
        sorted(
            payloads.observations,
            key=lambda item: (
                item.station_id,
                _utc_text(item.measured_at),
                str(item.temperature_c),
                item.source_id or "",
                item.qc_passed,
            ),
        )
    )
    source_payload = {
        "grid": canonical_grid,
        "hourly": canonical_hourly,
        "observations": canonical_observations,
        "retrieved_at": payloads.retrieved_at,
        "raw": {
            "grid": _canonical_raw_json(payloads.grid_payload_json, "grid_payload_json"),
            "hourly": _canonical_raw_json(payloads.hourly_payload_json, "hourly_payload_json"),
            "observations": _canonical_raw_json(payloads.observations_payload_json, "observations_payload_json"),
        },
        "target_date": target,
        "as_of": as_of,
    }
    source_payload_json = canonical_json(source_payload)
    return WeatherFeatures(
        forecast_issued_at=forecast_issued_at,
        forecast_valid_start=min((item.valid_start for item in grid), key=_utc),
        forecast_valid_end=max((item.valid_end for item in grid), key=_utc),
        observation_measured_at=max((item.measured_at for item in target_observations), key=_utc),
        observation_coverage_start=min((item.measured_at for item in target_observations), key=_utc),
        observation_count=len(target_observations),
        weather_retrieved_at=payloads.retrieved_at,
        grid_forecast_high_f=grid_high,
        hourly_forecast_high_f=hourly_high,
        running_observed_high_f=observed_high,
        forecast_spread_f=(grid_high - hourly_high).quantize(_ONE_TENTH, rounding=ROUND_HALF_UP),
        target_weekday=target.weekday(),
        source_payload_json=source_payload_json,
        source_payload_hash=sha256(source_payload_json.encode()).hexdigest(),
    )


def _require_decimal(value: Decimal | None, name: str) -> None:
    if value is None:
        return
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise LadderValidationError(f"{name} must be a finite nonnegative Decimal")


def _quote_from_market(item: RetrievedMarket) -> ShadowQuote:
    raw_payload = _canonical_raw_json(
        item.raw_payload_json,
        "market raw_payload_json",
        LadderValidationError,
    )
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
        raw_payload_hash=canonical_sha256(raw_payload),
    )


def normalize_complete_ladder(event: RetrievedEvent, target_date: date) -> tuple[ShadowQuote, ...]:
    if parse_event_target_date(event.event_ticker) != target_date:
        raise LadderValidationError("event target date does not match capture target")
    _require_aware(event.close_time, LadderValidationError, "event close_time")
    _require_aware(event.retrieved_at, LadderValidationError, "event retrieved_at")
    if event.status != "open":
        raise LadderValidationError("event must be open")
    if len(event.market_tickers) != len(set(event.market_tickers)):
        raise LadderValidationError("event enumeration contains duplicate tickers")
    actual_tickers = tuple(item.market_ticker for item in event.markets)
    if len(actual_tickers) != len(set(actual_tickers)) or set(actual_tickers) != set(event.market_tickers):
        raise LadderValidationError("event market enumeration is incomplete")
    if len(event.markets) < 3:
        raise LadderValidationError("event ladder requires two tails and at least one bounded range")
    common_settlement_source = event.markets[0].fingerprints.settlement_source
    for item in event.markets:
        _require_aware(item.close_time, LadderValidationError, "market close_time")
        _require_aware(item.price_retrieved_at, LadderValidationError, "price_retrieved_at")
        if item.event_ticker != event.event_ticker or item.status != "open":
            raise LadderValidationError("market does not belong to the same open event")
        if item.close_time != event.close_time:
            raise LadderValidationError("market close times differ")
        if any(bound is not None and type(bound) is not int for bound in (item.lower_bound_f, item.upper_bound_f)):
            raise LadderValidationError("market bounds must be exact integers or null")
        if type(item.is_lower_tail) is not bool or type(item.is_upper_tail) is not bool:
            raise LadderValidationError("market tail flags must be bool")
        if _utc(item.price_retrieved_at) > _utc(event.retrieved_at):
            raise LadderValidationError("event retrieval predates a sibling quote")
        if not all(asdict(item.fingerprints).values()):
            raise LadderValidationError("contract, rules, and settlement fingerprints are required")
        if item.fingerprints.settlement_source != common_settlement_source:
            raise LadderValidationError("settlement-source fingerprints differ")
    quotes = tuple(
        _quote_from_market(item)
        for item in sorted(
            event.markets,
            key=lambda item: (
                item.lower_bound_f is not None,
                item.lower_bound_f if item.lower_bound_f is not None else -10_000,
                item.market_ticker,
            ),
        )
    )
    validate_one_hot_ladder(quotes)
    return quotes


def validate_one_hot_ladder(quotes: Sequence[ShadowQuote]) -> None:
    if len(quotes) < 3:
        raise LadderValidationError("ladder is incomplete")
    for item in quotes:
        if any(bound is not None and type(bound) is not int for bound in (item.lower_bound_f, item.upper_bound_f)):
            raise LadderValidationError("quote bounds must be exact integers or null")
        if type(item.is_lower_tail) is not bool or type(item.is_upper_tail) is not bool:
            raise LadderValidationError("quote tail flags must be bool")
        prices = (item.yes_bid_cents, item.yes_ask_cents, item.no_bid_cents, item.no_ask_cents)
        if any(type(value) is not int for value in prices):
            raise LadderValidationError("quote cents must be exact integers")
        for name in ("yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size"):
            if type(getattr(item, name)) is not Decimal:
                raise LadderValidationError(f"{name} must be a Decimal")
        if item.volume is not None and type(item.volume) is not Decimal:
            raise LadderValidationError("volume must be a Decimal or null")
        if item.last_price_cents is not None and type(item.last_price_cents) is not int:
            raise LadderValidationError("last price cents must be an exact integer or null")
    lower_tails = [item for item in quotes if item.is_lower_tail]
    upper_tails = [item for item in quotes if item.is_upper_tail]
    if len(lower_tails) != 1 or len(upper_tails) != 1:
        raise LadderValidationError("ladder must have exactly two tails")
    if quotes[0] is not lower_tails[0] or quotes[-1] is not upper_tails[0]:
        raise LadderValidationError("tails must be ladder endpoints")
    if quotes[0].lower_bound_f is not None or quotes[0].upper_bound_f is None:
        raise LadderValidationError("lower tail bounds are invalid")
    if quotes[-1].upper_bound_f is not None or quotes[-1].lower_bound_f is None:
        raise LadderValidationError("upper tail bounds are invalid")
    for item in quotes[1:-1]:
        if item.is_lower_tail or item.is_upper_tail or item.lower_bound_f is None or item.upper_bound_f is None:
            raise LadderValidationError("bounded market has invalid tail flags or bounds")
        if item.lower_bound_f > item.upper_bound_f:
            raise LadderValidationError("bounded market range is reversed")
    for previous, current in zip(quotes, quotes[1:]):
        if previous.upper_bound_f is None or current.lower_bound_f is None:
            raise LadderValidationError("ladder endpoint is misplaced")
        if previous.upper_bound_f + 1 != current.lower_bound_f:
            raise LadderValidationError("ladder ranges are not contiguous and non-overlapping")
    for item in quotes:
        prices = (item.yes_bid_cents, item.yes_ask_cents, item.no_bid_cents, item.no_ask_cents)
        if any(not 0 <= value <= 100 for value in prices):
            raise LadderValidationError("quote cents must be integers in [0, 100]")
        if item.yes_bid_cents > item.yes_ask_cents or item.no_bid_cents > item.no_ask_cents:
            raise LadderValidationError("quote book is crossed")
        if item.yes_bid_cents + item.no_ask_cents != 100 or item.yes_ask_cents + item.no_bid_cents != 100:
            raise LadderValidationError("yes/no complements are inconsistent")
        for name in ("yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size", "volume"):
            _require_decimal(getattr(item, name), name)
        if item.last_price_cents is not None and not 0 <= item.last_price_cents <= 100:
            raise LadderValidationError("last price cents are invalid")


def _contains_high(item: ShadowQuote | RetrievedMarket, high_f: int) -> bool:
    return (
        (item.lower_bound_f is None or item.lower_bound_f <= high_f)
        and (item.upper_bound_f is None or high_f <= item.upper_bound_f)
    )


def _official_high_ticker(
    high_f: Decimal,
    markets: Sequence[ShadowQuote | RetrievedMarket],
) -> str:
    if (
        type(high_f) is not Decimal
        or not high_f.is_finite()
        or high_f != high_f.to_integral_value()
    ):
        raise OutcomeValidationError("official high must be an integer Fahrenheit value")
    matches = [item.market_ticker for item in markets if _contains_high(item, int(high_f))]
    if len(matches) != 1:
        raise OutcomeValidationError("official high must match exactly one sibling market")
    return matches[0]


def official_high_market(high_f: Decimal, quotes: Sequence[ShadowQuote]) -> str:
    """Return the unique sibling ticker containing an official integer high."""
    return _official_high_ticker(high_f, quotes)


def _validate_cli_product(cli_product: NwsDailyLabel, target_date: date) -> None:
    for name, value in (
        ("official issued_at", cli_product.issued_at),
        ("official retrieved_at", cli_product.retrieved_at),
    ):
        _require_aware(value, OutcomeValidationError, name)
    if cli_product.issued_at > cli_product.retrieved_at:
        raise OutcomeValidationError("official product cannot be issued after retrieval")
    if cli_product.target_date != target_date:
        raise OutcomeValidationError("official product target date does not match event civil day")
    if cli_product.station_id != "KNYC":
        raise OutcomeValidationError("official product must identify KNYC")
    if re.fullmatch(r"[A-Za-z0-9-]+", cli_product.product_id) is None:
        raise OutcomeValidationError("official product id is invalid")
    expected_url = f"https://api.weather.gov/products/{cli_product.product_id}"
    if cli_product.source_url != expected_url:
        raise OutcomeValidationError("official product source identity is invalid")
    payload = _canonical_raw_json(
        cli_product.raw_payload_json,
        "official raw_payload_json",
        OutcomeValidationError,
    )
    if not isinstance(payload, dict):
        raise OutcomeValidationError("official raw payload must be an object")
    if (
        payload.get("id") != cli_product.product_id
        or payload.get("@id") != expected_url
        or payload.get("productCode") != "CLI"
        or payload.get("issuingOffice") != "KOKX"
    ):
        raise OutcomeValidationError("official CLI payload identity is invalid")
    raw_issuance = payload.get("issuanceTime")
    if not isinstance(raw_issuance, str):
        raise OutcomeValidationError("official CLI issuance identity is invalid")
    try:
        payload_issued_at = datetime.fromisoformat(raw_issuance.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OutcomeValidationError("official CLI issuance identity is invalid") from exc
    if payload_issued_at.tzinfo is None or _utc(payload_issued_at) != _utc(cli_product.issued_at):
        raise OutcomeValidationError("official CLI issuance identity is invalid")
    if cli_product.evidence_id != canonical_sha256(payload):
        raise OutcomeValidationError("official evidence id does not match CLI payload")
    product_text = payload.get("productText")
    if not isinstance(product_text, str):
        raise OutcomeValidationError("official CLI product text is missing")
    report_date = re.search(
        r"THE CENTRAL PARK NY CLIMATE SUMMARY FOR ([A-Z]+ \d{1,2} \d{4})",
        product_text,
    )
    maximum = re.search(r"\bMAXIMUM\s+(-?\d+(?:\.\d+)?)\b", product_text)
    if report_date is None or maximum is None:
        raise OutcomeValidationError("official CLI target or maximum is missing")
    try:
        parsed_date = datetime.strptime(report_date.group(1), "%B %d %Y").date()
        parsed_high = Decimal(maximum.group(1))
    except (ValueError, ArithmeticError) as exc:
        raise OutcomeValidationError("official CLI target or maximum is malformed") from exc
    if parsed_date != target_date or parsed_high != cli_product.official_high_f:
        raise OutcomeValidationError("official CLI content does not match normalized label")


def validate_outcome_batch(
    event_payload: RetrievedEvent,
    cli_product: NwsDailyLabel,
    captured_fingerprints: Mapping[str, Fingerprints],
) -> OutcomeBatch:
    """Build a deterministic complete outcome batch from public evidence."""
    target_date = parse_event_target_date(event_payload.event_ticker)
    _require_aware(event_payload.retrieved_at, OutcomeValidationError, "settlement retrieved_at")
    if event_payload.status not in {"finalized", "settled"}:
        raise OutcomeValidationError("event must be finalized or settled")
    markets = event_payload.markets
    enumerated = event_payload.market_tickers
    retrieved = tuple(item.market_ticker for item in markets)
    if (
        len(markets) <= 1
        or len(enumerated) != len(set(enumerated))
        or len(retrieved) != len(set(retrieved))
        or set(enumerated) != set(retrieved)
        or set(captured_fingerprints) != set(retrieved)
    ):
        raise OutcomeValidationError("complete captured sibling enumeration is required")
    if any(
        item.event_ticker != event_payload.event_ticker
        or item.status not in {"finalized", "settled"}
        or item.result not in {"yes", "no"}
        or captured_fingerprints.get(item.market_ticker) != item.fingerprints
        for item in markets
    ):
        raise OutcomeValidationError("settled siblings or capture fingerprints are invalid")
    if sum(item.result == "yes" for item in markets) != 1:
        raise OutcomeValidationError("complete outcome ladder requires exactly one YES")

    _validate_cli_product(cli_product, target_date)
    winning_ticker = _official_high_ticker(cli_product.official_high_f, markets)
    yes_ticker = next(item.market_ticker for item in markets if item.result == "yes")
    if winning_ticker != yes_ticker:
        raise OutcomeValidationError("settled YES does not match official integer high")

    official_inputs = {
        "target_date": cli_product.target_date,
        "station_id": cli_product.station_id,
        "official_high_f": cli_product.official_high_f,
        "issued_at": cli_product.issued_at,
        "source_url": cli_product.source_url,
        "product_id": cli_product.product_id,
        "evidence_id": cli_product.evidence_id,
    }
    market_projections: list[tuple[RetrievedMarket, dict[str, Any]]] = []
    for item in sorted(markets, key=lambda sibling: sibling.market_ticker):
        market_projection = {
            "event_ticker": event_payload.event_ticker,
            "event_status": event_payload.status,
            "market_ticker": item.market_ticker,
            "market_status": item.status,
            "result": item.result,
            "lower_bound_f": item.lower_bound_f,
            "upper_bound_f": item.upper_bound_f,
            "is_lower_tail": item.is_lower_tail,
            "is_upper_tail": item.is_upper_tail,
            "fingerprints": item.fingerprints,
        }
        market_projections.append((item, market_projection))
    complete_projection_hash = canonical_sha256(
        {
            "event_ticker": event_payload.event_ticker,
            "target_date": target_date,
            "siblings": tuple(projection for _, projection in market_projections),
            "official": official_inputs,
        }
    )
    outcome_batch_id = canonical_sha256(
        {
            "hash_kind": "complete_stable_sibling_projection_v1",
            "complete_projection_hash": complete_projection_hash,
        }
    )
    stable_rows = tuple(
        (
            item,
            canonical_sha256(
                {
                    "market_source_identity": canonical_sha256(market_projection),
                    "complete_projection_hash": complete_projection_hash,
                }
            ),
        )
        for item, market_projection in market_projections
    )
    settlement_observed_at = event_payload.retrieved_at
    label_available_at = max(settlement_observed_at, cli_product.retrieved_at)
    rows = tuple(
        OutcomeRow(
            outcome_id=canonical_sha256(
                {
                    "outcome_batch_id": outcome_batch_id,
                    "market_ticker": item.market_ticker,
                    "source_payload_hash": source_hash,
                }
            ),
            outcome_batch_id=outcome_batch_id,
            market_ticker=item.market_ticker,
            event_ticker=event_payload.event_ticker,
            expected_sibling_count=len(markets),
            result=item.result,  # type: ignore[arg-type]
            kalshi_status=item.status,
            settlement_observed_at=settlement_observed_at,
            source_payload_hash=source_hash,
            fingerprints=item.fingerprints,
            official_high_f=cli_product.official_high_f,
            official_evidence_id=cli_product.evidence_id,
            official_source_url=cli_product.source_url,
            official_product_id=cli_product.product_id,
            official_issued_at=cli_product.issued_at,
            official_retrieved_at=cli_product.retrieved_at,
            label_available_at=label_available_at,
        )
        for item, source_hash in stable_rows
    )
    return OutcomeBatch(
        outcome_batch_id=outcome_batch_id,
        event_ticker=event_payload.event_ticker,
        target_date=target_date,
        settlement_observed_at=settlement_observed_at,
        label_available_at=label_available_at,
        rows=rows,
    )


def validate_capture_timing(batch: CaptureBatch, max_sweep: timedelta) -> None:
    if max_sweep < timedelta(0):
        raise CaptureTimingError("max_sweep cannot be negative")
    for name in ("capture_started_at", "capture_finished_at", "as_of", "close_time", "event_retrieved_at"):
        _require_aware(getattr(batch, name), CaptureTimingError, name)
    feature_time_names = (
        "forecast_issued_at",
        "forecast_valid_start",
        "forecast_valid_end",
        "observation_measured_at",
        "observation_coverage_start",
        "weather_retrieved_at",
    )
    for name in feature_time_names:
        _require_aware(getattr(batch.features, name), CaptureTimingError, name)
    for quote in batch.quotes:
        _require_aware(quote.close_time, CaptureTimingError, "quote close_time")
        _require_aware(quote.price_retrieved_at, CaptureTimingError, "price_retrieved_at")
    if _utc(batch.as_of) != _utc(batch.capture_finished_at):
        raise CaptureTimingError("as_of must equal capture_finished_at")
    if _utc(batch.capture_finished_at) < _utc(batch.capture_started_at):
        raise CaptureTimingError("capture sweep is reversed")
    if _utc(batch.capture_finished_at) - _utc(batch.capture_started_at) > max_sweep:
        raise CaptureTimingError("capture sweep exceeded its bound")
    if _utc(batch.as_of) >= _utc(batch.close_time):
        raise CaptureTimingError("capture is not pre-close")
    if parse_event_target_date(batch.event_ticker) != batch.target_date:
        raise CaptureTimingError("capture target date does not match event ticker")
    expected_seconds = _elapsed_seconds(batch.close_time, batch.as_of)
    if batch.seconds_to_close != expected_seconds or batch.seconds_to_close < 0:
        raise CaptureTimingError("seconds_to_close is inconsistent")
    if select_due_horizon(batch.as_of, batch.close_time, frozenset()) != batch.horizon_bucket:
        raise CaptureTimingError("capture is outside the requested horizon window")
    feature_times = (
        batch.features.forecast_issued_at,
        batch.features.observation_measured_at,
        batch.features.weather_retrieved_at,
    )
    if any(_utc(value) > _utc(batch.as_of) for value in feature_times):
        raise CaptureTimingError("feature evidence became available after as_of")
    if batch.features.observation_coverage_start > batch.features.observation_measured_at:
        raise CaptureTimingError("observation coverage is reversed")
    for name in (
        "grid_forecast_high_f",
        "hourly_forecast_high_f",
        "running_observed_high_f",
        "forecast_spread_f",
    ):
        value = getattr(batch.features, name)
        if type(value) is not Decimal or not value.is_finite():
            raise CaptureTimingError(f"{name} must be a finite Decimal")
    capture_start = _utc(batch.capture_started_at)
    capture_as_of = _utc(batch.as_of)
    weather_retrieved = _utc(batch.features.weather_retrieved_at)
    event_retrieved = _utc(batch.event_retrieved_at)
    if not capture_start <= weather_retrieved <= capture_as_of:
        raise CaptureTimingError("weather retrieval fell outside the capture sweep")
    if not capture_start <= event_retrieved <= capture_as_of:
        raise CaptureTimingError("event retrieval fell outside the capture sweep")
    if not batch.quotes:
        raise CaptureTimingError("capture has no quotes")
    for quote in batch.quotes:
        if quote.close_time != batch.close_time:
            raise CaptureTimingError("quote close does not match batch close")
        quote_retrieved = _utc(quote.price_retrieved_at)
        if not capture_start <= quote_retrieved <= capture_as_of:
            raise CaptureTimingError("quote retrieval fell outside the capture sweep")
        if quote_retrieved < weather_retrieved:
            raise CaptureTimingError("weather was retrieved after quote retrieval")


def build_capture_batch(
    *,
    event: RetrievedEvent,
    target_date: date,
    horizon_bucket: HorizonBucket,
    features: WeatherFeatures,
    capture_started_at: datetime,
    capture_finished_at: datetime,
    as_of: datetime,
    model_version: str = WEATHER_SHADOW_CAPTURE_MODEL_VERSION,
    fee_schedule_version: str = WEATHER_SHADOW_FEE_SCHEDULE_VERSION,
) -> CaptureBatch:
    if not model_version or not fee_schedule_version:
        raise WeatherShadowValidationError("model and fee-schedule versions are required")
    horizon_names = {name for name, _ in _HORIZONS}
    if horizon_bucket not in horizon_names:
        raise CaptureTimingError("unknown fixed horizon")
    quotes = normalize_complete_ladder(event, target_date)
    quotes_hash = canonical_sha256(
        {
            "event_ticker": event.event_ticker,
            "event_status": event.status,
            "event_close_time": event.close_time,
            "event_retrieved_at": event.retrieved_at,
            "event_market_tickers": tuple(sorted(event.market_tickers)),
            "quotes": quotes,
            "model_version": model_version,
            "fee_schedule_version": fee_schedule_version,
        }
    )
    capture_key = canonical_sha256(
        {"event_ticker": event.event_ticker, "horizon_bucket": horizon_bucket, "model_version": model_version}
    )
    seconds_to_close = _elapsed_seconds(event.close_time, as_of)
    batch = CaptureBatch(
        snapshot_id="",
        capture_key=capture_key,
        event_ticker=event.event_ticker,
        target_date=target_date,
        capture_started_at=capture_started_at,
        capture_finished_at=capture_finished_at,
        as_of=as_of,
        close_time=event.close_time,
        event_retrieved_at=event.retrieved_at,
        seconds_to_close=seconds_to_close,
        horizon_bucket=horizon_bucket,
        features=features,
        quotes_hash=quotes_hash,
        fee_schedule_version=fee_schedule_version,
        model_version=model_version,
        quotes=quotes,
    )
    validate_capture_timing(batch, timedelta(seconds=10))
    snapshot_inputs = {field.name: getattr(batch, field.name) for field in fields(batch) if field.name != "snapshot_id"}
    return replace(batch, snapshot_id=canonical_sha256(snapshot_inputs))
