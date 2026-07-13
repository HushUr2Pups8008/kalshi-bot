"""Strict public weather.gov readers for the KXHIGHNY shadow workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import aiohttp

from weather.shadow_models import (
    NwsCapturePayloads,
    NwsDailyLabel,
    NwsGridForecast,
    NwsHourlyForecast,
    NwsObservation,
)


_ORIGIN = "https://api.weather.gov"
_POINTS_URL = f"{_ORIGIN}/points/40.7812,-73.9665"
_PRODUCTS_URL = f"{_ORIGIN}/products/types/CLI/locations/NYC"
_USER_AGENT = "kalshi-bot-weather-shadow/1.0 (https://github.com/HushUr2Pups8008/kalshi-bot)"
_NYC = ZoneInfo("America/New_York")
_INTERVAL = re.compile(
    r"(?P<start>[^/]+)/P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?"
)
_REPORT_DATE = re.compile(
    r"THE CENTRAL PARK NY CLIMATE SUMMARY FOR ([A-Z]+ \d{1,2} \d{4})"
)
_MAXIMUM = re.compile(r"(?m)^\s*MAXIMUM\s+(-?\d+(?:\.\d+)?)(?:\s|$)")


class NwsPublicClientError(ValueError):
    """Raised when a weather.gov response cannot be trusted."""


def _canonical_json(payload: Any) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise NwsPublicClientError("payload is not canonical JSON") from exc


def _source_hash(payload: Any) -> str:
    return sha256(_canonical_json(payload).encode()).hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise NwsPublicClientError(f"{label} must be an object")
    return value


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise NwsPublicClientError(f"{key} must be a non-empty string")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise NwsPublicClientError(f"{label} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NwsPublicClientError(f"{label} is malformed") from exc
    if parsed.tzinfo is None:
        raise NwsPublicClientError(f"{label} must include an offset")
    return parsed.astimezone(timezone.utc)


def _number(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise NwsPublicClientError(f"{label} must be numeric")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise NwsPublicClientError(f"{label} must be numeric") from exc
    if not parsed.is_finite():
        raise NwsPublicClientError(f"{label} must be finite")
    return parsed


def _interval(value: Any) -> tuple[datetime, datetime]:
    if not isinstance(value, str):
        raise NwsPublicClientError("grid interval is malformed")
    match = _INTERVAL.fullmatch(value)
    if match is None:
        raise NwsPublicClientError("grid interval is malformed")
    start = _timestamp(match.group("start"), "grid interval start")
    duration = timedelta(
        days=int(match.group("days") or 0),
        hours=int(match.group("hours") or 0),
        minutes=int(match.group("minutes") or 0),
        seconds=float(match.group("seconds") or 0),
    )
    if duration <= timedelta(0):
        raise NwsPublicClientError("grid interval must have positive duration")
    return start, start + duration


class NwsPublicClient:
    """Fetch neutral weather DTOs from fixed public weather.gov endpoints."""

    def __init__(
        self,
        *,
        session_factory: Callable[..., Any] = aiohttp.ClientSession,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlsplit(url)
        allowed = (
            parsed.path == "/points/40.7812,-73.9665"
            or parsed.path == "/products/types/CLI/locations/NYC"
            or bool(re.fullmatch(r"/products/[A-Za-z0-9-]+", parsed.path))
            or bool(re.fullmatch(r"/gridpoints/[A-Z]{3}/\d+,\d+(?:/forecast/hourly)?", parsed.path))
            or parsed.path == "/stations/KNYC/observations"
        )
        if (
            parsed.scheme != "https"
            or parsed.netloc != "api.weather.gov"
            or parsed.username is not None
            or parsed.query
            or parsed.fragment
            or not allowed
        ):
            raise NwsPublicClientError("weather URL has invalid origin or path")

    async def _request_json(
        self,
        session: Any,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        self._validate_url(url)
        kwargs: dict[str, Any] = {"allow_redirects": False}
        if params is not None:
            kwargs["params"] = dict(params)
        try:
            response = await session.get(url, **kwargs)
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            raise NwsPublicClientError("weather.gov request failed") from exc
        try:
            if response.status != 200:
                raise NwsPublicClientError(
                    f"weather.gov request returned HTTP {response.status}"
                )
            try:
                payload = await response.json(content_type=None)
            except (aiohttp.ClientError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise NwsPublicClientError("weather.gov response is not JSON") from exc
            return _mapping(payload, "weather.gov response")
        finally:
            response.release()

    @staticmethod
    def _station(station_id: str) -> str:
        if station_id != "KNYC":
            raise NwsPublicClientError("only KNYC observations are permitted")
        return station_id

    @staticmethod
    def _target_bounds(target_date: date) -> tuple[datetime, datetime]:
        if not isinstance(target_date, date):
            raise NwsPublicClientError("target_date must be a date")
        start = datetime.combine(target_date, time.min, _NYC).astimezone(timezone.utc)
        end = datetime.combine(target_date + timedelta(days=1), time.min, _NYC).astimezone(
            timezone.utc
        )
        return start, end

    @staticmethod
    def _parse_grid(payload: Mapping[str, Any]) -> tuple[NwsGridForecast, ...]:
        properties = _mapping(payload.get("properties"), "grid properties")
        issued_at = _timestamp(properties.get("updateTime"), "grid updateTime")
        temperature = _mapping(properties.get("temperature"), "grid temperature")
        if temperature.get("uom") != "wmoUnit:degC":
            raise NwsPublicClientError("grid temperature unit is invalid")
        values = temperature.get("values")
        if not isinstance(values, list) or not values:
            raise NwsPublicClientError("grid temperature values are missing")
        source_id = _source_hash(payload)
        seen: set[tuple[datetime, datetime]] = set()
        normalized: list[NwsGridForecast] = []
        for raw_value in values:
            value = _mapping(raw_value, "grid temperature value")
            start, end = _interval(value.get("validTime"))
            key = (start, end)
            if key in seen:
                raise NwsPublicClientError("duplicate grid interval")
            seen.add(key)
            normalized.append(
                NwsGridForecast(
                    valid_start=start,
                    valid_end=end,
                    issued_at=issued_at,
                    temperature_c=_number(value.get("value"), "grid temperature"),
                    source_id=source_id,
                    qc_passed=True,
                )
            )
        return tuple(normalized)

    @staticmethod
    def _parse_hourly(payload: Mapping[str, Any]) -> tuple[NwsHourlyForecast, ...]:
        properties = _mapping(payload.get("properties"), "hourly properties")
        issued_at = _timestamp(properties.get("updateTime"), "hourly updateTime")
        periods = properties.get("periods")
        if not isinstance(periods, list) or not periods:
            raise NwsPublicClientError("hourly periods are missing")
        source_id = _source_hash(payload)
        seen: set[datetime] = set()
        normalized: list[NwsHourlyForecast] = []
        for raw_period in periods:
            period = _mapping(raw_period, "hourly period")
            start = _timestamp(period.get("startTime"), "hourly startTime")
            end = _timestamp(period.get("endTime"), "hourly endTime")
            if end <= start:
                raise NwsPublicClientError("hourly interval is malformed")
            if start in seen:
                raise NwsPublicClientError("duplicate hourly period")
            seen.add(start)
            if period.get("temperatureUnit") != "F":
                raise NwsPublicClientError("hourly temperature unit is invalid")
            temperature_f = _number(period.get("temperature"), "hourly temperature")
            normalized.append(
                NwsHourlyForecast(
                    start_time=start,
                    issued_at=issued_at,
                    temperature_c=(temperature_f - Decimal(32)) * Decimal(5) / Decimal(9),
                    source_id=source_id,
                    qc_passed=True,
                )
            )
        return tuple(normalized)

    @staticmethod
    def _parse_observations(
        payload: Mapping[str, Any], station_id: str
    ) -> tuple[NwsObservation, ...]:
        features = payload.get("features")
        if not isinstance(features, list) or not features:
            raise NwsPublicClientError("station observations are missing")
        source_id = _source_hash(payload)
        station_url = f"{_ORIGIN}/stations/{station_id}"
        seen: set[datetime] = set()
        normalized: list[NwsObservation] = []
        for raw_feature in features:
            feature = _mapping(raw_feature, "observation feature")
            feature_url = _string(feature, "id")
            parsed_feature_url = urlsplit(feature_url)
            if (
                parsed_feature_url.scheme != "https"
                or parsed_feature_url.netloc != "api.weather.gov"
                or not parsed_feature_url.path.startswith(f"/stations/{station_id}/observations/")
            ):
                raise NwsPublicClientError("observation source identity is invalid")
            properties = _mapping(feature.get("properties"), "observation properties")
            if properties.get("station") != station_url:
                raise NwsPublicClientError("observation station identity is invalid")
            measured_at = _timestamp(properties.get("timestamp"), "observation timestamp")
            if measured_at in seen:
                raise NwsPublicClientError("duplicate observation timestamp")
            seen.add(measured_at)
            temperature = _mapping(properties.get("temperature"), "observation temperature")
            if temperature.get("unitCode") != "wmoUnit:degC":
                raise NwsPublicClientError("observation temperature unit is invalid")
            if temperature.get("qualityControl") != "V":
                raise NwsPublicClientError("observation quality control failed")
            normalized.append(
                NwsObservation(
                    station_id=station_id,
                    measured_at=measured_at,
                    temperature_c=_number(
                        temperature.get("value"), "observation temperature"
                    ),
                    source_id=source_id,
                    qc_passed=True,
                )
            )
        return tuple(normalized)

    async def fetch_capture_bundle(
        self, *, target_date: date, station_id: str = "KNYC"
    ) -> NwsCapturePayloads:
        station = self._station(station_id)
        start, end = self._target_bounds(target_date)
        timeout = aiohttp.ClientTimeout(total=8)
        async with self._session_factory(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        ) as session:
            point_payload = await self._request_json(session, _POINTS_URL)
            point_properties = _mapping(point_payload.get("properties"), "point properties")
            grid_url = _string(point_properties, "forecastGridData")
            hourly_url = _string(point_properties, "forecastHourly")
            self._validate_url(grid_url)
            self._validate_url(hourly_url)
            grid_payload = await self._request_json(session, grid_url)
            hourly_payload = await self._request_json(session, hourly_url)
            observations_url = f"{_ORIGIN}/stations/{station}/observations"
            observations_payload = await self._request_json(
                session,
                observations_url,
                params={
                    "start": start.isoformat().replace("+00:00", "Z"),
                    "end": end.isoformat().replace("+00:00", "Z"),
                },
            )
            retrieved_at = self._clock().astimezone(timezone.utc)
            return NwsCapturePayloads(
                grid=self._parse_grid(grid_payload),
                hourly=self._parse_hourly(hourly_payload),
                observations=self._parse_observations(observations_payload, station),
                retrieved_at=retrieved_at,
                grid_payload_json=_canonical_json(grid_payload),
                hourly_payload_json=_canonical_json(hourly_payload),
                observations_payload_json=_canonical_json(observations_payload),
            )

    @staticmethod
    def _product_metadata(raw: Any) -> tuple[Mapping[str, Any], str, str]:
        metadata = _mapping(raw, "CLI product metadata")
        product_id = _string(metadata, "id")
        if re.fullmatch(r"[A-Za-z0-9-]+", product_id) is None:
            raise NwsPublicClientError("CLI product id is unsafe")
        product_url = f"{_ORIGIN}/products/{quote(product_id, safe='')}"
        if metadata.get("@id") != product_url:
            raise NwsPublicClientError("CLI product source identity is invalid")
        if metadata.get("productCode") != "CLI" or metadata.get("issuingOffice") != "KOKX":
            raise NwsPublicClientError("CLI product identity is invalid")
        _timestamp(metadata.get("issuanceTime"), "CLI product issuanceTime")
        return metadata, product_id, product_url

    @staticmethod
    def _label_date(product_text: str) -> date | None:
        match = _REPORT_DATE.search(product_text)
        if match is None:
            return None
        try:
            return datetime.strptime(match.group(1), "%B %d %Y").date()
        except ValueError as exc:
            raise NwsPublicClientError("CLI report date is malformed") from exc

    async def fetch_daily_label(
        self, *, target_date: date, station_id: str = "KNYC"
    ) -> NwsDailyLabel | None:
        station = self._station(station_id)
        self._target_bounds(target_date)
        timeout = aiohttp.ClientTimeout(total=8)
        async with self._session_factory(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        ) as session:
            listing = await self._request_json(session, _PRODUCTS_URL)
            graph = listing.get("@graph")
            if not isinstance(graph, list):
                raise NwsPublicClientError("CLI product listing is malformed")
            products: list[tuple[datetime, Mapping[str, Any], str, str]] = []
            seen: set[str] = set()
            for raw_metadata in graph:
                metadata, product_id, product_url = self._product_metadata(raw_metadata)
                if product_id in seen:
                    raise NwsPublicClientError("duplicate CLI product id")
                seen.add(product_id)
                products.append(
                    (
                        _timestamp(metadata["issuanceTime"], "CLI product issuanceTime"),
                        metadata,
                        product_id,
                        product_url,
                    )
                )
            for issued_at, metadata, product_id, product_url in sorted(
                products, key=lambda item: item[0], reverse=True
            ):
                payload = await self._request_json(session, product_url)
                if (
                    payload.get("id") != product_id
                    or payload.get("@id") != product_url
                    or payload.get("productCode") != "CLI"
                    or payload.get("issuingOffice") != "KOKX"
                    or payload.get("issuanceTime") != metadata.get("issuanceTime")
                ):
                    raise NwsPublicClientError("CLI product identity does not match listing")
                product_text = _string(payload, "productText")
                if self._label_date(product_text) != target_date:
                    continue
                maximum = _MAXIMUM.search(product_text)
                if maximum is None:
                    raise NwsPublicClientError("CLI maximum temperature is missing")
                raw_json = _canonical_json(payload)
                return NwsDailyLabel(
                    target_date=target_date,
                    station_id=station,
                    official_high_f=_number(maximum.group(1), "CLI maximum temperature"),
                    issued_at=issued_at,
                    retrieved_at=self._clock().astimezone(timezone.utc),
                    source_url=product_url,
                    product_id=product_id,
                    evidence_id=sha256(raw_json.encode()).hexdigest(),
                    raw_payload_json=raw_json,
                )
            return None
