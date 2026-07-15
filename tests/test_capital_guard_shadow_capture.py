from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import json
import sqlite3
from pathlib import Path

import pytest

from analysis import DecisionFinancialProvenance, SignalAnalysis
from analysis.decision_blender import BlendResult
from feeds import NewsItem
from kalshi import KalshiMarket
from kalshi.series_metadata import SettlementSource
from polymarket.candidate_adapter import (
    PolymarketExecutionMarket,
    adapt_polymarket_analysis,
)
from polymarket.normalizer import normalize_polymarket_market
from tasks.capital_guard_shadow_capture import (
    CapitalGuardShadowCaptureEnvelope,
    CapitalGuardShadowCaptureSink,
)
from tasks.trade_readiness_gate import evaluate_readiness
from trading.capital_guard_shadow import CapitalGuardShadowStore
from trading.fees import (
    KALSHI_GENERAL_2026_07_07,
    POLYMARKET_US_2026_07_01,
    fee_type_for_schedule,
)


NOW = datetime(2026, 7, 15, 12, 30, tzinfo=UTC)
D = Decimal


def _rows(path: Path, sql: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as conn:
        return [tuple(row) for row in conn.execute(sql)]


def _envelope(
    *,
    disagreement: float = 0.10,
    include_fee_precision: bool = True,
    include_financial_provenance: bool = True,
    venue: str = "kalshi",
    side: str = "yes",
):
    schedule = (
        KALSHI_GENERAL_2026_07_07
        if venue == "kalshi"
        else POLYMARKET_US_2026_07_01
    )
    market = KalshiMarket(
        ticker="KXTEST-26JUL15-T50",
        title="Will the test threshold resolve YES?",
        yes_bid=40,
        yes_ask=42,
        yes_price=41,
        volume=100,
        open_interest=50,
        close_time="2026-07-16T12:30:00Z",
        status="active",
        series_ticker="KXTEST",
        event_ticker="KXTEST-26JUL15",
        rules_primary="Resolves YES when the official value is at least 50.",
        settlement_sources=(
            SettlementSource(
                label="Official source",
                url="https://example.test/value",
                domain="example.test",
            ),
        ),
        price_available=True,
        yes_bid_cents=40,
        yes_ask_cents=42,
        no_bid_cents=58,
        no_ask_cents=60,
        yes_bid_levels=((D("0.40"), D("10")),),
        no_bid_levels=((D("0.58"), D("7")), (D("0.57"), D("10"))),
        book_as_of=NOW,
        book_payload_hash="a" * 64,
        price_source="kalshi-orderbook-v2",
        price_method="fixed-point-depth-complement-v1",
        quantity_step=D("1"),
        fee_multiplier=D("1"),
        fee_type=fee_type_for_schedule(schedule),
        fee_effective_at=schedule.effective_from,
        fill_role="taker",
        fee_provenance_hash="c" * 64,
        report_venue=venue,
        report_venue_market_id=(
            "KXTEST-26JUL15-T50" if venue == "kalshi" else "pm-test-threshold-50"
        ),
        outcome_side=side,
    )
    if venue == "polymarket_us":
        market.venue_market_id = "pm-test-threshold-50"
        market.fee_coefficient = D("0.06")
        market.source_payload_hash = "c" * 64
    signal_meta = {
        "lifecycle_id": "lifecycle-1",
        "settlement_source_match": True,
    }
    financial_provenance = (
        DecisionFinancialProvenance(
            sizing_bankroll_dollars=D("8.76"),
            max_position_dollars=D("5"),
            max_ticker_exposure_dollars=D("5"),
            fee_account_precision_dollars=(
                D("0.0001")
                if include_fee_precision and venue == "kalshi"
                else None
            ),
            fee_accumulator_dollars=D("0"),
        )
        if include_financial_provenance
        else None
    )
    analysis = SignalAnalysis(
        news_item=NewsItem("Threshold update", "https://example.test/news", "wire"),
        market=market,
        estimated_probability=0.55,
        executed_price_cents=42 if side == "yes" else 60,
        edge=0.13 if side == "yes" else -0.15,
        side=side,
        kelly_fraction=float(D("3") / D("8.76")),
        kelly_dollars=3.0,
        capped_dollars=2.1,
        confidence=0.8,
        signal_meta=signal_meta,
        decision_financial_provenance=financial_provenance,
    )
    blend = BlendResult(
        blended_p=0.55,
        blended_confidence=0.8,
        disagreement_score=disagreement,
        blend_mode="weighted_blend",
        readiness_gate_min_edge_override=None,
        trade_blocked_reason=None,
        fast_lane_p=0.55,
        fast_lane_confidence=0.8,
        accumulation_p=None,
        accumulation_confidence=None,
        structural_p=None,
        structural_confidence=None,
    )
    readiness_input = {
        "source_lane": "fast",
        "blended_confidence": 0.8,
        "disagreement_score": disagreement,
        "default_min_edge": 0.05,
        "evidence_source_classes": [],
        "drift_suspect": False,
        "in_recovery": False,
        "recency_score": 1.0,
        "time_to_close_seconds": 86400.0,
        "settlement_source_relevant": True,
        "market_liquidity_dollars": 100.0,
        "market_price_momentum_cents": 0.0,
        "intended_side": side,
        "open_exposure_drawdown_pct": 0.30,
    }
    readiness = evaluate_readiness(readiness_input, 1.0)
    return CapitalGuardShadowCaptureEnvelope(
        analysis=analysis,
        blend_result=blend,
        readiness_decision=readiness,
        readiness_input=readiness_input,
        regime_weights={"fast": 1.0, "interpretation": 0.0, "structural": 0.0},
        regime_confidence=1.0,
        trade_blocked_reason=readiness.trade_blocked_reason,
        venue=venue,
        market_family="KXTEST",
        lifecycle_id="lifecycle-1",
        decision_at=NOW,
        default_min_edge=0.05,
    )


@pytest.mark.asyncio
async def test_complete_sole_g7_attempt_persists_replay_eligible_candidate(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    sink = CapitalGuardShadowCaptureSink(store)

    result = await sink.capture(_envelope())

    assert result.attempt_status == "inserted"
    assert result.candidate_status == "inserted"
    assert _rows(store.db_path, "SELECT scorable FROM capital_guard_shadow_capture_attempts") == [(1,)]
    assert _rows(store.db_path, "SELECT replay_eligible FROM capital_guard_shadow_candidates") == [(1,)]


@pytest.mark.asyncio
async def test_other_gate_failure_is_captured_but_not_replay_eligible(tmp_path: Path) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)

    await CapitalGuardShadowCaptureSink(store).capture(_envelope(disagreement=0.30))

    failures, replay = _rows(
        store.db_path,
        "SELECT ordered_failures_json, replay_eligible FROM capital_guard_shadow_candidates",
    )[0]
    assert json.loads(str(failures)) == [
        "G3_disagreement_score",
        "G7_open_exposure_drawdown",
    ]
    assert replay == 0


@pytest.mark.asyncio
async def test_missing_fee_provenance_persists_unscorable_attempt_only(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)

    result = await CapitalGuardShadowCaptureSink(store).capture(
        replace(
            _envelope(include_fee_precision=False),
            trade_blocked_reason="structural_tier2_veto",
        )
    )

    assert result.candidate_status is None
    failures, blocker, reasons = _rows(
        store.db_path,
        "SELECT ordered_failures_json, non_gate_blocker, "
        "ordered_unscorable_reasons_json FROM capital_guard_shadow_capture_attempts",
    )[0]
    assert json.loads(str(failures)) == ["G7_open_exposure_drawdown"]
    assert blocker == "structural_tier2_veto"
    assert "missing_fee_account_precision_dollars" in json.loads(str(reasons))
    assert _rows(store.db_path, "SELECT count(*) FROM capital_guard_shadow_candidates") == [(0,)]


@pytest.mark.asyncio
async def test_malformed_book_timestamp_still_persists_denominator_attempt(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    envelope = _envelope()
    envelope.analysis.market.book_as_of = datetime(2026, 7, 15, 12, 30)

    result = await CapitalGuardShadowCaptureSink(store).capture(envelope)

    assert result.candidate_status is None
    reasons = json.loads(
        str(
            _rows(
                store.db_path,
                "SELECT ordered_unscorable_reasons_json "
                "FROM capital_guard_shadow_capture_attempts",
            )[0][0]
        )
    )
    assert "invalid_book_as_of" in reasons


@pytest.mark.asyncio
async def test_identical_lifecycle_retry_is_idempotent(tmp_path: Path) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    sink = CapitalGuardShadowCaptureSink(store)
    envelope = _envelope()

    first = await sink.capture(envelope)
    retry = await sink.capture(envelope)

    assert (first.attempt_status, first.candidate_status) == ("inserted", "inserted")
    assert (retry.attempt_status, retry.candidate_status) == ("identical", "identical")
    assert _rows(store.db_path, "SELECT count(*) FROM capital_guard_shadow_capture_attempts") == [(1,)]
    assert _rows(store.db_path, "SELECT count(*) FROM capital_guard_shadow_candidates") == [(1,)]


@pytest.mark.asyncio
async def test_position_caps_use_exact_decision_time_provenance(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)

    result = await CapitalGuardShadowCaptureSink(store).capture(_envelope())

    assert result.candidate_status == "inserted"
    sizing = json.loads(
        str(
            _rows(
                store.db_path,
                "SELECT sizing_json FROM capital_guard_shadow_candidates",
            )[0][0]
        )
    )
    assert sizing["max_position_dollars"] == "5"
    assert sizing["max_ticker_exposure_dollars"] == "5"


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["yes", "no"])
async def test_kalshi_and_polymarket_capture_identical_selected_side_book_bytes(
    tmp_path: Path,
    side: str,
) -> None:
    captured: dict[str, tuple[object, ...]] = {}
    for venue in ("kalshi", "polymarket_us"):
        store = CapitalGuardShadowStore(tmp_path / f"{venue}-{side}.db")
        store.initialize(applied_at=NOW)
        result = await CapitalGuardShadowCaptureSink(store).capture(
            _envelope(venue=venue, side=side)
        )
        assert result.candidate_status == "inserted"
        captured[venue] = _rows(
            store.db_path,
            "SELECT executable_book_json, executable_price_dollars, "
            "executable_quantity, gross_edge FROM capital_guard_shadow_candidates",
        )[0]

    assert captured["kalshi"] == captured["polymarket_us"]


@pytest.mark.asyncio
async def test_normalized_polymarket_adapter_persists_incomplete_denominator_attempt(
    tmp_path: Path,
) -> None:
    normalized = normalize_polymarket_market(
        {
            "id": 8594,
            "slug": "will-example-happen-2026",
            "title": "Will example happen in 2026?",
            "question": "Does the official source report the example happened?",
            "description": "Resolves from the official published report.",
            "resolutionSource": "https://example.test/official-report",
            "eventTitle": "Example Event",
            "eventSlug": "example-event",
            "seriesTitle": "Example Series",
            "seriesSlug": "example-series",
            "status": "open",
            "outcomes": [
                {
                    "name": "Yes",
                    "bestBid": {"value": "0.41", "quantity": "120"},
                    "bestAsk": {"value": "0.42"},
                },
                {
                    "name": "No",
                    "bestBid": {"value": "0.58", "quantity": "80"},
                    "bestAsk": {"value": "0.59"},
                },
            ],
            "volume": {"value": "1234.50"},
            "openInterest": {"value": "99"},
            "closeTime": "2026-12-31T23:59:59Z",
            "feeCoefficient": "0.06",
            "feeEffectiveAt": "2026-07-01T04:00:00Z",
            "quantityStep": "1",
            "priceTick": "0.01",
            "fillRole": "taker",
        },
        snapshot_at=NOW,
    )
    adapted = adapt_polymarket_analysis(
        replace(
            _envelope().analysis,
            decision_financial_provenance=None,
        ),
        normalized,
    )

    assert isinstance(adapted.market, PolymarketExecutionMarket)
    assert adapted.market.venue_market_id == "8594"
    assert adapted.market.question == normalized.question
    assert adapted.market.resolution_source == normalized.resolution_source
    assert adapted.market.yes_bid_size == normalized.yes_bid_size
    assert adapted.market.quantity_step == normalized.quantity_step
    assert adapted.market.fee_coefficient == normalized.fee_coefficient
    assert adapted.market.raw_payload_hash == normalized.source_payload_hash

    store = CapitalGuardShadowStore(tmp_path / "polymarket-shadow.db")
    store.initialize(applied_at=NOW)
    result = await CapitalGuardShadowCaptureSink(store).capture(
        replace(
            _envelope(venue="polymarket_us"),
            analysis=adapted,
            venue="polymarket_us",
            market_family=normalized.series_ticker,
        )
    )

    assert (result.attempt_status, result.candidate_status) == ("inserted", None)
    assert _rows(
        store.db_path,
        "SELECT venue, venue_market_id, scorable "
        "FROM capital_guard_shadow_capture_attempts",
    ) == [("polymarket_us", "8594", 0)]
    reasons = json.loads(
        str(
            _rows(
                store.db_path,
                "SELECT ordered_unscorable_reasons_json "
                "FROM capital_guard_shadow_capture_attempts",
            )[0][0]
        )
    )
    assert "missing_selected_side_bid_depth" in reasons
    assert "missing_fee_accumulator_dollars" in reasons
    assert _rows(store.db_path, "SELECT count(*) FROM capital_guard_shadow_candidates") == [(0,)]

    missing_id_analysis = replace(
        adapted,
        market=replace(adapted.market, venue_market_id=None),
    )
    missing_id_store = CapitalGuardShadowStore(tmp_path / "polymarket-missing-id.db")
    missing_id_store.initialize(applied_at=NOW)
    missing_id_result = await CapitalGuardShadowCaptureSink(missing_id_store).capture(
        replace(
            _envelope(venue="polymarket_us"),
            analysis=missing_id_analysis,
            venue="polymarket_us",
            market_family=normalized.series_ticker,
        )
    )

    assert missing_id_result.candidate_status is None
    missing_id_reasons = json.loads(
        str(
            _rows(
                missing_id_store.db_path,
                "SELECT ordered_unscorable_reasons_json "
                "FROM capital_guard_shadow_capture_attempts",
            )[0][0]
        )
    )
    assert "missing_canonical_venue_market_id" in missing_id_reasons
