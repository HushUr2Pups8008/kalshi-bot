"""Guarded bridge from decision-grade research to paper-review blend admission."""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from analysis import SignalAnalysis
from analysis.research_gate import ResearchEvidence
from tasks.blend_task import BlendTask, BlendTaskResult, TradeCandidate
from tasks.evidence_store import DossierState, EvidenceRecord, StructuralPriorRecord
from tasks.research_dossier import ResearchDossierSnapshot, default_store
from tasks.research_prewarm_task import ResearchPrewarmResult
from utils.logger import trade_log
from utils.lifecycle import build_research_lifecycle_id
from utils.research_evidence_quality import has_reliable_research_source_path
from utils.research_market_eligibility import research_market_eligibility

DECISION_GRADE_STATUS = "decision_grade_candidate"
_COUNTER_QUERY_INTENTS = frozenset({"disconfirming", "contradiction_check"})
_COUNTER_CLAIM_TYPES = frozenset(
    {"contradiction", "disconfirming", "contradiction_check"}
)
_OFFICIAL_SOURCE_CLASSES = frozenset(
    {"official", "official_primary", "official_source", "resolution_source", "rules_source"}
)
_STRUCTURED_SIGNAL_METRICS = frozenset(
    {
        "cpi_monthly_change_single_decimal",
        "gdpnow_real_gdp_growth_saar",
        "nws_daily_high_temp_f",
    }
)
_EDGE_TOLERANCE = 0.005
_SETTLEMENT_CLAIM_TYPES = frozenset(
    {
        "corroboration",
        "official_resolution",
        "resolution",
        "settlement",
        "settlement_source",
        "supporting",
    }
)


class ResearchAdmissionStore(Protocol):
    async def get_dossier_snapshot(
        self,
        market_ticker: str,
    ) -> ResearchDossierSnapshot | None: ...

    async def get_research_run_evidence(
        self,
        market_ticker: str,
        research_run_id: str,
    ) -> list[ResearchEvidence]: ...

    async def claim_research_paper_admission(
        self,
        market_ticker: str,
        research_run_id: str,
        contract_fingerprint: str,
    ) -> bool: ...

    async def complete_research_paper_admission(
        self,
        market_ticker: str,
        research_run_id: str,
        contract_fingerprint: str,
        *,
        state: str,
        enqueued: bool | None,
        outcome_reason: str | None,
    ) -> None: ...


@dataclass(frozen=True)
class ResearchPaperSignal:
    market_ticker: str
    research_run_id: str
    contract_fingerprint: str | None
    side: str
    estimated_probability: float
    confidence: float
    market_price: float
    estimated_edge: float
    researched_ts: datetime
    evidence: tuple[ResearchEvidence, ...]


@dataclass(frozen=True)
class ResearchPaperAdmissionResult:
    market_ticker: str
    admitted: bool
    reason: str | None = None
    enqueued: bool = False
    blend_result: BlendTaskResult | None = None


