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
from typing import Any, Awaitable, Callable, Protocol

from analysis import SignalAnalysis
from analysis import decision_blender
from analysis.decision_blender import BlendResult, LaneInput
from analysis.dossier_builder import half_life_for_regime, recency_score
from analysis.evidence_scorer import source_quality
from config import PAPER_MIN_EDGE, cfg
from kalshi import KalshiMarket
from tasks import evidence_store
from tasks.evidence_store import DossierState, EvidenceRecord, StructuralPriorRecord
from tasks.trade_readiness_gate import (
    G1_CONFIDENCE_THRESHOLD,
    G1_FAILSAFE_CONFIDENCE_THRESHOLD,
    G4_REGIME_CONFIDENCE_THRESHOLD,
    ReadinessDecision,
    evaluate_readiness,
)
from utils.logger import get_logger, trade_log, write_trade_log_async


log = get_logger("blend_task")


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
        is_paper_mode: bool | None = None,
        now: Callable[[], datetime] | None = None,
        calibration: CalibrationLike | None = None,
    ) -> None:
        self._trading_queue = trading_queue
        self._store = store if store is not None else evidence_store.default_store()
        self._logger = logger
        self._blender = blender
        self._readiness_evaluator = readiness_evaluator
        self._structural_stability_resolver = structural_stability_resolver
        self._calibration = calibration
        self._is_paper_mode = (
            cfg.is_paper_trading if is_paper_mode is None else is_paper_mode
        )
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

        readiness_input = _readiness_input(
            blend_result=blend_result,
            dossier=dossier,
            recent_records=recent_records,
            trigger_record=_trigger_evidence_record(fast_lane_result),
            market=market,
            default_min_edge=default_min_edge,
            now=self._now(),
        )
        try:
            readiness = self._readiness_evaluator(readiness_input, regime_confidence)
        except Exception as exc:
            raise ReadinessEvaluationError(
                f"readiness evaluation failed for {ticker}: {exc}"
            ) from exc
        await self._emit_gate_summary(
            ticker=ticker,
            readiness=readiness,
            blend_result=blend_result,
        )

        trade_blocked_reason = (
            blend_result.trade_blocked_reason
            or readiness.trade_blocked_reason
        )

        # PROFIT-EXEC-002 series-correlation guard. Only fires if no upstream
        # block already exists; the first candidate in a same-series burst
        # still trades, subsequent ones within the configured window are
        # suppressed via the OBS-003 SKIPPED stream.
        series_prefix = _series_prefix(ticker)
        if trade_blocked_reason is None:
            window = cfg.series_correlation_window_seconds
            if window > 0:
                last_enqueued_ts = self._recent_series_enqueues.get(series_prefix)
                if (
                    last_enqueued_ts is not None
                    and time.monotonic() - last_enqueued_ts < window
                ):
                    trade_blocked_reason = "series_correlation_in_window"

        evidence_ids = _evidence_ids_for_blend(
            recent_records=recent_records,
            trigger_evidence_id=(fast_lane_result.signal_meta or {}).get("trigger_evidence_id"),
            include_recent=dossier is not None,
        )
        venue = _venue_string(
            getattr(fast_lane_result, "venue", None)
            or getattr(fast_lane_result.market, "venue", None)
        ) or "kalshi"
        await self._emit_blend_decision(
            ticker=ticker,
            venue=venue,
            blend_result=blend_result,
            regime_weights=regime_weights,
            regime_confidence=regime_confidence,
            trade_blocked_reason=trade_blocked_reason,
            evidence_ids=evidence_ids,
        )

        if trade_blocked_reason is not None:
            await self._emit_skipped(
                ticker=ticker,
                blend_result=blend_result,
                readiness=readiness,
                trade_blocked_reason=trade_blocked_reason,
                fast_lane_result=fast_lane_result,
                venue=venue,
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
                series_prefix, exc,
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
        fast = LaneInput(
            p=fast_lane_result.estimated_probability,
            confidence=fast_lane_result.confidence * self._calibration_scale("fast"),
            lane_id="fast",
            signal_kind="real",
        )
        accumulation = self._build_accumulation_lane(dossier)
        structural = self._build_structural_lane(structural_prior)
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
                dossier.current_estimate, confidence,
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
                structural_prior.prior_estimate, confidence,
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
    ) -> None:
        await write_trade_log_async(
            self._logger.log_blend_decision,
            market_ticker=ticker,
            fast_lane_p=blend_result.fast_lane_p,
            fast_lane_confidence=blend_result.fast_lane_confidence,
            accumulation_p=blend_result.accumulation_p,
            accumulation_confidence=blend_result.accumulation_confidence,
            structural_p=blend_result.structural_p,
            structural_confidence=blend_result.structural_confidence,
            regime_weights=regime_weights,
            regime_confidence=regime_confidence,
            blended_p=blend_result.blended_p,
            blended_confidence=blend_result.blended_confidence,
            disagreement_score=blend_result.disagreement_score,
            blend_mode=blend_result.blend_mode,
            trade_considered=True,
            trade_blocked_reason=trade_blocked_reason,
            evidence_ids_contributing=evidence_ids,
            venue=venue,
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
    ) -> None:
        if not hasattr(self._logger, "log_gate_summary"):
            return
        g1_threshold = (
            G1_FAILSAFE_CONFIDENCE_THRESHOLD
            if readiness.fail_safe_active
            else G1_CONFIDENCE_THRESHOLD
        )
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
        # F-11 P1-A: SignalAnalysis.market_yes_price was deleted; the canonical
        # post-P0 source of the executed-side entry price is executed_price_cents.
        # market_price is in float cents for the legacy edge math.
        market_price = float(fast_lane_result.executed_price_cents or 0)
        blended_p = blend_result.blended_p
        edge = blended_p - market_price / 100.0
        if readiness.readiness_gate_min_edge_override is not None:
            min_edge_threshold = readiness.readiness_gate_min_edge_override
        else:
            min_edge_threshold = (
                PAPER_MIN_EDGE if self._is_paper_mode else cfg.min_edge
            )
        skipped_kwargs: dict[str, Any] = {
            "reason": trade_blocked_reason,
            "ticker": ticker,
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
        }
        signal_meta = fast_lane_result.signal_meta
        if signal_meta:
            skipped_kwargs["signal_meta"] = signal_meta
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


