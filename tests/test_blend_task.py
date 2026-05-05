import asyncio
from datetime import UTC, datetime

import pytest

from analysis import SignalAnalysis
from analysis.decision_blender import BlendResult
from config import cfg
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
        self.skipped_records: list[dict] = []

    def log_blend_decision(self, **kwargs) -> None:
        self.records.append(kwargs)

    def log_skipped(self, **kwargs) -> None:
        # Captures BlendTask-emitted SKIPPED records per PROFIT-OBS-003 (post-soak).
        self.skipped_records.append(kwargs)


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
async def test_trigger_evidence_id_is_logged_even_before_dossier_catches_up():
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(),
        logger=logger,
        is_paper_mode=True,
    )
    analysis = _analysis()
    analysis.signal_meta = {"trigger_evidence_id": "ev-trigger"}

    result = await task.process_fast_lane_result(analysis)

    assert result.ready is True
    assert logger.records[0]["evidence_ids_contributing"] == ["ev-trigger"]


@pytest.mark.asyncio
async def test_trigger_evidence_id_is_deduplicated_with_recent_evidence():
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    store = FakeStore(
        dossier=_dossier(),
        structural_prior=_structural_prior(),
        evidence=[
            _evidence("ev-trigger", source_class="news"),
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
    analysis = _analysis()
    analysis.signal_meta = {"trigger_evidence_id": "ev-trigger"}

    result = await task.process_fast_lane_result(analysis)

    assert result.ready is True
    assert logger.records[0]["evidence_ids_contributing"] == ["ev-trigger", "ev-2"]


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


@pytest.mark.asyncio
async def test_calibration_scaling_applied_to_lane_inputs():
    """CalibrationLike.get_scaling_factor is called and reduces effective confidence."""
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    captured: dict = {}

    def recording_blender(**kwargs) -> BlendResult:  # noqa: ANN003
        captured["fast_conf"] = kwargs["fast"].confidence
        from analysis.decision_blender import blend as real_blend
        return real_blend(**kwargs)

    class HalfScaleCalibration:
        def get_scaling_factor(self, lane: str) -> float:
            return 0.5  # halve all confidences

    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(),
        logger=logger,
        blender=recording_blender,
        is_paper_mode=True,
        calibration=HalfScaleCalibration(),
    )

    await task.process_fast_lane_result(_analysis(confidence=0.80))

    assert captured.get("fast_conf") == pytest.approx(0.40)


@pytest.mark.asyncio
async def test_no_calibration_scaling_when_calibration_is_none():
    """Default (no calibration) passes confidence unchanged."""
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    captured: dict = {}

    def recording_blender(**kwargs) -> BlendResult:  # noqa: ANN003
        captured["fast_conf"] = kwargs["fast"].confidence
        from analysis.decision_blender import blend as real_blend
        return real_blend(**kwargs)

    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(),
        logger=logger,
        blender=recording_blender,
        is_paper_mode=True,
    )

    await task.process_fast_lane_result(_analysis(confidence=0.80))

    assert captured.get("fast_conf") == pytest.approx(0.80)


def test_blend_task_does_not_import_trading_layer():
    import tasks.blend_task as blend_task

    assert "trading" not in blend_task.__dict__


@pytest.mark.asyncio
async def test_dossier_with_no_estimate_uses_fast_lane_exemptions():
    """Dossier exists but current_estimate=None → accumulation absent → fast-lane gate (G1/G3/G4 only)."""
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(dossier=_dossier(current_estimate=None)),
        logger=logger,
        is_paper_mode=True,
    )

    result = await task.process_fast_lane_result(_analysis())

    assert result.readiness_decision.applied_conditions == ("G1", "G3", "G4")


# ---------------------------------------------------------------------------
# PROFIT-OBS-003 harness — BlendTask SKIPPED-emission for blocked-reason path.
#
# Spec: docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md
# Status: pre-loaded during PROFIT-PHASE2-001 soak; do not implement before
# 2026-05-09. Until BlendTask._emit_skipped lands these tests xfail strictly,
# so xpass on the day prod code lands forces removal of the markers in CI.
# ---------------------------------------------------------------------------

from feeds import NewsItem  # noqa: E402  (imported here to keep harness self-contained)