class ResearchPaperSignalProvider:
    """Load and validate decision-grade research proof for paper review."""

    def __init__(
        self,
        store: ResearchAdmissionStore | None = None,
        *,
        now: Callable[[], datetime] | None = None,
        max_age_seconds: float = 6 * 3600,
    ) -> None:
        self.store = store or default_store()
        self._now = now or (lambda: datetime.now(UTC))
        self.max_age = timedelta(seconds=max(0.0, float(max_age_seconds)))

    async def get_signal(
        self,
        market_ticker: str,
    ) -> tuple[ResearchPaperSignal | None, str | None]:
        snapshot = await self.store.get_dossier_snapshot(market_ticker)
        if snapshot is None:
            return None, "missing_dossier"
        if (
            snapshot.last_verdict_status != DECISION_GRADE_STATUS
            or snapshot.last_decision_grade_status != DECISION_GRADE_STATUS
        ):
            return None, "not_decision_grade_candidate"
        if not snapshot.last_research_run_id:
            return None, "missing_research_run"
        side = (snapshot.last_force_side or "").strip().lower()
        if side not in {"yes", "no"}:
            return None, "missing_side"
        if (
            snapshot.last_estimated_probability is None
            or snapshot.last_confidence is None
            or snapshot.last_market_price is None
            or snapshot.last_estimated_edge is None
        ):
            return None, "missing_price_edge"
        researched_ts = _parse_ts(snapshot.last_researched_ts)
        if researched_ts is None:
            return None, "missing_research_timestamp"
        if self._now().astimezone(UTC) - researched_ts > self.max_age:
            return None, "stale_research"
        if _recompute_edge(
            side=side,
            estimated_probability=float(snapshot.last_estimated_probability),
            market_price=float(snapshot.last_market_price),
        ) <= _EDGE_TOLERANCE:
            return None, "no_positive_edge"
        if not _edge_recomputes(
            side=side,
            estimated_probability=float(snapshot.last_estimated_probability),
            market_price=float(snapshot.last_market_price),
            estimated_edge=float(snapshot.last_estimated_edge),
        ):
            return None, "edge_not_recomputable"
        evidence = tuple(
            await self.store.get_research_run_evidence(
                market_ticker,
                snapshot.last_research_run_id,
            )
        )
        if not has_reliable_research_source_path(evidence):
            return None, "no_reliable_source_path"
        if not await _has_counter_query(self.store, snapshot.last_research_run_id):
            return None, "missing_counter_query"
        if not _has_counter_evidence(side, evidence):
            return None, "missing_counter_evidence"
        return (
            ResearchPaperSignal(
                market_ticker=market_ticker,
                research_run_id=snapshot.last_research_run_id,
                contract_fingerprint=snapshot.last_contract_fingerprint,
                side=side,
                estimated_probability=float(snapshot.last_estimated_probability),
                confidence=float(snapshot.last_confidence),
                market_price=float(snapshot.last_market_price),
                estimated_edge=float(snapshot.last_estimated_edge),
                researched_ts=researched_ts,
                evidence=evidence,
            ),
            None,
        )


class ResearchBackedBlendStore:
    """Minimal BlendTask store view backed by a validated research signal."""

    def __init__(self, signal: ResearchPaperSignal) -> None:
        self.signal = signal

    async def get_dossier(self, market_ticker: str) -> DossierState | None:
        if market_ticker != self.signal.market_ticker:
            return None
        return DossierState(
            market_ticker=market_ticker,
            dossier_version=1,
            current_estimate=self.signal.estimated_probability,
            confidence=self.signal.confidence,
            prior_estimate=None,
            drift_suspect=False,
            in_recovery=False,
            created_ts=self.signal.researched_ts.isoformat(),
            updated_ts=self.signal.researched_ts.isoformat(),
        )

    async def get_structural_prior(
        self,
        market_ticker: str,
    ) -> StructuralPriorRecord | None:
        return None

    async def get_recent_evidence(
        self,
        market_ticker: str,
        *,
        limit: int = 100,
    ) -> list[EvidenceRecord]:
        if market_ticker != self.signal.market_ticker:
            return []
        return [
            EvidenceRecord(
                evidence_id=f"research:{self.signal.research_run_id}:{index}",
                market_ticker=market_ticker,
                source=item.source_name or item.source_class,
                source_class=item.source_class,
                headline=item.title or item.snippet[:120],
                ingested_ts=(
                    item.retrieved_at
                    or item.inserted_at
                    or self.signal.researched_ts.isoformat()
                ),
                content_hash=f"research-{self.signal.research_run_id}-{index}",
                update_type="research_decision_grade",
                dossier_version_before=1,
                dossier_version_after=1,
                url=item.source_url or None,
                published_ts=item.published_at,
                original_weight=max(0.1, min(1.0, item.supports_confidence or 0.8)),
            )
            for index, item in enumerate(self.signal.evidence[:limit], start=1)
        ]