def _readiness_input(
    *,
    blend_result: BlendResult,
    dossier: DossierState | None,
    recent_records: list[EvidenceRecord],
    trigger_record: EvidenceRecord | None = None,
    market: KalshiMarket,
    default_min_edge: float,
    now: datetime,
) -> dict[str, Any]:
    source_lane = "accumulation" if (dossier is not None and dossier.current_estimate is not None) else "fast"
    readiness_records = _readiness_records(recent_records, trigger_record)
    return {
        "source_lane": source_lane,
        "blended_confidence": blend_result.blended_confidence,
        "disagreement_score": blend_result.disagreement_score,
        "default_min_edge": default_min_edge,
        "evidence_source_classes": [
            record.source_class for record in readiness_records
        ],
        "drift_suspect": dossier.drift_suspect if dossier is not None else False,
        "in_recovery": dossier.in_recovery if dossier is not None else False,
        "recency_score": _recency_score(market, readiness_records, now),
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
        (record.original_weight, record.ingested_ts)
        for record in recent_records
        if record.original_weight is not None
    ]
    dominant_regime = _dominant_regime(_regime_weights(market))
    half_life_days = half_life_for_regime(dominant_regime)
    return recency_score(pairs, now, half_life_days)


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
    """PROFIT-EXEC-002 — extract the Kalshi series prefix from a ticker.

    Splits on `-` and returns the first component. Examples:
    - `KXFISAEXTEND-26APR-MAY01` → `KXFISAEXTEND`
    - `KXMOCTRUMP25-26-APR24` → `KXMOCTRUMP25`
    - `KXTRUMPIRAN-26MAY01` → `KXTRUMPIRAN`
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
        chain.append(
            "G3: "
            + (
                "FAIL"
                if "G3_disagreement_score" in readiness.failure_reasons
                else "PASS"
            )
        )
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
    entropy = -sum(
        weight * math.log(weight)
        for weight in regime_weights.values()
        if weight > 0
    )
    max_entropy = math.log(3)
    confidence = 1.0 - (entropy / max_entropy)
    return max(0.0, min(1.0, confidence))