_OBS003_XFAIL_REASON = (
    "PROFIT-OBS-003: BlendTask._emit_skipped not yet implemented. "
    "Lands post-soak per docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md."
)


def _news_item_for_obs003() -> NewsItem:
    return NewsItem(
        headline="Iran-deal headline used by OBS-003 payload-shape pin test",
        url="https://example.test/obs003",
        source="Reuters",
        body="",
        item_id="obs003-1",
    )


def _analysis_with_news() -> SignalAnalysis:
    """SignalAnalysis with a populated news_item — required for the payload-shape pin test."""
    market = _market()
    return SignalAnalysis(
        news_item=_news_item_for_obs003(),
        market=market,
        estimated_probability=0.72,
        market_yes_price=market.yes_price,
        edge=0.72 - market.yes_prob,
        side="yes",
        kelly_fraction=0.0,
        kelly_dollars=0.0,
        capped_dollars=0.0,
        reasoning="OBS-003 pin",
        confidence=0.90,
        match_score=0.8,
        llm_direction="up",
        llm_magnitude="moderate",
    )


def _blocked_blender_factory(reason: str):
    def _blender(**kwargs):  # noqa: ANN003
        return BlendResult(
            blended_p=0.70,
            blended_confidence=0.04,  # below G1 floor for the G1 case; immaterial otherwise
            disagreement_score=0.0,
            blend_mode="blocked_for_test",
            readiness_gate_min_edge_override=None,
            trade_blocked_reason=reason,
            fast_lane_p=0.72,
            fast_lane_confidence=0.90,
            accumulation_p=None,
            accumulation_confidence=None,
            structural_p=0.20,
            structural_confidence=0.80,
        )

    return _blender


@pytest.mark.xfail(reason=_OBS003_XFAIL_REASON, strict=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trade_blocked_reason",
    [
        "G1_blended_confidence",
        "G2_evidence_source_class_diversity",
        "G3_disagreement_score",
        "G4_regime_confidence",
        "G5_dossier_drift_suspect",
        "G6_recency_score",
        "structural_tier2_veto: synthetic test",
    ],
    ids=["G1", "G2", "G3", "G4", "G5", "G6", "blender_side"],
)
async def test_blocked_blend_emits_skipped_record(trade_blocked_reason: str) -> None:
    """Each blocked-reason path MUST emit exactly one SKIPPED record carrying the reason."""
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(),
        logger=logger,
        blender=_blocked_blender_factory(trade_blocked_reason),
        is_paper_mode=True,
        now=lambda: datetime(2026, 4, 18, 12, tzinfo=UTC),
    )

    result = await task.process_fast_lane_result(_analysis())

    assert result.candidate is None
    assert result.trade_blocked_reason == trade_blocked_reason
    assert queue.empty()
    # BLEND_DECISION still emits exactly once (existing behaviour).
    assert len(logger.records) == 1
    assert logger.records[0]["trade_blocked_reason"] == trade_blocked_reason
    # NEW: SKIPPED also emits exactly once with the same reason.
    assert len(logger.skipped_records) == 1, (
        f"expected one BlendTask-emitted SKIPPED record for {trade_blocked_reason}; "
        f"got {len(logger.skipped_records)}"
    )
    assert logger.skipped_records[0]["reason"] == trade_blocked_reason
    assert logger.skipped_records[0]["ticker"] == "KXBLEND-1"


@pytest.mark.asyncio
async def test_unblocked_blend_does_not_emit_blendtask_skipped_record() -> None:
    """Happy path: candidate enqueued → BlendTask emits zero SKIPPED records.

    Executor-side SKIPPED emission for `_validate()` rejections is a separate
    codepath and is not exercised here.
    """
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(
            dossier=_dossier(),
            structural_prior=_structural_prior(),
            evidence=[
                _evidence("ev-news-1", source_class="news"),
                _evidence("ev-news-2", source_class="news", source="AP"),
                _evidence("ev-court-1", source_class="court", source="VitalLaw.com"),
            ],
        ),
        logger=logger,
        is_paper_mode=True,
        now=lambda: datetime(2026, 4, 18, 12, tzinfo=UTC),
    )

    result = await task.process_fast_lane_result(_analysis())

    assert result.candidate is not None
    assert result.trade_blocked_reason is None
    assert queue.qsize() == 1
    assert len(logger.records) == 1  # BLEND_DECISION still fires
    assert logger.skipped_records == [], (
        "BlendTask must not emit SKIPPED on the happy path"
    )


