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
    canonical_json,
    canonical_sha256,
    derive_weather_features,
    parse_event_target_date,
    select_due_horizon,
    validate_outcome_batch,
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
    OutcomeBatch,
    OutcomeCheck,
    OutcomeTarget,
    RetrievedEvent,
)

CAPTURE_CADENCE_SECONDS = 300
MAX_EVENTS_PER_CYCLE = 2
NETWORK_BUILD_BUDGET_SECONDS = 20
LABEL_BUILD_BUDGET_SECONDS = 20
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


@dataclass(frozen=True)
class _LabelPersistencePlan:
    event_ticker: str
    batch: OutcomeBatch | None
    check: OutcomeCheck | None
    seal_at: datetime | None


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
        runtime_logger: logging.Logger | None = None,
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
        self._logger = runtime_logger or logger
        self._model_version = model_version
        self._fee_schedule_version = fee_schedule_version
        self._now = now
        self._sleep = sleep

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        initialized = False
        while stop_event is None or not stop_event.is_set():
            if not initialized:
                try:
                    initialization = asyncio.create_task(
                        self._store.initialize(),
                        name="kxhighny-shadow-initialize",
                    )
                    try:
                        await asyncio.shield(initialization)
                    except asyncio.CancelledError:
                        await self._drain_cancelled_persistence(initialization)
                        raise
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._logger.warning(
                        "KXHIGHNY weather shadow failed stage=initialize error=%s",
                        type(exc).__name__,
                    )
                else:
                    initialized = True
                    self._logger.info("KXHIGHNY weather shadow initialized")
                if not initialized:
                    if stop_event is None or not stop_event.is_set():
                        await self._sleep(CAPTURE_CADENCE_SECONDS)
                    continue
            if stop_event is not None and stop_event.is_set():
                break
            capture_result, _ = await asyncio.gather(
                self._run_lane("capture", self.run_capture_once),
                self._run_lane("label", self.run_label_once),
            )
            if isinstance(capture_result, CaptureCycleResult):
                self._logger.info(
                    "KXHIGHNY weather shadow capture cycle "
                    "attempted=%d captured=%d skipped=%d",
                    capture_result.attempted,
                    capture_result.captured,
                    capture_result.skipped,
                )
            if stop_event is None or not stop_event.is_set():
                await self._sleep(CAPTURE_CADENCE_SECONDS)

    async def _run_lane(
        self,
        name: str,
        operation: Callable[[], Awaitable[object]],
    ) -> object | None:
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.warning(
                "KXHIGHNY weather shadow %s cycle failed (%s)",
                name,
                type(exc).__name__,
            )
            return None

    async def run_label_once(self) -> None:
        """Revalidate captured outcome targets within an independent budget."""
        targets = await self._store.list_outcome_targets(self._now())
        plans: list[_LabelPersistencePlan] = []
        try:
            async with asyncio.timeout(LABEL_BUILD_BUDGET_SECONDS):
                for target in targets:
                    try:
                        plan = await self._prepare_label(target)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._logger.warning(
                            "KXHIGHNY weather shadow event label failed (%s)",
                            type(exc).__name__,
                        )
                    else:
                        if plan is not None:
                            plans.append(plan)
        except TimeoutError:
            self._logger.warning("KXHIGHNY weather shadow label budget expired")
            return
        for plan in plans:
            await self._persist_label_plan(plan)

    async def label_event(self, target: OutcomeTarget) -> None:
        """Validate and persist one captured event's public outcome evidence."""
        try:
            async with asyncio.timeout(LABEL_BUILD_BUDGET_SECONDS):
                plan = await self._prepare_label(target)
        except TimeoutError:
            self._logger.warning("KXHIGHNY weather shadow event label budget expired")
            return
        if plan is not None:
            await self._persist_label_plan(plan)

    async def _prepare_label(
        self, target: OutcomeTarget
    ) -> _LabelPersistencePlan | None:
        state = await self._store.label_state(target.event_ticker)
        if state.sealed or state.quarantined:
            return None
        event_payload = await self._label_markets.get_event(
            event_ticker=target.event_ticker
        )
        cli_product = await self._label_weather.fetch_daily_label(
            target_date=target.target_date,
            station_id=_STATION_ID,
        )
        if cli_product is None:
            return None
        fingerprints = await self._store.capture_fingerprints(target.event_ticker)
        batch = validate_outcome_batch(event_payload, cli_product, fingerprints)
        if batch.target_date != target.target_date:
            raise WeatherShadowValidationError("outcome target identity changed")
        if not state.labeled:
            return _LabelPersistencePlan(target.event_ticker, batch, None, None)
        if len(state.outcome_batch_ids) != 1:
            return None

        baseline_hash = state.outcome_batch_ids[0]
        observed_hash = batch.outcome_batch_id
        agrees = observed_hash == baseline_hash
        checked_at = self._now()
        check_date = checked_at.astimezone(timezone.utc).date()
        details = canonical_json(
            {
                "event_ticker": target.event_ticker,
                "official_evidence_id": cli_product.evidence_id,
                "official_product_id": cli_product.product_id,
                "official_retrieved_at": cli_product.retrieved_at,
                "settlement_observed_at": event_payload.retrieved_at,
            }
        )
        check = OutcomeCheck(
            check_id=canonical_sha256(
                {
                    "event_ticker": target.event_ticker,
                    "check_date_utc": check_date,
                    "observed_batch_hash": observed_hash,
                    "baseline_batch_hash": baseline_hash,
                }
            ),
            event_ticker=target.event_ticker,
            check_date_utc=check_date,
            checked_at=checked_at,
            check_kind="daily",
            observed_batch_hash=observed_hash,
            baseline_batch_hash=baseline_hash,
            agrees_with_baseline=agrees,
            details_json=details,
        )
        return _LabelPersistencePlan(
            target.event_ticker,
            None if agrees else batch,
            check,
            checked_at,
        )

    async def _persist_label_plan(self, plan: _LabelPersistencePlan) -> None:
        persistence = asyncio.create_task(
            self._execute_label_persistence(plan),
            name=f"kxhighny-shadow-label-persist:{plan.event_ticker}",
        )
        try:
            await asyncio.shield(persistence)
        except asyncio.CancelledError:
            await self._drain_cancelled_persistence(persistence)
            raise

    async def _execute_label_persistence(self, plan: _LabelPersistencePlan) -> None:
        if plan.batch is not None:
            await self._store.append_outcome_batch(plan.batch)
        if plan.check is not None:
            await self._store.append_outcome_check(plan.check)
        if plan.seal_at is not None:
            await self._store.try_seal_event(plan.event_ticker, plan.seal_at)

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
                    except WeatherShadowValidationError as exc:
                        self._logger.warning(
                            "KXHIGHNY weather shadow event capture failed "
                            "(%s: %s)",
                            type(exc).__name__,
                            exc,
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
                    except Exception as exc:
                        self._logger.warning(
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
            self._logger.warning("KXHIGHNY weather shadow network/build budget expired")
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
                self._logger.warning(
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
        persistence: asyncio.Task[object],
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
