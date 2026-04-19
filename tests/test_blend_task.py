import asyncio
from datetime import UTC, datetime

import pytest

from analysis import SignalAnalysis
from analysis.decision_blender import BlendResult
from kalshi import KalshiMarket
from tasks.blend_task import (
    BlendTask,
    QueueInsertionError,
    TradeCandidate,
    _regime_confidence,
    process_fast_lane_result,
)
from tasks.evidence_store import DossierState, EvidenceRecord, StructuralPriorRecord


class FakeStore:
    def __init__(
        self,
        *,
        dossier: DossierState | None = None,
        structural_prior: StructuralPriorRecord | None = None,
        evidence: list[EvidenceRecord] | None = None,
    ) -> None:
        self.dossier = dossier
        self.structural_prior = structural_prior
        self.evidence = evidence or []
        self.calls: list[tuple[str, str]] = []

    async def get_dossier(self, market_ticker: str) -> DossierState | None:
        self.calls.append(("get_dossier", market_ticker))
        return self.dossier

    async def get_structural_prior(
        self,
        market_ticker: str,
    ) -> StructuralPriorRecord | None:
        self.calls.append(("get_structural_prior", market_ticker))
        return self.structural_prior

    async def get_recent_evidence(
        self,
        market_ticker: str,
        *,
        limit: int = 100,
    ) -> list[EvidenceRecord]:
        self.calls.append(("get_recent_evidence", market_ticker))
        assert limit == 100
        return list(self.evidence)


class SpyLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log_blend_decision(self, **kwargs) -> None:
        self.records.append(kwargs)


class FailingQueue(asyncio.Queue):
    async def put(self, item):  # noqa: ANN001
        raise RuntimeError("queue closed")


def _market(
    ticker: str = "KXBLEND-1",
    *,
    regime_weights: dict[str, float] | None = None,
) -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        title="Will an Iran peace deal be signed?",
        yes_bid=49,
        yes_ask=51,
        yes_price=50,
        volume=100,
        open_interest=50,
        close_time="2026-05-01T00:00:00Z",
        status="open",
        regime_weights=regime_weights
        or {"fast": 1.0, "interpretation": 0.0, "structural": 0.0},
    )


def _analysis(
    *,
    market: KalshiMarket | None = None,
    probability: float = 0.72,
    confidence: float = 0.90,
) -> SignalAnalysis:
    market = market or _market()
    return SignalAnalysis(
        news_item=None,
        market=market,
        estimated_probability=probability,
        market_yes_price=market.yes_price,
        edge=probability - market.yes_prob,
        side="yes",
        kelly_fraction=0.0,
        kelly_dollars=0.0,
        capped_dollars=0.0,
        reasoning="fast lane test signal",
        confidence=confidence,
        match_score=0.8,
    )


def _dossier(
    *,
    ticker: str = "KXBLEND-1",
    confidence: float = 0.80,
    current_estimate: float | None = 0.68,
    drift_suspect: bool = False,
    in_recovery: bool = False,
) -> DossierState:
    return DossierState(
        market_ticker=ticker,
        dossier_version=3,
        current_estimate=current_estimate,
        confidence=confidence,
        prior_estimate=0.55,
        drift_suspect=drift_suspect,
        in_recovery=in_recovery,
        created_ts="2026-04-18T00:00:00+00:00",
        updated_ts="2026-04-18T01:00:00+00:00",
    )


def _structural_prior(ticker: str = "KXBLEND-1") -> StructuralPriorRecord:
    return StructuralPriorRecord(
        market_ticker=ticker,
        prior_estimate=0.64,
        confidence=0.50,
        computed_ts="2026-04-18T01:30:00+00:00",
        recompute_trigger="dossier_update",
        input_source_count=2,
        llm_called=False,
    )


def _evidence(
    evidence_id: str,
    *,
    source_class: str,
    source: str = "Reuters",
    ticker: str = "KXBLEND-1",
    ingested_ts: str | None = None,
    original_weight: float = 0.8,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        market_ticker=ticker,
        source=source,
        source_class=source_class,
        headline=f"Evidence {evidence_id}",
        ingested_ts=(
            ingested_ts
            or datetime(2026, 4, 18, 12, tzinfo=UTC).isoformat()
        ),
        content_hash=f"hash-{evidence_id}",
        update_type="state",
        dossier_version_before=1,
        dossier_version_after=2,
        original_weight=original_weight,
    )