@pytest.mark.xfail(reason=_OBS003_XFAIL_REASON, strict=True)
@pytest.mark.asyncio
async def test_skipped_payload_carries_blended_edge_not_fast_lane_edge() -> None:
    """Pin spec §5: BlendTask-emitted SKIPPED records carry the *post-blend*
    `model_probability` and `edge`, not the fast-lane raw values.

    This is the load-bearing payload-shape decision that audit consumers
    (bothealth.sh, governance Phase 2 reasoning, future readiness-gate
    calibration) depend on. Regressing this re-introduces the discontinuity
    between OPPORTUNITY's raw-edge and SKIPPED's reported edge.
    """
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()

    fast_lane = _analysis_with_news()  # estimated_probability=0.72, market_yes_price=50
    # Blender pushes a different blended_p so we can distinguish post-blend vs fast-lane.
    blended_p_value = 0.61

    def _blender(**kwargs):  # noqa: ANN003
        return BlendResult(
            blended_p=blended_p_value,
            blended_confidence=0.04,
            disagreement_score=0.0,
            blend_mode="blocked_for_payload_pin",
            readiness_gate_min_edge_override=None,
            trade_blocked_reason="G1_blended_confidence",
            fast_lane_p=fast_lane.estimated_probability,
            fast_lane_confidence=fast_lane.confidence,
            accumulation_p=None,
            accumulation_confidence=None,
            structural_p=0.20,
            structural_confidence=0.80,
        )

    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(),
        logger=logger,
        blender=_blender,
        is_paper_mode=True,
    )

    await task.process_fast_lane_result(fast_lane)

    assert len(logger.skipped_records) == 1
    record = logger.skipped_records[0]

    # The post-blend probability rides the SKIPPED record, not the fast-lane raw.
    assert record["model_probability"] == pytest.approx(blended_p_value), (
        "BlendTask SKIPPED.model_probability must be blended_p, not fast_lane.estimated_probability"
    )
    assert record["model_probability"] != pytest.approx(fast_lane.estimated_probability), (
        "regression guard: model_probability must not equal fast-lane raw"
    )

    # market_price is sourced from the fast-lane analysis (it does not change at blend time).
    assert record["market_price"] == pytest.approx(fast_lane.market_yes_price)

    # edge is the post-blend edge: blended_p - market_yes_price/100.0 (executor convention).
    expected_edge = blended_p_value - fast_lane.market_yes_price / 100.0
    assert record["edge"] == pytest.approx(expected_edge, abs=1e-6)

    # Reason mirrors the trade_blocked_reason.
    assert record["reason"] == "G1_blended_confidence"

    # Method = "llm" because the fast-lane analysis carries llm_* fields.
    assert record["method"] == "llm"
    assert record["llm_direction"] == "up"
    assert record["llm_magnitude"] == "moderate"

    # Headline / source forwarded from the news_item.
    assert record["ticker"] == fast_lane.market.ticker
    assert record["source"] == "Reuters"
    assert record["headline"].startswith("Iran-deal headline")


