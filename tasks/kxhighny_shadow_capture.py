"""Bounded, isolated orchestration for KXHIGHNY weather shadow captures."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from kalshi.public_market_data import PublicMarketDataReader
from tasks.kxhighny_shadow_validation import (
    WeatherShadowValidationError,
    build_capture_batch,
    canonical_sha256,
    derive_weather_features,
    parse_event_target_date,
    select_due_horizon,
)
from tasks.weather_shadow_store import CaptureWriteResult, WeatherShadowStore
from weather.nws_public_client import NwsPublicClient
from weather.shadow_models import (
    WEATHER_SHADOW_CAPTURE_MODEL_VERSION,
    WEATHER_SHADOW_FEE_SCHEDULE_VERSION,
    CaptureAttemptResult,
    CaptureBatch,
    CaptureCycleResult,
    HorizonBucket,
    RetrievedEvent,
)

CAPTURE_CADENCE_SECONDS = 300
MAX_EVENTS_PER_CYCLE = 2
NETWORK_BUILD_BUDGET_SECONDS = 20
_SERIES_TICKER = "KXHIGHNY"
_STATION_ID = "KNYC"

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class _PreparedCapture:
    event_ticker: str
    horizon_bucket: HorizonBucket
    batch: CaptureBatch | None
    skipped: CaptureAttemptResult | None = None


class WeatherShadowCaptureTask:
    """Capture complete public weather ladders without trading dependencies."""

    def __init__(
        self,
        *,
        store: WeatherShadowStore,
        capture_markets: PublicMarketDataReader,
        capture_weather: NwsPublicClient,
        label_markets: PublicMarketDataReader,
        label_weather: NwsPublicClient,
        model_version: str = WEATHER_SHADOW_CAPTURE_MODEL_VERSION,
        fee_schedule_version: str = WEATHER_SHADOW_FEE_SCHEDULE_VERSION,
        now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._capture_markets = capture_markets
        self._capture_weather = capture_weather
        self._label_markets = label_markets
        self._label_weather = label_weather
        self._model_version = model_version
        self._fee_schedule_version = fee_schedule_version
        self._now = now
        self._sleep = sleep

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        initialized = False
        while stop_event is None or not stop_event.is_set():
            if not initialized:
                try:
                    await self._store.initialize()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "KXHIGHNY weather shadow failed stage=initialize error=%s",
                        type(exc).__name__,
                    )
                else:
                    initialized = True
                if not initialized:
                    if stop_event is None or not stop_event.is_set():
                        await self._sleep(CAPTURE_CADENCE_SECONDS)
                    continue
            try:
                await self.run_capture_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "KXHIGHNY weather shadow capture cycle failed (%s)",
                    type(exc).__name__,
                )
            if stop_event is None or not stop_event.is_set():
                await self._sleep(CAPTURE_CADENCE_SECONDS)

    async def run_capture_once(self) -> CaptureCycleResult:
        prepared: list[_PreparedCapture] = []
        attempted = 0
        try:
            async with asyncio.timeout(NETWORK_BUILD_BUDGET_SECONDS):
                events = await self._capture_markets.list_active_events(
                    series_ticker=_SERIES_TICKER
                )
                cycle_now = self._now()
                due: list[tuple[RetrievedEvent, HorizonBucket]] = []
                seen: set[str] = set()
                for event in sorted(
                    events, key=lambda item: (item.close_time, item.event_ticker)
                ):
                    if event.event_ticker in seen:
                        continue
                    seen.add(event.event_ticker)
                    horizon = select_due_horizon(
                        cycle_now, event.close_time, frozenset()
                    )
                    if horizon is None:
                        continue
                    due.append((event, horizon))
                    if len(due) == MAX_EVENTS_PER_CYCLE:
                        break

                attempted = len(due)
                for event, horizon in due:
                    try:
                        prepared.append(await self._prepare_capture(event, horizon))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "KXHIGHNY weather shadow event capture failed (%s)",
                            type(exc).__name__,
                        )
                        prepared.append(
                            _PreparedCapture(
                                event.event_ticker,
                                horizon,
                                None,
                                CaptureAttemptResult(
                                    event.event_ticker, horizon, False, "ineligible"
                                ),
                            )
                        )
        except TimeoutError:
            logger.warning("KXHIGHNY weather shadow network/build budget expired")
            return CaptureCycleResult(
                attempted=attempted,
                captured=0,
                skipped=attempted,
            )

        results: list[CaptureAttemptResult] = []
        for item in prepared:
            if item.skipped is not None:
                results.append(item.skipped)
                continue
            assert item.batch is not None
            try:
                results.append(await self._persist(item.batch))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "KXHIGHNY weather shadow persistence failed (%s)",
                    type(exc).__name__,
                )
                results.append(
                    CaptureAttemptResult(
                        item.event_ticker,
                        item.horizon_bucket,
                        False,
                        "persistence_failed",
                    )
                )

        captured = sum(result.captured for result in results)
        return CaptureCycleResult(
            attempted=attempted,
            captured=captured,
            skipped=attempted - captured,
        )

    async def capture_event(
        self, event: RetrievedEvent, horizon: HorizonBucket
    ) -> CaptureAttemptResult:
        try:
            async with asyncio.timeout(NETWORK_BUILD_BUDGET_SECONDS):
                prepared = await self._prepare_capture(event, horizon)
        except TimeoutError:
            return CaptureAttemptResult(
                event.event_ticker, horizon, False, "budget_expired"
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return CaptureAttemptResult(event.event_ticker, horizon, False, "ineligible")

        if prepared.skipped is not None:
            return prepared.skipped
        assert prepared.batch is not None
        return await self._persist(prepared.batch)

    async def _prepare_capture(
        self, event: RetrievedEvent, horizon: HorizonBucket
    ) -> _PreparedCapture:
        key = canonical_sha256(
            {
                "event_ticker": event.event_ticker,
                "horizon_bucket": horizon,
                "model_version": self._model_version,
            }
        )
        state = await self._store.capture_key_state(key)
        if state.claimed:
            return _PreparedCapture(
                event.event_ticker,
                horizon,
                None,
                CaptureAttemptResult(
                    event.event_ticker,
                    horizon,
                    False,
                    "already_claimed",
                    state.snapshot_id,
                ),
            )
        if event.status != "open":
            return _PreparedCapture(
                event.event_ticker,
                horizon,
                None,
                CaptureAttemptResult(
                    event.event_ticker, horizon, False, "event_not_open"
                ),
            )

        target_date = parse_event_target_date(event.event_ticker)
        capture_started_at = self._now()
        payloads = await self._capture_weather.fetch_capture_bundle(
            target_date=target_date,
            station_id=_STATION_ID,
        )
        refreshed = await self._capture_markets.get_event(
            event_ticker=event.event_ticker
        )
        capture_finished_at = self._now()

        if refreshed.event_ticker != event.event_ticker or refreshed.status != "open":
            raise WeatherShadowValidationError(
                "event identity or open status changed during capture"
            )
        if not self._is_fully_enumerated(refreshed):
            raise WeatherShadowValidationError("event market enumeration is incomplete")

        features = derive_weather_features(
            payloads,
            target_date,
            capture_finished_at,
        )
        batch = build_capture_batch(
            event=refreshed,
            target_date=target_date,
            horizon_bucket=horizon,
            features=features,
            capture_started_at=capture_started_at,
            capture_finished_at=capture_finished_at,
            as_of=capture_finished_at,
            model_version=self._model_version,
            fee_schedule_version=self._fee_schedule_version,
        )
        return _PreparedCapture(event.event_ticker, horizon, batch)

    @staticmethod
    def _is_fully_enumerated(event: RetrievedEvent) -> bool:
        enumerated = event.market_tickers
        retrieved = tuple(item.market_ticker for item in event.markets)
        return (
            bool(enumerated)
            and len(enumerated) == len(set(enumerated))
            and len(retrieved) == len(set(retrieved))
            and set(enumerated) == set(retrieved)
        )

    async def _persist(self, batch: CaptureBatch) -> CaptureAttemptResult:
        persistence = asyncio.create_task(
            self._store.append_capture(batch),
            name=f"kxhighny-shadow-persist:{batch.capture_key}",
        )
        try:
            write_result = await asyncio.shield(persistence)
        except asyncio.CancelledError:
            await self._drain_cancelled_persistence(persistence)
            raise
        return self._capture_result(batch, write_result)

    @staticmethod
    async def _drain_cancelled_persistence(
        persistence: asyncio.Task[CaptureWriteResult],
    ) -> None:
        owner = asyncio.current_task()
        if owner is not None:
            while owner.cancelling():
                owner.uncancel()
        while not persistence.done():
            try:
                await asyncio.shield(persistence)
            except asyncio.CancelledError:
                if owner is not None:
                    while owner.cancelling():
                        owner.uncancel()
                continue
            except Exception:
                break
        if not persistence.cancelled():
            persistence.exception()

    @staticmethod
    def _capture_result(
        batch: CaptureBatch, write_result: CaptureWriteResult
    ) -> CaptureAttemptResult:
        captured = write_result.status != "conflict"
        return CaptureAttemptResult(
            batch.event_ticker,
            batch.horizon_bucket,
            captured,
            write_result.status,
            write_result.snapshot_id,
        )