@pytest.mark.asyncio
async def test_ready_candidate_reads_lanes_logs_and_enqueues():
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    store = FakeStore(
        dossier=_dossier(),
        structural_prior=_structural_prior(),
        evidence=[
            _evidence("ev-1", source_class="news"),
            _evidence("ev-2", source_class="official", source="White House"),
        ],
    )
    task = BlendTask(
        trading_queue=queue,
        store=store,
        logger=logger,
        is_paper_mode=True,
        now=lambda: datetime(2026, 4, 18, 12, tzinfo=UTC),
    )

    result = await task.process_fast_lane_result(_analysis())

    assert result.ready is True
    assert queue.qsize() == 1
    candidate = await queue.get()
    assert candidate.blended_probability == pytest.approx(result.blend_result.blended_p)
    assert candidate.signal_meta["source_lane"] == "blend"
    assert candidate.signal_meta["readiness_gate_min_edge_override"] is None
    assert logger.records == [
        {
            "market_ticker": "KXBLEND-1",
            "fast_lane_p": pytest.approx(0.72),
            "fast_lane_confidence": pytest.approx(0.9),
            "accumulation_p": pytest.approx(0.68),
            "accumulation_confidence": pytest.approx(0.8),
            "structural_p": pytest.approx(0.64),
            "structural_confidence": pytest.approx(0.5),
            "regime_weights": {"fast": 1.0, "interpretation": 0.0, "structural": 0.0},
            "regime_confidence": pytest.approx(1.0),
            "blended_p": pytest.approx(result.blend_result.blended_p),
            "blended_confidence": pytest.approx(result.blend_result.blended_confidence),
            "disagreement_score": pytest.approx(result.blend_result.disagreement_score),
            "blend_mode": result.blend_result.blend_mode,
            "trade_considered": True,
            "trade_blocked_reason": None,
            "evidence_ids_contributing": ["ev-1", "ev-2"],
        }
    ]
    assert {call[0] for call in store.calls} == {
        "get_dossier",
        "get_structural_prior",
        "get_recent_evidence",
    }


@pytest.mark.asyncio
async def test_missing_slow_lane_context_uses_fast_lane_exemptions():
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(),
        logger=logger,
        is_paper_mode=True,
    )

    result = await task.process_fast_lane_result(_analysis())

    assert result.ready is True
    assert result.readiness_decision.applied_conditions == ("G1", "G3", "G4")
    assert logger.records[0]["accumulation_p"] is None
    assert logger.records[0]["structural_p"] is None
    assert logger.records[0]["evidence_ids_contributing"] == []


@pytest.mark.asyncio
async def test_blocked_candidate_logs_reason_and_does_not_enqueue():
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    store = FakeStore(
        dossier=_dossier(),
        structural_prior=_structural_prior(),
        evidence=[_evidence("ev-1", source_class="news")],
    )
    task = BlendTask(
        trading_queue=queue,
        store=store,
        logger=logger,
        is_paper_mode=True,
    )

    result = await task.process_fast_lane_result(_analysis())

    assert result.ready is False
    assert result.candidate is None
    assert result.trade_blocked_reason == "G2_evidence_source_class_diversity"
    assert queue.empty()
    assert logger.records[0]["trade_considered"] is True
    assert logger.records[0]["trade_blocked_reason"] == "G2_evidence_source_class_diversity"


@pytest.mark.asyncio
async def test_blender_block_reason_wins_and_candidate_is_not_enqueued():
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()

    def blocked_blender(**kwargs) -> BlendResult:  # noqa: ANN003
        return BlendResult(
            blended_p=0.70,
            blended_confidence=0.90,
            disagreement_score=0.0,
            blend_mode="structural_tier2_veto",
            readiness_gate_min_edge_override=None,
            trade_blocked_reason="structural_tier2_veto: test",
            fast_lane_p=0.72,
            fast_lane_confidence=0.90,
            accumulation_p=None,
            accumulation_confidence=None,
            structural_p=0.20,
            structural_confidence=0.80,
        )

    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(),
        logger=logger,
        blender=blocked_blender,
        is_paper_mode=True,
    )

    result = await task.process_fast_lane_result(_analysis())

    assert result.ready is False
    assert queue.empty()
    assert result.trade_blocked_reason == "structural_tier2_veto: test"
    assert logger.records[0]["trade_blocked_reason"] == "structural_tier2_veto: test"


@pytest.mark.asyncio
async def test_queue_failure_is_explicit_after_telemetry():
    logger = SpyLogger()
    task = BlendTask(
        trading_queue=FailingQueue(),
        store=FakeStore(),
        logger=logger,
        is_paper_mode=True,
    )

    with pytest.raises(QueueInsertionError, match="failed to enqueue"):
        await task.process_fast_lane_result(_analysis())

    assert len(logger.records) == 1
    assert logger.records[0]["trade_blocked_reason"] is None


@pytest.mark.asyncio
async def test_module_entrypoint_uses_supplied_store_and_queue():
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()

    result = await process_fast_lane_result(
        _analysis(),
        trading_queue=queue,
        store=FakeStore(),
        logger=logger,
    )

    assert result.ready is True
    assert queue.qsize() == 1
    assert len(logger.records) == 1


def test_regime_confidence_uses_contract_entropy_formula():
    assert _regime_confidence({"fast": 1.0, "interpretation": 0.0, "structural": 0.0}) == pytest.approx(1.0)
    assert _regime_confidence({"fast": 1 / 3, "interpretation": 1 / 3, "structural": 1 / 3}) == pytest.approx(0.0)


def test_blend_task_does_not_import_trading_layer():
    import tasks.blend_task as blend_task

    assert "trading" not in blend_task.__dict__