class ResearchPaperAdmissionBridge:
    """Admit strict decision-grade research into BlendTask paper review."""

    def __init__(
        self,
        *,
        research_store: ResearchAdmissionStore | None = None,
        trading_queue: asyncio.Queue[TradeCandidate],
        logger: Any = trade_log,
        now: Callable[[], datetime] | None = None,
        signal_provider: ResearchPaperSignalProvider | None = None,
        route_analysis: (
            Callable[[SignalAnalysis, ResearchBackedBlendStore], Awaitable[Any]]
            | None
        ) = None,
    ) -> None:
        self.provider = signal_provider or ResearchPaperSignalProvider(
            research_store,
            now=now,
        )
        self.trading_queue = trading_queue
        self.logger = logger
        self._now = now or (lambda: datetime.now(UTC))
        self._route_analysis = route_analysis

    async def admit_prewarm_result(
        self,
        result: ResearchPrewarmResult,
        market: Any,
    ) -> ResearchPaperAdmissionResult:
        result_ticker = str(result.market_ticker or "").strip()
        market_ticker = str(getattr(market, "ticker", "") or "").strip()
        ticker = result_ticker or market_ticker
        if not _is_decision_grade_prewarm_result(result):
            return ResearchPaperAdmissionResult(
                market_ticker=ticker,
                admitted=False,
                reason="not_decision_grade_candidate",
            )
        if not result_ticker or not market_ticker or result_ticker != market_ticker:
            return ResearchPaperAdmissionResult(
                market_ticker=ticker,
                admitted=False,
                reason="research_market_identity_mismatch",
            )
        eligibility = research_market_eligibility(market, now=self._now())
        if not eligibility.eligible:
            return ResearchPaperAdmissionResult(
                market_ticker=ticker,
                admitted=False,
                reason=eligibility.reason or "market_not_active",
            )
        signal, reason = await self.provider.get_signal(ticker)
        if signal is None:
            return ResearchPaperAdmissionResult(
                market_ticker=ticker,
                admitted=False,
                reason=reason or "not_decision_grade_candidate",
            )
        if not signal.contract_fingerprint:
            return ResearchPaperAdmissionResult(
                market_ticker=ticker,
                admitted=False,
                reason="missing_contract_fingerprint",
            )
        if (
            signal.market_ticker != ticker
            or not result.research_run_id
            or result.research_run_id != signal.research_run_id
            or not result.research_contract_fingerprint
            or result.research_contract_fingerprint != signal.contract_fingerprint
        ):
            return ResearchPaperAdmissionResult(
                market_ticker=ticker,
                admitted=False,
                reason="research_result_snapshot_mismatch",
            )
        current_signal, reason = _signal_with_current_market_price(market, signal)
        if current_signal is None:
            return ResearchPaperAdmissionResult(
                market_ticker=ticker,
                admitted=False,
                reason=reason,
            )
        analysis = _signal_analysis_from_research(market, current_signal)
        claim_admission = getattr(
            self.provider.store,
            "claim_research_paper_admission",
            None,
        )
        complete_admission = getattr(
            self.provider.store,
            "complete_research_paper_admission",
            None,
        )
        if not callable(claim_admission) or not callable(complete_admission):
            return ResearchPaperAdmissionResult(
                market_ticker=ticker,
                admitted=False,
                reason="admission_claim_unavailable",
            )
        claimed = await claim_admission(
            ticker,
            current_signal.research_run_id,
            current_signal.contract_fingerprint,
        )
        if not claimed:
            return ResearchPaperAdmissionResult(
                market_ticker=ticker,
                admitted=False,
                reason="duplicate_research_admission",
            )
        try:
            _emit_research_opportunity(self.logger, market, current_signal, analysis)
            research_store = ResearchBackedBlendStore(current_signal)
            if self._route_analysis is not None:
                blend_result = await self._route_analysis(analysis, research_store)
            else:
                task = BlendTask(
                    trading_queue=self.trading_queue,
                    store=research_store,
                    logger=self.logger,
                    is_paper_mode=True,
                    now=self._now,
                )
                blend_result = await task.process_fast_lane_result(analysis)
        except Exception as exc:
            try:
                await complete_admission(
                    ticker,
                    current_signal.research_run_id,
                    current_signal.contract_fingerprint,
                    state="failed",
                    enqueued=None,
                    outcome_reason=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
            raise
        await complete_admission(
            ticker,
            current_signal.research_run_id,
            current_signal.contract_fingerprint,
            state="completed",
            enqueued=bool(blend_result.enqueued),
            outcome_reason=blend_result.trade_blocked_reason,
        )
        return ResearchPaperAdmissionResult(
            market_ticker=ticker,
            admitted=blend_result.ready,
            reason=blend_result.trade_blocked_reason,
            enqueued=blend_result.enqueued,
            blend_result=blend_result,
    )


def _signal_with_current_market_price(
    market: Any,
    signal: ResearchPaperSignal,
) -> tuple[ResearchPaperSignal | None, str]:
    if not callable(getattr(market, "is_tradeable", None)) or not market.is_tradeable():
        return None, "current_market_price_unavailable"
    price_cents = (
        getattr(market, "yes_ask_cents", None)
        if signal.side == "yes"
        else getattr(market, "no_ask_cents", None)
    )
    if price_cents is None:
        return None, "current_market_price_unavailable"
    current_price = float(price_cents) / 100.0
    current_edge = _recompute_edge(
        side=signal.side,
        estimated_probability=signal.estimated_probability,
        market_price=current_price,
    )
    if current_edge <= _EDGE_TOLERANCE:
        return None, "current_market_no_positive_edge"
    return (
        replace(
            signal,
            market_price=current_price,
            estimated_edge=round(current_edge, 4),
        ),
        "",
    )


def _is_decision_grade_prewarm_result(result: ResearchPrewarmResult) -> bool:
    if result.status == DECISION_GRADE_STATUS:
        return True
    return (
        result.status == "skipped_terminal"
        and result.skip_reason == DECISION_GRADE_STATUS
    )


def _signal_analysis_from_research(
    market: Any,
    signal: ResearchPaperSignal,
) -> SignalAnalysis:
    trigger_index, trigger_evidence = _select_trigger_evidence(signal)
    trigger_evidence_id = f"research:{signal.research_run_id}:{trigger_index}"
    settlement_source_match = _research_settlement_source_match(trigger_evidence)
    lifecycle_id = build_research_lifecycle_id(
        ticker=signal.market_ticker,
        research_run_id=signal.research_run_id,
        contract_fingerprint=signal.contract_fingerprint,
    )
    return SignalAnalysis(
        news_item=None,
        market=market,
        estimated_probability=signal.estimated_probability,
        executed_price_cents=int(round(signal.market_price * 100)),
        edge=signal.estimated_edge,
        side=signal.side,
        confidence=signal.confidence,
        signal_type="research_decision_grade",
        reasoning="decision-grade research admitted for paper-review blend",
        signal_meta={
            "research_admission_status": DECISION_GRADE_STATUS,
            "research_run_id": signal.research_run_id,
            "research_contract_fingerprint": signal.contract_fingerprint,
            "trigger_evidence_id": trigger_evidence_id,
            "trigger_evidence_source_class": (
                trigger_evidence.source_class
                if trigger_evidence is not None
                else "research"
            ),
            "trigger_evidence_source": (
                trigger_evidence.source_name
                if trigger_evidence is not None
                else "research"
            ),
            "trigger_evidence_original_weight": 1.0,
            "settlement_source_match": settlement_source_match,
            "lifecycle_id": lifecycle_id,
        },
    )


def _emit_research_opportunity(
    logger: Any,
    market: Any,
    signal: ResearchPaperSignal,
    analysis: SignalAnalysis,
) -> None:
    log_opportunity = getattr(logger, "log_opportunity", None)
    if not callable(log_opportunity):
        return
    trigger_index, evidence = _select_trigger_evidence(signal)
    settlement_source_match = _research_settlement_source_match(evidence)
    lifecycle_id = build_research_lifecycle_id(
        ticker=signal.market_ticker,
        research_run_id=signal.research_run_id,
        contract_fingerprint=signal.contract_fingerprint,
    )
    log_opportunity(
        ticker=signal.market_ticker,
        market_title=str(getattr(market, "title", "") or signal.market_ticker),
        entry_price_cents=signal.market_price * 100.0,
        estimated_probability=signal.estimated_probability,
        edge=signal.estimated_edge,
        kelly_fraction=0.0,
        kelly_dollars=0.0,
        capped_dollars=0.0,
        side=signal.side,
        reasoning=analysis.reasoning,
        source=evidence.source_name if evidence is not None else "research",
        headline=evidence.title if evidence is not None else signal.market_ticker,
        method="research_decision_grade",
        llm_direction=signal.side,
        source_class=evidence.source_class if evidence is not None else "research",
        evidence_id=f"research:{signal.research_run_id}:{trigger_index}",
        settlement_source_match=settlement_source_match,
        research_status=DECISION_GRADE_STATUS,
        research_run_id=signal.research_run_id,
        signal_type=analysis.signal_type,
        lifecycle_id=lifecycle_id,
    )


def _select_trigger_evidence(
    signal: ResearchPaperSignal,
) -> tuple[int, ResearchEvidence | None]:
    directional = [
        (index, item)
        for index, item in enumerate(signal.evidence, start=1)
        if str(item.supports_direction or "").strip().lower() == signal.side
        and str(item.claim_type or "").strip().lower() in _SETTLEMENT_CLAIM_TYPES
    ]
    if directional:
        return max(
            directional,
            key=lambda pair: (float(pair[1].supports_confidence or 0.0), -pair[0]),
        )
    if signal.evidence:
        return 1, signal.evidence[0]
    return 0, None


def _research_settlement_source_match(evidence: ResearchEvidence | None) -> bool:
    if evidence is None:
        return False
    source_class = str(evidence.source_class or "").strip().lower()
    claim_type = str(evidence.claim_type or "").strip().lower()
    if source_class in {"resolution_source", "rules_source"}:
        return True
    return source_class in _OFFICIAL_SOURCE_CLASSES and claim_type in {
        "official_resolution",
        "resolution",
        "settlement",
        "settlement_source",
    }


def _edge_recomputes(
    *,
    side: str,
    estimated_probability: float,
    market_price: float,
    estimated_edge: float,
) -> bool:
    recomputed = _recompute_edge(
        side=side,
        estimated_probability=estimated_probability,
        market_price=market_price,
    )
    return recomputed > _EDGE_TOLERANCE and abs(recomputed - estimated_edge) <= _EDGE_TOLERANCE


def _recompute_edge(
    *,
    side: str,
    estimated_probability: float,
    market_price: float,
) -> float:
    side_probability = (
        estimated_probability if side == "yes" else 1.0 - estimated_probability
    )
    return side_probability - market_price - 0.01


def _has_counter_evidence(
    side: str,
    evidence: tuple[ResearchEvidence, ...],
) -> bool:
    opposite = "no" if side == "yes" else "yes"
    structured_support_metrics = {
        str(item.metric_name or "").strip()
        for item in evidence
        if item.claim_type.strip().lower() in _SETTLEMENT_CLAIM_TYPES
        and item.supports_direction.strip().lower() == side
        and _is_structured_official_metric_countercheck(item)
    }
    for item in evidence:
        claim_type = item.claim_type.strip().lower()
        direction = item.supports_direction.strip().lower()
        if claim_type in _COUNTER_CLAIM_TYPES and direction in {opposite, "neutral"}:
            return True
        if (
            claim_type in _COUNTER_CLAIM_TYPES
            and direction == side
            and str(item.metric_name or "").strip() in structured_support_metrics
            and _is_structured_official_metric_countercheck(item)
        ):
            return True
    return False


def _is_structured_official_metric_countercheck(item: ResearchEvidence) -> bool:
    source_class = item.source_class.strip().lower()
    metric_name = str(item.metric_name or "").strip()
    extraction_confidence = float(item.extraction_confidence or 0.0)
    return (
        source_class in _OFFICIAL_SOURCE_CLASSES
        and metric_name in _STRUCTURED_SIGNAL_METRICS
        and float(item.supports_confidence or 0.0) >= 0.8
        and (item.metric_value is not None or extraction_confidence >= 0.8)
    )


async def _has_counter_query(store: ResearchAdmissionStore, research_run_id: str) -> bool:
    query_checker = getattr(store, "has_research_run_query_intent", None)
    if callable(query_checker):
        return bool(await query_checker(research_run_id, set(_COUNTER_QUERY_INTENTS)))
    db_path = getattr(store, "db_path", None)
    if db_path is None:
        return False

    def check_db() -> bool:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "research_run_queries" not in tables:
                return False
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(research_run_queries)"
                ).fetchall()
            }
            if "query_intent" not in columns:
                return False
            placeholders = ",".join("?" for _ in _COUNTER_QUERY_INTENTS)
            row = conn.execute(
                f"""
                SELECT 1
                FROM research_run_queries
                WHERE research_run_id = ?
                  AND query_intent IN ({placeholders})
                LIMIT 1
                """,
                (research_run_id, *_COUNTER_QUERY_INTENTS),
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False
        finally:
            if conn is not None:
                conn.close()

    return await asyncio.to_thread(check_db)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