# ---------------------------------------------------------------------------
# OBS-003 harness — Codex review F1 + F3 follow-up additions
#
# F1 (medium): the existing `logger.skipped_records` assertion is satisfied by
# *any* path that calls the injected logger; it does not catch an
# implementation that bypasses the injection and calls module-level
# `trade_log.log_skipped` directly. Pin the async-write target.
#
# F3 (medium): the existing payload-shape pin checks blended `model_probability`
# / `edge` only. OBS-003's central value is unifying downstream trade-log
# consumers; key-completeness against the executor-side payload matters as
# much as the post-blend values. Pin the full required-key set.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason=_OBS003_XFAIL_REASON, strict=True)
@pytest.mark.asyncio
async def test_obs003_blocked_path_writes_via_injected_logger_log_skipped(
    monkeypatch,
) -> None:
    """F1 — pin the async-write target.

    `tasks.blend_task.write_trade_log_async` must be invoked with the
    *injected* logger's `log_skipped` method as the first positional argument
    on the blocked-reason path. Catches an implementation that bypasses the
    injection and reaches the module-level `trade_log.log_skipped` directly,
    which would silently mutate production logs in tests if patching is wrong.
    """
    import tasks.blend_task as _bt

    spy_calls: list[tuple] = []

    async def _spy_writer(writer, *args, **kwargs):
        spy_calls.append((writer, args, kwargs))
        # Forward only when the writer is one of the legitimate logger methods
        # so the existing log_blend_decision side effect still flows.
        return writer(*args, **kwargs)

    monkeypatch.setattr(_bt, "write_trade_log_async", _spy_writer)

    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(),
        logger=logger,
        blender=_blocked_blender_factory("G1_blended_confidence"),
        is_paper_mode=True,
        now=lambda: datetime(2026, 4, 18, 12, tzinfo=UTC),
    )

    await task.process_fast_lane_result(_analysis())

    # Find the SKIPPED-emission write among the captured calls.
    skipped_writes = [
        (writer, args, kwargs)
        for writer, args, kwargs in spy_calls
        if writer == logger.log_skipped
    ]
    assert len(skipped_writes) == 1, (
        "BlendTask must call `write_trade_log_async(logger.log_skipped, ...)` "
        f"exactly once on the blocked-reason path; got {len(skipped_writes)} "
        f"call(s) where writer is logger.log_skipped. "
        f"All captured writers: {[str(c[0]) for c in spy_calls]}"
    )
    # Bypass-detection: assert the writer is NOT the module-level
    # `trade_log.log_skipped` (the un-injected fallback).
    from utils.logger import trade_log as _module_trade_log
    bypass_writes = [
        (writer, args, kwargs)
        for writer, args, kwargs in spy_calls
        if writer == _module_trade_log.log_skipped
    ]
    assert bypass_writes == [], (
        "BlendTask must not bypass the injected logger; "
        f"detected {len(bypass_writes)} call(s) targeting the module-level "
        "`trade_log.log_skipped` directly."
    )


@pytest.mark.xfail(reason=_OBS003_XFAIL_REASON, strict=True)
@pytest.mark.asyncio
async def test_obs003_skipped_payload_carries_required_keys() -> None:
    """F3 — full executor-compatible key set.

    OBS-003's downstream consumers (bothealth.sh, governance Phase 2 reasoning,
    future readiness-gate calibration runs) join the BlendTask SKIPPED stream
    against the executor's pre-existing SKIPPED stream. Both streams must
    carry the same key set so the union is queryable as a single table. The
    canonical key set is the executor's at `trading/executor.py:137-152`.
    """
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(),
        logger=logger,
        blender=_blocked_blender_factory("G1_blended_confidence"),
        is_paper_mode=True,
        now=lambda: datetime(2026, 4, 18, 12, tzinfo=UTC),
    )

    await task.process_fast_lane_result(_analysis_with_news())

    assert len(logger.skipped_records) == 1
    record = logger.skipped_records[0]

    required_keys = {
        "reason",
        "ticker",
        "headline",
        "source",
        "method",
        "llm_direction",
        "llm_magnitude",
        "model_probability",
        "market_price",
        "edge",
        "min_edge_threshold",
        # `signal_meta` is conditional on the upstream analysis carrying
        # signal_meta (mirrors the executor's `if signal_meta:` guard at
        # trading/executor.py:151). Not part of the always-required set.
    }
    missing = required_keys - record.keys()
    assert missing == set(), (
        f"BlendTask SKIPPED payload missing executor-compatible keys: {missing}. "
        f"Got keys: {sorted(record.keys())}"
    )

    # Headline-truncation parity: the executor truncates to 80 chars at
    # trading/executor.py:139 (`analysis.news_item.headline[:80]`). The
    # BlendTask SKIPPED emission must follow the same convention.
    assert len(record["headline"]) <= 80, (
        f"BlendTask SKIPPED.headline must follow the executor's 80-char "
        f"truncation convention; got {len(record['headline'])} chars"
    )


