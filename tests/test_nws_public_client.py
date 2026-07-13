from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from weather.nws_public_client import NwsPublicClient, NwsPublicClientError


FIXTURES = Path(__file__).parent / "fixtures" / "weather_shadow"
ORIGIN = "https://api.weather.gov"
POINTS_URL = f"{ORIGIN}/points/40.7812,-73.9665"
GRID_URL = f"{ORIGIN}/gridpoints/OKX/34,45"
HOURLY_URL = f"{GRID_URL}/forecast/hourly"
OBS_URL = f"{ORIGIN}/stations/KNYC/observations"
PRODUCTS_URL = f"{ORIGIN}/products/types/CLI/locations/NYC"
PRODUCT_URL = f"{ORIGIN}/products/fixture-cli-20260713"
USER_AGENT = "kalshi-bot-weather-shadow/1.0 (https://github.com/HushUr2Pups8008/kalshi-bot)"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def test_representative_nws_fixture_key_sets_are_explicit() -> None:
    points = _fixture("nws_points.json")
    grid = _fixture("nws_grid.json")
    hourly = _fixture("nws_hourly.json")
    observations = _fixture("nws_observations.json")
    products = _fixture("nws_cli_products.json")
    product = _fixture("nws_cli_product.json")

    assert set(points) == {"id", "properties"}
    assert set(points["properties"]) == {"forecastGridData", "forecastHourly"}
    assert set(grid) == {"id", "properties"}
    assert set(grid["properties"]) == {"@id", "updateTime", "temperature"}
    assert set(grid["properties"]["temperature"]) == {"uom", "values"}
    assert all(
        set(value) == {"validTime", "value"}
        for value in grid["properties"]["temperature"]["values"]
    )
    assert set(hourly) == {"properties"}
    assert set(hourly["properties"]) == {"updateTime", "periods"}
    assert all(
        set(period) == {"startTime", "endTime", "temperature", "temperatureUnit"}
        for period in hourly["properties"]["periods"]
    )
    assert set(observations) == {"features"}
    assert all(set(feature) == {"id", "properties"} for feature in observations["features"])
    assert all(
        set(feature["properties"]) == {"station", "timestamp", "temperature"}
        for feature in observations["features"]
    )
    assert all(
        set(feature["properties"]["temperature"])
        == {"unitCode", "value", "qualityControl"}
        for feature in observations["features"]
    )
    assert set(products) == {"@graph"}
    expected_product_metadata_keys = {
        "@id",
        "id",
        "wmoCollectiveId",
        "issuingOffice",
        "issuanceTime",
        "productCode",
        "productName",
    }
    assert all(set(metadata) == expected_product_metadata_keys for metadata in products["@graph"])
    assert set(product) == expected_product_metadata_keys | {"productText"}


