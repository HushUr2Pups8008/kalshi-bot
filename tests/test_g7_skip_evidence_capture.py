from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from analysis import SignalAnalysis
from feeds import NewsItem
from kalshi import KalshiMarket
from tasks.g7_skip_evidence_capture import (
    G7SkipEvidenceCaptureEnvelope,
    G7SkipEvidenceCaptureSink,
)
from tasks.trade_readiness_gate import evaluate_readiness
from trading.g7_skip_evidence import (
    G7SkipEvidenceStore,
    read_g7_skip_evidence_records,
)


NOW = datetime(2026, 7, 31, 14, 30, tzinfo=UTC)


def _analysis(*, signal_meta: dict[str, object] | None = None) -> SignalAnalysis:
    market = KalshiMarket(
        ticker="KXG7-26JUL31-T50",
        title="Will the G7 test threshold resolve YES?",
        yes_bid=49,
        yes_ask=51,
        yes_price=50,
        volume=100,
        open_interest=50,
        close_time="2026-08-01T14:30:00Z",
        status="active",
        regime_weights={"fast": 1.0, "interpretation": 0.0, "structural": 0.0},
        yes_bid_cents=49,
        yes_ask_cents=51,
        no_bid_cents=49,
        no_ask_cents=51,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
    )
    return SignalAnalysis(
        news_item=NewsItem("G7 test signal", "https://example.test/g7", "wire"),
        market=market,
        estimated_probability=0.72,
        executed_price_cents=50,
        edge=0.22,
        side="yes",
        kelly_fraction=0.0,
        kelly_dollars=0.0,
        capped_dollars=0.0,
        reasoning="G7 evidence capture test",
        confidence=0.90,
        match_score=0.8,
        signal_meta=signal_meta,
    )


def _envelope(
    *,
    analysis: SignalAnalysis,
    market_liquidity_dollars: float | None,
    market_price_momentum_cents: float | None,
) -> G7SkipEvidenceCaptureEnvelope:
    readiness_input = {
        "source_lane": "fast",
        "blended_confidence": 0.9,
        "disagreement_score": 0.1,
        "default_min_edge": 0.05,
        "evidence_source_classes": [],
        "drift_suspect": False,
        "in_recovery": False,
        "recency_score": 1.0,
        "time_to_close_seconds": 86400.0,
        "settlement_source_relevant": True,
        "market_liquidity_dollars": market_liquidity_dollars,
        "market_price_momentum_cents": market_price_momentum_cents,
        "intended_side": "yes",
        "open_exposure_drawdown_pct": 0.0,
    }
    readiness = evaluate_readiness(readiness_input, 1.0)
    assert readiness.trade_blocked_reason is not None
    return G7SkipEvidenceCaptureEnvelope(
        analysis=analysis,
        readiness_decision=readiness,
        readiness_input=readiness_input,
        trade_blocked_reason=readiness.trade_blocked_reason,
        venue="kalshi",
        market_family="KXG7",
        lifecycle_id="g7-lifecycle-1",
        decision_at=NOW,
    )


@pytest.mark.asyncio
async def test_capture_preserves_unavailable_provider_metadata_without_refetch(
    tmp_path: Path,
) -> None:
    metadata = {
        "source": "kalshi_orderbook",
        "status": "unavailable",
        "reason": "RuntimeError",
    }
    analysis = _analysis(signal_meta={"g7_execution_liquidity": metadata})
    envelope = _envelope(
        analysis=analysis,
        market_liquidity_dollars=0.0,
        market_price_momentum_cents=0.0,
    )
    store = G7SkipEvidenceStore(tmp_path / "g7_skip_evidence.db")
    store.initialize(applied_at=NOW)

    result = await G7SkipEvidenceCaptureSink(store).capture(envelope)

    assert result.status == "inserted"
    [record] = read_g7_skip_evidence_records(store.db_path)
    assert record.liquidity_evidence_status == "unavailable"
    assert record.execution_liquidity == metadata
    assert record.g7_failures == ("G7_zero_liquidity",)
    assert record.g7_inputs["market_liquidity_dollars"] == 0.0
    assert analysis.signal_meta == {"g7_execution_liquidity": metadata}


@pytest.mark.asyncio
async def test_capture_marks_momentum_block_not_queried_when_metadata_is_absent(
    tmp_path: Path,
) -> None:
    analysis = _analysis()
    envelope = _envelope(
        analysis=analysis,
        market_liquidity_dollars=None,
        market_price_momentum_cents=-1.0,
    )
    store = G7SkipEvidenceStore(tmp_path / "g7_skip_evidence.db")
    store.initialize(applied_at=NOW)

    result = await G7SkipEvidenceCaptureSink(store).capture(envelope)

    assert result.status == "inserted"
    [record] = read_g7_skip_evidence_records(store.db_path)
    assert record.liquidity_evidence_status == "not_queried"
    assert record.execution_liquidity == {
        "status": "not_queried",
        "reason": "execution_liquidity_not_queried",
    }
    assert record.g7_failures == ("G7_adverse_price_momentum",)


@pytest.mark.asyncio
async def test_capture_preserves_observed_executable_liquidity_metadata(
    tmp_path: Path,
) -> None:
    metadata = {
        "source": "kalshi_orderbook",
        "side": "yes",
        "limit_price": 0.50,
        "best_price": 0.50,
        "executable_quantity": 0.0,
        "executable_notional": 0.0,
        "as_of": NOW.isoformat(),
        "raw_payload_hash": "a" * 64,
    }
    analysis = _analysis(signal_meta={"g7_execution_liquidity": metadata})
    envelope = _envelope(
        analysis=analysis,
        market_liquidity_dollars=0.0,
        market_price_momentum_cents=0.0,
    )
    store = G7SkipEvidenceStore(tmp_path / "g7_skip_evidence.db")
    store.initialize(applied_at=NOW)

    await G7SkipEvidenceCaptureSink(store).capture(envelope)

    [record] = read_g7_skip_evidence_records(store.db_path)
    assert record.liquidity_evidence_status == "observed"
    assert record.execution_liquidity == {
        **metadata,
        "as_of": "2026-07-31T14:30:00.000000Z",
    }
    assert record.g7_inputs["minimum_market_liquidity_dollars"] > 0.0
    assert record.g7_results["ordered_failures"] == ["G7_zero_liquidity"]
