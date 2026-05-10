"""Blend-lane orchestration task.

S3.4 lane meeting point: fast-lane analysis triggers this task, while the
accumulation and structural lanes provide contextual inputs. This module does
not execute trades, size positions, or place orders.
"""

from __future__ import annotations

import asyncio
import inspect
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Protocol

from analysis import SignalAnalysis
from analysis import decision_blender
from analysis.decision_blender import BlendResult, LaneInput
from analysis.dossier_builder import half_life_for_regime, recency_score
from config import PAPER_MIN_EDGE, cfg
from kalshi import KalshiMarket
from tasks import evidence_store
from tasks.evidence_store import DossierState, EvidenceRecord, StructuralPriorRecord
from tasks.trade_readiness_gate import ReadinessDecision, evaluate_readiness
from utils.logger import trade_log, write_trade_log_async


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
    ) -> None: ...


@dataclass(frozen=True)
class TradeCandidate:
    """Queue payload produced only after the readiness gate passes."""

    fast_lane_analysis: SignalAnalysis
    market: KalshiMarket
    blended_probability: float
    market_yes_price: float
    side: str
    signal_meta: dict[str, Any]
    readiness_decision: ReadinessDecision


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

        readiness_input = _readiness_input(
            blend_result=blend_result,
            dossier=dossier,
            recent_records=recent_records,
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

        trade_blocked_reason = (
            blend_result.trade_blocked_reason
            or readiness.trade_blocked_reason
        )
        evidence_ids = _evidence_ids_for_blend(
            recent_records=recent_records,
            trigger_evidence_id=(fast_lane_result.signal_meta or {}).get("trigger_evidence_id"),
            include_recent=dossier is not None,
        )
        await self._emit_blend_decision(
            ticker=ticker,
            blend_result=blend_result,
            regime_weights=regime_weights,
            regime_confidence=regime_confidence,
            trade_blocked_reason=trade_blocked_reason,
            evidence_ids=evidence_ids,
        )

        if trade_blocked_reason is not None:
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
        try:
            await self._trading_queue.put(candidate)
        except Exception as exc:
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
        fast = LaneInput(
            p=fast_lane_result.estimated_probability,
            confidence=fast_lane_result.confidence * self._calibration_scale("fast"),
            lane_id="fast",
        )
        accumulation = (
            LaneInput(
                p=dossier.current_estimate,
                confidence=dossier.confidence * self._calibration_scale("accumulation"),
                lane_id="accumulation",
            )
            if dossier is not None and dossier.current_estimate is not None
            else None
        )
        structural = (
            LaneInput(
                p=structural_prior.prior_estimate,
                confidence=structural_prior.confidence * self._calibration_scale("structural"),
                lane_id="structural",
            )
            if structural_prior is not None and structural_prior.prior_estimate is not None
            else None
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
        )

    async def _emit_skipped(
        self,
        *,
        ticker: str,
        blend_result: BlendResult,
        readiness: ReadinessDecision,
        trade_blocked_reason: str,
        fast_lane_result: SignalAnalysis,
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
        market_price = fast_lane_result.market_yes_price
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
    market: KalshiMarket,
    default_min_edge: float,
    now: datetime,
) -> dict[str, Any]:
    source_lane = "accumulation" if (dossier is not None and dossier.current_estimate is not None) else "fast"
    return {
        "source_lane": source_lane,
        "blended_confidence": blend_result.blended_confidence,
        "disagreement_score": blend_result.disagreement_score,
        "default_min_edge": default_min_edge,
        "evidence_source_classes": [
            record.source_class for record in recent_records
        ],
        "drift_suspect": dossier.drift_suspect if dossier is not None else False,
        "in_recovery": dossier.in_recovery if dossier is not None else False,
        "recency_score": _recency_score(market, recent_records, now),
    }


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
    }
    return TradeCandidate(
        fast_lane_analysis=fast_lane_result,
        market=fast_lane_result.market,
        blended_probability=blend_result.blended_p,
        market_yes_price=fast_lane_result.market_yes_price,
        side=fast_lane_result.side,
        signal_meta=signal_meta,
        readiness_decision=readiness,
    )


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
