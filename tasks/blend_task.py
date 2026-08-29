"""Blend-lane orchestration task.

S3.4 lane meeting point: fast-lane analysis triggers this task, while the
accumulation and structural lanes provide contextual inputs. This module does
not execute trades, size positions, or place orders.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable, Protocol

from analysis import SignalAnalysis
from analysis import decision_blender
from analysis.decision_blender import BlendResult, LaneInput
from analysis.dossier_builder import half_life_for_regime, recency_score
from analysis.evidence_scorer import source_quality
from config import PAPER_MIN_EDGE, cfg
from kalshi import KalshiMarket
from polymarket.domain_key import pm_domain_key
from tasks import evidence_store
from tasks.capital_guard_shadow_capture import CapitalGuardShadowCaptureEnvelope
from tasks.evidence_store import DossierState, EvidenceRecord, StructuralPriorRecord
from tasks.g7_skip_evidence_capture import (
    G7SkipEvidenceCaptureEnvelope,
    decision_time_at_or_after_execution_observation,
)
from tasks.prequeue_book_provenance import (
    PrequeueBookProvenanceResult,
    unavailable_prequeue_book_provenance,
)
from tasks.side_calibration_quarantine import (
    SideCalibrationQuarantineDecisionContext,
    build_side_calibration_decision_context,
)
from tasks.trade_readiness_gate import (
    G1_CONFIDENCE_THRESHOLD,
    G1_FAILSAFE_CONFIDENCE_THRESHOLD,
    G4_REGIME_CONFIDENCE_THRESHOLD,
    G7_MAX_OPEN_EXPOSURE_DRAWDOWN_PCT,
    ReadinessDecision,
    evaluate_readiness,
)
from trading.orderbook import ExecutableLiquidity
from utils.logger import get_logger, trade_log, write_trade_log_async
from utils.lifecycle import strict_optional_bool


log = get_logger("blend_task")
_PROCESS_CONTROL_EXCEPTIONS = (KeyboardInterrupt, SystemExit, GeneratorExit)
_READINESS_LIQUIDITY_UNSET = object()
_PREQUEUE_BOOK_PROVENANCE_TIMEOUT_SECONDS = 1.0


def _log_capital_guard_capture_diagnostic_noexcept(
    message: str,
    *args: object,
) -> None:
    try:
        log.warning(message, *args)
    except _PROCESS_CONTROL_EXCEPTIONS:
        raise
    except BaseException:
        return


def _log_g7_skip_evidence_capture_diagnostic_noexcept(
    message: str,
    *args: object,
) -> None:
    try:
        log.warning(message, *args)
    except _PROCESS_CONTROL_EXCEPTIONS:
        raise
    except BaseException:
        return


def _log_side_calibration_quarantine_diagnostic_noexcept(
    message: str,
    *args: object,
) -> None:
    try:
        log.warning(message, *args)
    except _PROCESS_CONTROL_EXCEPTIONS:
        raise
    except BaseException:
        return


def _current_task_is_cancelling() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


def _observe_prequeue_provider_result(task: asyncio.Future[object]) -> None:
    try:
        task.exception()
    except BaseException:
        return


class BlendTaskError(Exception):
    """Base exception for blend-task orchestration failures."""


class BlendComputationError(BlendTaskError):
    """Raised when the pure blender cannot produce a result."""


class ReadinessEvaluationError(BlendTaskError):
    """Raised when readiness evaluation fails on malformed integration input."""


class QueueInsertionError(BlendTaskError):
    """Raised when an approved candidate cannot be enqueued."""


class CalibrationLike(Protocol):
    def get_scaling_factor(self, lane: str) -> float: ...


class EvidenceStoreLike(Protocol):
    async def get_dossier(self, market_ticker: str) -> DossierState | None: ...

    async def get_structural_prior(
        self,
        market_ticker: str,
    ) -> StructuralPriorRecord | None: ...

    async def get_recent_evidence(
        self,
        market_ticker: str,
        *,
        limit: int = 100,
    ) -> list[EvidenceRecord]: ...


class BlendDecisionLogger(Protocol):
    def log_blend_decision(
        self,
        *,
        market_ticker: str,
        fast_lane_p: float | None,
        fast_lane_confidence: float | None,
        accumulation_p: float | None,
        accumulation_confidence: float | None,
        structural_p: float | None,
        structural_confidence: float | None,
        regime_weights: dict[str, float],
        regime_confidence: float,
        blended_p: float,
        blended_confidence: float,
        disagreement_score: float,
        blend_mode: str,
        trade_considered: bool,
        trade_blocked_reason: str | None,
        evidence_ids_contributing: list[str],
        venue: str | None = None,
        recency_score: float | None = None,
        recency_threshold: float | None = None,
        recency_distance: float | None = None,
        lifecycle_id: str | None = None,
        settlement_source_match: bool | None = None,
        g7_mark_snapshot: dict[str, Any] | None = None,
    ) -> None: ...

    def log_skipped(self, **kwargs: Any) -> None: ...

    def log_gate_summary(self, **kwargs: Any) -> None: ...

    def log_lane_skipped(self, **kwargs: Any) -> None: ...


@dataclass(frozen=True)
class TradeCandidate:
    """Queue payload produced only after the readiness gate passes.

    P-5 LD-10 / F-11 P1-A: carries ``executed_price_cents`` (the executed-side
    ask in cents) as the canonical post-P0 entry price for the chosen side.
    The ``entry_price_cents`` field is the float mirror used by the executor
    and historical-event readers; the deprecated ``market_yes_price`` alias
    has been removed in P1-A. Field name matches Position.entry_price_cents
    for in-process consistency; F-11 P1-B carries the same canonical name into
    the paper trade DB column.
    """

    fast_lane_analysis: SignalAnalysis
    market: KalshiMarket
    blended_probability: float
    entry_price_cents: float
    side: str
    signal_meta: dict[str, Any]
    readiness_decision: ReadinessDecision
    executed_price_cents: int | None = None


@dataclass(frozen=True)
class BlendTaskResult:
    market_ticker: str
    blend_result: BlendResult
    readiness_decision: ReadinessDecision
    trade_blocked_reason: str | None
    candidate: TradeCandidate | None
    enqueued: bool

    @property
    def ready(self) -> bool:
        return self.candidate is not None and self.enqueued


StructuralStabilityResolver = Callable[[str], bool | Awaitable[bool]]


@dataclass(frozen=True)
class OpenExposureDrawdownSnapshot:
    """Compact mark provenance carried with a G7 readiness decision."""

    drawdown_pct: float
    configured_bankroll: float | None
    notional_bankroll: float | None
    marked_value: float | None
    total_entry_cost: float | None
    unknown_entry_cost: float | None
    priced_count: int | None
    unpriced_count: int | None
    snapshot_fallback_count: int | None
    valuation_basis: str
    unpriced_positions_valued_at_zero: bool | None
    provider: str
    fallback_status: str
    observed_at: str
    valuation_as_of: str | None

    def as_log_record(self, *, threshold_pct: float) -> dict[str, Any]:
        return {
            "drawdown_pct": self.drawdown_pct,
            "threshold_pct": threshold_pct,
            "configured_bankroll": self.configured_bankroll,
            "notional_bankroll": self.notional_bankroll,
            "marked_value": self.marked_value,
            "total_entry_cost": self.total_entry_cost,
            "unknown_entry_cost": self.unknown_entry_cost,
            "priced_count": self.priced_count,
            "unpriced_count": self.unpriced_count,
            "snapshot_fallback_count": self.snapshot_fallback_count,
            "valuation_basis": self.valuation_basis,
            "unpriced_positions_valued_at_zero": self.unpriced_positions_valued_at_zero,
            "provider": self.provider,
            "fallback_status": self.fallback_status,
            "observed_at": self.observed_at,
            "valuation_as_of": self.valuation_as_of,
        }


OpenExposureDrawdownProvider = Callable[
    [],
    float | None | OpenExposureDrawdownSnapshot | Awaitable[float | None | OpenExposureDrawdownSnapshot],
]

ExecutionLiquidityProvider = Callable[
    [SignalAnalysis],
    ExecutableLiquidity | Awaitable[ExecutableLiquidity],
]

PrequeueBookProvenanceProvider = Callable[
    [SignalAnalysis],
    PrequeueBookProvenanceResult | Awaitable[PrequeueBookProvenanceResult],
]


class CapitalGuardCaptureSinkLike(Protocol):
    async def capture(self, envelope: CapitalGuardShadowCaptureEnvelope) -> object: ...


class G7SkipEvidenceCaptureSinkLike(Protocol):
    async def capture(self, envelope: G7SkipEvidenceCaptureEnvelope) -> object: ...


class SideCalibrationQuarantineSinkLike(Protocol):
    async def capture(
        self,
        context: SideCalibrationQuarantineDecisionContext,
    ) -> object: ...


class BlendTask:
    """Read lane context, blend, gate, log, and enqueue approved candidates."""

    def __init__(
        self,
        *,
        trading_queue: asyncio.Queue[TradeCandidate],
        store: EvidenceStoreLike | None = None,
        logger: BlendDecisionLogger = trade_log,
        blender: Callable[..., BlendResult] = decision_blender.blend,
        readiness_evaluator: Callable[
            [Any, float],
            ReadinessDecision,
        ] = evaluate_readiness,
        structural_stability_resolver: StructuralStabilityResolver | None = None,
        open_exposure_drawdown_provider: OpenExposureDrawdownProvider | None = None,
        is_paper_mode: bool | None = None,
        now: Callable[[], datetime] | None = None,
        calibration: CalibrationLike | None = None,
        capital_guard_capture_sink: CapitalGuardCaptureSinkLike | None = None,
        g7_skip_evidence_capture_sink: G7SkipEvidenceCaptureSinkLike | None = None,
        execution_liquidity_provider: ExecutionLiquidityProvider | None = None,
        runtime_paper_cohort_id: str | None = None,
        runtime_paper_cohort_kind: str | None = None,
        prequeue_book_provenance_provider: PrequeueBookProvenanceProvider | None = None,
        side_calibration_quarantine_sink: SideCalibrationQuarantineSinkLike | None = None,
    ) -> None:
        self._trading_queue = trading_queue
        self._store = store if store is not None else evidence_store.default_store()
        self._logger = logger
        self._blender = blender
        self._readiness_evaluator = readiness_evaluator
        self._structural_stability_resolver = structural_stability_resolver
        self._open_exposure_drawdown_provider = open_exposure_drawdown_provider
        self._calibration = calibration
        self._capital_guard_capture_sink = capital_guard_capture_sink
        self._g7_skip_evidence_capture_sink = g7_skip_evidence_capture_sink
        self._execution_liquidity_provider = execution_liquidity_provider
        self._runtime_paper_cohort_id = runtime_paper_cohort_id
        self._runtime_paper_cohort_kind = runtime_paper_cohort_kind
        self._prequeue_book_provenance_provider = prequeue_book_provenance_provider
        self._side_calibration_quarantine_sink = side_calibration_quarantine_sink
        self._prequeue_book_provenance_provider_task: (
            asyncio.Future[PrequeueBookProvenanceResult] | None
        ) = None
        self._is_paper_mode = cfg.is_paper_trading if is_paper_mode is None else is_paper_mode
        self._now = now if now is not None else lambda: datetime.now(UTC)
        # PROFIT-EXEC-002 series-correlation guard state. Maps series prefix
        # (e.g. "KXFISAEXTEND") to the `time.monotonic()` value at which a
        # candidate from that series was last successfully enqueued. The
        # guard suppresses subsequent candidates from the same prefix
        # within `cfg.series_correlation_window_seconds`.
        self._recent_series_enqueues: dict[str, float] = {}

    async def process_fast_lane_result(
        self,
        fast_lane_result: SignalAnalysis,
    ) -> BlendTaskResult:
        """Evaluate one fast-lane result and enqueue only if readiness passes."""
        if not self._has_valid_executed_price_cents(fast_lane_result.executed_price_cents):
            return await self._invalid_executed_price_result(fast_lane_result)
        market = fast_lane_result.market
        ticker = market.ticker
        dossier, structural_prior, recent_records = await self._read_lane_context(ticker)
        regime_weights = _regime_weights(market)
        regime_confidence = _regime_confidence(regime_weights)
        default_min_edge = PAPER_MIN_EDGE if self._is_paper_mode else cfg.min_edge

        blend_result = self._blend(
            fast_lane_result=fast_lane_result,
            dossier=dossier,
            structural_prior=structural_prior,
            regime_weights=regime_weights,
            regime_confidence=regime_confidence,
            default_min_edge=default_min_edge,
            structural_stable=await self._structural_stable(ticker),
        )
        await self._emit_lane_skips(
            ticker=ticker,
            dossier=dossier,
            structural_prior=structural_prior,
        )

        decision_at = self._now()
        open_exposure_snapshot = await self._open_exposure_drawdown_snapshot(observed_at=decision_at)
        g7_mark_snapshot = (
            open_exposure_snapshot.as_log_record(threshold_pct=G7_MAX_OPEN_EXPOSURE_DRAWDOWN_PCT)
            if open_exposure_snapshot is not None
            else None
        )
        use_execution_liquidity = (
            isinstance(market, KalshiMarket)
            and self._execution_liquidity_provider is not None
            and blend_result.trade_blocked_reason is None
        )
        readiness_input = _readiness_input(
            blend_result=blend_result,
            dossier=dossier,
            recent_records=recent_records,
            trigger_record=_trigger_evidence_record(fast_lane_result),
            market=market,
            analysis=fast_lane_result,
            default_min_edge=default_min_edge,
            now=decision_at,
            open_exposure_drawdown_pct=(
                open_exposure_snapshot.drawdown_pct if open_exposure_snapshot is not None else None
            ),
            market_liquidity_override=(
                None if use_execution_liquidity else _READINESS_LIQUIDITY_UNSET
            ),
        )
        try:
            readiness = self._readiness_evaluator(readiness_input, regime_confidence)
        except Exception as exc:
            raise ReadinessEvaluationError(f"readiness evaluation failed for {ticker}: {exc}") from exc
        if use_execution_liquidity and readiness.trade_blocked_reason is None:
            execution_liquidity = await self._execution_liquidity_for(fast_lane_result)
            liquidity_override: float | None = (
                float(execution_liquidity.executable_notional)
                if execution_liquidity is not None
                else _event_news_research_top_notional(market, fast_lane_result)
            )
            if execution_liquidity is None and liquidity_override is not None:
                self._set_execution_liquidity_meta(
                    fast_lane_result,
                    {
                        "source": "rest_top_size",
                        "status": "fallback",
                        "executable_notional": liquidity_override,
                        "reason": "orderbook_unavailable",
                    },
                )
            readiness_input = _readiness_input(
                blend_result=blend_result,
                dossier=dossier,
                recent_records=recent_records,
                trigger_record=_trigger_evidence_record(fast_lane_result),
                market=market,
                analysis=fast_lane_result,
                default_min_edge=default_min_edge,
                now=decision_at,
                open_exposure_drawdown_pct=(
                    open_exposure_snapshot.drawdown_pct if open_exposure_snapshot is not None else None
                ),
                market_liquidity_override=(
                    0.0 if liquidity_override is None else liquidity_override
                ),
            )
            try:
                readiness = self._readiness_evaluator(readiness_input, regime_confidence)
            except Exception as exc:
                raise ReadinessEvaluationError(
                    f"readiness evaluation failed for {ticker}: {exc}"
                ) from exc
        source_meta = fast_lane_result.signal_meta if isinstance(fast_lane_result.signal_meta, dict) else {}
        raw_lifecycle_id = source_meta.get("lifecycle_id")
        lifecycle_id = (
            raw_lifecycle_id.strip() if isinstance(raw_lifecycle_id, str) and raw_lifecycle_id.strip() else None
        )
        settlement_source_match = strict_optional_bool(source_meta.get("settlement_source_match"))
        await self._emit_gate_summary(
            ticker=ticker,
            readiness=readiness,
            blend_result=blend_result,
            g7_mark_snapshot=g7_mark_snapshot,
            lifecycle_id=lifecycle_id,
            settlement_source_match=settlement_source_match,
        )

        trade_blocked_reason = blend_result.trade_blocked_reason or readiness.trade_blocked_reason

        # PROFIT-EXEC-002 series-correlation guard. Only fires if no upstream
        # block already exists; the first candidate in a same-series burst
        # still trades, subsequent ones within the configured window are
        # suppressed via the OBS-003 SKIPPED stream.
        series_prefix = _series_prefix(ticker)
        if trade_blocked_reason is None:
            window = cfg.series_correlation_window_seconds
            if window > 0:
                last_enqueued_ts = self._recent_series_enqueues.get(series_prefix)
                if last_enqueued_ts is not None and time.monotonic() - last_enqueued_ts < window:
                    trade_blocked_reason = "series_correlation_in_window"

        evidence_ids = _evidence_ids_for_blend(
            recent_records=recent_records,
            trigger_evidence_id=(fast_lane_result.signal_meta or {}).get("trigger_evidence_id"),
            include_recent=dossier is not None,
        )
        venue = (
            _venue_string(
                getattr(fast_lane_result, "venue", None)
                or getattr(fast_lane_result.market, "venue", None)
                or getattr(fast_lane_result.market, "report_venue", None)
            )
            or "kalshi"
        )
        await self._emit_blend_decision(
            ticker=ticker,
            venue=venue,
            blend_result=blend_result,
            regime_weights=regime_weights,
            regime_confidence=regime_confidence,
            trade_blocked_reason=trade_blocked_reason,
            evidence_ids=evidence_ids,
            readiness=readiness,
            lifecycle_id=lifecycle_id,
            settlement_source_match=settlement_source_match,
            g7_mark_snapshot=g7_mark_snapshot,
        )

        if trade_blocked_reason is not None:
            await self._emit_skipped(
                ticker=ticker,
                blend_result=blend_result,
                readiness=readiness,
                trade_blocked_reason=trade_blocked_reason,
                fast_lane_result=fast_lane_result,
                venue=venue,
                g7_mark_snapshot=g7_mark_snapshot,
            )
            await self._capture_capital_guard_shadow(
                fast_lane_result=fast_lane_result,
                blend_result=blend_result,
                readiness=readiness,
                readiness_input=readiness_input,
                regime_weights=regime_weights,
                regime_confidence=regime_confidence,
                trade_blocked_reason=trade_blocked_reason,
                venue=venue,
                market_family=series_prefix,
                lifecycle_id=lifecycle_id,
                decision_at=decision_at,
                default_min_edge=default_min_edge,
            )
            await self._capture_g7_skip_evidence(
                fast_lane_result=fast_lane_result,
                readiness=readiness,
                readiness_input=readiness_input,
                trade_blocked_reason=trade_blocked_reason,
                venue=venue,
                market_family=series_prefix,
                lifecycle_id=lifecycle_id,
                decision_at=decision_at,
            )
            return BlendTaskResult(
                market_ticker=ticker,
                blend_result=blend_result,
                readiness_decision=readiness,
                trade_blocked_reason=trade_blocked_reason,
                candidate=None,
                enqueued=False,
            )

        candidate = _trade_candidate(
            fast_lane_result=fast_lane_result,
            blend_result=blend_result,
            readiness=readiness,
            regime_weights=regime_weights,
            regime_confidence=regime_confidence,
        )
        await self._project_prequeue_book_provenance(candidate)
        quarantine_reason = await self._capture_side_calibration_quarantine(
            candidate=candidate,
            decision_at=decision_at,
            dossier=dossier,
            evidence_ids=tuple(evidence_ids),
        )
        if quarantine_reason is not None:
            await self._emit_skipped(
                ticker=ticker,
                blend_result=blend_result,
                readiness=readiness,
                trade_blocked_reason=quarantine_reason,
                fast_lane_result=fast_lane_result,
                venue=venue,
                g7_mark_snapshot=g7_mark_snapshot,
            )
            return BlendTaskResult(
                market_ticker=ticker,
                blend_result=blend_result,
                readiness_decision=readiness,
                trade_blocked_reason=quarantine_reason,
                candidate=None,
                enqueued=False,
            )
        # PROFIT-EXEC-002: record this enqueue as the latest for the series
        # prefix BEFORE the queue put so the guard reflects state-on-attempt.
        # If we recorded AFTER the put, a CancelledError (or other exception)
        # between put and record would leave the candidate enqueued but the
        # guard state unrecorded -- the next same-series candidate within the
        # window would pass the guard, re-introducing the FISA multi-trade
        # failure mode. Revert on enqueue failure via .pop() (tolerates
        # already-reverted state) to keep dict consistent with the queue.
        # (silent-failure-hunter finding 3 / EXEC-002.)
        self._recent_series_enqueues[series_prefix] = time.monotonic()
        try:
            await self._trading_queue.put(candidate)
        except Exception as exc:
            log.warning(
                "EXEC-002 reverting series guard for %s after queue failure: %s",
                series_prefix,
                exc,
            )
            self._recent_series_enqueues.pop(series_prefix, None)
            raise QueueInsertionError(f"failed to enqueue {ticker}: {exc}") from exc

        return BlendTaskResult(
            market_ticker=ticker,
            blend_result=blend_result,
            readiness_decision=readiness,
            trade_blocked_reason=None,
            candidate=candidate,
            enqueued=True,
        )

    async def _capture_side_calibration_quarantine(
        self,
        *,
        candidate: TradeCandidate,
        decision_at: datetime,
        dossier: DossierState | None,
        evidence_ids: tuple[str, ...],
    ) -> str | None:
        """Capture the frozen candidate or block admission when capture cannot complete."""

        sink = self._side_calibration_quarantine_sink
        if sink is None:
            return None
        context = build_side_calibration_decision_context(
            decision_at=decision_at,
            candidate=candidate,
            dossier=dossier,
            evidence_ids=evidence_ids,
        )
        try:
            result = await sink.capture(context)
        except _PROCESS_CONTROL_EXCEPTIONS:
            raise
        except asyncio.CancelledError:
            if _current_task_is_cancelling():
                raise
            _log_side_calibration_quarantine_diagnostic_noexcept(
                "paper side-calibration capture cancelled for %s",
                candidate.market.ticker,
            )
            return "paper_side_calibration_capture_failed"
        except BaseException as exc:
            _log_side_calibration_quarantine_diagnostic_noexcept(
                "paper side-calibration capture failed for %s: %s",
                candidate.market.ticker,
                exc,
            )
            return "paper_side_calibration_capture_failed"
        if (
            getattr(result, "status", None) in {"inserted", "duplicate"}
            and getattr(result, "disposition", None) == "candidate"
        ):
            return "paper_side_calibration_unvalidated"
        _log_side_calibration_quarantine_diagnostic_noexcept(
            "paper side-calibration capture unscorable for %s: status=%r disposition=%r",
            candidate.market.ticker,
            getattr(result, "status", None),
            getattr(result, "disposition", None),
        )
        return "paper_side_calibration_capture_failed"

    async def _invalid_executed_price_result(
        self,
        fast_lane_result: SignalAnalysis,
    ) -> BlendTaskResult:
        """Return a terminal result without running lane, readiness, or G7 work."""
        reason = "invalid_executed_price"
        blend_result = BlendResult(
            blended_p=fast_lane_result.estimated_probability,
            blended_confidence=fast_lane_result.confidence,
            disagreement_score=0.0,
            blend_mode="dominant_lane",
            readiness_gate_min_edge_override=None,
            trade_blocked_reason=reason,
            fast_lane_p=fast_lane_result.estimated_probability,
            fast_lane_confidence=fast_lane_result.confidence,
            accumulation_p=None,
            accumulation_confidence=None,
            structural_p=None,
            structural_confidence=None,
        )
        readiness = ReadinessDecision(
            passed=False,
            failure_reasons=(reason,),
            trade_blocked_reason=reason,
            readiness_gate_min_edge_override=None,
            scaled_confidence=0.0,
            regime_confidence=0.0,
            fail_safe_active=False,
            applied_conditions=(),
        )
        market = fast_lane_result.market
        venue = (
            _venue_string(
                getattr(fast_lane_result, "venue", None)
                or getattr(market, "venue", None)
                or getattr(market, "report_venue", None)
            )
            or "kalshi"
        )
        await self._emit_skipped(
            ticker=market.ticker,
            blend_result=blend_result,
            readiness=readiness,
            trade_blocked_reason=reason,
            fast_lane_result=fast_lane_result,
            venue=venue,
            g7_mark_snapshot=None,
        )
        return BlendTaskResult(
            market_ticker=market.ticker,
            blend_result=blend_result,
            readiness_decision=readiness,
            trade_blocked_reason=reason,
            candidate=None,
            enqueued=False,
        )

    async def _execution_liquidity_for(
        self,
        analysis: SignalAnalysis,
    ) -> ExecutableLiquidity | None:
        provider = self._execution_liquidity_provider
        if provider is None:
            return None
        if not self._has_valid_executed_price_cents(analysis.executed_price_cents):
            self._set_execution_liquidity_meta(
                analysis,
                {
                    "source": "kalshi_orderbook",
                    "status": "unavailable",
                    "reason": "invalid_executed_price",
                },
            )
            return None
        try:
            result = provider(analysis)
            liquidity = await result if inspect.isawaitable(result) else result
            self._validate_execution_liquidity(liquidity, analysis)
        except Exception as exc:
            self._set_execution_liquidity_meta(
                analysis,
                {
                    "source": "kalshi_orderbook",
                    "status": "unavailable",
                    "reason": type(exc).__name__,
                },
            )
            return None

        self._set_execution_liquidity_meta(
            analysis,
            {
                "source": "kalshi_orderbook",
                "side": liquidity.side,
                "limit_price": float(liquidity.limit_price),
                "best_price": (
                    float(liquidity.best_price) if liquidity.best_price is not None else None
                ),
                "executable_quantity": float(liquidity.executable_quantity),
                "executable_notional": float(liquidity.executable_notional),
                "as_of": liquidity.as_of.isoformat(),
                "raw_payload_hash": liquidity.raw_payload_hash,
            },
        )
        return liquidity

    async def _project_prequeue_book_provenance(self, candidate: TradeCandidate) -> None:
        provider = self._prequeue_book_provenance_provider
        if provider is None:
            return
        analysis = candidate.fast_lane_analysis
        if self._prequeue_book_provenance_provider_is_pending():
            candidate.signal_meta["prequeue_book_provenance"] = (
                unavailable_prequeue_book_provenance(
                    analysis,
                    reason="provider_busy",
                ).as_metadata()
            )
            return
        try:
            result = provider(analysis)
            provenance = await self._await_prequeue_book_provenance(result)
            if not isinstance(provenance, PrequeueBookProvenanceResult):
                raise TypeError("prequeue book provenance provider returned an invalid result")
            metadata = provenance.as_metadata()
        except asyncio.CancelledError:
            if _current_task_is_cancelling():
                raise
            metadata = unavailable_prequeue_book_provenance(
                analysis,
                reason="provider_cancelled",
            ).as_metadata()
        except TimeoutError:
            metadata = unavailable_prequeue_book_provenance(
                analysis,
                reason="provider_timeout",
            ).as_metadata()
        except Exception as exc:
            metadata = unavailable_prequeue_book_provenance(
                analysis,
                reason=f"provider_error:{type(exc).__name__}",
            ).as_metadata()
        candidate.signal_meta["prequeue_book_provenance"] = metadata

    async def _await_prequeue_book_provenance(
        self,
        result: PrequeueBookProvenanceResult | Awaitable[PrequeueBookProvenanceResult],
    ) -> PrequeueBookProvenanceResult:
        if not inspect.isawaitable(result):
            return result
        provider_task = asyncio.ensure_future(result)
        self._prequeue_book_provenance_provider_task = provider_task
        provider_task.add_done_callback(self._on_prequeue_book_provenance_provider_done)
        try:
            done, _ = await asyncio.wait(
                {provider_task},
                timeout=_PREQUEUE_BOOK_PROVENANCE_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            provider_task.cancel()
            raise
        if not done:
            # Retain ownership: cancelling a provider awaiting to_thread cannot stop its worker.
            raise TimeoutError("prequeue book provenance provider timed out")
        return provider_task.result()

    def _prequeue_book_provenance_provider_is_pending(self) -> bool:
        provider_task = self._prequeue_book_provenance_provider_task
        if provider_task is None:
            return False
        if provider_task.done():
            self._prequeue_book_provenance_provider_task = None
            return False
        return True

    def _on_prequeue_book_provenance_provider_done(
        self,
        provider_task: asyncio.Future[PrequeueBookProvenanceResult],
    ) -> None:
        _observe_prequeue_provider_result(provider_task)
        if self._prequeue_book_provenance_provider_task is provider_task:
            self._prequeue_book_provenance_provider_task = None

    @staticmethod
    def _validate_execution_liquidity(
        liquidity: object,
        analysis: SignalAnalysis,
    ) -> None:
        if not isinstance(liquidity, ExecutableLiquidity):
            raise TypeError("execution liquidity provider returned an invalid result")
        if liquidity.market_ticker != analysis.market.ticker:
            raise ValueError("execution liquidity market does not match analysis")
        if liquidity.side != analysis.side:
            raise ValueError("execution liquidity side does not match analysis")
        executed_price_cents = analysis.executed_price_cents
        if not BlendTask._has_valid_executed_price_cents(executed_price_cents):
            raise ValueError("executed price must be an integer between 1 and 99 cents")
        expected_limit = Decimal(executed_price_cents) / Decimal("100")
        if liquidity.limit_price != expected_limit:
            raise ValueError("execution liquidity limit does not match analysis")

    @staticmethod
    def _has_valid_executed_price_cents(executed_price_cents: object) -> bool:
        return (
            not isinstance(executed_price_cents, bool)
            and isinstance(executed_price_cents, int)
            and 0 < executed_price_cents < 100
        )

    @staticmethod
    def _set_execution_liquidity_meta(
        analysis: SignalAnalysis,
        record: dict[str, object],
    ) -> None:
        source_meta = analysis.signal_meta if isinstance(analysis.signal_meta, dict) else {}
        analysis.signal_meta = {
            **source_meta,
            "g7_execution_liquidity": record,
        }

    async def _capture_capital_guard_shadow(
        self,
        *,
        fast_lane_result: SignalAnalysis,
        blend_result: BlendResult,
        readiness: ReadinessDecision,
        readiness_input: dict[str, Any],
        regime_weights: dict[str, float],
        regime_confidence: float,
        trade_blocked_reason: str,
        venue: str,
        market_family: str,
        lifecycle_id: str | None,
        decision_at: datetime,
        default_min_edge: float,
    ) -> None:
        sink = self._capital_guard_capture_sink
        if sink is None or "G7_open_exposure_drawdown" not in readiness.failure_reasons:
            return
        envelope = CapitalGuardShadowCaptureEnvelope(
            analysis=fast_lane_result,
            blend_result=blend_result,
            readiness_decision=readiness,
            readiness_input=readiness_input,
            regime_weights=regime_weights,
            regime_confidence=regime_confidence,
            trade_blocked_reason=trade_blocked_reason,
            venue=venue,
            market_family=market_family,
            lifecycle_id=lifecycle_id,
            decision_at=decision_at,
            default_min_edge=default_min_edge,
        )
        try:
            await sink.capture(envelope)
        except _PROCESS_CONTROL_EXCEPTIONS:
            raise
        except asyncio.CancelledError:
            _log_capital_guard_capture_diagnostic_noexcept(
                "capital guard shadow capture cancelled for %s",
                fast_lane_result.market.ticker,
            )
        except BaseException as exc:
            _log_capital_guard_capture_diagnostic_noexcept(
                "capital guard shadow capture failed for %s: %s",
                fast_lane_result.market.ticker,
                exc,
            )

    async def _capture_g7_skip_evidence(
        self,
        *,
        fast_lane_result: SignalAnalysis,
        readiness: ReadinessDecision,
        readiness_input: dict[str, Any],
        trade_blocked_reason: str,
        venue: str,
        market_family: str,
        lifecycle_id: str | None,
        decision_at: datetime,
    ) -> None:
        sink = self._g7_skip_evidence_capture_sink
        non_drawdown_g7_failures = tuple(
            reason
            for reason in readiness.failure_reasons
            if reason.startswith("G7_") and reason != "G7_open_exposure_drawdown"
        )
        if sink is None or trade_blocked_reason not in non_drawdown_g7_failures:
            return
        envelope = G7SkipEvidenceCaptureEnvelope(
            analysis=fast_lane_result,
            readiness_decision=readiness,
            readiness_input=readiness_input,
            trade_blocked_reason=trade_blocked_reason,
            venue=venue,
            market_family=market_family,
            lifecycle_id=lifecycle_id,
            decision_at=decision_time_at_or_after_execution_observation(
                decision_at,
                fast_lane_result,
            ),
            runtime_paper_cohort_id=self._runtime_paper_cohort_id,
            runtime_paper_cohort_kind=self._runtime_paper_cohort_kind,
        )
        try:
            await sink.capture(envelope)
        except _PROCESS_CONTROL_EXCEPTIONS:
            raise
        except asyncio.CancelledError:
            _log_g7_skip_evidence_capture_diagnostic_noexcept(
                "G7 skip evidence capture cancelled for %s",
                fast_lane_result.market.ticker,
            )
        except BaseException as exc:
            _log_g7_skip_evidence_capture_diagnostic_noexcept(
                "G7 skip evidence capture failed for %s: %s",
                fast_lane_result.market.ticker,
                exc,
            )

    async def _read_lane_context(
        self,
        market_ticker: str,
    ) -> tuple[DossierState | None, StructuralPriorRecord | None, list[EvidenceRecord]]:
        dossier, structural_prior, recent_records = await asyncio.gather(
            self._store.get_dossier(market_ticker),
            self._store.get_structural_prior(market_ticker),
            self._store.get_recent_evidence(market_ticker, limit=100),
        )
        return dossier, structural_prior, recent_records

    def _blend(
        self,
        *,
        fast_lane_result: SignalAnalysis,
        dossier: DossierState | None,
        structural_prior: StructuralPriorRecord | None,
        regime_weights: dict[str, float],
        regime_confidence: float,
        default_min_edge: float,
        structural_stable: bool,
    ) -> BlendResult:
        # PROFIT-BLENDER-001 (2026-05-24): mark accumulation/structural lanes
        # `signal_kind="fallback"` when they returned a default-neutral value
        # (p≈0.5 with low confidence). The blender uses this flag to exclude
        # them from the weighted-blend math when ≥1 real lane exists, so a
        # high-confidence fast-lane signal isn't diluted by lanes that have
        # no real data. The fast lane is always "real" — if there's no fast-
        # lane signal, the lane is None (excluded entirely).
        #
        # Heuristic thresholds tight by design: only flag lanes that are
        # clearly neutral defaults. A legitimately low-confidence real
        # signal at p≠0.5 stays classified as real and continues to
        # contribute. Spec: docs/superpowers/specs/2026-05-24-lane-aware-
        # blender-design.md.
        # Official research p is not an LLM guess. Applying the news-lane
        # calibration scale (and a fake accumulation dossier) dropped
        # T240/T7 below G1 after the research gate had already admitted.
        from utils.event_news_research import is_event_news_paper_cohort

        research_grade = (
            str(getattr(fast_lane_result, "signal_type", "") or "")
            == "research_decision_grade"
            and is_event_news_paper_cohort()
        )
        fast_scale = 1.0 if research_grade else self._calibration_scale("fast")
        fast = LaneInput(
            p=fast_lane_result.estimated_probability,
            confidence=fast_lane_result.confidence * fast_scale,
            lane_id="fast",
            signal_kind="real",
        )
        accumulation = (
            None if research_grade else self._build_accumulation_lane(dossier)
        )
        structural = (
            None if research_grade else self._build_structural_lane(structural_prior)
        )
        try:
            return self._blender(
                fast=fast,
                accumulation=accumulation,
                structural=structural,
                regime_weights=regime_weights,
                regime_confidence=regime_confidence,
                fast_signal_active=True,
                structural_stable=structural_stable,
                default_min_edge=default_min_edge,
            )
        except Exception as exc:
            raise BlendComputationError(
                f"blend computation failed for {fast_lane_result.market.ticker}: {exc}"
            ) from exc

    def _calibration_scale(self, lane: str) -> float:
        if self._calibration is None:
            return 1.0
        return self._calibration.get_scaling_factor(lane)

    async def _open_exposure_drawdown_snapshot(
        self,
        *,
        observed_at: datetime,
    ) -> OpenExposureDrawdownSnapshot | None:
        provider = self._open_exposure_drawdown_provider
        if provider is None:
            return None
        try:
            value = provider()
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, OpenExposureDrawdownSnapshot):
                drawdown_pct = value.drawdown_pct
                if (
                    isinstance(drawdown_pct, bool)
                    or not isinstance(drawdown_pct, (int, float))
                    or not math.isfinite(float(drawdown_pct))
                    or drawdown_pct < 0.0
                ):
                    raise ValueError("open exposure drawdown snapshot is invalid")
                return value
            if value is None:
                return None
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("open exposure drawdown is non-finite")
            return OpenExposureDrawdownSnapshot(
                drawdown_pct=max(0.0, value),
                configured_bankroll=None,
                notional_bankroll=None,
                marked_value=None,
                total_entry_cost=None,
                unknown_entry_cost=None,
                priced_count=None,
                unpriced_count=None,
                snapshot_fallback_count=None,
                valuation_basis="provider_scalar_unattributed",
                unpriced_positions_valued_at_zero=None,
                provider="legacy_drawdown_provider",
                fallback_status="scalar_only",
                observed_at=observed_at.isoformat(),
                valuation_as_of=None,
            )
        except Exception as exc:
            log.warning("open exposure drawdown unavailable; failing readiness closed: %s", exc)
            return OpenExposureDrawdownSnapshot(
                drawdown_pct=1.0,
                configured_bankroll=None,
                notional_bankroll=None,
                marked_value=None,
                total_entry_cost=None,
                unknown_entry_cost=None,
                priced_count=None,
                unpriced_count=None,
                snapshot_fallback_count=None,
                valuation_basis="unavailable",
                unpriced_positions_valued_at_zero=None,
                provider="blend_task",
                fallback_status="provider_error",
                observed_at=observed_at.isoformat(),
                valuation_as_of=None,
            )

    async def _open_exposure_drawdown_pct(self) -> float | None:
        snapshot = await self._open_exposure_drawdown_snapshot(observed_at=self._now())
        return snapshot.drawdown_pct if snapshot is not None else None

    # PROFIT-BLENDER-001 fallback heuristics. Tight thresholds by design:
    # a lane is flagged "fallback" only when p is very near 0.5 AND
    # confidence is low — i.e. the producer's "no data" default-neutral
    # branch. Legitimately low-confidence real signals at non-neutral p
    # stay classified as "real" and contribute to the blend.
    _FALLBACK_NEAR_NEUTRAL_TOLERANCE = 0.02
    _FALLBACK_CONFIDENCE_CEILING = 0.20

    def _classify_lane_signal_kind(
        self,
        p: float,
        confidence: float,
    ) -> str:
        """Return "fallback" if the lane returned a default-neutral value
        with low confidence, else "real". Used by accumulation and
        structural lanes — fast lane is always "real" when present."""
        near_neutral = abs(p - 0.5) < self._FALLBACK_NEAR_NEUTRAL_TOLERANCE
        low_confidence = confidence < self._FALLBACK_CONFIDENCE_CEILING
        return "fallback" if (near_neutral and low_confidence) else "real"

    def _build_accumulation_lane(
        self,
        dossier: DossierState | None,
    ) -> LaneInput | None:
        if dossier is None or dossier.current_estimate is None:
            return None
        confidence = dossier.confidence * self._calibration_scale("accumulation")
        return LaneInput(
            p=dossier.current_estimate,
            confidence=confidence,
            lane_id="accumulation",
            signal_kind=self._classify_lane_signal_kind(
                dossier.current_estimate,
                confidence,
            ),
        )

    def _build_structural_lane(
        self,
        structural_prior: StructuralPriorRecord | None,
    ) -> LaneInput | None:
        if structural_prior is None or structural_prior.prior_estimate is None:
            return None
        confidence = structural_prior.confidence * self._calibration_scale("structural")
        return LaneInput(
            p=structural_prior.prior_estimate,
            confidence=confidence,
            lane_id="structural",
            signal_kind=self._classify_lane_signal_kind(
                structural_prior.prior_estimate,
                confidence,
            ),
        )

    async def _structural_stable(self, market_ticker: str) -> bool:
        if self._structural_stability_resolver is None:
            return False
        result = self._structural_stability_resolver(market_ticker)
        if inspect.isawaitable(result):
            return bool(await result)
        return bool(result)

    async def _emit_blend_decision(
        self,
        *,
        ticker: str,
        venue: str | None,
        blend_result: BlendResult,
        regime_weights: dict[str, float],
        regime_confidence: float,
        trade_blocked_reason: str | None,
        evidence_ids: list[str],
        readiness: ReadinessDecision,
        lifecycle_id: str | None,
        settlement_source_match: bool | None,
        g7_mark_snapshot: dict[str, Any] | None,
    ) -> None:
        decision_kwargs: dict[str, Any] = {
            "market_ticker": ticker,
            "fast_lane_p": blend_result.fast_lane_p,
            "fast_lane_confidence": blend_result.fast_lane_confidence,
            "accumulation_p": blend_result.accumulation_p,
            "accumulation_confidence": blend_result.accumulation_confidence,
            "structural_p": blend_result.structural_p,
            "structural_confidence": blend_result.structural_confidence,
            "regime_weights": regime_weights,
            "regime_confidence": regime_confidence,
            "blended_p": blend_result.blended_p,
            "blended_confidence": blend_result.blended_confidence,
            "disagreement_score": blend_result.disagreement_score,
            "blend_mode": blend_result.blend_mode,
            "trade_considered": True,
            "trade_blocked_reason": trade_blocked_reason,
            "evidence_ids_contributing": evidence_ids,
            "venue": venue,
            "recency_score": readiness.recency_score,
            "recency_threshold": readiness.recency_threshold,
            "recency_distance": readiness.recency_distance,
            "lifecycle_id": lifecycle_id,
            "settlement_source_match": settlement_source_match,
        }
        if g7_mark_snapshot is not None:
            decision_kwargs["g7_mark_snapshot"] = g7_mark_snapshot
        await write_trade_log_async(
            self._logger.log_blend_decision,
            **decision_kwargs,
        )

    async def _emit_lane_skips(
        self,
        *,
        ticker: str,
        dossier: DossierState | None,
        structural_prior: StructuralPriorRecord | None,
    ) -> None:
        if not cfg.enable_lane_skip_when_no_data:
            return
        if not hasattr(self._logger, "log_lane_skipped"):
            return
        skipped: list[tuple[str, str]] = []
        if dossier is None or dossier.current_estimate is None:
            skipped.append(("accumulation", "no_dossier_estimate"))
        if structural_prior is None or structural_prior.prior_estimate is None:
            skipped.append(("structural", "no_structural_prior"))
        for lane_id, reason in skipped:
            await write_trade_log_async(
                self._logger.log_lane_skipped,
                market_ticker=ticker,
                lane_id=lane_id,
                reason=reason,
            )

    async def _emit_gate_summary(
        self,
        *,
        ticker: str,
        readiness: ReadinessDecision,
        blend_result: BlendResult,
        g7_mark_snapshot: dict[str, Any] | None,
        lifecycle_id: str | None,
        settlement_source_match: bool | None,
    ) -> None:
        if not hasattr(self._logger, "log_gate_summary"):
            return
        g1_threshold = G1_FAILSAFE_CONFIDENCE_THRESHOLD if readiness.fail_safe_active else G1_CONFIDENCE_THRESHOLD
        await write_trade_log_async(
            self._logger.log_gate_summary,
            ticker=ticker,
            market_prefix=_series_prefix(ticker),
            binding_constraint=_binding_constraint(readiness),
            scaled_confidence=readiness.scaled_confidence,
            regime_confidence=readiness.regime_confidence,
            blended_confidence=blend_result.blended_confidence,
            g1_threshold=g1_threshold,
            g4_threshold=G4_REGIME_CONFIDENCE_THRESHOLD,
            gate_chain=_gate_chain(readiness, g1_threshold),
            recency_score=readiness.recency_score,
            recency_threshold=readiness.recency_threshold,
            recency_distance=readiness.recency_distance,
            g7_mark_snapshot=g7_mark_snapshot,
            lifecycle_id=lifecycle_id,
            settlement_source_match=settlement_source_match,
        )

    async def _emit_skipped(
        self,
        *,
        ticker: str,
        blend_result: BlendResult,
        readiness: ReadinessDecision,
        trade_blocked_reason: str,
        fast_lane_result: SignalAnalysis,
        venue: str,
        g7_mark_snapshot: dict[str, Any] | None,
    ) -> None:
        """Emit a SKIPPED record for the blocked-reason early-return path.

        Mirrors the executor's SKIPPED payload shape at trading/executor.py:137-152
        so downstream consumers (bothealth.sh, governance Phase 2 reasoning,
        future readiness-gate calibration) can treat the union of
        BlendTask-emitted and executor-emitted SKIPPED records as a single
        queryable stream. The `reason` field disambiguates origin: G1-G6 /
        blender-side reasons originate here; everything else originates at the
        executor. See PROFIT-OBS-003 closure notes in the debt log.
        """
        news_item = fast_lane_result.news_item
        headline = news_item.headline[:80] if news_item is not None else None
        source = news_item.source if news_item is not None else None
        method = (
            "llm"
            if any(
                value is not None
                for value in (
                    fast_lane_result.llm_direction,
                    fast_lane_result.llm_magnitude,
                    fast_lane_result.llm_confidence,
                )
            )
            else "keyword"
        )
        executed_price_cents = fast_lane_result.executed_price_cents
        if self._has_valid_executed_price_cents(executed_price_cents):
            market_price = float(executed_price_cents)
            blended_p = blend_result.blended_p
            edge = blended_p - market_price / 100.0
            if readiness.readiness_gate_min_edge_override is not None:
                min_edge_threshold = readiness.readiness_gate_min_edge_override
            else:
                min_edge_threshold = PAPER_MIN_EDGE if self._is_paper_mode else cfg.min_edge
        else:
            blended_p = None
            market_price = None
            edge = None
            min_edge_threshold = None
        skipped_kwargs: dict[str, Any] = {
            "reason": trade_blocked_reason,
            "ticker": ticker,
            "side": fast_lane_result.side,
            "headline": headline,
            "source": source,
            "method": method,
            "llm_direction": fast_lane_result.llm_direction,
            "llm_magnitude": fast_lane_result.llm_magnitude,
            "model_probability": blended_p,
            "market_price": market_price,
            "edge": edge,
            "min_edge_threshold": min_edge_threshold,
            "venue": venue,
            "recency_score": readiness.recency_score,
            "recency_threshold": readiness.recency_threshold,
            "recency_distance": readiness.recency_distance,
        }
        signal_meta = fast_lane_result.signal_meta
        if signal_meta:
            skipped_kwargs["signal_meta"] = signal_meta
        if g7_mark_snapshot is not None:
            skipped_kwargs["g7_mark_snapshot"] = g7_mark_snapshot
        await write_trade_log_async(
            self._logger.log_skipped,
            **skipped_kwargs,
        )


async def process_fast_lane_result(
    fast_lane_result: SignalAnalysis,
    *,
    trading_queue: asyncio.Queue[TradeCandidate],
    store: EvidenceStoreLike | None = None,
    logger: BlendDecisionLogger = trade_log,
) -> BlendTaskResult:
    """Convenience entry point for one fast-lane-triggered blend evaluation."""
    task = BlendTask(trading_queue=trading_queue, store=store, logger=logger)
    return await task.process_fast_lane_result(fast_lane_result)


def _event_news_research_top_notional(
    market: KalshiMarket,
    analysis: SignalAnalysis,
) -> float | None:
    """Politics official-p G7 fallback. Freeze and news-lane stay fail-closed."""
    if str(getattr(analysis, "signal_type", "") or "") != "research_decision_grade":
        return None
    from utils.event_news_research import event_news_executable_top_notional

    return event_news_executable_top_notional(market, str(getattr(analysis, "side", "") or ""))


def _readiness_input(
    *,
    blend_result: BlendResult,
    dossier: DossierState | None,
    recent_records: list[EvidenceRecord],
    trigger_record: EvidenceRecord | None = None,
    market: KalshiMarket,
    analysis: SignalAnalysis,
    default_min_edge: float,
    now: datetime,
    open_exposure_drawdown_pct: float | None = None,
    market_liquidity_override: float | None | object = _READINESS_LIQUIDITY_UNSET,
) -> dict[str, Any]:
    source_lane = "accumulation" if (dossier is not None and dossier.current_estimate is not None) else "fast"
    if str(getattr(analysis, "signal_type", "") or "") == "research_decision_grade":
        from utils.event_news_research import is_event_news_paper_cohort

        if is_event_news_paper_cohort():
            source_lane = "fast"
    readiness_records = _readiness_records(recent_records, trigger_record)
    return {
        "source_lane": source_lane,
        "blended_confidence": blend_result.blended_confidence,
        "disagreement_score": blend_result.disagreement_score,
        "default_min_edge": default_min_edge,
        "evidence_source_classes": [record.source_class for record in readiness_records],
        "drift_suspect": dossier.drift_suspect if dossier is not None else False,
        "in_recovery": dossier.in_recovery if dossier is not None else False,
        "recency_score": _recency_score(market, readiness_records, now),
        "time_to_close_seconds": _time_to_close_seconds(market, now),
        "settlement_source_relevant": _settlement_source_relevant(analysis),
        "market_liquidity_dollars": (
            _optional_float(getattr(market, "liquidity_dollars", None))
            if market_liquidity_override is _READINESS_LIQUIDITY_UNSET
            else _optional_float(market_liquidity_override)
        ),
        "market_price_momentum_cents": _market_price_momentum_cents(market),
        "intended_side": getattr(analysis, "side", None),
        "open_exposure_drawdown_pct": open_exposure_drawdown_pct,
    }


def _trigger_evidence_record(analysis: SignalAnalysis) -> EvidenceRecord | None:
    meta = analysis.signal_meta or {}
    evidence_id = meta.get("trigger_evidence_id")
    source_class = meta.get("trigger_evidence_source_class")
    ingested_ts = meta.get("trigger_evidence_ingested_ts")
    if not (
        isinstance(evidence_id, str)
        and evidence_id
        and isinstance(source_class, str)
        and source_class
        and isinstance(ingested_ts, str)
        and ingested_ts
    ):
        return None
    original_weight = meta.get("trigger_evidence_original_weight")
    if not isinstance(original_weight, int | float):
        original_weight = source_quality(source_class)
    return EvidenceRecord(
        evidence_id=evidence_id,
        market_ticker=analysis.market.ticker,
        source=str(meta.get("trigger_evidence_source") or "unknown"),
        source_class=source_class,
        headline=str(meta.get("trigger_evidence_headline") or ""),
        ingested_ts=ingested_ts,
        content_hash=str(meta.get("trigger_evidence_content_hash") or evidence_id),
        update_type="state",
        dossier_version_before=0,
        dossier_version_after=0,
        original_weight=float(original_weight),
    )


def _readiness_records(
    recent_records: list[EvidenceRecord],
    trigger_record: EvidenceRecord | None,
) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    seen: set[str] = set()
    for record in [trigger_record, *recent_records]:
        if record is None or record.evidence_id in seen:
            continue
        records.append(record)
        seen.add(record.evidence_id)
    return records


def _evidence_ids_for_blend(
    *,
    recent_records: list[EvidenceRecord],
    trigger_evidence_id: Any,
    include_recent: bool,
) -> list[str]:
    ids: list[str] = []
    if isinstance(trigger_evidence_id, str) and trigger_evidence_id:
        ids.append(trigger_evidence_id)
    if include_recent:
        ids.extend(record.evidence_id for record in recent_records)
    seen: set[str] = set()
    deduped: list[str] = []
    for evidence_id in ids:
        if evidence_id in seen:
            continue
        deduped.append(evidence_id)
        seen.add(evidence_id)
    return deduped


def _recency_score(
    market: KalshiMarket,
    recent_records: list[EvidenceRecord],
    now: datetime,
) -> float:
    pairs = [
        (record.original_weight, record.ingested_ts) for record in recent_records if record.original_weight is not None
    ]
    dominant_regime = _dominant_regime(_regime_weights(market))
    half_life_days = half_life_for_regime(dominant_regime)
    return recency_score(pairs, now, half_life_days)


def _time_to_close_seconds(market: KalshiMarket, now: datetime) -> float | None:
    close_time = getattr(market, "close_time", None)
    if not isinstance(close_time, str) or not close_time.strip():
        return None
    text = close_time.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        close_dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if close_dt.tzinfo is None:
        close_dt = close_dt.replace(tzinfo=UTC)
    now_dt = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return max(0.0, (close_dt.astimezone(UTC) - now_dt.astimezone(UTC)).total_seconds())


def _settlement_source_relevant(analysis: SignalAnalysis) -> bool | None:
    meta = analysis.signal_meta
    if not isinstance(meta, dict) or "settlement_source_match" not in meta:
        return None
    return strict_optional_bool(meta["settlement_source_match"])


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _market_price_momentum_cents(market: KalshiMarket) -> float | None:
    last_price = getattr(market, "last_price_cents", None)
    previous_price = getattr(market, "previous_price_cents", None)
    if last_price is None or previous_price is None:
        return None
    return float(last_price) - float(previous_price)


def _trade_candidate(
    *,
    fast_lane_result: SignalAnalysis,
    blend_result: BlendResult,
    readiness: ReadinessDecision,
    regime_weights: dict[str, float],
    regime_confidence: float,
) -> TradeCandidate:
    readiness_override = (
        blend_result.readiness_gate_min_edge_override
        if blend_result.readiness_gate_min_edge_override is not None
        else readiness.readiness_gate_min_edge_override
    )
    signal_meta = {
        "source_lane": "blend",
        "fast_lane_p": blend_result.fast_lane_p,
        "fast_lane_confidence": blend_result.fast_lane_confidence,
        "accumulation_p": blend_result.accumulation_p,
        "accumulation_confidence": blend_result.accumulation_confidence,
        "structural_p": blend_result.structural_p,
        "structural_confidence": blend_result.structural_confidence,
        "blended_p": blend_result.blended_p,
        "blended_confidence": blend_result.blended_confidence,
        "disagreement_score": blend_result.disagreement_score,
        "regime_weights": regime_weights,
        "regime_confidence": regime_confidence,
        "blend_mode": blend_result.blend_mode,
        "readiness_gate_min_edge_override": readiness_override,
        # PROFIT-SOURCE-001: track distinct evidence source-class count so single-
        # vs multi-source trade performance can be compared once G2 allows single.
        "evidence_source_class_count": readiness.source_class_count,
    }
    source_meta = fast_lane_result.signal_meta or {}
    for key, value in source_meta.items():
        if key == "lifecycle_id":
            if isinstance(value, str) and value.strip():
                signal_meta[key] = value.strip()
        elif key == "settlement_source_match":
            signal_meta[key] = strict_optional_bool(value)
        elif key.startswith(("research_", "trigger_evidence_")) or key == "g7_execution_liquidity":
            signal_meta[key] = value
    # F-11 P1-A: SignalAnalysis.market_yes_price was deleted; the canonical
    # post-P0 source for the executed-side entry price is executed_price_cents.
    # entry_price_cents is the float mirror used by the executor's edge math.
    canonical_cents = fast_lane_result.executed_price_cents
    return TradeCandidate(
        fast_lane_analysis=fast_lane_result,
        market=fast_lane_result.market,
        blended_probability=blend_result.blended_p,
        entry_price_cents=float(canonical_cents) if canonical_cents is not None else 0.0,
        side=fast_lane_result.side,
        signal_meta=signal_meta,
        readiness_decision=readiness,
        executed_price_cents=canonical_cents,
    )


def _series_prefix(ticker: str) -> str:
    """PROFIT-EXEC-002 / PROFIT-VENUE-PARITY-001 — venue-aware family prefix.

    Keys the series-correlation guard (the in-window suppression at the
    call site below) and the GATE_SUMMARY ``market_prefix`` field off the
    *contest family* a market belongs to.

    Kalshi: the leading ``ticker.split('-', 1)[0]`` token IS the real series
    (e.g. ``KXFISAEXTEND-26APR-MAY01`` → ``KXFISAEXTEND``, shared by all the
    series' multi-outcome contracts). Kept BYTE-IDENTICAL — this is the
    default branch for anything ``pm_domain_key`` rejects (KX* raises) or
    cannot resolve to a stem.

    Polymarket: the leading slug token is a market-MAKER prefix (e.g. ``ewc``)
    shared across many INDEPENDENT contests (Alaska Senate, Michigan Senate,
    ...). Splitting on ``-`` would lump ~30 unrelated contests into one
    correlation bucket and over-suppress the venue (the #148 over-grouping,
    one layer up). The canonical per-contest family is ``pm_domain_key``'s
    stem (PROFIT-VENUE-PARITY-001 open-risk #1 mandates a SINGLE PM family
    key — we delegate, never re-derive with a fresh regex). The leading
    ``polymarket_us:`` namespace is stripped so ``ewc-usse-me`` distinguishes
    the Maine Senate contest (both dem/rep sides share it) from
    ``ewc-usse-ak`` (Alaska, correctly independent). A bare ``polymarket_us``
    result (no ``:``) means the slug had no recognizable stem → coarse split
    fallback, never a crash.
    """
    if not ticker:
        # Per rules/risk_review.md "prefer raising over swallowing on
        # money-movement paths": an empty ticker would silently produce
        # an empty-string guard key that would collide with other
        # malformed tickers and create ambiguous suppression behavior.
        # In production, ticker is never empty (KalshiMarket validation
        # upstream); this raise surfaces invariant violations instead
        # of masking them. (silent-failure-hunter finding 1 / EXEC-002.)
        raise ValueError(f"_series_prefix: empty or null ticker: {ticker!r}")
    try:
        key = pm_domain_key(ticker)
    except ValueError:
        # KX* (Kalshi) tickers raise — the leading token is the real series.
        return ticker.split("-", 1)[0]
    _namespace, sep, stem = key.partition(":")
    if sep and stem:
        return stem
    # Bare 'polymarket_us' (unrecognized stem) → coarse split fallback.
    return ticker.split("-", 1)[0]


def _venue_string(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    text = str(value).strip().lower()
    return text or None


def _binding_constraint(readiness: ReadinessDecision) -> str:
    if readiness.passed:
        return "passed"
    reasons = set(readiness.failure_reasons)
    if "G4_regime_confidence" in reasons:
        return "G4_regime_low"
    if "G1_blended_confidence" in reasons:
        return "G1_blended_confidence"
    if "G3_disagreement_score" in reasons:
        return "G3_disagreement_score"
    return readiness.failure_reasons[0] if readiness.failure_reasons else "unknown"


def _gate_chain(readiness: ReadinessDecision, g1_threshold: float) -> list[str]:
    chain = [
        (
            f"G4: rc={readiness.regime_confidence:.4f} "
            f"{'<' if readiness.regime_confidence < G4_REGIME_CONFIDENCE_THRESHOLD else '>='} "
            f"{G4_REGIME_CONFIDENCE_THRESHOLD:.4f} "
            f"{'FAIL' if 'G4_regime_confidence' in readiness.failure_reasons else 'PASS'}"
        ),
        (
            f"G1: sc={readiness.scaled_confidence:.4f} "
            f"{'<' if readiness.scaled_confidence < g1_threshold else '>='} "
            f"{g1_threshold:.4f} "
            f"{'FAIL' if 'G1_blended_confidence' in readiness.failure_reasons else 'PASS'}"
        ),
    ]
    if "G3" in readiness.applied_conditions:
        chain.append("G3: " + ("FAIL" if "G3_disagreement_score" in readiness.failure_reasons else "PASS"))
    for reason in readiness.failure_reasons:
        if not reason.startswith(("G1_", "G3_", "G4_")):
            chain.append(f"{reason}: FAIL")
    return chain


def _regime_weights(market: KalshiMarket) -> dict[str, float]:
    weights = dict(market.regime_weights or {})
    if not weights:
        return {
            "fast": 1.0 / 3.0,
            "interpretation": 1.0 / 3.0,
            "structural": 1.0 / 3.0,
        }
    return weights


def _dominant_regime(regime_weights: dict[str, float]) -> str:
    if not regime_weights:
        return "interpretation"
    return max(regime_weights, key=regime_weights.get)


def _regime_confidence(regime_weights: dict[str, float]) -> float:
    if not regime_weights:
        return 0.0
    entropy = -sum(weight * math.log(weight) for weight in regime_weights.values() if weight > 0)
    max_entropy = math.log(3)
    confidence = 1.0 - (entropy / max_entropy)
    return max(0.0, min(1.0, confidence))