# ---------------------------------------------------------------------------
# PROFIT-EXEC-002 harness — series-correlation guard inside BlendTask.
#
# Spec: docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md
# Status: pre-loaded during PROFIT-PHASE2-001 soak; do not implement before
# 2026-05-09. Lands AFTER OBS-003 (the EXEC-002 spec §8 acceptance criterion
# explicitly assumes the OBS-003 SKIPPED-emission path is in place).
#
# Until BlendTask gains `_recent_series_enqueues` / `_series_prefix` /
# the in-window suppression check, these tests xfail strictly. The
# AttributeError on the missing symbol is the xfail trigger; the strict
# marker forces removal once prod lands.
# ---------------------------------------------------------------------------

import tasks.blend_task as _bt_mod  # noqa: E402

_EXEC002_XFAIL_REASON = (
    "PROFIT-EXEC-002: series-correlation guard not yet implemented in BlendTask. "
    "Lands post-soak per docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md."
)


def _market_for_series(ticker: str) -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        title=f"Will {ticker} resolve YES?",
        yes_bid=49,
        yes_ask=51,
        yes_price=50,
        volume=100,
        open_interest=50,
        close_time="2026-06-01T00:00:00Z",
        status="open",
        regime_weights={"fast": 1.0, "interpretation": 0.0, "structural": 0.0},
    )


def _analysis_for_series(ticker: str) -> SignalAnalysis:
    return SignalAnalysis(
        news_item=None,
        market=_market_for_series(ticker),
        estimated_probability=0.72,
        market_yes_price=50,
        edge=0.22,
        side="yes",
        kelly_fraction=0.0,
        kelly_dollars=0.0,
        capped_dollars=0.0,
        reasoning="EXEC-002 harness signal",
        confidence=0.90,
        match_score=0.8,
    )


@pytest.mark.xfail(reason=_EXEC002_XFAIL_REASON, strict=True)
@pytest.mark.parametrize(
    "ticker,expected_prefix",
    [
        ("KXFISAEXTEND-26APR-MAY01", "KXFISAEXTEND"),
        ("KXFISAEXTEND-26APR-MAY02", "KXFISAEXTEND"),
        ("KXMOCTRUMP25-26-APR24", "KXMOCTRUMP25"),
        ("KXTRUMPIRAN-26MAY01", "KXTRUMPIRAN"),
        ("KXSBUDGETRES-26APR-APR28", "KXSBUDGETRES"),
        ("KXVANCEPAKISTAN-26MAY15", "KXVANCEPAKISTAN"),
    ],
    ids=["FISA-MAY01", "FISA-MAY02", "MOCTRUMP25", "TRUMPIRAN", "SBUDGETRES", "VANCEPAKISTAN"],
)
def test_series_prefix_extraction(ticker: str, expected_prefix: str) -> None:
    """The `_series_prefix` helper splits on `-` and returns the first component."""
    helper = getattr(_bt_mod, "_series_prefix", None) or getattr(
        _bt_mod.BlendTask, "_series_prefix", None
    )
    assert helper is not None, "_series_prefix helper must exist on BlendTask or module"
    assert helper(ticker) == expected_prefix


@pytest.mark.xfail(reason=_EXEC002_XFAIL_REASON, strict=True)
@pytest.mark.asyncio
async def test_fisa_replay_three_same_series_only_one_enqueues() -> None:
    """The 2026-05-01 FISA case (3 trades within 7s) must collapse to 1."""
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(
            dossier=_dossier(),
            structural_prior=_structural_prior(),
            evidence=[
                _evidence("ev-1", source_class="news"),
                _evidence("ev-2", source_class="news", source="AP"),
                _evidence("ev-3", source_class="court", source="VitalLaw.com"),
            ],
        ),
        logger=logger,
        is_paper_mode=True,
        now=lambda: datetime(2026, 4, 18, 12, tzinfo=UTC),
    )

    tickers = [
        "KXFISAEXTEND-26APR-MAY01",
        "KXFISAEXTEND-26APR-MAY02",
        "KXFISAEXTEND-26APR-MAY03",
    ]
    for ticker in tickers:
        await task.process_fast_lane_result(_analysis_for_series(ticker))

    assert queue.qsize() == 1, (
        f"FISA series burst must produce exactly 1 enqueue; got {queue.qsize()}"
    )
    in_window_records = [
        r for r in logger.skipped_records
        if r.get("reason") == "series_correlation_in_window"
    ]
    assert len(in_window_records) == 2, (
        f"expected 2 series_correlation_in_window SKIPPED records; got {len(in_window_records)}"
    )