class FakeResponse:
    def __init__(self, payload: Any, *, status: int = 200, headers: dict[str, str] | None = None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    async def json(self, *, content_type: None = None) -> Any:
        return deepcopy(self.payload)

    def release(self) -> None:
        return None


class FakeSession:
    def __init__(self, responses: dict[str, list[FakeResponse]], calls: list[dict[str, Any]]):
        self.responses = responses
        self.calls = calls

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        try:
            response = self.responses[url].pop(0)
        except (KeyError, IndexError) as exc:
            raise AssertionError(f"unexpected GET {url}") from exc
        if isinstance(response, BaseException):
            raise response
        return response


class FakeFactory:
    def __init__(self, responses: dict[str, list[FakeResponse]]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.kwargs: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> FakeSession:
        self.kwargs = kwargs
        return FakeSession(self.responses, self.calls)


def _client(**overrides: Any):
    defaults = {
        POINTS_URL: [FakeResponse(_fixture("nws_points.json"))],
        GRID_URL: [FakeResponse(_fixture("nws_grid.json"))],
        HOURLY_URL: [FakeResponse(_fixture("nws_hourly.json"))],
        OBS_URL: [FakeResponse(_fixture("nws_observations.json"))],
        PRODUCTS_URL: [FakeResponse(_fixture("nws_cli_products.json"))],
        PRODUCT_URL: [FakeResponse(_fixture("nws_cli_product.json"))],
    }
    defaults.update(overrides)
    factory = FakeFactory(defaults)
    return NwsPublicClient(session_factory=factory), factory


@pytest.mark.asyncio
async def test_fetch_capture_bundle_discovers_and_normalizes_public_payloads() -> None:
    client, factory = _client()

    bundle = await client.fetch_capture_bundle(target_date=date(2026, 7, 13))

    assert len(bundle.grid) == 2
    assert len(bundle.hourly) == 2
    assert len(bundle.observations) == 2
    assert bundle.grid[0].temperature_c == Decimal("23.3333333333")
    assert bundle.hourly[0].temperature_c == Decimal("23.33333333333333333333333333")
    assert bundle.observations[0].station_id == "KNYC"
    assert all(item.source_id and len(item.source_id) == 64 for item in bundle.grid)
    assert json.loads(bundle.grid_payload_json)["properties"]["temperature"]["uom"] == "wmoUnit:degC"
    assert factory.kwargs["timeout"].total == 8
    assert factory.kwargs["headers"] == {"User-Agent": USER_AGENT}
    assert [call["url"] for call in factory.calls] == [POINTS_URL, GRID_URL, HOURLY_URL, OBS_URL]
    assert all(call["allow_redirects"] is False for call in factory.calls)
    assert factory.calls[-1]["params"] == {
        "start": "2026-07-13T04:00:00Z",
        "end": "2026-07-14T04:00:00Z",
    }


@pytest.mark.asyncio
async def test_normalization_tolerates_unrelated_live_payload_keys() -> None:
    points = _fixture("nws_points.json")
    grid = _fixture("nws_grid.json")
    hourly = _fixture("nws_hourly.json")
    observations = _fixture("nws_observations.json")
    points["@context"] = {"version": "1.1"}
    points["properties"]["gridId"] = "OKX"
    grid["geometry"] = None
    grid["properties"]["gridId"] = "OKX"
    grid["properties"]["temperature"]["futureField"] = True
    hourly["geometry"] = None
    hourly["properties"]["units"] = "us"
    hourly["properties"]["periods"][0]["name"] = ""
    observations["pagination"] = {"next": None}
    observations["features"][0]["type"] = "Feature"
    observations["features"][0]["properties"]["rawMessage"] = "fixture"
    client, _ = _client(
        **{
            POINTS_URL: [FakeResponse(points)],
            GRID_URL: [FakeResponse(grid)],
            HOURLY_URL: [FakeResponse(hourly)],
            OBS_URL: [FakeResponse(observations)],
        }
    )

    bundle = await client.fetch_capture_bundle(target_date=date(2026, 7, 13))

    assert len(bundle.grid) == 2
    assert len(bundle.hourly) == 2
    assert len(bundle.observations) == 2


@pytest.mark.parametrize(
    ("target_date", "expected_start", "expected_end", "duration"),
    [
        (
            date(2026, 3, 8),
            datetime(2026, 3, 8, 5, tzinfo=timezone.utc),
            datetime(2026, 3, 9, 4, tzinfo=timezone.utc),
            timedelta(hours=23),
        ),
        (
            date(2026, 11, 1),
            datetime(2026, 11, 1, 4, tzinfo=timezone.utc),
            datetime(2026, 11, 2, 5, tzinfo=timezone.utc),
            timedelta(hours=25),
        ),
    ],
)
def test_target_bounds_follow_new_york_dst_days(
    target_date: date,
    expected_start: datetime,
    expected_end: datetime,
    duration: timedelta,
) -> None:
    start, end = NwsPublicClient._target_bounds(target_date)
    assert (start, end) == (expected_start, expected_end)
    assert end - start == duration


@pytest.mark.asyncio
async def test_fetch_daily_label_uses_cli_nyc_product_identity() -> None:
    client, factory = _client()

    label = await client.fetch_daily_label(target_date=date(2026, 7, 13))

    assert label is not None
    assert label.station_id == "KNYC"
    assert label.official_high_f == Decimal("89")
    assert label.product_id == "fixture-cli-20260713"
    assert label.source_url == PRODUCT_URL
    assert len(label.evidence_id) == 64
    assert [call["url"] for call in factory.calls] == [PRODUCTS_URL, PRODUCT_URL]


@pytest.mark.asyncio
async def test_daily_label_returns_none_when_no_product_matches_date() -> None:
    product = _fixture("nws_cli_product.json")
    product["productText"] = product["productText"].replace("JULY 13 2026", "JULY 12 2026")
    client, _ = _client(**{PRODUCT_URL: [FakeResponse(product)]})
    assert await client.fetch_daily_label(target_date=date(2026, 7, 13)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixture_name", "mutate", "message"),
    [
        ("nws_points.json", lambda p: p["properties"].update(forecastGridData="https://evil.example/grid"), "origin"),
        ("nws_points.json", lambda p: p["properties"].update(forecastGridData=f"{GRID_URL}?bad=1"), "origin"),
        ("nws_points.json", lambda p: p["properties"].update(forecastHourly="https://evil.example/hourly"), "origin"),
        ("nws_points.json", lambda p: p["properties"].update(forecastHourly=f"{HOURLY_URL}#bad"), "origin"),
        ("nws_grid.json", lambda p: p["properties"]["temperature"].update(uom="wmoUnit:degF"), "unit"),
        ("nws_grid.json", lambda p: p["properties"]["temperature"]["values"][0].update(validTime="bad"), "interval"),
        ("nws_hourly.json", lambda p: p["properties"]["periods"][0].update(temperatureUnit="C"), "unit"),
        ("nws_observations.json", lambda p: p["features"][0]["properties"]["temperature"].update(qualityControl="Z"), "quality"),
        ("nws_observations.json", lambda p: p["features"][0]["properties"].update(station="https://api.weather.gov/stations/KLGA"), "station"),
    ],
)
async def test_capture_rejects_off_origin_units_intervals_qc_and_identity(
    fixture_name: str, mutate, message: str
) -> None:
    payload = _fixture(fixture_name)
    mutate(payload)
    url = {
        "nws_points.json": POINTS_URL,
        "nws_grid.json": GRID_URL,
        "nws_hourly.json": HOURLY_URL,
        "nws_observations.json": OBS_URL,
    }[fixture_name]
    client, _ = _client(**{url: [FakeResponse(payload)]})
    with pytest.raises(NwsPublicClientError, match=message):
        await client.fetch_capture_bundle(target_date=date(2026, 7, 13))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixture_name", "url", "mutate", "message"),
    [
        (
            "nws_points.json",
            POINTS_URL,
            lambda p: p.update(id=f"{POINTS_URL}/"),
            "point.*identity",
        ),
        (
            "nws_grid.json",
            GRID_URL,
            lambda p: p.update(id=f"{GRID_URL}/other"),
            "grid.*identity",
        ),
        (
            "nws_grid.json",
            GRID_URL,
            lambda p: p["properties"].update({"@id": f"{GRID_URL}?bad=1"}),
            "grid.*identity",
        ),
        (
            "nws_hourly.json",
            HOURLY_URL,
            lambda p: p.update(id=f"{HOURLY_URL}/other"),
            "hourly.*identity",
        ),
        (
            "nws_observations.json",
            OBS_URL,
            lambda p: p["features"][0].update(
                id=f"{ORIGIN}/stations/KNYC/observations/2026-07-13T04:51:00+00:00?bad=1"
            ),
            "observation.*identity",
        ),
        (
            "nws_observations.json",
            OBS_URL,
            lambda p: p["features"][0].update(
                id=f"{ORIGIN}/stations/KNYC/observations/../other"
            ),
            "observation.*identity",
        ),
    ],
)
async def test_capture_rejects_response_source_identity_mismatches(
    fixture_name: str, url: str, mutate, message: str
) -> None:
    payload = _fixture(fixture_name)
    mutate(payload)
    client, _ = _client(**{url: [FakeResponse(payload)]})
    with pytest.raises(NwsPublicClientError, match=message):
        await client.fetch_capture_bundle(target_date=date(2026, 7, 13))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timestamp",
    ["2026-07-13T03:59:59+00:00", "2026-07-14T04:00:00+00:00"],
)
async def test_capture_rejects_observations_outside_end_exclusive_target_bounds(
    timestamp: str,
) -> None:
    payload = _fixture("nws_observations.json")
    payload["features"][0]["id"] = f"{ORIGIN}/stations/KNYC/observations/{timestamp}"
    payload["features"][0]["properties"]["timestamp"] = timestamp
    client, _ = _client(**{OBS_URL: [FakeResponse(payload)]})
    with pytest.raises(NwsPublicClientError, match="target bounds"):
        await client.fetch_capture_bundle(target_date=date(2026, 7, 13))