@pytest.mark.asyncio
async def test_cross_series_burst_does_not_interfere() -> None:
    """Different series prefixes within the window must both enqueue."""
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(
            dossier=_dossier(),
            structural_prior=_structural_prior(),
            evidence=[
                _evidence("ev-1", source_class="news"),
                _evidence("ev-2", source_class="news", source="AP"),
                _evidence("ev-3", source_class="court", source="VitalLaw.com"),
            ],
        ),
        logger=logger,
        is_paper_mode=True,
        now=lambda: datetime(2026, 4, 18, 12, tzinfo=UTC),
    )

    await task.process_fast_lane_result(_analysis_for_series("KXFISAEXTEND-26APR-MAY01"))
    await task.process_fast_lane_result(_analysis_for_series("KXTRUMPIRAN-26MAY01"))

    assert queue.qsize() == 2, "cross-series candidates must both enqueue"
    assert not any(
        r.get("reason") == "series_correlation_in_window" for r in logger.skipped_records
    ), "no series_correlation_in_window SKIPPED expected for cross-series traffic"


@pytest.mark.xfail(reason=_EXEC002_XFAIL_REASON, strict=True)
@pytest.mark.asyncio
async def test_window_expiry_allows_second_same_series_candidate(monkeypatch) -> None:
    """Same series, second candidate arriving after the window has expired.

    Per Codex review F2: pure behavior — does not poke at any private guard
    state. Time-travel via `time.monotonic` monkeypatch so the second
    candidate appears past the window without depending on the implementation
    being a `dict[str, float]`.
    """
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    monkeypatch.setattr(
        cfg, "series_correlation_window_seconds", 60, raising=False,
    )

    # Fake monotonic clock that the test can advance.
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr("tasks.blend_task.time.monotonic", lambda: clock["now"])

    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(
            dossier=_dossier(),
            structural_prior=_structural_prior(),
            evidence=[
                _evidence("ev-1", source_class="news"),
                _evidence("ev-2", source_class="news", source="AP"),
                _evidence("ev-3", source_class="court", source="VitalLaw.com"),
            ],
        ),
        logger=logger,
        is_paper_mode=True,
        now=lambda: datetime(2026, 4, 18, 12, tzinfo=UTC),
    )

    await task.process_fast_lane_result(_analysis_for_series("KXFISAEXTEND-26APR-MAY01"))
    # Advance fake monotonic past the 60s window.
    clock["now"] += 120.0

    await task.process_fast_lane_result(_analysis_for_series("KXFISAEXTEND-26APR-MAY02"))

    assert queue.qsize() == 2, "post-window same-series candidate must enqueue"


@pytest.mark.asyncio
async def test_window_override_zero_disables_guard(monkeypatch) -> None:
    """`cfg.series_correlation_window_seconds = 0` must disable the guard entirely."""
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    monkeypatch.setattr(
        cfg, "series_correlation_window_seconds", 0, raising=False,
    )
    task = BlendTask(
        trading_queue=queue,
        store=FakeStore(
            dossier=_dossier(),
            structural_prior=_structural_prior(),
            evidence=[
                _evidence("ev-1", source_class="news"),
                _evidence("ev-2", source_class="news", source="AP"),
                _evidence("ev-3", source_class="court", source="VitalLaw.com"),
            ],
        ),
        logger=logger,
        is_paper_mode=True,
        now=lambda: datetime(2026, 4, 18, 12, tzinfo=UTC),
    )

    for ticker in (
        "KXFISAEXTEND-26APR-MAY01",
        "KXFISAEXTEND-26APR-MAY02",
        "KXFISAEXTEND-26APR-MAY03",
    ):
        await task.process_fast_lane_result(_analysis_for_series(ticker))

    assert queue.qsize() == 3, "window=0 must allow every candidate"
    assert not any(
        r.get("reason") == "series_correlation_in_window" for r in logger.skipped_records
    ), "window=0 must fully bypass the guard"


# Removed `test_blend_task_carries_recent_series_enqueues_state` per Codex review F2:
# the dict-shape pin over-constrained the implementation. A correct bounded LRU,
# deque, injected guard object, or module-level helper would have failed even with
# correct FISA-replay behavior. Behavior is already pinned by the runtime tests
# above (FISA replay + cross-series + window-expiry + window-override).