@pytest.mark.asyncio
async def test_capture_accepts_observation_at_inclusive_target_start() -> None:
    timestamp = "2026-07-13T04:00:00+00:00"
    payload = _fixture("nws_observations.json")
    payload["features"][0]["id"] = f"{ORIGIN}/stations/KNYC/observations/{timestamp}"
    payload["features"][0]["properties"]["timestamp"] = timestamp
    client, _ = _client(**{OBS_URL: [FakeResponse(payload)]})

    bundle = await client.fetch_capture_bundle(target_date=date(2026, 7, 13))

    assert bundle.observations[0].measured_at == datetime(
        2026, 7, 13, 4, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name,key", [("nws_grid.json", "values"), ("nws_hourly.json", "periods")])
async def test_capture_rejects_missing_data(fixture_name: str, key: str) -> None:
    payload = _fixture(fixture_name)
    if fixture_name == "nws_grid.json":
        payload["properties"]["temperature"][key] = []
        url = GRID_URL
    else:
        payload["properties"][key] = []
        url = HOURLY_URL
    client, _ = _client(**{url: [FakeResponse(payload)]})
    with pytest.raises(NwsPublicClientError, match="missing"):
        await client.fetch_capture_bundle(target_date=date(2026, 7, 13))


@pytest.mark.asyncio
async def test_rejects_http_redirect_and_product_id_mismatch() -> None:
    client, _ = _client(**{POINTS_URL: [FakeResponse({}, status=302, headers={"Location": "https://evil.example"})]})
    with pytest.raises(NwsPublicClientError):
        await client.fetch_capture_bundle(target_date=date(2026, 7, 13))

    product = _fixture("nws_cli_product.json")
    product["id"] = "other"
    client, _ = _client(**{PRODUCT_URL: [FakeResponse(product)]})
    with pytest.raises(NwsPublicClientError, match="product"):
        await client.fetch_daily_label(target_date=date(2026, 7, 13))


@pytest.mark.asyncio
async def test_rejects_transport_timeout_and_duplicate_observation() -> None:
    client, _ = _client(**{POINTS_URL: [TimeoutError("eight seconds elapsed")]})
    with pytest.raises(NwsPublicClientError, match="request failed"):
        await client.fetch_capture_bundle(target_date=date(2026, 7, 13))

    observations = _fixture("nws_observations.json")
    observations["features"].append(deepcopy(observations["features"][0]))
    client, _ = _client(**{OBS_URL: [FakeResponse(observations)]})
    with pytest.raises(NwsPublicClientError, match="duplicate observation"):
        await client.fetch_capture_bundle(target_date=date(2026, 7, 13))


@pytest.mark.asyncio
async def test_rejects_non_knyc_station_before_http() -> None:
    client, factory = _client()
    with pytest.raises(NwsPublicClientError, match="KNYC"):
        await client.fetch_capture_bundle(target_date=date(2026, 7, 13), station_id="KLGA")
    assert factory.calls == []
